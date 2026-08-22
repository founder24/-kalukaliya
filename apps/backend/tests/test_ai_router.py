import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("JWT_SECRET", "test-secret-at-least-32-characters-long")
os.environ.setdefault("APP_ENV", "development")


@pytest.mark.anyio
async def test_generate_response_uses_workers_ai_for_every_model_name():
    """Compatibility model names must never route generation to a legacy SDK."""
    generated = AsyncMock(return_value="Workers response")

    with patch("app.services.ai.workers_ai_client.generate_with_workers_ai", generated):
        from app.services.ai.router import generate_response

        result = await generate_response(
            system_prompt="You are helpful.",
            user_message="Hello",
            model="legacy-model-name",
        )

    assert result == "Workers response"
    generated.assert_awaited_once()
    assert generated.await_args.kwargs["is_assamese"] is False


@pytest.mark.anyio
async def test_generate_response_marks_assamese_for_workers_ai():
    generated = AsyncMock(return_value="অসমীয়া উত্তৰ")

    with patch("app.services.ai.workers_ai_client.generate_with_workers_ai", generated):
        from app.services.ai.router import generate_response

        await generate_response("সহায়ক হওক", "পোহৰ কি?", model="any-model")

    assert generated.await_args.kwargs["is_assamese"] is True


@pytest.mark.anyio
async def test_stream_response_uses_workers_ai_stream():
    async def stream(*_args, **_kwargs):
        yield "Workers"
        yield " stream"

    client = MagicMock()
    client.stream_generate_with_retry = stream
    with patch("app.services.ai.workers_ai_client.workers_ai_client", client):
        from app.services.ai.router import stream_response

        chunks = [
            chunk async for chunk in stream_response(
                system_prompt="You are helpful.",
                user_message="Hello",
                model="legacy-model-name",
            )
        ]

    assert chunks == ["Workers", " stream"]