"""
Task 183 — Confirm the Assamese chapter URL still resolves after a seed run
(slug_as route).

After an Assamese seed run writes notes_as to a chapter, the public-facing
/as/* chapter route must return HTTP 200 with the correct Assamese content.
The route resolves chapters by matching slug_as first, then falls back to the
English slug when slug_as is absent.

Tests:
  1. slug_as route resolves via slug_as field when it is set on the chapter
  2. slug_as route falls back to English slug when slug_as is None (the normal
     post-seed state, since save_chapter_content() does not write slug_as)
  3. save_chapter_content() writes notes_as but does NOT populate slug_as
  4. Route returns 404 when chapter_slug matches neither slug nor slug_as
  5. Response body includes content_as and has_assamese=True after seed run
"""

import contextlib
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── helpers ────────────────────────────────────────────────────────────────────

def _make_board(name="AHSEC", slug="ahsec"):
    b = MagicMock()
    b.id = "board-1"
    b.name = name
    b.slug = slug
    b.status = "active"
    return b


def _make_class(name="Class 12", board_id="board-1"):
    c = MagicMock()
    c.id = "class-1"
    c.name = name
    c.board_id = board_id
    c.status = "active"
    return c


def _make_stream(name="Science", class_id="class-1"):
    s = MagicMock()
    s.id = "stream-1"
    s.name = name
    s.class_id = class_id
    s.status = "active"
    return s


def _make_subject(name="Biology", stream_id="stream-1", slug="biology"):
    s = MagicMock()
    s.id = "subject-1"
    s.name = name
    s.slug = slug
    s.stream_id = stream_id
    s.status = "active"
    return s


def _make_chapter(
    *,
    slug="cell-biology",
    slug_as=None,
    notes_en="English notes",
    notes_as="",
    content_en="",
    content_as="",
):
    ch = MagicMock()
    ch.id = "chapter-1"
    ch.title = "Cell Biology"
    ch.slug = slug
    ch.slug_as = slug_as
    ch.chapter_number = 1
    ch.notes_en = notes_en
    ch.notes_as = notes_as
    ch.content_en = content_en
    ch.content_as = content_as
    ch.published_topics = []
    ch.pyq_pdf_url = None
    ch.meta_description = ""
    ch.word_count = None
    ch.faq_jsonld = []
    ch.notes_generated = False
    ch.created_at = None
    ch.updated_at = None
    return ch


def _patch_hierarchy(board, cls, stream, subject, chapters):
    """
    Return a context manager that mocks the five DB calls made inside
    _resolve_chapter_by_slug: Board.find_one, Board.find, Class.find,
    Stream.find, Subject.find, Chapter.find.
    """
    board_query = MagicMock()
    board_query.to_list = AsyncMock(return_value=[board])
    board_find_one = AsyncMock(return_value=board)

    class_query = MagicMock()
    class_query.to_list = AsyncMock(return_value=[cls])

    stream_query = MagicMock()
    stream_query.to_list = AsyncMock(return_value=[stream])

    subject_query = MagicMock()
    subject_query.to_list = AsyncMock(return_value=[subject])

    chapter_query = MagicMock()
    chapter_query.sort = MagicMock(return_value=chapter_query)
    chapter_query.to_list = AsyncMock(return_value=chapters)

    @contextlib.contextmanager
    def _ctx():
        with (
            patch("app.api.v1.public_content.Board.find_one", board_find_one),
            patch("app.api.v1.public_content.Board.find", return_value=board_query),
            patch("app.api.v1.public_content.Class.find", return_value=class_query),
            patch("app.api.v1.public_content.Stream.find", return_value=stream_query),
            patch("app.api.v1.public_content.Subject.find", return_value=subject_query),
            patch("app.api.v1.public_content.Chapter.find", return_value=chapter_query),
        ):
            yield

    return _ctx()


# ── tests ──────────────────────────────────────────────────────────────────────

