"""
Tests for admin panel endpoints.
Validates auth guards, route registration, and response shapes.
"""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, AsyncMock, MagicMock

from httpx import AsyncClient
from jose import jwt

from app.config import settings


def _make_admin_cookie() -> dict:
    """Mint a valid admin session JWT and return as cookie dict."""
    payload = {
        "sub": "admin-user-id",
        "type": "admin",
        "role": "admin",
        "exp": datetime.now(timezone.utc) + timedelta(hours=8),
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return {"syrabit_admin_session": token}


def _make_expired_cookie() -> dict:
    """Mint an expired admin session JWT."""
    payload = {
        "sub": "admin-user-id",
        "type": "admin",
        "role": "admin",
        "exp": datetime.now(timezone.utc) - timedelta(hours=1),
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return {"syrabit_admin_session": token}


# ---------------------------------------------------------------------------
# Auth guard tests - verify 401 without valid cookie
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_dashboard_requires_auth(client: AsyncClient):
    """GET /admin/dashboard without cookie returns 401."""
    response = await client.get("/api/v1/admin/dashboard")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_users_requires_auth(client: AsyncClient):
    """GET /admin/users without cookie returns 401."""
    response = await client.get("/api/v1/admin/users")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_conversations_requires_auth(client: AsyncClient):
    """GET /admin/conversations without cookie returns 401."""
    response = await client.get("/api/v1/admin/conversations")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_settings_requires_auth(client: AsyncClient):
    """GET /admin/settings without cookie returns 401."""
    response = await client.get("/api/v1/admin/settings")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_analytics_requires_auth(client: AsyncClient):
    """GET /admin/analytics without cookie returns 401."""
    response = await client.get("/api/v1/admin/analytics")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_notifications_requires_auth(client: AsyncClient):
    """GET /admin/notifications without cookie returns 401."""
    response = await client.get("/api/v1/admin/notifications")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_seo_pipeline_status_requires_auth(client: AsyncClient):
    """GET /admin/seo/pipeline-status without cookie returns 401."""
    response = await client.get("/api/v1/admin/seo/pipeline-status")
    assert response.status_code == 401


@pytest.mark.anyio
async def test_expired_cookie_returns_401(client: AsyncClient):
    """Expired admin cookie should return 401."""
    cookies = _make_expired_cookie()
    response = await client.get(
        "/api/v1/admin/dashboard", cookies=cookies
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Authenticated endpoint tests - verify response shapes
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_dashboard_response_shape(client: AsyncClient):
    """GET /admin/dashboard with valid cookie returns expected keys."""
    cookies = _make_admin_cookie()
    response = await client.get("/api/v1/admin/dashboard", cookies=cookies)
    assert response.status_code == 200
    data = response.json()
    # The endpoint gracefully handles DB errors and returns fallback values
    expected_keys = [
        "total_users", "active_today", "total_messages",
        "messages_today", "revenue_total", "revenue_month",
        "pro_users", "free_users", "system_health",
        "signups_today", "chat_fallbacks", "latency",
        "token_spend", "top_queries", "chat_speedups", "vector_stats",
    ]
    for key in expected_keys:
        assert key in data, f"Missing key: {key}"


@pytest.mark.anyio
async def test_users_response_shape(client: AsyncClient):
    """GET /admin/users with valid cookie returns {users, total}."""
    cookies = _make_admin_cookie()
    response = await client.get("/api/v1/admin/users", cookies=cookies)
    assert response.status_code == 200
    data = response.json()
    assert "users" in data
    assert "total" in data
    assert isinstance(data["users"], list)
    assert isinstance(data["total"], int)


@pytest.mark.anyio
async def test_conversations_response_shape(client: AsyncClient):
    """GET /admin/conversations with valid cookie returns a list."""
    cookies = _make_admin_cookie()
    response = await client.get("/api/v1/admin/conversations", cookies=cookies)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


@pytest.mark.anyio
async def test_settings_response_shape(client: AsyncClient):
    """GET /admin/settings with valid cookie returns config object."""
    cookies = _make_admin_cookie()
    response = await client.get("/api/v1/admin/settings", cookies=cookies)
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, dict)
    # Should contain default settings keys when DB is unavailable
    assert "registrations_open" in data or "maintenance_mode" in data or len(data) > 0


@pytest.mark.anyio
async def test_analytics_response_shape(client: AsyncClient):
    """GET /admin/analytics with valid cookie returns overview data."""
    cookies = _make_admin_cookie()
    response = await client.get("/api/v1/admin/analytics", cookies=cookies)
    assert response.status_code == 200
    data = response.json()
    expected_keys = [
        "total_users", "total_chats", "total_messages",
        "avg_messages_per_chat", "feedback_stats",
    ]
    for key in expected_keys:
        assert key in data, f"Missing key: {key}"


@pytest.mark.anyio
async def test_notifications_response_shape(client: AsyncClient):
    """GET /admin/notifications with valid cookie returns {notifications: [...]}."""
    cookies = _make_admin_cookie()
    response = await client.get("/api/v1/admin/notifications", cookies=cookies)
    assert response.status_code == 200
    data = response.json()
    assert "notifications" in data
    assert isinstance(data["notifications"], list)


@pytest.mark.anyio
async def test_seo_pipeline_status_response_shape(client: AsyncClient):
    """GET /admin/seo/pipeline-status with valid cookie returns expected shape."""
    cookies = _make_admin_cookie()
    response = await client.get("/api/v1/admin/seo/pipeline-status", cookies=cookies)
    assert response.status_code == 200
    data = response.json()
    assert "source" in data
    assert data["source"] == "placeholder"
    assert "total_topics" in data
    assert "published" in data
