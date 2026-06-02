"""
PR 358 Chat Audit Tests

Comprehensive integration tests covering:
1. English mode streaming (SSE format, Vertex routing, prompt enforcement)
2. Assamese mode streaming (Sarvam routing, Vertex fallback, Assamese prompt)
3. RAG unavailable fallback (graceful degradation)
4. Chat response speed (latency with mocked services)
5. Lang field override (PR 358 fix: explicit lang overrides auto-detection)
"""

import json
import time
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from httpx import AsyncClient, ASGITransport


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_redis():
    """Create a mock Redis instance with async methods."""
    mock = MagicMock()
    mock.get = AsyncMock(return_value=None)
    mock.set = AsyncMock()
    mock.delete = AsyncMock()
    mock.incr = AsyncMock(return_value=1)
    mock.expire = AsyncMock()
    mock.pipeline = MagicMock(return_value=MagicMock(
        delete=MagicMock(),
        execute=AsyncMock(return_value=[]),
    ))
    return mock


def _mock_tracer():
    """Create a no-op tracer with a span context manager."""
    span = MagicMock()
    span.set_attribute = MagicMock()
    span.__enter__ = MagicMock(return_value=span)
    span.__exit__ = MagicMock(return_value=False)
    tracer = MagicMock()
    tracer.start_as_current_span = MagicMock(return_value=span)
    return tracer


async def _make_stream_generator(*chunks):
    """Create an async generator yielding text chunks."""
    for chunk in chunks:
        yield chunk


async def _raise_before_yield(exc):
    """Async generator helper that raises an exception before yielding.

    Used to simulate provider failures in stream tests. The yield after
    the raise is unreachable but required for Python to treat this as an
    async generator.
    """
    raise exc
    yield  # noqa: unreachable - needed for async generator type


def _parse_sse_events(body: str) -> list[dict]:
    """Parse SSE response body into list of JSON event dicts."""
    events = []
    for line in body.split("\n"):
        line = line.strip()
        if line.startswith("data: "):
            raw = line[6:]
            try:
                events.append(json.loads(raw))
            except (json.JSONDecodeError, ValueError):
                pass
    return events


def _common_patches(mock_redis_instance=None, generate_result=None):
    """Return dict of common patches for chat endpoint tests.

    NOTE: Callers must patch ``search_service`` and ``stream_response``
    (or ``generate_response``) themselves, since every test provides its
    own mock for these.
    """
    if mock_redis_instance is None:
        mock_redis_instance = _mock_redis()
    if generate_result is None:
        generate_result = "This is a test response."

    return {
        "rate_limit": patch(
            "app.api.deps.rate_limit.check_rate_limit",
            new_callable=AsyncMock,
            return_value=(True, 1, 100, "monthly"),
        ),
        "redis": patch(
            "app.db.redis.get_redis",
            return_value=mock_redis_instance,
        ),
        "auth": patch(
            "app.api.v1.auth.get_current_user_optional",
            new_callable=AsyncMock,
            return_value=None,
        ),
        "topic_match": patch(
            "app.services.chat_service.ChatService.check_topic_match",
            new_callable=AsyncMock,
            return_value={"topic_title": "Test", "score": 0.85},
        ),
        "generate_response": patch(
            "app.services.ai.router.generate_response",
            new_callable=AsyncMock,
            return_value=generate_result,
        ),
        "posthog": patch(
            "app.utils.tracking.track_chat_completed",
            new_callable=AsyncMock,
        ),
        "token_budget": patch(
            "app.core.token_budget.truncate_chunks_to_budget",
            side_effect=lambda chunks, **kwargs: chunks,
        ),
        "tracer": patch(
            "app.core.telemetry.get_tracer",
            return_value=_mock_tracer(),
        ),
        "save_chat": patch(
            "app.services.chat_service.ChatService.save_chat",
            new_callable=AsyncMock,
        ),
        "user_find": patch(
            "app.models.user.User.find_one",
            new_callable=AsyncMock,
            return_value=None,
        ),
        "user_get": patch(
            "app.models.user.User.get",
            new_callable=AsyncMock,
            return_value=None,
        ),
    }


