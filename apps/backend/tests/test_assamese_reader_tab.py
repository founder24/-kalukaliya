"""
Task 152 — Confirm the Assamese reader tab shows bulk-translated content
without a full republish.

The chapter-by-slug endpoint resolves chapters directly from the Chapter
document.  After a seed-assamese run writes notes_as to the DB, the API
must return a non-empty content_as (and has_assamese=True) without
requiring the chapter to pass through the full publish pipeline.

Tests:
  1. notes_as written by seed-assamese → content_as returned, has_assamese=True
  2. notes_as absent, content_as present (legacy) → content_as returned,
     has_assamese=True (backward-compat)
  3. Both notes_as and content_as absent → content_as='', has_assamese=False
  4. notes_as takes priority over content_as when both are set
  5. Serialiser field audit: response includes content_as and has_assamese keys
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ── helpers ───────────────────────────────────────────────────────────────────

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
    return c


def _make_stream(name="Science", class_id="class-1"):
    s = MagicMock()
    s.id = "stream-1"
    s.name = name
    s.class_id = class_id
    return s


def _make_subject(name="Biology", stream_id="stream-1", slug="biology"):
    s = MagicMock()
    s.id = "subject-1"
    s.name = name
    s.slug = slug
    s.stream_id = "stream-1"
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
    """Return a context-manager stack that mocks all four DB calls in _resolve_chapter_by_slug."""
    import contextlib

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


# ── tests ─────────────────────────────────────────────────────────────────────

class TestAssameseReaderTabApiResponse:
    """
    Confirm that _resolve_chapter_by_slug returns notes_as content without
    requiring a full publish pipeline run.
    """

    @pytest.mark.asyncio
    async def test_notes_as_returned_as_content_as_without_publish(self):
        """
        Primary path: notes_as set by seed-assamese run → API returns
        non-empty content_as and has_assamese=True.

        The chapter must NOT have been through the full publish pipeline —
        we verify by intentionally leaving content_en empty so the only
        English path for content_as is notes_as.
        """
        from app.api.v1.public_content import _resolve_chapter_by_slug
        from starlette.responses import Response

        board = _make_board()
        cls = _make_class()
        stream = _make_stream()
        subject = _make_subject()
        chapter = _make_chapter(
            notes_as="অসমীয়া টোকা",   # written by seed-assamese, no publish
            content_as="",
            content_en="",
        )

        response = Response()
        with _patch_hierarchy(board, cls, stream, subject, [chapter]):
            result = await _resolve_chapter_by_slug(
                "ahsec", "class-12", None, "biology", "cell-biology",
                response, use_slug_as=False,
            )

        assert result["content_as"] == "অসমীয়া টোকা", (
            "content_as must equal notes_as written by seed-assamese "
            "even when the chapter was never re-published"
        )
        assert result["has_assamese"] is True, (
            "has_assamese must be True whenever content_as is non-empty"
        )

    @pytest.mark.asyncio
    async def test_legacy_content_as_returned_when_notes_as_absent(self):
        """
        Backward-compat path: notes_as absent, content_as present →
        API still returns non-empty content_as and has_assamese=True.
        """
        from app.api.v1.public_content import _resolve_chapter_by_slug
        from starlette.responses import Response

        board = _make_board()
        cls = _make_class()
        stream = _make_stream()
        subject = _make_subject()
        chapter = _make_chapter(
            notes_as="",
            content_as="Legacy Assamese content",
        )

        response = Response()
        with _patch_hierarchy(board, cls, stream, subject, [chapter]):
            result = await _resolve_chapter_by_slug(
                "ahsec", "class-12", None, "biology", "cell-biology",
                response, use_slug_as=False,
            )

        assert result["content_as"] == "Legacy Assamese content"
        assert result["has_assamese"] is True

    @pytest.mark.asyncio
    async def test_no_assamese_fields_returns_empty(self):
        """
        Both notes_as and content_as absent → content_as='' and
        has_assamese=False so the frontend tab shows the 'no content' state.
        """
        from app.api.v1.public_content import _resolve_chapter_by_slug
        from starlette.responses import Response

        board = _make_board()
        cls = _make_class()
        stream = _make_stream()
        subject = _make_subject()
        chapter = _make_chapter(notes_as="", content_as="")

        response = Response()
        with _patch_hierarchy(board, cls, stream, subject, [chapter]):
            result = await _resolve_chapter_by_slug(
                "ahsec", "class-12", None, "biology", "cell-biology",
                response, use_slug_as=False,
            )

        assert result["content_as"] == ""
        assert result["has_assamese"] is False

    @pytest.mark.asyncio
    async def test_notes_as_takes_priority_over_content_as(self):
        """
        When both notes_as and content_as are set, notes_as wins.
        This is the correct post-seed state: staff may have edited notes_as
        while older content_as may still hold a stale AI-generated value.
        """
        from app.api.v1.public_content import _resolve_chapter_by_slug
        from starlette.responses import Response

        board = _make_board()
        cls = _make_class()
        stream = _make_stream()
        subject = _make_subject()
        chapter = _make_chapter(
            notes_as="নতুন অসমীয়া",          # should win
            content_as="পুৰণি content_as",    # should be ignored
        )

        response = Response()
        with _patch_hierarchy(board, cls, stream, subject, [chapter]):
            result = await _resolve_chapter_by_slug(
                "ahsec", "class-12", None, "biology", "cell-biology",
                response, use_slug_as=False,
            )

        assert result["content_as"] == "নতুন অসমীয়া", (
            "notes_as must take priority over content_as in the API response"
        )
        assert result["has_assamese"] is True

    @pytest.mark.asyncio
    async def test_response_shape_includes_required_assamese_keys(self):
        """
        Serialiser field audit: the response dict must contain both
        content_as and has_assamese so the frontend reader tab can render.
        """
        from app.api.v1.public_content import _resolve_chapter_by_slug
        from starlette.responses import Response

        board = _make_board()
        cls = _make_class()
        stream = _make_stream()
        subject = _make_subject()
        chapter = _make_chapter(notes_as="অসমীয়া")

        response = Response()
        with _patch_hierarchy(board, cls, stream, subject, [chapter]):
            result = await _resolve_chapter_by_slug(
                "ahsec", "class-12", None, "biology", "cell-biology",
                response, use_slug_as=False,
            )

        assert "content_as" in result, (
            "content_as key must be present in the chapter API response"
        )
        assert "has_assamese" in result, (
            "has_assamese key must be present in the chapter API response"
        )
        assert isinstance(result["content_as"], str)
        assert isinstance(result["has_assamese"], bool)
