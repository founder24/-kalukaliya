"""Tests for AI content generation service with mocked AI clients."""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from beanie import PydanticObjectId
from datetime import datetime, timezone

from app.models.content import Chapter, Topic


FAKE_CHAPTER_ID = str(PydanticObjectId())

SAMPLE_EN_CONTENT = """# Cell Biology

## Introduction

Cell biology is the study of cells, their structure, function, and behavior.

## Topics

### Photosynthesis

Photosynthesis is the process by which plants convert sunlight into energy.
Plants use chlorophyll to absorb light energy and convert carbon dioxide and water
into glucose and oxygen. This process is essential for life on Earth.

### Respiration

Cellular respiration is the process of breaking down glucose to produce ATP.
It occurs in the mitochondria of cells and involves glycolysis, the Krebs cycle,
and the electron transport chain.

## Conclusion

Understanding cell biology is fundamental to understanding life itself.
"""

SAMPLE_AS_CONTENT = """# কোষ জীৱবিজ্ঞান

## পৰিচয়

কোষ জীৱবিজ্ঞান হৈছে কোষৰ গঠন, কাৰ্য আৰু আচৰণৰ অধ্যয়ন।

## বিষয়সমূহ

### সালোকসংশ্লেষণ

সালোকসংশ্লেষণ হৈছে উদ্ভিদে সূৰ্যৰ পোহৰক শক্তিলৈ ৰূপান্তৰ কৰা প্ৰক্ৰিয়া।
"""


def _make_chapter_with_topics(chapter_id=None, content_en=None):
    """Create a mock Chapter with topics."""
    mock = MagicMock(spec=Chapter)
    mock.id = PydanticObjectId(chapter_id) if chapter_id else PydanticObjectId()
    mock.title = "Cell Biology"
    mock.slug = "cell-biology"
    mock.subject_id = PydanticObjectId()
    mock.chapter_number = 1
    mock.status = "draft"
    mock.content_en = content_en
    mock.content_as = None
    mock.meta_description = None
    mock.keywords = None
    mock.word_count = None
    mock.published_topics = [
        Topic(title="Photosynthesis", topic_slug="photosynthesis", definition="Process of making food"),
        Topic(title="Respiration", topic_slug="respiration", definition="Process of breaking down glucose"),
    ]
    mock.created_at = datetime.now(timezone.utc)
    mock.updated_at = datetime.now(timezone.utc)
    mock.save = AsyncMock()
    return mock


class TestGenerateNotes:
    """Test ContentGenerationService.generate_notes method."""

    @pytest.mark.asyncio
    @patch("app.services.content_generation.Chapter")
    @patch("app.services.content_generation.sarvam_client")
    @patch("app.services.content_generation.vertex_client")
    async def test_generate_notes_success(self, mock_vertex, mock_sarvam, mock_chapter_cls):
        """Test successful generation of English and Assamese content."""
        from app.services.content_generation import ContentGenerationService

        # Setup mocks
        mock_chapter = _make_chapter_with_topics(chapter_id=FAKE_CHAPTER_ID)
        mock_chapter_cls.get = AsyncMock(return_value=mock_chapter)
        mock_vertex.generate = AsyncMock(return_value=SAMPLE_EN_CONTENT)
        mock_sarvam.generate = AsyncMock(return_value=SAMPLE_AS_CONTENT)

        service = ContentGenerationService()
        result = await service.generate_notes(FAKE_CHAPTER_ID)

        # Verify result shape
        assert result["status"] == "generated"
        assert result["word_count"] > 0
        assert "meta_description" in result
        assert "keywords" in result
        assert "content_en_preview" in result
        assert "content_as_preview" in result

        # Verify AI clients were called
        mock_vertex.generate.assert_called_once()
        mock_sarvam.generate.assert_called_once()

        # Verify chapter was saved
        mock_chapter.save.assert_called_once()
        assert mock_chapter.status == "generated"
        assert mock_chapter.content_en == SAMPLE_EN_CONTENT
        assert mock_chapter.content_as == SAMPLE_AS_CONTENT

    @pytest.mark.asyncio
    @patch("app.services.content_generation.Chapter")
    @patch("app.services.content_generation.sarvam_client")
    @patch("app.services.content_generation.vertex_client")
    async def test_generate_notes_sets_metadata(self, mock_vertex, mock_sarvam, mock_chapter_cls):
        """Test that metadata is correctly extracted."""
        from app.services.content_generation import ContentGenerationService

        mock_chapter = _make_chapter_with_topics(chapter_id=FAKE_CHAPTER_ID)
        mock_chapter_cls.get = AsyncMock(return_value=mock_chapter)
        mock_vertex.generate = AsyncMock(return_value=SAMPLE_EN_CONTENT)
        mock_sarvam.generate = AsyncMock(return_value=SAMPLE_AS_CONTENT)

        service = ContentGenerationService()
        result = await service.generate_notes(FAKE_CHAPTER_ID)

        # meta_description should be first 160 chars of content
        assert len(mock_chapter.meta_description) <= 160
        # keywords should contain topic titles
        assert "Photosynthesis" in mock_chapter.keywords
        assert "Respiration" in mock_chapter.keywords
        # word_count should be set
        assert mock_chapter.word_count > 0

    @pytest.mark.asyncio
    @patch("app.services.content_generation.Chapter")
    async def test_generate_notes_chapter_not_found(self, mock_chapter_cls):
        """Test error when chapter does not exist."""
        from app.services.content_generation import ContentGenerationService

        mock_chapter_cls.get = AsyncMock(return_value=None)

        service = ContentGenerationService()
        with pytest.raises(RuntimeError, match="Chapter not found"):
            await service.generate_notes(FAKE_CHAPTER_ID)

    @pytest.mark.asyncio
    @patch("app.services.content_generation.Chapter")
    @patch("app.services.content_generation.vertex_client")
    async def test_generate_notes_vertex_error(self, mock_vertex, mock_chapter_cls):
        """Test error handling when Vertex AI raises RuntimeError."""
        from app.services.content_generation import ContentGenerationService

        mock_chapter = _make_chapter_with_topics(chapter_id=FAKE_CHAPTER_ID)
        mock_chapter_cls.get = AsyncMock(return_value=mock_chapter)
        mock_vertex.generate = AsyncMock(side_effect=RuntimeError("Vertex AI not configured"))

        service = ContentGenerationService()
        with pytest.raises(RuntimeError, match="Vertex AI not configured"):
            await service.generate_notes(FAKE_CHAPTER_ID)


