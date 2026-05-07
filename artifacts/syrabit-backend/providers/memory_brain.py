"""
providers.memory_brain — workers-AI custom embedded Mongo memory brain.

Long-term chat memory store. Items are embedded with the custom
Cloudflare Workers-AI worker (Gemma-300M + Qwen3-0.6B mean-pool, 1024-dim
— see ``providers.workers_embed``) and stored in a dedicated MongoDB
collection (``memory_brain`` by default) with an Atlas ``$vectorSearch``
index of the same dimension.

Public API
----------
    write_memory(user_id, text, *, kind="note", metadata=None)
        Embed and upsert a memory document. Returns the inserted ``_id``.

    query_memory(user_id, query, *, top_k=5, kind=None,
                 metadata_filter=None)
        Vector-search the user's memories via Atlas ``$vectorSearch`` and
        return the top matches.

    ensure_index()
        Create the Atlas Vector Search index on the memory_brain
        collection if it does not already exist. Idempotent.

    health_check()
        Return a status dict combining workers-AI availability and the
        Mongo collection's index health.

Schema
------
Each document::

    {
        "_id":             ObjectId,
        "user_id":         str,
        "kind":            str,                # "note" | "summary" | "fact" | …
        "text":            str,                # original memory content
        "embedding":       list[float],        # workers_ai_custom, 1024-dim
        "embedding_model": "workers_ai_custom@gemma+qwen3-meanpool-1024",
        "embedding_dim":   1024,
        "embedding_source":"workers_ai_custom",
        "metadata":        dict,               # arbitrary caller fields
        "created_at":      datetime (UTC),
    }
"""
from __future__ import annotations

import datetime as _dt
import logging
import os
from typing import Any, Optional

logger = logging.getLogger("providers.memory_brain")

# ── Config ────────────────────────────────────────────────────────────────────
COLLECTION       = os.environ.get("MEMORY_BRAIN_COLLECTION", "memory_brain").strip() or "memory_brain"
INDEX_NAME       = os.environ.get("MEMORY_BRAIN_INDEX_NAME", "memory_brain_vector_index").strip() or "memory_brain_vector_index"
EMBED_DIM        = int(os.environ.get("MEMORY_BRAIN_DIMS", "1024") or "1024")
EMBED_METRIC     = os.environ.get("MEMORY_BRAIN_METRIC", "cosine").strip() or "cosine"
EMBED_MODEL_NAME = "workers_ai_custom@gemma+qwen3-meanpool-1024"
PROVIDER_NAME    = os.environ.get("MEMORY_BRAIN_PROVIDER", "workers_ai_custom").strip().lower() or "workers_ai_custom"

_FILTER_PATHS = [
    f.strip() for f in
    os.environ.get(
        "MEMORY_BRAIN_FILTER_FIELDS",
        "user_id,kind",
    ).split(",")
    if f.strip()
]


# ── Mongo collection accessor ─────────────────────────────────────────────────
def _collection():
    """Return the Motor collection. Raises ``RuntimeError`` if unavailable."""
    from deps import db  # imported lazily so test stubs win
    if db is None:
        raise RuntimeError(
            "memory_brain: MongoDB unavailable (deps.db is None)"
        )
    return db[COLLECTION]


# ── Embedding ─────────────────────────────────────────────────────────────────
async def _embed_one(text: str, *, input_type: str) -> list[float]:
    """Single-text workers-AI custom embedding. Raises on failure."""
    if PROVIDER_NAME != "workers_ai_custom":
        raise RuntimeError(
            f"memory_brain: unsupported MEMORY_BRAIN_PROVIDER={PROVIDER_NAME!r}"
        )
    from providers import workers_embed as _we
    if not _we.is_enabled():
        raise RuntimeError(
            "memory_brain: workers_ai_custom disabled — set "
            "WORKERS_EMBED_URL and WORKERS_EMBED_SECRET"
        )
    vecs = await _we.embed([text], input_type=input_type)
    if not vecs or vecs[0] is None:
        raise RuntimeError("memory_brain: workers_ai_custom returned no vector")
    vec = vecs[0]
    if len(vec) != EMBED_DIM:
        raise RuntimeError(
            f"memory_brain: workers_ai_custom dim mismatch "
            f"({len(vec)} vs {EMBED_DIM})"
        )
    return vec


# ── Public API ────────────────────────────────────────────────────────────────
async def write_memory(
    user_id: str,
    text: str,
    *,
    kind: str = "note",
    metadata: Optional[dict[str, Any]] = None,
) -> str:
    """Embed *text* and insert into the memory_brain collection.
    Returns the string form of the inserted ``_id``.
    """
    if not user_id or not text or not text.strip():
        raise ValueError("memory_brain.write_memory: user_id and text required")

    vec = await _embed_one(text, input_type="search_document")
    doc = {
        "user_id":          user_id,
        "kind":             kind,
        "text":             text,
        "embedding":        vec,
        "embedding_model":  EMBED_MODEL_NAME,
        "embedding_dim":    EMBED_DIM,
        "embedding_source": "workers_ai_custom",
        "metadata":         dict(metadata or {}),
        "created_at":       _dt.datetime.now(_dt.timezone.utc),
    }
    col = _collection()
    result = await col.insert_one(doc)
    return str(getattr(result, "inserted_id", ""))


