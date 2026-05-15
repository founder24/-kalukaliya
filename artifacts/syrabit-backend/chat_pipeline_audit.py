"""
chat_pipeline_audit.py — lightweight per-turn pipeline tracing for the
Syrabit chat endpoint.

Audit records are accumulated in-memory during a chat turn and flushed as a
single structured JSON log line at stream completion.  The logger name is
``chat.pipeline`` so CloudWatch / Sentry can filter and alarm on it
independently from the noisy per-path DEBUG logs that already exist.

Stages emitted in the live deployment
--------------------------------------
entry            — auth mode, message shape, language, subject presence
intent_guardrail — intent classification result + safety-gate decision
phase0           — Phase-0 parallel prefetch outcomes (history, subject, doc)
route            — router decision (rag / web / direct / cache) + score
retrieval        — RAG source, chunk count, web-search flag
llm_start        — cache hit, history turns, prompt size
[flush]          — total_ms, outcome, rag_source, approximate token count

Usage::

    from chat_pipeline_audit import ChatPipelineAudit

    audit = ChatPipelineAudit(
        user_id=user_id,
        is_anon=is_anon,
        conversation_id=conv_id,
    )

    audit.record("entry", auth_mode="jwt", msg_chars=len(msg))
    audit.record("route", decision="rag", intent="notes")
    ...
    audit.flush(outcome="ok", total_tokens=312)
"""

from __future__ import annotations

import logging
import time
from typing import Any

_logger = logging.getLogger("chat.pipeline")


class ChatPipelineAudit:
    """Collects labelled stage events for a single chat turn.

    All public methods are intentionally exception-safe so a bug here
    never breaks the request path (V4 §12 — fail only on the audit side,
    not on the product side).
    """

    __slots__ = ("_t0", "_user_id", "_is_anon", "_conv_id", "_stages")

    def __init__(
        self,
        *,
        user_id: str | None = None,
        is_anon: bool = True,
        conversation_id: str | None = None,
    ) -> None:
        self._t0: float = time.monotonic()
        self._user_id: str | None = user_id
        self._is_anon: bool = is_anon
        self._conv_id: str | None = conversation_id
        self._stages: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    def record(self, stage: str, **kwargs: Any) -> None:
        """Append an audit event for *stage* with an elapsed_ms timestamp."""
        try:
            elapsed_ms = round((time.monotonic() - self._t0) * 1000, 1)
            self._stages.append({"stage": stage, "elapsed_ms": elapsed_ms, **kwargs})
        except Exception:
            pass

    # ------------------------------------------------------------------
    def flush(self, outcome: str = "ok", **kwargs: Any) -> None:
        """Emit the full turn trace as a single structured log line.

        The ``extra`` dict is forwarded to the Python logging system so
        CloudWatch Insights / Sentry structured-log parsers can index the
        individual fields without needing to parse the message string.
        """
        try:
            total_ms = round((time.monotonic() - self._t0) * 1000, 1)
            _logger.info(
                "PIPELINE user=%s anon=%s conv=%s outcome=%s total_ms=%s stages=%d",
                self._user_id or "",
                self._is_anon,
                self._conv_id or "",
                outcome,
                total_ms,
                len(self._stages),
                extra={
                    "pipeline_audit": True,
                    "user_id":   self._user_id or "",
                    "is_anon":   self._is_anon,
                    "conv_id":   self._conv_id or "",
                    "outcome":   outcome,
                    "total_ms":  total_ms,
                    "stages":    self._stages,
                    **kwargs,
                },
            )
        except Exception:
            pass
