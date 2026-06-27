"""
MemoryService — writes Q&A exchanges from chat sessions into the memory_brain
collection so the MyMemoriesPage can surface them to the student.

Write rules (all must be true):
  1. User is authenticated (user_id is not an anonymous/IP identifier).
  2. The exchange is educational (confidence_tier is not "generic" or "error",
     OR the request had an explicit chapter_id, meaning the student was reading
     a chapter and asked a contextual question).
  3. The assistant response is substantive (>= 40 chars after stripping).
  4. The same question hasn't been stored for this user in the last 24 hours
     (dedup by question prefix to avoid saving retries as separate entries).

Each memory document shape:
  {
    user_id:          str,
    kind:             "qa",
    text:             str,           # user's question — displayed in UI
    answer:           str,           # assistant response — stored for context
    subject_name:     str | None,    # derived from context_chunks titles
    chapter_name:     str | None,    # from request.chapter_name or chunks
    session_id:       str | None,
    lang:             "en" | "as",
    confidence_tier:  str,           # high / mid / low / none / generic
    event:            "chat_qa",
    created_at:       datetime (UTC),
  }
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# IDs that look like anonymous identifiers — never write memories for these.
_ANON_PREFIXES = ("ip_", "anon_", "guest_")

# Minimum response length to be considered a real answer.
_MIN_RESPONSE_CHARS = 40

# Don't save memories for these tiers (no educational content retrieved).
_SKIP_TIERS = {"generic", "error"}


def _is_anon(user_id: str) -> bool:
    return any(user_id.startswith(p) for p in _ANON_PREFIXES)


def _extract_chapter_subject(
    context_chunks: list[dict],
    chapter_name: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    """Return (subject_name, chapter_name) from available sources."""
    # Prefer explicit request values
    chapter = chapter_name or None
    subject = None

    if context_chunks:
        # Chunks have {"title": "...", "score": ..., "doc_id": "..."}
        # Title is usually "Chapter Name — Topic" or just "Chapter Name"
        first_title = context_chunks[0].get("title", "")
        if first_title:
            if "—" in first_title:
                parts = first_title.split("—", 1)
                subject = parts[0].strip() or None
                chapter = chapter or parts[1].strip() or None
            else:
                chapter = chapter or first_title.strip() or None

    return subject, chapter


async def write_qa_memory(
    *,
    user_id: str,
    user_message: str,
    assistant_response: str,
    detected_lang: str,
    confidence_tier: str,
    context_chunks: list[dict],
    session_id: Optional[str],
    chapter_name: Optional[str] = None,
    chapter_id: Optional[str] = None,
) -> None:
    """
    Fire-and-forget: persist a Q&A memory for authenticated users when the
    exchange was educational. Safe to call via asyncio.create_task().
    """
    try:
        # ── Guard 1: only authenticated users ─────────────────────────────
        if _is_anon(user_id):
            return

        # ── Guard 2: educational exchange ─────────────────────────────────
        is_educational = (
            confidence_tier not in _SKIP_TIERS
            or bool(chapter_id)  # user was reading a chapter card
        )
        if not is_educational:
            return

        # ── Guard 3: substantive response ─────────────────────────────────
        response_stripped = assistant_response.strip()
        if len(response_stripped) < _MIN_RESPONSE_CHARS:
            return

        # ── Guard 4: 24-hour dedup by question prefix ──────────────────────
        from app.db.mongo import get_mongo_client
        from app.config import settings

        db = get_mongo_client()[settings.MONGODB_DB_NAME]
        question_prefix = user_message.strip()[:80]
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

        existing = await db.memory_brain.find_one({
            "user_id": user_id,
            "text": {"$regex": f"^{_re_escape(question_prefix[:40])}", "$options": "i"},
            "created_at": {"$gte": cutoff},
        })
        if existing:
            logger.debug(
                "memory_dedup_skip",
                extra={"user_id": user_id, "prefix": question_prefix[:30]},
            )
            return

        # ── Extract subject / chapter ──────────────────────────────────────
        subject_name, resolved_chapter = _extract_chapter_subject(
            context_chunks, chapter_name
        )

        # ── Write memory ───────────────────────────────────────────────────
        doc = {
            "user_id": user_id,
            "kind": "qa",
            "text": user_message.strip()[:500],
            "answer": response_stripped[:2000],
            "subject_name": subject_name,
            "chapter_name": resolved_chapter,
            "session_id": session_id,
            "lang": detected_lang,
            "confidence_tier": confidence_tier,
            "event": "chat_qa",
            "created_at": datetime.now(timezone.utc),
        }
        await db.memory_brain.insert_one(doc)
        logger.info(
            "memory_written",
            extra={
                "user_id": user_id,
                "kind": "qa",
                "lang": detected_lang,
                "chapter": resolved_chapter,
                "confidence_tier": confidence_tier,
            },
        )

    except Exception as e:
        # Never crash the caller — memory writing is best-effort.
        logger.warning(f"write_qa_memory failed (non-fatal): {e}")


def _re_escape(text: str) -> str:
    """Minimal regex escaping for the dedup query prefix."""
    import re
    return re.escape(text)


async def ensure_memory_indexes() -> None:
    """
    Create indexes on memory_brain once at startup.
    - (user_id, created_at DESC) for paginated listing
    - TTL index: memories expire after 180 days automatically
    - created_at for TTL sweep
    """
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings
        from pymongo import ASCENDING, DESCENDING

        db = get_mongo_client()[settings.MONGODB_DB_NAME]

        await db.memory_brain.create_index(
            [("user_id", ASCENDING), ("created_at", DESCENDING)],
            name="memory_brain_user_created",
        )
        await db.memory_brain.create_index(
            [("created_at", ASCENDING)],
            expireAfterSeconds=180 * 24 * 3600,  # 180 days TTL
            name="memory_brain_ttl",
        )
        logger.info("memory_brain indexes ensured")
    except Exception as e:
        logger.warning(f"memory_brain index creation failed (non-fatal): {e}")
