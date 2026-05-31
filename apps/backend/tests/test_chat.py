import pytest
from httpx import AsyncClient
from unittest.mock import patch, AsyncMock
import json


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

    with patch("app.services.chat_service.search_service") as mock_search:
        mock_search.is_available.return_value = False
        mock_search.search_context = AsyncMock(
            side_effect=AssertionError("search_context should not be called")
        )

        result = await ChatService.retrieve_context("test query", "free")

        assert result == []
        mock_search.search_context.assert_not_called()


@pytest.mark.anyio
async def test_is_generic_query_greetings():
    """Test generic query detection for greetings."""
    from app.services.chat_service import ChatService

    assert ChatService.is_generic_query("hi") is True
    assert ChatService.is_generic_query("Hello!") is True
    assert ChatService.is_generic_query("How are you?") is True
    assert ChatService.is_generic_query("thanks") is True
    assert ChatService.is_generic_query("  hey  ") is True
    assert ChatService.is_generic_query("bye!") is True


@pytest.mark.anyio
async def test_is_generic_query_real_questions():
    """Test generic query detection does NOT flag real questions."""
    from app.services.chat_service import ChatService

    assert ChatService.is_generic_query("What is photosynthesis?") is False
    assert ChatService.is_generic_query("Explain the water cycle") is False
    assert ChatService.is_generic_query("hello can you explain gravity") is False
    assert ChatService.is_generic_query("hi tell me about atoms") is False


@pytest.mark.anyio
async def test_retrieve_context_filters_low_scores():
    """Test that retrieve_context filters chunks below 0.70 threshold."""
    from app.services.chat_service import ChatService

    mock_chunks = [
        {
            "id": "1",
            "title": "Good",
            "content": "relevant content",
            "score": 0.85,
            "url": "",
        },
        {"id": "2", "title": "Bad", "content": "irrelevant", "score": 0.50, "url": ""},
        {"id": "3", "title": "OK", "content": "borderline", "score": 0.70, "url": ""},
    ]

    with patch("app.services.chat_service.search_service") as mock_search:
        mock_search.is_available.return_value = True
        mock_search.search_context = AsyncMock(return_value=mock_chunks)

        result = await ChatService.retrieve_context("test query", "free")

        # Should keep score >= 0.70, filter out score 0.50
        assert len(result) == 2
        assert all(c["score"] >= 0.70 for c in result)


@pytest.mark.anyio
async def test_stream_llm_uses_content_field():
    """Test that stream_llm yields 'content' field not 'text'."""
    from app.services.chat_service import ChatService

    async def mock_stream(*args, **kwargs):
        yield "Hello"
        yield " world"

    with patch("app.services.ai.router.stream_response", side_effect=mock_stream):
        events = []
        async for event in ChatService.stream_llm(
            system_prompt="test",
            sanitized_message="hi",
            target_model="gemini-2.0-flash",
            detected_lang="en",
            user_id="test-user",
            request_message="hi",
        ):
            events.append(event)

        # Check content field is used (not text)
        for event in events:
            if event.startswith("data: "):
                data = json.loads(event[6:].strip())
                if "content" in data:
                    assert "text" not in data or data.get(
                        "__syrabit_stream_complete_7f3a9b2e__"
                    )
                    break
        else:
            pytest.fail("No event with 'content' field found")


@pytest.mark.anyio
async def test_check_topic_match_skips_when_no_topics():
    """Test that check_topic_match skips embedding when no topics exist."""
    from app.services.chat_service import ChatService

    with (
        patch(
            "app.services.ai.topic_matcher.topic_matcher.has_topics",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "app.services.ai.embedder.generate_embedding_vector",
            new_callable=AsyncMock,
        ) as mock_embed,
    ):
        result = await ChatService.check_topic_match("What is photosynthesis?")
    assert result is None
    mock_embed.assert_not_called()


@pytest.mark.anyio
async def test_get_greeting_response_english():
    """Test greeting response for English."""
    from app.services.chat_service import ChatService

    result = ChatService.get_greeting_response("hello", "en")
    assert result is not None
    assert "Syrabit" in result


@pytest.mark.anyio
async def test_get_greeting_response_assamese():
    """Test greeting response for Assamese."""
    from app.services.chat_service import ChatService

    result = ChatService.get_greeting_response("hi", "as")
    assert result is not None
    assert "\u099b\u09bf\u09f0\u09be\u09ac\u09bf\u099f" in result


@pytest.mark.anyio
async def test_get_greeting_response_non_greeting():
    """Test that non-greeting messages return None."""
    from app.services.chat_service import ChatService

    result = ChatService.get_greeting_response("What is photosynthesis?", "en")
    assert result is None
