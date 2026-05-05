"""Quiz pre-generation provider-order tests (2026-05-05).

Covers the new ``prefer_vertex`` flag plumbed through
``_generate_and_clean_quiz`` and ``pregenerate_chapter_quiz``:

* ``prefer_vertex=True`` (the formatting-stage owner instruction):
  Vertex / Gemini is PRIMARY, Azure GPT-4.1-mini is FALLBACK.
* ``prefer_vertex=False`` (the historical lazy on-click path used
  by ``quiz_generate``): Azure is PRIMARY, Vertex is FALLBACK.

Also verifies the admin_pipeline polish flow imports
``pregenerate_chapter_quiz`` so the source-level wiring contract
holds (the actual `asyncio.create_task` callsite is exercised by
the broader pipeline tests, but the import line is what guarantees
the polish stage can schedule the pool generation).
"""
from __future__ import annotations

import pathlib
import sys
import json
from unittest.mock import patch, AsyncMock

import pytest

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


_FAKE_QUIZ_PAYLOAD = json.dumps({
    "questions": [
        {
            "q": f"Sample question {i}?",
            "choices": ["A", "B", "C", "D"],
            "answer": 0,
            "explanation": "Because A is correct.",
        }
        for i in range(3)
    ]
})


@pytest.mark.asyncio
async def test_prefer_vertex_true_calls_vertex_first():
    """prefer_vertex=True must call Vertex Gemini FIRST and never call
    Azure when Vertex succeeds. This matches the 2026-05-05 instruction
    that Vertex owns the formatting + quiz stage."""
    from routes import edu_study

    az_mock = AsyncMock(return_value=_FAKE_QUIZ_PAYLOAD)
    vx_mock = AsyncMock(return_value=_FAKE_QUIZ_PAYLOAD)

    with patch.object(edu_study, "_az_quiz_chat", az_mock), \
         patch.object(edu_study, "_call_vertex_chat", vx_mock):
        out = await edu_study._generate_and_clean_quiz(
            context="A long enough source text " * 30,
            topic="Test topic",
            chapter_ref="seba/class-10/sci/test",
            subject_name="Science",
            count=3,
            response_lang="en",
            prefer_vertex=True,
        )

    assert isinstance(out, list) and len(out) == 3
    assert vx_mock.await_count == 1, "Vertex must be called when prefer_vertex=True"
    assert az_mock.await_count == 0, (
        "Azure must NOT be called when prefer_vertex=True and Vertex succeeds"
    )
    # Sanity: confirm Vertex was called with the Gemini model id.
    args, _ = vx_mock.call_args
    assert args[1] == "gemini-2.5-flash"


@pytest.mark.asyncio
async def test_prefer_vertex_true_falls_back_to_azure_on_vertex_failure():
    """When prefer_vertex=True and Vertex fails, Azure must be tried as
    the fallback so the polish flow's quiz pre-gen never collapses on a
    Gemini outage."""
    from routes import edu_study

    az_mock = AsyncMock(return_value=_FAKE_QUIZ_PAYLOAD)
    vx_mock = AsyncMock(side_effect=RuntimeError("simulated vertex outage"))

    with patch.object(edu_study, "_az_quiz_chat", az_mock), \
         patch.object(edu_study, "_call_vertex_chat", vx_mock):
        out = await edu_study._generate_and_clean_quiz(
            context="A long enough source text " * 30,
            topic="Test topic",
            chapter_ref="seba/class-10/sci/test",
            subject_name="Science",
            count=3,
            response_lang="en",
            prefer_vertex=True,
        )

    assert len(out) == 3
    assert vx_mock.await_count == 1, "Vertex must be tried first"
    assert az_mock.await_count == 1, "Azure must be tried as the fallback"


@pytest.mark.asyncio
async def test_prefer_vertex_false_keeps_azure_primary():
    """Default (prefer_vertex=False) must preserve the historical
    Azure-PRIMARY → Vertex-FALLBACK order used by lazy on-click
    quiz_generate. Regressing this would silently swap the provider
    used by every student-facing quiz click."""
    from routes import edu_study

    az_mock = AsyncMock(return_value=_FAKE_QUIZ_PAYLOAD)
    vx_mock = AsyncMock(return_value=_FAKE_QUIZ_PAYLOAD)

    with patch.object(edu_study, "_az_quiz_chat", az_mock), \
         patch.object(edu_study, "_call_vertex_chat", vx_mock):
        out = await edu_study._generate_and_clean_quiz(
            context="A long enough source text " * 30,
            topic="Test topic",
            chapter_ref="seba/class-10/sci/test",
            subject_name="Science",
            count=3,
            response_lang="en",
            # prefer_vertex omitted — defaults to False
        )

    assert len(out) == 3
    assert az_mock.await_count == 1
    assert vx_mock.await_count == 0


