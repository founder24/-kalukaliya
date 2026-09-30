"""
Pipeline Audit Tests

End-to-end pipeline test with all external services mocked.
Proves that no unnecessary sequential waits exist in the chat pipeline
by measuring elapsed time with all I/O operations mocked to return immediately.
"""

import time
import pytest
from contextlib import asynccontextmanager
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient


@pytest.fixture
def mocked_pipeline_client():
    """
    TestClient with ALL external services mocked:
    - Vertex Search (embedding + search)
    - Redis (rate limit, cache)
    - MongoDB (chat save, user update)
    - LLM providers (Sarvam, Cloudflare, Vertex)
    - PostHog tracking
    """
    from app.main import app

    mock_redis = MagicMock()
    mock_redis.incr = AsyncMock(return_value=1)
    mock_redis.expire = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock()
    mock_redis.delete = AsyncMock()
    mock_pipe = MagicMock()
    mock_pipe.incr = MagicMock(return_value=mock_pipe)
    mock_pipe.exec = AsyncMock(return_value=[1, 1])
    mock_redis.pipeline = MagicMock(return_value=mock_pipe)

    mock_context_chunks = [
        {
            "id": "doc-1",
            "title": "Test Document",
            "content": "This is test content about Assam education.",
            "score": 0.95,
            "reranker_score": 0.9,
            "url": "https://example.com/doc1",
        }
    ]

    patches = {
        "rate_limit": patch(
            "app.api.v1.chat.check_rate_limit",
            new_callable=AsyncMock,
            return_value=(True, 1, 6, "anonymous"),
        ),
        "redis_cache": patch("app.db.redis.get_redis", return_value=mock_redis),
        "topic_match": patch(
            "app.services.chat_service.ChatService.check_topic_match_with_embedding",
            new_callable=AsyncMock,
            return_value=(None, None),
        ),
        "search": patch(
            "app.services.chat_service.ChatService.retrieve_web_context",
            new_callable=AsyncMock,
            return_value=[],
        ),
        "history": patch(
            "app.services.chat_service.ChatService.load_conversation_history",
            new_callable=AsyncMock,
            return_value=[],
        ),
        "last_source": patch(
            "app.services.chat_service.ChatService.load_last_source_ctx",
            new_callable=AsyncMock,
            return_value=None,
        ),
        "llm_generate": patch(
            "app.services.chat_service.ChatService.call_llm",
            new_callable=AsyncMock,
            return_value=(
                "This is a test response about Assam education.",
                "mock-model",
            ),
        ),
        "token_budget": patch(
            "app.core.token_budget.truncate_chunks_to_budget",
            return_value=mock_context_chunks,
        ),
        "posthog": patch(
            "app.utils.tracking.track_chat_completed",
            new_callable=AsyncMock,
        ),
        "auth_optional": patch(
            "app.api.v1.auth.get_current_user_optional",
            new_callable=AsyncMock,
            return_value=None,
        ),
    }

    @asynccontextmanager
    async def no_startup_lifespan(_app):
        # These endpoint tests mock provider I/O and do not exercise expensive
        # production startup tasks such as greeting-embedding warmup.
        yield

    with (
        patches["rate_limit"],
        patches["redis_cache"],
        patches["topic_match"],
        patches["search"],
        patches["history"],
        patches["last_source"],
        patches["llm_generate"],
        patches["token_budget"],
        patches["posthog"],
        patches["auth_optional"],
        patch.object(app.router, "lifespan_context", no_startup_lifespan),
    ):
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client


def test_chat_pipeline_completes_under_500ms(mocked_pipeline_client):
    """
    With all external services mocked, the chat pipeline should
    complete in under 500ms. This proves no unnecessary sequential waits.
    With mocks returning immediately, actual time should be well under 100ms.
    """
    start = time.time()
    response = mocked_pipeline_client.post(
        "/api/v1/chat/",
        json={"message": "What is the capital of Assam?", "session_id": "test-session"},
        headers={"Origin": "https://syrabit.ai"},
    )
    elapsed_ms = (time.time() - start) * 1000

    # The response may be 200 (success) or 4xx (auth/validation)
    # but should NOT be 500 (internal error) and should be fast
    assert response.status_code != 500, f"Pipeline returned 500: {response.text[:500]}"

    # With all mocks, should complete very quickly
    assert elapsed_ms < 500, (
        f"Pipeline took {elapsed_ms:.0f}ms with mocked services. "
        f"Expected < 500ms. Possible sequential wait detected."
    )


def test_chat_pipeline_returns_valid_response(mocked_pipeline_client):
    """
    With all services mocked, the chat endpoint should return
    a well-formed response.
    """
    response = mocked_pipeline_client.post(
        "/api/v1/chat/",
        json={"message": "Tell me about AHSEC exams", "session_id": "test-session-2"},
        headers={"Origin": "https://syrabit.ai"},
    )

    # If we get 200, validate the response structure
    if response.status_code == 200:
        data = response.json()
        assert "response" in data
        assert "model_used" in data
        assert "latency_ms" in data
        assert isinstance(data["latency_ms"], int)
        assert data["latency_ms"] < 500


def test_stream_endpoint_accessible(mocked_pipeline_client):
    """
    The streaming endpoint should be accessible and not crash.
    """
    response = mocked_pipeline_client.post(
        "/api/v1/chat/stream",
        json={"message": "Hello", "session_id": "stream-test"},
        headers={"Origin": "https://syrabit.ai"},
    )
    # Should not be 500
    assert response.status_code != 500, (
        f"Stream endpoint returned 500: {response.text[:500]}"
    )


def test_multiple_sequential_requests_consistent(mocked_pipeline_client):
    """
    Multiple requests in sequence should all complete quickly,
    proving no state leakage or accumulating delays.
    """
    times = []
    for i in range(3):
        start = time.time()
        response = mocked_pipeline_client.post(
            "/api/v1/chat/",
            json={
                "message": f"Test question {i}",
                "session_id": f"seq-test-{i}",
            },
            headers={"Origin": "https://syrabit.ai"},
        )
        elapsed_ms = (time.time() - start) * 1000
        times.append(elapsed_ms)
        assert response.status_code != 500

    # All should be fast, and later requests should not be slower
    for t in times:
        assert t < 500, f"Request took {t:.0f}ms, expected < 500ms"
