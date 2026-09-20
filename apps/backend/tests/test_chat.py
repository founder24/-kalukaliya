import pytest
from httpx import AsyncClient
from unittest.mock import MagicMock, patch, AsyncMock
import json

from fastapi import HTTPException
from pydantic import ValidationError


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


def test_chat_request_bounds_nested_context_messages():
    from app.api.v1.chat import ChatRequest, MAX_CONTEXT_MESSAGE_BYTES

    with pytest.raises(ValidationError):
        ChatRequest(
            message="hello",
            context_messages=[
                {"role": "user", "content": "x" * MAX_CONTEXT_MESSAGE_BYTES}
            ],
        )

    with pytest.raises(ValidationError):
        ChatRequest(message="hello", context_messages=["not an object"])


def test_chat_body_size_guard_rejects_oversized_content_length():
    from app.api.v1.chat import MAX_CHAT_BODY_BYTES, _enforce_chat_body_size
    from starlette.requests import Request

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/chat/",
            "headers": [
                (b"content-length", str(MAX_CHAT_BODY_BYTES + 1).encode()),
            ],
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        _enforce_chat_body_size(request)
    assert exc_info.value.status_code == 413


def test_rate_limit_headers_include_remaining_and_month_reset():
    from app.api.v1.chat import _rate_limit_headers

    headers = _rate_limit_headers(3, 30)
    assert headers["X-RateLimit-Limit"] == "30"
    assert headers["X-RateLimit-Remaining"] == "27"
    assert int(headers["X-RateLimit-Reset"]) > 0

    exhausted = _rate_limit_headers(31, 30, retry_after=3600)
    assert exhausted["X-RateLimit-Remaining"] == "0"
    assert exhausted["Retry-After"] == "3600"


def test_response_quality_scores_language_and_length_signals():
    from app.services.ai.response_quality import score_response_quality

    assert score_response_quality("This is a detailed English answer.", "en") == {
        "score": 1.0,
        "passed": True,
        "flags": [],
    }
    short = score_response_quality("ok", "en")
    assert short["passed"] is False
    assert "too_short" in short["flags"]


@pytest.mark.anyio
async def test_save_chat_reports_success_for_stream_completion():
    from app.services.chat_service import ChatService

    chat_doc = MagicMock()
    chat_doc.save = AsyncMock()
    chat_class = MagicMock(return_value=chat_doc)
    chat_class.find_one = AsyncMock(return_value=None)

    with (
        patch("app.models.chat.Chat", chat_class),
        patch.object(
            ChatService,
            "_invalidate_history_cache",
            new_callable=AsyncMock,
        ),
    ):
        saved = await ChatService.save_chat(
            user_id="anon_test",
            session_id="session-test",
            user_message="hello",
            assistant_response="hi",
            target_model="@cf/test",
            latency_ms=10,
            context_chunks=[],
            detected_lang="en",
        )

    assert saved is True
    chat_doc.save.assert_awaited_once()


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
    """Test that retrieval failure returns an empty v2 result without raising."""
    from app.services.chat_service import ChatService

    with patch(
        "app.services.rag.retrieval_v2.retrieve_v2",
        new_callable=AsyncMock,
        side_effect=RuntimeError("Vector search unavailable"),
    ):
        chunks, path = await ChatService.retrieve_context("test query", "free")

    assert chunks == []
    assert path == "empty"


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

    with patch(
        "app.services.rag.retrieval_v2.retrieve_v2",
        new_callable=AsyncMock,
        return_value=(mock_chunks, "vectorize"),
    ):
        chunks, path = await ChatService.retrieve_context("test query", "free")

    # Should keep score >= 0.70, filter out score 0.50
    assert path == "vectorize"
    assert len(chunks) == 2
    assert all(c["score"] >= 0.70 for c in chunks)


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
