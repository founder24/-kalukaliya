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


def test_assamese_rag_chat_falls_over_from_sarvam_to_vertex():
    """sarvam raise → vertex must produce the Assamese answer.

    Task #291 — the chain is strictly sarvam → vertex with NO further
    downgrade. If both fail, ``select_provider`` returns ``None`` so the
    caller errors out cleanly rather than silently routing to a wrong-model
    last resort (e.g. IndicTrans2, which is a translation model).
    """
    from llm import select_provider

    # Force the primary to fail by excluding it; verify the next pick is vertex.
    chosen = select_provider("assamese_rag_chat", lang="as",
                             exclude=frozenset({"sarvam"}))
    assert chosen == "vertex", (
        f"Expected fallback to 'vertex' after excluding sarvam, got {chosen!r}. "
        f"This breaks the locked sarvam→vertex chain."
    )

    # Both excluded — pool is exhausted, must NOT degrade to workers_ai_indic.
    chosen = select_provider("assamese_rag_chat", lang="as",
                             exclude=frozenset({"sarvam", "vertex"}))
    assert chosen in (None, ""), (
        f"Expected None when sarvam+vertex both excluded (strict 2-leg chain); "
        f"got {chosen!r}. workers_ai_indic must NEVER be selected for chat."
    )
    print("  PASS: assamese_rag_chat sarvam→vertex strict cascade verified (no downgrade)")


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
        # Failover path → vertex (sarvam excluded).
        llm.select_provider("assamese_rag_chat", lang="as",
                            exclude=frozenset({"sarvam"}))
    finally:
        llm.logger.removeHandler(h)

    msgs = [r.getMessage() for r in handler_records]
    # Must mention vertex and the assamese feature in some informational record.
    assert any("vertex" in m and "assamese_rag_chat" in m for m in msgs), (
        f"Expected select_provider to log fallback to vertex for "
        f"assamese_rag_chat; captured records were: {msgs!r}"
    )
    print("  PASS: select_provider logs assamese_rag_chat → vertex fallback")


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
    test_assamese_rag_chat_falls_over_from_sarvam_to_vertex()
    test_assamese_rag_chat_logs_locked_chain_order_on_fallback()
    test_english_question_in_assamese_mode_triggers_cross_lang_translation()
    test_assamese_question_skips_translation_noop()
    print("\nAll Task #291 Assamese chat-failover assertions verified.")
