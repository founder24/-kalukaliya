"""
providers.pinecone_ai — Pinecone Inference API (embeddings + reranking).

Uses Pinecone's hosted Inference API directly via REST — no SDK needed.
Supports multilingual models that handle Assamese/Bengali script natively.

API endpoints:
  POST https://api.pinecone.io/embed
  POST https://api.pinecone.io/rerank

Auth: Api-Key header (not Bearer).

Embedding models:
  multilingual-e5-large  — 768-dim, multilingual (Assamese, Bengali, Hindi …)
  llama-text-embed-v2    — 1024-dim, English-optimised, best accuracy

Reranking models:
  bge-reranker-v2-m3     — multilingual reranker (handles Assamese queries)
  pinecone-rerank-v0     — English, fast

Configuration (env vars):
  PINECONE_API_KEY       — required
  PINECONE_EMBED_MODEL   — default: multilingual-e5-large
  PINECONE_RERANK_MODEL  — default: bge-reranker-v2-m3
  PINECONE_TIMEOUT_S     — HTTP timeout seconds (default: 12)
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Callable, List, Optional, TypeVar

import httpx

logger = logging.getLogger("providers.pinecone_ai")

# ── Config ────────────────────────────────────────────────────────────────────
_API_KEY      = os.environ.get("PINECONE_API_KEY", "").strip()
_EMBED_MODEL  = os.environ.get("PINECONE_EMBED_MODEL",  "multilingual-e5-large").strip() or "multilingual-e5-large"
_RERANK_MODEL = os.environ.get("PINECONE_RERANK_MODEL", "bge-reranker-v2-m3").strip()   or "bge-reranker-v2-m3"
_TIMEOUT      = float(os.environ.get("PINECONE_TIMEOUT_S", "12") or "12")
_BASE_URL     = "https://api.pinecone.io"

ENABLED = bool(_API_KEY)

# ── HTTP client ───────────────────────────────────────────────────────────────
_http_client: Optional[httpx.AsyncClient] = None
_http_lock = asyncio.Lock()


async def _get_client() -> httpx.AsyncClient:
    global _http_client
    async with _http_lock:
        if _http_client is None or _http_client.is_closed:
            _http_client = httpx.AsyncClient(
                base_url=_BASE_URL,
                headers={"Api-Key": _API_KEY, "Content-Type": "application/json", "X-Pinecone-API-Version": "2024-10"},
                timeout=httpx.Timeout(connect=6, read=_TIMEOUT, write=10, pool=10),
                limits=httpx.Limits(max_connections=30, max_keepalive_connections=10),
            )
    return _http_client


# ── Embeddings ────────────────────────────────────────────────────────────────
async def embed(
    texts: List[str],
    *,
    input_type: str = "query",
    model: Optional[str] = None,
) -> List[List[float]]:
    """Embed a list of texts. Returns list of float vectors.

    Args:
        texts:      List of strings to embed (max ~96 per batch).
        input_type: "query" for search queries, "passage" for documents.
        model:      Override PINECONE_EMBED_MODEL env var.
    """
    if not ENABLED:
        raise RuntimeError("Pinecone AI not configured — set PINECONE_API_KEY")
    if not texts:
        return []

    mdl = model or _EMBED_MODEL
    payload = {
        "model": mdl,
        "inputs": [{"text": t[:8192]} for t in texts],
        "parameters": {"input_type": input_type, "truncate": "END"},
    }
    t0 = time.perf_counter()
    client = await _get_client()
    resp = await client.post("/embed", json=payload)
    resp.raise_for_status()
    data = resp.json()
    vectors = [item["values"] for item in (data.get("data") or [])]
    logger.debug(
        "[pinecone] embed model=%s n=%d dim=%d dur=%.0fms",
        mdl, len(texts), len(vectors[0]) if vectors else 0,
        (time.perf_counter() - t0) * 1000,
    )
    return vectors


async def embed_one(text: str, *, input_type: str = "query") -> Optional[List[float]]:
    """Convenience wrapper — embed a single string."""
    vecs = await embed([text], input_type=input_type)
    return vecs[0] if vecs else None


async def embed_passages(texts: List[str]) -> List[List[float]]:
    """Embed document passages (uses 'passage' input_type for better recall)."""
    return await embed(texts, input_type="passage")


# ── Reranking ─────────────────────────────────────────────────────────────────
async def rerank(
    query: str,
    documents: List[str],
    *,
    top_n: Optional[int] = None,
    model: Optional[str] = None,
) -> List[float]:
    """Score documents against a query using Pinecone's reranker.

    Returns a list of floats (scores), one per input document, in the
    original document order (not ranked order). Caller is responsible for
    sorting if needed.

    Args:
        query:     Search query string.
        documents: List of document strings to score.
        top_n:     How many top results to return (default: all).
        model:     Override PINECONE_RERANK_MODEL.
    """
    if not ENABLED:
        raise RuntimeError("Pinecone AI not configured — set PINECONE_API_KEY")
    if not documents:
        return []

    mdl = model or _RERANK_MODEL
    n = top_n if top_n is not None else len(documents)
    payload = {
        "model": mdl,
        "query": query[:4096],
        "documents": [{"text": d[:4096]} for d in documents],
        "top_n": min(n, len(documents)),
        "return_documents": False,
    }
    t0 = time.perf_counter()
    client = await _get_client()
    resp = await client.post("/rerank", json=payload)
    resp.raise_for_status()
    data = resp.json()

    # Build a scores list in original document order
    scores_map: dict[int, float] = {}
    for item in (data.get("data") or []):
        scores_map[item["index"]] = float(item.get("score", 0.0))

    result = [scores_map.get(i, 0.0) for i in range(len(documents))]
    logger.debug(
        "[pinecone] rerank model=%s n_docs=%d top_n=%d dur=%.0fms",
        mdl, len(documents), n, (time.perf_counter() - t0) * 1000,
    )
    return result


T = TypeVar("T")


async def rerank_items(
    query: str,
    items: List[T],
    text_fn: Callable[[T], str],
    *,
    top_k: int = 3,
    model: Optional[str] = None,
    min_score: Optional[float] = None,
) -> List[T]:
    """Rerank a list of arbitrary items. Compatible with the old voyage interface.

    Args:
        query:     Search query.
        items:     List of items to rank.
        text_fn:   Function that extracts the text to score from each item.
        top_k:     Return this many top items (sorted by relevance).
        model:     Override reranking model.
        min_score: If set, drop items whose reranker score is below this
                   threshold. At least one item is always returned so the
                   caller never gets an empty list when all candidates are weak.
                   bge-reranker-v2-m3 scores are typically in the -10..+10
                   range; -1.0 drops only actively irrelevant chunks.
    """
    if not items:
        return items
    docs = [text_fn(item) for item in items]
    scores = await rerank(query, docs, top_n=top_k, model=model)
    ranked = sorted(zip(scores, items), key=lambda x: x[0], reverse=True)
    if min_score is not None:
        filtered = [(s, item) for s, item in ranked if s >= min_score]
        if filtered:
            return [item for _, item in filtered[:top_k]]
    return [item for _, item in ranked[:top_k]]


# ── Health check ──────────────────────────────────────────────────────────────
async def health_check() -> dict:
    """Quick probe — embed a single short string."""
    if not ENABLED:
        return {"ok": False, "error": "not_configured"}
    t0 = time.perf_counter()
    try:
        vecs = await embed(["health check"], input_type="query")
        return {
            "ok": bool(vecs),
            "model": _EMBED_MODEL,
            "dims": len(vecs[0]) if vecs else 0,
            "latency_ms": round((time.perf_counter() - t0) * 1000),
        }
    except Exception as exc:
        return {
            "ok": False,
            "latency_ms": round((time.perf_counter() - t0) * 1000),
            "error": str(exc)[:200],
        }
