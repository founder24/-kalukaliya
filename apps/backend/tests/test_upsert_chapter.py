"""
Regression tests for upsert_chapter in scripts/ahsec_ingest.py.

Covers the three matching paths and the title-corruption bug introduced by the
old PDF-URL fallback that did find_one({subject_id, source_pdf_url}) without
any further discriminator.  All chapters in a textbook share the same PDF URL,
so the old code grabbed chapter 1's record for every subsequent chapter and
overwrote its chapter_number — leaving the wrong title in place forever.

Patch strategy: all Beanie / MongoDB calls inside upsert_chapter are mocked
so tests run without a real database.
"""

from __future__ import annotations

import re
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_chapter(id_str: str, title: str, chapter_number: int,
                  subject_id: str = "subj1",
                  source_pdf_url: str = "https://example.com/book.pdf",
                  slug: str | None = None) -> MagicMock:
    """Return a mock Chapter object with the minimum fields upsert_chapter reads."""
    ch = MagicMock()
    ch.id = id_str
    ch.title = title
    ch.chapter_number = chapter_number
    ch.subject_id = subject_id
    ch.source_pdf_url = source_pdf_url
    ch.slug = slug or re.sub(r"[\s_-]+", "-",
                              re.sub(r"[^\w\s-]", "", title.lower())).strip("-")
    ch.updated_at = datetime.now(timezone.utc)
    ch.save = AsyncMock()
    return ch


async def _run_upsert(
    subject_id: str,
    chapter_num: int,
    title: str,
    medium: str = "en",
    source_pdf_url: str = "https://example.com/book.pdf",
    *,
    # What find_one returns for each call (step 1: title match, step 3: number match)
    title_match: object | None = None,
    number_match: object | None = None,
    # What find() returns for the PDF-sibling query (step 2)
    pdf_siblings: list | None = None,
    # What find() returns for the slug-uniqueness scan (step 4 / step-3 correction)
    slug_siblings: list | None = None,
) -> tuple:
    """Run upsert_chapter with fully mocked Beanie."""
    from scripts.ahsec_ingest import upsert_chapter  # noqa: PLC0415

    # find_one: first call = title match (step 1), second = number match (step 3)
    find_one_results = [title_match, number_match]
    find_one_iter = iter(find_one_results)
    find_one_mock = AsyncMock(side_effect=lambda *a, **kw: next(find_one_iter))

    # find().to_list(): first call = PDF siblings (step 2),
    # subsequent calls = slug-uniqueness scans
    slug_siblings = slug_siblings or []
    pdf_siblings = pdf_siblings or []

    find_call_count = {"n": 0}

    def _find_side_effect(*args, **kwargs):
        proxy = MagicMock()
        call_n = find_call_count["n"]
        find_call_count["n"] += 1
        if call_n == 0:
            # First find() call → PDF sibling lookup (step 2)
            proxy.to_list = AsyncMock(return_value=pdf_siblings)
        else:
            # Later find() calls → slug-uniqueness scans
            proxy.to_list = AsyncMock(return_value=slug_siblings)
        return proxy

    # Minimal Chapter mock for the newly created row (step 4)
    new_chapter_mock = _make_chapter("new-id", title, chapter_num,
                                     subject_id=subject_id,
                                     source_pdf_url=source_pdf_url)
    new_chapter_mock.insert = AsyncMock()

    chapter_cls = MagicMock()
    chapter_cls.find_one = find_one_mock
    chapter_cls.find = MagicMock(side_effect=_find_side_effect)
    chapter_cls.return_value = new_chapter_mock  # Chapter(...)

    with patch("scripts.ahsec_ingest.re", re), \
         patch("app.models.content.Chapter", chapter_cls), \
         patch.dict("sys.modules", {"app.models.content": MagicMock(Chapter=chapter_cls)}):
        result = await upsert_chapter(
            subject_id, chapter_num, title, medium, source_pdf_url=source_pdf_url
        )
    return result


# ── Tests ──────────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_step1_title_match_returns_same_chapter():
    """Step 1: exact title match returns the stored chapter unchanged."""
    stored = _make_chapter("ch1-id", "Units and Measurements", 1)
    chapter, created = await _run_upsert(
        "subj1", 1, "Units and Measurements",
        title_match=stored,
    )
    assert chapter is stored
    assert created is False
    stored.save.assert_not_called()


@pytest.mark.anyio
async def test_step1_title_match_corrects_chapter_number():
    """Step 1: title matches but stored chapter_number differs — update it."""
    stored = _make_chapter("ch1-id", "Units and Measurements", 99)
    chapter, created = await _run_upsert(
        "subj1", 1, "Units and Measurements",
        title_match=stored,
    )
    assert chapter.chapter_number == 1
    stored.save.assert_called_once()


