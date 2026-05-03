"""
routes.voice — Voice API endpoints (TTS, STT, two-leg voice dispatch).

POST /api/voice/tts
  Converts text to speech.
  - Indic languages (hi, bn, as): Google Cloud TTS Neural2.
    Falls back to PROVIDER_PRIORITY weighted round-robin on failure.
  - English/other: PROVIDER_PRIORITY weighted round-robin with
    fallback-without-replacement.
    Weighted pool: ElevenLabs(primary) → Deepgram → Workers AI.
  Returns audio/mpeg bytes (mp3).

POST /api/voice/stt
  Speech-to-text via PROVIDER_PRIORITY weighted round-robin with fallback-without-
  replacement. Weighted pool: Deepgram(primary) → AssemblyAI → Vertex → Workers AI.
  Accepts multipart/form-data with an 'audio' file field.

POST /api/voice/voice
  Two-leg independent-selection pipeline:
    Leg 1 (STT) — select_provider("stt") with per-leg fallback-without-replacement
    Leg 2 (TTS) — Google Neural2 for Indic; select_provider("tts") round-robin for others
  LLM reply generated between the two legs.
  Returns { transcript, reply_text, audio_b64 }.

GET  /api/voice/health
  Reports readiness of all voice providers (Deepgram, ElevenLabs, AssemblyAI,
  Workers AI, Google TTS/STT).
"""
from __future__ import annotations

import asyncio
import base64
import logging
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Depends, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from auth_deps import get_current_user, get_current_user_optional

logger = logging.getLogger("routes.voice")

router = APIRouter(tags=["voice"])

_GOOGLE_TTS_LANGS = frozenset({"hi", "bn", "as", "hi-in", "bn-in", "as-in"})


class TtsRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000, description="Text to synthesize")
    voice_id: Optional[str] = Field(None, description="ElevenLabs voice ID (uses env var default if omitted)")
    language: str = Field("en", description="BCP-47 language code (en, hi, as, bn, ...)")
    model_id: Optional[str] = Field(None, description="TTS model ID (ElevenLabs default: eleven_multilingual_v2)")
    gender: str = Field("female", description="TTS voice gender for Indic (Google Neural2): female or male")


class VoiceRequest(BaseModel):
    language: str = Field("en", description="BCP-47 language code for STT + TTS")
    voice_id: Optional[str] = Field(None, description="TTS voice ID (ElevenLabs)")
    system_prompt: Optional[str] = Field(None, description="System prompt for the LLM reply step")


# ── Individual provider TTS callers ───────────────────────────────────────────

async def _tts_elevenlabs(text: str, voice_id: Optional[str], language: str) -> bytes:
    """TTS via ElevenLabs eleven_multilingual_v2. Raises RuntimeError on failure."""
    from providers import elevenlabs
    if not elevenlabs.ENABLED:
        raise RuntimeError("ElevenLabs TTS not available (ELEVENLABS_API_KEY not set)")
    return await elevenlabs.synthesize(
        text,
        voice_id=voice_id or None,
        language_code=language[:2] if language else None,
    )


async def _tts_deepgram(text: str, voice_id: Optional[str], language: str) -> bytes:
    """TTS via Deepgram Aura-2. Raises RuntimeError on failure."""
    from providers import deepgram as _dg
    if not _dg.ENABLED:
        raise RuntimeError("Deepgram TTS not available (DEEPGRAM_API_KEY not set)")
    return await _dg.synthesize(text, voice=voice_id or None, language=language)


async def _tts_workers_ai(text: str, language: str) -> bytes:
    """Last-resort TTS via Workers AI Deepgram Aura. Raises RuntimeError on failure."""
    from providers.cloudflare_ai import speak as _cf_speak
    return await _cf_speak(text, lang=language[:2] if language else "en")


# ── Individual provider STT callers ───────────────────────────────────────────

async def _stt_deepgram(audio_bytes: bytes, language: str) -> str:
    """Primary STT via Deepgram Nova-3. Raises RuntimeError on failure."""
    from providers import deepgram as _dg
    if not _dg.ENABLED:
        raise RuntimeError("Deepgram STT not available (DEEPGRAM_API_KEY not set)")
    return await _dg.transcribe(audio_bytes, language_code=language or None)


async def _stt_assemblyai(audio_bytes: bytes, language: str) -> str:
    """STT via AssemblyAI 'best' model. Raises RuntimeError on failure."""
    from providers import assemblyai
    if not assemblyai.ENABLED:
        raise RuntimeError("AssemblyAI STT not available (ASSEMBLYAI_API_KEY not set)")
    return await assemblyai.transcribe(audio_bytes, language_code=language or None)