# ---------------------------------------------------------------------------
# 1. TestEnglishModeChatStream
# ---------------------------------------------------------------------------


class TestEnglishModeChatStream:
    """Tests English mode chat end-to-end."""

    @pytest.mark.asyncio
    async def test_english_stream_returns_correct_sse_format(self):
        """POST /api/v1/chat/stream with lang='en' returns proper SSE events."""
        from app.main import app

        chunks = ["Photosynthesis", " is the process", " of converting light."]

        async def _stream_gen(system_prompt, user_message, model):
            for c in chunks:
                yield c

        patches = _common_patches()

        mock_search = MagicMock()
        mock_search.is_available.return_value = False

        with (
            patches["rate_limit"],
            patches["redis"],
            patches["auth"],
            patches["topic_match"],
            patch("app.services.chat_service.search_service", mock_search),
            patch("app.services.ai.router.stream_response", side_effect=_stream_gen),
            patches["posthog"],
            patches["token_budget"],
            patches["tracer"],
            patches["save_chat"],
            patches["user_find"],
            patches["user_get"],
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                response = await ac.post(
                    "/api/v1/chat/stream",
                    json={"message": "What is photosynthesis?", "lang": "en"},
                )

        assert response.status_code == 200
        events = _parse_sse_events(response.text)

        # Should have content events (done=False) followed by a final done event
        content_events = [e for e in events if e.get("done") is False]
        done_events = [e for e in events if e.get("done") is True]

        assert len(content_events) >= 1
        assert len(done_events) == 1

        # Content events have 'content' field with correct mocked text
        for ev in content_events:
            assert "content" in ev
        assert content_events[0]["content"] == "Photosynthesis"

        # Final event has lang and model
        final = done_events[0]
        assert final.get("lang") == "en"
        assert "gemini" in final.get("model", "").lower()

    @pytest.mark.asyncio
    async def test_english_stream_routes_to_vertex(self):
        """When lang='en', stream_response is called with the gemini model."""
        from app.main import app

        called_models = []

        async def _capture_stream(system_prompt, user_message, model):
            called_models.append(model)
            yield "test chunk"

        patches = _common_patches()
        mock_search = MagicMock()
        mock_search.is_available.return_value = False

        with (
            patches["rate_limit"],
            patches["redis"],
            patches["auth"],
            patches["topic_match"],
            patch("app.services.chat_service.search_service", mock_search),
            patch("app.services.ai.router.stream_response", side_effect=_capture_stream),
            patches["posthog"],
            patches["token_budget"],
            patches["tracer"],
            patches["save_chat"],
            patches["user_find"],
            patches["user_get"],
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                await ac.post(
                    "/api/v1/chat/stream",
                    json={"message": "Explain gravity", "lang": "en"},
                )

        assert len(called_models) == 1
        assert "gemini" in called_models[0].lower()

    def test_english_system_prompt_enforces_english(self):
        """build_system_prompt('en', []) includes English enforcement."""
        from app.services.chat_service import ChatService

        prompt = ChatService.build_system_prompt("en", [])
        assert "You MUST respond in English only" in prompt


# ---------------------------------------------------------------------------
# 2. TestAssameseModeChatStream
# ---------------------------------------------------------------------------


class TestAssameseModeChatStream:
    """Tests Assamese mode chat end-to-end."""

    @pytest.mark.asyncio
    async def test_assamese_stream_routes_to_sarvam(self):
        """POST /api/v1/chat/stream with lang='as' routes to Sarvam model."""
        from app.main import app

        called_models = []

        async def _capture_stream(system_prompt, user_message, model):
            called_models.append(model)
            yield "\u09aa\u09cb\u09b9\u09f0"

        patches = _common_patches()
        mock_search = MagicMock()
        mock_search.is_available.return_value = False

        with (
            patches["rate_limit"],
            patches["redis"],
            patches["auth"],
            patches["topic_match"],
            patch("app.services.chat_service.search_service", mock_search),
            patch("app.services.ai.router.stream_response", side_effect=_capture_stream),
            patches["posthog"],
            patches["token_budget"],
            patches["tracer"],
            patches["save_chat"],
            patches["user_find"],
            patches["user_get"],
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                response = await ac.post(
                    "/api/v1/chat/stream",
                    json={
                        "message": "\u09aa\u09cb\u09b9\u09f0 \u09b8\u0982\u09b6\u09cd\u09b2\u09c7\u09b7\u09a3 \u0995\u09bf?",
                        "lang": "as",
                    },
                )

        assert response.status_code == 200
        events = _parse_sse_events(response.text)
        done_events = [e for e in events if e.get("done") is True]
        assert len(done_events) == 1
        assert done_events[0].get("lang") == "as"

        # Verify the model was Sarvam
        assert len(called_models) == 1
        assert "sarvam" in called_models[0].lower() or "openhathi" in called_models[0].lower()

    @pytest.mark.asyncio
    async def test_assamese_fallback_to_vertex_on_sarvam_failure(self):
        """When Sarvam fails for lang='as', falls back to Vertex AI."""
        from app.main import app

        async def _vertex_fallback(system_prompt, user_message):
            yield "Fallback "
            yield "response"

        patches = _common_patches()
        mock_search = MagicMock()
        mock_search.is_available.return_value = False

        mock_vertex = MagicMock()
        mock_vertex.stream_generate_with_retry = _vertex_fallback

        with (
            patches["rate_limit"],
            patches["redis"],
            patches["auth"],
            patches["topic_match"],
            patch("app.services.chat_service.search_service", mock_search),
            patch(
                "app.services.ai.router.stream_response",
                side_effect=lambda *a, **kw: _raise_before_yield(RuntimeError("Sarvam API timeout")),
            ),
            patch("app.services.ai.vertex_client.vertex_client", mock_vertex),
            patches["posthog"],
            patches["token_budget"],
            patches["tracer"],
            patches["save_chat"],
            patches["user_find"],
            patches["user_get"],
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                response = await ac.post(
                    "/api/v1/chat/stream",
                    json={
                        "message": "\u09aa\u09cb\u09b9\u09f0 \u09b8\u0982\u09b6\u09cd\u09b2\u09c7\u09b7\u09a3 \u0995\u09bf?",
                        "lang": "as",
                    },
                )

        assert response.status_code == 200
        events = _parse_sse_events(response.text)

        # Should have a fallback event
        fallback_events = [e for e in events if e.get("fallback") is True]
        assert len(fallback_events) == 1
        assert fallback_events[0].get("provider") == "vertex"

        # Should have content events from the vertex fallback
        content_events = [e for e in events if "content" in e and e.get("done") is not True]
        assert len(content_events) >= 1
        assert content_events[0]["content"] == "Fallback "

        # Verify the vertex mock was actually invoked as the fallback path
        # (stream_generate_with_retry is a plain async generator, not a Mock,
        # so we check via the content events above confirming vertex yielded data)

    def test_assamese_system_prompt_uses_assamese_script(self):
        """build_system_prompt('as', []) uses Assamese script, not English enforcement."""
        from app.services.chat_service import ChatService

        prompt = ChatService.build_system_prompt("as", [])
        assert "You MUST respond in English only" not in prompt
        # Should contain Assamese Unicode characters (U+0980-U+09FF)
        import re
        assamese_chars = re.findall(r"[\u0980-\u09FF]", prompt)
        assert len(assamese_chars) > 0, "Assamese prompt should contain Assamese script"


# ---------------------------------------------------------------------------
# 3. TestRAGUnavailableFallback
# ---------------------------------------------------------------------------


class TestRAGUnavailableFallback:
    """Tests graceful degradation when RAG is unavailable."""

    @pytest.mark.asyncio
    async def test_chat_responds_when_search_service_unavailable(self):
        """When search_service.is_available() is False, stream still completes."""
        from app.main import app

        async def _stream_gen(system_prompt, user_message, model):
            yield "Answer from LLM base knowledge"

        patches = _common_patches()
        mock_search = MagicMock()
        mock_search.is_available.return_value = False

        with (
            patches["rate_limit"],
            patches["redis"],
            patches["auth"],
            patches["topic_match"],
            patch("app.services.chat_service.search_service", mock_search),
            patch("app.services.ai.router.stream_response", side_effect=_stream_gen),
            patches["posthog"],
            patches["token_budget"],
            patches["tracer"],
            patches["save_chat"],
            patches["user_find"],
            patches["user_get"],
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                response = await ac.post(
                    "/api/v1/chat/stream",
                    json={"message": "What is the capital of Assam?", "lang": "en"},
                )

        assert response.status_code == 200
        events = _parse_sse_events(response.text)

        # Should have content events and a done event, no error events
        error_events = [e for e in events if "error" in e]
        done_events = [e for e in events if e.get("done") is True]
        content_events = [e for e in events if "content" in e and e.get("done") is not True]

        assert len(error_events) == 0
        assert len(done_events) == 1
        assert len(content_events) >= 1
        assert content_events[0]["content"] == "Answer from LLM base knowledge"

    @pytest.mark.asyncio
    async def test_chat_responds_when_search_returns_empty(self):
        """When search_context returns [], stream still completes."""
        from app.main import app

        async def _stream_gen(system_prompt, user_message, model):
            yield "LLM response without RAG"

        patches = _common_patches()
        mock_search = MagicMock()
        mock_search.is_available.return_value = True
        mock_search.search_context = AsyncMock(return_value=[])

        with (
            patches["rate_limit"],
            patches["redis"],
            patches["auth"],
            patches["topic_match"],
            patch("app.services.chat_service.search_service", mock_search),
            patch("app.services.ai.router.stream_response", side_effect=_stream_gen),
            patches["posthog"],
            patches["token_budget"],
            patches["tracer"],
            patches["save_chat"],
            patches["user_find"],
            patches["user_get"],
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                response = await ac.post(
                    "/api/v1/chat/stream",
                    json={"message": "Tell me about HSLC exams", "lang": "en"},
                )

        assert response.status_code == 200
        events = _parse_sse_events(response.text)
        error_events = [e for e in events if "error" in e]
        done_events = [e for e in events if e.get("done") is True]

        assert len(error_events) == 0
        assert len(done_events) == 1

    @pytest.mark.asyncio
    async def test_chat_responds_when_search_raises_exception(self):
        """When search_context raises, retrieve_context catches it and returns []."""
        from app.main import app

        async def _stream_gen(system_prompt, user_message, model):
            yield "Response despite search failure"

        patches = _common_patches()
        mock_search = MagicMock()
        mock_search.is_available.return_value = True
        mock_search.search_context = AsyncMock(side_effect=Exception("Search API down"))

        with (
            patches["rate_limit"],
            patches["redis"],
            patches["auth"],
            patches["topic_match"],
            patch("app.services.chat_service.search_service", mock_search),
            patch("app.services.ai.router.stream_response", side_effect=_stream_gen),
            patches["posthog"],
            patches["token_budget"],
            patches["tracer"],
            patches["save_chat"],
            patches["user_find"],
            patches["user_get"],
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                response = await ac.post(
                    "/api/v1/chat/stream",
                    json={"message": "What are AHSEC results?", "lang": "en"},
                )

        assert response.status_code == 200
        events = _parse_sse_events(response.text)
        error_events = [e for e in events if "error" in e]
        done_events = [e for e in events if e.get("done") is True]
        content_events = [e for e in events if "content" in e and e.get("done") is not True]

        assert len(error_events) == 0
        assert len(done_events) == 1
        assert len(content_events) >= 1
        assert content_events[0]["content"] == "Response despite search failure"

    def test_system_prompt_without_rag_context(self):
        """build_system_prompt('en', []) produces a prompt without citation instructions."""
        from app.services.chat_service import ChatService

        prompt = ChatService.build_system_prompt("en", [])
        # Without RAG context, prompt should not mention numbered citations
        assert "Context:" not in prompt
        assert "[1]" not in prompt
        # But should still be a useful prompt
        assert "Syrabit" in prompt


# ---------------------------------------------------------------------------
# 4. TestChatResponseSpeed
# ---------------------------------------------------------------------------


class TestChatResponseSpeed:
    """Latency tests with mocked services to verify no unnecessary waits."""

    @pytest.mark.asyncio
    async def test_stream_first_chunk_latency(self):
        """First SSE data chunk should arrive within 1000ms with mocked services."""
        from app.main import app

        async def _stream_gen(system_prompt, user_message, model):
            yield "First chunk"
            yield " second chunk"

        patches = _common_patches()
        mock_search = MagicMock()
        mock_search.is_available.return_value = False

        with (
            patches["rate_limit"],
            patches["redis"],
            patches["auth"],
            patches["topic_match"],
            patch("app.services.chat_service.search_service", mock_search),
            patch("app.services.ai.router.stream_response", side_effect=_stream_gen),
            patches["posthog"],
            patches["token_budget"],
            patches["tracer"],
            patches["save_chat"],
            patches["user_find"],
            patches["user_get"],
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                start = time.time()
                response = await ac.post(
                    "/api/v1/chat/stream",
                    json={"message": "Hello", "lang": "en"},
                )
                first_chunk_time = time.time() - start

        # With all mocks returning instantly, first response should be fast
        # Threshold set to 1000ms to avoid flakiness under CI runner load
        assert first_chunk_time < 1.000, (
            f"First chunk took {first_chunk_time*1000:.0f}ms, expected < 1000ms"
        )

    @pytest.mark.asyncio
    async def test_stream_total_completion_time(self):
        """Total stream completion should be within 2000ms with mocked services."""
        from app.main import app

        async def _stream_gen(system_prompt, user_message, model):
            yield "chunk1"
            yield " chunk2"
            yield " chunk3"

        patches = _common_patches()
        mock_search = MagicMock()
        mock_search.is_available.return_value = False

        with (
            patches["rate_limit"],
            patches["redis"],
            patches["auth"],
            patches["topic_match"],
            patch("app.services.chat_service.search_service", mock_search),
            patch("app.services.ai.router.stream_response", side_effect=_stream_gen),
            patches["posthog"],
            patches["token_budget"],
            patches["tracer"],
            patches["save_chat"],
            patches["user_find"],
            patches["user_get"],
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                start = time.time()
                response = await ac.post(
                    "/api/v1/chat/stream",
                    json={"message": "Tell me something", "lang": "en"},
                )
                total_time = time.time() - start

        assert response.status_code == 200
        events = _parse_sse_events(response.text)
        done_events = [e for e in events if e.get("done") is True]
        assert len(done_events) == 1

        # Threshold set to 2000ms to avoid flakiness under CI runner load
        assert total_time < 2.000, (
            f"Total stream took {total_time*1000:.0f}ms, expected < 2000ms"
        )

    @pytest.mark.asyncio
    async def test_non_streaming_response_latency(self):
        """Non-streaming POST /api/v1/chat/ should complete in < 2000ms."""
        from app.main import app

        patches = _common_patches(generate_result="A quick test answer.")
        mock_search = MagicMock()
        mock_search.is_available.return_value = False

        with (
            patches["rate_limit"],
            patches["redis"],
            patches["auth"],
            patches["topic_match"],
            patch("app.services.chat_service.search_service", mock_search),
            patches["generate_response"],
            patches["posthog"],
            patches["token_budget"],
            patches["tracer"],
            patches["save_chat"],
            patches["user_find"],
            patches["user_get"],
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                start = time.time()
                response = await ac.post(
                    "/api/v1/chat/",
                    json={"message": "What is gravity?", "lang": "en"},
                )
                elapsed = time.time() - start

        assert response.status_code == 200
        data = response.json()
        assert data["latency_ms"] < 2000
        # Threshold set to 2000ms to avoid flakiness under CI runner load
        assert elapsed < 2.000, (
            f"Non-streaming response took {elapsed*1000:.0f}ms, expected < 2000ms"
        )


# ---------------------------------------------------------------------------
# 5. TestLangFieldOverride
# ---------------------------------------------------------------------------


class TestLangFieldOverride:
    """Tests that the lang field correctly overrides auto-detection (PR 358 fix)."""

    @pytest.mark.asyncio
    async def test_lang_en_overrides_assamese_text(self):
        """Sending Assamese text with lang='en' routes to Vertex/gemini."""
        from app.main import app

        called_models = []

        async def _capture_stream(system_prompt, user_message, model):
            called_models.append(model)
            yield "English response"

        patches = _common_patches()
        mock_search = MagicMock()
        mock_search.is_available.return_value = False

        with (
            patches["rate_limit"],
            patches["redis"],
            patches["auth"],
            patches["topic_match"],
            patch("app.services.chat_service.search_service", mock_search),
            patch("app.services.ai.router.stream_response", side_effect=_capture_stream),
            patches["posthog"],
            patches["token_budget"],
            patches["tracer"],
            patches["save_chat"],
            patches["user_find"],
            patches["user_get"],
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                response = await ac.post(
                    "/api/v1/chat/stream",
                    json={
                        "message": "\u0986\u09aa\u09c1\u09a8\u09bf \u0995\u09c7\u09a8\u09c7\u0995\u09c8 \u0986\u099b\u09c7?",
                        "lang": "en",
                    },
                )

        assert response.status_code == 200
        # Should route to Gemini (Vertex), not Sarvam
        assert len(called_models) == 1
        assert "gemini" in called_models[0].lower()

    @pytest.mark.asyncio
    async def test_lang_as_overrides_english_text(self):
        """Sending English text with lang='as' routes to Sarvam model."""
        from app.main import app

        called_models = []

        async def _capture_stream(system_prompt, user_message, model):
            called_models.append(model)
            yield "\u09aa\u09cb\u09b9\u09f0"

        patches = _common_patches()
        mock_search = MagicMock()
        mock_search.is_available.return_value = False

        with (
            patches["rate_limit"],
            patches["redis"],
            patches["auth"],
            patches["topic_match"],
            patch("app.services.chat_service.search_service", mock_search),
            patch("app.services.ai.router.stream_response", side_effect=_capture_stream),
            patches["posthog"],
            patches["token_budget"],
            patches["tracer"],
            patches["save_chat"],
            patches["user_find"],
            patches["user_get"],
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                response = await ac.post(
                    "/api/v1/chat/stream",
                    json={
                        "message": "What is photosynthesis?",
                        "lang": "as",
                    },
                )

        assert response.status_code == 200
        # Should route to Sarvam, not Vertex
        assert len(called_models) == 1
        assert "sarvam" in called_models[0].lower() or "openhathi" in called_models[0].lower()

    @pytest.mark.asyncio
    async def test_lang_null_falls_back_to_detection(self):
        """When lang is null, auto-detection picks English for English text."""
        from app.main import app

        called_models = []

        async def _capture_stream(system_prompt, user_message, model):
            called_models.append(model)
            yield "Auto detected"

        patches = _common_patches()
        mock_search = MagicMock()
        mock_search.is_available.return_value = False

        with (
            patches["rate_limit"],
            patches["redis"],
            patches["auth"],
            patches["topic_match"],
            patch("app.services.chat_service.search_service", mock_search),
            patch("app.services.ai.router.stream_response", side_effect=_capture_stream),
            patches["posthog"],
            patches["token_budget"],
            patches["tracer"],
            patches["save_chat"],
            patches["user_find"],
            patches["user_get"],
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                response = await ac.post(
                    "/api/v1/chat/stream",
                    json={
                        "message": "Explain quantum computing in simple terms",
                        "lang": None,
                    },
                )

        assert response.status_code == 200
        # English text with no override should detect English and route to Vertex
        assert len(called_models) == 1
        assert "gemini" in called_models[0].lower()

    @pytest.mark.asyncio
    async def test_invalid_lang_rejected(self):
        """Sending lang='fr' (invalid) should return 422 validation error."""
        from app.main import app

        async def _stream_gen(system_prompt, user_message, model):
            yield "should not reach"

        patches = _common_patches()
        mock_search = MagicMock()
        mock_search.is_available.return_value = False

        with (
            patches["rate_limit"],
            patches["redis"],
            patches["auth"],
            patches["topic_match"],
            patch("app.services.chat_service.search_service", mock_search),
            patch("app.services.ai.router.stream_response", side_effect=_stream_gen),
            patches["posthog"],
            patches["token_budget"],
            patches["tracer"],
            patches["save_chat"],
            patches["user_find"],
            patches["user_get"],
        ):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                response = await ac.post(
                    "/api/v1/chat/stream",
                    json={
                        "message": "Bonjour le monde",
                        "lang": "fr",
                    },
                )

        assert response.status_code == 422
