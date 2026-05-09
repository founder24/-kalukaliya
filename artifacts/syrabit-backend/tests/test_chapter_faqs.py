"""Task #12 — golden test for the AEO Answer-Card + FAQ materializer.

Covers the contract in ``.local/tasks/task-12.md``:

  * ``aca_jobs.materialize_chapter_faqs`` produces 5–10 deterministic
    Q→A pairs per chapter and a 40–60-word Quick-Answer.
  * The deterministic templates ``faq.md`` + ``quick_answer.md`` are
    wired into ``content_formatter._MATERIALIZATION_QUERY_TYPES`` so
    ``format_content(query_type='faq', ...)`` short-circuits the LLM
    dispatch and emits ``formatted_by="deterministic_template"``.
  * The materialised FAQ entries round-trip through the
    ``routes.seo_pages._build_jsonld`` ``FAQPage`` block — i.e. the
    SEO renderer's existing JSON-LD shape happily consumes the
    materialised payload.

Runs without network: the helpers under test are pure-functions of
their inputs (no Mongo / Pinecone / Vertex calls).
"""
from __future__ import annotations

import asyncio
import json
import sys

from tests._deps_stub import install_deps_stub  # noqa: E402

install_deps_stub()


from aca_jobs.materialize_chapter_faqs import (  # noqa: E402
    FAQ_MAX_PER_CHAPTER,
    FAQ_MIN_PER_CHAPTER,
    QUICK_ANSWER_MAX_WORDS,
    QUICK_ANSWER_MIN_WORDS,
    build_faq_pairs,
    build_quick_answer,
)
from content_formatter import (  # noqa: E402
    _MATERIALIZATION_QUERY_TYPES,
    format_content,
)
from routes.seo_pages import _build_jsonld  # noqa: E402


_SUBTOPICS = [
    {"title": "Light reactions",
     "summary": "Light reactions occur in the thylakoid membranes and "
                "convert solar energy into ATP and NADPH."},
    {"title": "Calvin cycle",
     "summary": "The Calvin cycle uses ATP and NADPH to fix CO2 into "
                "glucose in the stroma of the chloroplast."},
    {"title": "Chlorophyll", "summary": ""},
]
_PYQ_STEMS = [
    {"question_stem": "Explain the role of NADPH in the light reactions "
                       "of photosynthesis.", "year": 2024},
    {"question_stem": "Describe the Calvin cycle with a labelled diagram.",
     "year": 2023},
]


def test_query_types_extended_for_task_12() -> None:
    assert "faq" in _MATERIALIZATION_QUERY_TYPES
    assert "quick_answer" in _MATERIALIZATION_QUERY_TYPES


def test_quick_answer_word_count_in_band() -> None:
    body = build_quick_answer(
        chapter_title="Photosynthesis",
        subject_name="Biology",
        board_name="AHSEC",
        class_name="Class 11",
        subtopics=_SUBTOPICS,
        chapter_description="",
    )
    n = len(body.split())
    assert QUICK_ANSWER_MIN_WORDS <= n <= QUICK_ANSWER_MAX_WORDS, (
        f"quick-answer word-count {n} outside "
        f"[{QUICK_ANSWER_MIN_WORDS}, {QUICK_ANSWER_MAX_WORDS}]: {body!r}"
    )


def test_faq_pairs_in_band_and_unique() -> None:
    pairs = build_faq_pairs(
        chapter_title="Photosynthesis",
        subject_name="Biology",
        board_name="AHSEC",
        class_name="Class 11",
        subtopics=_SUBTOPICS,
        pyq_stems=_PYQ_STEMS,
        chapter_description="Photosynthesis is the process by which "
                            "green plants convert light energy into "
                            "chemical energy stored in glucose.",
    )
    assert FAQ_MIN_PER_CHAPTER <= len(pairs) <= FAQ_MAX_PER_CHAPTER
    questions = [p["question"].lower() for p in pairs]
    assert len(questions) == len(set(questions)), "duplicate question stems"
    for p in pairs:
        assert p["question"], "empty question"
        assert p["answer"], "empty answer"
        assert p["source"] in ("syllabus", "pyq", "evergreen")


def test_faq_pairs_pad_to_minimum_when_corpus_thin() -> None:
    pairs = build_faq_pairs(
        chapter_title="Photosynthesis",
        subject_name="Biology",
        board_name="AHSEC",
        class_name="Class 11",
        subtopics=[],
        pyq_stems=[],
        chapter_description="",
    )
    assert len(pairs) >= FAQ_MIN_PER_CHAPTER, (
        "evergreen pad failed to lift sparse-corpus chapter to the "
        f"5-pair floor (got {len(pairs)})"
    )


async def test_format_content_renders_faq_via_template() -> None:
    out = await format_content(
        "What is photosynthesis?",
        query_type="faq",
        template_data={
            "question": "What is photosynthesis?",
            "answer":   "Photosynthesis is the process by which "
                        "green plants convert light energy into "
                        "chemical energy stored in glucose.",
        },
    )
    assert out["formatted_by"] == "deterministic_template"
    assert "What is photosynthesis?" in out["text"]
    assert "Photosynthesis is the process" in out["text"]


async def test_format_content_renders_quick_answer_via_template() -> None:
    body = "Photosynthesis is the chapter that explains how plants "\
           "convert light energy into chemical energy stored in glucose."
    out = await format_content(
        body, query_type="quick_answer",
        template_data={"answer": body},
    )
    assert out["formatted_by"] == "deterministic_template"
    assert body in out["text"]


def test_jsonld_emits_materialised_faq_entries() -> None:
    pairs = build_faq_pairs(
        chapter_title="Photosynthesis",
        subject_name="Biology",
        board_name="AHSEC",
        class_name="Class 11",
        subtopics=_SUBTOPICS,
        pyq_stems=_PYQ_STEMS,
        chapter_description="Photosynthesis is the process by which "
                            "green plants make glucose.",
    )
    jsonld_str = _build_jsonld(
        page_url="https://syrabit.ai/board/ahsec/class/class-11/"
                 "subject/biology/chapter/photosynthesis/notes",
        page_type="notes",
        chapter_title="Photosynthesis",
        subject_name="Biology",
        board_name="AHSEC",
        class_name="Class 11",
        description="Photosynthesis chapter — AHSEC Class 11 Biology notes.",
        subtopics=_SUBTOPICS,
        faq_entries=pairs,
    )
    parsed = json.loads(
        jsonld_str.replace("<\\/", "</")
                  .replace("\\u2028", "\u2028")
                  .replace("\\u2029", "\u2029")
    )
    faq_blocks = [n for n in parsed["@graph"] if n.get("@type") == "FAQPage"]
    assert len(faq_blocks) == 1, "exactly one FAQPage block expected"
    main_entity = faq_blocks[0]["mainEntity"]
    assert len(main_entity) == len(pairs), (
        "renderer dropped or duplicated FAQ entries"
    )
    expected_qs = {p["question"] for p in pairs}
    rendered_qs = {q["name"] for q in main_entity}
    assert expected_qs == rendered_qs, (
        "FAQPage Question.name set does not match materialised pairs"
    )
    for entry in main_entity:
        assert entry["@type"] == "Question"
        ans = entry["acceptedAnswer"]
        assert ans["@type"] == "Answer"
        assert ans["text"], "empty Answer.text"
