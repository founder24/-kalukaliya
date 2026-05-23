import pytest
from httpx import AsyncClient
from unittest.mock import patch


@pytest.mark.anyio
async def test_chat_empty_message(client: AsyncClient):
    """Test that empty messages are rejected with 422"""
    response = await client.post("/api/v1/chat/", json={
        "message": ""
    })
    assert response.status_code == 422


@pytest.mark.anyio
async def test_chat_message_too_long(client: AsyncClient):
    """Test that messages over 2000 chars are rejected"""
    response = await client.post("/api/v1/chat/", json={
        "message": "x" * 2001
    })
    assert response.status_code == 422


@pytest.mark.anyio
async def test_chat_rate_limit_returns_429(client: AsyncClient):
    """Test rate limiting returns 429"""
    with patch("app.api.v1.chat.check_rate_limit", return_value=False):
        response = await client.post("/api/v1/chat/", json={
            "message": "hello world"
        })
        assert response.status_code == 429


@pytest.mark.anyio
async def test_chat_error_does_not_leak_details(client: AsyncClient):
    """Test that internal errors return generic messages, not stack traces"""
    with patch("app.api.v1.chat.check_rate_limit", return_value=True), \
         patch("app.services.ai.router.detect_language_and_route", side_effect=Exception("secret db connection string")):
        response = await client.post("/api/v1/chat/", json={
            "message": "hello"
        })
        if response.status_code == 500:
            detail = response.json().get("detail", "")
            assert "secret db connection string" not in detail
            assert "internal error" in detail.lower() or "try again" in detail.lower()
