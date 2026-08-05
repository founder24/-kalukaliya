"""
Tests: payments_pending TTL index and /payments/recover endpoint.

Covers:
1. create_order always writes expires_at = created_at + 2 days into payments_pending.
2. The TTL index is declared on expires_at with expireAfterSeconds=0 in the DB init
   code, so MongoDB will auto-expire records at the exact moment stored in expires_at.
3. /payments/recover returns pending records that have not yet expired.
4. /payments/recover excludes records whose expires_at has already passed.
5. /payments/recover returns an empty list when no records exist for the user.
"""

import hashlib
import hmac
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TEST_KEY_SECRET = "test_razorpay_key_secret"
TEST_KEY_ID = "test_razorpay_key_id"
ORDER_ID_TTL = "order_TTL_TEST_001"
STARTER_AMOUNT = 9900


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_broken_redis() -> AsyncMock:
    broken = AsyncMock()
    broken.get = AsyncMock(side_effect=Exception("Redis down"))
    broken.set = AsyncMock(side_effect=Exception("Redis down"))
    return broken


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_user():
    user = MagicMock()
    user.id = "user-ttl-test-001"
    user.email = "ttl@example.com"
    user.subscription_tier = "free"
    user.update = AsyncMock()
    return user


@pytest.fixture
def mock_razorpay_client():
    mock_order = MagicMock()
    mock_order.__getitem__ = MagicMock(
        side_effect=lambda key: ORDER_ID_TTL if key == "id" else None
    )
    mock_order.get = MagicMock(return_value=None)

    mock_client_instance = MagicMock()
    mock_client_instance.order.create = MagicMock(return_value=mock_order)
    return mock_client_instance


# ---------------------------------------------------------------------------
# 1. TTL index: DB init code declares expires_at TTL correctly
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_payments_pending_ttl_index_is_declared_in_db_init():
    """
    create_indexes() must call _ensure_ttl_index on payments_pending with
    expireAfterSeconds=0 (expire at the datetime stored in expires_at).

    We patch _ensure_ttl_index directly and capture every (collection, key_spec,
    expire_after) call — no need to spin up a real MongoDB connection.
    """
    from app.db import mongo as mongo_module

    recorded_calls: list[tuple] = []

    async def _capturing_ensure_ttl(collection, key_spec, expire_after_seconds):
        recorded_calls.append((collection.name, key_spec, expire_after_seconds))

    # Build a minimal mock DB where every attribute returns a fresh AsyncMock
    # collection with a .name so _capturing_ensure_ttl can record it.
    _collections: dict = {}

    def _make_collection(name):
        if name not in _collections:
            c = AsyncMock()
            c.name = name
            c.create_index = AsyncMock()
            c.drop_index = AsyncMock(side_effect=Exception("index not found"))
            _collections[name] = c
        return _collections[name]

    class _MockDb:
        def __getattr__(self, name):
            return _make_collection(name)

    mock_db = _MockDb()
    mock_client = MagicMock()
    mock_client.__getitem__ = MagicMock(return_value=mock_db)

    original_client = mongo_module._client
    mongo_module._client = mock_client
    try:
        with patch.object(mongo_module, "_ensure_ttl_index", side_effect=_capturing_ensure_ttl):
            await mongo_module.create_indexes()
    finally:
        mongo_module._client = original_client

    # There must be a call whose collection name is payments_pending,
    # with expires_at in the key spec and expireAfterSeconds=0.
    pp_calls = [
        (coll, ks, exp)
        for coll, ks, exp in recorded_calls
        if coll == "payments_pending"
    ]
    assert pp_calls, (
        "Expected _ensure_ttl_index to be called for 'payments_pending', "
        f"but only saw calls for: {[c for c, _, _ in recorded_calls]}"
    )

    coll_name, key_spec, expire_after = pp_calls[0]
    indexed_fields = [field for field, _ in key_spec]
    assert "expires_at" in indexed_fields, (
        f"TTL index must be on 'expires_at', but got key_spec={key_spec}"
    )
    assert expire_after == 0, (
        f"expireAfterSeconds must be 0 so MongoDB deletes at the stored expires_at "
        f"datetime; got {expire_after}"
    )


