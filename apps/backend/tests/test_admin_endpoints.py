"""
Tests for admin panel backend endpoints.
Validates auth guards (401 without cookie) and response shapes.
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi import HTTPException
from fastapi.testclient import TestClient
import jwt
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
        "/api/v1/admin/users",
        "/api/v1/admin/conversations",
        "/api/v1/admin/content/boards",
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
        mock_db.chats.aggregate = MagicMock(
            return_value=AsyncMock(to_list=AsyncMock(return_value=[{"total": 100}]))
        )

        response = client.get("/api/v1/admin/dashboard", cookies=admin_cookie)
        assert response.status_code == 200
        data = response.json()
        expected_keys = [
            "total_users",
            "active_today",
            "total_messages",
            "messages_today",
            "pro_users",
            "free_users",
            "system_health",
            "signups_today",
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
        assert "cf_workers_ai" in data

    def test_seo_entity_status_placeholder(self, client, admin_cookie):
        """SEO entity status returns entity health data."""
        response = client.get("/api/v1/admin/seo/entity/status", cookies=admin_cookie)
        assert response.status_code == 200
        data = response.json()
        assert "entities_total" in data
        assert "health_score" in data

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


def test_admin_login_stops_when_limiter_is_unavailable(client):
    limiter_error = HTTPException(
        status_code=503,
        detail="Authentication temporarily unavailable. Please try again shortly.",
    )
    with (
        patch(
            "app.api.v1.admin._check_rate_limit",
            new_callable=AsyncMock,
            side_effect=limiter_error,
        ) as rate_limiter,
        patch(
            "app.api.v1.admin.User.find_one", new_callable=AsyncMock
        ) as user_lookup,
    ):
        response = client.post(
            "/api/v1/admin/login",
            json={"email": "admin@example.com", "password": "StrongPassword123!"},
        )

    assert response.status_code == 503
    assert response.json()["detail"] == limiter_error.detail
    rate_limiter.assert_awaited_once()
    user_lookup.assert_not_awaited()
    assert "set-cookie" not in response.headers


def test_admin_login_preserves_rate_limit_429(client):
    limiter_error = HTTPException(status_code=429, detail="Too many attempts")
    with (
        patch(
            "app.api.v1.admin._check_rate_limit",
            new_callable=AsyncMock,
            side_effect=limiter_error,
        ) as rate_limiter,
        patch(
            "app.api.v1.admin.User.find_one", new_callable=AsyncMock
        ) as user_lookup,
    ):
        response = client.post(
            "/api/v1/admin/login",
            json={"email": "admin@example.com", "password": "StrongPassword123!"},
        )

    assert response.status_code == 429
    assert response.json()["detail"] == "Too many attempts"
    rate_limiter.assert_awaited_once()
    user_lookup.assert_not_awaited()


def test_admin_login_maps_unexpected_limiter_error_to_generic_503(client):
    with (
        patch(
            "app.api.v1.admin._check_rate_limit",
            new_callable=AsyncMock,
            side_effect=RuntimeError("private storage detail"),
        ) as rate_limiter,
        patch(
            "app.api.v1.admin.User.find_one", new_callable=AsyncMock
        ) as user_lookup,
    ):
        response = client.post(
            "/api/v1/admin/login",
            json={"email": "admin@example.com", "password": "StrongPassword123!"},
        )

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "Authentication temporarily unavailable. Please try again shortly."
    )
    assert "private storage detail" not in response.text
    rate_limiter.assert_awaited_once()
    user_lookup.assert_not_awaited()


def test_admin_login_sets_session_cookie_after_limiter_allows(client):
    admin = MagicMock()
    admin.hashed_password = "hashed"
    admin.verify_password.return_value = True
    admin.role = "admin"
    admin.name = "Test Admin"
    admin.id = "test-admin-id"

    with (
        patch(
            "app.api.v1.admin._check_rate_limit",
            new_callable=AsyncMock,
        ) as rate_limiter,
        patch(
            "app.api.v1.admin.User.find_one",
            new_callable=AsyncMock,
            return_value=admin,
        ) as user_lookup,
        patch(
            "app.api.v1.admin._get_admin_signing_key",
            return_value=("test-signing-key", "HS256"),
        ),
    ):
        response = client.post(
            "/api/v1/admin/login",
            json={"email": "admin@example.com", "password": "StrongPassword123!"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    set_cookie = response.headers.get("set-cookie", "")
    assert "syrabit_admin_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie
    rate_limiter.assert_awaited_once()
    user_lookup.assert_awaited_once_with({"email": "admin@example.com"})
    admin.verify_password.assert_called_once_with("StrongPassword123!")
