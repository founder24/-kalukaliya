"""Task #416 — Let students browse and delete their saved memories.

The backend ``memory_brain`` collection (Task #401) stores per-user Q&A
turns and confirmed flashcard facts keyed by ``user_id``. This route
exposes two minimal, auth-required endpoints so a student can:

  * ``GET    /api/user/memories``           — paginated list of *their*
    saved memories (newest first), with embedding vectors stripped.
  * ``DELETE /api/user/memories/{memory_id}`` — delete a single memory,
    but only when the document's ``user_id`` matches the caller.

Both routes hard-scope every Mongo query to ``user_id`` so a logged-in
student can never browse or delete another user's memories. The
companion "recent activity" widget endpoint
(``routes.memory_recent``) intentionally lives on a separate path and
is read-only / optional-auth; this module is the privacy-control
surface and therefore strictly authenticated.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, Depends, HTTPException

from auth_deps import get_current_user
from providers.memory_brain import COLLECTION as _MB_COLLECTION

logger = logging.getLogger("routes.memory_browse")

router = APIRouter()

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 100
_PREVIEW_CHAR_CAP = 600


def _truncate(s: str, cap: int = _PREVIEW_CHAR_CAP) -> str:
    if not s:
        return ""
    s = s.strip()
    return s if len(s) <= cap else s[: cap - 1] + "…"


def _shape(doc: dict[str, Any]) -> dict[str, Any]:
    md = doc.get("metadata") or {}
    created = doc.get("created_at")
    return {
        "id":           str(doc.get("_id", "")),
        "kind":         doc.get("kind") or "note",
        "text":         _truncate(doc.get("text") or ""),
        "subject_id":   md.get("subject_id"),
        "subject_name": md.get("subject_name"),
        "chapter_name": md.get("chapter_name"),
        "event":        md.get("event"),
        "quality":      md.get("quality"),
        "created_at":   created.isoformat() if hasattr(created, "isoformat") else created,
    }


@router.get("/user/memories")
async def list_my_memories(
    limit: int = _DEFAULT_LIMIT,
    offset: int = 0,
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Return the signed-in student's saved memory_brain entries,
    newest first, with simple offset pagination.
    """
    try:
        n = max(1, min(int(limit or _DEFAULT_LIMIT), _MAX_LIMIT))
    except (TypeError, ValueError):
        n = _DEFAULT_LIMIT
    try:
        off = max(0, int(offset or 0))
    except (TypeError, ValueError):
        off = 0

    user_id = user.get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        from deps import db
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("memory_browse: deps import failed: %s", exc)
        raise HTTPException(status_code=503, detail="memory store unavailable")
    if db is None:
        raise HTTPException(status_code=503, detail="memory store unavailable")

    col = db[_MB_COLLECTION]
    items: list[dict[str, Any]] = []
    total: Optional[int] = None
    try:
        total = await col.count_documents({"user_id": user_id})
        cursor = (
            col.find(
                {"user_id": user_id},
                # Embedding vectors are 1024 floats — never ship them
                # to the browser, they'd dwarf the payload.
                {"embedding": 0},
            )
            .sort("created_at", -1)
            .skip(off)
            .limit(n)
        )
        async for doc in cursor:
            items.append(_shape(doc))
    except Exception as exc:
        logger.warning("memory_browse: Mongo read failed: %s", exc)
        raise HTTPException(status_code=503, detail="memory store unavailable")

    return {
        "items":    items,
        "limit":    n,
        "offset":   off,
        "total":    int(total or 0),
        "has_more": (off + len(items)) < int(total or 0),
    }


@router.delete("/user/memories/{memory_id}")
async def delete_my_memory(
    memory_id: str,
    user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Delete a single memory the caller owns. Returns 404 when the id
    does not exist *or* belongs to a different user — we deliberately
    do not distinguish so a student cannot probe other users' ids.
    """
    user_id = user.get("id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        oid = ObjectId(memory_id)
    except (InvalidId, TypeError):
        raise HTTPException(status_code=404, detail="Memory not found")

    try:
        from deps import db
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("memory_browse: deps import failed: %s", exc)
        raise HTTPException(status_code=503, detail="memory store unavailable")
    if db is None:
        raise HTTPException(status_code=503, detail="memory store unavailable")

    col = db[_MB_COLLECTION]
    try:
        # Hard-scope on user_id so a leaked / guessed ObjectId from
        # another user cannot be deleted by the caller.
        result = await col.delete_one({"_id": oid, "user_id": user_id})
    except Exception as exc:
        logger.warning("memory_browse: Mongo delete failed: %s", exc)
        raise HTTPException(status_code=503, detail="memory store unavailable")

    if not getattr(result, "deleted_count", 0):
        raise HTTPException(status_code=404, detail="Memory not found")

    return {"ok": True, "id": memory_id}
