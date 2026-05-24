"""
Tests for admin knowledge endpoints.
Validates auth guards and response shapes.
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient
from jose import jwt
from datetime import datetime, timezone, timedelta

from app.config import settings


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


@pytest.fixture
def admin_cookie():
    """Generate a valid admin session cookie."""
    expire = datetime.now(timezone.utc) + timedelta(hours=8)
    payload = {
        "sub": "test_admin_id",
        "type": "admin",
        "role": "admin",
        "exp": expire,
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return {"syrabit_admin_session": token}


class TestAuthGuards:
    """Admin knowledge endpoints should return 401 without a valid session cookie."""

    endpoints_get = [
        "/api/v1/admin/content/knowledge",
        "/api/v1/admin/content/knowledge/test-slug",
    ]

    @pytest.mark.parametrize("endpoint", endpoints_get)
    def test_get_endpoints_require_auth(self, client, endpoint):
        response = client.get(endpoint)
        assert response.status_code == 401

    def test_post_create_requires_auth(self, client):
        response = client.post(
            "/api/v1/admin/content/knowledge", json={"slug": "test"}
        )
        assert response.status_code == 401

    def test_post_publish_requires_auth(self, client):
        response = client.post(
            "/api/v1/admin/content/knowledge/test-slug/publish"
        )
        assert response.status_code == 401

    def test_post_bulk_publish_requires_auth(self, client):
        response = client.post("/api/v1/admin/content/knowledge/bulk-publish")
        assert response.status_code == 401


class TestAdminKnowledgeEndpoints:
    @patch("app.api.v1.admin_knowledge.KnowledgeObject")
    def test_list_knowledge_empty(self, mock_model, client, admin_cookie):
        # Mock the find() chain: find() -> .count(), .skip() -> .limit() -> .to_list()
        mock_query = MagicMock()
        mock_query.count = AsyncMock(return_value=0)
        mock_query.skip = MagicMock(return_value=mock_query)
        mock_query.limit = MagicMock(return_value=mock_query)
        mock_query.to_list = AsyncMock(return_value=[])
        mock_model.find = MagicMock(return_value=mock_query)

        response = client.get(
            "/api/v1/admin/content/knowledge", cookies=admin_cookie
        )
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data
        assert "page" in data

    @patch("app.api.v1.admin_knowledge.KnowledgeObject")
    def test_get_knowledge_not_found(self, mock_model, client, admin_cookie):
        mock_model.find_one = AsyncMock(return_value=None)
        response = client.get(
            "/api/v1/admin/content/knowledge/nonexistent", cookies=admin_cookie
        )
        assert response.status_code == 404
