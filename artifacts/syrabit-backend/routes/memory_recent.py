"""Task #415 — "Pick up where you left off" widget endpoint.

Surfaces the student's most recent ``memory_brain`` entries (chat-turn
``qa`` memories + high-quality ``fact`` flashcard recalls written by
``memory_brain_chat`` since Task #401) so the dashboard can render
3-5 personalised "continue your X" cards *outside* a chat turn.

Why a dedicated endpoint instead of reusing
``providers.memory_brain.query_memory()``?

  ``query_memory`` is a vector search and needs a query string — it is
  the right surface when a chat turn is in flight. The dashboard widget
  has no query: it just wants the most recent N memories for the
  signed-in user. So this route reads the same Mongo collection
  (``memory_brain``) directly, sorted by ``created_at`` desc, and
  re-uses ``providers.memory_brain.COLLECTION`` so the source of truth
  stays in one module.

Anonymous (non-logged-in) users get an empty list with ``anon: true``
so the frontend can render a graceful empty state without a 401.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends

from auth_deps import get_current_user_optional
from providers.memory_brain import COLLECTION as _MB_COLLECTION

logger = logging.getLogger("routes.memory_recent")

router = APIRouter()

_MAX_LIMIT = 10
_DEFAULT_LIMIT = 5
_PREVIEW_CHAR_CAP = 220


def _split_qa(text: str) -> tuple[str, str]:
    """memory_brain_chat composes ``"Q: ...\\nA: ..."``. Pull both
    halves back out for the card title / body. Falls back to the raw
    text when the format isn't recognised so we never show an empty
    card."""
    if not text:
        return "", ""
    raw = text.strip()
    q, a = "", ""
    for line in raw.splitlines():
        ls = line.lstrip()
        if not q and ls.startswith("Q:"):
            q = ls[2:].strip()
        elif not a and ls.startswith("A:"):
            a = ls[2:].strip()
    if not q and not a:
        return raw, ""
    return q, a


def _truncate(s: str, cap: int = _PREVIEW_CHAR_CAP) -> str:
    if not s:
        return ""
    s = s.strip()
    return s if len(s) <= cap else s[: cap - 1] + "…"


def _shape(doc: dict[str, Any]) -> dict[str, Any]:
    md = doc.get("metadata") or {}
    q, a = _split_qa(doc.get("text") or "")
    created = doc.get("created_at")
    return {
        "id":              str(doc.get("_id", "")),
        "kind":            doc.get("kind") or "note",
        "event":           md.get("event"),
        "title":           _truncate(q or doc.get("text") or "", 140),
        "preview":         _truncate(a or "", _PREVIEW_CHAR_CAP),
        "subject_id":      md.get("subject_id"),
        "subject_name":    md.get("subject_name"),
        "chapter_name":    md.get("chapter_name"),
        "conversation_id": md.get("conversation_id"),
        "quality":         md.get("quality"),
        "created_at":      created.isoformat() if hasattr(created, "isoformat") else created,
    }


@router.get("/edu/memory/recent")
async def memory_recent(
    limit: int = _DEFAULT_LIMIT,
    user=Depends(get_current_user_optional),
) -> dict[str, Any]:
    """Return the signed-in student's most recent memory_brain entries
    for the dashboard "Pick up where you left off" widget.

    Anonymous callers get ``{"items": [], "anon": true}`` so the
    frontend can render an empty / signup-nudge state without a 401.
    """
    try:
        n = max(1, min(int(limit or _DEFAULT_LIMIT), _MAX_LIMIT))
    except (TypeError, ValueError):
        n = _DEFAULT_LIMIT

    if not user or not user.get("id"):
        return {"items": [], "anon": True, "limit": n}

    user_id = user["id"]

    try:
        from deps import db
    except Exception as exc:
        logger.debug("memory_recent: deps import failed: %s", exc)
        return {"items": [], "anon": False, "limit": n, "ok": False}
    if db is None:
        return {"items": [], "anon": False, "limit": n, "ok": False}

    col = db[_MB_COLLECTION]
    items: list[dict[str, Any]] = []
    try:
        cursor = (
            col.find(
                {"user_id": user_id},
                # Embedding vectors are 1024 floats — never ship them
                # to the dashboard, they'd dwarf the payload.
                {"embedding": 0},
            )
            .sort("created_at", -1)
            .limit(n)
        )
        async for doc in cursor:
            items.append(_shape(doc))
    except Exception as exc:
        logger.warning("memory_recent: Mongo read failed (non-fatal): %s", exc)
        return {"items": [], "anon": False, "limit": n, "ok": False}

    return {"items": items, "anon": False, "limit": n, "ok": True}
