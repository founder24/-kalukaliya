"""
Tests: payments collection audit record is always written, independently of the
receipt email step.

Scenarios covered:
1. email_raises_insert_still_called — when send_first_purchase_receipt_email raises,
   payments.insert_one must still have been awaited with the correct fields.
2. insert_raises_still_returns_200 — when payments.insert_one raises (e.g. MongoDB
   transient error), verify_payment must still return HTTP 200 (insert failure is
   non-fatal; the user upgrade has already gone through).
"""

import hashlib
import hmac
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest
from httpx import AsyncClient

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TEST_KEY_SECRET = "test_razorpay_key_secret_history"
TEST_KEY_ID = "test_razorpay_key_id_history"
ORDER_ID = "order_HISTORY_RECORD_001"
PAYMENT_ID = "pay_HISTORY_RECORD_001"
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
    mock.set = AsyncMock(return_value=True)  # nx=True → key was new, proceed
    mock.delete = AsyncMock(return_value=1)
    return mock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_pro_user():
    """Free-tier user about to be upgraded to Pro."""
    user = MagicMock()
    user.id = "user-history-record-test"
    user.email = "history@example.com"
    user.subscription_tier = "free"
    user.update = AsyncMock()
    return user


@pytest.fixture
def mock_mongo_insert_ok():
    """
    MongoDB mock where payments.insert_one succeeds.
    Exposes mock_payments so tests can assert insert_one was called.
    """
    mock_payments_pending = MagicMock()
    mock_payments_pending.delete_one = AsyncMock(return_value=MagicMock(deleted_count=1))
    mock_payments_pending.find_one = AsyncMock(return_value=None)

    mock_payments = MagicMock()
    mock_payments.insert_one = AsyncMock()

    mock_db = MagicMock()
    mock_db.payments_pending = mock_payments_pending
    mock_db.payments = mock_payments

    mock_client = MagicMock()
    mock_client.__getitem__ = MagicMock(return_value=mock_db)

    return mock_client, mock_payments


@pytest.fixture
def mock_mongo_insert_raises():
    """
    MongoDB mock where payments.insert_one raises (e.g. transient Mongo error).
    All other operations (delete_one, find_one) succeed so the verify can complete.
    """
    mock_payments_pending = MagicMock()
    mock_payments_pending.delete_one = AsyncMock(return_value=MagicMock(deleted_count=1))
    mock_payments_pending.find_one = AsyncMock(return_value=None)

    mock_payments = MagicMock()
    mock_payments.insert_one = AsyncMock(
        side_effect=Exception("MongoDB transient error during insert")
    )

    mock_db = MagicMock()
    mock_db.payments_pending = mock_payments_pending
    mock_db.payments = mock_payments

    mock_client = MagicMock()
    mock_client.__getitem__ = MagicMock(return_value=mock_db)

    return mock_client, mock_payments


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_insert_still_called_when_email_raises(
    client: AsyncClient, mock_pro_user, mock_mongo_insert_ok
):
    """
    When send_first_purchase_receipt_email raises, payments.insert_one must still
    have been awaited with the correct audit fields.  The email step comes AFTER
    insert_one in the handler; this test confirms the insert block runs independently
    of whatever happens to the email.
    """
    from app.main import app
    from app.api.v1.auth import get_current_user
    from app.config import settings

    mock_client, mock_payments = mock_mongo_insert_ok
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
                side_effect=Exception("SMTP connection refused"),
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

        # The endpoint must still succeed even though the email raised.
        assert response.status_code == 200, (
            f"Expected 200 when email fails, got {response.status_code}: {response.text}"
        )
        data = response.json()
        assert data["status"] == "success", (
            f"Expected status='success' when email is non-fatal, got: {data}"
        )

        # payments.insert_one must have been awaited.
        mock_payments.insert_one.assert_awaited_once()

        # Verify the fields passed to insert_one are correct.
        call_kwargs = mock_payments.insert_one.call_args[0][0]
        assert call_kwargs["user_id"] == str(mock_pro_user.id), (
            "insert_one doc must include correct user_id"
        )
        assert call_kwargs["razorpay_order_id"] == ORDER_ID, (
            "insert_one doc must include razorpay_order_id"
        )
        assert call_kwargs["razorpay_payment_id"] == PAYMENT_ID, (
            "insert_one doc must include razorpay_payment_id"
        )
        assert call_kwargs["amount"] == PRO_AMOUNT, (
            "insert_one doc must include the correct amount"
        )
        assert call_kwargs["status"] == "completed", (
            "insert_one doc must have status='completed'"
        )
        assert call_kwargs["type"] == "subscription", (
            "insert_one doc must have type='subscription'"
        )

    finally:
        app.dependency_overrides.pop(get_current_user, None)
        settings.RAZORPAY_KEY_SECRET = original_key_secret


@pytest.mark.anyio
async def test_verify_returns_200_when_insert_raises(
    client: AsyncClient, mock_pro_user, mock_mongo_insert_raises
):
    """
    When payments.insert_one raises (non-fatal), verify_payment must still return
    HTTP 200.  The user upgrade must have already been applied before the failed
    insert attempt.
    """
    from app.main import app
    from app.api.v1.auth import get_current_user
    from app.config import settings

    mock_client, mock_payments = mock_mongo_insert_raises
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

        # The endpoint must return 200 even though insert_one raised.
        assert response.status_code == 200, (
            f"Expected 200 when insert_one fails, got {response.status_code}: {response.text}"
        )
        data = response.json()
        assert data["status"] == "success", (
            f"Expected status='success' when insert failure is non-fatal, got: {data}"
        )

        # insert_one must have been attempted (the try block ran).
        mock_payments.insert_one.assert_awaited_once()

        # The user upgrade must have gone through before the failed insert.
        mock_pro_user.update.assert_awaited_once()
        update_doc = mock_pro_user.update.call_args[0][0]
        assert update_doc["$set"]["subscription_tier"] == PLAN, (
            "User must be upgraded to the correct plan even when insert_one fails"
        )

    finally:
        app.dependency_overrides.pop(get_current_user, None)
        settings.RAZORPAY_KEY_SECRET = original_key_secret
