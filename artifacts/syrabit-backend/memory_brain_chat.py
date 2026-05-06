"""Task #401 — Chat / lesson hooks for the memory_brain Voyage store.

Thin, best-effort adapters around ``providers.memory_brain.write_memory``
and ``providers.memory_brain.query_memory`` so that the chat hot path
and the flashcard-review hot path can opt into long-term personalised
recall WITHOUT adding upstream-dependency risk:

  * Every helper here swallows exceptions and logs at WARNING / DEBUG
    so a Voyage outage, a Mongo blip, or a missing Atlas vector index
    cannot break the user-visible response.
  * Reads have a short wall-clock timeout so the chat first-token
    latency budget is preserved even when Voyage/Mongo are slow.
  * Writes are intended to be scheduled on background tasks via
    ``asyncio.create_task(...)`` by the caller.
  * All helpers no-op silently when ``MEMORY_BRAIN_CHAT_ENABLED`` is
    set to a falsy value, so operators can hot-disable the feature
    without redeploying.

The helpers keep their domain-specific event shaping local: the chat
turn writer truncates the user / assistant texts to a reasonable cap
before embedding, and the flashcard writer composes a "Q: front /
A: back" string so a future ``query_memory("what is X?")`` retrieval
hits both sides of the card.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

import memory_brain_metrics as _mbm

logger = logging.getLogger("memory_brain_chat")


def _classify(exc: BaseException) -> str:
    """Map an exception to a short, low-cardinality reason label so
    the dashboard's "top failure reasons" doesn't explode into a
    long-tail of per-stack-trace strings.
    """
    name = type(exc).__name__
    msg = str(exc).lower()
    if "voyage" in msg:
        return "voyage_error"
    if "mongo" in msg or "motor" in msg:
        return "mongo_error"
    if "vector" in msg and "index" in msg:
        return "vector_index_missing"
    if "timeout" in msg or name.endswith("TimeoutError"):
        return "timeout"
    return name.lower()[:40] or "error"


def _enabled() -> bool:
    val = (os.environ.get("MEMORY_BRAIN_CHAT_ENABLED", "1") or "").strip().lower()
    return val not in ("0", "false", "no", "off", "")


_QUERY_TIMEOUT_S = float(
    os.environ.get("MEMORY_BRAIN_QUERY_TIMEOUT_S", "0.6") or "0.6"
)
_QUERY_TOP_K = int(
    os.environ.get("MEMORY_BRAIN_QUERY_TOP_K", "3") or "3"
)
# Atlas $vectorSearch returns a normalised similarity score in [0, 1]
# for cosine-like spaces. Below this threshold the match is too weak
# to be worth grounding the LLM on (more likely to mis-personalise the
# answer than help). Tunable per-call and per-deploy.
_QUERY_MIN_SCORE = float(
    os.environ.get("MEMORY_BRAIN_QUERY_MIN_SCORE", "0.55") or "0.55"
)


async def query_user_memories(
    user_id: Optional[str],
    query: str,
    *,
    top_k: int = _QUERY_TOP_K,
    timeout_s: float = _QUERY_TIMEOUT_S,
    min_score: float = _QUERY_MIN_SCORE,
) -> list[dict[str, Any]]:
    """Best-effort fetch of the top memory_brain matches for *user_id*.

    Returns ``[]`` when:
      * the feature is disabled (env flag),
      * ``user_id`` or ``query`` is missing,
      * the lookup raises (Voyage outage, Mongo unavailable, Atlas
        index not yet created), or
      * the lookup exceeds ``timeout_s`` seconds — the chat hot path
        cannot wait on long memory reads.

    Results below ``min_score`` are filtered out so the prompt is not
    polluted with weak / off-topic memories. ``score`` is the field
    populated by Atlas ``$vectorSearch`` (already in [0, 1] for the
    cosine-similarity space the index uses); items missing a score are
    kept conservatively (provider may not surface scores in every code
    path).

    Caller is expected to pass the resulting list into
    ``rag.build_rag_system_prompt(..., user_memories=...)``.
    """
    if not _enabled() or not user_id or not (query and query.strip()):
        return []
    try:
        from providers.memory_brain import query_memory as _qm
    except Exception as exc:
        logger.debug("memory_brain import failed: %s", exc)
        _mbm.record_event("read", kind="query", ok=False, reason="import_error")
        return []
    try:
        results = await asyncio.wait_for(
            _qm(user_id, query, top_k=top_k),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        logger.debug("memory_brain query timed out (>%ss)", timeout_s)
        _mbm.record_event("read", kind="query", ok=False, reason="timeout")
        return []
    except Exception as exc:
        logger.warning("memory_brain query failed (non-fatal): %s", exc)
        _mbm.record_event("read", kind="query", ok=False, reason=_classify(exc))
        return []

    _mbm.record_event("read", kind="query", ok=True)
    if not isinstance(results, list):
        return []
    filtered: list[dict[str, Any]] = []
    for r in results:
        if not isinstance(r, dict):
            continue
        score = r.get("score")
        if score is not None:
            try:
                if float(score) < min_score:
                    continue
            except (TypeError, ValueError):
                pass
        filtered.append(r)
    return filtered


async def _safe_write(
    user_id: str,
    text: str,
    *,
    kind: str,
    metadata: Optional[dict[str, Any]] = None,
) -> None:
    """Internal: best-effort write_memory wrapper. Swallows errors."""
    try:
        from providers.memory_brain import write_memory as _wm
    except Exception as exc:
        logger.debug("memory_brain import failed: %s", exc)
        _mbm.record_event("write", kind=kind, ok=False, reason="import_error")
        return
    try:
        await _wm(user_id, text, kind=kind, metadata=metadata or {})
    except Exception as exc:
        logger.warning(
            "memory_brain write_memory(kind=%s) failed (non-fatal): %s",
            kind, exc,
        )
        _mbm.record_event("write", kind=kind, ok=False, reason=_classify(exc))
        return
    _mbm.record_event("write", kind=kind, ok=True)


_QA_USER_CHAR_CAP = 600
_QA_ANSWER_CHAR_CAP = 1200


def _truncate(s: str, cap: int) -> str:
    if not s:
        return ""
    s = s.strip()
    return s if len(s) <= cap else s[: cap - 1] + "…"


async def write_chat_turn_memory(
    user_id: Optional[str],
    user_msg: str,
    answer: str,
    *,
    subject_id: Optional[str] = None,
    subject_name: Optional[str] = None,
    chapter_name: Optional[str] = None,
    conversation_id: Optional[str] = None,
    rag_source: Optional[str] = None,
) -> None:
    """Persist one chat turn into the memory_brain as a ``qa`` event.

    Composed as ``"Q: <truncated user message>\\nA: <truncated answer>"``
    so a later ``query_memory("...similar question...")`` matches on
    either side of the pair. No-op for anonymous users (no ``user_id``)
    and for empty messages / answers.

    Designed to be called from a background task — ``asyncio.create_task``
    in the chat persist tail. Never raises.
    """
    if not _enabled() or not user_id:
        return
    user_msg = (user_msg or "").strip()
    answer = (answer or "").strip()
    if not user_msg or not answer:
        return
    text = (
        f"Q: {_truncate(user_msg, _QA_USER_CHAR_CAP)}\n"
        f"A: {_truncate(answer, _QA_ANSWER_CHAR_CAP)}"
    )
    metadata: dict[str, Any] = {"event": "chat_turn"}
    if subject_id:
        metadata["subject_id"] = subject_id
    if subject_name:
        metadata["subject_name"] = subject_name
    if chapter_name:
        metadata["chapter_name"] = chapter_name
    if conversation_id:
        metadata["conversation_id"] = conversation_id
    if rag_source:
        metadata["rag_source"] = rag_source
    await _safe_write(user_id, text, kind="qa", metadata=metadata)


_FLASHCARD_FRONT_CAP = 400
_FLASHCARD_BACK_CAP = 800


async def write_flashcard_recall_memory(
    user_id: Optional[str],
    *,
    front: str,
    back: str,
    quality: int,
    note_id: Optional[str] = None,
    card_id: Optional[str] = None,
    interval_days: Optional[int] = None,
    repetitions: Optional[int] = None,
) -> None:
    """Persist one *successful* flashcard review as a ``fact`` memory.

    Only fires when ``quality >= 4`` (SM-2 grades 4 / 5 — "easy" or
    "perfect" recall) so the memory_brain accumulates confirmed
    learned facts, not items the student is still struggling with.
    Composed as ``"Q: <front>\\nA: <back>"`` so the same retrieval
    surface as chat-turn memories.

    No-op for anonymous device-only actors (those have a non-user
    actor identifier — caller should only pass a real user_id when
    ``actor_kind == "user"``). Never raises.
    """
    if not _enabled() or not user_id:
        return
    if quality is None or quality < 4:
        return
    front = (front or "").strip()
    back = (back or "").strip()
    if not front and not back:
        return
    text = (
        f"Q: {_truncate(front, _FLASHCARD_FRONT_CAP)}\n"
        f"A: {_truncate(back, _FLASHCARD_BACK_CAP)}"
    )
    metadata: dict[str, Any] = {
        "event": "flashcard_recall",
        "quality": int(quality),
    }
    if note_id:
        metadata["note_id"] = note_id
    if card_id:
        metadata["card_id"] = card_id
    if interval_days is not None:
        metadata["interval_days"] = int(interval_days)
    if repetitions is not None:
        metadata["repetitions"] = int(repetitions)
    await _safe_write(user_id, text, kind="fact", metadata=metadata)


__all__ = [
    "query_user_memories",
    "write_chat_turn_memory",
    "write_flashcard_recall_memory",
]
