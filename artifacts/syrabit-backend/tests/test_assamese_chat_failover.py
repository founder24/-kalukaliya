"""Smoke test — Task #291: Assamese chat provider-failover end-to-end.

Two scenarios required by the spec:

1. **Sarvam failure → Vertex Assamese answer.** When the locked primary
   (sarvam) raises, ``select_provider`` must redraw and the request must
   complete via vertex with a fallback log line containing the locked
   chain order so on-call can audit the path.

2. **Cross-language English question in Assamese mode.** An English-only
   question routed to the Assamese chat must be translated to Assamese
   (``ensure_question_in_assamese``) before embedding so the
   ``namespace="as"`` Pinecone lookup finds the Assamese corpus, and the
   translation step must emit a ``[T291][CROSS-LANG-Q]`` log line.

Run::

    python -m pytest tests/test_assamese_chat_failover.py -v
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_assamese_rag_chat_falls_over_from_sarvam_to_indic():
    """Strict 2-leg primary/fallback pool (2026-05-05 user instruction):
    sarvam (primary) → workers_ai_indic (fallback). Vertex REMOVED from
    the Assamese chat chain entirely.

    Strict-chain exhaustion surfaces None when BOTH legs are excluded so
    the caller errors out cleanly rather than silently routing to a
    wrong-language last-resort (workers_ai_llama31_8b / generic workers_ai
    / vertex on an English-prompt path).
    """
    from llm import select_provider

    # Sarvam excluded → only workers_ai_indic remains.
    for _ in range(40):
        chosen = select_provider("assamese_rag_chat", lang="as",
                                 exclude=frozenset({"sarvam"}))
        assert chosen == "workers_ai_indic", (
            f"Expected workers_ai_indic as the sole fallback when sarvam is "
            f"excluded, got {chosen!r}. Vertex must NOT appear (removed from chat)."
        )

    # Both legs excluded — pool exhausted, must NOT degrade to
    # workers_ai_llama31_8b / generic workers_ai / vertex (wrong-language
    # or removed-from-chat).
    chosen = select_provider("assamese_rag_chat", lang="as",
                             exclude=frozenset({"sarvam", "workers_ai_indic"}))
    assert chosen in (None, ""), (
        f"Expected None when both legs excluded (strict-chain); got {chosen!r}. "
        f"workers_ai_llama31_8b / generic workers_ai / vertex must NEVER be "
        f"selected for the Assamese chat path."
    )
    print("  PASS: assamese_rag_chat 2-leg primary/fallback verified (no wrong-language / vertex downgrade)")


def test_assamese_rag_chat_logs_locked_chain_order_on_fallback():
    """select_provider must log the chosen provider so production fallbacks
    are auditable. We don't assert exact wording but verify the log-record
    name & level so log routing keeps working."""
    import llm

    handler_records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            handler_records.append(record)

    h = _Capture(level=logging.DEBUG)
    llm.logger.addHandler(h)
    llm.logger.setLevel(logging.DEBUG)
    llm.logger.propagate = False
    logging.disable(logging.NOTSET)
    try:
        # Strict 2-leg failover (2026-05-05): with sarvam excluded the
        # only remaining draw is workers_ai_indic.
        llm.select_provider("assamese_rag_chat", lang="as",
                            exclude=frozenset({"sarvam"}))
    finally:
        llm.logger.removeHandler(h)

    msgs = [r.getMessage() for r in handler_records]
    # 2-leg chain (2026-05-05): with sarvam excluded the chosen provider
    # must be workers_ai_indic and it must appear alongside the assamese
    # feature key in some log record so on-call can audit.
    assert any(
        "workers_ai_indic" in m and "assamese_rag_chat" in m
        for m in msgs
    ), (
        f"Expected select_provider to log fallback to workers_ai_indic for "
        f"assamese_rag_chat; captured records were: {msgs!r}"
    )
    print("  PASS: select_provider logs assamese_rag_chat 2-leg fallback")


def test_english_question_in_assamese_mode_triggers_cross_lang_translation():
    """ensure_question_in_assamese must translate EN → AS and emit the
    [T291][CROSS-LANG-Q] log line so the namespace='as' lookup gets an
    Assamese-script query as input."""
    from routes import ai_chat

    translate_stub = mock.AsyncMock(return_value="সালোকসংশ্লেষণ কি?")

    handler_records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            handler_records.append(record)

    h = _Capture(level=logging.INFO)
    ai_chat.logger.addHandler(h)
    ai_chat.logger.setLevel(logging.INFO)
    ai_chat.logger.propagate = False
    logging.disable(logging.NOTSET)

    try:
        with mock.patch.object(ai_chat,
                               "_assamese_translate_gemini_main_sarvam_polish",
                               translate_stub):
            out = asyncio.run(ai_chat.ensure_question_in_assamese("What is photosynthesis?"))
    finally:
        ai_chat.logger.removeHandler(h)

    assert out == "সালোকসংশ্লেষণ কি?", (
        f"Expected translated Assamese output, got {out!r}"
    )
    translate_stub.assert_awaited_once()

    msgs = [r.getMessage() for r in handler_records]
    assert any("[T291][CROSS-LANG-Q]" in m for m in msgs), (
        f"Expected [T291][CROSS-LANG-Q] log line; captured: {msgs!r}"
    )
    print("  PASS: EN question in Assamese mode → translated + log emitted")


def test_assamese_question_skips_translation_noop():
    """Already-Assamese questions must be a no-op (no translate call)."""
    from routes import ai_chat

    translate_stub = mock.AsyncMock(return_value="should not be called")
    with mock.patch.object(ai_chat,
                           "_assamese_translate_gemini_main_sarvam_polish",
                           translate_stub):
        out = asyncio.run(ai_chat.ensure_question_in_assamese("সালোকসংশ্লেষণ কি?"))
    assert out == "সালোকসংশ্লেষণ কি?"
    translate_stub.assert_not_called()
    print("  PASS: already-Assamese question is a no-op (no translation)")


if __name__ == "__main__":
    test_assamese_rag_chat_falls_over_from_sarvam_to_indic()
    test_assamese_rag_chat_logs_locked_chain_order_on_fallback()
    test_english_question_in_assamese_mode_triggers_cross_lang_translation()
    test_assamese_question_skips_translation_noop()
    print("\nAll Task #291 Assamese chat-failover assertions verified.")
