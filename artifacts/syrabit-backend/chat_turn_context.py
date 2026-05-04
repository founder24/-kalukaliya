"""Task #360 — Memory-brain enforcement guard for the chat hot path.

Implements the per-turn enforcement contract from the v3 spec
(``infra/per-cloud-feature-delegation.md`` §2.4):

  (1) load chat_history + user_profile from MongoDB
  (2) Pinecone RAG retrieve
  (3) input moderation
  (4) LLM dispatch with both contexts injected
  (5) output moderation (streaming-compatible)
  (6) write turn back to MongoDB

This module exposes ``ChatTurnContext``, a context-manager that wraps
one chat turn and asserts the dispatcher cannot be invoked without a
prior MongoDB read in the *same* turn. The guard is loud in
dev/test (raises ``RuntimeError``) and emits a metric in prod so a
silent regression never ships.

The async-only ``gpt-oss-120b`` guard lives next to the per-turn guard
because both share the same "live chat hot path" definition: any code
that runs inside ``ChatTurnContext`` is by definition the live path.
"""
from __future__ import annotations

import contextvars
import logging
import os
from contextlib import contextmanager
from typing import Iterator, Optional


logger = logging.getLogger(__name__)


# Models that are forbidden on the live chat hot path. ``gpt-oss-120b``
# is reserved for async batch entrypoints (PDF summarizer, model-paper
# generator). The list is matched by substring so both bare names
# (``gpt-oss-120b``) and CF-AI fully-qualified slugs
# (``@cf/openai/gpt-oss-120b``) are caught.
_LIVE_CHAT_FORBIDDEN_SUBSTRINGS = (
    "gpt-oss-120b",
)


# Context vars — one per turn, reset on context-manager entry/exit.
_mongo_read_done: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "syrabit_chat_turn_mongo_read_done", default=False,
)
_in_chat_turn: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "syrabit_in_chat_turn", default=False,
)
_async_batch_path: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "syrabit_async_batch_path", default=False,
)


class MemoryBrainEnforcementError(RuntimeError):
    """Raised when the chat dispatcher runs without a prior Mongo read."""


class ForbiddenLiveChatModelError(RuntimeError):
    """Raised when a forbidden model is dispatched on the live chat path.

    ``gpt-oss-120b`` is the canonical example — it must only be reached
    from async batch entrypoints (PDF summarizer, model-paper generator).
    """


def _is_dev_or_test() -> bool:
    """Loud in dev/test, soft in prod (metric-only).

    Production is identified by ``ENV=production`` or
    ``ENVIRONMENT=production``. Anything else (unset, ``dev``, ``test``,
    ``staging``, pytest) is treated as dev/test so missing reads raise.
    """
    env = (os.environ.get("ENV") or os.environ.get("ENVIRONMENT") or "").lower()
    if env in ("production", "prod"):
        return False
    return True


def mark_mongo_read() -> None:
    """Record that the per-turn MongoDB read has completed.

    Called from the FastAPI chat handler immediately after the
    ``gather(mongo_history_load, embed.then(pinecone_query))`` resolves.
    Outside a ``ChatTurnContext`` this is a no-op so non-chat code paths
    don't have to opt in.
    """
    if _in_chat_turn.get():
        _mongo_read_done.set(True)


def assert_mongo_read_or_raise(*, dispatcher_name: str = "chat_dispatcher") -> None:
    """Enforcement check — call from the dispatcher just before the LLM.

    - Inside a ``ChatTurnContext`` and Mongo read missing: raise in
      dev/test, emit a metric in prod (so a silent regression is
      always observable on the dashboard).
    - Outside a ``ChatTurnContext``: no-op. Async batch flows are not
      subject to this rule.
    """
    if not _in_chat_turn.get():
        return
    if _mongo_read_done.get():
        return
    msg = (
        f"{dispatcher_name}: chat dispatcher invoked without a prior "
        f"MongoDB read in this turn. The v3 per-turn order is "
        f"(1) load_history → (2) RAG → (3) input_moderation → "
        f"(4) dispatch — step (1) was skipped."
    )
    if _is_dev_or_test():
        raise MemoryBrainEnforcementError(msg)
    logger.error(msg)
    try:
        # Best-effort metric emission. Never block the chat turn.
        from metrics import record_credit_fallback as _emit  # type: ignore
        _emit("memory_brain_guard_skipped")  # piggy-back on existing counter
    except Exception:
        pass


def assert_live_chat_model_allowed(model_id: str) -> None:
    """Async-only guard for ``gpt-oss-120b`` and friends.

    Raises ``ForbiddenLiveChatModelError`` if a live-chat code path
    (any code running inside a ``ChatTurnContext`` and NOT inside an
    ``async_batch_scope``) tries to dispatch to a forbidden model.
    Async batch flows opt-in via ``async_batch_scope()``.
    """
    if not model_id:
        return
    lowered = model_id.lower()
    forbidden = next(
        (s for s in _LIVE_CHAT_FORBIDDEN_SUBSTRINGS if s in lowered),
        None,
    )
    if forbidden is None:
        return
    if _async_batch_path.get():
        return
    # Inside a chat turn OR no-context-explicit-batch: forbid.
    raise ForbiddenLiveChatModelError(
        f"Live-chat dispatch to {model_id!r} is forbidden — model "
        f"{forbidden!r} is reserved for async batch entrypoints "
        f"(PDF summarizer, model-paper generator). Wrap the call in "
        f"`async_batch_scope()` if this is a legitimate batch flow."
    )


@contextmanager
def chat_turn(*, session_id: str = "", user_id: str = "") -> Iterator["ChatTurn"]:
    """Open a chat turn — resets per-turn state, enforces invariants on exit.

    Usage::

        with chat_turn(session_id=sid, user_id=uid) as turn:
            history, profile = await load_history_and_profile(...)
            turn.mark_mongo_read()
            ...
            await dispatcher(messages, ...)
    """
    in_tok = _in_chat_turn.set(True)
    read_tok = _mongo_read_done.set(False)
    try:
        yield ChatTurn(session_id=session_id, user_id=user_id)
    finally:
        _in_chat_turn.reset(in_tok)
        _mongo_read_done.reset(read_tok)


@contextmanager
def async_batch_scope() -> Iterator[None]:
    """Mark the current scope as an async-batch flow.

    Code inside this scope may dispatch to ``gpt-oss-120b`` and similar
    batch-only models. The chat-turn guard does not apply.
    """
    tok = _async_batch_path.set(True)
    try:
        yield
    finally:
        _async_batch_path.reset(tok)


class ChatTurn:
    """Lightweight per-turn handle returned by :func:`chat_turn`."""

    __slots__ = ("session_id", "user_id")

    def __init__(self, session_id: str = "", user_id: str = "") -> None:
        self.session_id = session_id
        self.user_id = user_id

    def mark_mongo_read(self) -> None:
        mark_mongo_read()

    def assert_ready_for_dispatch(self, *, dispatcher_name: str = "chat_dispatcher") -> None:
        assert_mongo_read_or_raise(dispatcher_name=dispatcher_name)


__all__ = [
    "ChatTurn",
    "ChatTurnContext",
    "ForbiddenLiveChatModelError",
    "MemoryBrainEnforcementError",
    "assert_live_chat_model_allowed",
    "assert_mongo_read_or_raise",
    "async_batch_scope",
    "chat_turn",
    "mark_mongo_read",
]


# Backwards-friendly alias — the spec text says "ChatTurnContext".
ChatTurnContext = chat_turn
