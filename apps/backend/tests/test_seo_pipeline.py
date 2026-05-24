"""Tests for SEO publish pipeline and public endpoints."""

import pytest
import pytest_asyncio
from unittest.mock import patch, AsyncMock, MagicMock
from httpx import AsyncClient, ASGITransport
from beanie import PydanticObjectId
from datetime import datetime, timezone

from app.models.content import Chapter, Topic
from app.services.content_publisher import _chunk_content, ContentPublisherService


FAKE_CHAPTER_ID = PydanticObjectId()
FAKE_SUBJECT_ID = PydanticObjectId()

ADMIN_PAYLOAD = {"sub": "admin-id", "type": "admin", "role": "admin"}


@pytest_asyncio.fixture
async def client():
    """Create async test client."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _make_mock_chapter(chapter_id=None, content_en=None, status="draft",
                       published_topics=None, faq_jsonld=None):
    """Create a mock Chapter for testing."""
    mock = MagicMock(spec=Chapter)
    mock.id = chapter_id or PydanticObjectId()
    mock.title = "Cell Biology"
    mock.slug = "cell-biology"
    mock.subject_id = FAKE_SUBJECT_ID
    mock.chapter_number = 1
    mock.status = status
    mock.content_en = content_en
    mock.content_as = None
    mock.meta_description = "Cell biology notes"
    mock.keywords = "cell, biology"
    mock.word_count = 1500 if content_en else None
    mock.published_topics = published_topics or []
    mock.faq_jsonld = faq_jsonld
    mock.created_at = datetime.now(timezone.utc)
    mock.updated_at = datetime.now(timezone.utc)
    mock.save = AsyncMock()
    mock.insert = AsyncMock()
    mock.delete = AsyncMock()
    return mock


class TestChunkContent:
    """Test the _chunk_content helper function."""

    def test_empty_content(self):
        assert _chunk_content("") == []

    def test_none_content(self):
        assert _chunk_content(None) == []

    def test_short_content_single_chunk(self):
        content = "This is a short paragraph."
        chunks = _chunk_content(content)
        assert len(chunks) == 1
        assert chunks[0] == content

    def test_long_content_multiple_chunks(self):
        # Create content with multiple paragraphs that exceed 512 tokens (~2048 chars)
        paragraphs = []
        for i in range(20):
            paragraphs.append(f"Paragraph {i}: " + "word " * 50)
        content = "\n\n".join(paragraphs)
        chunks = _chunk_content(content)
        assert len(chunks) > 1

    def test_chunks_preserve_paragraphs(self):
        content = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
        chunks = _chunk_content(content)
        # With short content, should be a single chunk
        assert len(chunks) >= 1
        # All paragraphs should be present across all chunks
        combined = "\n\n".join(chunks)
        assert "First paragraph" in combined
        assert "Second paragraph" in combined
        assert "Third paragraph" in combined


class TestPublishToAzureSearch:
    """Test ContentPublisherService.publish_to_azure_search."""

    @pytest.mark.asyncio
    @patch("app.services.content_publisher.SearchClient")
    @patch("app.services.content_publisher.settings")
    async def test_publish_chunks_content_correctly(self, mock_settings, mock_search_client_cls):
        """Test that content is chunked and uploaded to Azure Search."""
        mock_settings.AZURE_SEARCH_ENDPOINT = "https://test.search.windows.net"
        mock_settings.AZURE_SEARCH_ADMIN_KEY = "test-key"
        mock_settings.AZURE_SEARCH_INDEX_NAME = "test-index"

        # Create a mock search client
        mock_client_instance = MagicMock()
        mock_result_item = MagicMock()
        mock_result_item.succeeded = True
        mock_client_instance.upload_documents.return_value = [mock_result_item] * 3
        mock_client_instance.close = MagicMock()
        mock_search_client_cls.return_value = mock_client_instance

        # Create chapter with enough content to produce multiple chunks
        paragraphs = ["Paragraph content " + "word " * 60 for _ in range(10)]
        content = "\n\n".join(paragraphs)
        mock_chapter = _make_mock_chapter(content_en=content)

        service = ContentPublisherService()
        result = await service.publish_to_azure_search(mock_chapter)

        assert result["status"] == "indexed"
        assert result["chunks_total"] > 0
        assert result["chunks_succeeded"] > 0
        assert result["index_name"] == "test-index"
        mock_client_instance.upload_documents.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.services.content_publisher.settings")
    async def test_publish_raises_without_config(self, mock_settings):
        """Test that publishing raises when Azure Search is not configured."""
        mock_settings.AZURE_SEARCH_ENDPOINT = None
        mock_settings.AZURE_SEARCH_ADMIN_KEY = None

        mock_chapter = _make_mock_chapter(content_en="Some content")

        service = ContentPublisherService()
        with pytest.raises(RuntimeError, match="Azure Search not configured"):
            await service.publish_to_azure_search(mock_chapter)

    @pytest.mark.asyncio
    @patch("app.services.content_publisher.settings")
    async def test_publish_raises_without_content(self, mock_settings):
        """Test that publishing raises when chapter has no content."""
        mock_settings.AZURE_SEARCH_ENDPOINT = "https://test.search.windows.net"
        mock_settings.AZURE_SEARCH_ADMIN_KEY = "test-key"

        mock_chapter = _make_mock_chapter(content_en=None)

        service = ContentPublisherService()
        with pytest.raises(RuntimeError, match="has no English content"):
            await service.publish_to_azure_search(mock_chapter)


class TestPublishChapter:
    """Test the full publish_chapter pipeline."""

    @pytest.mark.asyncio
    @patch("app.services.content_publisher.httpx.AsyncClient")
    @patch("app.services.content_publisher.SearchClient")
    @patch("app.services.content_publisher.settings")
    @patch("app.services.content_publisher.Chapter")
    async def test_publish_chapter_updates_status(
        self, mock_chapter_cls, mock_settings, mock_search_client_cls, mock_httpx_client
    ):
        """Test that publish_chapter sets status to 'published'."""
        mock_settings.AZURE_SEARCH_ENDPOINT = "https://test.search.windows.net"
        mock_settings.AZURE_SEARCH_ADMIN_KEY = "test-key"
        mock_settings.AZURE_SEARCH_INDEX_NAME = "test-index"
        mock_settings.CF_WORKER_URL = "https://edge.test.ai"

        # Mock Azure Search
        mock_client_instance = MagicMock()
        mock_result_item = MagicMock()
        mock_result_item.succeeded = True
        mock_client_instance.upload_documents.return_value = [mock_result_item]
        mock_client_instance.close = MagicMock()
        mock_search_client_cls.return_value = mock_client_instance

        # Mock httpx for Cloudflare
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_httpx_instance = AsyncMock()
        mock_httpx_instance.post = AsyncMock(return_value=mock_response)
        mock_httpx_instance.__aenter__ = AsyncMock(return_value=mock_httpx_instance)
        mock_httpx_instance.__aexit__ = AsyncMock(return_value=None)
        mock_httpx_client.return_value = mock_httpx_instance

        # Mock chapter
        mock_chapter = _make_mock_chapter(
            chapter_id=FAKE_CHAPTER_ID,
            content_en="Test content for publishing.\n\nAnother paragraph.",
            status="generated",
        )
        mock_chapter_cls.get = AsyncMock(return_value=mock_chapter)

        service = ContentPublisherService()
        result = await service.publish_chapter(str(FAKE_CHAPTER_ID))

        assert result["status"] == "published"
        assert mock_chapter.status == "published"
        mock_chapter.save.assert_called_once()


class TestFaqJsonld:
    """Test FAQ JSON-LD generation endpoint."""

    @pytest.mark.asyncio
    @patch("app.api.v1.admin_content._validate_admin_session", return_value=ADMIN_PAYLOAD)
    @patch("app.api.v1.admin_content._csrf_check", new_callable=AsyncMock)
    @patch("app.api.v1.admin_content.Chapter")
    async def test_generate_faq_jsonld(self, mock_chapter_cls, mock_csrf, mock_auth, client):
        """Test FAQ JSON-LD generation from chapter topics."""
        topics = [
            Topic(title="What is DNA?", topic_slug="what-is-dna", definition="DNA is the molecule of heredity."),
            Topic(title="What is RNA?", topic_slug="what-is-rna", definition="RNA is a nucleic acid."),
        ]
        mock_chapter = _make_mock_chapter(
            chapter_id=FAKE_CHAPTER_ID,
            published_topics=topics,
        )
        mock_chapter_cls.get = AsyncMock(return_value=mock_chapter)

        response = await client.post(
            f"/api/v1/admin/content/chapters/{FAKE_CHAPTER_ID}/faq-jsonld",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["entries_count"] == 2
        assert data["faq_jsonld"][0]["@type"] == "Question"
        assert data["faq_jsonld"][0]["name"] == "What is DNA?"
        assert data["faq_jsonld"][0]["acceptedAnswer"]["@type"] == "Answer"
        assert data["faq_jsonld"][0]["acceptedAnswer"]["text"] == "DNA is the molecule of heredity."
        mock_chapter.save.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.api.v1.admin_content._validate_admin_session", return_value=ADMIN_PAYLOAD)
    @patch("app.api.v1.admin_content._csrf_check", new_callable=AsyncMock)
    @patch("app.api.v1.admin_content.Chapter")
    async def test_generate_faq_jsonld_no_topics(self, mock_chapter_cls, mock_csrf, mock_auth, client):
        """Test FAQ JSON-LD generation fails when no topics."""
        mock_chapter = _make_mock_chapter(
            chapter_id=FAKE_CHAPTER_ID,
            published_topics=[],
        )
        mock_chapter_cls.get = AsyncMock(return_value=mock_chapter)

        response = await client.post(
            f"/api/v1/admin/content/chapters/{FAKE_CHAPTER_ID}/faq-jsonld",
        )
        assert response.status_code == 400


class TestPublicEndpoints:
    """Test public (non-admin) endpoints."""

    @pytest.mark.asyncio
    @patch("app.api.v1.public_content.Chapter")
    async def test_get_faq_jsonld_public(self, mock_chapter_cls, client):
        """Test public FAQ JSON-LD endpoint (no auth required)."""
        faq_data = [
            {"@type": "Question", "name": "What is DNA?", "acceptedAnswer": {"@type": "Answer", "text": "DNA is..."}}
        ]
        mock_chapter = _make_mock_chapter(
            chapter_id=FAKE_CHAPTER_ID,
            faq_jsonld=faq_data,
        )
        mock_chapter_cls.get = AsyncMock(return_value=mock_chapter)

        response = await client.get(f"/api/v1/content/chapters/{FAKE_CHAPTER_ID}/faq-jsonld")
        assert response.status_code == 200
        data = response.json()
        assert data["faq_jsonld"] == faq_data

    @pytest.mark.asyncio
    @patch("app.api.v1.public_content.Chapter")
    async def test_get_published_topics_public(self, mock_chapter_cls, client):
        """Test public published topics endpoint (no auth required)."""
        topics = [
            Topic(title="DNA", topic_slug="dna", definition="Genetic material"),
            Topic(title="RNA", topic_slug="rna", definition="Ribonucleic acid"),
        ]
        mock_chapter = _make_mock_chapter(
            chapter_id=FAKE_CHAPTER_ID,
            published_topics=topics,
        )
        mock_chapter_cls.get = AsyncMock(return_value=mock_chapter)

        response = await client.get(f"/api/v1/content/chapters/{FAKE_CHAPTER_ID}/published-topics")
        assert response.status_code == 200
        data = response.json()
        assert len(data["topics"]) == 2
        assert data["topics"][0]["title"] == "DNA"
        assert data["topics"][1]["title"] == "RNA"

    @pytest.mark.asyncio
    @patch("app.api.v1.public_content.Chapter")
    async def test_get_faq_jsonld_not_found(self, mock_chapter_cls, client):
        """Test public FAQ endpoint returns 404 for missing chapter."""
        mock_chapter_cls.get = AsyncMock(return_value=None)
        fake_id = str(PydanticObjectId())

        response = await client.get(f"/api/v1/content/chapters/{fake_id}/faq-jsonld")
        assert response.status_code == 404

    @pytest.mark.asyncio
    @patch("app.api.v1.public_content.Chapter")
    async def test_get_published_topics_not_found(self, mock_chapter_cls, client):
        """Test public topics endpoint returns 404 for missing chapter."""
        mock_chapter_cls.get = AsyncMock(return_value=None)
        fake_id = str(PydanticObjectId())

        response = await client.get(f"/api/v1/content/chapters/{fake_id}/published-topics")
        assert response.status_code == 404


class TestSEOAdminEndpoints:
    """Test SEO admin endpoints (coverage, pipeline-status)."""

    @pytest.mark.asyncio
    @patch("app.api.v1.admin_seo._validate_admin_session", return_value=ADMIN_PAYLOAD)
    @patch("app.api.v1.admin_seo.Subject")
    @patch("app.api.v1.admin_seo.Chapter")
    async def test_coverage_endpoint(self, mock_chapter_cls, mock_subject_cls, mock_auth, client):
        """Test SEO coverage endpoint returns correct structure."""
        mock_chapter = MagicMock()
        mock_chapter.subject_id = FAKE_SUBJECT_ID
        mock_chapter.status = "published"

        mock_find_all_ch = MagicMock()
        mock_find_all_ch.to_list = AsyncMock(return_value=[mock_chapter])
        mock_chapter_cls.find_all.return_value = mock_find_all_ch

        mock_subject = MagicMock()
        mock_subject.id = FAKE_SUBJECT_ID
        mock_subject.name = "Biology"

        mock_find_all_sub = MagicMock()
        mock_find_all_sub.to_list = AsyncMock(return_value=[mock_subject])
        mock_subject_cls.find_all.return_value = mock_find_all_sub

        response = await client.get("/api/v1/admin/seo/coverage")
        assert response.status_code == 200
        data = response.json()
        assert "coverage" in data
        assert "totals" in data
        assert data["totals"]["published"] == 1

    @pytest.mark.asyncio
    @patch("app.api.v1.admin_seo._validate_admin_session", return_value=ADMIN_PAYLOAD)
    @patch("app.api.v1.admin_seo.Subject")
    @patch("app.api.v1.admin_seo.Chapter")
    async def test_pipeline_status_endpoint(self, mock_chapter_cls, mock_subject_cls, mock_auth, client):
        """Test SEO pipeline-status endpoint returns correct structure."""
        mock_chapter = MagicMock()
        mock_chapter.subject_id = FAKE_SUBJECT_ID
        mock_chapter.status = "generated"

        mock_find_all_ch = MagicMock()
        mock_find_all_ch.to_list = AsyncMock(return_value=[mock_chapter])
        mock_chapter_cls.find_all.return_value = mock_find_all_ch

        mock_subject = MagicMock()
        mock_subject.id = FAKE_SUBJECT_ID
        mock_subject.name = "Physics"

        mock_find_all_sub = MagicMock()
        mock_find_all_sub.to_list = AsyncMock(return_value=[mock_subject])
        mock_subject_cls.find_all.return_value = mock_find_all_sub

        response = await client.get("/api/v1/admin/seo/pipeline-status")
        assert response.status_code == 200
        data = response.json()
        assert "pipelines" in data
        assert "total_chapters" in data
        assert data["total_chapters"] == 1
        assert data["status"] == "active"
