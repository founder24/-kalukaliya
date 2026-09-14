"""Credential-free regression tests for Assamese translation boundaries."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.ai.note_quality import ModelPreambleError
from app.services.content.chapter_translator import ChapterTranslator
from app.services.content_generation import ContentGenerationService
from scripts.retranslate_assamese import _translate_chunk


_CHAPTER_ID = "507f1f77bcf86cd799439011"
_TRANSLATION_PREAMBLE = "Here is the Assamese translation:\n\nঅনুবাদিত পাঠ."


def _chapter(*, content_en="English chapter content"):
    chapter = MagicMock()
    chapter.id = _CHAPTER_ID
    chapter.title = "Test Chapter"
    chapter.slug = "test-chapter"
    chapter.notes_en = content_en
    chapter.content_en = content_en
    chapter.notes_as = ""
    chapter.content_as = ""
    chapter.save = AsyncMock()
    chapter.update = AsyncMock()
    return chapter


@pytest.mark.anyio
async def test_content_generation_rejects_translation_chunk_before_save():
    chapter = _chapter()
    service = ContentGenerationService()
    service._gcs_update = AsyncMock()

    with (
        patch(
            "app.services.content_generation.Chapter.get",
            new_callable=AsyncMock,
            return_value=chapter,
        ),
        patch(
            "app.services.content_generation.workers_ai_client.generate",
            new_callable=AsyncMock,
            return_value=_TRANSLATION_PREAMBLE,
        ) as mock_generate,
    ):
        with pytest.raises(ModelPreambleError) as exc_info:
            await service.generate_assamese_only(_CHAPTER_ID, force=True)

    message = str(exc_info.value)
    assert _CHAPTER_ID in message
    assert "translation chunk 1/1" in message
    mock_generate.assert_awaited_once()
    chapter.save.assert_not_awaited()
    service._gcs_update.assert_not_awaited()


@pytest.mark.anyio
async def test_chapter_translator_rejects_content_before_update(caplog):
    chapter = _chapter()
    translator = ChapterTranslator()

    with patch(
        "app.services.content.chapter_translator.workers_ai_client.generate",
        new_callable=AsyncMock,
        side_effect=["অসমীয়া শিৰোনাম", _TRANSLATION_PREAMBLE],
    ) as mock_generate:
        result = await translator.translate_chapter(chapter)

    assert result is False
    assert _CHAPTER_ID in caplog.text
    assert "translation chunk 1/1" in caplog.text
    assert mock_generate.await_count == 2
    chapter.update.assert_not_awaited()


@pytest.mark.anyio
async def test_retranslate_script_rejects_chunk_before_caller_can_append():
    client = MagicMock()
    client.generate = AsyncMock(return_value=_TRANSLATION_PREAMBLE)

    with pytest.raises(ModelPreambleError) as exc_info:
        await _translate_chunk(
            "English source",
            chapter_id=_CHAPTER_ID,
            chunk_index=2,
            total_chunks=3,
            ai_client=client,
        )

    message = str(exc_info.value)
    assert _CHAPTER_ID in message
    assert "translation chunk 2/3" in message
    client.generate.assert_awaited_once()


@pytest.mark.anyio
async def test_retranslate_script_returns_clean_chunk():
    client = MagicMock()
    client.generate = AsyncMock(return_value="অনুবাদিত পাঠ")

    result = await _translate_chunk(
        "English source",
        chapter_id=_CHAPTER_ID,
        chunk_index=1,
        total_chunks=1,
        ai_client=client,
    )

    assert result == "অনুবাদিত পাঠ"