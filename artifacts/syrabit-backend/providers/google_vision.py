"""Google Cloud Vision — DOCUMENT_TEXT_DETECTION for Indic script OCR.

Handles Devanagari (Hindi, Marathi) and Bengali (Bengali, Assamese) script
documents — past papers, textbooks — where Workers AI vision confidence < 0.80
or the document language is Indic.

Workers AI vision remains primary for Latin-script and general image analysis.

Auth: GOOGLE_APPLICATION_CREDENTIALS_JSON (service account JSON blob).

Pricing: ~$1.50 / 1,000 images (DOCUMENT_TEXT_DETECTION).
At $2,000 credits → ~1.33M document OCR requests.
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

_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
_TOKEN_REFRESH_BUFFER_SEC = 60.0

_INDIC_SCRIPT_LANGS = frozenset({
    "as", "as-in",
    "bn", "bn-in",
    "hi", "hi-in",
    "mr", "mr-in",
    "ne", "ne-in",
    "sa", "sa-in",
})

_DEVANAGARI_LANG_CODES = frozenset({"hi", "hi-in", "mr", "mr-in", "ne", "ne-in", "sa", "sa-in"})
_BENGALI_SCRIPT_LANG_CODES = frozenset({"bn", "bn-in", "as", "as-in"})

_token: Optional[str] = None
_token_expiry: float = 0.0
_token_lock = asyncio.Lock()
_creds = None

_LOW_CONFIDENCE_THRESHOLD = 0.80


def _sa_raw() -> str:
    return os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON", "").strip()


def is_configured() -> bool:
    return bool(_sa_raw() and _sa_raw().startswith("{"))


def _normalise_lang(lang: str) -> str:
    return (lang or "").lower().strip().replace("_", "-")


def is_indic_script_lang(lang: str) -> bool:
    return _normalise_lang(lang) in _INDIC_SCRIPT_LANGS


def should_use_google_vision(
    *,
    lang: Optional[str] = None,
    workers_ai_confidence: Optional[float] = None,
) -> bool:
    """Return True if Google Vision should be used for OCR.

    Triggers when:
    - detected/requested language is Indic (Devanagari or Bengali script), OR
    - Workers AI vision returned a confidence below the LOW_CONFIDENCE_THRESHOLD.
    """
    if lang and is_indic_script_lang(lang):
        return True
    if workers_ai_confidence is not None and workers_ai_confidence < _LOW_CONFIDENCE_THRESHOLD:
        return True
    return False


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
        logger.warning("[google-vision] Failed to load SA credentials: %s", exc)
        return None


def _refresh_token_sync() -> tuple[str, float]:
    creds = _load_sa_credentials()
    if creds is None:
        raise RuntimeError("No SA credentials for Google Vision")
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


def _detect_script_language_hints(lang: Optional[str]) -> list[str]:
    """Return language hints for Vision API based on detected language."""
    if not lang:
        return []
    lang_norm = _normalise_lang(lang)
    if lang_norm in _DEVANAGARI_LANG_CODES:
        return ["hi", "mr"]
    if lang_norm in _BENGALI_SCRIPT_LANG_CODES:
        return ["bn", "as"]
    return []


async def ocr_document(
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
    *,
    lang: Optional[str] = None,
    timeout_s: float = 60.0,
) -> dict:
    """Extract text from a document image using Google Cloud Vision DOCUMENT_TEXT_DETECTION.

    Optimised for Devanagari and Bengali script documents (past papers, textbooks).

    Returns a dict with:
        text: str — full extracted text
        confidence: float — overall detection confidence (0.0–1.0)
        pages: int — number of detected pages
        provider: str — always "google_vision"
        error: str — set only on failure

    Args:
        image_bytes: Raw image bytes (JPEG, PNG, WebP, GIF, TIFF, PDF).
        mime_type: MIME type of the image.
        lang: Optional language hint (hi, bn, as) for better accuracy.
        timeout_s: HTTP request timeout.
    """
    if not is_configured():
        return {"error": "Google Vision not configured — set GOOGLE_APPLICATION_CREDENTIALS_JSON"}

    image_b64 = base64.b64encode(image_bytes).decode()
    lang_hints = _detect_script_language_hints(lang)

    request_body: dict = {
        "requests": [
            {
                "image": {"content": image_b64},
                "features": [{"type": "DOCUMENT_TEXT_DETECTION", "maxResults": 1}],
                "imageContext": {},
            }
        ]
    }

    if lang_hints:
        request_body["requests"][0]["imageContext"]["languageHints"] = lang_hints

    url = "https://vision.googleapis.com/v1/images:annotate"

    try:
        token = await _get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
    except Exception as exc:
        logger.warning("[google-vision] Auth failed: %s", exc)
        return {"error": f"Auth failed: {exc}"}

    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(url, json=request_body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "[google-vision] HTTP %d: %s",
            exc.response.status_code,
            exc.response.text[:300],
        )
        return {"error": f"HTTP {exc.response.status_code}"}
    except Exception as exc:
        logger.warning("[google-vision] ocr_document failed: %s: %s", type(exc).__name__, str(exc)[:200])
        return {"error": str(exc)[:200]}

    elapsed_ms = (time.perf_counter() - t0) * 1000
    responses = data.get("responses", [])
    if not responses:
        return {"error": "No responses from Vision API", "provider": "google_vision"}

    resp_data = responses[0]
    if "error" in resp_data:
        err = resp_data["error"]
        logger.warning("[google-vision] API error: %s", err)
        return {"error": err.get("message", str(err)), "provider": "google_vision"}

    full_annotation = resp_data.get("fullTextAnnotation", {})
    text = full_annotation.get("text", "")

    pages = full_annotation.get("pages", [])
    page_count = len(pages)

    confidence = 0.0
    if pages:
        page_confidences = []
        for page in pages:
            for block in page.get("blocks", []):
                bc = block.get("confidence", 0.0)
                if bc > 0:
                    page_confidences.append(bc)
        if page_confidences:
            confidence = sum(page_confidences) / len(page_confidences)

    logger.info(
        "[google-vision] DOCUMENT_TEXT_DETECTION lang=%s text=%d chars confidence=%.2f pages=%d (%.0fms)",
        lang or "auto", len(text), confidence, page_count, elapsed_ms,
    )
    try:
        from providers.gcp_counters import inc_vision as _inc_vision
        _inc_vision()
    except Exception:
        pass

    return {
        "text": text,
        "confidence": round(confidence, 3),
        "pages": page_count,
        "provider": "google_vision",
        "lang_hints": lang_hints,
    }
