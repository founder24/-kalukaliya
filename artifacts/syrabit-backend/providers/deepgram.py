"""
providers.deepgram — Deepgram Speech-to-Text (STT) and Text-to-Speech (TTS).

STT: POST /v1/listen  — synchronous pre-recorded transcription via Nova-3.
TTS: POST /v1/speak   — Aura-2 synthesis.

Configuration:
  DEEPGRAM_API_KEY — Deepgram API key (required; BYOK via CF gateway optional)

STT language support: Deepgram Nova-3 supports en, hi, as (Assamese), and many others.
TTS voices: aura-2-en-us (default), aura-2-hi-in, etc.

Typical STT latency: 1-3s for a 1-minute audio clip on Nova-3.
Typical TTS latency: 300-800ms.
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
# Default English voice: aura-2-zeus-en — deep, mature, authoritative
# American male, the closest "CEO" timbre Deepgram offers. Operators
# wanting a true Indian English male voice should set CARTESIA_VOICE_EN
# (Cartesia is preferred over Deepgram in the Syra TTS route when set).
_TTS_VOICE_EN  = os.environ.get("DEEPGRAM_TTS_VOICE_EN",  "aura-2-zeus-en")
_TTS_VOICE_HI  = os.environ.get("DEEPGRAM_TTS_VOICE_HI",  "aura-2-hi-in")
_TTS_VOICE_AS  = os.environ.get("DEEPGRAM_TTS_VOICE_AS",  "aura-2-hi-in")  # Assamese → Hindi voice

_LANG_TO_TTS_VOICE: dict[str, str] = {
    "en": _TTS_VOICE_EN,
    "hi": _TTS_VOICE_HI,
    "as": _TTS_VOICE_AS,
}


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
    logger.info("Deepgram STT/TTS ready — stt_model=%s byok=%s", _STT_MODEL, (_API_KEY == BYOK_PLACEHOLDER))
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


async def synthesize(
    text: str,
    *,
    voice: Optional[str] = None,
    language: Optional[str] = None,
) -> bytes:
    """Synthesize *text* with Deepgram Aura-2 and return mp3 audio bytes.

    Args:
        text:     Text to synthesize (max ~2000 chars per call).
        voice:    Aura-2 voice ID override.  Defaults to language-appropriate voice.
        language: BCP-47 language code used to select the default voice.

    Returns:
        MP3 audio bytes.

    Raises:
        RuntimeError: Deepgram API error.
    """
    if not ENABLED:
        raise RuntimeError("Deepgram TTS is not enabled (DEEPGRAM_API_KEY not set)")

    lang_key = (language or "en")[:2].lower()
    voice_id = voice or _LANG_TO_TTS_VOICE.get(lang_key, _TTS_VOICE_EN)

    client = _get_client()
    base = _base_url()

    t0 = time.perf_counter()
    try:
        resp = await client.post(
            f"{base}/speak",
            headers=_headers("application/json"),
            params={"model": voice_id, "encoding": "mp3"},
            json={"text": text},
        )
        resp.raise_for_status()
        audio_bytes = resp.content
        latency = round((time.perf_counter() - t0) * 1000)
        logger.info(
            "Deepgram TTS: %d chars → %d bytes, voice=%s %dms",
            len(text), len(audio_bytes), voice_id, latency,
        )
        return audio_bytes
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
        return {"ok": ok, "status_code": resp.status_code, "stt_model": _STT_MODEL}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}
