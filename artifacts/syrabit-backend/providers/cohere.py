"""
providers.cohere — Cohere embeddings via Cloudflare AI Gateway (BYOK).

All requests route through the CF AI Gateway at:
  {gateway_base}/cohere/v1/embed

BYOK mode: when CF_GATEWAY_ENABLED is true, the local COHERE_API_KEY is
optional — set it in the Cloudflare AI Gateway dashboard and the backend
sends a placeholder with cf-aig-byok-key: true so CF injects the real key.

Embedding models (1024-dim — compatible with the Atlas Vector Search index):
  embed-multilingual-v3.0   — best quality, multilingual (default; ideal for
                               Assamese, Bengali, Hindi + English content)
  embed-english-v3.0        — slightly faster, English-only

input_type values (Cohere-specific, asymmetric fine-tuning):
  "search_document"  — use when indexing content
  "search_query"     — use when embedding a user question

Configuration:
  COHERE_API_KEY        — Cohere API key (optional when CF BYOK is set up)
  COHERE_EMBED_MODEL    — embedding model (default: embed-multilingual-v3.0)
  COHERE_EMBED_PRIMARY  — "true" to use Cohere instead of Workers AI BGE.
                          WARNING: switching requires re-indexing all content
                          because BGE and Cohere live in different vector spaces.
  COHERE_TIMEOUT_S      — HTTP timeout in seconds (default: 15)
"""
from __future__ import annotations

import logging
import time
from typing import List, Optional

import httpx

from config import (
    _COHERE_KEY,
    COHERE_EMBED_MODEL,
    CF_GATEWAY_ENABLED,
    CF_CACHE_TTL,
    CF_AI_GATEWAY_TOKEN,
    is_cf_gateway_up,
    get_provider_base_url,
    byok_headers,
    BYOK_PLACEHOLDER,
)

logger = logging.getLogger("providers.cohere")

_API_KEY     = _COHERE_KEY
_MODEL       = COHERE_EMBED_MODEL
_EMBED_DIMS  = 1024
_TIMEOUT     = 15.0

ENABLED: bool = bool(_API_KEY)

_using_byok = CF_GATEWAY_ENABLED and _API_KEY == BYOK_PLACEHOLDER

if ENABLED:
    logger.info(
        "Cohere ready — model=%s dims=%d byok=%s",
        _MODEL, _EMBED_DIMS, _using_byok,
    )
else:
    logger.info(
        "Cohere disabled (COHERE_API_KEY not set and CF gateway BYOK not active)"
    )


def _base_url() -> str:
    url = get_provider_base_url("cohere")
    return url or "https://api.cohere.com/v1"


def _request_headers() -> dict:
    h: dict = {"Content-Type": "application/json"}
    if is_cf_gateway_up():
        bh = byok_headers(include_ttl=True, clear_upstream_auth=True)
        h.update(bh)
    else:
        h["Authorization"] = f"Bearer {_API_KEY}"
    return h


_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(_TIMEOUT),
            http2=True,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _client


async def close() -> None:
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


async def embed(
    texts: List[str],
    *,
    input_type: str = "search_document",
    model: Optional[str] = None,
) -> List[List[float]]:
    """Return a list of 1024-dim embedding vectors, one per input text.

    ``input_type`` must be one of:
      "search_document" — for content being indexed
      "search_query"    — for user questions / query strings

    Returns [] on error so callers can fall back gracefully.
    """
    if not ENABLED:
        return []
    if not texts:
        return []

    mdl = model or _MODEL
    t0 = time.perf_counter()
    try:
        client = _get_client()
        base = _base_url()
        headers = _request_headers()
        response = await client.post(
            f"{base}/embed",
            headers=headers,
            json={
                "model": mdl,
                "texts": texts,
                "input_type": input_type,
                "embedding_types": ["float"],
            },
        )
        response.raise_for_status()
        data = response.json()
        vectors = data.get("embeddings", {}).get("float", [])
        if not vectors:
            vectors = data.get("embeddings", [])
        latency = round((time.perf_counter() - t0) * 1000)
        logger.debug(
            "Cohere embed: %d texts model=%s %dms", len(texts), mdl, latency
        )
        return vectors
    except Exception as exc:
        logger.warning("Cohere embed failed (non-fatal): %s", exc)
        return []


async def embed_query(text: str, model: Optional[str] = None) -> List[float]:
    """Embed a single query string. Returns [] on error."""
    results = await embed([text], input_type="search_query", model=model)
    return results[0] if results else []


async def embed_document(text: str, model: Optional[str] = None) -> List[float]:
    """Embed a single document string for indexing. Returns [] on error."""
    results = await embed([text], input_type="search_document", model=model)
    return results[0] if results else []


async def health_check() -> dict:
    if not ENABLED:
        return {"ok": False, "reason": "COHERE_API_KEY not set"}
    t0 = time.perf_counter()
    try:
        vectors = await embed(["health check"], input_type="search_query")
        if not vectors or len(vectors[0]) != _EMBED_DIMS:
            dims = len(vectors[0]) if vectors else 0
            return {"ok": False, "reason": f"unexpected dims: {dims}"}
        return {
            "ok": True,
            "latency_ms": round((time.perf_counter() - t0) * 1000),
            "model": _MODEL,
            "dims": len(vectors[0]),
            "byok": _using_byok,
        }
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}
