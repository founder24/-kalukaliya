"""
Tests for public content API endpoints.
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient

from app.models.knowledge import (
    KnowledgeObject,
    ContentBlock,
    ContentMetadata,
    GeneratedContent,
)


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
def mock_ko():
    """Create a mock KnowledgeObject."""
    ko = KnowledgeObject(
        slug="ahsec-hs1-physics-laws-of-motion",
        board="ahsec",
        class_level="hs-1st-year",
        subject="physics",
        chapter="laws-of-motion",
        topic="Laws of Motion",
        content=ContentBlock(body_markdown="Content about laws of motion. " * 50),
        metadata=ContentMetadata(board_name="AHSEC"),
        generated=GeneratedContent(
            mcqs=[
                {
                    "question": "Q1?",
                    "options": ["a", "b", "c", "d"],
                    "correct": "a",
                    "explanation": "E1",
                }
            ],
            summary="Summary text here.",
        ),
    )
    return ko


class TestRenderEndpoints:
    @patch("app.api.v1.content.KnowledgeObject")
    def test_render_chapter_returns_html(self, mock_model, client, mock_ko):
        mock_model.find_one = AsyncMock(return_value=mock_ko)
        response = client.get(
            "/api/v1/content/render/ahsec/hs-1st-year/physics/laws-of-motion"
        )
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "Laws of Motion" in response.text

    @patch("app.api.v1.content.KnowledgeObject")
    def test_render_chapter_has_cache_headers(self, mock_model, client, mock_ko):
        mock_model.find_one = AsyncMock(return_value=mock_ko)
        response = client.get(
            "/api/v1/content/render/ahsec/hs-1st-year/physics/laws-of-motion"
        )
        assert "s-maxage=86400" in response.headers.get("cache-control", "")

    @patch("app.api.v1.content.KnowledgeObject")
    def test_render_chapter_404_when_not_found(self, mock_model, client):
        mock_model.find_one = AsyncMock(return_value=None)
        response = client.get(
            "/api/v1/content/render/ahsec/hs-1st-year/physics/nonexistent"
        )
        assert response.status_code == 404

    @patch("app.api.v1.content.KnowledgeObject")
    def test_render_page_type_mcqs(self, mock_model, client, mock_ko):
        mock_model.find_one = AsyncMock(return_value=mock_ko)
        response = client.get(
            "/api/v1/content/render/ahsec/hs-1st-year/physics/laws-of-motion/mcqs"
        )
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    @patch("app.api.v1.content.KnowledgeObject")
    def test_render_invalid_page_type(self, mock_model, client, mock_ko):
        mock_model.find_one = AsyncMock(return_value=mock_ko)
        response = client.get(
            "/api/v1/content/render/ahsec/hs-1st-year/physics/laws-of-motion/invalid"
        )
        assert response.status_code == 400


class TestContentJsonEndpoint:
    @patch("app.api.v1.content.KnowledgeObject")
    def test_get_content_json(self, mock_model, client, mock_ko):
        mock_model.find_one = AsyncMock(return_value=mock_ko)
        response = client.get("/api/v1/content/ahsec-hs1-physics-laws-of-motion")
        assert response.status_code == 200
        data = response.json()
        assert data["slug"] == "ahsec-hs1-physics-laws-of-motion"

    @patch("app.api.v1.content.KnowledgeObject")
    def test_get_content_json_404(self, mock_model, client):
        mock_model.find_one = AsyncMock(return_value=None)
        response = client.get("/api/v1/content/nonexistent-slug")
        assert response.status_code == 404


class TestSubjectListing:
    @patch("app.api.v1.content.KnowledgeObject")
    def test_list_chapters(self, mock_model, client, mock_ko):
        # Mock the find().to_list() chain
        mock_query = MagicMock()
        mock_query.to_list = AsyncMock(return_value=[mock_ko])
        mock_model.find = MagicMock(return_value=mock_query)

        response = client.get("/api/v1/content/subject/ahsec/hs-1st-year/physics")
        assert response.status_code == 200
        data = response.json()
        assert data["subject"] == "physics"
        assert data["total"] >= 1
        assert "chapters" in data
