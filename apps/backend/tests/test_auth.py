import pytest
from httpx import AsyncClient


@pytest.mark.anyio
async def test_signup_weak_password(client: AsyncClient):
    """Test signup rejects weak passwords"""
    response = await client.post("/api/v1/auth/signup", json={
        "email": "new@example.com",
        "password": "short",
    })
    assert response.status_code == 422


@pytest.mark.anyio
async def test_login_invalid_credentials(client: AsyncClient):
    """Test login with wrong password returns 401"""
    response = await client.post("/api/v1/auth/login", json={
        "email": "nonexistent@example.com",
        "password": "wrongpassword123",
    })
    assert response.status_code == 401


@pytest.mark.anyio
async def test_refresh_token_must_be_in_body(client: AsyncClient):
    """Test that refresh token as query param is rejected (must be in body)"""
    response = await client.post("/api/v1/auth/refresh?refresh_token=fake_token")
    assert response.status_code == 422


@pytest.mark.anyio
async def test_refresh_token_invalid(client: AsyncClient):
    """Test that invalid refresh token in body returns 401"""
    response = await client.post("/api/v1/auth/refresh", json={
        "refresh_token": "invalid_token_value"
    })
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