async def query_memory(
    user_id: str,
    query: str,
    *,
    top_k: int = 5,
    kind: Optional[str] = None,
    metadata_filter: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Vector-search the user's memory brain. Returns a ranked list of
    matches with the original text and score (highest first).
    """
    if not user_id or not query:
        return []

    vec = await _embed_one(query, input_type="search_query")

    vs_filter: dict[str, Any] = {"user_id": user_id}
    if kind:
        vs_filter["kind"] = kind
    if metadata_filter:
        vs_filter.update(metadata_filter)

    pipeline = [
        {
            "$vectorSearch": {
                "index": INDEX_NAME,
                "path": "embedding",
                "queryVector": vec,
                "numCandidates": max(top_k * 15, top_k),
                "limit": top_k,
                "filter": vs_filter,
            }
        },
        {"$addFields": {"score": {"$meta": "vectorSearchScore"}}},
        {"$project": {"embedding": 0}},
    ]

    col = _collection()
    try:
        cursor = col.aggregate(pipeline)
        docs = await cursor.to_list(length=top_k)
    except Exception as exc:
        logger.error("[memory_brain] query failed: %s", exc)
        return []

    matches: list[dict[str, Any]] = []
    for d in docs:
        matches.append({
            "id":         str(d.get("_id", "")),
            "user_id":    d.get("user_id"),
            "kind":       d.get("kind"),
            "text":       d.get("text"),
            "metadata":   d.get("metadata") or {},
            "created_at": d.get("created_at"),
            "score":      float(d.get("score") or 0.0),
        })
    return matches


# ── Index management ──────────────────────────────────────────────────────────
async def ensure_index() -> dict[str, Any]:
    """Create the Atlas Vector Search index on the memory_brain
    collection if it does not already exist. Returns a status dict.
    """
    try:
        from deps import db
        if db is None:
            return {"ok": False, "reason": "MongoDB unavailable"}

        definition = {
            "fields": [
                {
                    "type":          "vector",
                    "numDimensions": EMBED_DIM,
                    "path":          "embedding",
                    "similarity":    EMBED_METRIC,
                },
                *[{"type": "filter", "path": p} for p in _FILTER_PATHS],
            ]
        }
        await db.command(
            "createSearchIndexes",
            COLLECTION,
            indexes=[{
                "name":       INDEX_NAME,
                "type":       "vectorSearch",
                "definition": definition,
            }],
        )
        logger.info(
            "memory_brain: created Atlas VS index '%s' on collection '%s'",
            INDEX_NAME, COLLECTION,
        )
        return {
            "ok":         True,
            "created":    True,
            "index":      INDEX_NAME,
            "collection": COLLECTION,
        }
    except Exception as exc:
        msg = str(exc)
        if "already exists" in msg.lower() or "IndexAlreadyExists" in msg:
            return {
                "ok":         True,
                "created":    False,
                "index":      INDEX_NAME,
                "collection": COLLECTION,
                "note":       "already exists",
            }
        logger.warning("memory_brain.ensure_index failed (non-fatal): %s", exc)
        return {"ok": False, "reason": msg}


# ── Health check ──────────────────────────────────────────────────────────────
async def health_check() -> dict[str, Any]:
    """Combined health: workers-AI custom embed reachability + Mongo
    collection presence + index info. Always returns 200-ish dict;
    ``ok=False`` on any leg failure."""
    info: dict[str, Any] = {
        "configured":  PROVIDER_NAME == "workers_ai_custom",
        "provider":    PROVIDER_NAME,
        "collection":  COLLECTION,
        "index":       INDEX_NAME,
        "dims":        EMBED_DIM,
        "model":       EMBED_MODEL_NAME,
    }

    # Embed leg
    try:
        from providers import workers_embed as _we
        embed_ok = bool(_we.is_enabled())
        embed_info: dict[str, Any] = {"ok": embed_ok}
        if not embed_ok:
            embed_info["reason"] = (
                "workers_ai_custom disabled — set WORKERS_EMBED_URL/SECRET"
            )
    except Exception as exc:
        embed_info = {"ok": False, "reason": f"workers_embed import failed: {exc}"}
    info["embed"] = embed_info

    # Mongo leg
    try:
        col = _collection()
        count = await col.estimated_document_count()
        info["mongo"] = {
            "ok": True,
            "estimated_document_count": int(count or 0),
        }
    except Exception as exc:
        info["mongo"] = {"ok": False, "reason": str(exc)[:200]}

    # Atlas vector-search index leg.
    vs_info: dict[str, Any] = {
        "ok":     False,
        "exists": False,
        "name":   INDEX_NAME,
    }
    try:
        col = _collection()
        cursor = col.aggregate([{"$listSearchIndexes": {}}])
        idx_docs = await cursor.to_list(length=50)
        match = next(
            (d for d in idx_docs if d.get("name") == INDEX_NAME),
            None,
        )
        if match is not None:
            vs_info["exists"] = True
            status = (match.get("status") or match.get("state") or "").upper()
            queryable = bool(match.get("queryable", status == "READY"))
            vs_info["status"] = status or "UNKNOWN"
            vs_info["queryable"] = queryable
            vs_info["ok"] = queryable
        else:
            vs_info["reason"] = (
                f"vector index {INDEX_NAME!r} not found on collection "
                f"{COLLECTION!r}; run providers.memory_brain.ensure_index()"
            )
    except Exception as exc:
        vs_info["reason"] = (
            f"$listSearchIndexes failed (Atlas only?): {str(exc)[:200]}"
        )
    info["vector_index"] = vs_info

    info["ok"] = (
        bool(embed_info.get("ok"))
        and bool(info["mongo"].get("ok"))
        and bool(vs_info.get("ok"))
    )
    return info
