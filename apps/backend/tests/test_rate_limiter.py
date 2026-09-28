"""Tests for the MongoDB-backed authentication rate limiter.

The auth rate limiter moved off Redis to the MongoDB ``auth_rate_limit``
collection (TTL-keyed per IP/minute). It uses an atomic
``find_one_and_update`` upsert and fails closed with HTTP 503 if MongoDB is
unavailable. It raises HTTP 429 when the per-minute attempt count exceeds the
configured max, and remains disabled in development.
"""

from datetime import datetime, timedelta, timezone

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
@pytest.mark.parametrize("failure_point", ["client", "counter"])
async def test_rate_limit_fails_closed_when_mongo_unavailable(failure_point):
    """Auth attempts stop with a generic 503 if the limiter store fails."""
    from app.api.v1.auth import _check_rate_limit
    from app.config import settings

    mock_request = _make_request()
    mock_client = _mongo_client_with_count(1)
    if failure_point == "counter":
        mock_client.__getitem__.return_value.auth_rate_limit.find_one_and_update = (
            AsyncMock(side_effect=TimeoutError("private database detail"))
        )

    with patch.object(settings, "APP_ENV", "production"):
        get_client_patch = patch(
            "app.db.mongo.get_mongo_client",
            side_effect=RuntimeError("private database detail"),
        ) if failure_point == "client" else patch(
            "app.db.mongo.get_mongo_client",
            return_value=mock_client,
        )
        with get_client_patch:
            with pytest.raises(HTTPException) as exc_info:
                await _check_rate_limit(mock_request, "login", 10)

    assert exc_info.value.status_code == 503
    assert "temporarily unavailable" in exc_info.value.detail.lower()
    assert "private database detail" not in exc_info.value.detail


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
async def test_rate_limit_uses_atomic_endpoint_ip_minute_key_and_ttl():
    """Keep the atomic counter scoped to one endpoint, client IP, and minute."""
    from app.api.v1.auth import _check_rate_limit
    from app.config import settings

    mock_request = _make_request()
    mock_request.headers = {"CF-Connecting-IP": "203.0.113.9"}
    mock_client = _mongo_client_with_count(2)
    mock_collection = mock_client.__getitem__.return_value.auth_rate_limit
    before = datetime.now(timezone.utc)

    with (
        patch.object(settings, "APP_ENV", "production"),
        patch("app.api.v1.auth.time.time", return_value=1_800),
        patch("app.db.mongo.get_mongo_client", return_value=mock_client),
    ):
        await _check_rate_limit(mock_request, "login", 10)

    after = datetime.now(timezone.utc)
    mock_collection.find_one_and_update.assert_awaited_once()
    filters, update = mock_collection.find_one_and_update.await_args.args
    assert filters == {"_id": "login:203.0.113.9:30"}
    assert update["$inc"] == {"count": 1}
    assert before + timedelta(seconds=90) <= update["$setOnInsert"]["expires_at"]
    assert update["$setOnInsert"]["expires_at"] <= after + timedelta(seconds=90)
    assert mock_collection.find_one_and_update.await_args.kwargs["upsert"] is True


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
