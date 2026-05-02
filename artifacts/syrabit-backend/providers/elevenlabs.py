"""
providers.elevenlabs — ElevenLabs Text-to-Speech (TTS) via REST API.

Implements async TTS using ElevenLabs' v1 REST API:
  POST /v1/text-to-speech/{voice_id}

Configuration:
  ELEVENLABS_API_KEY   — ElevenLabs API key (BYOK via CF gateway optional)
  ELEVENLABS_VOICE_ID  — default voice ID (required if not passed per call)
  ELEVENLABS_MODEL_ID  — TTS model: "eleven_multilingual_v2" (default)

Returns mp3 bytes. Supported languages: see ElevenLabs multilingual model docs.
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

from config import (
    _ELEVENLABS_KEY,
    ELEVENLABS_VOICE_ID,
    ELEVENLABS_MODEL_ID,
    CF_GATEWAY_ENABLED,
    CF_CACHE_TTL,
    CF_AI_GATEWAY_TOKEN,
    BYOK_PLACEHOLDER,
    cf_gateway_url,
)

logger = logging.getLogger("providers.elevenlabs")

_API_KEY   = _ELEVENLABS_KEY
_VOICE_ID  = ELEVENLABS_VOICE_ID
_MODEL_ID  = ELEVENLABS_MODEL_ID
_TIMEOUT_S = 60.0

ENABLED: bool = bool(_API_KEY and _API_KEY != BYOK_PLACEHOLDER) or (CF_GATEWAY_ENABLED and bool(_API_KEY))

_DIRECT_BASE = "https://api.elevenlabs.io"

if ENABLED:
    logger.info(
        "ElevenLabs TTS ready — model=%s voice=%s byok=%s",
        _MODEL_ID, _VOICE_ID or "(per-call)", (_API_KEY == BYOK_PLACEHOLDER),
    )
else:
    logger.info("ElevenLabs TTS disabled (ELEVENLABS_API_KEY not set)")


def _base_url() -> str:
    if CF_GATEWAY_ENABLED:
        gw = cf_gateway_url("elevenlabs")
        if gw:
            return gw
    return _DIRECT_BASE


def _headers() -> dict:
    h: dict = {"Content-Type": "application/json"}
    if CF_GATEWAY_ENABLED and _API_KEY == BYOK_PLACEHOLDER:
        h["xi-api-key"] = ""
        h["cf-aig-byok-key"] = "true"
        h["cf-aig-cache-ttl"] = str(CF_CACHE_TTL)
        if CF_AI_GATEWAY_TOKEN:
            h["cf-aig-authorization"] = f"Bearer {CF_AI_GATEWAY_TOKEN}"
    else:
        h["xi-api-key"] = _API_KEY
    return h


_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(_TIMEOUT_S),
            http2=True,
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=3),
        )
    return _client


async def close() -> None:
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


async def synthesize(
    text: str,
    *,
    voice_id: Optional[str] = None,
    model_id: Optional[str] = None,
    language_code: Optional[str] = None,
) -> bytes:
    """Synthesize *text* to mp3 bytes via ElevenLabs.

    Args:
        text:          Text to speak (max ~5,000 chars).
        voice_id:      ElevenLabs voice ID. Falls back to ELEVENLABS_VOICE_ID env var.
        model_id:      Model ID. Defaults to ELEVENLABS_MODEL_ID (eleven_multilingual_v2).
        language_code: Optional ISO 639-1 code (e.g. "hi", "en") for language hint.

    Returns:
        Raw mp3 audio bytes.

    Raises:
        RuntimeError: if ElevenLabs is disabled or API call fails.
        ValueError:    if no voice_id is available.
    """
    if not ENABLED:
        raise RuntimeError("ElevenLabs TTS is not enabled (ELEVENLABS_API_KEY not set)")

    vid = voice_id or _VOICE_ID
    if not vid:
        raise ValueError(
            "voice_id is required — set ELEVENLABS_VOICE_ID env var or pass voice_id per call."
        )

    mdl = model_id or _MODEL_ID
    base = _base_url()
    client = _get_client()
    headers = _headers()

    payload: dict = {
        "text": text[:5000],
        "model_id": mdl,
        "output_format": "mp3_44100_128",
    }
    if language_code:
        payload["language_code"] = language_code[:2]

    url = f"{base}/v1/text-to-speech/{vid}"
    try:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        return resp.content
    except httpx.HTTPStatusError as exc:
        logger.error(
            "ElevenLabs TTS HTTP %d: %s",
            exc.response.status_code, exc.response.text[:300],
        )
        raise RuntimeError(f"ElevenLabs TTS failed: HTTP {exc.response.status_code}")
    except Exception as exc:
        logger.error("ElevenLabs TTS error: %s", exc)
        raise RuntimeError(f"ElevenLabs TTS error: {exc}")


async def health_check() -> dict:
    """Return provider readiness status."""
    if not ENABLED:
        return {"ok": False, "reason": "ELEVENLABS_API_KEY not set"}
    try:
        client = _get_client()
        resp = await client.get(
            f"{_base_url()}/v1/voices",
            headers=_headers(),
            timeout=5.0,
        )
        ok = resp.status_code == 200
        return {"ok": ok, "status_code": resp.status_code, "model": _MODEL_ID}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}