@pytest.mark.anyio
async def test_step2_pdf_url_narrows_by_chapter_number():
    """Step 2: with multiple PDF siblings, only the one matching chapter_number is reused.

    This is the core regression test.  Before the fix, find_one({source_pdf_url})
    returned ch1 for every chapter number, causing chapter 2's record to steal
    chapter 1's title.  After the fix, chapter 2 is matched by chapter_number
    within the sibling set and returned correctly.
    """
    ch1 = _make_chapter("ch1-id", "Units and Measurements", 1)
    ch2 = _make_chapter("ch2-id", "Motion in a Straight Line", 2)

    # Ingestion is processing chapter 2 — title "Motion in a Straight Line" not in DB yet
    chapter, created = await _run_upsert(
        "subj1", 2, "Motion in a Straight Line",
        title_match=None,          # step 1: no title match
        pdf_siblings=[ch1, ch2],   # step 2: both chapters share the PDF URL
        number_match=None,         # step 3 never reached
    )
    assert chapter is ch2, (
        "upsert_chapter should have matched ch2 by chapter_number, not ch1"
    )
    assert created is False
    # ch1's chapter_number and title must be untouched
    assert ch1.chapter_number == 1
    assert ch1.title == "Units and Measurements"


@pytest.mark.anyio
async def test_step2_corrects_title_when_corrupted_by_old_bug():
    """Step 2: when matched by chapter_number the title is corrected in-place.

    Simulates the exact corruption the old bug caused: chapter 2's record has
    chapter_number=2 (correctly updated) but still carries chapter 1's title.
    A --force re-run should correct it.
    """
    corrupted_ch2 = _make_chapter(
        "ch2-id", "Units and Measurements", 2,  # wrong title from old bug
        source_pdf_url="https://example.com/book.pdf",
    )
    # slug_siblings for the uniqueness scan
    ch1_for_slug = _make_chapter("ch1-id", "Units and Measurements", 1,
                                  slug="units-and-measurements")

    chapter, created = await _run_upsert(
        "subj1", 2, "Motion in a Straight Line",
        title_match=None,
        pdf_siblings=[corrupted_ch2],   # only sibling; matched by chapter_number
        number_match=None,
        slug_siblings=[ch1_for_slug],
    )

    assert chapter is corrupted_ch2
    assert chapter.title == "Motion in a Straight Line", (
        "Corrupted title should have been corrected to the incoming title"
    )
    assert chapter.slug == "motion-in-a-straight-line"
    corrupted_ch2.save.assert_called_once()


@pytest.mark.anyio
async def test_step2_does_not_steal_unrelated_sibling():
    """Step 2: if no sibling matches chapter_number or title, fall through to step 3."""
    ch1 = _make_chapter("ch1-id", "Units and Measurements", 1)
    # ch3 is a new chapter not yet in the PDF sibling set
    ch3_number_match = _make_chapter("ch3-id", "Motion in a Plane", 3)

    chapter, created = await _run_upsert(
        "subj1", 3, "Motion in a Plane",
        title_match=None,
        pdf_siblings=[ch1],          # only ch1 in DB — no number or title match for ch3
        number_match=ch3_number_match,  # step 3 finds it by number
    )
    # Must have fallen through to step 3 and returned ch3, not ch1
    assert chapter is ch3_number_match
    assert ch1.chapter_number == 1   # ch1 untouched


@pytest.mark.anyio
async def test_step3_corrects_corrupted_title_via_number_fallback():
    """Step 3: chapter matched by chapter_number gets its corrupted title corrected."""
    corrupted = _make_chapter(
        "ch7-id", "Units and Measurements", 7,  # wrong title, no source_pdf_url set
        source_pdf_url="",
    )
    ch1_slug = _make_chapter("ch1-id", "Units and Measurements", 1,
                              slug="units-and-measurements")

    chapter, created = await _run_upsert(
        "subj1", 7, "Thermodynamics",
        source_pdf_url="",           # no PDF URL → skip step 2 entirely
        title_match=None,
        number_match=corrupted,
        slug_siblings=[ch1_slug],
    )

    assert chapter is corrupted
    assert chapter.title == "Thermodynamics"
    assert chapter.slug == "thermodynamics"
    corrupted.save.assert_called_once()


@pytest.mark.anyio
async def test_step4_creates_new_chapter_when_no_match():
    """Step 4: no match at any step → a new Chapter is inserted and created=True."""
    chapter, created = await _run_upsert(
        "subj1", 5, "Laws of Motion",
        title_match=None,
        pdf_siblings=[],
        number_match=None,
        slug_siblings=[],
    )
    assert created is True
    assert chapter.title == "Laws of Motion"
    assert chapter.chapter_number == 5
