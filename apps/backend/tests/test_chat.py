import pytest
from httpx import AsyncClient
from unittest.mock import patch, AsyncMock


@pytest.mark.anyio
async def test_chat_empty_message(client: AsyncClient):
    """Test that empty messages are rejected with 422"""
    response = await client.post("/api/v1/chat/", json={"message": ""})
    assert response.status_code == 422


@pytest.mark.anyio
async def test_chat_message_too_long(client: AsyncClient):
    """Test that messages over 2000 chars are rejected"""
    response = await client.post("/api/v1/chat/", json={"message": "x" * 2001})
    assert response.status_code == 422


@pytest.mark.anyio
async def test_chat_rate_limit_returns_429(client: AsyncClient):
    """Test rate limiting returns 429"""
    with patch(
        "app.api.v1.chat.check_rate_limit",
        return_value=(False, 101, 100, "monthly"),
    ):
        response = await client.post("/api/v1/chat/", json={"message": "hello world"})
        assert response.status_code == 429


@pytest.mark.anyio
async def test_chat_error_does_not_leak_details(client: AsyncClient):
    """Test that internal errors return generic messages, not stack traces"""
    with (
        patch(
            "app.api.v1.chat.check_rate_limit",
            return_value=(True, 1, 100, "monthly"),
        ),
        patch(
            "app.services.ai.router.detect_language_and_route",
            side_effect=Exception("secret db connection string"),
        ),
    ):
        response = await client.post("/api/v1/chat/", json={"message": "hello"})
        if response.status_code == 500:
            detail = response.json().get("detail", "")
            assert "secret db connection string" not in detail
            assert "internal error" in detail.lower() or "try again" in detail.lower()


@pytest.mark.anyio
async def test_retrieve_context_returns_empty_when_search_not_initialized():
    """Test that retrieve_context returns [] immediately when search service is not initialized."""
    from app.services.chat_service import ChatService

    with patch(
        "app.services.chat_service.search_service"
    ) as mock_search:
        mock_search.is_available.return_value = False
        mock_search.search_context = AsyncMock(
            side_effect=AssertionError("search_context should not be called")
        )

        result = await ChatService.retrieve_context("test query", "free")

        assert result == []
        mock_search.search_context.assert_not_called()
