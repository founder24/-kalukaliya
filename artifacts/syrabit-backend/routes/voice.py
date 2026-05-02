"""
routes.voice — Voice API endpoints (TTS, STT, two-leg voice dispatch).

POST /api/voice/tts
  Text-to-speech via the PROVIDER_PRIORITY weighted round-robin.
  Primary: Cartesia Sonic-2. Falls back to Workers AI Deepgram.
  Returns audio/mpeg bytes (mp3).

POST /api/voice/stt
  Speech-to-text via the PROVIDER_PRIORITY weighted round-robin.
  Primary: AssemblyAI "best". Falls back to Workers AI Whisper.
  Accepts multipart/form-data with an audio file.

POST /api/voice/voice
  Two-leg concurrent dispatch: STT (assemblyai → workers_ai) in parallel
  with prompt processing, then TTS (cartesia → elevenlabs → workers_ai)
  on the reply. Returns { transcript, reply_text, audio_b64 }.

GET  /api/voice/voices
  Lists available Cartesia voices (for admin UI to pick a voice ID).

GET  /api/voice/health
  Reports readiness of all voice providers.
"""
from __future__ import annotations

import base64
import logging
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Depends, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from auth_deps import get_current_user

logger = logging.getLogger("routes.voice")

router = APIRouter(tags=["voice"])


class TtsRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000, description="Text to synthesize")
    voice_id: Optional[str] = Field(None, description="Cartesia voice UUID (uses CARTESIA_VOICE_ID default if omitted)")
    language: str = Field("en", description="BCP-47 language code (en, hi, as, bn, ...)")
    model_id: Optional[str] = Field(None, description="Cartesia model ID (default: sonic-2)")


class VoiceRequest(BaseModel):
    language: str = Field("en", description="BCP-47 language code for STT + TTS")
    voice_id: Optional[str] = Field(None, description="Cartesia voice UUID for TTS")
    system_prompt: Optional[str] = Field(None, description="System prompt for the LLM reply step")


# ── TTS helpers ───────────────────────────────────────────────────────────────

async def _tts_cartesia(
    text: str,
    voice_id: Optional[str],
    model_id: Optional[str],
    language: str,
) -> bytes:
    """Attempt TTS via Cartesia. Raises on failure."""
    from providers import cartesia
    if not cartesia.ENABLED:
        raise RuntimeError("Cartesia not available")
    return await cartesia.synthesize(
        text,
        voice_id=voice_id or None,
        model_id=model_id or None,
        language=language,
    )


async def _tts_workers_ai(text: str, language: str) -> bytes:
    """Fallback TTS via Workers AI Deepgram Aura model. Returns mp3 bytes."""
    from providers.cloudflare_ai import speak as _cf_speak
    return await _cf_speak(text, lang=language[:2])


async def _synthesize_with_fallback(
    text: str,
    voice_id: Optional[str],
    model_id: Optional[str],
    language: str,
) -> bytes:
    """TTS with weighted round-robin: cartesia → elevenlabs → workers_ai.

    Uses select_provider("tts") for the primary pick, then falls back
    through the PROVIDER_PRIORITY order on error.
    """
    from llm import select_provider
    tried: set = set()

    # First attempt: weighted draw from select_provider.
    primary = select_provider("tts", lang=language)
    tried.add(primary)

    for provider in [primary] + ["cartesia", "workers_ai"]:
        if provider in tried and provider != primary:
            continue
        tried.add(provider)
        try:
            if provider in ("cartesia", "elevenlabs"):
                return await _tts_cartesia(text, voice_id, model_id, language)
            if provider == "workers_ai":
                return await _tts_workers_ai(text, language)
        except Exception as exc:
            logger.warning("TTS %s failed: %s", provider, exc)
            continue

    # Last-resort: Workers AI fallback.
    return await _tts_workers_ai(text, language)


# ── STT helpers ───────────────────────────────────────────────────────────────

async def _stt_assemblyai(audio_bytes: bytes, language: str) -> str:
    """STT via AssemblyAI. Raises on failure."""
    from providers import assemblyai
    if not assemblyai.ENABLED:
        raise RuntimeError("AssemblyAI not available")
    return await assemblyai.transcribe(audio_bytes, language_code=language or None)


async def _stt_workers_ai(audio_bytes: bytes) -> str:
    """Fallback STT via Workers AI Whisper. Returns transcript string."""
    from providers.cloudflare_ai import transcribe as _cf_transcribe
    return await _cf_transcribe(audio_bytes)


