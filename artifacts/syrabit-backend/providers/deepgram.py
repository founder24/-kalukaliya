"""
providers.deepgram — Deepgram Speech-to-Text (STT) + Aura-2 Text-to-Speech (TTS).

STT: POST /v1/listen  — synchronous pre-recorded transcription via Nova-3.
TTS: POST /v1/speak   — synchronous synthesis via Aura-2 (English).

Configuration:
  DEEPGRAM_API_KEY      — Deepgram API key (required; BYOK via CF gateway optional)
  DEEPGRAM_STT_MODEL    — STT model override (default: nova-3)
  DEEPGRAM_TTS_MODEL    — TTS model override (default: aura-2-thalia-en)

STT language support: Deepgram Nova-3 supports en, hi, as (Assamese), and many others.
TTS language support: Deepgram Aura-2 currently English-only (en-US/en-GB voices).

Typical STT latency: 1-3s for a 1-minute audio clip on Nova-3.
Typical TTS latency: 200-500ms for ~100 chars on Aura-2.

Task #552 §G-R (2026-05-09 reversal): Deepgram Aura-2 is now the sole
English-TTS primary (un-retiring the original Task #552 §G removal).
ElevenLabs eleven_multilingual_v2 is the named fallback. Google Cloud TTS
Neural2 remains the sole Indic-TTS specialist (Aura-2 is English-only).
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

import httpx

from config import (
    _DEEPGRAM_KEY,
    CF_GATEWAY_ENABLED,
    CF_CACHE_TTL,
    CF_AI_GATEWAY_TOKEN,
    BYOK_PLACEHOLDER,
    cf_gateway_url,
    is_cf_gateway_up,
)

logger = logging.getLogger("providers.deepgram")

_API_KEY     = _DEEPGRAM_KEY
_DIRECT_BASE = "https://api.deepgram.com/v1"
_TIMEOUT_S   = 60.0

ENABLED: bool = bool(_API_KEY and _API_KEY != BYOK_PLACEHOLDER) or (CF_GATEWAY_ENABLED and bool(_API_KEY))

_STT_MODEL     = os.environ.get("DEEPGRAM_STT_MODEL", "nova-3")
# Task #552 §G-R (2026-05-09) — Aura-2 TTS un-retired as English-TTS primary.
# Default voice "thalia" is a clear, friendly female English voice; override
# via DEEPGRAM_TTS_MODEL (e.g. "aura-2-helios-en", "aura-2-zeus-en").
_TTS_MODEL     = os.environ.get("DEEPGRAM_TTS_MODEL", "aura-2-thalia-en")


def _base_url() -> str:
    if is_cf_gateway_up():
        gw = cf_gateway_url("deepgram")
        if gw:
            return gw
    return _DIRECT_BASE


def _headers(content_type: str = "application/json") -> dict:
    h: dict = {
        "Authorization": f"Token {_API_KEY}" if _API_KEY != BYOK_PLACEHOLDER else "Token ",
        "Content-Type": content_type,
    }
    if CF_GATEWAY_ENABLED and _API_KEY == BYOK_PLACEHOLDER:
        h["cf-aig-byok-key"] = "true"
        h["cf-aig-cache-ttl"] = str(CF_CACHE_TTL)
        if CF_AI_GATEWAY_TOKEN:
            h["cf-aig-authorization"] = f"Bearer {CF_AI_GATEWAY_TOKEN}"
    return h


if ENABLED:
    logger.info(
        "Deepgram ready — stt_model=%s tts_model=%s byok=%s (Task #552 §G-R: TTS un-retired)",
        _STT_MODEL, _TTS_MODEL, (_API_KEY == BYOK_PLACEHOLDER),
    )
else:
    logger.info("Deepgram disabled (DEEPGRAM_API_KEY not set)")


_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(_TIMEOUT_S),
            http2=True,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    return _client


async def close() -> None:
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


async def transcribe(
    audio_bytes: bytes,
    *,
    language_code: Optional[str] = None,
    model: Optional[str] = None,
) -> str:
    """Transcribe *audio_bytes* with Deepgram Nova-3 and return the transcript.

    Args:
        audio_bytes:   Raw audio data (mp3, wav, flac, ogg, m4a, webm).
        language_code: BCP-47 language code.  ``None`` = auto-detect.
        model:         Override the default STT model.

    Returns:
        Transcript string (may be empty for silent audio).

    Raises:
        RuntimeError: Deepgram API error or timeout.
    """
    if not ENABLED:
        raise RuntimeError("Deepgram STT is not enabled (DEEPGRAM_API_KEY not set)")

    stt_model = model or _STT_MODEL
    client = _get_client()
    base = _base_url()

    params: dict = {"model": stt_model, "smart_format": "true"}
    if language_code:
        params["language"] = language_code[:2]

    t0 = time.perf_counter()
    try:
        resp = await client.post(
            f"{base}/listen",
            headers=_headers("audio/mpeg"),
            params=params,
            content=audio_bytes,
        )
        resp.raise_for_status()
        data = resp.json()
        transcript = (
            data.get("results", {})
            .get("channels", [{}])[0]
            .get("alternatives", [{}])[0]
            .get("transcript", "")
        ) or ""
        latency = round((time.perf_counter() - t0) * 1000)
        logger.info(
            "Deepgram STT: %d bytes → %d chars, model=%s lang=%s %dms",
            len(audio_bytes), len(transcript), stt_model, language_code or "auto", latency,
        )
        return transcript
    except httpx.HTTPStatusError as exc:
        logger.error("Deepgram STT HTTP %d: %s", exc.response.status_code, exc.response.text[:300])
        raise RuntimeError(f"Deepgram STT failed: HTTP {exc.response.status_code}")
    except Exception as exc:
        logger.error("Deepgram STT failed: %s", exc)
        raise RuntimeError(f"Deepgram STT error: {exc}")


# Task #552 §G-R (2026-05-09) — Deepgram Aura-2 TTS un-retired as the
# canonical English-TTS primary. ElevenLabs is the named fallback; Google
# Neural2 still owns Indic TTS (Aura-2 is English-only).
async def synthesize(
    text: str,
    *,
    model: Optional[str] = None,
    encoding: str = "mp3",
) -> bytes:
    """Synthesize *text* to audio bytes via Deepgram Aura-2.

    Args:
        text:     English text to speak (Aura-2 is English-only).
        model:    Voice/model override (default DEEPGRAM_TTS_MODEL = aura-2-thalia-en).
        encoding: Output container — "mp3" (default), "wav", "flac", or "ogg-opus".

    Returns:
        Raw audio bytes in the requested encoding.

    Raises:
        RuntimeError: Deepgram API error or timeout.
    """
    if not ENABLED:
        raise RuntimeError("Deepgram TTS is not enabled (DEEPGRAM_API_KEY not set)")

    # Task #552 §G-R: TTS bypasses CF AI Gateway because the gateway's
    # Deepgram provider slug currently proxies only `/v1/listen` (STT);
    # `/v1/speak` (Aura-2 TTS) returns CF's own 401 wrapper because the
    # upstream auth header is not forwarded for that path. STT continues
    # to use the gateway via _base_url(). BYOK mode is therefore NOT
    # supported for TTS — fail loud (V4 §12, no silent fallback) so the
    # dispatcher chain advances cleanly to ElevenLabs instead of shipping
    # an unauthenticated request to api.deepgram.com that would 401.
    if _API_KEY == BYOK_PLACEHOLDER:
        raise RuntimeError(
            "Deepgram Aura-2 TTS requires a real DEEPGRAM_API_KEY in env "
            "(BYOK-via-CF-Gateway is STT-only — gateway does not proxy "
            "/v1/speak); chain will advance to ElevenLabs"
        )

    tts_model = model or _TTS_MODEL
    client = _get_client()
    base = _DIRECT_BASE

    encoding_map = {
        "mp3":      ("audio/mpeg",      {}),
        "wav":      ("audio/wav",       {"encoding": "linear16", "container": "wav"}),
        "flac":     ("audio/flac",      {"encoding": "flac"}),
        "ogg-opus": ("audio/ogg",       {"encoding": "opus", "container": "ogg"}),
    }
    if encoding not in encoding_map:
        raise ValueError(f"Unsupported encoding {encoding!r}; choose from {list(encoding_map)}")
    _content_type, extra_params = encoding_map[encoding]

    params: dict = {"model": tts_model, **extra_params}

    t0 = time.perf_counter()
    try:
        resp = await client.post(
            f"{base}/speak",
            headers=_headers("application/json"),
            params=params,
            json={"text": text[:2000]},  # Aura-2 hard limit: 2000 chars per request
        )
        resp.raise_for_status()
        audio = resp.content
        latency = round((time.perf_counter() - t0) * 1000)
        logger.info(
            "Deepgram TTS: %d chars → %d bytes, model=%s enc=%s %dms",
            len(text), len(audio), tts_model, encoding, latency,
        )
        return audio
    except httpx.HTTPStatusError as exc:
        logger.error("Deepgram TTS HTTP %d: %s", exc.response.status_code, exc.response.text[:300])
        raise RuntimeError(f"Deepgram TTS failed: HTTP {exc.response.status_code}")
    except Exception as exc:
        logger.error("Deepgram TTS failed: %s", exc)
        raise RuntimeError(f"Deepgram TTS error: {exc}")


async def health_check() -> dict:
    """Return provider readiness status."""
    if not ENABLED:
        return {"ok": False, "reason": "DEEPGRAM_API_KEY not set"}
    try:
        client = _get_client()
        resp = await client.get(
            f"{_base_url()}/projects",
            headers=_headers(),
            timeout=5.0,
        )
        ok = resp.status_code in (200, 204)
        return {"ok": ok, "status_code": resp.status_code,
                "stt_model": _STT_MODEL, "tts_model": _TTS_MODEL}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}
