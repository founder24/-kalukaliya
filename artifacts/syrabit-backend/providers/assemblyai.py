"""
providers.assemblyai — AssemblyAI Speech-to-Text (STT) via REST API.

Implements async transcription using AssemblyAI's v2 REST API:
  POST /v2/upload         — upload audio bytes
  POST /v2/transcript     — submit transcription job
  GET  /v2/transcript/:id — poll until complete

Configuration:
  ASSEMBLYAI_API_KEY   — AssemblyAI API key (required; BYOK via CF gateway optional)
  ASSEMBLYAI_STT_MODEL — transcription model: "best" (default) or "nano"

Language support: AssemblyAI auto-detects language when language_code is omitted.
Supported language codes: en, hi, as (Assamese — mapped to hi fallback), etc.

Typical latency: 15-30s for a 1-minute audio clip on the "best" model.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

import httpx

from config import (
    _ASSEMBLYAI_KEY,
    ASSEMBLYAI_STT_MODEL,
    CF_GATEWAY_ENABLED,
    CF_CACHE_TTL,
    CF_AI_GATEWAY_TOKEN,
    BYOK_PLACEHOLDER,
    cf_gateway_url,
    is_cf_gateway_up,
)

logger = logging.getLogger("providers.assemblyai")

_API_KEY    = _ASSEMBLYAI_KEY
_MODEL      = ASSEMBLYAI_STT_MODEL
_DIRECT_BASE = "https://api.assemblyai.com"
_TIMEOUT_S  = 180.0    # upload + transcription can take up to 3 minutes

ENABLED: bool = bool(_API_KEY and _API_KEY != BYOK_PLACEHOLDER) or (CF_GATEWAY_ENABLED and bool(_API_KEY))


def _base_url() -> str:
    """Return the AssemblyAI base URL — CF AI Gateway when enabled, direct otherwise."""
    if is_cf_gateway_up():
        gw = cf_gateway_url("assemblyai")
        if gw:
            return gw
    return _DIRECT_BASE

_POLL_INTERVAL_S = 3.0   # seconds between status polls
_MAX_POLLS       = 60    # 60 × 3s = 3 minutes max

if ENABLED:
    logger.info("AssemblyAI STT ready — model=%s byok=%s", _MODEL, (_API_KEY == BYOK_PLACEHOLDER))
else:
    logger.info("AssemblyAI STT disabled (ASSEMBLYAI_API_KEY not set)")


def _headers() -> dict:
    """Build request headers for AssemblyAI REST API."""
    h: dict = {
        "Authorization": _API_KEY if _API_KEY != BYOK_PLACEHOLDER else "",
        "Content-Type": "application/json",
    }
    if CF_GATEWAY_ENABLED and _API_KEY == BYOK_PLACEHOLDER:
        h["cf-aig-byok-key"] = "true"
        h["cf-aig-cache-ttl"] = str(CF_CACHE_TTL)
        if CF_AI_GATEWAY_TOKEN:
            h["cf-aig-authorization"] = f"Bearer {CF_AI_GATEWAY_TOKEN}"
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


async def transcribe(
    audio_bytes: bytes,
    *,
    language_code: Optional[str] = None,
    model: Optional[str] = None,
) -> str:
    """Transcribe *audio_bytes* and return the transcript text.

    Args:
        audio_bytes:   Raw audio data (mp3, wav, flac, ogg, m4a, webm).
        language_code: BCP-47 language code.  ``None`` = auto-detect.
                       ``"as"`` (Assamese) is not natively supported —
                       falls back to Hindi ("hi") for best Indic accuracy.
        model:         Override the default STT model ("best" or "nano").

    Returns:
        Transcript string (may be empty for silent audio).

    Raises:
        RuntimeError: AssemblyAI API error or timeout.
    """
    if not ENABLED:
        raise RuntimeError("AssemblyAI STT is not enabled (ASSEMBLYAI_API_KEY not set)")

    stt_model = model or _MODEL
    client = _get_client()
    headers = _headers()

    # Map unsupported language codes to best available fallback.
    _LANG_FALLBACK = {"as": "hi", "bn": "hi"}
    api_lang = _LANG_FALLBACK.get(language_code or "", language_code)

    t0 = time.perf_counter()
    base = _base_url()

    # Step 1 — upload audio bytes.
    try:
        up_resp = await client.post(
            f"{base}/v2/upload",
            headers={**headers, "Content-Type": "application/octet-stream"},
            content=audio_bytes,
        )
        up_resp.raise_for_status()
        upload_url = up_resp.json()["upload_url"]
    except httpx.HTTPStatusError as exc:
        logger.error("AssemblyAI upload HTTP %d: %s", exc.response.status_code, exc.response.text[:300])
        raise RuntimeError(f"AssemblyAI upload failed: HTTP {exc.response.status_code}")
    except Exception as exc:
        logger.error("AssemblyAI upload failed: %s", exc)
        raise RuntimeError(f"AssemblyAI upload error: {exc}")

    # Step 2 — submit transcription job.
    payload: dict = {
        "audio_url": upload_url,
        "speech_model": stt_model,
    }
    if api_lang:
        payload["language_code"] = api_lang
    else:
        payload["language_detection"] = True

    try:
        job_resp = await client.post(
            f"{base}/v2/transcript",
            headers=headers,
            json=payload,
        )
        job_resp.raise_for_status()
        job_id = job_resp.json()["id"]
    except httpx.HTTPStatusError as exc:
        logger.error("AssemblyAI transcript submit HTTP %d: %s", exc.response.status_code, exc.response.text[:300])
        raise RuntimeError(f"AssemblyAI transcript submit failed: HTTP {exc.response.status_code}")
    except Exception as exc:
        logger.error("AssemblyAI transcript submit failed: %s", exc)
        raise RuntimeError(f"AssemblyAI transcript submit error: {exc}")

    # Step 3 — poll until complete.
    poll_url = f"{base}/v2/transcript/{job_id}"
    for poll_n in range(_MAX_POLLS):
        await asyncio.sleep(_POLL_INTERVAL_S)
        try:
            poll_resp = await client.get(poll_url, headers=headers)
            poll_resp.raise_for_status()
            data = poll_resp.json()
        except Exception as exc:
            logger.warning("AssemblyAI poll %d failed: %s", poll_n + 1, exc)
            continue

        status = data.get("status", "")
        if status == "completed":
            text = data.get("text") or ""
            latency = round((time.perf_counter() - t0) * 1000)
            logger.info(
                "AssemblyAI STT: %d bytes → %d chars, model=%s lang=%s %dms",
                len(audio_bytes), len(text), stt_model, api_lang or "auto", latency,
            )
            return text
        if status == "error":
            err_msg = data.get("error") or "unknown error"
            logger.error("AssemblyAI transcription error: %s", err_msg)
            raise RuntimeError(f"AssemblyAI transcription failed: {err_msg}")
        # status is "queued" or "processing" — keep polling.

    raise RuntimeError(
        f"AssemblyAI transcription timed out after {_MAX_POLLS * _POLL_INTERVAL_S:.0f}s"
    )


async def health_check() -> dict:
    """Return provider readiness status."""
    if not ENABLED:
        return {"ok": False, "reason": "ASSEMBLYAI_API_KEY not set"}
    try:
        client = _get_client()
        resp = await client.get(
            f"{_base_url()}/v2/transcript?limit=1",
            headers=_headers(),
            timeout=5.0,
        )
        ok = resp.status_code in (200, 204)
        return {"ok": ok, "status_code": resp.status_code, "model": _MODEL}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}
