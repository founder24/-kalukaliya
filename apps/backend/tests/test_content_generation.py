"""Unit tests for ContentGenerationService.generate_notes skip guard.

Verifies that generate_notes() returns early without calling the AI when
force=False and the chapter already has notes_en OR content_en set.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.content_generation import ContentGenerationService


# ── Helpers ───────────────────────────────────────────────────────────────────


# Valid 24-char hex ObjectId for use in all tests
_CHAPTER_ID = "5f43a0d9e4b0b4b4b4b4b4b4"


def _make_chapter(*, notes_en=None, content_en=None):
    """Build a minimal mock Chapter with controllable notes_en / content_en."""
    from app.models.content import Chapter

    chapter = MagicMock(spec=Chapter)
    chapter.id = _CHAPTER_ID
    chapter.title = "Test Chapter"
    chapter.notes_en = notes_en
    chapter.content_en = content_en
    chapter.published_topics = []
    chapter.faq_jsonld = None
    chapter.notes_generated = False
    chapter.status = "pending"
    return chapter


# ── Tests: skip guard when notes_en is already set ───────────────────────────


@pytest.mark.anyio
async def test_generate_notes_skips_when_notes_en_present():
    """generate_notes(force=False) must return early when notes_en is non-empty.

    The Workers AI client must never be called in this case.
    """
    chapter = _make_chapter(notes_en="Existing English notes content")
    service = ContentGenerationService()

    with (
        patch(
            "app.services.content_generation.Chapter.get",
            new_callable=AsyncMock,
            return_value=chapter,
        ),
        patch(
            "app.services.content_generation.workers_ai_client.generate",
            new_callable=AsyncMock,
        ) as mock_generate,
    ):
        result = await service.generate_notes(_CHAPTER_ID, force=False)

    # Should return the chapter unchanged without calling the AI
    assert result is chapter
    mock_generate.assert_not_called()


@pytest.mark.anyio
async def test_generate_notes_skips_when_content_en_present():
    """generate_notes(force=False) must return early when content_en is non-empty.

    Legacy behaviour: content_en alone is sufficient to trigger the skip.
    """
    chapter = _make_chapter(content_en="Legacy English content already present")
    service = ContentGenerationService()

    with (
        patch(
            "app.services.content_generation.Chapter.get",
            new_callable=AsyncMock,
            return_value=chapter,
        ),
        patch(
            "app.services.content_generation.workers_ai_client.generate",
            new_callable=AsyncMock,
        ) as mock_generate,
    ):
        result = await service.generate_notes(_CHAPTER_ID, force=False)

    assert result is chapter
    mock_generate.assert_not_called()


@pytest.mark.anyio
async def test_generate_notes_skips_when_both_fields_present():
    """generate_notes(force=False) skips when both notes_en and content_en are set."""
    chapter = _make_chapter(
        notes_en="Structured notes content",
        content_en="Legacy content field",
    )
    service = ContentGenerationService()

    with (
        patch(
            "app.services.content_generation.Chapter.get",
            new_callable=AsyncMock,
            return_value=chapter,
        ),
        patch(
            "app.services.content_generation.workers_ai_client.generate",
            new_callable=AsyncMock,
        ) as mock_generate,
    ):
        result = await service.generate_notes(_CHAPTER_ID, force=False)

    assert result is chapter
    mock_generate.assert_not_called()


@pytest.mark.anyio
async def test_generate_notes_does_not_skip_when_notes_en_whitespace_only():
    """A whitespace-only notes_en must NOT trigger the skip guard."""
    chapter = _make_chapter(notes_en="   \n  ")
    chapter.content_en = None
    chapter.content_as = None
    chapter.notes_as = None
    chapter.meta_description = None
    chapter.keywords = None
    chapter.faq_jsonld = []
    chapter.word_count = 0
    chapter.notes_generated = False
    chapter.status = "pending"
    chapter.updated_at = None
    chapter.save = AsyncMock()

    service = ContentGenerationService()

    with (
        patch(
            "app.services.content_generation.Chapter.get",
            new_callable=AsyncMock,
            return_value=chapter,
        ),
        patch(
            "app.services.content_generation.workers_ai_client.generate",
            new_callable=AsyncMock,
            return_value="Generated content from AI",
        ) as mock_generate,
        patch.object(
            service, "_auto_publish", new_callable=AsyncMock, return_value={}
        ),
    ):
        await service.generate_notes(_CHAPTER_ID, force=False)

    # The AI must have been called because whitespace-only notes_en is not "present"
    mock_generate.assert_called()


@pytest.mark.anyio
async def test_generate_notes_force_true_overwrites_existing_notes_en():
    """generate_notes(force=True) must call the AI even when notes_en is set."""
    chapter = _make_chapter(notes_en="Old notes that should be overwritten")
    chapter.content_as = None
    chapter.notes_as = None
    chapter.meta_description = None
    chapter.keywords = None
    chapter.faq_jsonld = []
    chapter.word_count = 0
    chapter.notes_generated = False
    chapter.status = "pending"
    chapter.updated_at = None
    chapter.save = AsyncMock()

    service = ContentGenerationService()

    with (
        patch(
            "app.services.content_generation.Chapter.get",
            new_callable=AsyncMock,
            return_value=chapter,
        ),
        patch(
            "app.services.content_generation.workers_ai_client.generate",
            new_callable=AsyncMock,
            return_value="Fresh AI-generated content",
        ) as mock_generate,
        patch.object(
            service, "_auto_publish", new_callable=AsyncMock, return_value={}
        ),
    ):
        await service.generate_notes(_CHAPTER_ID, force=True)

    # With force=True the AI must have been called despite notes_en being present
    mock_generate.assert_called()