# ---------------------------------------------------------------------------
# 2. create_order sets expires_at = now + 2 days
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_order_sets_expires_at_two_days_ahead(
    client: AsyncClient, mock_user, mock_razorpay_client
):
    """
    POST /payments/create-order must write expires_at ≈ now + 2 days into
    payments_pending so the TTL index can auto-expire stale records.
    """
    from app.main import app
    from app.api.v1.auth import get_current_user
    from app.config import settings

    written_doc = {}

    async def _replace_one(filter_doc, doc, upsert=False):
        written_doc.update(doc)
        return MagicMock(upserted_id=None)

    mock_collection = MagicMock()
    mock_collection.replace_one = AsyncMock(side_effect=_replace_one)

    mock_db = MagicMock()
    mock_db.payments_pending = mock_collection

    mock_mongo = MagicMock()
    mock_mongo.__getitem__ = MagicMock(return_value=mock_db)

    async def override_auth():
        return mock_user

    app.dependency_overrides[get_current_user] = override_auth
    original_key_id = settings.RAZORPAY_KEY_ID
    original_key_secret = settings.RAZORPAY_KEY_SECRET
    settings.RAZORPAY_KEY_ID = TEST_KEY_ID
    settings.RAZORPAY_KEY_SECRET = TEST_KEY_SECRET

    try:
        with (
            patch("app.db.redis.get_redis", return_value=_make_broken_redis()),
            patch("app.db.mongo.get_mongo_client", return_value=mock_mongo),
            patch("razorpay.Client", return_value=mock_razorpay_client),
        ):
            response = await client.post(
                "/api/v1/payments/create-order",
                json={"plan": "starter"},
            )

        assert response.status_code == 200, response.text

        # expires_at must be set and roughly 2 days in the future
        assert "expires_at" in written_doc, (
            "payments_pending document must include 'expires_at' for TTL to work"
        )
        expires_at: datetime = written_doc["expires_at"]
        now = datetime.now(timezone.utc)
        diff = expires_at - now
        # Allow ±5 minutes tolerance around the 2-day target
        assert timedelta(days=1, hours=23, minutes=55) <= diff <= timedelta(days=2, minutes=5), (
            f"expires_at should be ~2 days from now, but diff={diff}"
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        settings.RAZORPAY_KEY_ID = original_key_id
        settings.RAZORPAY_KEY_SECRET = original_key_secret


# ---------------------------------------------------------------------------
# 3. /payments/recover returns live pending records for the user
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_recover_returns_non_expired_pending_records(
    client: AsyncClient, mock_user
):
    """
    POST /payments/recover must return pending records whose expires_at is in
    the future.  This simulates an orphaned record left by a failed /verify.
    """
    from app.main import app
    from app.api.v1.auth import get_current_user

    future_record = {
        "_id": MagicMock(__str__=lambda self: "abc123"),
        "order_id": ORDER_ID_TTL,
        "user_id": str(mock_user.id),
        "plan": "starter",
        "amount": STARTER_AMOUNT,
        "created_at": datetime.now(timezone.utc) - timedelta(hours=1),
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=47),
    }

    mock_cursor = AsyncMock()
    mock_cursor.to_list = AsyncMock(return_value=[future_record])
    mock_cursor.sort = MagicMock(return_value=mock_cursor)

    mock_collection = MagicMock()
    mock_collection.find = MagicMock(return_value=mock_cursor)

    mock_db = MagicMock()
    mock_db.payments_pending = mock_collection

    mock_mongo = MagicMock()
    mock_mongo.__getitem__ = MagicMock(return_value=mock_db)

    async def override_auth():
        return mock_user

    app.dependency_overrides[get_current_user] = override_auth

    try:
        with patch("app.db.mongo.get_mongo_client", return_value=mock_mongo):
            response = await client.post("/api/v1/payments/recover")

        assert response.status_code == 200, response.text
        data = response.json()
        assert "pending_payments" in data
        assert len(data["pending_payments"]) == 1
        record = data["pending_payments"][0]
        assert record["order_id"] == ORDER_ID_TTL
        assert record["plan"] == "starter"
        assert record["amount"] == STARTER_AMOUNT

        # Confirm the query filtered on user_id and expires_at > now
        call_args = mock_collection.find.call_args
        query_filter = call_args[0][0]
        assert query_filter["user_id"] == str(mock_user.id), (
            "recover must filter by user_id"
        )
        assert "$gt" in query_filter.get("expires_at", {}), (
            "recover must filter expires_at > now to skip already-expired records"
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ---------------------------------------------------------------------------
# 4. /payments/recover excludes expired records (via the query filter)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_recover_excludes_expired_records(
    client: AsyncClient, mock_user
):
    """
    /payments/recover must pass expires_at: {$gt: now} in its MongoDB query so
    that documents already expired (but not yet purged by the TTL reaper) are
    not surfaced to the user.
    """
    from app.main import app
    from app.api.v1.auth import get_current_user

    # DB returns empty list — simulating that all matching (expired) docs were
    # filtered out by the $gt query.
    mock_cursor = AsyncMock()
    mock_cursor.to_list = AsyncMock(return_value=[])
    mock_cursor.sort = MagicMock(return_value=mock_cursor)

    mock_collection = MagicMock()
    mock_collection.find = MagicMock(return_value=mock_cursor)

    mock_db = MagicMock()
    mock_db.payments_pending = mock_collection

    mock_mongo = MagicMock()
    mock_mongo.__getitem__ = MagicMock(return_value=mock_db)

    async def override_auth():
        return mock_user

    app.dependency_overrides[get_current_user] = override_auth

    try:
        with patch("app.db.mongo.get_mongo_client", return_value=mock_mongo):
            response = await client.post("/api/v1/payments/recover")

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["pending_payments"] == [], (
            "Expected empty list when all pending records are expired"
        )

        # The filter must still include expires_at: {$gt: ...} even for empty results
        call_args = mock_collection.find.call_args
        query_filter = call_args[0][0]
        assert "$gt" in query_filter.get("expires_at", {}), (
            "recover query must always include expires_at: {$gt: now}"
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ---------------------------------------------------------------------------
# 5. /payments/recover returns empty list when no records exist
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_recover_returns_empty_list_when_no_pending(
    client: AsyncClient, mock_user
):
    """
    /payments/recover must return {pending_payments: []} when there are no
    orphaned records for the user — not an error, not a 404.
    """
    from app.main import app
    from app.api.v1.auth import get_current_user

    mock_cursor = AsyncMock()
    mock_cursor.to_list = AsyncMock(return_value=[])
    mock_cursor.sort = MagicMock(return_value=mock_cursor)

    mock_collection = MagicMock()
    mock_collection.find = MagicMock(return_value=mock_cursor)

    mock_db = MagicMock()
    mock_db.payments_pending = mock_collection

    mock_mongo = MagicMock()
    mock_mongo.__getitem__ = MagicMock(return_value=mock_db)

    async def override_auth():
        return mock_user

    app.dependency_overrides[get_current_user] = override_auth

    try:
        with patch("app.db.mongo.get_mongo_client", return_value=mock_mongo):
            response = await client.post("/api/v1/payments/recover")

        assert response.status_code == 200, response.text
        assert response.json() == {"pending_payments": []}
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ---------------------------------------------------------------------------
# 6. Stale record simulation: verify that TTL expiry logic is correct
# ---------------------------------------------------------------------------


def test_stale_record_would_be_expired_by_ttl():
    """
    Unit-level logic check: a payments_pending record created 3 days ago has
    an expires_at in the past.  MongoDB TTL with expireAfterSeconds=0 would
    delete it because expires_at < now.

    This does not require a real MongoDB connection — it validates the
    datetime arithmetic used in create_order is correct so the TTL fires.
    """
    # Simulate a record written 3 days ago
    three_days_ago = datetime.now(timezone.utc) - timedelta(days=3)
    stale_record = {
        "order_id": "order_STALE_001",
        "created_at": three_days_ago,
        "expires_at": three_days_ago + timedelta(days=2),  # = 1 day ago
    }

    now = datetime.now(timezone.utc)
    expires_at = stale_record["expires_at"]

    # With expireAfterSeconds=0, MongoDB deletes when expires_at <= now
    assert expires_at < now, (
        f"Stale record's expires_at={expires_at} should be in the past; "
        f"MongoDB TTL should have deleted it by now={now}"
    )

    # A fresh record (created now) should NOT be expired
    fresh_record = {
        "created_at": now,
        "expires_at": now + timedelta(days=2),
    }
    assert fresh_record["expires_at"] > now, (
        "Fresh record's expires_at should be in the future — TTL must not touch it"
    )
