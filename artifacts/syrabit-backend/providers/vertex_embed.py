"""Vertex AI Embeddings — text-embedding-004 fallback for long-form content.

Position-2 in the embed fallback chain:
  workers_ai(bge-m3) → vertex_embed → cohere → voyage_ai

Triggers only when:
  - Content length > 2048 tokens (long-form: papers, chapters), OR
  - Workers AI embed is saturated (cooldown active).

Auth: GOOGLE_APPLICATION_CREDENTIALS_JSON (service account JSON blob).

Model: text-embedding-004 (768-dim — note: smaller than bge-large 1024-dim).
WARNING: Vertex embed vectors are in a different embedding space than
Workers AI bge-large-en-v1.5. Do NOT mix them in the same Vectorize index.
This provider is intended as a long-doc fallback where the text exceeds
Workers AI token limits, NOT as a primary embedding provider.

Pricing: ~$0.00013 / 1K chars (text-embedding-004).
At $2,000 credits → ~15 billion chars embedded.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import List, Optional

import httpx

logger = logging.getLogger(__name__)

_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
_TOKEN_REFRESH_BUFFER_SEC = 60.0
_LONG_FORM_TOKEN_THRESHOLD = 2048
_VERTEX_EMBED_MODEL = "text-embedding-004"
_VERTEX_EMBED_DIMENSIONS = 768

_token: Optional[str] = None
_token_expiry: float = 0.0
_token_lock = asyncio.Lock()
_creds = None


def _sa_raw() -> str:
    return os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON", "").strip()


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


def _get_location() -> str:
    return (
        os.environ.get("VERTEX_LOCATION", "us-central1") or "us-central1"
    ).strip()


def is_configured() -> bool:
    return bool(_sa_raw() and _sa_raw().startswith("{") and _get_project_id())


def is_long_form(text: str) -> bool:
    """Rough token estimate: 1 token ≈ 4 chars."""
    return len(text) > (_LONG_FORM_TOKEN_THRESHOLD * 4)


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
        logger.warning("[vertex-embed] Failed to load SA credentials: %s", exc)
        return None


def _refresh_token_sync() -> tuple[str, float]:
    creds = _load_sa_credentials()
    if creds is None:
        raise RuntimeError("No SA credentials for Vertex AI Embeddings")
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


async def embed_text(
    text: str,
    task_type: str = "RETRIEVAL_DOCUMENT",
    *,
    timeout_s: float = 30.0,
) -> Optional[List[float]]:
    """Embed text using Vertex AI text-embedding-004.

    Returns a 768-dim float vector, or None on failure.

    IMPORTANT: Vertex vectors are in a different space than Workers AI
    bge-large-en-v1.5 (1024-dim). Only use this as a fallback for
    long-form content that exceeds Workers AI token limits — do NOT
    mix with standard bge vectors in the same index.

    Args:
        text: Input text (up to ~3000 tokens).
        task_type: RETRIEVAL_DOCUMENT, RETRIEVAL_QUERY, SEMANTIC_SIMILARITY, etc.
        timeout_s: HTTP timeout.
    """
    if not is_configured():
        logger.debug("[vertex-embed] not configured")
        return None

    project = _get_project_id()
    location = _get_location()

    url = (
        f"https://{location}-aiplatform.googleapis.com/v1/projects/{project}"
        f"/locations/{location}/publishers/google/models/{_VERTEX_EMBED_MODEL}:predict"
    )

    payload = {
        "instances": [
            {
                "content": text[:32000],
                "task_type": task_type,
            }
        ]
    }

    try:
        token = await _get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
    except Exception as exc:
        logger.warning("[vertex-embed] Auth failed: %s", exc)
        return None

    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "[vertex-embed] HTTP %d: %s",
            exc.response.status_code,
            exc.response.text[:300],
        )
        return None
    except Exception as exc:
        logger.warning("[vertex-embed] embed_text failed: %s: %s", type(exc).__name__, str(exc)[:200])
        return None

    elapsed_ms = (time.perf_counter() - t0) * 1000
    predictions = data.get("predictions", [])
    if not predictions:
        logger.warning("[vertex-embed] empty predictions (%.0fms)", elapsed_ms)
        return None

    embedding = predictions[0].get("embeddings", {})
    values = embedding.get("values", [])
    if not values:
        logger.warning("[vertex-embed] no values in prediction (%.0fms)", elapsed_ms)
        return None

    vec = [float(v) for v in values]
    logger.info(
        "[vertex-embed] text-embedding-004 dim=%d text=%d chars (%.0fms)",
        len(vec), len(text), elapsed_ms,
    )
    try:
        from providers.gcp_counters import inc_embed as _inc_embed
        _inc_embed(len(text[:32000]))
    except Exception:
        pass
    return vec


async def embed_batch(
    texts: List[str],
    task_type: str = "RETRIEVAL_DOCUMENT",
) -> List[Optional[List[float]]]:
    """Embed multiple texts concurrently."""
    if not texts:
        return []
    results = await asyncio.gather(
        *[embed_text(t, task_type) for t in texts],
        return_exceptions=True,
    )
    out = []
    for r in results:
        if isinstance(r, BaseException):
            logger.warning("[vertex-embed] batch item failed: %s", r)
            out.append(None)
        else:
            out.append(r)
    return out
