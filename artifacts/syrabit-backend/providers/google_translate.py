"""Google Cloud Translation v3 — primary translation for Indic languages.

Primary for Hindi (hi), Bengali (bn), and Assamese (as).
Workers AI indictrans2 becomes the fallback when Google Translation is unavailable.
Non-Indic text bypasses Google Translation entirely and goes to Workers AI.

Auth: GOOGLE_APPLICATION_CREDENTIALS_JSON (service account JSON blob).

Pricing: ~$20 / 1M chars.
At $2,000 GCP credits → ~100M chars → ~8 years at current scale (~$19/month total).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
_TOKEN_REFRESH_BUFFER_SEC = 60.0

_INDIC_TARGET_LANGS = frozenset({"hi", "bn", "as", "hi-in", "bn-in", "as-in"})

_LANG_NORMALISE = {
    "hi-IN": "hi",
    "bn-IN": "bn",
    "as-IN": "as",
    "hi-in": "hi",
    "bn-in": "bn",
    "as-in": "as",
}

_token: Optional[str] = None
_token_expiry: float = 0.0
_token_lock = asyncio.Lock()
_creds = None


def _sa_raw() -> str:
    return os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON", "").strip()


def is_configured() -> bool:
    return bool(_sa_raw() and _sa_raw().startswith("{"))


def is_indic_target(lang: str) -> bool:
    normalized = _LANG_NORMALISE.get((lang or "").strip(), (lang or "").lower().strip())
    return normalized in _INDIC_TARGET_LANGS


def _get_project_id() -> str:
    project = (
        os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        or os.environ.get("GCP_PROJECT_ID", "")
        or os.environ.get("VERTEX_PROJECT_ID", "")
    ).strip()
    if not project:
        try:
            raw = _sa_raw()
            if raw:
                info = json.loads(raw)
                project = info.get("project_id", "")
        except Exception:
            pass
    return project


def _load_sa_credentials():
    global _creds
    if _creds is not None:
        return _creds
    raw = _sa_raw()
    if not raw:
        return None
    try:
        info = json.loads(raw)
    except Exception:
        return None
    try:
        from google.oauth2 import service_account
        _creds = service_account.Credentials.from_service_account_info(
            info, scopes=[_SCOPE]
        )
        return _creds
    except Exception as exc:
        logger.warning("[google-translate] Failed to load SA credentials: %s", exc)
        return None


def _refresh_token_sync() -> tuple[str, float]:
    creds = _load_sa_credentials()
    if creds is None:
        raise RuntimeError("No SA credentials for Google Translation")
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


async def translate(
    text: str,
    target_lang: str = "hi",
    source_lang: str = "en",
    *,
    timeout_s: float = 30.0,
) -> Optional[str]:
    """Translate text using Google Cloud Translation v3.

    Returns the translated text, or None on failure/unsupported language.
    Only handles Indic target languages (hi, bn, as). Non-Indic targets
    return None so callers bypass Google Translation entirely.

    Args:
        text: Text to translate (up to 30KB per request).
        target_lang: ISO 639-1 code — hi, bn, as (or BCP-47: hi-IN, bn-IN, as-IN).
        source_lang: Source language ISO code (en by default).
        timeout_s: HTTP timeout.
    """
    if not text:
        return None

    lang = _LANG_NORMALISE.get(target_lang, _LANG_NORMALISE.get(target_lang.lower().strip(), target_lang.lower().strip()))
    if lang not in _INDIC_TARGET_LANGS:
        logger.debug("[google-translate] %s not an Indic target — bypassing", target_lang)
        return None

    if not is_configured():
        logger.debug("[google-translate] not configured")
        return None

    project = _get_project_id()
    if not project:
        logger.warning("[google-translate] GCP project ID not set")
        return None

    src = _LANG_NORMALISE.get(source_lang, source_lang)
    payload = {
        "contents": [text[:30000]],
        "targetLanguageCode": lang,
        "sourceLanguageCode": src,
        "mimeType": "text/plain",
    }

    url = (
        f"https://translation.googleapis.com/v3/projects/{project}"
        f"/locations/global:translateText"
    )

    try:
        token = await _get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
    except Exception as exc:
        logger.warning("[google-translate] Auth failed: %s", exc)
        return None

    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "[google-translate] HTTP %d: %s",
            exc.response.status_code,
            exc.response.text[:300],
        )
        return None
    except Exception as exc:
        logger.warning("[google-translate] translate failed: %s: %s", type(exc).__name__, str(exc)[:200])
        return None

    elapsed_ms = (time.perf_counter() - t0) * 1000
    translations = data.get("translations", [])
    if not translations:
        logger.warning("[google-translate] empty translations response (%.0fms)", elapsed_ms)
        return None

    result = translations[0].get("translatedText", "")
    if result:
        logger.info(
            "[google-translate] v3 src=%s tgt=%s chars_in=%d chars_out=%d (%.0fms)",
            src, lang, len(text), len(result), elapsed_ms,
        )
    return result or None
