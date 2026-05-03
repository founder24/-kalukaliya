"""
providers.voyage_ai — Voyage AI embeddings via Cloudflare AI Gateway (BYOK).

All requests go DIRECT to https://api.voyageai.com/v1/embeddings.

Cloudflare AI Gateway does NOT currently support Voyage AI as a proxied
provider — the gateway returns 400 ``{"code":2008,"message":"Invalid
provider"}`` for any /voyage-ai/, /voyage/, or /voyageai/ path. The slug
entry in config.py (``"voyage_ai": "voyage-ai/v1"``) is kept only for
parity with future CF support and is intentionally bypassed here. Because
of this, BYOK is unavailable for Voyage and the local ``VOYAGE_API_KEY``
secret IS required (the backend sends ``Authorization: Bearer <key>`` on
every request). If the env var is missing the provider self-disables and
the embed dispatcher falls back to Cohere on the next exclude-loop tick.

Embedding models (all 1024-dim by default — compatible with the existing
Cohere-shaped vector indexes so we can mix Voyage + Cohere outputs in one
Pinecone namespace without re-indexing):
  voyage-3.5                — best English retrieval (nDCG@10 = 0.816,
                               vs Cohere embed-multilingual-v3.0 = 0.781).
                               Default for the English / mixed-script
                               sub-pool (POOL_WEIGHTS["embed_en"]).
  voyage-3-large            — legacy default kept for back-compat.

input_type values (Voyage-specific, asymmetric fine-tuning, identical
contract to Cohere so callers can swap providers without changing args):
  "document"  — when indexing content (maps from Cohere's "search_document")
  "query"     — when embedding a user question  (maps from "search_query")

Configuration:
  VOYAGE_API_KEY        — Voyage AI API key (optional when CF BYOK is set up).
                           Legacy name VOYAGE_AI_API_KEY is also honoured.
  VOYAGE_EMBED_MODEL    — embedding model (default: voyage-3.5)
  VOYAGE_EMBED_DIMS     — output dim (default: 1024 to match Cohere index)
  VOYAGE_TIMEOUT_S      — HTTP timeout in seconds (default: 15)
"""
from __future__ import annotations

import logging
import time
from typing import List, Optional

import httpx

from config import (
    _VOYAGE_AI_KEY,
    VOYAGE_EMBED_MODEL,
    VOYAGE_EMBED_DIMS,
    BYOK_PLACEHOLDER,
)

logger = logging.getLogger("providers.voyage_ai")

_API_KEY     = _VOYAGE_AI_KEY
_MODEL       = VOYAGE_EMBED_MODEL
_EMBED_DIMS  = VOYAGE_EMBED_DIMS
_TIMEOUT     = 15.0
_DIRECT_BASE = "https://api.voyageai.com/v1"

# CF AI Gateway BYOK is not supported for Voyage (gateway returns
# {"code":2008,"message":"Invalid provider"}), so a real API key is
# mandatory — a BYOK placeholder is treated as "no key configured".
ENABLED: bool = bool(_API_KEY) and _API_KEY != BYOK_PLACEHOLDER

if ENABLED:
    logger.info(
        "Voyage AI ready — model=%s dims=%d byok=False (direct-only)",
        _MODEL, _EMBED_DIMS,
    )
elif _API_KEY == BYOK_PLACEHOLDER:
    logger.info(
        "Voyage AI disabled — VOYAGE_API_KEY missing (CF AI Gateway does "
        "not proxy Voyage, so BYOK substitution is not possible; set "
        "VOYAGE_API_KEY directly to enable the embed_en primary)."
    )
else:
    logger.info("Voyage AI disabled (VOYAGE_API_KEY not set).")


def _base_url() -> str:
    return _DIRECT_BASE


def _request_headers() -> dict:
    return {
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {_API_KEY}",
    }


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


# Map the Cohere-style input_type contract used elsewhere in the backend
# (search_document / search_query) onto Voyage's vocabulary
# (document / query) so callers can target either provider with the same
# kwargs.
_INPUT_TYPE_MAP = {
    "search_document": "document",
    "search_query":    "query",
    "document":        "document",
    "query":           "query",
}


async def embed(
    texts: List[str],
    *,
    input_type: str = "search_document",
    model: Optional[str] = None,
) -> List[List[float]]:
    """Return a list of embedding vectors (one per input text) at the
    configured ``VOYAGE_EMBED_DIMS`` (default 1024) so the result is
    drop-in-compatible with the existing Cohere-shaped Pinecone index.

    ``input_type`` accepts both Cohere-style values ("search_document" /
    "search_query") and Voyage-style values ("document" / "query") so the
    same call shape works on either provider.

    Returns [] on error so callers can fall back gracefully.
    """
    if not ENABLED:
        return []
    if not texts:
        return []

    mdl = model or _MODEL
    voyage_input_type = _INPUT_TYPE_MAP.get(input_type, "document")
    t0 = time.perf_counter()
    try:
        client = _get_client()
        base = _base_url()
        headers = _request_headers()
        response = await client.post(
            f"{base}/embeddings",
            headers=headers,
            json={
                "model":      mdl,
                "input":      texts,
                "input_type": voyage_input_type,
                # Pin output dim so indexes that expect 1024 keep working
                # even if Voyage flips its default for a future model rev.
                "output_dimension": _EMBED_DIMS,
            },
        )
        response.raise_for_status()
        data = response.json()
        # Voyage response shape: {"data": [{"embedding": [...], "index": 0}, ...]}
        items = data.get("data", []) or []
        vectors = [
            (item.get("embedding") or [])
            for item in sorted(items, key=lambda x: x.get("index", 0))
        ]
        latency = round((time.perf_counter() - t0) * 1000)
        logger.debug(
            "Voyage AI embed: %d texts model=%s %dms", len(texts), mdl, latency
        )
        return vectors
    except Exception as exc:
        logger.warning("Voyage AI embed failed (non-fatal): %s", exc)
        return []


async def embed_query(text: str, model: Optional[str] = None) -> List[float]:
    """Embed a single query string. Returns [] on error."""
    results = await embed([text], input_type="query", model=model)
    return results[0] if results else []


async def embed_document(text: str, model: Optional[str] = None) -> List[float]:
    """Embed a single document string for indexing. Returns [] on error."""
    results = await embed([text], input_type="document", model=model)
    return results[0] if results else []


async def health_check() -> dict:
    if not ENABLED:
        return {"ok": False, "reason": "VOYAGE_API_KEY not set"}
    t0 = time.perf_counter()
    try:
        vectors = await embed(["health check"], input_type="query")
        if not vectors or len(vectors[0]) != _EMBED_DIMS:
            dims = len(vectors[0]) if vectors else 0
            return {"ok": False, "reason": f"unexpected dims: {dims}"}
        return {
            "ok":          True,
            "latency_ms":  round((time.perf_counter() - t0) * 1000),
            "model":       _MODEL,
            "dims":        len(vectors[0]),
            "byok":        _using_byok,
        }
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}
