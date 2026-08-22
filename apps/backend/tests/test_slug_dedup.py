"""
Task 235 — Confirm slug deduplication still works correctly after a repair run
on real chapter data.

Three slug-dedup paths live inside _make_unique_slug (called by upsert_chapter):

  Step 2 — PDF-URL match + title correction:
      A chapter is found by source_pdf_url + chapter_number.  Its title has
      drifted (old PDF-URL bug).  The helper regenerates the slug from the
      corrected title; the result must not collide with sibling slugs.

  Step 3 — chapter_number fallback + title correction:
      No PDF-URL match.  The chapter is found by chapter_number alone.  Its
      title has drifted.  A sibling already owns the base slug, so the helper
      must append a -2 suffix.

  Step 4 — new chapter creation:
      No existing chapter matches the subject + title + chapter_number.  A
      sibling already owns the base slug, so the newly created chapter must
      receive a -2 suffix.

All tests run entirely in-process; no MongoDB / network calls are made.
"""

from __future__ import annotations

import re
import sys
import types
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers to build lightweight Chapter-like mocks
# ---------------------------------------------------------------------------

def _ch(id_: str, slug: str, title: str = "", subject_id: str = "subj-1",
        chapter_number: int = 1, source_pdf_url: str = "") -> MagicMock:
    """Return a MagicMock that quacks like a Chapter document."""
    ch = MagicMock()
    ch.id = id_
    ch.slug = slug
    ch.title = title or slug.replace("-", " ").title()
    ch.subject_id = subject_id
    ch.chapter_number = chapter_number
    ch.source_pdf_url = source_pdf_url
    ch.updated_at = datetime.now(timezone.utc)
    ch.save = AsyncMock()
    return ch


# ---------------------------------------------------------------------------
# Import the helpers under test (namespace package — no __init__.py needed)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def _import_ingest_helpers():
    """
    Import _make_unique_slug and upsert_chapter from scripts.ahsec_ingest.

    The script imports heavy optional deps (motor, beanie, etc.) at function
    call time, not at module level, so the import succeeds in a test
    environment that has none of those installed — as long as we mock
    app.models.content before calling the helpers.
    """
    import importlib
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "scripts.ahsec_ingest",
        Path(__file__).parent.parent / "scripts" / "ahsec_ingest.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["scripts.ahsec_ingest"] = mod
    spec.loader.exec_module(mod)

    # Expose on the module's globals so tests can reference them easily
    pytest._slug_dedup_module = mod


def _get_mod():
    return pytest._slug_dedup_module


# ---------------------------------------------------------------------------
# Tests for _make_unique_slug directly
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_make_unique_slug_no_collision():
    """_make_unique_slug returns the base slug unchanged when no siblings exist."""
    mod = _get_mod()

    sibling = _ch("ch-1", "thermodynamics", subject_id="subj-1")

    mock_find = MagicMock()
    mock_find.to_list = AsyncMock(return_value=[sibling])

    with patch("app.models.content.Chapter") as MockChapter:
        MockChapter.find.return_value = mock_find
        # Patch the deferred import inside _make_unique_slug
        with patch.dict("sys.modules", {"app.models.content": MagicMock(Chapter=MockChapter)}):
            result = await mod._make_unique_slug("subj-1", "kinematics")

    assert result == "kinematics"


@pytest.mark.anyio
async def test_make_unique_slug_appends_suffix_on_collision():
    """_make_unique_slug appends -2 when the base slug is already taken."""
    mod = _get_mod()

    sibling = _ch("ch-1", "cell-biology", subject_id="subj-1")

    mock_find = MagicMock()
    mock_find.to_list = AsyncMock(return_value=[sibling])

    with patch("app.models.content.Chapter") as MockChapter:
        MockChapter.find.return_value = mock_find
        with patch.dict("sys.modules", {"app.models.content": MagicMock(Chapter=MockChapter)}):
            result = await mod._make_unique_slug("subj-1", "cell-biology")

    assert result == "cell-biology-2"


