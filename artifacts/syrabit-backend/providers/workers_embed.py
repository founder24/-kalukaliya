"""
providers.workers_embed — Custom Workers-AI embedding worker client (Task #382).

Talks to the sibling Cloudflare Worker at ``WORKERS_EMBED_URL`` (deployed
from ``artifacts/syrabit/workers/embed-worker/``) which mean-pools
Gemma-300M + Qwen3-0.6B representations into a fixed 1024-dim vector
that matches the existing Pinecone serverless index. This is the primary
embed path when ``EMBED_PROVIDER_PRIMARY=workers_ai_custom`` (the new
default after Task #382).

Auth is a shared secret in the ``X-Embed-Secret`` header — the worker
rejects requests that don't match its own ``EMBED_SHARED_SECRET``
binding.

Configuration (env vars)
------------------------
  WORKERS_EMBED_URL         — base URL of the embedding worker
                              (e.g. https://embed.syrabit.ai)
  WORKERS_EMBED_SECRET      — 256-bit hex shared secret
  WORKERS_EMBED_DIMS        — expected output dim (default: 1024)
  WORKERS_EMBED_MAX_BATCH   — max texts per batch (default: 32)
  WORKERS_EMBED_TIMEOUT_S   — HTTP timeout seconds (default: 20)
  WORKERS_EMBED_RETRIES     — retry count on transient failure
                              (default: 2 — 3 total attempts)

The module is **soft-fail by contract**: the public ``embed()`` /
``embed_query()`` / ``embed_documents()`` calls raise ``RuntimeError``
on a hard failure so the dispatcher in ``llm.py`` can log and exclude
the provider for the next exclude-loop tick. The HTTP path itself does
internal retries with bounded backoff before surfacing.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import List, Optional

import httpx

logger = logging.getLogger("providers.workers_embed")

# ── Config ────────────────────────────────────────────────────────────────────
_URL          = os.environ.get("WORKERS_EMBED_URL", "").strip().rstrip("/")
_SECRET       = os.environ.get("WORKERS_EMBED_SECRET", "").strip()
_DIMS         = int(os.environ.get("WORKERS_EMBED_DIMS", "1024") or "1024")
_MAX_BATCH    = int(os.environ.get("WORKERS_EMBED_MAX_BATCH", "32") or "32")
_TIMEOUT      = float(os.environ.get("WORKERS_EMBED_TIMEOUT_S", "20") or "20")
_RETRIES      = int(os.environ.get("WORKERS_EMBED_RETRIES", "2") or "2")

# V4 §2 acceptance banner (B2, 2026-05-06): operators / log scrapers grep
# for the exact string `embed_model=gemma-300m+qwen3-0.6b via embed.syrabit.ai`
# to confirm the worker-AI custom embed path is the active primary on the
# pod. Emitted once per process at module import (cheap; no I/O). When URL
# or SECRET is missing the banner is suppressed and llm.py raises a hard
# RuntimeError on the first embed attempt — fail-loud per V4 §12.
if _URL and _SECRET:
    logger.info(
        "embed_model=gemma-300m+qwen3-0.6b via %s "
        "(V4 §2 primary, dims=%d, max_batch=%d)",
        _URL, _DIMS, _MAX_BATCH,
    )
elif _URL or _SECRET:
    logger.warning(
        "workers_embed: partial config (URL set=%s, SECRET set=%s) — "
        "primary embed path will fail until both are set; "
        "V4 §3 failover to Vertex requires RAG_EMBEDDING_PROVIDER=fallback_vertex",
        bool(_URL), bool(_SECRET),
    )
else:
    logger.info(
        "workers_embed: not configured (WORKERS_EMBED_URL/SECRET unset) — "
        "EMBED_PROVIDER_PRIMARY must be flipped off workers_ai_custom or "
        "embed calls will raise on first use (V4 §3 manual override)"
    )

ENABLED: bool = bool(_URL and _SECRET)

if ENABLED:
    logger.info(
        "Workers AI custom embed ready — url=%s dims=%d batch=%d",
        _URL, _DIMS, _MAX_BATCH,
    )
else:
    logger.info(
        "Workers AI custom embed disabled — set WORKERS_EMBED_URL + "
        "WORKERS_EMBED_SECRET to enable"
    )


# ── HTTP client (lazy, shared) ───────────────────────────────────────────────
_client: Optional[httpx.AsyncClient] = None
_client_lock = asyncio.Lock()


async def _get_client() -> httpx.AsyncClient:
    global _client
    async with _client_lock:
        if _client is None or _client.is_closed:
            _client = httpx.AsyncClient(
                timeout=httpx.Timeout(_TIMEOUT),
                limits=httpx.Limits(
                    max_connections=20, max_keepalive_connections=10,
                ),
            )
    return _client


async def close() -> None:
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


def _headers() -> dict:
    return {
        "Content-Type": "application/json",
        "X-Embed-Secret": _SECRET,
    }


async def _post_embed(texts: List[str], task_type: str) -> List[List[float]]:
    """POST to the worker's /embed endpoint with bounded retries."""
    if not ENABLED:
        raise RuntimeError(
            "workers_embed: WORKERS_EMBED_URL / WORKERS_EMBED_SECRET not set"
        )
    if not texts:
        return []

    body = {"texts": texts, "task_type": task_type}
    last_exc: Optional[BaseException] = None
    client = await _get_client()
    for attempt in range(_RETRIES + 1):
        t0 = time.perf_counter()
        try:
            resp = await client.post(
                f"{_URL}/embed",
                headers=_headers(),
                json=body,
            )
            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                raise RuntimeError(
                    f"workers_embed: HTTP {resp.status_code}: {resp.text[:120]}"
                )
            resp.raise_for_status()
            data = resp.json()
            vectors = data.get("vectors") or []
            if not isinstance(vectors, list) or len(vectors) != len(texts):
                raise RuntimeError(
                    f"workers_embed: unexpected response shape "
                    f"({len(vectors)} vectors for {len(texts)} texts)"
                )
            for v in vectors:
                if not isinstance(v, list) or len(v) != _DIMS:
                    raise RuntimeError(
                        f"workers_embed: vector dim mismatch "
                        f"(got {len(v) if isinstance(v, list) else type(v).__name__}, "
                        f"want {_DIMS})"
                    )
            logger.debug(
                "[workers_embed] embed n=%d dims=%d %dms attempt=%d",
                len(texts), _DIMS,
                round((time.perf_counter() - t0) * 1000), attempt,
            )
            return vectors
        except Exception as exc:
            last_exc = exc
            if attempt >= _RETRIES:
                break
            backoff = min(2 ** attempt * 0.25, 2.0)
            logger.warning(
                "[workers_embed] attempt %d/%d failed: %s — retrying in %.1fs",
                attempt + 1, _RETRIES + 1, exc, backoff,
            )
            await asyncio.sleep(backoff)

    raise RuntimeError(f"workers_embed: exhausted retries — {last_exc}")


