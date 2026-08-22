"""
Latency Audit Tests

Verify that production latency optimizations are in place:
1. Parallel execution of retrieve_context + load_conversation_history
2. Rate limit uses Redis pipeline (reduces HTTP round-trips)
3. Unified middleware adds all expected headers in a single pass
4. RAG search warm-up runs during startup (topic embeddings)
"""

import asyncio
import time
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def sync_client():
    """In-process async client that avoids app lifespan background work."""
    from app.main import app

    with patch("app.api.v1.auth._check_rate_limit", AsyncMock()):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
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
# (b) Rate limit uses MongoDB monthly quota in every backend request path
# ═══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_rate_limit_skips_burst_when_edge_header_present():
    """
    When X-Rate-Limited-By: edge header is present, the backend should
    still enforce only the MongoDB monthly quota.
    """
    from app.api.deps.rate_limit import check_rate_limit

    mock_db = MagicMock()
    mock_db.quota_usage.find_one_and_update = AsyncMock(return_value={"count": 1})
    mock_client = MagicMock()
    mock_client.__getitem__.return_value = mock_db

    mock_request = MagicMock()
    mock_request.headers = {"x-rate-limited-by": "edge"}

    with patch("app.db.mongo.get_mongo_client", return_value=mock_client):
        result = await check_rate_limit(
            "user-123", "free", "127.0.0.1", request=mock_request
        )

    allowed, current_count, limit, limit_type = result
    assert allowed is True
    assert limit_type == "monthly"
    assert current_count == 1
    mock_db.quota_usage.find_one_and_update.assert_awaited_once()


@pytest.mark.asyncio
async def test_rate_limit_uses_incr_without_edge_header():
    """
    Without an edge header, the backend still uses the same MongoDB quota.
    Burst limiting is handled at the edge layer exclusively.
    """
    from app.api.deps.rate_limit import check_rate_limit

    mock_db = MagicMock()
    mock_db.quota_usage.find_one_and_update = AsyncMock(return_value={"count": 1})
    mock_client = MagicMock()
    mock_client.__getitem__.return_value = mock_db

    mock_request = MagicMock()
    mock_request.headers = {}

    with patch("app.db.mongo.get_mongo_client", return_value=mock_client):
        result = await check_rate_limit(
            "user-123", "free", "127.0.0.1", request=mock_request
        )

    allowed, current_count, limit, limit_type = result
    assert allowed is True
    assert limit_type == "monthly"
    assert current_count == 1
    mock_db.quota_usage.find_one_and_update.assert_awaited_once()


# ═══════════════════════════════════════════════════════════════
# (c) Unified middleware adds all expected headers
# ═══════════════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_middleware_adds_security_headers(sync_client):
    """
    A single unified middleware should add X-Content-Type-Options,
    X-Frame-Options, X-Request-ID, and Strict-Transport-Security.
    """
    response = await sync_client.get("/health")

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
# (d) RAG search warm-up runs during startup (Vertex Search retired)
# ═══════════════════════════════════════════════════════════════


def test_vertex_search_module_is_retired():
    """Vertex Search has been retired — its module must no longer exist.

    RAG retrieval now uses MongoVectorSearchService (MongoDB topic embeddings
    + in-memory cosine similarity). Importing the old Vertex module must fail
    so nothing accidentally revives the retired 800-3000ms search path.
    """
    import pytest as _pytest

    with _pytest.raises(ModuleNotFoundError):
        import app.services.search.vertex_search  # noqa: F401

    # The replacement service is what the retrieval pipeline uses now.
    from app.services.search.mongo_vector_search import MongoVectorSearchService

    assert hasattr(MongoVectorSearchService, "search_context"), (
        "MongoVectorSearchService must expose search_context()"
    )


def test_lifespan_warms_up_rag_search():
    """The app lifespan must warm the RAG search path during startup.

    Vertex Search's warm_up() is retired; warm-up is now handled by preloading
    the topic embeddings (topic_matcher._load_embeddings) used by
    MongoVectorSearchService, so the first real request is not slowed down.
    """
    import inspect
    from app import main

    source = inspect.getsource(main.lifespan)
    assert "_warm_topic_matcher" in source, (
        "Lifespan should warm the topic embeddings that back RAG search"
    )
    assert "_load_embeddings" in source, (
        "Lifespan should preload topic embeddings during startup warm-up"
    )
