import pytest
from httpx import AsyncClient
from unittest.mock import patch, AsyncMock, MagicMock


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


# ═══════════════════════════════════════════════════════════════
# OCR / IMAGE ANALYSIS TESTS
# ═══════════════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_image_endpoint_requires_auth(client: AsyncClient):
    """POST /api/v1/chat/image without auth returns 401 or 403"""
    response = await client.post(
        "/api/v1/chat/image",
        files={"file": ("test.png", b"fake-image-data", "image/png")},
    )
    assert response.status_code in (401, 403)


@pytest.mark.anyio
async def test_image_endpoint_success(client: AsyncClient, mock_user):
    """POST /api/v1/chat/image with mocked auth and vision returns 200"""
    from app.main import app
    from app.api.v1.auth import get_current_user

    app.dependency_overrides[get_current_user] = lambda: mock_user
    try:
        with (
            patch(
                "app.api.v1.chat.check_rate_limit",
                new_callable=AsyncMock,
                return_value=(True, 1, 100, "monthly"),
            ),
            patch(
                "app.api.v1.chat.cloudflare_client.vision_analyze",
                new_callable=AsyncMock,
                return_value="Hello World",
            ),
        ):
            response = await client.post(
                "/api/v1/chat/image",
                files={"file": ("test.png", b"fake-image-data", "image/png")},
                data={"prompt": "Extract all text from this image"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["text"] == "Hello World"
            assert "model" in data
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ═══════════════════════════════════════════════════════════════
# TTS TESTS
# ═══════════════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_tts_endpoint_success(client: AsyncClient, mock_user):
    """POST /api/v1/chat/tts with mocked auth and TTS returns audio/wav"""
    from app.main import app
    from app.api.v1.auth import get_current_user

    app.dependency_overrides[get_current_user] = lambda: mock_user
    try:
        with (
            patch(
                "app.api.v1.chat.check_rate_limit",
                new_callable=AsyncMock,
                return_value=(True, 1, 100, "monthly"),
            ),
            patch(
                "app.api.v1.chat.cloudflare_client.text_to_speech",
                new_callable=AsyncMock,
                return_value=b"fake-audio",
            ),
        ):
            response = await client.post(
                "/api/v1/chat/tts",
                json={"text": "Hello world", "lang": "en"},
            )
            assert response.status_code == 200
            assert response.headers["content-type"] == "audio/wav"
            assert response.content == b"fake-audio"
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.anyio
async def test_tts_endpoint_empty_text_rejected(client: AsyncClient, mock_user):
    """POST /api/v1/chat/tts with empty text returns 422"""
    from app.main import app
    from app.api.v1.auth import get_current_user

    app.dependency_overrides[get_current_user] = lambda: mock_user
    try:
        response = await client.post(
            "/api/v1/chat/tts",
            json={"text": "", "lang": "en"},
        )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ═══════════════════════════════════════════════════════════════
# RATE LIMIT ENFORCEMENT TESTS
# ═══════════════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_image_rate_limit_returns_429(client: AsyncClient, mock_user):
    """POST /api/v1/chat/image with rate limit exceeded returns 429"""
    from app.main import app
    from app.api.v1.auth import get_current_user

    app.dependency_overrides[get_current_user] = lambda: mock_user
    try:
        with patch(
            "app.api.v1.chat.check_rate_limit",
            new_callable=AsyncMock,
            return_value=(False, 101, 100, "monthly"),
        ):
            response = await client.post(
                "/api/v1/chat/image",
                files={"file": ("test.png", b"fake-image-data", "image/png")},
            )
            assert response.status_code == 429
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.anyio
async def test_tts_rate_limit_returns_429(client: AsyncClient, mock_user):
    """POST /api/v1/chat/tts with rate limit exceeded returns 429"""
    from app.main import app
    from app.api.v1.auth import get_current_user

    app.dependency_overrides[get_current_user] = lambda: mock_user
    try:
        with patch(
            "app.api.v1.chat.check_rate_limit",
            new_callable=AsyncMock,
            return_value=(False, 101, 100, "monthly"),
        ):
            response = await client.post(
                "/api/v1/chat/tts",
                json={"text": "Hello world", "lang": "en"},
            )
            assert response.status_code == 429
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ═══════════════════════════════════════════════════════════════
# FILE VALIDATION TESTS
# ═══════════════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_image_non_image_content_type_rejected(client: AsyncClient, mock_user):
    """POST /api/v1/chat/image with non-image content type returns 400"""
    from app.main import app
    from app.api.v1.auth import get_current_user

    app.dependency_overrides[get_current_user] = lambda: mock_user
    try:
        with patch(
            "app.api.v1.chat.check_rate_limit",
            new_callable=AsyncMock,
            return_value=(True, 1, 100, "monthly"),
        ):
            response = await client.post(
                "/api/v1/chat/image",
                files={"file": ("test.txt", b"not an image", "text/plain")},
            )
            assert response.status_code == 400
            assert "image" in response.json()["detail"].lower()
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.anyio
async def test_image_oversized_file_rejected(client: AsyncClient, mock_user):
    """POST /api/v1/chat/image with file over 4MB returns 400"""
    from app.main import app
    from app.api.v1.auth import get_current_user

    app.dependency_overrides[get_current_user] = lambda: mock_user
    try:
        with patch(
            "app.api.v1.chat.check_rate_limit",
            new_callable=AsyncMock,
            return_value=(True, 1, 100, "monthly"),
        ):
            # Create a file slightly over 4MB
            oversized_data = b"x" * (4 * 1024 * 1024 + 1)
            response = await client.post(
                "/api/v1/chat/image",
                files={"file": ("big.png", oversized_data, "image/png")},
            )
            assert response.status_code == 400
            assert "4MB" in response.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_current_user, None)
