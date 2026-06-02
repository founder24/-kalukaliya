import pytest
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
async def test_protected_subscription_without_token(client: AsyncClient):
    """Test subscription endpoint requires auth"""
    response = await client.get("/api/v1/subscription/status")
    assert response.status_code in [401, 403]


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
async def test_subscription_create_order_with_invalid_token(client: AsyncClient):
    """Test that subscription create-order with invalid token returns 401"""
    response = await client.post(
        "/api/v1/subscription/create-order",
        headers={"Authorization": "Bearer bad.token.here"},
    )
    assert response.status_code == 401