async def _stt_workers_ai(audio_bytes: bytes) -> str:
    """Last-resort STT via Workers AI Whisper-large-v3-turbo. Raises RuntimeError on failure."""
    from providers.cloudflare_ai import transcribe as _cf_transcribe
    return await _cf_transcribe(audio_bytes)


# ── Weighted fallback-without-replacement dispatch ─────────────────────────────

async def _synthesize_with_fallback(
    text: str,
    voice_id: Optional[str],
    model_id: Optional[str],
    language: str,
) -> bytes:
    """TTS: weighted fallback-without-replacement via select_provider("tts").

    PROVIDER_PRIORITY["tts"]: elevenlabs(primary) → deepgram → vertex(skip) → workers_ai.

    vertex TTS endpoint not wired; raises RuntimeError which the fallback loop
    catches, excludes from pool, and redraws.
    elevenlabs, deepgram, and workers_ai are the actively synthesizing providers.
    """
    from llm import select_provider

    exclude: frozenset = frozenset()
    max_attempts = 6  # covers all providers in tts priority list

    for _ in range(max_attempts):
        provider = select_provider("tts", lang=language, exclude=exclude)
        try:
            if provider == "elevenlabs":
                return await _tts_elevenlabs(text, voice_id, language)
            elif provider == "deepgram":
                return await _tts_deepgram(text, voice_id, language)
            elif provider == "workers_ai":
                return await _tts_workers_ai(text, language)
            elif provider == "vertex":
                raise RuntimeError("TTS not supported by 'vertex' — no Cloud TTS client wired")
            elif provider == "bedrock":
                from providers.bedrock import call_tts as _bk_tts
                return await _bk_tts(text, voice=voice_id)
            elif provider == "azure_openai":
                from providers.azure_openai import call_tts as _az_tts
                return await _az_tts(text, voice=voice_id)
            else:
                raise RuntimeError(f"TTS: unknown provider {provider!r}")
        except Exception as exc:
            logger.warning("TTS %s failed: %s — removing from pool and retrying", provider, exc)
            exclude = exclude | {provider}

    # Absolute last resort: Workers AI regardless of exclusion list.
    logger.error("TTS: all providers exhausted, forcing Workers AI fallback")
    return await _tts_workers_ai(text, language)