async def _transcribe_with_fallback(audio_bytes: bytes, language: str) -> str:
    """STT with weighted round-robin: assemblyai → workers_ai.

    Uses select_provider("stt") for the primary pick.
    """
    from llm import select_provider
    primary = select_provider("stt", lang=language)
    tried: set = {primary}

    try:
        if primary == "assemblyai":
            return await _stt_assemblyai(audio_bytes, language)
        elif primary == "workers_ai":
            return await _stt_workers_ai(audio_bytes)
    except Exception as exc:
        logger.warning("STT %s failed: %s — trying Workers AI fallback", primary, exc)

    # Fallback to Workers AI Whisper.
    try:
        return await _stt_workers_ai(audio_bytes)
    except Exception as exc:
        logger.error("STT Workers AI fallback also failed: %s", exc)
        raise HTTPException(status_code=502, detail="Speech recognition failed.")


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/voice/tts",
    response_class=Response,
    summary="Text-to-speech (weighted: Cartesia → Workers AI)",
    description=(
        "Convert text to speech using the PROVIDER_PRIORITY weighted round-robin. "
        "Primary: Cartesia Sonic-2. Fallback: Workers AI Deepgram Aura. "
        "Returns mp3 audio bytes."
    ),
)
async def text_to_speech(
    body: TtsRequest,
    current_user: dict = Depends(get_current_user),
):
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
            "X-TTS-Chars": str(len(body.text)),
            "X-TTS-Bytes": str(len(audio_bytes)),
        },
    )


@router.post(
    "/voice/stt",
    summary="Speech-to-text (weighted: AssemblyAI → Workers AI Whisper)",
    description=(
        "Transcribe audio using the PROVIDER_PRIORITY weighted round-robin. "
        "Primary: AssemblyAI 'best' model. Fallback: Workers AI Whisper-large-v3-turbo. "
        "Accepts multipart/form-data with 'audio' file field."
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
    summary="Two-leg voice pipeline (STT + LLM + TTS)",
    description=(
        "Full voice pipeline: (1) Transcribe audio via STT (AssemblyAI → Workers AI Whisper), "
        "(2) Generate a reply via the chat LLM, "
        "(3) Synthesize the reply via TTS (Cartesia → Workers AI Deepgram). "
        "Returns { transcript, reply_text, audio_b64 }."
    ),
)
async def voice_pipeline(
    audio: UploadFile = File(..., description="Audio file for STT"),
    language: str = Form("en", description="BCP-47 language code"),
    voice_id: Optional[str] = Form(None, description="Cartesia voice UUID"),
    system_prompt: Optional[str] = Form(None, description="System prompt for the LLM"),
    current_user: dict = Depends(get_current_user),
):
    import asyncio as _asyncio

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file.")
    if len(audio_bytes) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Audio file too large (max 25 MB).")

    # Leg 1 — STT: transcribe the audio.
    try:
        transcript = await _transcribe_with_fallback(audio_bytes, language)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Voice pipeline STT failed: %s", exc)
        raise HTTPException(status_code=502, detail="Speech recognition failed.")

    if not transcript or not transcript.strip():
        return {"transcript": "", "reply_text": "", "audio_b64": ""}

    # Leg 2 — LLM: generate a reply.
    try:
        from llm import call_llm_api_chat
        feature = "assamese_rag_chat" if language == "as" else "english_rag_chat"
        msgs = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        msgs.append({"role": "user", "content": transcript.strip()})
        reply_text = await call_llm_api_chat(msgs, max_tokens=512)
        if hasattr(reply_text, "__str__"):
            reply_text = str(reply_text)
    except Exception as exc:
        logger.error("Voice pipeline LLM failed: %s", exc)
        raise HTTPException(status_code=502, detail="LLM reply generation failed.")

    # Leg 3 — TTS: synthesize the reply.
    try:
        audio_out = await _synthesize_with_fallback(reply_text, voice_id, None, language)
        audio_b64 = base64.b64encode(audio_out).decode("ascii")
    except Exception as exc:
        logger.warning("Voice pipeline TTS failed (returning text only): %s", exc)
        audio_b64 = ""

    return {
        "transcript": transcript,
        "reply_text": reply_text,
        "audio_b64": audio_b64,
        "language": language,
    }


@router.get(
    "/voice/voices",
    summary="List available Cartesia voices",
    description="Returns all voices available in the Cartesia Voice Library.",
)
async def list_voices(current_user: dict = Depends(get_current_user)):
    from providers import cartesia
    if not cartesia.ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Cartesia API key not configured.",
        )
    voices = await cartesia.list_voices()
    return {"voices": voices, "count": len(voices)}


@router.get(
    "/voice/health",
    summary="Voice provider health check",
    description="Reports readiness of Cartesia, AssemblyAI, and Workers AI voice providers.",
)
async def voice_health():
    import asyncio as _asyncio
    from providers import cartesia
    from providers import assemblyai

    cartesia_task = _asyncio.create_task(cartesia.health_check())
    assemblyai_task = _asyncio.create_task(assemblyai.health_check())

    cartesia_health, assemblyai_health = await _asyncio.gather(
        cartesia_task, assemblyai_task, return_exceptions=True
    )
    if isinstance(cartesia_health, Exception):
        cartesia_health = {"ok": False, "reason": str(cartesia_health)}
    if isinstance(assemblyai_health, Exception):
        assemblyai_health = {"ok": False, "reason": str(assemblyai_health)}

    try:
        from providers import cloudflare_ai as _cfai
        workers_ai_ok = _cfai._ENABLED
    except Exception:
        workers_ai_ok = False
    workers_ai_health = {
        "ok": workers_ai_ok,
        "model": "@cf/openai/whisper-large-v3-turbo",
    }

    return {
        "cartesia":   cartesia_health,
        "assemblyai": assemblyai_health,
        "workers_ai": workers_ai_health,
    }
