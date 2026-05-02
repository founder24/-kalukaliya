"""Google Cloud Text-to-Speech — Neural2 voices for Indic languages.

Primary TTS for Hindi, Bengali, and Assamese. ElevenLabs is primary
for English. Deepgram Aura-2 is the secondary English fallback.
Workers AI is the last-resort fallback for all languages.

Voices:
  hi-IN: hi-IN-Neural2-A (female), hi-IN-Neural2-C (male)
  bn-IN: bn-IN-Neural2-A (female), bn-IN-Neural2-B (male)
  as-IN: as-IN-Wavenet-B (best available for Assamese)

Auth: GOOGLE_APPLICATION_CREDENTIALS_JSON (service account JSON blob).

Pricing (Neural2): ~$16 / 1M chars.
At $2,000 GCP credits → ~125M chars → effectively unlimited at current scale.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_STT_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
_TOKEN_REFRESH_BUFFER_SEC = 60.0

_token: Optional[str] = None
_token_expiry: float = 0.0
_token_lock = asyncio.Lock()
_creds = None

_INDIC_VOICES: dict[str, dict[str, str]] = {
    "hi": {
        "female": "hi-IN-Neural2-A",
        "male":   "hi-IN-Neural2-C",
        "default": "hi-IN-Neural2-A",
        "lang_code": "hi-IN",
    },
    "hi-in": {
        "female": "hi-IN-Neural2-A",
        "male":   "hi-IN-Neural2-C",
        "default": "hi-IN-Neural2-A",
        "lang_code": "hi-IN",
    },
    "bn": {
        "female": "bn-IN-Neural2-A",
        "male":   "bn-IN-Neural2-B",
        "default": "bn-IN-Neural2-A",
        "lang_code": "bn-IN",
    },
    "bn-in": {
        "female": "bn-IN-Neural2-A",
        "male":   "bn-IN-Neural2-B",
        "default": "bn-IN-Neural2-A",
        "lang_code": "bn-IN",
    },
    "as": {
        "female": "as-IN-Wavenet-B",
        "male":   "as-IN-Wavenet-D",
        "default": "as-IN-Wavenet-B",
        "lang_code": "as-IN",
    },
    "as-in": {
        "female": "as-IN-Wavenet-B",
        "male":   "as-IN-Wavenet-D",
        "default": "as-IN-Wavenet-B",
        "lang_code": "as-IN",
    },
}


def _sa_raw() -> str:
    return os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON", "").strip()


def is_configured() -> bool:
    return bool(_sa_raw())


def is_indic_lang(lang: str) -> bool:
    return (lang or "").lower().strip() in _INDIC_VOICES


def _load_sa_credentials():
    global _creds
    if _creds is not None:
        return _creds
    raw = _sa_raw()
    if not raw or not raw.startswith("{"):
        return None
    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("[google-tts] GOOGLE_APPLICATION_CREDENTIALS_JSON is not valid JSON")
        return None
    try:
        from google.oauth2 import service_account
        _creds = service_account.Credentials.from_service_account_info(
            info, scopes=[_STT_SCOPE]
        )
        return _creds
    except Exception as exc:
        logger.warning("[google-tts] Failed to load SA credentials: %s", exc)
        return None


def _refresh_token_sync() -> tuple[str, float]:
    creds = _load_sa_credentials()
    if creds is None:
        raise RuntimeError("No service account credentials for Google TTS")
    from google.auth.transport.requests import Request as _Req
    creds.refresh(_Req())
    from datetime import datetime, timezone
    if creds.expiry is None:
        ttl = 3600.0
    else:
        exp_utc = creds.expiry.replace(tzinfo=timezone.utc).timestamp()
        ttl = max(60.0, exp_utc - datetime.now(tz=timezone.utc).timestamp())
    return creds.token, time.monotonic() + ttl


async def _get_access_token() -> str:
    global _token, _token_expiry
    async with _token_lock:
        now = time.monotonic()
        if _token and now < (_token_expiry - _TOKEN_REFRESH_BUFFER_SEC):
            return _token
        _token, _token_expiry = await asyncio.to_thread(_refresh_token_sync)
        return _token


async def synthesize(
    text: str,
    lang: str = "hi",
    *,
    gender: str = "female",
    audio_encoding: str = "MP3",
    speaking_rate: float = 1.0,
    timeout_s: float = 60.0,
) -> Optional[bytes]:
    """Synthesize Indic text to speech using Google Cloud TTS Neural2.

    Returns raw audio bytes (MP3 by default), or None on failure.

    Args:
        text: Text to synthesize (max 5000 chars per Google TTS limit).
        lang: ISO language code — hi, bn, as (or hi-IN, bn-IN, as-IN).
        gender: 'female' or 'male'.
        audio_encoding: 'MP3', 'LINEAR16', 'OGG_OPUS'.
        speaking_rate: 0.25–4.0 (1.0 = normal).
        timeout_s: HTTP timeout.
    """
    if not is_configured():
        logger.debug("[google-tts] not configured — skipping")
        return None

    lang_key = lang.lower().strip()
    voice_config = _INDIC_VOICES.get(lang_key)
    if not voice_config:
        logger.debug("[google-tts] %s is not a supported Indic language", lang)
        return None

    voice_name = voice_config.get(gender, voice_config["default"])
    lang_code = voice_config["lang_code"]

    payload = {
        "input": {"text": text[:5000]},
        "voice": {
            "languageCode": lang_code,
            "name": voice_name,
        },
        "audioConfig": {
            "audioEncoding": audio_encoding,
            "speakingRate": speaking_rate,
        },
    }

    url = "https://texttospeech.googleapis.com/v1/text:synthesize"

    try:
        token = await _get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
    except Exception as exc:
        logger.warning("[google-tts] Auth failed: %s", exc)
        return None

    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "[google-tts] HTTP %d: %s",
            exc.response.status_code,
            exc.response.text[:300],
        )
        return None
    except Exception as exc:
        logger.warning("[google-tts] synthesize failed: %s: %s", type(exc).__name__, str(exc)[:200])
        return None

    elapsed_ms = (time.perf_counter() - t0) * 1000
    audio_content = data.get("audioContent")
    if not audio_content:
        logger.warning("[google-tts] no audioContent in response (%.0fms)", elapsed_ms)
        return None

    audio_bytes = base64.b64decode(audio_content)
    logger.info(
        "[google-tts] Neural2 voice=%s text=%d chars audio=%d bytes (%.0fms)",
        voice_name, len(text), len(audio_bytes), elapsed_ms,
    )
    try:
        from providers.gcp_counters import inc_tts as _inc_tts
        _inc_tts(len(text[:5000]))
    except Exception:
        pass
    return audio_bytes