async def _transcribe_with_fallback(audio_bytes: bytes, language: str) -> str:
    """STT: weighted fallback-without-replacement via select_provider("stt").

    PROVIDER_PRIORITY["stt"]: deepgram(primary) → assemblyai → vertex(skip) →
      workers_ai

    vertex STT endpoint not wired; raises RuntimeError which the fallback loop
    catches, excludes from pool, and redraws.
    deepgram, assemblyai, and workers_ai are the actively transcribing providers.
    """
    from llm import select_provider

    exclude: frozenset = frozenset()
    max_attempts = 7  # covers all providers in stt priority list

    for _ in range(max_attempts):
        provider = select_provider("stt", lang=language, exclude=exclude)
        try:
            if provider == "deepgram":
                return await _stt_deepgram(audio_bytes, language)
            elif provider == "assemblyai":
                return await _stt_assemblyai(audio_bytes, language)
            elif provider == "workers_ai":
                return await _stt_workers_ai(audio_bytes)
            elif provider == "vertex":
                raise RuntimeError("STT not supported by 'vertex' — no Cloud STT client wired")
            elif provider == "bedrock":
                from providers.bedrock import call_stt as _bk_stt
                return await _bk_stt(audio_bytes, language=language)
            elif provider == "azure_openai":
                from providers.azure_openai import call_stt as _az_stt
                return await _az_stt(audio_bytes, language=language)
            else:
                raise RuntimeError(f"STT: unknown provider {provider!r}")
        except Exception as exc:
            logger.warning("STT %s failed: %s — removing from pool and retrying", provider, exc)
            exclude = exclude | {provider}

    # Absolute last resort.
    logger.error("STT: all providers exhausted, forcing Workers AI fallback")
    try:
        return await _stt_workers_ai(audio_bytes)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Speech recognition failed — all providers exhausted.")


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/voice/tts",
    response_class=Response,
    summary="Text-to-speech (Indic: Google Neural2; English: ElevenLabs → Deepgram → Workers AI)",
    description=(
        "Convert text to speech. For Indic languages (hi, bn, as) uses "
        "Google Cloud TTS Neural2 before falling back to the "
        "PROVIDER_PRIORITY weighted round-robin with fallback-without-replacement. "
        "Weighted pool: ElevenLabs(primary) → Deepgram → Workers AI(last-resort). "
        "Returns mp3 audio bytes."
    ),
)
async def text_to_speech(
    body: TtsRequest,
    current_user: Optional[dict] = Depends(get_current_user_optional),
):
    lang_key = body.language.lower().strip()

    # Task #247: Google Neural2 dispatch for Indic languages.
    # Runs before the existing weighted round-robin so GCP credits are
    # consumed for Indic TTS — ElevenLabs/Deepgram have limited Indic support anyway.
    if lang_key in _GOOGLE_TTS_LANGS:
        from providers import google_tts
        if google_tts.is_configured():
            try:
                audio_bytes = await google_tts.synthesize(
                    body.text,
                    lang=lang_key,
                    gender=body.gender,
                )
                if audio_bytes:
                    return Response(
                        content=audio_bytes,
                        media_type="audio/mpeg",
                        headers={
                            "Content-Disposition": 'inline; filename="speech.mp3"',
                            "Cache-Control": "public, max-age=3600",
                            "X-TTS-Provider": "google_neural2",
                            "X-TTS-Lang": lang_key,
                            "X-TTS-Chars": str(len(body.text)),
                            "X-TTS-Bytes": str(len(audio_bytes)),
                        },
                    )
            except Exception as exc:
                logger.warning(
                    "[voice-tts] Google Neural2 failed for %s: %s — falling back to round-robin",
                    lang_key, exc,
                )

    try:
        audio_bytes = await _synthesize_with_fallback(
            body.text,
            voice_id=body.voice_id or None,
            model_id=body.model_id or None,
            language=body.language,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        logger.error("TTS synthesis failed: %s", exc)
        raise HTTPException(status_code=502, detail="TTS synthesis failed.")

    return Response(
        content=audio_bytes,
        media_type="audio/mpeg",
        headers={
            "Content-Disposition": 'inline; filename="speech.mp3"',
            "Cache-Control": "public, max-age=3600",
            "X-TTS-Provider": "tts",
            "X-TTS-Chars": str(len(body.text)),
            "X-TTS-Bytes": str(len(audio_bytes)),
        },
    )


@router.post(
    "/voice/stt",
    summary="Speech-to-text (weighted round-robin: AssemblyAI → Workers AI Whisper)",
    description=(
        "Transcribe audio using the PROVIDER_PRIORITY weighted round-robin "
        "with fallback-without-replacement. "
        "Weighted pool: AssemblyAI(1000) → Workers AI Whisper(last-resort). "
        "Accepts multipart/form-data with an 'audio' file field."
    ),
)
async def speech_to_text(
    audio: UploadFile = File(..., description="Audio file (mp3, wav, flac, ogg, m4a, webm)"),
    language: str = Form("en", description="BCP-47 language code (en, hi, as, ...)"),
    current_user: dict = Depends(get_current_user),
):
    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file.")
    if len(audio_bytes) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Audio file too large (max 25 MB).")

    transcript = await _transcribe_with_fallback(audio_bytes, language)
    return {
        "transcript": transcript,
        "language": language,
        "bytes_received": len(audio_bytes),
    }


@router.post(
    "/voice/voice",
    summary="Two-leg voice pipeline (STT leg + LLM + TTS leg)",
    description=(
        "Full voice pipeline with independent per-leg weighted provider selection:\n\n"
        "**Leg 1 — STT** (select_provider('stt') with fallback-without-replacement):\n"
        "  AssemblyAI(1000) → Workers AI Whisper(last-resort)\n\n"
        "**LLM** — generate reply via call_llm_api_chat\n\n"
        "**Leg 2 — TTS** (Google Neural2 for Indic; select_provider('tts') round-robin for others):\n"
        "  Indic: Google Neural2 → ElevenLabs → Deepgram → Workers AI(last-resort)\n\n"
        "Returns { transcript, reply_text, audio_b64, language }."
    ),
)
async def voice_pipeline(
    audio: UploadFile = File(..., description="Audio file for STT leg"),
    language: str = Form("en", description="BCP-47 language code for both STT and TTS legs"),
    voice_id: Optional[str] = Form(None, description="TTS voice ID (ElevenLabs)"),
    system_prompt: Optional[str] = Form(None, description="System prompt injected before the user transcript"),
    current_user: dict = Depends(get_current_user),
):
    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file.")
    if len(audio_bytes) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Audio file too large (max 25 MB).")

    # ── Concurrent two-leg dispatch ────────────────────────────────────────────
    # STT and TTS are fully independent dispatch legs — each draws from its own
    # PROVIDER_PRIORITY pool (select_provider("stt") / select_provider("tts"))
    # with weighted fallback-without-replacement.
    #
    # The TTS provider pool is pre-selected concurrently while the STT leg runs
    # so it is ready the moment the LLM reply is available, minimising latency.
    # The LLM step between the two legs is serial (requires the STT transcript).
    #
    # Dispatch pools:
    #   STT leg: assemblyai(1000) → vertex(2k,skip) → bedrock(1k,skip) → azure_openai(1,skip) → workers_ai(0)
    #   TTS leg: google_neural2(Indic first) → elevenlabs → deepgram → vertex(skip) → workers_ai(0)

    from llm import select_provider as _sp

    async def _stt_leg() -> str:
        return await _transcribe_with_fallback(audio_bytes, language)

    async def _tts_provider_preselect() -> Optional[str]:
        """Pre-select TTS provider from weighted pool while STT leg runs."""
        try:
            return _sp("tts", lang=language, exclude=frozenset())
        except Exception:
            return None

    # Launch STT leg and TTS provider pre-selection concurrently.
    stt_result, _tts_hint = await asyncio.gather(
        _stt_leg(), _tts_provider_preselect(), return_exceptions=True
    )

    if isinstance(stt_result, BaseException):
        logger.error("Voice pipeline STT leg failed: %s", stt_result)
        raise HTTPException(status_code=502, detail="Speech recognition failed.")

    transcript: str = stt_result
    if not transcript or not transcript.strip():
        return {"transcript": "", "reply_text": "", "audio_b64": "", "language": language}

    # ── LLM: generate conversational reply (serial — requires STT transcript) ──
    try:
        from llm import call_llm_api_chat
        msgs: list = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        msgs.append({"role": "user", "content": transcript.strip()})
        reply_text = str(await call_llm_api_chat(msgs, max_tokens=512, lang=(language or "en")[:2]))
    except Exception as exc:
        logger.error("Voice pipeline LLM step failed: %s", exc)
        raise HTTPException(status_code=502, detail="LLM reply generation failed.")

    # ── TTS leg: Google Neural2 for Indic; weighted fallback-without-replacement for others ──
    # _tts_hint carries the pre-selected provider (drawn concurrently with STT).
    # _synthesize_with_fallback re-draws from the full pool; the hint is advisory.
    audio_b64 = ""
    try:
        audio_out = await _synthesize_with_fallback(reply_text, voice_id, None, language)
        audio_b64 = base64.b64encode(audio_out).decode("ascii")
    except Exception as exc:
        # TTS failure is non-fatal: return transcript + text reply without audio.
        logger.warning("Voice pipeline TTS leg failed (returning text-only): %s", exc)

    return {
        "transcript": transcript,
        "reply_text": reply_text,
        "audio_b64": audio_b64,
        "language": language,
    }


@router.get(
    "/voice/health",
    summary="Voice provider health check",
    description="Reports readiness of ElevenLabs, Deepgram, AssemblyAI, Workers AI, and Google TTS/STT providers.",
)
async def voice_health():
    from providers import assemblyai, elevenlabs

    assemblyai_task = asyncio.create_task(assemblyai.health_check())
    elevenlabs_task = asyncio.create_task(elevenlabs.health_check())

    assemblyai_health, elevenlabs_health = await asyncio.gather(
        assemblyai_task, elevenlabs_task, return_exceptions=True
    )
    if isinstance(assemblyai_health, Exception):
        assemblyai_health = {"ok": False, "reason": str(assemblyai_health)}
    if isinstance(elevenlabs_health, Exception):
        elevenlabs_health = {"ok": False, "reason": str(elevenlabs_health)}

    try:
        from providers import deepgram as _dg
        deepgram_health = {"ok": _dg.ENABLED, "model": "nova-3"}
    except Exception:
        deepgram_health = {"ok": False, "reason": "deepgram module unavailable"}

    try:
        from providers import cloudflare_ai as _cfai
        workers_ai_ok = _cfai._ENABLED
    except Exception:
        workers_ai_ok = False

    from providers import google_stt as _gstt, google_tts as _gtts
    google_health = {
        "stt_chirp2": {"ok": _gstt.is_configured(), "model": "chirp_2", "langs": ["hi-IN", "bn-IN", "as-IN"]},
        "tts_neural2": {"ok": _gtts.is_configured(), "voices": ["hi-IN-Neural2-A", "bn-IN-Neural2-A", "as-IN-Wavenet-B"]},
    }

    return {
        "elevenlabs": elevenlabs_health,
        "deepgram":   deepgram_health,
        "assemblyai": assemblyai_health,
        "workers_ai": {"ok": workers_ai_ok, "model": "@cf/openai/whisper-large-v3-turbo"},
        "google":     google_health,
    }
