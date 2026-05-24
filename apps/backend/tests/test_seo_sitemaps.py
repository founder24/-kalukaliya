"""
Tests for SEO sitemap endpoints.
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient

from app.models.knowledge import KnowledgeObject, ContentBlock, ContentMetadata


@pytest.fixture(autouse=True)
def mock_beanie_collection():
    """Patch Beanie's motor collection to avoid CollectionWasNotInitialized."""
    with patch.object(
        KnowledgeObject, "get_motor_collection", return_value=MagicMock()
    ):
        yield


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


@pytest.fixture
def mock_ko_list():
    ko1 = KnowledgeObject(
        slug="ahsec-hs1-physics-motion",
        board="ahsec",
        class_level="hs-1st-year",
        subject="physics",
        chapter="motion",
        topic="Motion",
        content=ContentBlock(body_markdown="Content"),
        metadata=ContentMetadata(),
    )
    ko2 = KnowledgeObject(
        slug="ahsec-hs1-physics-force",
        board="ahsec",
        class_level="hs-1st-year",
        subject="physics",
        chapter="force",
        topic="Force",
        content=ContentBlock(body_markdown="Content"),
        metadata=ContentMetadata(),
    )
    return [ko1, ko2]


class TestSitemapEndpoints:
    def test_sitemap_index(self, client):
        response = client.get("/api/v1/seo/sitemap-index.xml")
        assert response.status_code == 200
        assert "application/xml" in response.headers["content-type"]
        assert "sitemapindex" in response.text
        assert "sitemap-subjects.xml" in response.text

    @patch("app.api.v1.seo.KnowledgeObject")
    def test_sitemap_subjects(self, mock_model, client, mock_ko_list):
        mock_query = MagicMock()
        mock_query.project = MagicMock(return_value=mock_query)
        mock_query.to_list = AsyncMock(return_value=mock_ko_list)
        mock_model.find = MagicMock(return_value=mock_query)

        response = client.get("/api/v1/seo/sitemap-subjects.xml")
        assert response.status_code == 200
        assert "application/xml" in response.headers["content-type"]
        assert "urlset" in response.text

    @patch("app.api.v1.seo.KnowledgeObject")
    def test_sitemap_chapters(self, mock_model, client, mock_ko_list):
        mock_query = MagicMock()
        mock_query.project = MagicMock(return_value=mock_query)
        mock_query.to_list = AsyncMock(return_value=mock_ko_list)
        mock_model.find = MagicMock(return_value=mock_query)

        response = client.get("/api/v1/seo/sitemap-chapters.xml")
        assert response.status_code == 200
        assert "application/xml" in response.headers["content-type"]
        # Should contain chapter URLs
        assert "ahsec/hs-1st-year/physics/motion" in response.text

    @patch("app.api.v1.seo.KnowledgeObject")
    def test_sitemap_mcqs(self, mock_model, client, mock_ko_list):
        mock_query = MagicMock()
        mock_query.project = MagicMock(return_value=mock_query)
        mock_query.to_list = AsyncMock(return_value=mock_ko_list)
        mock_model.find = MagicMock(return_value=mock_query)

        response = client.get("/api/v1/seo/sitemap-mcqs.xml")
        assert response.status_code == 200
        assert "/mcqs" in response.text
