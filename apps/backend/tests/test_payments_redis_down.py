"""
Tests: Starter payment resolves correctly when Redis is completely unavailable.

Scenario covered:
1. POST /payments/create-order writes the pending record to MongoDB payments_pending.
2. Redis is fully unavailable (every call raises) for both create-order and verify.
3. POST /payments/verify looks up the record from MongoDB, resolves plan="starter",
   and upgrades the user — no 503, no plan misresolution to "pro".

This guards against regressions in the MongoDB-backed fallback path introduced to
prevent plan misresolution when Redis is down.
"""

import hashlib
import hmac
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest
from httpx import AsyncClient

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TEST_KEY_SECRET = "test_razorpay_key_secret"
TEST_KEY_ID = "test_razorpay_key_id"
ORDER_ID = "order_STARTER_REDIS_DOWN_001"
PAYMENT_ID = "pay_STARTER_REDIS_DOWN_001"
STARTER_AMOUNT = 9900  # paise


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_signature(order_id: str, payment_id: str, secret: str = TEST_KEY_SECRET) -> str:
    """Compute the Razorpay HMAC-SHA256 signature the same way the endpoint does."""
    message = f"{order_id}|{payment_id}"
    return hmac.HMAC(
        key=secret.encode(),
        msg=message.encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()


def _make_broken_redis() -> AsyncMock:
    """Redis mock that raises on every operation — simulates total Redis outage."""
    broken = AsyncMock()
    broken.get = AsyncMock(side_effect=Exception("Redis connection refused"))
    broken.set = AsyncMock(side_effect=Exception("Redis connection refused"))
    return broken


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_starter_user():
    """Free-tier user who is about to purchase Starter."""
    user = MagicMock()
    user.id = "user-starter-redis-down"
    user.email = "starter@example.com"
    user.subscription_tier = "free"
    user.update = AsyncMock()
    return user


@pytest.fixture
def mock_razorpay_client():
    """
    Mock razorpay.Client so no real HTTP calls are made.
    order.create returns a minimal Razorpay order object.
    """
    mock_order = MagicMock()
    mock_order.__getitem__ = MagicMock(
        side_effect=lambda key: ORDER_ID if key == "id" else None
    )
    mock_order.get = MagicMock(return_value=None)

    mock_client_instance = MagicMock()
    mock_client_instance.order.create = MagicMock(return_value=mock_order)

    return mock_client_instance


@pytest.fixture
def mock_mongo_for_create_order():
    """
    MongoDB mock used during create-order.
    Captures what was written to payments_pending via replace_one.
    """
    written = {}

    async def _replace_one_side_effect(filter_doc, document, upsert=False):
        written.update(document)
        result = MagicMock()
        result.upserted_id = None
        return result

    mock_collection = MagicMock()
    mock_collection.replace_one = AsyncMock(side_effect=_replace_one_side_effect)

    mock_db = MagicMock()
    mock_db.payments_pending = mock_collection

    mock_client = MagicMock()
    mock_client.__getitem__ = MagicMock(return_value=mock_db)

    return mock_client, mock_db, mock_collection, written


@pytest.fixture
def mock_mongo_for_verify(mock_mongo_for_create_order):
    """
    MongoDB mock used during verify-payment.
    find_one returns the Starter record as if create-order already wrote it.
    Remaining collections (payments, payments_pending delete) are no-ops.
    """
    _, _, _, written = mock_mongo_for_create_order

    pending_record = {
        "order_id": ORDER_ID,
        "user_id": "user-starter-redis-down",
        "plan": "starter",
        "amount": STARTER_AMOUNT,
        "created_at": datetime.now(timezone.utc),
        "expires_at": datetime.now(timezone.utc) + timedelta(days=2),
    }

    mock_payments_pending = MagicMock()
    mock_payments_pending.find_one = AsyncMock(return_value=pending_record)
    mock_payments_pending.delete_one = AsyncMock()

    mock_payments = MagicMock()
    mock_payments.insert_one = AsyncMock()

    mock_db = MagicMock()
    mock_db.payments_pending = mock_payments_pending
    mock_db.payments = mock_payments

    mock_client = MagicMock()
    mock_client.__getitem__ = MagicMock(return_value=mock_db)

    return mock_client, mock_db, mock_payments_pending


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_order_writes_to_mongo_when_redis_down(
    client: AsyncClient, mock_starter_user, mock_razorpay_client, mock_mongo_for_create_order
):
    """
    POST /payments/create-order with plan='starter' must write a payments_pending
    document to MongoDB even when Redis is completely unavailable.
    """
    from app.main import app
    from app.api.v1.auth import get_current_user
    from app.config import settings

    mock_client, _, mock_collection, written = mock_mongo_for_create_order

    async def override_auth():
        return mock_starter_user

    app.dependency_overrides[get_current_user] = override_auth
    original_key_id = settings.RAZORPAY_KEY_ID
    original_key_secret = settings.RAZORPAY_KEY_SECRET
    settings.RAZORPAY_KEY_ID = TEST_KEY_ID
    settings.RAZORPAY_KEY_SECRET = TEST_KEY_SECRET

    try:
        with (
            patch("app.db.redis.get_redis", return_value=_make_broken_redis()),
            patch("app.db.mongo.get_mongo_client", return_value=mock_client),
            patch("razorpay.Client", return_value=mock_razorpay_client),
        ):
            response = await client.post(
                "/api/v1/payments/create-order",
                json={"plan": "starter"},
            )

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["order_id"] == ORDER_ID
        assert data["amount"] == STARTER_AMOUNT

        # payments_pending.replace_one must have been called
        assert mock_collection.replace_one.call_count >= 1, (
            "Expected replace_one to be called on payments_pending, but it was not"
        )

        # The document written must contain plan='starter' and the correct amount
        call_args = mock_collection.replace_one.call_args
        # replace_one(filter, document, upsert=True) — document is second positional arg
        written_doc = call_args[0][1]
        assert written_doc["plan"] == "starter", (
            f"Expected plan='starter' in payments_pending, got {written_doc.get('plan')!r}"
        )
        assert written_doc["amount"] == STARTER_AMOUNT, (
            f"Expected amount={STARTER_AMOUNT} in payments_pending, got {written_doc.get('amount')!r}"
        )
        assert written_doc["order_id"] == ORDER_ID
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        settings.RAZORPAY_KEY_ID = original_key_id
        settings.RAZORPAY_KEY_SECRET = original_key_secret


@pytest.mark.anyio
async def test_verify_payment_resolves_starter_from_mongo_when_redis_down(
    client: AsyncClient, mock_starter_user, mock_mongo_for_verify
):
    """
    POST /payments/verify must resolve plan='starter' from MongoDB payments_pending
    and successfully upgrade the user when Redis is completely unavailable.

    This is the primary regression guard for the MongoDB-backed Redis-down fallback.
    The verified plan must be 'starter', not 'pro' or a 503 error.
    """
    from app.main import app
    from app.api.v1.auth import get_current_user
    from app.config import settings

    mock_client, _, mock_payments_pending = mock_mongo_for_verify
    sig = _make_signature(ORDER_ID, PAYMENT_ID)

    async def override_auth():
        return mock_starter_user

    app.dependency_overrides[get_current_user] = override_auth
    original_key_secret = settings.RAZORPAY_KEY_SECRET
    settings.RAZORPAY_KEY_SECRET = TEST_KEY_SECRET

    try:
        with (
            patch("app.db.redis.get_redis", return_value=_make_broken_redis()),
            patch("app.db.mongo.get_mongo_client", return_value=mock_client),
            patch(
                "app.services.comms.resend_client.send_first_purchase_receipt_email",
                new_callable=AsyncMock,
            ),
        ):
            response = await client.post(
                "/api/v1/payments/verify",
                json={
                    "razorpay_order_id": ORDER_ID,
                    "razorpay_payment_id": PAYMENT_ID,
                    "razorpay_signature": sig,
                },
            )

        assert response.status_code == 200, (
            f"Expected 200 but got {response.status_code}: {response.text}"
        )
        data = response.json()
        assert data["status"] == "success", f"Unexpected response: {data}"
        assert "starter" in data.get("message", "").lower(), (
            f"Expected 'starter' in success message, got: {data.get('message')!r}"
        )

        # payments_pending.find_one must have been called to resolve the plan
        mock_payments_pending.find_one.assert_awaited_once_with(
            {"order_id": ORDER_ID}
        )

        # User must have been upgraded — update() called with subscription_tier='starter'
        mock_starter_user.update.assert_awaited_once()
        update_doc = mock_starter_user.update.call_args[0][0]
        assert update_doc["$set"]["subscription_tier"] == "starter", (
            f"Expected subscription_tier='starter', got {update_doc['$set'].get('subscription_tier')!r}"
        )
        assert update_doc["$set"]["subscription_status"] == "active"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        settings.RAZORPAY_KEY_SECRET = original_key_secret


@pytest.mark.anyio
async def test_verify_payment_does_not_misresolve_to_pro_when_redis_down(
    client: AsyncClient, mock_starter_user, mock_mongo_for_verify
):
    """
    Even if a malicious client passes plan='pro' as a hint, /verify must NOT
    upgrade the user to 'pro' when the MongoDB record says 'starter'.
    Plan resolution must be authoritative (server-side only).
    """
    from app.main import app
    from app.api.v1.auth import get_current_user
    from app.config import settings

    mock_client, _, _ = mock_mongo_for_verify
    sig = _make_signature(ORDER_ID, PAYMENT_ID)

    async def override_auth():
        return mock_starter_user

    app.dependency_overrides[get_current_user] = override_auth
    original_key_secret = settings.RAZORPAY_KEY_SECRET
    settings.RAZORPAY_KEY_SECRET = TEST_KEY_SECRET

    try:
        with (
            patch("app.db.redis.get_redis", return_value=_make_broken_redis()),
            patch("app.db.mongo.get_mongo_client", return_value=mock_client),
            patch(
                "app.services.comms.resend_client.send_first_purchase_receipt_email",
                new_callable=AsyncMock,
            ),
        ):
            response = await client.post(
                "/api/v1/payments/verify",
                json={
                    "razorpay_order_id": ORDER_ID,
                    "razorpay_payment_id": PAYMENT_ID,
                    "razorpay_signature": sig,
                    "plan": "pro",  # attacker tries to escalate to pro
                },
            )

        assert response.status_code == 200, response.text

        # The upgrade must be to 'starter' (from MongoDB), not 'pro' (from client hint)
        mock_starter_user.update.assert_awaited_once()
        update_doc = mock_starter_user.update.call_args[0][0]
        resolved_tier = update_doc["$set"]["subscription_tier"]
        assert resolved_tier == "starter", (
            f"Plan escalation vulnerability: resolved to {resolved_tier!r} instead of 'starter' "
            "when client hint='pro' but MongoDB record='starter'"
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        settings.RAZORPAY_KEY_SECRET = original_key_secret
