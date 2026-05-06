"""
retrievers.factory — choose the active retriever at call time.

Resolution order (highest priority first):

  1. Runtime override stored in `db.settings` document
     `{"id": "retriever_config", "active": "<name>"}`. Set via the
     admin endpoint (`/admin/retriever/config`) and refreshed every
     `_DB_OVERRIDE_TTL_SEC`.
  2. The `RAG_RETRIEVER` environment variable.
  3. `DEFAULT_RETRIEVER` (== `"vectorize"`).

Concrete retriever instances are memoised per-name so the Vertex
client's token cache survives across calls. `invalidate_retriever_cache()`
drops the cache (used by tests + the admin update endpoint after a
successful switch).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

from .base import Retriever
from .vectorize import VectorizeRetriever
# Task #490 — VertexVectorSearchRetriever deleted along with retrievers/vertex.py.
from .mongodb_vector import MongoVectorRetriever
from .pinecone_vector import PineconeVectorRetriever

logger = logging.getLogger("retrievers.factory")

DEFAULT_RETRIEVER = "vectorize"
_KNOWN: dict[str, type[Retriever]] = {
    "vectorize": VectorizeRetriever,
    # MongoDB Atlas Vector Search — available on Flex/M10+ tiers.
    # Enable via admin endpoint: POST /admin/retriever/config {"active": "mongodb_vector"}
    # Requires Atlas VS index + embedding field in the configured collection.
    "mongodb_vector": MongoVectorRetriever,
    # Pinecone serverless — ANN index for AHSEC/SEBA chunks (sub-50 ms p99).
    # Enable via admin endpoint: POST /admin/retriever/config {"active": "pinecone_vector"}
    # Requires PINECONE_KEY + PINECONE_INDEX env vars; run migrate_chunks_to_pinecone.py first.
    "pinecone_vector": PineconeVectorRetriever,
}

_instances: dict[str, Retriever] = {}
_db_override: Optional[str] = None
_db_override_fetched_at: float = 0.0
_DB_OVERRIDE_TTL_SEC = 30.0


def list_available_retrievers() -> list[str]:
    return sorted(_KNOWN.keys())


def get_retriever_by_name(name: str) -> Retriever:
    """Return a memoised retriever by exact name. Raises `ValueError`
    if the name is not registered. Exposed so the benchmark + ingestion
    scripts can pin a specific backend regardless of the active toggle.

    Task #383 — when ``VECTORIZE_SHADOW_ON`` is true and the resolved
    primary backend is not vectorize, the returned instance is wrapped
    in a ``ShadowRetriever`` so writes/queries are mirrored to
    Cloudflare Vectorize for parity verification (results from the
    primary remain authoritative).
    """
    key = (name or "").strip().lower()
    if key not in _KNOWN:
        raise ValueError(
            f"Unknown retriever {name!r}; known={list_available_retrievers()}"
        )
    inst = _instances.get(key)
    if inst is None:
        inst = _KNOWN[key]()
        try:
            from vectorize_shadow import maybe_wrap_with_shadow
            inst = maybe_wrap_with_shadow(inst)
        except Exception as exc:
            logger.debug("vectorize shadow wrap skipped: %s", exc)
        _instances[key] = inst
    return inst


async def _read_db_override() -> Optional[str]:
    """Best-effort read of the admin-set override. Never raises — a
    DB hiccup falls back to env / default."""
    global _db_override, _db_override_fetched_at
    now = time.monotonic()
    if now - _db_override_fetched_at < _DB_OVERRIDE_TTL_SEC and _db_override is not None:
        return _db_override or None
    try:
        from deps import db  # local import: avoid circular at module load
        if db is None:
            _db_override_fetched_at = now
            _db_override = ""
            return None
        doc = await db.settings.find_one({"id": "retriever_config"}, {"active": 1, "_id": 0})
        active = ((doc or {}).get("active") or "").strip().lower()
        _db_override = active
        _db_override_fetched_at = now
        return active or None
    except Exception as exc:
        logger.debug("retriever override db read skipped: %s", exc)
        # Don't poison the cache — a transient DB error must let the
        # next call retry instead of locking us into a stale value.
        _db_override = None
        _db_override_fetched_at = 0.0
        return None


def get_active_retriever_name() -> str:
    """Synchronous resolution that skips the DB override (for code
    paths that already cached the answer or run outside an event loop,
    e.g. ingestion scripts)."""
    env = (os.environ.get("RAG_RETRIEVER", "") or "").strip().lower()
    if env in _KNOWN:
        return env
    return DEFAULT_RETRIEVER


async def get_retriever() -> Retriever:
    """Async resolver — checks the DB override then falls back to env."""
    override = await _read_db_override()
    if override and override in _KNOWN:
        return get_retriever_by_name(override)
    return get_retriever_by_name(get_active_retriever_name())


async def set_active_retriever(name: str) -> str:
    """Persist a runtime switch. Validates the name, writes to
    `db.settings`, and invalidates the override cache so the next
    `get_retriever()` call sees it immediately."""
    key = (name or "").strip().lower()
    if key not in _KNOWN:
        raise ValueError(
            f"Unknown retriever {name!r}; known={list_available_retrievers()}"
        )
    from deps import db
    if db is None:
        raise RuntimeError("retriever override requires MongoDB")
    await db.settings.update_one(
        {"id": "retriever_config"},
        {"$set": {"id": "retriever_config", "active": key}},
        upsert=True,
    )
    invalidate_retriever_cache()
    logger.info("retriever runtime switch → %s", key)
    return key


def invalidate_retriever_cache() -> None:
    """Drop memoised instances + the DB override cache. Used by tests
    and after the admin toggle changes the active backend."""
    global _db_override, _db_override_fetched_at
    _instances.clear()
    _db_override = None
    _db_override_fetched_at = 0.0
