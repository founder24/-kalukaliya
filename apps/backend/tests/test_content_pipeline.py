"""
Tests for the ContentPipeline orchestration.
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from app.services.content.pipeline import ContentPipeline
from app.models.knowledge import KnowledgeObject, ContentBlock, ContentMetadata


@pytest.fixture(autouse=True)
def mock_beanie_collection():
    """Patch Beanie's motor collection to avoid CollectionWasNotInitialized."""
    with patch.object(
        KnowledgeObject, "get_motor_collection", return_value=MagicMock()
    ):
        yield


@pytest.fixture(autouse=True)
def mock_beanie_save():
    """Patch KnowledgeObject.save to avoid DB calls during testing."""
    with patch.object(KnowledgeObject, "save", new_callable=AsyncMock):
        yield


@pytest.fixture
def sample_ko():
    ko = KnowledgeObject(
        slug="test-slug",
        board="ahsec",
        class_level="hs-1st-year",
        subject="physics",
        chapter="laws-of-motion",
        topic="Laws of Motion",
        content=ContentBlock(
            body_markdown="Test content about physics. " * 50,
            key_concepts=["Concept A", "Concept B"],
            prev_year_questions=[
                {"year": "2023", "question": "Q1", "answer": "A1", "marks": 2}
            ],
        ),
        metadata=ContentMetadata(),
    )
    return ko


@pytest.mark.asyncio
class TestContentPipeline:
    @patch("app.services.content.pipeline.KnowledgeObject")
    async def test_publish_not_found(self, mock_model):
        mock_model.find_one = AsyncMock(return_value=None)
        pipeline = ContentPipeline()
        result = await pipeline.publish("nonexistent-slug")
        assert result["steps"]["fetch"] == "not_found"

    @patch("app.services.content.pipeline.KnowledgeObject")
    async def test_publish_generates_mcqs(self, mock_model, sample_ko):
        mock_model.find_one = AsyncMock(return_value=sample_ko)

        pipeline = ContentPipeline()
        pipeline.indexer.client = None  # Skip search indexing

        result = await pipeline.publish("test-slug")
        assert result["steps"]["fetch"] == "ok"
        assert result["steps"]["generate"] == "ok"
        assert len(sample_ko.generated.mcqs) > 0

    @patch("app.services.content.pipeline.KnowledgeObject")
    async def test_publish_renders_html(self, mock_model, sample_ko):
        mock_model.find_one = AsyncMock(return_value=sample_ko)

        pipeline = ContentPipeline()
        pipeline.indexer.client = None

        result = await pipeline.publish("test-slug")
        assert result["steps"]["render"] == "ok"

    @patch("app.services.content.pipeline.KnowledgeObject")
    async def test_publish_skips_indexnow_without_key(self, mock_model, sample_ko):
        mock_model.find_one = AsyncMock(return_value=sample_ko)

        pipeline = ContentPipeline()
        pipeline.indexer.client = None

        result = await pipeline.publish("test-slug")
        assert result["steps"]["indexnow"] == "skipped"

    @patch("app.services.content.pipeline.KnowledgeObject")
    async def test_publish_skips_cloudflare_without_creds(
        self, mock_model, sample_ko
    ):
        mock_model.find_one = AsyncMock(return_value=sample_ko)

        pipeline = ContentPipeline()
        pipeline.indexer.client = None

        result = await pipeline.publish("test-slug")
        assert result["steps"]["cloudflare_kv"] == "skipped"