class TestSlugAsRouteResolution:
    """
    Confirm _resolve_chapter_by_slug (use_slug_as=True) correctly resolves the
    chapter after a seed run writes notes_as.
    """

    @pytest.mark.asyncio
    async def test_resolves_via_slug_as_field_when_set(self):
        """
        Primary path: chapter has slug_as populated and notes_as written by the
        Assamese seed run.  The route must match on slug_as and return the
        Assamese content.
        """
        from app.api.v1.public_content import _resolve_chapter_by_slug
        from starlette.responses import Response

        AS_SLUG = "কোষ-জীৱবিজ্ঞান"
        AS_NOTES = "কোষ বিভাজন সম্পর্কে অসমীয়া টোকা"

        board = _make_board()
        cls = _make_class()
        stream = _make_stream()
        subject = _make_subject()
        chapter = _make_chapter(
            slug="cell-biology",
            slug_as=AS_SLUG,
            notes_as=AS_NOTES,
        )

        response = Response()
        with _patch_hierarchy(board, cls, stream, subject, [chapter]):
            result = await _resolve_chapter_by_slug(
                "ahsec", "class-12", None, "biology", AS_SLUG,
                response, use_slug_as=True,
            )

        assert result["content_as"] == AS_NOTES, (
            "Route must return notes_as as content_as when chapter is resolved "
            "by slug_as after an Assamese seed run"
        )
        assert result["has_assamese"] is True
        assert result["chapter_slug"] == "cell-biology", (
            "chapter_slug in response should be the English slug"
        )

    @pytest.mark.asyncio
    async def test_resolves_via_english_slug_fallback_when_slug_as_absent(self):
        """
        The normal post-seed state: save_chapter_content() writes notes_as but
        does NOT populate slug_as.  The slug_as route must still resolve the
        chapter by falling back to the English slug, so students can access
        Assamese content even before slug_as is manually set.
        """
        from app.api.v1.public_content import _resolve_chapter_by_slug
        from starlette.responses import Response

        EN_SLUG = "cell-biology"
        AS_NOTES = "কোষ বিভাজন সম্পর্কে অসমীয়া টোকা"

        board = _make_board()
        cls = _make_class()
        stream = _make_stream()
        subject = _make_subject()
        chapter = _make_chapter(
            slug=EN_SLUG,
            slug_as=None,   # not set — typical state right after seed run
            notes_as=AS_NOTES,
        )

        response = Response()
        with _patch_hierarchy(board, cls, stream, subject, [chapter]):
            result = await _resolve_chapter_by_slug(
                "ahsec", "class-12", None, "biology", EN_SLUG,
                response, use_slug_as=True,
            )

        assert result["content_as"] == AS_NOTES, (
            "Assamese content must be accessible via the English slug on the "
            "slug_as route when slug_as has not been populated by ingestion"
        )
        assert result["has_assamese"] is True

    @pytest.mark.asyncio
    async def test_response_includes_assamese_content_keys(self):
        """
        Full response-shape check: after a seed run the route's JSON body must
        include content_as (non-empty) and has_assamese=True so the frontend
        reader tab can render.
        """
        from app.api.v1.public_content import _resolve_chapter_by_slug
        from starlette.responses import Response

        board = _make_board()
        cls = _make_class()
        stream = _make_stream()
        subject = _make_subject()
        chapter = _make_chapter(
            slug="cell-biology",
            slug_as="কোষ-জীৱবিজ্ঞান",
            notes_as="অসমীয়া বিষয়বস্তু",
        )

        response = Response()
        with _patch_hierarchy(board, cls, stream, subject, [chapter]):
            result = await _resolve_chapter_by_slug(
                "ahsec", "class-12", None, "biology", "কোষ-জীৱবিজ্ঞান",
                response, use_slug_as=True,
            )

        assert "content_as" in result, "content_as key must be present in response"
        assert "has_assamese" in result, "has_assamese key must be present in response"
        assert isinstance(result["content_as"], str)
        assert isinstance(result["has_assamese"], bool)
        assert result["content_as"] != "", (
            "content_as must be non-empty after seed run writes notes_as"
        )

    @pytest.mark.asyncio
    async def test_returns_404_when_slug_matches_nothing(self):
        """
        The route must raise a 404 when the chapter_slug matches neither the
        English slug nor slug_as, so students get a clear error rather than
        a silent empty response.
        """
        from app.api.v1.public_content import _resolve_chapter_by_slug
        from starlette.responses import Response
        from fastapi import HTTPException

        board = _make_board()
        cls = _make_class()
        stream = _make_stream()
        subject = _make_subject()
        chapter = _make_chapter(
            slug="cell-biology",
            slug_as="কোষ-জীৱবিজ্ঞান",
            notes_as="অসমীয়া টোকা",
        )

        response = Response()
        with pytest.raises(HTTPException) as exc_info:
            with _patch_hierarchy(board, cls, stream, subject, [chapter]):
                await _resolve_chapter_by_slug(
                    "ahsec", "class-12", None, "biology", "wrong-slug-xyz",
                    response, use_slug_as=True,
                )

        assert exc_info.value.status_code == 404, (
            "slug_as route must return 404 when chapter_slug resolves to nothing"
        )


