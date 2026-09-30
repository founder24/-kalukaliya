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
async def test_retired_subscription_create_order_is_not_mounted(client: AsyncClient):
    """Retired Python subscription endpoints return 404, regardless of config."""
    response = await client.post("/api/v1/subscription/create-order")
    assert response.status_code == 404


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
async def test_auth_rate_limit_fails_closed_when_mongo_unavailable():
    """Login fails closed when the MongoDB rate-limit backend is down.

    The rate limiter moved off Redis to MongoDB (auth_rate_limit collection).
    It intentionally returns 503 rather than allowing authentication to proceed
    without an atomic limiter.
    """
    from app.main import app
    from httpx import AsyncClient, ASGITransport

    with (
        patch(
            "app.db.mongo.get_mongo_client",
            side_effect=RuntimeError("MongoDB not initialized"),
        ),
        patch(
            "app.models.user.User.find_one",
            new_callable=AsyncMock,
            return_value=None,
        ),
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
            assert "MongoDB not initialized" not in response.json()["detail"]


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
