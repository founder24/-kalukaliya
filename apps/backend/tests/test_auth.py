from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from httpx import AsyncClient


@pytest.mark.anyio
async def test_signup_weak_password(client: AsyncClient):
    """Test signup rejects weak passwords"""
    response = await client.post(
        "/api/v1/auth/signup",
        json={
            "email": "new@example.com",
            "password": "short",
        },
    )
    assert response.status_code == 422


@pytest.mark.anyio
async def test_login_invalid_credentials(client: AsyncClient):
    """Test login with wrong password returns 401"""
    response = await client.post(
        "/api/v1/auth/login",
        json={
            "email": "nonexistent@example.com",
            "password": "wrongpassword123",
        },
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_refresh_token_must_be_in_body(client: AsyncClient):
    """Test that refresh token as query param is rejected (must be in body)"""
    response = await client.post("/api/v1/auth/refresh?refresh_token=fake_token")
    assert response.status_code == 422


@pytest.mark.anyio
async def test_refresh_token_invalid(client: AsyncClient):
    """Test that invalid refresh token in body returns 401"""
    response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": "invalid_token_value"}
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_protected_endpoint_without_token(client: AsyncClient):
    """Test that protected endpoints reject unauthenticated requests"""
    response = await client.get("/api/v1/users/me")
    assert response.status_code in [401, 403]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/subscription/status"),
        ("POST", "/api/v1/subscription/create-order"),
    ],
)
async def test_retired_subscription_routes_remain_unmounted(
    client: AsyncClient, method: str, path: str
):
    """Legacy Python payment routes stay outside the Cloudflare-native API."""
    response = await client.request(method, path)
    assert response.status_code == 404


@pytest.mark.anyio
async def test_protected_endpoint_with_invalid_token(client: AsyncClient):
    """Test that an invalid JWT token returns 401 with a detail field, not 500"""
    response = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": "Bearer invalid.jwt.token"},
    )
    assert response.status_code == 401
    data = response.json()
    assert "detail" in data


@pytest.mark.anyio
async def test_refresh_with_malformed_token(client: AsyncClient):
    """Test that a completely malformed refresh token returns 401"""
    response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": "completely.invalid.token"},
    )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_users_me_returns_503_on_key_misconfiguration(client: AsyncClient):
    """Test that RuntimeError from key misconfiguration returns 503, not 500"""
    with patch(
        "app.api.v1.auth._get_verification_key",
        side_effect=RuntimeError("RS256 JWT_PUBLIC_KEY required in production"),
    ):
        response = await client.get(
            "/api/v1/users/me",
            headers={"Authorization": "Bearer some.valid.looking-token"},
        )
    assert response.status_code == 503
    assert "misconfigured" in response.json()["detail"].lower()


@pytest.mark.anyio
async def test_refresh_returns_503_on_key_misconfiguration(client: AsyncClient):
    """Test that RuntimeError from key misconfiguration in refresh returns 503"""
    with patch(
        "app.api.v1.auth._get_verification_key",
        side_effect=RuntimeError("RS256 JWT_PUBLIC_KEY required in production"),
    ):
        response = await client.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "some.refresh.token"},
        )
    assert response.status_code == 503
    assert "misconfigured" in response.json()["detail"].lower()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (
            "/api/v1/auth/signup",
            {
                "email": "rate-limit@example.com",
                "password": "Valid-Password-123!",
                "name": "Test Student",
            },
        ),
        (
            "/api/v1/auth/login",
            {"email": "rate-limit@example.com", "password": "Valid-Password-123!"},
        ),
        (
            "/api/v1/auth/forgot-password",
            {"email": "rate-limit@example.com"},
        ),
        (
            "/api/v1/auth/refresh",
            {"refresh_token": "opaque-refresh-token"},
        ),
    ],
)
async def test_auth_routes_stop_when_rate_limiter_is_unavailable(
    client: AsyncClient, path: str, payload: dict
):
    limiter_error = HTTPException(
        status_code=503,
        detail="Authentication temporarily unavailable. Please try again shortly.",
    )
    rate_limiter = AsyncMock(side_effect=limiter_error)

    with (
        patch("app.api.v1.auth._check_rate_limit", rate_limiter),
        patch("app.models.user.User.find_one", new_callable=AsyncMock) as user_lookup,
    ):
        response = await client.post(path, json=payload)

    assert response.status_code == 503
    rate_limiter.assert_awaited_once()
    user_lookup.assert_not_awaited()
