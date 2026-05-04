"""
providers.cohere — Cohere models via AWS Bedrock + Cloudflare AI Gateway BYOK.

Direct Cohere connection (api.cohere.com) was removed on 2026-05-04 — all
Cohere model calls now route through AWS Bedrock via the CF AI Gateway
``aws-bedrock`` provider slug. Cohere model access is granted in the AWS
Bedrock Console (Model access → Cohere). AWS Activate credit ($1k) covers
billing instead of a separate Cohere account.

Path:
  {gateway_base}/aws-bedrock/bedrock-runtime/{region}/model/{model_id}/invoke

Models (1024-dim — compatible with the existing Atlas/Pinecone vector indexes):
  cohere.embed-multilingual-v3   — Cohere Embed Multilingual v3 (default for
                                    Assamese, Bengali, Hindi + English content)
  cohere.rerank-v3-5:0           — Cohere Rerank 3.5 (multilingual reranker)

input_type values (Cohere-specific, asymmetric fine-tuning):
  "search_document"  — use when indexing content
  "search_query"     — use when embedding a user question

Configuration:
  AWS_REGION              — Bedrock region with Cohere access (e.g. us-west-2)
  CF_AI_GATEWAY_*         — CF AI Gateway BYOK (Provider Keys auto-inject AWS creds)
  COHERE_EMBED_MODEL      — Bedrock model ID (default: cohere.embed-multilingual-v3)
  COHERE_RERANK_MODEL     — Bedrock model ID (default: cohere.rerank-v3-5:0)
  COHERE_TIMEOUT_S        — HTTP timeout in seconds (default: 30)

NOTE: COHERE_API_KEY is no longer used; safe to delete from secrets.
"""
from __future__ import annotations

import logging
import os as _os
import time
from typing import List, Optional

import httpx

from config import (
    CF_GATEWAY_ENABLED,
    CF_CACHE_TTL,
    CF_AI_GATEWAY_TOKEN,
    _AWS_REGION,
    byok_headers,
    cf_gateway_url,
    is_cf_gateway_up,
)

logger = logging.getLogger("providers.cohere")

_EMBED_MODEL  = _os.environ.get("COHERE_EMBED_MODEL", "cohere.embed-multilingual-v3").strip() or "cohere.embed-multilingual-v3"
_RERANK_MODEL = _os.environ.get("COHERE_RERANK_MODEL", "cohere.rerank-v3-5:0").strip() or "cohere.rerank-v3-5:0"
_EMBED_DIMS   = 1024
_TIMEOUT      = float(_os.environ.get("COHERE_TIMEOUT_S", "30"))

ENABLED: bool = CF_GATEWAY_ENABLED and bool(cf_gateway_url("bedrock"))

if ENABLED:
    logger.info(
        "Cohere-via-Bedrock ready — embed=%s rerank=%s region=%s dims=%d",
        _EMBED_MODEL, _RERANK_MODEL, _AWS_REGION, _EMBED_DIMS,
    )
else:
    logger.info(
        "Cohere-via-Bedrock disabled (CF_GATEWAY_ENABLED not set or aws-bedrock slug missing)"
    )


def _bedrock_base() -> str:
    """Return the Bedrock base URL via CF AI Gateway aws-bedrock slug."""
    if is_cf_gateway_up():
        gw = cf_gateway_url("bedrock")
        if gw:
            return gw
    return ""


def _headers() -> dict:
    """CF AI Gateway BYOK headers for the aws-bedrock slug.

    Uses the standard BYOK marker (`cf-aig-byok-key: true` + empty
    `Authorization`) so CF AI Gateway injects the AWS SigV4 credentials
    stored in its Provider Keys panel for this gateway. This is the same
    header set verified live against `cohere.embed-multilingual-v3` and
    `cohere.rerank-v3-5:0` on 2026-05-04.
    """
    h: dict = byok_headers(include_ttl=True, clear_upstream_auth=True)
    h["Content-Type"] = "application/json"
    h["Accept"] = "application/json"
    return h


