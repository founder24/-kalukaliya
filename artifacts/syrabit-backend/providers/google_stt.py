"""Google Cloud Speech-to-Text v2 — Chirp_2 model for Indic languages.

Primary STT for Hindi (hi-IN), Bengali (bn-IN), and Assamese (as-IN).
Sarvam Saaras remains primary for English and other non-Indic languages.
Workers AI Whisper is used as fallback when both Chirp_2 and Sarvam fail.

Auth: GOOGLE_APPLICATION_CREDENTIALS_JSON only — service account JSON blob.
No API-key auth path: all GCP calls use service-account token exchange.

Pricing (Chirp_2): ~$0.016 / minute of audio.
At $2,000 GCP credit grant → ~125,000 minutes (~2,083 hours) of audio.
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

_INDIC_LANGUAGE_CODES = frozenset({"hi-in", "bn-in", "as-in", "hi", "bn", "as"})

_LANG_CODE_MAP = {
    "hi": "hi-IN",
    "bn": "bn-IN",
    "as": "as-IN",
    "hi-in": "hi-IN",
    "bn-in": "bn-IN",
    "as-in": "as-IN",
    "hi-IN": "hi-IN",
    "bn-IN": "bn-IN",
    "as-IN": "as-IN",
}

_CHIRP2_MODEL = "chirp_2"
_STT_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
_TOKEN_REFRESH_BUFFER_SEC = 60.0

_token: Optional[str] = None
_token_expiry: float = 0.0
_token_lock = asyncio.Lock()
_creds = None


def _sa_raw() -> str:
    return os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON", "").strip()


def is_configured() -> bool:
    return bool(_sa_raw() and _sa_raw().startswith("{"))


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
        logger.warning("[google-stt] GOOGLE_APPLICATION_CREDENTIALS_JSON is not valid JSON")
        return None
    try:
        from google.oauth2 import service_account
        _creds = service_account.Credentials.from_service_account_info(
            info, scopes=[_STT_SCOPE]
        )
        return _creds
    except Exception as exc:
        logger.warning("[google-stt] Failed to load SA credentials: %s", exc)
        return None


def _refresh_token_sync() -> tuple[str, float]:
    creds = _load_sa_credentials()
    if creds is None:
        raise RuntimeError("No service account credentials available for Google STT")
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


def _get_project_id() -> str:
    project = (
        os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        or os.environ.get("GCP_PROJECT_ID", "")
        or os.environ.get("VERTEX_PROJECT_ID", "")
    ).strip()
    if not project:
        raw = _sa_raw()
        if raw.startswith("{"):
            try:
                info = json.loads(raw)
                project = info.get("project_id", "")
            except Exception:
                pass
    return project


async def transcribe_indic(
    audio_bytes: bytes,
    language_code: str = "hi-IN",
    *,
    sample_rate_hz: int = 16000,
    audio_encoding: str = "LINEAR16",
    timeout_s: float = 120.0,
) -> Optional[str]:
    """Transcribe Indic language audio using Google Cloud Speech-to-Text v2 Chirp_2.

    Returns the transcript string, or None on failure so callers can fall back.

    Args:
        audio_bytes: Raw audio bytes.
        language_code: BCP-47 code — hi-IN, bn-IN, or as-IN.
        sample_rate_hz: Sample rate (16000 recommended for Chirp_2).
        audio_encoding: Audio encoding format (LINEAR16, FLAC, MP3, WEBM_OPUS, etc.).
        timeout_s: HTTP timeout in seconds.
    """
    if not is_configured():
        logger.debug("[google-stt] not configured — skipping Indic STT")
        return None

    lang = _LANG_CODE_MAP.get(language_code.lower().strip(), language_code)
    if lang.lower().replace("-", "").replace("_", "") not in {
        "hiin", "bnin", "asin", "hi", "bn", "as"
    }:
        logger.debug("[google-stt] %s is not an Indic language — skipping", lang)
        return None

    project = _get_project_id()
    if not project:
        logger.warning("[google-stt] GCP project ID not set — set GOOGLE_CLOUD_PROJECT or VERTEX_PROJECT_ID")
        return None

    audio_b64 = base64.b64encode(audio_bytes).decode()

    payload = {
        "config": {
            "model": _CHIRP2_MODEL,
            "languageCodes": [lang],
            "audioChannelCount": 1,
        },
        "content": audio_b64,
    }

    if audio_encoding and audio_encoding != "LINEAR16":
        payload["config"]["explicitDecodingConfig"] = {
            "encoding": audio_encoding,
            "sampleRateHertz": sample_rate_hz,
            "audioChannelCount": 1,
        }

    url = (
        f"https://speech.googleapis.com/v2/projects/{project}"
        f"/locations/global/recognizers/_:recognize"
    )

    try:
        token = await _get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
    except Exception as exc:
        logger.warning("[google-stt] Auth failed: %s", exc)
        return None

    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "[google-stt] HTTP %d: %s",
            exc.response.status_code,
            exc.response.text[:300],
        )
        return None
    except Exception as exc:
        logger.warning("[google-stt] transcribe_indic failed: %s: %s", type(exc).__name__, str(exc)[:200])
        return None

    elapsed_ms = (time.perf_counter() - t0) * 1000
    results = data.get("results", [])
    if not results:
        logger.debug("[google-stt] no transcription results returned (%.0fms)", elapsed_ms)
        return None

    parts = []
    for r in results:
        alts = r.get("alternatives", [])
        if alts:
            parts.append(alts[0].get("transcript", ""))

    transcript = " ".join(p for p in parts if p).strip()
    if transcript:
        logger.info(
            "[google-stt] Chirp_2 lang=%s audio=%d bytes transcript=%d chars (%.0fms)",
            lang, len(audio_bytes), len(transcript), elapsed_ms,
        )
    return transcript or None
