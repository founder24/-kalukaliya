import asyncio
import ast
import importlib
import logging
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_chat_correlation_id_reuses_only_valid_uuid():
    from app.api.v1.chat import _chat_correlation_id

    request_id = str(uuid.uuid4())
    request = SimpleNamespace(state=SimpleNamespace(request_id=request_id))
    assert _chat_correlation_id(request) == request_id

    request.state.request_id = "student@example.com"
    generated = _chat_correlation_id(request)
    assert generated != request.state.request_id
    assert str(uuid.UUID(generated)) == generated


def test_chat_error_classification_does_not_return_exception_text():
    from app.api.v1.chat import _classify_chat_error

    secret = "student@example.com asked about account 123"
    category = _classify_chat_error(RuntimeError(f"search failed for {secret}"))

    assert category == "search_service"
    assert secret not in category


def test_background_task_log_omits_exception_text(caplog):
    from app.api.v1.chat import _log_task_exception

    task = MagicMock(spec=asyncio.Task)
    task.cancelled.return_value = False
    task.exception.return_value = RuntimeError(
        "failed for student@example.com from 203.0.113.10"
    )
    correlation_id = str(uuid.uuid4())

    with caplog.at_level(logging.ERROR):
        _log_task_exception(task, correlation_id)

    assert "student@example.com" not in caplog.text
    assert "203.0.113.10" not in caplog.text
    assert "background_task_failed" in caplog.text


@pytest.mark.anyio
async def test_chat_request_rejects_pii_bearing_request_id(client, caplog):
    supplied_id = "student@example.com"

    with caplog.at_level(logging.INFO):
        response = await client.post(
            "/api/v1/chat/",
            headers={"X-Request-ID": supplied_id},
            json={"message": ""},
        )

    safe_request_id = response.headers["X-Request-ID"]
    assert response.status_code == 422
    assert supplied_id not in safe_request_id
    assert str(uuid.UUID(safe_request_id)) == safe_request_id
    assert supplied_id not in caplog.text
    assert "request_completed" in caplog.text


@pytest.mark.anyio
async def test_chat_request_replaces_caller_supplied_uuid(client):
    supplied_id = str(uuid.uuid4())

    response = await client.post(
        "/api/v1/chat/",
        headers={"X-Request-ID": supplied_id},
        json={"message": ""},
    )

    assert response.headers["X-Request-ID"] != supplied_id


def test_chat_log_extras_do_not_include_client_metadata():
    from pathlib import Path

    source = Path("app/api/v1/chat.py").read_text()
    tree = ast.parse(source)
    forbidden_keys = {
        "user_id",
        "session_id",
        "chapter_id",
        "subject_id",
        "board_id",
        "class_id",
        "board_name",
        "class_name",
        "source_type",
        "query",
        "message",
    }

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in {"debug", "info", "warning", "error", "exception"}:
            continue
        for keyword in node.keywords:
            if keyword.arg == "extra" and isinstance(keyword.value, ast.Dict):
                keys = {
                    key.value
                    for key in keyword.value.keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                }
                assert keys.isdisjoint(forbidden_keys)


@pytest.mark.anyio
async def test_llm_failure_logs_omit_user_and_provider_error(caplog):
    from app.services.chat_service import ChatService

    user_id = "student@example.com"
    provider_error = "provider failed for private question"
    correlation_id = str(uuid.uuid4())

    with (
        patch(
            "app.services.ai.router.generate_response",
            new=AsyncMock(side_effect=RuntimeError(provider_error)),
        ),
        patch(
            "app.services.ai.workers_ai_client.generate_with_workers_ai",
            new=AsyncMock(side_effect=RuntimeError(provider_error)),
        ),
        patch(
            "app.services.comms.ai_outage_alert.record_ai_outage",
            new=AsyncMock(),
        ),
        caplog.at_level(logging.ERROR),
        pytest.raises(RuntimeError),
    ):
        await ChatService.call_llm(
            system_prompt="system",
            sanitized_message="private question",
            target_model="test-model",
            detected_lang="en",
            user_id=user_id,
            correlation_id=correlation_id,
        )

    assert user_id not in caplog.text
    assert provider_error not in caplog.text
    assert "private question" not in caplog.text


