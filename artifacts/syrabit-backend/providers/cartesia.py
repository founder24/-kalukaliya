"""providers.cartesia — Cartesia Sonic TTS via REST.

Used as the primary TTS provider for the Syra admin orb when an
``CARTESIA_VOICE_EN`` voice id is configured. Cartesia's voice
library carries higher-quality Indian English voices (incl. mature
male "CEO"-type voices) than Deepgram's aura-2 catalogue, which is
why this provider was added in Task #298 round 5 alongside the
JARVIS upgrade.

Configuration:
  CARTESIA_API_KEY    — Cartesia API key (required to enable).
  CARTESIA_MODEL_ID   — Cartesia TTS model id (default ``sonic-2``).
  CARTESIA_VOICE_EN   — Voice id for English (Indian male CEO etc.).
  CARTESIA_VOICE_HI   — Voice id for Hindi (optional fallback).

If no voice id is configured for the requested language, the route
should skip this provider and fall back to Deepgram.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

import httpx

from config import (
    _CARTESIA_KEY,
    CARTESIA_VOICE_EN,
    CARTESIA_VOICE_HI,
    CARTESIA_MODEL_ID,
    BYOK_PLACEHOLDER,
)

logger = logging.getLogger("providers.cartesia")

_API_KEY    = _CARTESIA_KEY
_BASE       = "https://api.cartesia.ai"
_VERSION    = os.environ.get("CARTESIA_API_VERSION", "2024-11-13")
_TIMEOUT_S  = 30.0

# A voice is required for Cartesia to be useful — without one we have
# no idea which Cartesia voice to use, so we report the provider
# disabled rather than crash later. Operators set CARTESIA_VOICE_EN
# to a voice id from the Cartesia dashboard (e.g. an "Indian Accent
# Man — CEO" voice) to opt in.
ENABLED: bool = (
    bool(_API_KEY and _API_KEY != BYOK_PLACEHOLDER)
    and bool(CARTESIA_VOICE_EN or CARTESIA_VOICE_HI)
)

_LANG_TO_VOICE: dict[str, str] = {
    "en": CARTESIA_VOICE_EN,
    "hi": CARTESIA_VOICE_HI or CARTESIA_VOICE_EN,
    "as": CARTESIA_VOICE_HI or CARTESIA_VOICE_EN,  # closest Indic match
}

_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(_TIMEOUT_S),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
    return _client


async def close() -> None:
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


def _voice_for(language: Optional[str], override: Optional[str]) -> str:
    if override:
        return override
    lang_key = (language or "en")[:2].lower()
    return _LANG_TO_VOICE.get(lang_key) or CARTESIA_VOICE_EN


async def synthesize(
    text: str,
    *,
    voice: Optional[str] = None,
    language: Optional[str] = None,
) -> bytes:
    """Synthesize *text* with Cartesia and return MP3 bytes.

    Args:
        text:     Text to synthesize.
        voice:    Cartesia voice id override (else picked by language).
        language: BCP-47 language code; selects default voice.

    Raises:
        RuntimeError: when the provider is not configured or the
            Cartesia API returns a non-2xx response.
    """
    if not ENABLED:
        raise RuntimeError(
            "Cartesia TTS is not enabled "
            "(set CARTESIA_API_KEY + CARTESIA_VOICE_EN)"
        )
    voice_id = _voice_for(language, voice)
    if not voice_id:
        raise RuntimeError("Cartesia: no voice id configured for language")

    headers = {
        "X-API-Key": _API_KEY,
        "Cartesia-Version": _VERSION,
        "Content-Type": "application/json",
    }
    payload = {
        "model_id": CARTESIA_MODEL_ID,
        "transcript": text,
        "voice": {"mode": "id", "id": voice_id},
        "language": (language or "en")[:2].lower(),
        "output_format": {
            "container": "mp3",
            "sample_rate": 44100,
            "bit_rate": 128000,
        },
    }

    client = _get_client()
    t0 = time.perf_counter()
    try:
        resp = await client.post(
            f"{_BASE}/tts/bytes",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        audio_bytes = resp.content
        latency = round((time.perf_counter() - t0) * 1000)
        logger.info(
            "Cartesia TTS: %d chars → %d bytes, voice=%s model=%s %dms",
            len(text), len(audio_bytes), voice_id, CARTESIA_MODEL_ID, latency,
        )
        return audio_bytes
    except httpx.HTTPStatusError as exc:
        logger.error("Cartesia TTS HTTP %d: %s",
                     exc.response.status_code, exc.response.text[:300])
        raise RuntimeError(
            f"Cartesia TTS failed: HTTP {exc.response.status_code}"
        )
    except Exception as exc:
        logger.error("Cartesia TTS failed: %s", exc)
        raise RuntimeError(f"Cartesia TTS error: {exc}")


async def health_check() -> dict:
    if not ENABLED:
        return {
            "ok": False,
            "reason": "CARTESIA_API_KEY or CARTESIA_VOICE_EN missing",
        }
    return {"ok": True, "model": CARTESIA_MODEL_ID,
            "voice_en": bool(CARTESIA_VOICE_EN),
            "voice_hi": bool(CARTESIA_VOICE_HI)}
