"""
Tests for graceful service degradation when dependencies are unavailable.
Validates that endpoints return proper 503/401 responses instead of crashing.
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from httpx import AsyncClient
import jwt
from datetime import datetime, timezone, timedelta

from app.config import settings


@pytest.mark.anyio
async def test_razorpay_503_when_not_configured(client: AsyncClient):
    """Subscription create-order returns 503 when Razorpay keys are missing."""
    from app.main import app
    from app.api.v1.auth import get_current_user

    mock_user = MagicMock()
    mock_user.id = "test-user-id-123"
    mock_user.email = "test@example.com"
    mock_user.is_pro.return_value = False

    async def override_get_current_user():
        return mock_user

    app.dependency_overrides[get_current_user] = override_get_current_user
    try:
        with (
            patch(
                "app.services.payment.razorpay_client.razorpay_client.key_id",
                None,
            ),
            patch(
                "app.services.payment.razorpay_client.razorpay_client.key_secret",
                None,
            ),
        ):
            response = await client.post(
                "/api/v1/subscription/create-order",
                headers={"Authorization": "Bearer fake-token"},
            )
            assert response.status_code == 503
            assert "not configured" in response.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.anyio
async def test_beanie_uninitialized_returns_503(client: AsyncClient):
    """Login returns 503 when Beanie/MongoDB is not initialized."""
    try:
        from beanie.exceptions import CollectionWasNotInitialized
    except ImportError:
        pytest.skip("beanie not installed")

    with patch(
        "app.models.user.User.find_one",
        new_callable=AsyncMock,
        side_effect=CollectionWasNotInitialized,
    ):
        response = await client.post(
            "/api/v1/auth/login",
            json={
                "email": "test@example.com",
                "password": "SomePassword123",
            },
        )
        assert response.status_code == 503
        assert "Database service unavailable" in response.json()["detail"]


@pytest.mark.anyio
async def test_redis_unavailable_returns_503():
    """Login returns 503 when Redis is unavailable (rate limit fail-closed)."""
    from app.main import app
    from httpx import AsyncClient, ASGITransport

    with patch(
        "app.db.redis.get_redis", side_effect=RuntimeError("Redis not initialized")
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.post(
                "/api/v1/auth/login",
                json={
                    "email": "test@example.com",
                    "password": "SomePassword123",
                },
            )
            assert response.status_code == 503
            assert "Rate limiting service unavailable" in response.json()["detail"]


@pytest.mark.anyio
async def test_admin_verify_no_cookie_returns_401(client: AsyncClient):
    """Admin verify returns 401 when no session cookie present."""
    response = await client.get("/api/v1/admin/verify")
    assert response.status_code == 401
    assert "No admin session" in response.json()["detail"]


@pytest.mark.anyio
async def test_admin_verify_valid_cookie_returns_200(client: AsyncClient):
    """Admin verify returns 200 with valid admin JWT cookie."""
    expire = datetime.now(timezone.utc) + timedelta(hours=8)
    payload = {
        "sub": "test_admin_id",
        "type": "admin",
        "role": "admin",
        "exp": expire,
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

    response = await client.get(
        "/api/v1/admin/verify",
        cookies={"syrabit_admin_session": token},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["user_id"] == "test_admin_id"


@pytest.mark.anyio
async def test_admin_verify_non_admin_token_returns_403(client: AsyncClient):
    """Admin verify returns 403 for non-admin role tokens."""
    expire = datetime.now(timezone.utc) + timedelta(hours=8)
    payload = {
        "sub": "test_user_id",
        "type": "admin",
        "role": "user",
        "exp": expire,
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

    response = await client.get(
        "/api/v1/admin/verify",
        cookies={"syrabit_admin_session": token},
    )
    assert response.status_code == 403


@pytest.mark.anyio
async def test_admin_verify_expired_cookie_returns_401(client: AsyncClient):
    """Admin verify returns 401 for expired session cookie."""
    expire = datetime.now(timezone.utc) - timedelta(hours=1)
    payload = {
        "sub": "test_admin_id",
        "type": "admin",
        "role": "admin",
        "exp": expire,
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

    response = await client.get(
        "/api/v1/admin/verify",
        cookies={"syrabit_admin_session": token},
    )
    assert response.status_code == 401
