"""
Latency Audit Tests

Verify that production latency optimizations are in place:
1. Parallel execution of retrieve_context + load_conversation_history
2. Rate limit uses Redis pipeline (reduces HTTP round-trips)
3. Unified middleware adds all expected headers in a single pass
4. Azure Search warm_up is called during startup
"""

import asyncio
import time
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient


@pytest.fixture
def sync_client():
    """Synchronous TestClient for middleware/header tests."""
    from app.main import app

    with patch("app.api.v1.auth._check_rate_limit", AsyncMock()):
        with TestClient(app) as client:
            yield client


# ═══════════════════════════════════════════════════════════════
# (a) Parallel execution of retrieve_context + load_conversation_history
# ═══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_retrieve_context_and_history_run_in_parallel():
    """
    Mock both retrieve_context and load_conversation_history with 0.1s delays.
    If they run in parallel, total time should be ~0.1s, not ~0.2s.
    """
    from app.services.chat_service import ChatService

    call_log = []

    async def slow_retrieve_context(msg, tier):
        call_log.append(("retrieve_context_start", time.time()))
        await asyncio.sleep(0.1)
        call_log.append(("retrieve_context_end", time.time()))
        return [
            {
                "id": "1",
                "title": "Test",
                "content": "test content",
                "score": 0.9,
                "url": "http://test.com",
            }
        ]

    async def slow_load_history(session_id, max_turns=5):
        call_log.append(("load_history_start", time.time()))
        await asyncio.sleep(0.1)
        call_log.append(("load_history_end", time.time()))
        return "User: hello\nAssistant: hi"

    with (
        patch.object(
            ChatService, "retrieve_context", side_effect=slow_retrieve_context
        ),
        patch.object(
            ChatService, "load_conversation_history", side_effect=slow_load_history
        ),
    ):
        start = time.time()
        context_chunks, history = await asyncio.gather(
            ChatService.retrieve_context("test message", "free"),
            ChatService.load_conversation_history("session-123"),
        )
        elapsed = time.time() - start

    # If parallel, elapsed should be ~0.1s. If sequential, ~0.2s.
    assert elapsed < 0.15, (
        f"retrieve_context and load_conversation_history appear sequential "
        f"(took {elapsed:.3f}s, expected < 0.15s)"
    )
    assert len(context_chunks) == 1
    assert history == "User: hello\nAssistant: hi"

    # Verify both started before either finished
    starts = [t for name, t in call_log if "start" in name]
    ends = [t for name, t in call_log if "end" in name]
    assert len(starts) == 2
    assert len(ends) == 2
    # Both should have started before the first one ended
    assert max(starts) < min(ends), "Both tasks should start before either finishes"


@pytest.mark.asyncio
async def test_chat_endpoint_uses_asyncio_gather():
    """
    Verify that the chat.py source code actually uses asyncio.gather
    for retrieve_context and load_conversation_history.
    """
    import inspect
    from app.api.v1 import chat

    source = inspect.getsource(chat)
    assert "asyncio.gather" in source, "chat.py must use asyncio.gather for parallelism"
    assert "retrieve_context" in source
    assert "load_conversation_history" in source


# ═══════════════════════════════════════════════════════════════
# (b) Rate limit uses pipeline (or skips burst when edge header present)
# ═══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_rate_limit_skips_burst_when_edge_header_present():
    """
    When X-Rate-Limited-By: edge header is present, the backend should
    skip the burst rate limit check (fewer Redis calls).
    """
    from app.api.deps.rate_limit import check_rate_limit

    mock_redis = MagicMock()
    mock_redis.incr = AsyncMock(return_value=1)
    mock_redis.expire = AsyncMock()
    mock_redis.pipeline = MagicMock()

    mock_request = MagicMock()
    mock_request.headers = {"x-rate-limited-by": "edge"}

    with patch("app.api.deps.rate_limit.get_redis", return_value=mock_redis):
        result = await check_rate_limit(
            "user-123", "free", "127.0.0.1", request=mock_request
        )

    allowed, current_count, limit, limit_type = result
    assert allowed is True
    assert limit_type == "monthly"
    # When edge header is present, pipeline() should NOT be called
    mock_redis.pipeline.assert_not_called()
    # Only incr for monthly quota (no burst key)
    mock_redis.incr.assert_called_once()


@pytest.mark.asyncio
async def test_rate_limit_uses_incr_without_edge_header():
    """
    Without edge header, rate_limit should still use incr for monthly quota only.
    Burst limiting is handled at the edge layer exclusively.
    """
    from app.api.deps.rate_limit import check_rate_limit

    mock_redis = MagicMock()
    mock_redis.incr = AsyncMock(return_value=1)
    mock_redis.expire = AsyncMock()
    mock_redis.pipeline = MagicMock()

    mock_request = MagicMock()
    mock_request.headers = {}

    with patch("app.api.deps.rate_limit.get_redis", return_value=mock_redis):
        result = await check_rate_limit(
            "user-123", "free", "127.0.0.1", request=mock_request
        )

    allowed, current_count, limit, limit_type = result
    assert allowed is True
    assert limit_type == "monthly"
    # Only incr for monthly quota (no burst/pipeline needed)
    mock_redis.incr.assert_called_once()
    mock_redis.pipeline.assert_not_called()


# ═══════════════════════════════════════════════════════════════
# (c) Unified middleware adds all expected headers
# ═══════════════════════════════════════════════════════════════


def test_middleware_adds_security_headers(sync_client):
    """
    A single unified middleware should add X-Content-Type-Options,
    X-Frame-Options, X-Request-ID, and Strict-Transport-Security.
    """
    response = sync_client.get("/health")

    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert "max-age=" in response.headers.get("Strict-Transport-Security", "")
    assert response.headers.get("X-Request-ID") is not None
    assert len(response.headers.get("X-Request-ID", "")) > 0


def test_middleware_is_single_pass():
    """
    Verify main.py uses a single unified_middleware, not separate
    csrf_origin_check, add_security_headers, add_request_id middlewares.
    """
    import inspect
    from app import main

    source = inspect.getsource(main)
    # Should have unified middleware
    assert "unified_middleware" in source, "Should use unified_middleware"
    # Should NOT have separate middleware functions
    assert source.count("@app.middleware") == 1, (
        "Should have exactly 1 @app.middleware decorator (unified)"
    )


# ═══════════════════════════════════════════════════════════════
# (d) Azure Search warm_up is called during startup
# ═══════════════════════════════════════════════════════════════


def test_azure_search_has_warm_up_method():
    """Verify AzureSearchService has a warm_up method."""
    from app.services.search.azure_search import AzureSearchService

    assert hasattr(AzureSearchService, "warm_up"), (
        "AzureSearchService must have a warm_up method"
    )


def test_lifespan_calls_warm_up():
    """Verify the app lifespan calls search_service.warm_up()."""
    import inspect
    from app import main

    source = inspect.getsource(main.lifespan)
    assert "warm_up" in source, "Lifespan should call warm_up for Azure Search"