@pytest.mark.anyio
async def test_make_unique_slug_chains_suffix_beyond_two():
    """_make_unique_slug increments the suffix until it finds a free slot."""
    mod = _get_mod()

    siblings = [
        _ch("ch-1", "motion", subject_id="subj-1"),
        _ch("ch-2", "motion-2", subject_id="subj-1"),
        _ch("ch-3", "motion-3", subject_id="subj-1"),
    ]

    mock_find = MagicMock()
    mock_find.to_list = AsyncMock(return_value=siblings)

    with patch("app.models.content.Chapter") as MockChapter:
        MockChapter.find.return_value = mock_find
        with patch.dict("sys.modules", {"app.models.content": MagicMock(Chapter=MockChapter)}):
            result = await mod._make_unique_slug("subj-1", "motion")

    assert result == "motion-4"


@pytest.mark.anyio
async def test_make_unique_slug_exclude_id_ignores_self():
    """exclude_id lets a chapter not collide with its own current slug."""
    mod = _get_mod()

    # The chapter being corrected already owns "waves"; it should keep it.
    self_chapter = _ch("ch-self", "waves", subject_id="subj-1")

    mock_find = MagicMock()
    mock_find.to_list = AsyncMock(return_value=[self_chapter])

    with patch("app.models.content.Chapter") as MockChapter:
        MockChapter.find.return_value = mock_find
        with patch.dict("sys.modules", {"app.models.content": MagicMock(Chapter=MockChapter)}):
            result = await mod._make_unique_slug(
                "subj-1", "waves", exclude_id="ch-self"
            )

    # Should NOT add -2 because the only slug owner is the chapter itself
    assert result == "waves"


# ---------------------------------------------------------------------------
# Step 2: PDF-URL match + title correction
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_step2_pdf_url_title_correction_slug_dedup():
    """
    Step 2: chapter found via source_pdf_url + chapter_number; its stored title
    differs from the ingest title (old PDF-URL bug).  The corrected slug must
    not collide with the sibling that already owns the base slug.

    Setup:
      - sibling ch-sibling has slug "units-and-measurements" (same as the
        corrected title would generate)
      - ch-bad was stored with the wrong title "Thermodynamics" but now the
        ingest provides the correct title "Units and Measurements"
      - After upsert_chapter the slug on ch-bad must be "units-and-measurements-2"
    """
    mod = _get_mod()

    subject_id = "subj-physics"
    pdf_url = "https://example.com/physics.pdf"

    ch_bad = _ch(
        id_="ch-bad",
        slug="thermodynamics",      # old wrong slug
        title="Thermodynamics",     # corrupted title
        subject_id=subject_id,
        chapter_number=1,
        source_pdf_url=pdf_url,
    )
    ch_sibling = _ch(
        id_="ch-sibling",
        slug="units-and-measurements",
        title="Units and Measurements",
        subject_id=subject_id,
        chapter_number=2,
        source_pdf_url=pdf_url,
    )

    # find_one(title match) → None  (title doesn't exist yet under correct name)
    # find(pdf_siblings)    → [ch_bad, ch_sibling]
    # find(all siblings for _make_unique_slug) → [ch_bad, ch_sibling]

    mock_pdf_find = MagicMock()
    mock_pdf_find.to_list = AsyncMock(return_value=[ch_bad, ch_sibling])

    mock_slug_find = MagicMock()
    mock_slug_find.to_list = AsyncMock(return_value=[ch_bad, ch_sibling])

    find_results = iter([mock_pdf_find, mock_slug_find])

    def _fake_find(query):
        return next(find_results)

    with patch("app.models.content.Chapter") as MockChapter:
        MockChapter.find.side_effect = _fake_find
        MockChapter.find_one = AsyncMock(return_value=None)

        with patch.dict("sys.modules", {"app.models.content": MagicMock(Chapter=MockChapter)}):
            chapter, created = await mod.upsert_chapter(
                subject_id=subject_id,
                chapter_num=1,
                title="Units and Measurements",
                medium="en",
                source_pdf_url=pdf_url,
            )

    assert not created, "should reuse the existing chapter, not create a new one"
    # ch_bad.slug was updated by upsert_chapter
    assert ch_bad.slug == "units-and-measurements-2", (
        f"expected 'units-and-measurements-2', got {ch_bad.slug!r}"
    )
    assert ch_bad.title == "Units and Measurements"
    ch_bad.save.assert_awaited_once()


