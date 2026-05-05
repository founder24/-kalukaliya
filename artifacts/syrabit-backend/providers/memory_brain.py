"""
providers.memory_brain — Voyage-backed Mongo memory brain (Task #382).

A new long-term chat memory store. Items are embedded with Voyage
``voyage-3.5`` (1024-dim) and stored in a dedicated MongoDB collection
(``memory_brain`` by default) with an Atlas ``$vectorSearch`` index of
the same dimension.

This module REPLACES Voyage's previous role on the chunk embedding
path. The legacy ``providers.voyage_ai`` module remains importable and
unmodified, but the chunk embedder no longer calls it; Voyage's only
runtime customer in the new layout is ``memory_brain``.

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
        Return a status dict combining Voyage availability and the
        Mongo collection's index health.

Schema
------
Each document::

    {
        "_id":             ObjectId,
        "user_id":         str,
        "kind":            str,                # "note" | "summary" | "fact" | …
        "text":            str,                # original memory content
        "embedding":       list[float],        # voyage-3.5, 1024-dim
        "embedding_model": "voyage-3.5",
        "embedding_dim":   1024,
        "embedding_source":"voyage",
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
EMBED_MODEL_NAME = "voyage-3.5"
PROVIDER_NAME    = os.environ.get("MEMORY_BRAIN_PROVIDER", "voyage").strip().lower() or "voyage"

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
async def _embed_with_voyage(text: str, *, input_type: str) -> list[float]:
    """Single-text Voyage embedding. Empty list on failure."""
    if PROVIDER_NAME != "voyage":
        raise RuntimeError(
            f"memory_brain: unsupported MEMORY_BRAIN_PROVIDER={PROVIDER_NAME!r}"
        )
    from providers.voyage_ai import embed as _voyage_embed, ENABLED as _voy_on
    if not _voy_on:
        raise RuntimeError(
            "memory_brain: Voyage disabled — set VOYAGE_API_KEY"
        )
    vecs = await _voyage_embed([text], input_type=input_type)
    if not vecs:
        raise RuntimeError("memory_brain: Voyage returned no vectors")
    vec = vecs[0]
    if len(vec) != EMBED_DIM:
        raise RuntimeError(
            f"memory_brain: voyage dim mismatch ({len(vec)} vs {EMBED_DIM})"
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
    """Embed *text* with Voyage and insert into the memory_brain
    collection. Returns the string form of the inserted ``_id``.
    """
    if not user_id or not text or not text.strip():
        raise ValueError("memory_brain.write_memory: user_id and text required")

    vec = await _embed_with_voyage(text, input_type="document")
    doc = {
        "user_id":          user_id,
        "kind":             kind,
        "text":             text,
        "embedding":        vec,
        "embedding_model":  EMBED_MODEL_NAME,
        "embedding_dim":    EMBED_DIM,
        "embedding_source": "voyage",
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

    vec = await _embed_with_voyage(query, input_type="query")

    vs_filter: dict[str, Any] = {"user_id": user_id}
    if kind:
        vs_filter["kind"] = kind
    if metadata_filter:
        # Atlas vectorSearch filter syntax — caller supplies nested
        # metadata.<field> paths so they take effect on the indexed
        # filter fields list (configurable via MEMORY_BRAIN_FILTER_FIELDS).
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
    """Combined health: Voyage embed reachability + Mongo collection
    presence + index info. Always returns 200-ish dict; ``ok=False`` on
    any leg failure."""
    info: dict[str, Any] = {
        "configured":  PROVIDER_NAME == "voyage",
        "provider":    PROVIDER_NAME,
        "collection":  COLLECTION,
        "index":       INDEX_NAME,
        "dims":        EMBED_DIM,
        "model":       EMBED_MODEL_NAME,
    }

    # Voyage leg
    try:
        from providers.voyage_ai import health_check as _voy_health
        voy = await _voy_health()
    except Exception as exc:
        voy = {"ok": False, "reason": f"voyage_ai import/health failed: {exc}"}
    info["voyage"] = voy

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

    # Atlas vector-search index leg — the index is what makes
    # query_memory work, so a healthy collection without the index is
    # still effectively broken. We use the `listSearchIndexes`
    # aggregation stage (Atlas-only) and look for our configured index
    # name in a READY state.
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
        bool(voy.get("ok"))
        and bool(info["mongo"].get("ok"))
        and bool(vs_info.get("ok"))
    )
    return info
