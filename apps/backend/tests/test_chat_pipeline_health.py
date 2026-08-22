"""Workers AI health probe tests."""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("JWT_SECRET", "test-secret-at-least-32-characters-long")
os.environ.setdefault("APP_ENV", "development")

CRON_SECRET = "probe-test-secret-abc"
AUTH = {"Authorization": f"Bearer {CRON_SECRET}"}
ENDPOINT = "/api/v1/health/chat-pipeline"
ASSAMESE_TEXT = "মই ছ্যৰাবিট, আপোনাৰ শিক্ষামূলক সহায়ক।"


@pytest.fixture
async def ac():
    from app.main import app

    mock_tm = MagicMock()
    mock_tm._is_cache_valid.return_value = True
    mock_tm._embeddings = [1, 2, 3]
    with (
        patch("app.config.settings.TRANSLATE_CRON_SECRET", CRON_SECRET),
        patch("app.config.settings.EDGE_SHARED_SECRET", "worker-secret"),
        patch("app.services.ai.topic_matcher.topic_matcher", mock_tm),
        patch("app.db.mongo.init_mongo", new_callable=AsyncMock),
        patch("app.config.settings.startup_errors", []),
    ):
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            yield client


@pytest.mark.anyio
async def test_chat_pipeline_requires_cron_secret(ac):
    response = await ac.get(ENDPOINT)
    assert response.status_code == 401


@pytest.mark.anyio
async def test_chat_pipeline_reports_workers_ai_and_assamese_output(ac):
    async def stream(*_args, **_kwargs):
        yield ASSAMESE_TEXT

    worker_client = MagicMock()
    worker_client.stream_generate_with_retry = stream
    with (
        patch(
            "app.services.ai.workers_ai_client.generate_with_workers_ai",
            AsyncMock(side_effect=["PONG", ASSAMESE_TEXT]),
        ),
        patch("app.services.ai.workers_ai_client.workers_ai_client", worker_client),
    ):
        response = await ac.get(ENDPOINT, headers=AUTH)

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"].startswith("@cf/")
    assert payload["assamese_probe"]["has_assamese_script"] is True
    assert payload["streaming_assamese_probe"]["has_assamese_script"] is True


@pytest.mark.anyio
async def test_workers_ai_deep_ping_calls_the_authenticated_generation_route():
    from app.api.v1.health import workers_ai_ping

    with (
        patch("app.config.settings.EDGE_SHARED_SECRET", "worker-secret"),
        patch(
            "app.config.settings.WORKERS_AI_INTERNAL_URL",
            "https://syrabit-api-prod.example.workers.dev",
        ),
        patch(
            "app.services.ai.workers_ai_client.generate_with_workers_ai",
            AsyncMock(return_value="OK"),
        ) as generate,
    ):
        result = await workers_ai_ping()

    assert result["status"] == "healthy"
    assert result["endpoint"] == "https://syrabit-api-prod.example.workers.dev"
    generate.assert_awaited_once()
    assert generate.await_args.kwargs["max_tokens"] == 256


@pytest.mark.anyio
async def test_workers_ai_deep_ping_is_degraded_without_internal_endpoint():
    from app.api.v1.health import workers_ai_ping

    with (
        patch("app.config.settings.EDGE_SHARED_SECRET", "worker-secret"),
        patch("app.config.settings.WORKERS_AI_INTERNAL_URL", None),
    ):
        result = await workers_ai_ping()

    assert result["status"] == "degraded"
    assert "WORKERS_AI_INTERNAL_URL" in result["error"]


@pytest.mark.anyio
async def test_workers_ai_embedding_ping_exercises_the_rag_embedding_path():
    from app.api.v1.health import workers_ai_embedding_ping

    with patch(
        "app.services.ai.embedder.generate_embedding_vector",
        AsyncMock(return_value=[0.0] * 1024),
    ) as embed:
        result = await workers_ai_embedding_ping()

    assert result["status"] == "healthy"
    assert result["dimensions"] == 1024
    embed.assert_awaited_once_with("Syrabit embedding health check")


@pytest.mark.anyio
async def test_workers_ai_embedding_ping_rejects_wrong_vector_dimension():
    from app.api.v1.health import workers_ai_embedding_ping

    with patch(
        "app.services.ai.embedder.generate_embedding_vector",
        AsyncMock(return_value=[0.0] * 8),
    ):
        result = await workers_ai_embedding_ping()

    assert result["status"] == "degraded"
    assert "expected 1024" in result["error"]