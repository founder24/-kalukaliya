"""Tests for anonymous user access to chat and conversation endpoints."""

import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone


@pytest.fixture
async def anon_client():
    """Create async test client with rate limiting and Beanie mocked."""
    from app.main import app

    async def _noop_rate_limit(*args, **kwargs):
        return (True, 0, 100, "monthly")

    with (
        patch("app.api.v1.auth._check_rate_limit", _noop_rate_limit),
        patch(
            "app.api.deps.rate_limit.check_rate_limit",
            new_callable=AsyncMock,
            return_value=(True, 0, 100, "monthly"),
        ),
        patch(
            "app.models.user.User.find_one", new_callable=AsyncMock, return_value=None
        ),
        patch("app.models.user.User.get", new_callable=AsyncMock, return_value=None),
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


def _make_mock_chat(session_id: str, user_id: str, title: str = "Test Chat"):
    """Create a mock Chat object."""
    chat = MagicMock()
    chat.id = f"fake-id-{session_id}"
    chat.session_id = session_id
    chat.title = title
    chat.user_id = user_id
    chat.messages = [
        {"role": "user", "content": "hello", "timestamp": "2024-01-01T00:00:00Z"},
        {"role": "assistant", "content": "hi", "timestamp": "2024-01-01T00:00:01Z"},
    ]
    chat.created_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    chat.updated_at = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return chat


VALID_ANON_ID = "anon_" + "a" * 32  # anon_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa


@pytest.mark.anyio
async def test_anon_get_history_with_anon_id_returns_200(anon_client: AsyncClient):
    """Anonymous user with x-anon-id header can GET /chat/history and gets 200."""
    mock_chats = [_make_mock_chat(f"session-{i}", VALID_ANON_ID) for i in range(3)]

    mock_query = MagicMock()
    mock_query.sort = MagicMock(return_value=mock_query)
    mock_query.skip = MagicMock(return_value=mock_query)
    mock_query.limit = MagicMock(return_value=mock_query)
    mock_query.to_list = AsyncMock(return_value=mock_chats)
    mock_query.count = AsyncMock(return_value=3)

    with patch("app.models.chat.Chat.find", return_value=mock_query):
        response = await anon_client.get(
            "/api/v1/chat/history",
            headers={"x-anon-id": VALID_ANON_ID},
        )

    assert response.status_code == 200
    data = response.json()
    assert "chats" in data
    assert len(data["chats"]) <= 5


@pytest.mark.anyio
async def test_anon_get_history_without_anon_id_returns_empty(anon_client: AsyncClient):
    """Anonymous user without x-anon-id header gets empty list, not 401."""
    response = await anon_client.get("/api/v1/chat/history")

    assert response.status_code == 200
    data = response.json()
    assert data["chats"] == []
    assert data["pagination"]["total"] == 0


@pytest.mark.anyio
async def test_anon_history_capped_at_5(anon_client: AsyncClient):
    """Anonymous user history is hard-capped at 5 entries even if more exist."""
    mock_chats = [_make_mock_chat(f"session-{i}", VALID_ANON_ID) for i in range(5)]

    mock_query = MagicMock()
    mock_query.sort = MagicMock(return_value=mock_query)
    mock_query.skip = MagicMock(return_value=mock_query)
    mock_query.limit = MagicMock(return_value=mock_query)
    mock_query.to_list = AsyncMock(return_value=mock_chats)
    mock_query.count = AsyncMock(return_value=10)

    with patch("app.models.chat.Chat.find", return_value=mock_query):
        response = await anon_client.get(
            "/api/v1/chat/history",
            headers={"x-anon-id": VALID_ANON_ID},
        )

    assert response.status_code == 200
    data = response.json()
    # Verify limit was passed as 5
    mock_query.limit.assert_called_with(5)
    assert len(data["chats"]) <= 5


@pytest.mark.anyio
async def test_authenticated_user_gets_full_history(anon_client: AsyncClient):
    """Authenticated user can GET /chat/history with full paginated access."""
    from app.api.v1.auth import create_access_token

    mock_user = MagicMock()
    mock_user.id = "test-user-id-123"
    mock_user.email = "test@example.com"
    mock_user.subscription_tier = "free"

    token = create_access_token("test-user-id-123")
    headers = {"Authorization": f"Bearer {token}"}

    mock_chats = [
        _make_mock_chat(f"session-{i}", "test-user-id-123") for i in range(10)
    ]

    mock_query = MagicMock()
    mock_query.sort = MagicMock(return_value=mock_query)
    mock_query.skip = MagicMock(return_value=mock_query)
    mock_query.limit = MagicMock(return_value=mock_query)
    mock_query.to_list = AsyncMock(return_value=mock_chats)
    mock_query.count = AsyncMock(return_value=10)

    with (
        patch(
            "app.models.user.User.get", new_callable=AsyncMock, return_value=mock_user
        ),
        patch("app.models.chat.Chat.find", return_value=mock_query),
    ):
        response = await anon_client.get(
            "/api/v1/chat/history",
            headers=headers,
        )

    assert response.status_code == 200
    data = response.json()
    assert len(data["chats"]) == 10
    # Authenticated user gets their requested limit (20 default), not capped to 5
    mock_query.limit.assert_called_with(20)


@pytest.mark.anyio
async def test_anon_conversations_endpoint_capped_at_5(anon_client: AsyncClient):
    """Anonymous user GET /conversations/anon receives max 5 conversations."""
    mock_chats = [_make_mock_chat(f"session-{i}", VALID_ANON_ID) for i in range(5)]

    mock_query = MagicMock()
    mock_query.sort = MagicMock(return_value=mock_query)
    mock_query.skip = MagicMock(return_value=mock_query)
    mock_query.limit = MagicMock(return_value=mock_query)
    mock_query.to_list = AsyncMock(return_value=mock_chats)
    mock_query.count = AsyncMock(return_value=10)

    with patch("app.models.chat.Chat.find", return_value=mock_query):
        response = await anon_client.get(
            "/api/v1/conversations/anon",
            headers={"x-anon-id": VALID_ANON_ID},
            params={"limit": 50},
        )

    assert response.status_code == 200
    data = response.json()
    # Should be capped at 5 regardless of client requesting 50
    mock_query.limit.assert_called_with(5)
    assert len(data["conversations"]) <= 5


@pytest.mark.anyio
async def test_anon_invalid_anon_id_returns_empty(anon_client: AsyncClient):
    """Invalid anon_id (e.g., raw MongoDB ObjectId) returns empty list, not user data."""
    # This is a raw MongoDB ObjectId - should NOT be accepted as a valid anon_id
    invalid_anon_id = "507f1f77bcf86cd799439011"

    with patch("app.models.chat.Chat.find") as mock_find:
        response = await anon_client.get(
            "/api/v1/chat/history",
            headers={"x-anon-id": invalid_anon_id},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["chats"] == []
    assert data["pagination"]["total"] == 0
    # Chat.find should NOT be called with the invalid anon_id
    mock_find.assert_not_called()


@pytest.mark.anyio
async def test_anon_invalid_anon_id_conversations_alias_returns_empty(
    anon_client: AsyncClient,
):
    """Invalid anon_id via /chat/conversations (legacy alias) also returns empty list."""
    invalid_anon_id = "not-a-valid-format"

    with patch("app.models.chat.Chat.find") as mock_find:
        response = await anon_client.get(
            "/api/v1/chat/conversations",
            headers={"x-anon-id": invalid_anon_id},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["chats"] == []
    assert data["pagination"]["total"] == 0
    mock_find.assert_not_called()
