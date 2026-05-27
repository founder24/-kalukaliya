import os

# Set required env vars before any app module imports trigger Settings()
os.environ.setdefault("JWT_SECRET", "test-secret-at-least-32-characters-long")
os.environ.setdefault("APP_ENV", "development")

import pytest
from unittest.mock import patch, AsyncMock, MagicMock


@pytest.mark.anyio
async def test_generate_response_routes_gemini_to_vertex():
    """Test that model containing 'gemini' routes to Vertex AI"""
    mock_generate = AsyncMock(return_value="vertex response")

    with patch("app.services.ai.vertex_client.generate_with_vertex", mock_generate):
        from app.services.ai.router import generate_response

        result = await generate_response(
            system_prompt="You are helpful.",
            user_message="Hello",
            model="gemini-1.5-pro",
            stream=False,
        )

    mock_generate.assert_called_once_with(
        system_prompt="You are helpful.",
        user_message="Hello",
        model="gemini-1.5-pro",
        stream=False,
    )
    assert result == "vertex response"


@pytest.mark.anyio
async def test_generate_response_routes_sarvam_to_sarvam():
    """Test that model containing 'openhathi' routes to Sarvam AI"""
    mock_generate = AsyncMock(return_value="sarvam response")

    with patch("app.services.ai.sarvam_client.generate_with_sarvam", mock_generate):
        from app.services.ai.router import generate_response

        result = await generate_response(
            system_prompt="You are helpful.",
            user_message="Hello",
            model="openhathi-7b",
            stream=False,
        )

    mock_generate.assert_called_once_with(
        system_prompt="You are helpful.",
        user_message="Hello",
        stream=False,
    )
    assert result == "sarvam response"


@pytest.mark.anyio
async def test_generate_response_routes_cloudflare_as_default():
    """Test that unrecognized model routes to Cloudflare as fallback"""
    mock_generate = AsyncMock(return_value="cloudflare response")

    with patch(
        "app.services.ai.cloudflare_client.generate_with_cloudflare", mock_generate
    ):
        from app.services.ai.router import generate_response

        result = await generate_response(
            system_prompt="You are helpful.",
            user_message="Hello",
            model="@cf/meta/llama-3.1-70b-instruct",
            stream=False,
        )

    mock_generate.assert_called_once_with(
        system_prompt="You are helpful.",
        user_message="Hello",
        model="@cf/meta/llama-3.1-70b-instruct",
        stream=False,
    )
    assert result == "cloudflare response"


@pytest.mark.anyio
async def test_stream_response_routes_gemini_to_vertex():
    """Test that streaming with model 'gemini' routes to Vertex AI"""

    async def mock_stream(*args, **kwargs):
        for chunk in ["Hello", " from", " Vertex"]:
            yield chunk

    mock_client = MagicMock()
    mock_client.stream_generate_with_retry = mock_stream

    with patch("app.services.ai.vertex_client.vertex_client", mock_client):
        from app.services.ai.router import stream_response

        chunks = []
        async for chunk in stream_response(
            system_prompt="You are helpful.",
            user_message="Hello",
            model="gemini-1.5-pro",
        ):
            chunks.append(chunk)

    assert chunks == ["Hello", " from", " Vertex"]


@pytest.mark.anyio
async def test_stream_response_routes_sarvam_to_sarvam():
    """Test that streaming with model containing 'sarvam' routes to Sarvam AI"""

    async def mock_stream(*args, **kwargs):
        for chunk in ["Hello", " from", " Sarvam"]:
            yield chunk

    mock_client = MagicMock()
    mock_client.stream_generate_with_retry = mock_stream

    with patch("app.services.ai.sarvam_client.sarvam_client", mock_client):
        from app.services.ai.router import stream_response

        chunks = []
        async for chunk in stream_response(
            system_prompt="You are helpful.",
            user_message="Hello",
            model="sarvam-2b-v0.25",
        ):
            chunks.append(chunk)

    assert chunks == ["Hello", " from", " Sarvam"]


@pytest.mark.anyio
async def test_stream_response_routes_cloudflare_as_default():
    """Test that streaming with unrecognized model routes to Cloudflare as fallback"""

    async def mock_stream(*args, **kwargs):
        for chunk in ["Hello", " from", " Cloudflare"]:
            yield chunk

    mock_client = MagicMock()
    mock_client.stream_generate = mock_stream

    with patch("app.services.ai.cloudflare_client.cloudflare_client", mock_client):
        from app.services.ai.router import stream_response

        chunks = []
        async for chunk in stream_response(
            system_prompt="You are helpful.",
            user_message="Hello",
            model="@cf/meta/llama-3.1-70b-instruct",
        ):
            chunks.append(chunk)

    assert chunks == ["Hello", " from", " Cloudflare"]