# ---------------------------------------------------------------------------
# Step 3: chapter_number fallback + title correction with sibling present
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_step3_chapter_number_fallback_slug_dedup():
    """
    Step 3: no source_pdf_url provided.  Chapter found by chapter_number.
    Its stored title is wrong; corrected title would produce the same base slug
    as an existing sibling → upsert must append -2.

    Setup:
      - ch-old stored as chapter_number=3 with title "Old Title" / slug "old-title"
      - ch-sibling owns slug "electromagnetism" (same as the corrected title)
      - Corrected title = "Electromagnetism"
    """
    mod = _get_mod()

    subject_id = "subj-physics"

    ch_old = _ch(
        id_="ch-old",
        slug="old-title",
        title="Old Title",
        subject_id=subject_id,
        chapter_number=3,
        source_pdf_url="",
    )
    ch_sibling = _ch(
        id_="ch-sibling",
        slug="electromagnetism",
        title="Electromagnetism",
        subject_id=subject_id,
        chapter_number=5,
    )

    # find_one(title match) → None
    # find_one(chapter_number=3) → ch_old
    # find(all siblings for _make_unique_slug) → [ch_old, ch_sibling]

    mock_slug_find = MagicMock()
    mock_slug_find.to_list = AsyncMock(return_value=[ch_old, ch_sibling])

    async def _fake_find_one(query):
        if "chapter_number" in query:
            return ch_old
        return None

    with patch("app.models.content.Chapter") as MockChapter:
        MockChapter.find_one = AsyncMock(side_effect=_fake_find_one)
        MockChapter.find.return_value = mock_slug_find

        with patch.dict("sys.modules", {"app.models.content": MagicMock(Chapter=MockChapter)}):
            chapter, created = await mod.upsert_chapter(
                subject_id=subject_id,
                chapter_num=3,
                title="Electromagnetism",
                medium="en",
                source_pdf_url="",     # no PDF URL → step 3 path
            )

    assert not created
    assert ch_old.slug == "electromagnetism-2", (
        f"expected 'electromagnetism-2', got {ch_old.slug!r}"
    )
    assert ch_old.title == "Electromagnetism"
    ch_old.save.assert_awaited_once()


# ---------------------------------------------------------------------------
# Step 4: new chapter creation with pre-existing sibling slug
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_step4_new_chapter_creation_slug_dedup():
    """
    Step 4: no existing chapter matches subject + title + chapter_number.
    A sibling already owns the base slug derived from the title.
    The newly inserted chapter must receive slug 'gravitation-2'.

    Setup:
      - ch-existing owns slug "gravitation"
      - New chapter with title "Gravitation" is being inserted
    """
    mod = _get_mod()

    subject_id = "subj-physics"

    ch_existing = _ch(
        id_="ch-existing",
        slug="gravitation",
        title="Gravitation",
        subject_id=subject_id,
        chapter_number=10,
    )

    # find_one calls: title match → None; chapter_number match → None
    mock_slug_find = MagicMock()
    mock_slug_find.to_list = AsyncMock(return_value=[ch_existing])

    inserted_chapters: list = []

    async def _fake_insert(self_):
        self_.id = "ch-new"
        inserted_chapters.append(self_)

    with patch("app.models.content.Chapter") as MockChapter:
        MockChapter.find_one = AsyncMock(return_value=None)
        MockChapter.find.return_value = mock_slug_find

        # Capture the Chapter(...) constructor call and mock insert()
        real_chapter_instances: list = []

        def _chapter_constructor(**kwargs):
            obj = MagicMock()
            obj.__dict__.update(kwargs)
            for k, v in kwargs.items():
                setattr(obj, k, v)
            obj.insert = AsyncMock(side_effect=lambda: inserted_chapters.append(obj))
            real_chapter_instances.append(obj)
            return obj

        MockChapter.side_effect = _chapter_constructor

        with patch.dict("sys.modules", {"app.models.content": MagicMock(Chapter=MockChapter)}):
            chapter, created = await mod.upsert_chapter(
                subject_id=subject_id,
                chapter_num=11,
                title="Gravitation",
                medium="en",
            )

    assert created, "a new chapter should have been created"
    assert chapter.slug == "gravitation-2", (
        f"expected 'gravitation-2', got {chapter.slug!r}"
    )
    assert len(inserted_chapters) == 1, "insert() should be called exactly once"
