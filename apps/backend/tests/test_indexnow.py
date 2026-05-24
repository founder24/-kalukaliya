"""Tests for the IndexNow submission endpoint."""

import pytest
from unittest.mock import patch, AsyncMock
from httpx import AsyncClient, ASGITransport


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.anyio
async def test_submit_without_secret_returns_403(client: AsyncClient):
    resp = await client.post(
        "/api/v1/indexnow/submit",
        json={"urls": ["https://syrabit.ai/test"]},
    )
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_submit_with_wrong_secret_returns_403(client: AsyncClient):
    from app.config import settings

    original_key = settings.INDEXNOW_API_KEY
    settings.INDEXNOW_API_KEY = "correct-key"
    try:
        resp = await client.post(
            "/api/v1/indexnow/submit",
            json={"urls": ["https://syrabit.ai/test"]},
            headers={"X-IndexNow-Secret": "wrong-secret"},
        )
        assert resp.status_code == 403
    finally:
        settings.INDEXNOW_API_KEY = original_key


@pytest.mark.anyio
async def test_submit_with_valid_secret_returns_200(client: AsyncClient):
    from app.config import settings

    # Temporarily set a known key for testing
    original_key = settings.INDEXNOW_API_KEY
    settings.INDEXNOW_API_KEY = "test-indexnow-key-123"

    try:
        mock_response = AsyncMock()
        mock_response.status_code = 200

        with patch("app.api.v1.indexnow.httpx.AsyncClient") as MockClient:
            mock_instance = AsyncMock()
            mock_instance.post.return_value = mock_response
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=False)
            MockClient.return_value = mock_instance

            resp = await client.post(
                "/api/v1/indexnow/submit",
                json={"urls": ["https://syrabit.ai/test", "https://syrabit.ai/library"]},
                headers={"X-IndexNow-Secret": "test-indexnow-key-123"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["submitted"] == 2
            assert data["failed"] == 0
    finally:
        settings.INDEXNOW_API_KEY = original_key