class TestGenerateAssameseOnly:
    """Test ContentGenerationService.generate_assamese_only method."""

    @pytest.mark.asyncio
    @patch("app.services.content_generation.Chapter")
    @patch("app.services.content_generation.sarvam_client")
    async def test_generate_assamese_only_success(self, mock_sarvam, mock_chapter_cls):
        """Test successful Assamese-only translation."""
        from app.services.content_generation import ContentGenerationService

        mock_chapter = _make_chapter_with_topics(
            chapter_id=FAKE_CHAPTER_ID,
            content_en=SAMPLE_EN_CONTENT,
        )
        mock_chapter_cls.get = AsyncMock(return_value=mock_chapter)
        mock_sarvam.generate = AsyncMock(return_value=SAMPLE_AS_CONTENT)

        service = ContentGenerationService()
        result = await service.generate_assamese_only(FAKE_CHAPTER_ID)

        assert result["status"] == "translated"
        assert "content_as_preview" in result
        mock_sarvam.generate.assert_called_once()
        mock_chapter.save.assert_called_once()
        assert mock_chapter.content_as == SAMPLE_AS_CONTENT

    @pytest.mark.asyncio
    @patch("app.services.content_generation.Chapter")
    async def test_generate_assamese_only_no_english_content(self, mock_chapter_cls):
        """Test error when chapter has no English content."""
        from app.services.content_generation import ContentGenerationService

        mock_chapter = _make_chapter_with_topics(chapter_id=FAKE_CHAPTER_ID)
        mock_chapter.content_en = None
        mock_chapter_cls.get = AsyncMock(return_value=mock_chapter)

        service = ContentGenerationService()
        with pytest.raises(RuntimeError, match="has no English content"):
            await service.generate_assamese_only(FAKE_CHAPTER_ID)

    @pytest.mark.asyncio
    @patch("app.services.content_generation.Chapter")
    @patch("app.services.content_generation.sarvam_client")
    async def test_generate_assamese_sarvam_error(self, mock_sarvam, mock_chapter_cls):
        """Test error handling when Sarvam AI raises RuntimeError."""
        from app.services.content_generation import ContentGenerationService

        mock_chapter = _make_chapter_with_topics(
            chapter_id=FAKE_CHAPTER_ID,
            content_en=SAMPLE_EN_CONTENT,
        )
        mock_chapter_cls.get = AsyncMock(return_value=mock_chapter)
        mock_sarvam.generate = AsyncMock(side_effect=RuntimeError("Sarvam AI not configured"))

        service = ContentGenerationService()
        with pytest.raises(RuntimeError, match="Sarvam AI not configured"):
            await service.generate_assamese_only(FAKE_CHAPTER_ID)
