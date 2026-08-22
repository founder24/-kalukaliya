"""Regression coverage for the Workers AI FastAPI chat compatibility path."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest


async def _stream(*chunks: str):
    for chunk in chunks:
        yield chunk


class TestWorkersAiLanguageRouting:
    @pytest.mark.parametrize(
        ("message", "override", "expected_lang"),
        [
            ("What is gravity?", "en", "en"),
            ("পোহৰ কি?", "as", "as"),
            ("পোহৰ কি?", None, "as"),
        ],
    )
    def test_language_selection_always_uses_workers_model(
        self, message, override, expected_lang
    ):
        from app.services.chat_service import ChatService

        lang, model = ChatService.resolve_language_and_model(message, override)

        assert lang == expected_lang
        assert model.startswith("@cf/")


class TestWorkersAiStreaming:
    @pytest.mark.anyio
    async def test_stream_preserves_sse_contract(self):
        from app.services.chat_service import ChatService

        with patch(
            "app.services.ai.router.stream_response",
            new=lambda **_kwargs: _stream("Workers", " AI"),
        ):
            events = [
                event
                async for event in ChatService.stream_llm(
                    system_prompt="You are helpful.",
                    sanitized_message="What is gravity?",
                    target_model="@cf/zai-org/glm-4.7-flash",
                    detected_lang="en",
                    user_id="test-user",
                    request_message="What is gravity?",
                )
            ]

        payloads = [
            json.loads(event[6:].strip())
            for event in events
            if event.startswith("data: ")
        ]
        assert [payload["content"] for payload in payloads if "content" in payload] == [
            "Workers",
            " AI",
        ]
        assert payloads[-1]["__syrabit_stream_complete_7f3a9b2e__"] is True
        assert payloads[-1]["full_response"] == "Workers AI"

    @pytest.mark.anyio
    async def test_stream_retries_through_workers_client_after_primary_failure(self):
        from app.services.chat_service import ChatService

        async def primary_failure(**_kwargs):
            raise RuntimeError("primary Worker request failed")
            yield ""

        with (
            patch("app.services.ai.router.stream_response", new=primary_failure),
            patch(
                "app.services.ai.workers_ai_client.workers_ai_client.stream_generate_with_retry",
                new=lambda *_args, **_kwargs: _stream("retry response"),
            ),
        ):
            events = [
                event
                async for event in ChatService.stream_llm(
                    system_prompt="You are helpful.",
                    sanitized_message="পোহৰ কি?",
                    target_model="@cf/zai-org/glm-4.7-flash",
                    detected_lang="as",
                    user_id="test-user",
                    request_message="পোহৰ কি?",
                )
            ]

        assert any("retry response" in event for event in events)