class TestSaveChapterContentSlugAsBehavior:
    """
    Confirm that save_chapter_content() writes notes_as AND populates slug_as
    so that the /as/* route resolves to a proper Assamese URL slug.
    """

    @pytest.mark.asyncio
    async def test_save_chapter_content_writes_slug_as_from_title_as_param(self):
        """
        When the Assamese PDF title is passed via title_as=, save_chapter_content
        must derive slug_as from it, keeping all Assamese combining marks intact
        (vowel signs Mc and virama Mn must NOT be stripped).

        "কোষ জীৱবিজ্ঞান" → "কোষ-জীৱবিজ্ঞান"
        (spaces → hyphens; all Assamese Unicode characters preserved)
        """
        from scripts.ahsec_ingest import save_chapter_content

        AS_NOTES = "অসমীয়া পাঠ্যক্রমৰ টোকা"
        AS_TITLE = "কোষ জীৱবিজ্ঞান"
        EXPECTED_SLUG = "কোষ-জীৱবিজ্ঞান"

        chapter = MagicMock()
        chapter.notes_as = ""
        chapter.content_as = ""
        chapter.slug_as = None
        chapter.title_as = None   # not pre-set — ingestion provides it via param
        chapter.slug = "cell-biology"
        chapter.notes_en = ""
        chapter.content_en = ""
        chapter.rag_sections_as = []
        chapter.word_count = 0
        chapter.notes_generated = False
        chapter.save = AsyncMock()

        written = await save_chapter_content(
            chapter=chapter,
            notes_text=AS_NOTES,
            rag_sections=[],
            qa_sections=[],
            topics=[],
            medium="as",
            force=True,
            title_as=AS_TITLE,   # the Assamese PDF chapter title
        )

        assert written is True, "save_chapter_content must return True when content is saved"
        assert chapter.notes_as == AS_NOTES, (
            "notes_as must be set to the supplied Assamese text"
        )
        assert chapter.content_as == AS_NOTES, (
            "content_as must be kept in sync with notes_as"
        )
        assert chapter.title_as == AS_TITLE, (
            "title_as must be populated on the chapter from the supplied param"
        )
        assert chapter.slug_as == EXPECTED_SLUG, (
            f"slug_as must equal {EXPECTED_SLUG!r}; "
            "Assamese combining marks (vowel signs, virama) must be preserved "
            "in the slug — stripping them produces a corrupt, unreadable URL"
        )
        chapter.save.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_save_chapter_content_falls_back_to_english_slug_when_no_title_as(self):
        """
        When no Assamese title is available (title_as param absent and
        chapter.title_as not set), save_chapter_content(medium='as') must fall
        back to the English slug for slug_as so that the /as/* route still
        resolves without requiring a separate staff action.
        """
        from scripts.ahsec_ingest import save_chapter_content

        AS_NOTES = "অসমীয়া পাঠ্যক্রমৰ টোকা"
        EN_SLUG = "cell-biology"

        chapter = MagicMock()
        chapter.notes_as = ""
        chapter.content_as = ""
        chapter.slug_as = None
        chapter.title_as = None
        chapter.slug = EN_SLUG
        chapter.notes_en = ""
        chapter.content_en = ""
        chapter.rag_sections_as = []
        chapter.word_count = 0
        chapter.notes_generated = False
        chapter.save = AsyncMock()

        written = await save_chapter_content(
            chapter=chapter,
            notes_text=AS_NOTES,
            rag_sections=[],
            qa_sections=[],
            topics=[],
            medium="as",
            force=True,
            # no title_as supplied
        )

        assert written is True, "save_chapter_content must return True when content is saved"
        assert chapter.notes_as == AS_NOTES
        assert chapter.slug_as == EN_SLUG, (
            "slug_as must fall back to the English slug when no Assamese title "
            "is available, so the /as/* route resolves via the existing English slug"
        )
        chapter.save.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_save_chapter_content_does_not_overwrite_existing_slug_as(self):
        """
        When slug_as is already set (e.g. manually by staff), ingestion must
        not overwrite it.  This protects hand-crafted slugs from being stomped.
        """
        from scripts.ahsec_ingest import save_chapter_content

        EXISTING_SLUG_AS = "কোষ-বিজ্ঞান"

        chapter = MagicMock()
        chapter.notes_as = ""
        chapter.content_as = ""
        chapter.slug_as = EXISTING_SLUG_AS
        chapter.title_as = None
        chapter.slug = "cell-biology"
        chapter.notes_en = ""
        chapter.content_en = ""
        chapter.rag_sections_as = []
        chapter.word_count = 0
        chapter.notes_generated = False
        chapter.save = AsyncMock()

        await save_chapter_content(
            chapter=chapter,
            notes_text="new notes",
            rag_sections=[],
            qa_sections=[],
            topics=[],
            medium="as",
            force=True,
            title_as="কোষ জীৱবিজ্ঞান",   # a different Assamese title supplied
        )

        assert chapter.slug_as == EXISTING_SLUG_AS, (
            "save_chapter_content must not overwrite an existing slug_as; "
            "manual slugs set by staff must be preserved across re-ingestion"
        )

    @pytest.mark.asyncio
    async def test_save_chapter_content_skips_when_notes_as_present_without_force(self):
        """
        A second seed run must not overwrite existing Assamese notes unless
        --force is passed.  This protects manually-edited notes_as from being
        stomped by re-ingestion.
        """
        from scripts.ahsec_ingest import save_chapter_content

        EXISTING = "A" * 200   # >100 chars → treated as 'already has notes'

        chapter = MagicMock()
        chapter.notes_as = EXISTING
        chapter.save = AsyncMock()

        written = await save_chapter_content(
            chapter=chapter,
            notes_text="new content that should not overwrite",
            rag_sections=[],
            qa_sections=[],
            topics=[],
            medium="as",
            force=False,   # no force
        )

        assert written is False, (
            "save_chapter_content must return False and skip saving "
            "when notes_as is already present and force=False"
        )
        chapter.save.assert_not_awaited()


