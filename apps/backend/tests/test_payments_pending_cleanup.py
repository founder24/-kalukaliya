"""
Tests: payments_pending cleanup after a successful verify_payment call.

Scenarios covered:
1. delete_one fires — after a valid signature verify, the payments_pending record
   for the order is removed from MongoDB.
2. delete is non-fatal — if MongoDB raises during delete_one, verify_payment still
   returns HTTP 200 (the customer is not blocked by a cleanup failure).
"""

import hashlib
import hmac
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TEST_KEY_SECRET = "test_razorpay_key_secret"
TEST_KEY_ID = "test_razorpay_key_id"
ORDER_ID = "order_PENDING_CLEANUP_001"
PAYMENT_ID = "pay_PENDING_CLEANUP_001"
PRO_AMOUNT = 99900  # paise — matches plan_prices["pro"]
PLAN = "pro"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_signature(
    order_id: str = ORDER_ID,
    payment_id: str = PAYMENT_ID,
    secret: str = TEST_KEY_SECRET,
) -> str:
    """Compute the Razorpay HMAC-SHA256 signature the same way the endpoint does."""
    message = f"{order_id}|{payment_id}"
    return hmac.HMAC(
        key=secret.encode(),
        msg=message.encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()


def _make_redis(plan: str = PLAN, amount: int = PRO_AMOUNT) -> AsyncMock:
    """
    Working Redis mock that:
    - Allows the dedup SET NX (returns True → this is a new, unprocessed order).
    - Returns plan and amount for the order so the MongoDB fallback is not needed.
    """
    mock = AsyncMock()

    async def _get(key):
        if f"order_plan:{ORDER_ID}" in key:
            return plan
        if f"order_amount:{ORDER_ID}" in key:
            return str(amount)
        return None

    mock.get = AsyncMock(side_effect=_get)
    mock.set = AsyncMock(return_value=True)   # nx=True → key was new, proceed
    mock.delete = AsyncMock(return_value=1)
    return mock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_pro_user():
    """Free-tier user about to be upgraded to Pro."""
    user = MagicMock()
    user.id = "user-pending-cleanup-test"
    user.email = "cleanup@example.com"
    user.subscription_tier = "free"
    user.update = AsyncMock()
    return user


@pytest.fixture
def mock_mongo_happy():
    """
    MongoDB mock where all operations succeed.
    Exposes payments_pending so tests can assert delete_one was called.
    """
    mock_payments_pending = MagicMock()
    mock_payments_pending.delete_one = AsyncMock(return_value=MagicMock(deleted_count=1))
    mock_payments_pending.find_one = AsyncMock(return_value=None)  # not needed (Redis covers it)

    mock_payments = MagicMock()
    mock_payments.insert_one = AsyncMock()

    mock_db = MagicMock()
    mock_db.payments_pending = mock_payments_pending
    mock_db.payments = mock_payments

    mock_client = MagicMock()
    mock_client.__getitem__ = MagicMock(return_value=mock_db)

    return mock_client, mock_payments_pending


@pytest.fixture
def mock_mongo_delete_raises():
    """
    MongoDB mock where delete_one raises an exception (e.g. Mongo unavailable at
    cleanup time).  All other operations succeed so the verify can complete.
    """
    mock_payments_pending = MagicMock()
    mock_payments_pending.delete_one = AsyncMock(
        side_effect=Exception("MongoDB connection lost during delete")
    )
    mock_payments_pending.find_one = AsyncMock(return_value=None)

    mock_payments = MagicMock()
    mock_payments.insert_one = AsyncMock()

    mock_db = MagicMock()
    mock_db.payments_pending = mock_payments_pending
    mock_db.payments = mock_payments

    mock_client = MagicMock()
    mock_client.__getitem__ = MagicMock(return_value=mock_db)

    return mock_client, mock_payments_pending


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_verify_payment_deletes_pending_record(
    client: AsyncClient, mock_pro_user, mock_mongo_happy
):
    """
    After a successful verify_payment, the payments_pending document for the
    order must be removed via delete_one({"order_id": <order_id>}).
    """
    from app.main import app
    from app.api.v1.auth import get_current_user
    from app.config import settings

    mock_client, mock_payments_pending = mock_mongo_happy
    sig = _make_signature()

    async def override_auth():
        return mock_pro_user

    app.dependency_overrides[get_current_user] = override_auth
    original_key_secret = settings.RAZORPAY_KEY_SECRET
    settings.RAZORPAY_KEY_SECRET = TEST_KEY_SECRET

    try:
        with (
            patch("app.db.redis.get_redis", return_value=_make_redis()),
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
        assert data["status"] == "success", f"Unexpected response body: {data}"

        # The user must have been upgraded.
        mock_pro_user.update.assert_awaited_once()
        update_doc = mock_pro_user.update.call_args[0][0]
        assert update_doc["$set"]["subscription_tier"] == PLAN

        # payments_pending.delete_one must have been called with the right filter.
        mock_payments_pending.delete_one.assert_awaited_once_with(
            {"order_id": ORDER_ID}
        )

    finally:
        app.dependency_overrides.pop(get_current_user, None)
        settings.RAZORPAY_KEY_SECRET = original_key_secret


@pytest.mark.anyio
async def test_verify_payment_returns_200_when_pending_delete_fails(
    client: AsyncClient, mock_pro_user, mock_mongo_delete_raises
):
    """
    If MongoDB is unavailable specifically at the delete_one step, verify_payment
    must still return HTTP 200 — the cleanup failure must not surface to the customer.
    The user upgrade must have completed before the (failed) delete attempt.
    """
    from app.main import app
    from app.api.v1.auth import get_current_user
    from app.config import settings

    mock_client, mock_payments_pending = mock_mongo_delete_raises
    sig = _make_signature()

    async def override_auth():
        return mock_pro_user

    app.dependency_overrides[get_current_user] = override_auth
    original_key_secret = settings.RAZORPAY_KEY_SECRET
    settings.RAZORPAY_KEY_SECRET = TEST_KEY_SECRET

    try:
        with (
            patch("app.db.redis.get_redis", return_value=_make_redis()),
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

        # The endpoint must return 200 even though delete_one raised.
        assert response.status_code == 200, (
            f"Expected 200 when delete_one fails, got {response.status_code}: {response.text}"
        )
        data = response.json()
        assert data["status"] == "success", (
            f"Expected status='success' when delete is non-fatal, got: {data}"
        )

        # The user upgrade must have gone through before the failed delete.
        mock_pro_user.update.assert_awaited_once()
        update_doc = mock_pro_user.update.call_args[0][0]
        assert update_doc["$set"]["subscription_tier"] == PLAN, (
            "User must be upgraded even when pending record cleanup fails"
        )

        # delete_one must have been attempted (the try block ran).
        mock_payments_pending.delete_one.assert_awaited_once_with(
            {"order_id": ORDER_ID}
        )

    finally:
        app.dependency_overrides.pop(get_current_user, None)
        settings.RAZORPAY_KEY_SECRET = original_key_secret
