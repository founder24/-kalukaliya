import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock, MagicMock, patch


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
async def test_protected_subscription_without_token(client: AsyncClient):
    """Test subscription endpoint requires auth"""
    response = await client.get("/api/v1/subscription/status")
    assert response.status_code in [401, 403]


@pytest.mark.anyio
async def test_cors_preflight_includes_turnstile_headers(client: AsyncClient):
    """Test that CORS preflight response includes x-turnstile-token in allowed headers"""
    response = await client.options(
        "/api/v1/auth/signup",
        headers={
            "Origin": "https://syrabit.ai",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-turnstile-token",
        },
    )
    assert response.status_code == 200
    allow_headers = response.headers.get("access-control-allow-headers", "").lower()
    assert "x-turnstile-token" in allow_headers
    assert "cf-turnstile-response" in allow_headers


@pytest.mark.anyio
async def test_rate_limit_uses_x_real_ip():
    """Test that rate limiting uses X-Real-IP header when present"""
    from app.api.v1.auth import _check_rate_limit

    mock_redis = AsyncMock()
    mock_redis.incr = AsyncMock(return_value=1)
    mock_redis.expire = AsyncMock()

    mock_request = MagicMock()
    mock_request.headers = {"X-Real-IP": "203.0.113.50"}
    mock_request.client = MagicMock()
    mock_request.client.host = "10.0.0.1"

    with patch("app.db.redis.get_redis", return_value=mock_redis):
        await _check_rate_limit(mock_request, "login", 10)

    # Verify the rate key uses X-Real-IP, not the client host
    call_args = mock_redis.incr.call_args[0][0]
    assert "203.0.113.50" in call_args
    assert "10.0.0.1" not in call_args


@pytest.mark.anyio
async def test_rate_limit_uses_x_forwarded_for_fallback():
    """Test that rate limiting falls back to X-Forwarded-For when X-Real-IP is absent"""
    from app.api.v1.auth import _check_rate_limit

    mock_redis = AsyncMock()
    mock_redis.incr = AsyncMock(return_value=1)
    mock_redis.expire = AsyncMock()

    mock_request = MagicMock()
    mock_request.headers = {"X-Forwarded-For": "198.51.100.10, 10.0.0.1"}
    mock_request.client = MagicMock()
    mock_request.client.host = "10.0.0.1"

    with patch("app.db.redis.get_redis", return_value=mock_redis):
        await _check_rate_limit(mock_request, "login", 10)

    # Verify the rate key uses the first IP from X-Forwarded-For
    call_args = mock_redis.incr.call_args[0][0]
    assert "198.51.100.10" in call_args
    assert "10.0.0.1" not in call_args