class TestSlugAsRoutePartialSeedFailure:
    """
    Task 187 — Confirm students can't access Assamese content via /as/...
    when a seed run fails mid-way.

    When an Assamese seed run is interrupted (e.g. provider timeout), some
    chapters are saved with empty notes_as.  The /as/* route must return a
    graceful empty state (has_assamese=False, content_as='') rather than an
    error or a silent empty page.
    """

    @pytest.mark.asyncio
    async def test_empty_notes_as_returns_has_assamese_false(self):
        """
        Primary regression guard: a chapter that exists in the DB but whose
        notes_as is blank (because the seed run was interrupted after the
        chapter record was created but before Assamese content was written)
        must return HTTP 200 with has_assamese=False and content_as=''.

        A 500 or a missing has_assamese key here would silently break the
        frontend reader tab for every student hitting that chapter's /as/ URL.
        """
        from app.api.v1.public_content import _resolve_chapter_by_slug
        from starlette.responses import Response

        EN_SLUG = "cell-biology"

        board = _make_board()
        cls = _make_class()
        stream = _make_stream()
        subject = _make_subject()
        # Seed run failed mid-way: chapter record exists but notes_as is empty
        chapter = _make_chapter(
            slug=EN_SLUG,
            slug_as=None,
            notes_as="",      # blank — seed interrupted
            content_as="",
        )

        response = Response()
        with _patch_hierarchy(board, cls, stream, subject, [chapter]):
            result = await _resolve_chapter_by_slug(
                "ahsec", "class-12", None, "biology", EN_SLUG,
                response, use_slug_as=True,
            )

        assert result["has_assamese"] is False, (
            "Route must return has_assamese=False when notes_as is blank "
            "after a partial/interrupted seed run"
        )
        assert result["content_as"] == "", (
            "Route must return an empty content_as string when notes_as is "
            "blank — not None, not omitted, not a 500"
        )
        # The response must still be a well-formed dict (not an exception)
        assert "chapter_id" in result, (
            "Route must return a complete chapter response even when "
            "Assamese content is absent"
        )

    @pytest.mark.asyncio
    async def test_frontend_signal_present_when_no_assamese_content(self):
        """
        The frontend reader tab decides whether to show 'No Assamese content'
        by reading has_assamese and content_as from the chapter response.
        Both keys must be present and carry the correct types so the tab
        can render the empty state rather than crashing or showing a spinner
        forever.

        This test also covers the slug_as=set case: if slug_as was written to
        the chapter document before notes_as was populated, the route still
        resolves the chapter correctly and signals the empty state.
        """
        from app.api.v1.public_content import _resolve_chapter_by_slug
        from starlette.responses import Response

        board = _make_board()
        cls = _make_class()
        stream = _make_stream()
        subject = _make_subject()
        # slug_as was set (e.g. by a prior step) but notes_as never arrived
        chapter = _make_chapter(
            slug="photosynthesis",
            slug_as="সালোক-সংশ্লেষণ",
            notes_as="",      # seed never wrote Assamese content
            content_as="",
        )

        response = Response()
        with _patch_hierarchy(board, cls, stream, subject, [chapter]):
            result = await _resolve_chapter_by_slug(
                "ahsec", "class-12", None, "biology", "সালোক-সংশ্লেষণ",
                response, use_slug_as=True,
            )

        # Both signal keys must be present
        assert "has_assamese" in result, (
            "has_assamese must always be present in the /as/ route response "
            "so the frontend reader tab can make a conditional render decision"
        )
        assert "content_as" in result, (
            "content_as must always be present in the /as/ route response "
            "even when Assamese content is absent"
        )

        # Signal values must correctly reflect the empty-content state
        assert result["has_assamese"] is False, (
            "has_assamese must be False when content_as is empty, "
            "regardless of whether slug_as is set on the chapter"
        )
        assert result["content_as"] == "", (
            "content_as must be an empty string (not None) so the frontend "
            "can safely call .length or display it without a null-guard"
        )
        assert isinstance(result["has_assamese"], bool), (
            "has_assamese must be a bool so the frontend conditional "
            "if (has_assamese) renders correctly"
        )
        assert isinstance(result["content_as"], str), (
            "content_as must be a string so the frontend can safely render it"
        )