# ── Public API ───────────────────────────────────────────────────────────────
async def embed(
    texts: List[str],
    *,
    input_type: str = "search_document",
) -> List[List[float]]:
    """Embed a batch of texts. Splits into ``WORKERS_EMBED_MAX_BATCH``
    sub-batches when the input exceeds the worker's per-request cap.

    ``input_type`` accepts the Cohere-style values used elsewhere in
    the backend (``search_document`` / ``search_query``); it is mapped
    onto the worker's ``task_type`` parameter as a passthrough.
    """
    if not texts:
        return []
    out: List[List[float]] = []
    for start in range(0, len(texts), _MAX_BATCH):
        chunk = texts[start: start + _MAX_BATCH]
        out.extend(await _post_embed(chunk, task_type=input_type))
    return out


async def embed_query(text: str) -> List[float]:
    """Embed a single query. Returns ``[]`` on hard failure."""
    if not text:
        return []
    try:
        vecs = await embed([text], input_type="search_query")
    except Exception as exc:
        logger.warning("[workers_embed] embed_query failed: %s", exc)
        return []
    return vecs[0] if vecs else []


async def embed_documents(texts: List[str]) -> List[List[float]]:
    """Embed a list of document texts (uses ``search_document`` task type)."""
    return await embed(texts, input_type="search_document")


async def health_check() -> dict:
    """Probe the worker's /health endpoint and return a status dict."""
    if not ENABLED:
        return {
            "ok": False,
            "configured": False,
            "reason": "WORKERS_EMBED_URL / WORKERS_EMBED_SECRET not set",
        }
    t0 = time.perf_counter()
    try:
        client = await _get_client()
        resp = await client.get(f"{_URL}/health", headers=_headers())
        resp.raise_for_status()
        info = resp.json()
        return {
            "ok": bool(info.get("ok")),
            "configured": True,
            "url": _URL,
            "dims": int(info.get("dims") or 0) or _DIMS,
            "models": info.get("models") or [],
            "version": info.get("version"),
            "latency_ms": round((time.perf_counter() - t0) * 1000),
        }
    except Exception as exc:
        return {
            "ok": False,
            "configured": True,
            "url": _URL,
            "reason": str(exc)[:200],
        }


def is_enabled() -> bool:
    """Return ``True`` when the worker URL + secret are configured."""
    return ENABLED


def expected_dims() -> int:
    """Return the embedding dimension this provider will return."""
    return _DIMS