@pytest.mark.anyio
async def test_memory_failure_log_omits_user_and_message(caplog):
    from app.services.memory_service import write_qa_memory

    user_id = "student@example.com"
    message = "my private educational question"
    database_error = "database failed with private request data"

    with (
        patch(
            "app.db.mongo.get_mongo_client",
            side_effect=RuntimeError(database_error),
        ),
        caplog.at_level(logging.WARNING),
    ):
        await write_qa_memory(
            user_id=user_id,
            user_message=message,
            assistant_response="A substantive educational answer that is long enough to save.",
            detected_lang="en",
            confidence_tier="high",
            context_chunks=[],
            session_id="private-session",
            correlation_id=str(uuid.uuid4()),
        )

    assert user_id not in caplog.text
    assert message not in caplog.text
    assert database_error not in caplog.text


@pytest.mark.anyio
async def test_outage_fallback_log_omits_raw_provider_errors(caplog):
    from app.config import settings
    from app.services.comms import ai_outage_alert

    module = importlib.reload(ai_outage_alert)
    sentinel = "student@example.com private prompt"

    with (
        patch.object(settings, "ADMIN_EMAIL", ""),
        caplog.at_level(logging.ERROR),
    ):
        await module.record_ai_outage(
            "student@example.com",
            sarvam_error=sentinel,
            gemini_error=sentinel,
        )

    assert sentinel not in caplog.text
    assert "student@example.com" not in caplog.text
    assert "ai_outage_alert_no_email" in caplog.text


@pytest.mark.anyio
async def test_vector_search_failure_omits_query_from_logs_and_sentry(caplog):
    from app.services.search.mongo_vector_search import MongoVectorSearchService

    sentinel = "student@example.com private embedding request"
    breadcrumbs = []

    with (
        patch(
            "app.services.ai.embedder.generate_embedding_vector",
            new=AsyncMock(side_effect=RuntimeError(sentinel)),
        ),
        patch(
            "app.services.search.mongo_vector_search._EMBED_BACKOFF_DELAYS",
            [0, 0, 0],
        ),
        patch(
            "app.services.search.mongo_vector_search.sentry_sdk.add_breadcrumb",
            side_effect=lambda **kwargs: breadcrumbs.append(kwargs),
        ),
        caplog.at_level(logging.WARNING),
    ):
        chunks, latency = await MongoVectorSearchService().search_context(sentinel)

    assert chunks == []
    assert latency == 0.0
    assert sentinel not in caplog.text
    assert sentinel not in repr(breadcrumbs)


@pytest.mark.anyio
async def test_dead_letter_persistence_omits_personal_data():
    from app.services.dead_letter import store_dead_letter

    collection = MagicMock()
    collection.insert_one = AsyncMock()
    database = MagicMock()
    database.__getitem__.return_value = collection
    client = MagicMock()
    client.__getitem__.return_value = database
    correlation_id = str(uuid.uuid4())

    with patch("app.db.mongo.get_mongo_client", return_value=client):
        await store_dead_letter(
            user_id="student@example.com",
            message="private prompt with personal details",
            lang="en",
            error="provider echoed private prompt",
            correlation_id=correlation_id,
        )

    document = collection.insert_one.await_args.args[0]
    assert document["correlation_id"] == correlation_id
    assert document["error_class"] == "provider_failure"
    assert "user_id" not in document
    assert "message" not in document
    assert "error" not in document


@pytest.mark.anyio
async def test_topic_match_scope_fallback_omits_client_metadata(caplog):
    from app.services.ai.topic_matcher import TopicMatcher

    matcher = TopicMatcher()
    matcher._embeddings = [
        {
            "board_slug": "ahsec",
            "class_level": "11",
            "embedding": [1.0, 0.0],
        }
    ]
    matcher._vectors = __import__("numpy").array([[1.0, 0.0]])
    matcher._last_load = float("inf")
    sentinel = "student@example.com"

    with caplog.at_level(logging.DEBUG):
        await matcher.match_topic(
            [1.0, 0.0],
            board_slug=sentinel,
            class_level=sentinel,
        )

    assert sentinel not in caplog.text
    assert "private prompt" not in caplog.text