# ── _slug_as() unit tests ──────────────────────────────────────────────────────

class TestSlugAsHelper:
    """
    Exact-output tests for _slug_as() to guard against character stripping.

    Assamese orthography depends on combining marks (Unicode Mc and Mn
    categories) that Python's \\w regex does NOT match.  A regex-based filter
    would silently corrupt every vowel sign and virama, producing unreadable
    URLs.  These tests pin the exact expected output so any regression is
    caught immediately.
    """

    def test_basic_assamese_title_preserves_combining_marks(self):
        """
        "কোষ জীৱবিজ্ঞান" must become "কোষ-জীৱবিজ্ঞান" — spaces become hyphens
        and all vowel signs (ো ী ি া) and virama (্) are kept intact.
        """
        from scripts.ahsec_ingest import _slug_as
        result = _slug_as("কোষ জীৱবিজ্ঞান")
        assert result == "কোষ-জীৱবিজ্ঞান", (
            f"Got {result!r}; combining marks must not be stripped — "
            "Python \\w misses Mc/Mn categories (vowel signs, virama)"
        )

    def test_multiple_spaces_become_single_hyphen(self):
        from scripts.ahsec_ingest import _slug_as
        result = _slug_as("কোষ  বিভাজন")
        assert result == "কোষ-বিভাজন"

    def test_punctuation_stripped_combining_marks_kept(self):
        """Punctuation is removed but vowel signs and virama are retained."""
        from scripts.ahsec_ingest import _slug_as
        # comma and colon stripped; combining marks on ো, ু kept
        result = _slug_as("জীৱ, বিজ্ঞান:")
        assert result == "জীৱ-বিজ্ঞান"

    def test_already_hyphenated_input(self):
        from scripts.ahsec_ingest import _slug_as
        result = _slug_as("কোষ-জীৱবিজ্ঞান")
        assert result == "কোষ-জীৱবিজ্ঞান"

    def test_english_fallback_slug_passes_through(self):
        """English slugs (ASCII) must also work correctly as a fallback check."""
        from scripts.ahsec_ingest import _slug_as
        result = _slug_as("cell biology")
        assert result == "cell-biology"