def test_pregenerate_chapter_quiz_accepts_prefer_vertex():
    """Source-level contract — the public hook MUST accept the new
    prefer_vertex kwarg so admin_pipeline.py can schedule polish-stage
    pool generation pinned to Vertex."""
    import inspect
    from routes.edu_study import pregenerate_chapter_quiz

    sig = inspect.signature(pregenerate_chapter_quiz)
    assert "prefer_vertex" in sig.parameters, (
        "pregenerate_chapter_quiz must accept prefer_vertex kwarg — "
        "admin_pipeline._generate_chapter_all relies on it to pin Vertex "
        "for polish-stage quiz pre-generation"
    )
    p = sig.parameters["prefer_vertex"]
    assert p.kind == inspect.Parameter.KEYWORD_ONLY, (
        "prefer_vertex must be keyword-only to match the rest of the signature"
    )
    assert p.default is False, (
        "prefer_vertex default must remain False so existing callers "
        "(admin_create_chapter, admin_content) keep their Azure-primary order"
    )


def test_admin_pipeline_imports_pregenerate_for_polish_stage():
    """Source-level contract — admin_pipeline.py's polish branch must
    reference pregenerate_chapter_quiz with prefer_vertex=True so the
    Vertex-pinned quiz pool is materialised right after polish writes
    the chapter notes."""
    src = (_BACKEND / "routes" / "admin_pipeline.py").read_text(encoding="utf-8")
    assert "from routes.edu_study import pregenerate_chapter_quiz" in src, (
        "admin_pipeline.py must import pregenerate_chapter_quiz inside "
        "the polish-stage wrapper"
    )
    assert "prefer_vertex=True" in src, (
        "admin_pipeline.py must invoke pregenerate_chapter_quiz with "
        "prefer_vertex=True so Vertex Gemini owns the polish-stage "
        "quiz pool generation"
    )


@pytest.mark.asyncio
async def test_polish_stage_quiz_pregen_uses_bounded_semaphore():
    """Behavioural contract — `_polish_stage_quiz_pregen` must acquire
    the module-level `_quiz_pregen_sem` semaphore so the bulk
    `_generate_chapter_all` wave cannot schedule an unbounded number
    of concurrent Vertex quiz jobs. Asserts the wrapper:
      * acquires the semaphore (visible as a non-default _value)
      * delegates to pregenerate_chapter_quiz with prefer_vertex=True
      * never raises even if the inner call raises
    """
    from routes import admin_pipeline

    captured: dict = {}

    async def _fake_pregen(chapter_doc, *, prefer_vertex: bool = False, **kw):
        captured["chapter_id"] = chapter_doc.get("id")
        captured["prefer_vertex"] = prefer_vertex
        # Snapshot the semaphore depth WHILE inside the wrapper so we
        # can assert acquire+release happened around our call.
        captured["sem_depth_during_call"] = (
            admin_pipeline._QUIZ_PREGEN_CONCURRENCY
            - admin_pipeline._quiz_pregen_sem._value
        )
        return True

    import sys
    fake_edu = type(sys)("routes.edu_study")
    fake_edu.pregenerate_chapter_quiz = _fake_pregen
    sys.modules["routes.edu_study"] = fake_edu  # so the late import inside
                                                # the wrapper resolves to ours
    try:
        await admin_pipeline._polish_stage_quiz_pregen(
            {"id": "ch_1", "title": "Test Chapter"}
        )
    finally:
        # Clean up so other tests get the real module back on next import.
        del sys.modules["routes.edu_study"]

    assert captured["chapter_id"] == "ch_1"
    assert captured["prefer_vertex"] is True, (
        "_polish_stage_quiz_pregen must pin prefer_vertex=True"
    )
    assert captured["sem_depth_during_call"] >= 1, (
        "_polish_stage_quiz_pregen must acquire _quiz_pregen_sem before "
        "calling pregenerate_chapter_quiz so bulk waves are bounded"
    )
    # After return, the semaphore must be fully released.
    assert (
        admin_pipeline._quiz_pregen_sem._value
        == admin_pipeline._QUIZ_PREGEN_CONCURRENCY
    ), "Semaphore must be released after the wrapper returns"


@pytest.mark.asyncio
async def test_polish_stage_quiz_pregen_swallows_exceptions():
    """Behavioural contract — the polish-stage wrapper must NEVER
    propagate exceptions from `pregenerate_chapter_quiz`. The bulk
    chapter generate flow schedules these as detached tasks; an
    unhandled exception would surface as a noisy 'Task exception was
    never retrieved' warning AND leak the semaphore slot."""
    from routes import admin_pipeline

    async def _broken_pregen(chapter_doc, *, prefer_vertex: bool = False, **kw):
        raise RuntimeError("simulated vertex outage")

    import sys
    fake_edu = type(sys)("routes.edu_study")
    fake_edu.pregenerate_chapter_quiz = _broken_pregen
    sys.modules["routes.edu_study"] = fake_edu
    try:
        # Must not raise.
        await admin_pipeline._polish_stage_quiz_pregen(
            {"id": "ch_2", "title": "Broken Chapter"}
        )
    finally:
        del sys.modules["routes.edu_study"]

    # Semaphore must be released even on exception.
    assert (
        admin_pipeline._quiz_pregen_sem._value
        == admin_pipeline._QUIZ_PREGEN_CONCURRENCY
    ), "Semaphore must be released even when the inner call raises"
