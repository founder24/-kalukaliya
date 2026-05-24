"""
Tests for admin panel backend endpoints.
Validates auth guards (401 without cookie) and response shapes.
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient
from jose import jwt
from datetime import datetime, timezone, timedelta

from app.config import settings


@pytest.fixture
def client():
    """Create test client."""
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
    """All admin endpoints should return 401 without a valid session cookie."""

    endpoints_get = [
        "/api/v1/admin/dashboard",
        "/api/v1/admin/health",
        "/api/v1/admin/cf-overview",
        "/api/v1/admin/users",
        "/api/v1/admin/conversations",
        "/api/v1/admin/content/draft-served-subjects",
        "/api/v1/admin/analytics",
        "/api/v1/admin/settings",
        "/api/v1/admin/notifications",
        "/api/v1/admin/seo/entity/status",
        "/api/v1/admin/seo/entity/history",
        "/api/v1/admin/seo/pipeline-status",
        "/api/v1/admin/ai/providers",
        "/api/v1/admin/ai/status",
        "/api/v1/admin/revenue/overview",
        "/api/v1/admin/revenue/subscriptions",
        "/api/v1/admin/alerts/unacknowledged/count",
        "/api/v1/admin/alerts/cooldowns",
    ]

    @pytest.mark.parametrize("endpoint", endpoints_get)
    def test_get_endpoints_require_auth(self, client, endpoint):
        """GET endpoints return 401 without admin session cookie."""
        response = client.get(endpoint)
        assert response.status_code == 401
        assert "No admin session" in response.json()["detail"]

    def test_post_notifications_requires_auth(self, client):
        """POST /notifications returns 401 without admin session cookie."""
        response = client.post(
            "/api/v1/admin/notifications",
            json={"title": "Test", "message": "Test message"},
        )
        assert response.status_code == 401

    def test_put_settings_requires_auth(self, client):
        """PUT /settings returns 401 without admin session cookie."""
        response = client.put(
            "/api/v1/admin/settings",
            json={"maintenance_mode": True},
        )
        assert response.status_code == 401

    def test_patch_user_status_requires_auth(self, client):
        """PATCH /users/{id}/status returns 401 without admin session cookie."""
        response = client.patch(
            "/api/v1/admin/users/507f1f77bcf86cd799439011/status",
            json={"status": "suspended"},
        )
        assert response.status_code == 401


class TestResponseShapes:
    """Test response shapes for key endpoints with valid auth."""

    @patch("app.db.mongo.get_mongo_client")
    def test_dashboard_response_shape(self, mock_mongo, client, admin_cookie):
        """Dashboard returns expected fields."""
        mock_db = MagicMock()
        mock_mongo.return_value = MagicMock(__getitem__=MagicMock(return_value=mock_db))

        # Mock count_documents
        mock_db.users.count_documents = AsyncMock(return_value=10)
        mock_db.chats.aggregate = MagicMock(return_value=AsyncMock(
            to_list=AsyncMock(return_value=[{"total": 100}])
        ))

        response = client.get("/api/v1/admin/dashboard", cookies=admin_cookie)
        assert response.status_code == 200
        data = response.json()
        expected_keys = [
            "total_users", "active_today", "total_messages",
            "messages_today", "pro_users", "free_users",
            "system_health", "signups_today",
        ]
        for key in expected_keys:
            assert key in data

    @patch("app.db.mongo.get_mongo_client")
    def test_users_response_shape(self, mock_mongo, client, admin_cookie):
        """Users endpoint returns paginated response."""
        mock_db = MagicMock()
        mock_mongo.return_value = MagicMock(__getitem__=MagicMock(return_value=mock_db))
        mock_db.users.count_documents = AsyncMock(return_value=0)

        mock_cursor = MagicMock()
        mock_cursor.sort = MagicMock(return_value=mock_cursor)
        mock_cursor.skip = MagicMock(return_value=mock_cursor)
        mock_cursor.limit = MagicMock(return_value=mock_cursor)
        mock_cursor.to_list = AsyncMock(return_value=[])
        mock_db.users.find = MagicMock(return_value=mock_cursor)

        response = client.get("/api/v1/admin/users", cookies=admin_cookie)
        assert response.status_code == 200
        data = response.json()
        assert "users" in data
        assert "total" in data

    def test_ai_providers_response_shape(self, client, admin_cookie):
        """AI providers returns expected shape."""
        response = client.get("/api/v1/admin/ai/providers", cookies=admin_cookie)
        assert response.status_code == 200
        data = response.json()
        assert "providers" in data
        assert isinstance(data["providers"], list)

    def test_ai_status_response_shape(self, client, admin_cookie):
        """AI status returns expected shape."""
        response = client.get("/api/v1/admin/ai/status", cookies=admin_cookie)
        assert response.status_code == 200
        data = response.json()
        assert "overall_status" in data
        assert "vertex_ai" in data
        assert "sarvam_ai" in data

    def test_seo_entity_status_placeholder(self, client, admin_cookie):
        """SEO entity status returns placeholder data."""
        response = client.get("/api/v1/admin/seo/entity/status", cookies=admin_cookie)
        assert response.status_code == 200
        data = response.json()
        assert data["source"] == "placeholder"

    def test_cf_overview_placeholder(self, client, admin_cookie):
        """CF overview returns placeholder data."""
        response = client.get("/api/v1/admin/cf-overview", cookies=admin_cookie)
        assert response.status_code == 200
        data = response.json()
        assert data["source"] == "placeholder"

    @patch("app.db.mongo.get_mongo_client")
    def test_settings_response_shape(self, mock_mongo, client, admin_cookie):
        """Settings endpoint returns expected shape."""
        mock_db = MagicMock()
        mock_mongo.return_value = MagicMock(__getitem__=MagicMock(return_value=mock_db))
        mock_db.site_settings.find_one = AsyncMock(return_value=None)

        response = client.get("/api/v1/admin/settings", cookies=admin_cookie)
        assert response.status_code == 200
        data = response.json()
        assert "maintenance_mode" in data
        assert "registrations_open" in data

    @patch("app.db.mongo.get_mongo_client")
    def test_alerts_count_response(self, mock_mongo, client, admin_cookie):
        """Alerts count endpoint returns count field."""
        mock_db = MagicMock()
        mock_mongo.return_value = MagicMock(__getitem__=MagicMock(return_value=mock_db))
        mock_db.alerts.count_documents = AsyncMock(return_value=5)

        response = client.get(
            "/api/v1/admin/alerts/unacknowledged/count", cookies=admin_cookie
        )
        assert response.status_code == 200
        data = response.json()
        assert "count" in data
