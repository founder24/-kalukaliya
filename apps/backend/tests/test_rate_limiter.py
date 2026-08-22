"""Tests for the auth rate limiter (MongoDB-backed, fail-open behavior).

The auth rate limiter moved off Redis to the MongoDB ``auth_rate_limit``
collection (TTL-keyed per IP/minute). It uses an atomic
``find_one_and_update`` upsert and *fails open* — if MongoDB is unavailable it
logs a warning and allows the request through, because blocking auth entirely
is worse than a brief burst (bcrypt cost + Cloudflare WAF provide outer
protection). It raises HTTP 429 when the per-minute attempt count exceeds the
configured max.
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi import HTTPException


def _make_request():
    mock_request = MagicMock()
    mock_request.client.host = "127.0.0.1"
    # Header lookups must return real strings (or "") so the IP resolution
    # logic in _check_rate_limit works with a plain MagicMock.
    mock_request.headers = {}
    return mock_request


def _mongo_client_with_count(count: int) -> MagicMock:
    """Build a mock Mongo client whose auth_rate_limit upsert returns ``count``."""
    mock_db = MagicMock()
    mock_db.auth_rate_limit.find_one_and_update = AsyncMock(
        return_value={"count": count}
    )
    mock_client = MagicMock()
    mock_client.__getitem__.return_value = mock_db
    return mock_client


@pytest.mark.asyncio
async def test_rate_limit_fails_open_when_mongo_unavailable():
    """Verify _check_rate_limit allows the request (fail-open) when Mongo is down."""
    from app.api.v1.auth import _check_rate_limit
    from app.config import settings

    mock_request = _make_request()

    with patch.object(settings, "APP_ENV", "production"):
        with patch(
            "app.db.mongo.get_mongo_client",
            side_effect=RuntimeError("MongoDB not initialized"),
        ):
            # Must NOT raise — auth rate limiting fails open on DB failure.
            await _check_rate_limit(mock_request, "login", 10)


@pytest.mark.asyncio
async def test_rate_limit_returns_429_when_limit_exceeded():
    """Verify _check_rate_limit raises 429 when attempts exceed max."""
    from app.api.v1.auth import _check_rate_limit
    from app.config import settings

    mock_request = _make_request()
    # 11th attempt when max is 10.
    mock_client = _mongo_client_with_count(11)

    with patch.object(settings, "APP_ENV", "production"):
        with patch("app.db.mongo.get_mongo_client", return_value=mock_client):
            with pytest.raises(HTTPException) as exc_info:
                await _check_rate_limit(mock_request, "login", 10)
            assert exc_info.value.status_code == 429
            assert "Too many login attempts" in exc_info.value.detail


@pytest.mark.asyncio
async def test_rate_limit_allows_request_within_limit():
    """Verify _check_rate_limit allows requests within the limit."""
    from app.api.v1.auth import _check_rate_limit
    from app.config import settings

    mock_request = _make_request()
    mock_client = _mongo_client_with_count(5)  # Within the limit of 10.

    with patch.object(settings, "APP_ENV", "production"):
        with patch("app.db.mongo.get_mongo_client", return_value=mock_client):
            # Should not raise.
            await _check_rate_limit(mock_request, "login", 10)


@pytest.mark.asyncio
async def test_rate_limit_skipped_in_development():
    """Verify _check_rate_limit is a no-op in development mode."""
    from app.api.v1.auth import _check_rate_limit
    from app.config import settings

    mock_request = _make_request()

    with patch.object(settings, "APP_ENV", "development"):
        # Mongo should never be touched; if it were, this would blow up.
        with patch(
            "app.db.mongo.get_mongo_client",
            side_effect=AssertionError("Mongo must not be called in development"),
        ):
            await _check_rate_limit(mock_request, "login", 10)