# ── Ingestion-level regression test ───────────────────────────────────────────

class TestIngestionSlugAsEndToEnd:
    """
    Confirm the normal EN-then-AS seed workflow produces a persisted slug_as
    that resolves via _resolve_chapter_by_slug(use_slug_as=True).

    This is an integration-style unit test: it calls save_chapter_content()
    twice (once for EN, once for AS) on the same mock chapter and then runs
    the slug resolution logic against the result.
    """

    @pytest.mark.asyncio
    async def test_en_then_as_seed_produces_resolvable_slug_as(self):
        """
        After an English seed run followed by an Assamese seed run:
        1. chapter.slug_as is populated (derived from the Assamese PDF title)
        2. _resolve_chapter_by_slug(use_slug_as=True) resolves the chapter
           when given slug_as as chapter_slug
        3. The response body carries has_assamese=True and non-empty content_as
        """
        from scripts.ahsec_ingest import save_chapter_content
        from app.api.v1.public_content import _resolve_chapter_by_slug
        from starlette.responses import Response

        AS_TITLE = "কোষ জীৱবিজ্ঞান"
        EXPECTED_SLUG_AS = "কোষ-জীৱবিজ্ঞান"
        EN_NOTES = "English notes about cell biology."
        AS_NOTES = "অসমীয়া কোষ বিজ্ঞানৰ টোকা।"

        # ── Step 1: English seed run ──────────────────────────────────────────
        chapter = MagicMock()
        chapter.notes_en = ""
        chapter.content_en = ""
        chapter.notes_as = ""
        chapter.content_as = ""
        chapter.slug_as = None
        chapter.title_as = None
        chapter.slug = "cell-biology"
        chapter.rag_sections_en = []
        chapter.word_count = 0
        chapter.notes_generated = False
        chapter.save = AsyncMock()

        await save_chapter_content(
            chapter=chapter,
            notes_text=EN_NOTES,
            rag_sections=[],
            qa_sections=[],
            topics=[],
            medium="en",
            force=True,
        )
        # After EN seed: slug_as must remain unset
        assert chapter.slug_as is None, (
            "English seed must not set slug_as — no Assamese title is available yet"
        )

        # ── Step 2: Assamese seed run ─────────────────────────────────────────
        chapter.notes_as = ""       # reset for the AS write
        chapter.content_as = ""
        chapter.rag_sections_as = []
        chapter.save = AsyncMock()

        await save_chapter_content(
            chapter=chapter,
            notes_text=AS_NOTES,
            rag_sections=[],
            qa_sections=[],
            topics=[],
            medium="as",
            force=True,
            title_as=AS_TITLE,      # Assamese PDF chapter title
        )

        # slug_as must now be the Assamese-script slug
        assert chapter.slug_as == EXPECTED_SLUG_AS, (
            f"After AS seed, slug_as must be {EXPECTED_SLUG_AS!r}; "
            f"got {chapter.slug_as!r}"
        )
        assert chapter.title_as == AS_TITLE, (
            "title_as must be populated on the chapter document"
        )

        # ── Step 3: Route resolves via slug_as ───────────────────────────────
        # Reconstruct a chapter mock that _resolve_chapter_by_slug can match
        ch = _make_chapter(
            slug="cell-biology",
            slug_as=EXPECTED_SLUG_AS,
            notes_as=AS_NOTES,
        )

        board = _make_board()
        cls = _make_class()
        stream = _make_stream()
        subject = _make_subject()
        response = Response()

        with _patch_hierarchy(board, cls, stream, subject, [ch]):
            result = await _resolve_chapter_by_slug(
                "ahsec", "class-12", None, "biology", EXPECTED_SLUG_AS,
                response, use_slug_as=True,
            )

        assert result["has_assamese"] is True, (
            "Route must return has_assamese=True after the AS seed run"
        )
        assert result["content_as"] == AS_NOTES, (
            "Route must return the Assamese notes as content_as"
        )
        assert result["slug_as"] == EXPECTED_SLUG_AS, (
            "Route response must include slug_as so the frontend can build "
            "the canonical /as/… URL for sharing and SEO"
        )

    @pytest.mark.asyncio
    async def test_duplicate_key_error_on_save_triggers_retry_with_new_slug(self):
        """
        When two concurrent Assamese seed workers both compute the same slug_as
        and one wins the DB write, the loser's save() raises DuplicateKeyError.
        save_chapter_content() must catch that error, call _ensure_unique_slug_as
        to get a fresh suffix, and retry the save — the chapter must end up
        persisted with a unique slug_as, not raise an unhandled exception.
        """
        from scripts.ahsec_ingest import save_chapter_content
        from pymongo.errors import DuplicateKeyError as _DuplicateKeyError
        from unittest.mock import AsyncMock, MagicMock, patch

        SUBJECT_ID = "subj-concurrent"
        INITIAL_SLUG_AS = "পাঠ্যপুথি"
        RETRY_SLUG_AS   = "পাঠ্যপুথি-2"

        chapter = MagicMock()
        chapter.notes_as = ""
        chapter.content_as = ""
        chapter.slug_as = None
        chapter.title_as = None
        chapter.slug = "full-book"
        chapter.subject_id = SUBJECT_ID
        chapter.id = "chap-new"
        chapter.notes_en = ""
        chapter.content_en = ""
        chapter.rag_sections_as = []
        chapter.word_count = 0
        chapter.notes_generated = False

        # First save: raises DuplicateKeyError simulating a concurrent write collision.
        # Second save: succeeds.
        dup_error = _DuplicateKeyError(
            "E11000 duplicate key error collection: syrabit.chapters "
            "index: chapters_subject_slug_as_unique "
            "dup key: { subject_id: \"subj-concurrent\", slug_as: \"পাঠ্যপুথি\" }"
        )
        chapter.save = AsyncMock(side_effect=[dup_error, None])

        # After the first DuplicateKeyError, _ensure_unique_slug_as is called with
        # the conflicting slug; it must find that slug taken and return the suffix variant.
        existing_ch = MagicMock()
        existing_ch.id = "chap-rival"
        existing_ch.slug_as = INITIAL_SLUG_AS

        chapter_query = MagicMock()
        chapter_query.to_list = AsyncMock(return_value=[existing_ch])

        with patch("app.models.content.Chapter.find", return_value=chapter_query):
            written = await save_chapter_content(
                chapter=chapter,
                notes_text="অসমীয়া টোকা।",
                rag_sections=[],
                qa_sections=[],
                topics=[],
                medium="as",
                force=True,
                title_as="পাঠ্যপুথি",
            )

        assert written is True, "save must succeed after the DuplicateKeyError retry"
        assert chapter.save.await_count == 2, (
            "save must be called exactly twice: once for the initial attempt, "
            "once for the retry after slug_as disambiguation"
        )
        assert chapter.slug_as == RETRY_SLUG_AS, (
            f"After retry, slug_as must be {RETRY_SLUG_AS!r} (the suffix variant); "
            f"got {chapter.slug_as!r}"
        )

    @pytest.mark.asyncio
    async def test_two_same_titled_chapters_get_unique_slug_as(self):
        """
        When two chapters in the same subject share the same Assamese title
        (e.g. two "Full Book" parts), _ensure_unique_slug_as must produce
        distinct slug_as values so each /as/… URL resolves to its own chapter.

        Ch1: "পাঠ্যপুথি" → slug_as = "পাঠ্যপুথি"
        Ch2: "পাঠ্যপুথি" → slug_as = "পাঠ্যপুথি-2"  (collision avoided)
        """
        from scripts.ahsec_ingest import _ensure_unique_slug_as
        from unittest.mock import AsyncMock, MagicMock, patch

        SUBJECT_ID = "subject-abc"
        CANDIDATE   = "পাঠ্যপুথি"

        # Existing chapter in the same subject that already has slug_as = CANDIDATE
        existing_ch = MagicMock()
        existing_ch.id = "chapter-existing"
        existing_ch.slug_as = CANDIDATE

        # New chapter (different id) being processed
        new_chapter = MagicMock()
        new_chapter.subject_id = SUBJECT_ID
        new_chapter.id = "chapter-new"

        chapter_query = MagicMock()
        chapter_query.to_list = AsyncMock(return_value=[existing_ch])

        with patch("app.models.content.Chapter.find", return_value=chapter_query):
            # Import the module so the patch resolves against the right namespace
            import importlib, sys
            # Re-import to pick up patch
            from scripts.ahsec_ingest import _ensure_unique_slug_as as _fn
            result = await _fn(new_chapter, CANDIDATE)

        assert result == f"{CANDIDATE}-2", (
            f"Expected {CANDIDATE!r}-2 but got {result!r}; "
            "colliding slug_as values in the same subject must be disambiguated "
            "with a numeric suffix so each /as/… URL resolves to its own chapter"
        )

    @pytest.mark.asyncio
    async def test_retry_path_also_sets_slug_as_from_assamese_title(self):
        """
        When a stuck Assamese chapter is retried via the admin retry endpoint,
        save_chapter_content() is called with title_as=ch_title so the chapter
        receives slug_as from the Assamese PDF title — not an English slug fallback.

        This regression test guards the admin_content.py retry path specifically;
        the main ingestion loop test above covers the primary ahsec_ingest.py path.
        """
        from scripts.ahsec_ingest import save_chapter_content

        AS_TITLE = "সালোক-সংশ্লেষণ"           # Assamese PDF title for the chapter
        EXPECTED_SLUG = "সালোক-সংশ্লেষণ"       # spaces already hyphens; punctuation stripped

        chapter = MagicMock()
        chapter.notes_as = ""
        chapter.content_as = ""
        chapter.slug_as = None
        chapter.title_as = None
        chapter.slug = "photosynthesis"
        chapter.notes_en = "English notes."
        chapter.content_en = "English notes."
        chapter.rag_sections_as = []
        chapter.word_count = 0
        chapter.notes_generated = True
        chapter.save = AsyncMock()

        # Simulate the admin retry call with title_as=ch_title (medium='as')
        written = await save_chapter_content(
            chapter=chapter,
            notes_text="অসমীয়া সালোক সংশ্লেষণৰ টোকা।",
            rag_sections=[],
            qa_sections=[],
            topics=[],
            medium="as",
            force=True,
            title_as=AS_TITLE,   # the Assamese PDF title passed by the retry path
        )

        assert written is True
        assert chapter.title_as == AS_TITLE, (
            "Retry path must persist the Assamese PDF title to chapter.title_as"
        )
        assert chapter.slug_as == EXPECTED_SLUG, (
            f"Retry path must derive slug_as {EXPECTED_SLUG!r} from the Assamese "
            f"title, not fall back to the English slug 'photosynthesis'"
        )
