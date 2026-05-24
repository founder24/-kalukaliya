"""Tests for the auth rate limiter fail-closed behavior."""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_rate_limit_returns_503_when_redis_unavailable():
    """Verify _check_rate_limit raises 503 when Redis is down (fail-closed)."""
    from app.api.v1.auth import _check_rate_limit

    mock_request = MagicMock()
    mock_request.client.host = "127.0.0.1"

    with patch(
        "app.db.redis.get_redis", side_effect=ConnectionError("Redis unavailable")
    ):
        with pytest.raises(HTTPException) as exc_info:
            await _check_rate_limit(mock_request, "login", 10)
        assert exc_info.value.status_code == 503
        assert "Rate limiting service unavailable" in exc_info.value.detail


@pytest.mark.asyncio
async def test_rate_limit_returns_429_when_limit_exceeded():
    """Verify _check_rate_limit raises 429 when attempts exceed max."""
    from app.api.v1.auth import _check_rate_limit

    mock_request = MagicMock()
    mock_request.client.host = "127.0.0.1"

    mock_redis = AsyncMock()
    # Simulate exceeding the limit (11th attempt when max is 10)
    mock_redis.incr.return_value = 11
    mock_redis.expire.return_value = True

    with patch("app.db.redis.get_redis", return_value=mock_redis):
        with pytest.raises(HTTPException) as exc_info:
            await _check_rate_limit(mock_request, "login", 10)
        assert exc_info.value.status_code == 429
        assert "Too many login attempts" in exc_info.value.detail


@pytest.mark.asyncio
async def test_rate_limit_allows_request_within_limit():
    """Verify _check_rate_limit allows requests within the limit."""
    from app.api.v1.auth import _check_rate_limit

    mock_request = MagicMock()
    mock_request.client.host = "127.0.0.1"

    mock_redis = AsyncMock()
    mock_redis.incr.return_value = 5  # Within the limit of 10
    mock_redis.expire.return_value = True

    with patch("app.db.redis.get_redis", return_value=mock_redis):
        # Should not raise
        await _check_rate_limit(mock_request, "login", 10)