def _invoke_url(model_id: str) -> str:
    base = _bedrock_base()
    if not base:
        return ""
    return f"{base}/bedrock-runtime/{_AWS_REGION}/model/{model_id}/invoke"


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
    """Return a list of 1024-dim Cohere embedding vectors via Bedrock invoke.

    ``input_type`` must be one of:
      "search_document" — for content being indexed
      "search_query"    — for user questions / query strings

    Returns [] on error so callers can fall back gracefully.
    """
    if not ENABLED:
        return []
    if not texts:
        return []

    url = _invoke_url(model or _EMBED_MODEL)
    if not url:
        return []

    body = {
        "texts": texts,
        "input_type": input_type,
        "truncate": "END",
    }
    t0 = time.perf_counter()
    try:
        client = _get_client()
        response = await client.post(url, headers=_headers(), json=body)
        response.raise_for_status()
        data = response.json()
        # Bedrock-Cohere embed response: {"embeddings": [[...], [...]], "id": ...}
        # (some variants nest under embeddings.float — handle both.)
        vectors = data.get("embeddings", [])
        if isinstance(vectors, dict):
            vectors = vectors.get("float", [])
        latency = round((time.perf_counter() - t0) * 1000)
        logger.debug(
            "Cohere/Bedrock embed: %d texts model=%s %dms",
            len(texts), model or _EMBED_MODEL, latency,
        )
        return vectors or []
    except Exception as exc:
        logger.warning("Cohere/Bedrock embed failed (non-fatal): %s", exc)
        return []


async def embed_query(text: str, model: Optional[str] = None) -> List[float]:
    """Embed a single query string. Returns [] on error."""
    results = await embed([text], input_type="search_query", model=model)
    return results[0] if results else []


async def embed_document(text: str, model: Optional[str] = None) -> List[float]:
    """Embed a single document string for indexing. Returns [] on error."""
    results = await embed([text], input_type="search_document", model=model)
    return results[0] if results else []


async def rerank(
    query: str,
    documents: List[str],
    *,
    top_n: Optional[int] = None,
    model: Optional[str] = None,
) -> List[float]:
    """Rerank ``documents`` against ``query`` via Cohere Rerank 3.5 on Bedrock.

    Returns a list of relevance scores aligned with ``documents`` order
    (NOT pre-sorted) so callers can zip-and-sort themselves. Returns [] on
    error so the dispatcher can fall through to the next provider.
    """
    if not ENABLED:
        return []
    if not documents:
        return []

    url = _invoke_url(model or _RERANK_MODEL)
    if not url:
        return []

    body: dict = {
        "query": query,
        "documents": documents,
        "api_version": 2,
    }
    if top_n is not None:
        body["top_n"] = int(top_n)
    else:
        body["top_n"] = len(documents)

    t0 = time.perf_counter()
    try:
        client = _get_client()
        response = await client.post(url, headers=_headers(), json=body)
        response.raise_for_status()
        data = response.json()
        # Bedrock-Cohere rerank response:
        # {"results": [{"index": 0, "relevance_score": 0.97}, ...]}
        results = data.get("results", [])
        # Build score array aligned with original document order.
        scores = [0.0] * len(documents)
        for r in results:
            idx = r.get("index")
            sc = r.get("relevance_score", 0.0)
            if isinstance(idx, int) and 0 <= idx < len(scores):
                scores[idx] = float(sc)
        latency = round((time.perf_counter() - t0) * 1000)
        logger.debug(
            "Cohere/Bedrock rerank: %d docs top_n=%s %dms",
            len(documents), body["top_n"], latency,
        )
        return scores
    except Exception as exc:
        logger.warning("Cohere/Bedrock rerank failed (non-fatal): %s", exc)
        return []


async def health_check() -> dict:
    if not ENABLED:
        return {"ok": False, "reason": "CF AI Gateway not enabled or aws-bedrock slug missing"}
    t0 = time.perf_counter()
    try:
        vectors = await embed(["health check"], input_type="search_query")
        if not vectors or len(vectors[0]) != _EMBED_DIMS:
            dims = len(vectors[0]) if vectors else 0
            return {"ok": False, "reason": f"unexpected dims: {dims}"}
        return {
            "ok": True,
            "latency_ms": round((time.perf_counter() - t0) * 1000),
            "model": _EMBED_MODEL,
            "rerank_model": _RERANK_MODEL,
            "dims": len(vectors[0]),
            "transport": "aws-bedrock via CF AI Gateway BYOK",
        }
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}
