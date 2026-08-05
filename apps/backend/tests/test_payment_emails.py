"""
Tests for payment email sending on /payments/verify and /payments/credit-topup/verify.

Verifies:
- send_first_purchase_receipt_email is called with the correct email, amount, and
  order_id on a successful subscription payment verify.
- send_credit_topup_receipt_email is called with the correct values on a successful
  credit top-up verify.
- An email send failure does NOT cause either endpoint to return an error response.
- When Redis misses and MongoDB misses, /verify falls back to the Razorpay order API
  and upgrades the user successfully (Razorpay fallback path).
- When Redis, MongoDB, AND the Razorpay API all fail, /verify fails closed (503)
  rather than trusting client-supplied plan values.
"""

import hashlib
import hmac

import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock, MagicMock, patch

# Current plan prices in paise (1 INR = 100 paise)
PLAN_PRICES = {"starter": 9900, "pro": 99900}

TEST_KEY_SECRET = "test_razorpay_key_secret"


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


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_free_user():
    """Authenticated free-tier user."""
    user = MagicMock()
    user.id = "user-free-abc"
    user.email = "buyer@example.com"
    user.subscription_tier = "free"
    user.credits_remaining = 10
    user.update = AsyncMock()
    return user


@pytest.fixture
def mock_mongo_db():
    """Mock MongoDB client so payment record inserts don't fail."""
    mock_db = MagicMock()
    mock_db.payments = MagicMock()
    mock_db.payments.insert_one = AsyncMock()
    mock_client = MagicMock()
    mock_client.__getitem__ = MagicMock(return_value=mock_db)
    return mock_client, mock_db


def _make_redis_for_subscription(plan: str = "pro"):
    """
    Redis mock for subscription verify.
    Simulates what create-order stores: order_amount and order_plan keys.
    """
    amount = PLAN_PRICES[plan]

    async def _get(key):
        if key.startswith("order_amount:"):
            return str(amount).encode()
        if key.startswith("order_plan:"):
            return plan.encode()
        return None

    mock = AsyncMock()
    mock.get = AsyncMock(side_effect=_get)
    mock.set = AsyncMock(return_value=True)
    return mock


@pytest.fixture
def mock_redis_for_topup():
    """
    Redis mock for credit-topup verify:
    - SET NX (dedup) returns True (first time, not a duplicate)
    - GET credit_order returns stored credits
    """
    async def _get(key):
        if key.startswith("credit_order:"):
            return b"50"
        return None

    mock = AsyncMock()
    mock.set = AsyncMock(return_value=True)   # SET NX → True means "was new"
    mock.get = AsyncMock(side_effect=_get)
    return mock


# ---------------------------------------------------------------------------
# Tests: POST /payments/verify — email is called correctly
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize("plan,expected_amount", [
    ("starter", 9900),
    ("pro", 99900),
])
async def test_verify_payment_calls_first_purchase_email(
    client: AsyncClient, mock_free_user, mock_mongo_db, plan, expected_amount
):
    """
    A successful POST /payments/verify must call send_first_purchase_receipt_email
    with (user.email, amount_paise, order_id) — verified for both plans.
    """
    from app.main import app
    from app.api.v1.auth import get_current_user
    from app.config import settings

    order_id = f"order_{plan.upper()}_001"
    payment_id = f"pay_{plan.upper()}_001"
    sig = _make_signature(order_id, payment_id)
    mock_client, _ = mock_mongo_db

    async def override_auth():
        return mock_free_user

    app.dependency_overrides[get_current_user] = override_auth
    original_secret = settings.RAZORPAY_KEY_SECRET
    settings.RAZORPAY_KEY_SECRET = TEST_KEY_SECRET

    try:
        with (
            patch("app.db.redis.get_redis", return_value=_make_redis_for_subscription(plan)),
            patch("app.db.mongo.get_mongo_client", return_value=mock_client),
            patch(
                "app.services.comms.resend_client.send_first_purchase_receipt_email",
                new_callable=AsyncMock,
            ) as mock_send_email,
        ):
            response = await client.post(
                "/api/v1/payments/verify",
                json={
                    "razorpay_order_id": order_id,
                    "razorpay_payment_id": payment_id,
                    "razorpay_signature": sig,
                },
            )

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "success"

        mock_send_email.assert_awaited_once()
        call_args = mock_send_email.call_args[0]
        assert call_args[0] == "buyer@example.com"   # email
        assert call_args[1] == expected_amount         # amount in paise — no hardcoded fallback
        assert call_args[2] == order_id                # order_id
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        settings.RAZORPAY_KEY_SECRET = original_secret


# ---------------------------------------------------------------------------
# Tests: POST /payments/verify — email failure is non-fatal
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_verify_payment_email_failure_does_not_cause_error(
    client: AsyncClient, mock_free_user, mock_mongo_db
):
    """
    If send_first_purchase_receipt_email raises, /verify must still return 200.
    Email failure must never block the payment confirmation.
    """
    from app.main import app
    from app.api.v1.auth import get_current_user
    from app.config import settings

    order_id = "order_EMAILFAIL001"
    payment_id = "pay_EMAILFAIL001"
    sig = _make_signature(order_id, payment_id)
    mock_client, _ = mock_mongo_db

    async def override_auth():
        return mock_free_user

    app.dependency_overrides[get_current_user] = override_auth
    original_secret = settings.RAZORPAY_KEY_SECRET
    settings.RAZORPAY_KEY_SECRET = TEST_KEY_SECRET

    try:
        with (
            patch("app.db.redis.get_redis", return_value=_make_redis_for_subscription("pro")),
            patch("app.db.mongo.get_mongo_client", return_value=mock_client),
            patch(
                "app.services.comms.resend_client.send_first_purchase_receipt_email",
                new_callable=AsyncMock,
                side_effect=Exception("Resend API unreachable"),
            ),
        ):
            response = await client.post(
                "/api/v1/payments/verify",
                json={
                    "razorpay_order_id": order_id,
                    "razorpay_payment_id": payment_id,
                    "razorpay_signature": sig,
                },
            )

        assert response.status_code == 200
        assert response.json()["status"] == "success"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        settings.RAZORPAY_KEY_SECRET = original_secret


# ---------------------------------------------------------------------------
# Tests: POST /payments/verify — fail-closed when Redis is unavailable
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_verify_payment_fails_closed_when_all_sources_unavailable(
    client: AsyncClient, mock_free_user
):
    """
    When Redis, MongoDB, AND the Razorpay API all fail, /verify must return 503
    rather than silently upgrading the user with an unverified plan.
    The email function must never be called in this case.
    """
    from app.main import app
    from app.api.v1.auth import get_current_user
    from app.config import settings

    order_id = "order_ALLFAIL001"
    payment_id = "pay_ALLFAIL001"
    sig = _make_signature(order_id, payment_id)

    # Redis raises on every call — simulates total Redis outage
    broken_redis = AsyncMock()
    broken_redis.get = AsyncMock(side_effect=Exception("Redis connection refused"))
    broken_redis.set = AsyncMock(side_effect=Exception("Redis connection refused"))

    # MongoDB returns None — no pending record (e.g. write failed at order-creation time)
    mock_db = MagicMock()
    mock_db.payments_pending = MagicMock()
    mock_db.payments_pending.find_one = AsyncMock(return_value=None)
    mock_db.payments = MagicMock()
    mock_db.payments.insert_one = AsyncMock()
    mock_db.payments_pending.delete_one = AsyncMock()
    mock_mongo_client = MagicMock()
    mock_mongo_client.__getitem__ = MagicMock(return_value=mock_db)

    async def override_auth():
        return mock_free_user

    app.dependency_overrides[get_current_user] = override_auth
    original_secret = settings.RAZORPAY_KEY_SECRET
    settings.RAZORPAY_KEY_SECRET = TEST_KEY_SECRET

    try:
        with (
            patch("app.db.redis.get_redis", return_value=broken_redis),
            patch("app.db.mongo.get_mongo_client", return_value=mock_mongo_client),
            # Razorpay client.order.fetch raises — simulates Razorpay API outage
            patch("razorpay.Client") as mock_rz_class,
            patch(
                "app.services.comms.resend_client.send_first_purchase_receipt_email",
                new_callable=AsyncMock,
            ) as mock_send_email,
        ):
            mock_rz_instance = MagicMock()
            mock_rz_instance.order.fetch = MagicMock(
                side_effect=Exception("Razorpay API unreachable")
            )
            mock_rz_class.return_value = mock_rz_instance

            response = await client.post(
                "/api/v1/payments/verify",
                json={
                    "razorpay_order_id": order_id,
                    "razorpay_payment_id": payment_id,
                    "razorpay_signature": sig,
                    "plan": "pro",   # attacker tries to claim pro via client hint
                },
            )

        assert response.status_code == 503
        mock_send_email.assert_not_awaited()
        mock_free_user.update.assert_not_called()
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        settings.RAZORPAY_KEY_SECRET = original_secret


# ---------------------------------------------------------------------------
# Tests: POST /payments/verify — Razorpay API fallback when Redis + MongoDB miss
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize("plan,expected_amount", [
    ("starter", 9900),
    ("pro", 99900),
])
async def test_verify_payment_razorpay_fallback_upgrades_user(
    client: AsyncClient, mock_free_user, plan, expected_amount
):
    """
    When Redis is down AND MongoDB has no pending record, /verify must call
    Razorpay client.order.fetch as a last-resort fallback, resolve the plan and
    amount from the order notes, upgrade the user, and send the receipt email
    with the correct amount.
    """
    from app.main import app
    from app.api.v1.auth import get_current_user
    from app.config import settings

    order_id = f"order_RZFALLBACK_{plan.upper()}"
    payment_id = f"pay_RZFALLBACK_{plan.upper()}"
    sig = _make_signature(order_id, payment_id)

    # Redis raises on every call
    broken_redis = AsyncMock()
    broken_redis.get = AsyncMock(side_effect=Exception("Redis connection refused"))
    broken_redis.set = AsyncMock(side_effect=Exception("Redis connection refused"))

    # MongoDB returns None — pending record was never written
    mock_db = MagicMock()
    mock_db.payments_pending = MagicMock()
    mock_db.payments_pending.find_one = AsyncMock(return_value=None)
    mock_db.payments_pending.delete_one = AsyncMock()
    mock_db.payments = MagicMock()
    mock_db.payments.insert_one = AsyncMock()
    mock_mongo_client = MagicMock()
    mock_mongo_client.__getitem__ = MagicMock(return_value=mock_db)

    # Razorpay order.fetch returns the order with notes containing the plan
    rz_order_response = {
        "id": order_id,
        "amount": expected_amount,
        "currency": "INR",
        "notes": {"user_id": str(mock_free_user.id), "plan": plan},
    }

    async def override_auth():
        return mock_free_user

    app.dependency_overrides[get_current_user] = override_auth
    original_secret = settings.RAZORPAY_KEY_SECRET
    original_key_id = settings.RAZORPAY_KEY_ID
    settings.RAZORPAY_KEY_SECRET = TEST_KEY_SECRET
    settings.RAZORPAY_KEY_ID = "test_key_id"

    try:
        with (
            patch("app.db.redis.get_redis", return_value=broken_redis),
            patch("app.db.mongo.get_mongo_client", return_value=mock_mongo_client),
            patch("razorpay.Client") as mock_rz_class,
            patch(
                "app.services.comms.resend_client.send_first_purchase_receipt_email",
                new_callable=AsyncMock,
            ) as mock_send_email,
        ):
            mock_rz_instance = MagicMock()
            mock_rz_instance.order.fetch = MagicMock(return_value=rz_order_response)
            mock_rz_class.return_value = mock_rz_instance

            response = await client.post(
                "/api/v1/payments/verify",
                json={
                    "razorpay_order_id": order_id,
                    "razorpay_payment_id": payment_id,
                    "razorpay_signature": sig,
                },
            )

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["status"] == "success"
        assert plan in data["message"]

        # User must be upgraded
        mock_free_user.update.assert_called_once()
        update_call = mock_free_user.update.call_args[0][0]
        assert update_call["$set"]["subscription_tier"] == plan

        # Receipt email must be called with the amount resolved from Razorpay (not a fallback 0)
        mock_send_email.assert_awaited_once()
        call_args = mock_send_email.call_args[0]
        assert call_args[0] == mock_free_user.email   # email
        assert call_args[1] == expected_amount         # amount in paise from Razorpay order
        assert call_args[2] == order_id                # order_id
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        settings.RAZORPAY_KEY_SECRET = original_secret
        settings.RAZORPAY_KEY_ID = original_key_id


# ---------------------------------------------------------------------------
# Tests: POST /payments/verify — bad signature rejected
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_verify_payment_invalid_signature_rejected(client: AsyncClient):
    """
    /payments/verify returns 400 on a bad signature; email is never called.
    """
    from app.main import app
    from app.api.v1.auth import get_current_user
    from app.config import settings

    async def override_auth():
        mock = MagicMock()
        mock.id = "uid"
        mock.email = "x@x.com"
        mock.update = AsyncMock()
        return mock

    app.dependency_overrides[get_current_user] = override_auth
    original_secret = settings.RAZORPAY_KEY_SECRET
    settings.RAZORPAY_KEY_SECRET = TEST_KEY_SECRET

    try:
        with patch(
            "app.services.comms.resend_client.send_first_purchase_receipt_email",
            new_callable=AsyncMock,
        ) as mock_send_email:
            response = await client.post(
                "/api/v1/payments/verify",
                json={
                    "razorpay_order_id": "order_BAD",
                    "razorpay_payment_id": "pay_BAD",
                    "razorpay_signature": "totally_wrong_signature",
                },
            )

        assert response.status_code == 400
        mock_send_email.assert_not_awaited()
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        settings.RAZORPAY_KEY_SECRET = original_secret


# ---------------------------------------------------------------------------
# Tests: POST /payments/verify — idempotency (duplicate order)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_verify_payment_duplicate_returns_already_processed(
    client: AsyncClient, mock_free_user, mock_mongo_db
):
    """
    A second POST /payments/verify for the same order_id must return
    { status: 'already_processed' } without calling user.update or the email
    function a second time.
    """
    from app.main import app
    from app.api.v1.auth import get_current_user
    from app.config import settings

    order_id = "order_DUP001"
    payment_id = "pay_DUP001"
    sig = _make_signature(order_id, payment_id)
    mock_client, _ = mock_mongo_db

    # Redis mock where SET NX returns None (falsy) — key already exists
    dup_redis = AsyncMock()
    dup_redis.set = AsyncMock(return_value=None)  # NX miss → already set
    dup_redis.get = AsyncMock(return_value=None)

    async def override_auth():
        return mock_free_user

    app.dependency_overrides[get_current_user] = override_auth
    original_secret = settings.RAZORPAY_KEY_SECRET
    settings.RAZORPAY_KEY_SECRET = TEST_KEY_SECRET

    try:
        with (
            patch("app.db.redis.get_redis", return_value=dup_redis),
            patch("app.db.mongo.get_mongo_client", return_value=mock_client),
            patch(
                "app.services.comms.resend_client.send_first_purchase_receipt_email",
                new_callable=AsyncMock,
            ) as mock_send_email,
        ):
            response = await client.post(
                "/api/v1/payments/verify",
                json={
                    "razorpay_order_id": order_id,
                    "razorpay_payment_id": payment_id,
                    "razorpay_signature": sig,
                },
            )

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "already_processed"

        # Neither the user record nor the email must be touched on a duplicate
        mock_free_user.update.assert_not_called()
        mock_send_email.assert_not_awaited()
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        settings.RAZORPAY_KEY_SECRET = original_secret


@pytest.mark.anyio
async def test_verify_payment_dedup_key_released_on_processing_failure(
    client: AsyncClient, mock_free_user
):
    """
    If /payments/verify acquires the idempotency lock but then fails during
    processing (e.g. plan metadata unavailable → 503), the lock must be deleted
    so the customer can retry.

    This simulates the failure-after-lock scenario:
    1. First call: SET NX succeeds (was_new=True), but plan resolution fails → 503
       The dedup key must be deleted in the cleanup path.
    2. Second call: SET NX succeeds again (key was freed), plan resolves → 200 success.
    """
    from app.main import app
    from app.api.v1.auth import get_current_user
    from app.config import settings

    order_id = "order_FAILRETRY001"
    payment_id = "pay_FAILRETRY001"
    sig = _make_signature(order_id, payment_id)

    # ---- Redis state machine ------------------------------------------------
    # Track whether the key has been deleted so the second call sees a fresh lock.
    key_deleted = False
    set_nx_call_count = 0

    async def _set(key, value, ex=None, nx=False):
        nonlocal set_nx_call_count, key_deleted
        if nx and key.startswith("sub_verify:"):
            set_nx_call_count += 1
            # First call: key doesn't exist yet → True (was_new)
            # Second call: key was deleted in cleanup → True again (was_new)
            return True
        return True  # non-NX sets (order_amount / order_plan) also return True

    async def _get(key):
        nonlocal set_nx_call_count
        # First attempt: no plan/amount available → force failure path
        if set_nx_call_count == 1:
            return None
        # Second attempt: plan and amount are available → success path
        if key.startswith("order_amount:"):
            return str(PLAN_PRICES["pro"]).encode()
        if key.startswith("order_plan:"):
            return b"pro"
        return None

    async def _delete(key):
        nonlocal key_deleted
        if key.startswith("sub_verify:"):
            key_deleted = True

    stateful_redis = AsyncMock()
    stateful_redis.set = AsyncMock(side_effect=_set)
    stateful_redis.get = AsyncMock(side_effect=_get)
    stateful_redis.delete = AsyncMock(side_effect=_delete)

    # MongoDB: no pending record (forces Redis-miss path to use GET for plan/amount)
    mock_db = MagicMock()
    mock_db.payments_pending = MagicMock()
    mock_db.payments_pending.find_one = AsyncMock(return_value=None)
    mock_db.payments_pending.delete_one = AsyncMock()
    mock_db.payments = MagicMock()
    mock_db.payments.insert_one = AsyncMock()
    mock_mongo_client = MagicMock()
    mock_mongo_client.__getitem__ = MagicMock(return_value=mock_db)

    async def override_auth():
        return mock_free_user

    app.dependency_overrides[get_current_user] = override_auth
    original_secret = settings.RAZORPAY_KEY_SECRET
    original_key_id = settings.RAZORPAY_KEY_ID
    settings.RAZORPAY_KEY_SECRET = TEST_KEY_SECRET
    settings.RAZORPAY_KEY_ID = ""  # disables Razorpay fallback so plan stays None → 503

    try:
        with (
            patch("app.db.redis.get_redis", return_value=stateful_redis),
            patch("app.db.mongo.get_mongo_client", return_value=mock_mongo_client),
            patch(
                "app.services.comms.resend_client.send_first_purchase_receipt_email",
                new_callable=AsyncMock,
            ) as mock_send_email,
        ):
            # --- First call: fails with 503 because plan cannot be resolved ---
            response1 = await client.post(
                "/api/v1/payments/verify",
                json={
                    "razorpay_order_id": order_id,
                    "razorpay_payment_id": payment_id,
                    "razorpay_signature": sig,
                },
            )
            assert response1.status_code == 503, response1.text

            # Dedup key must have been released so the customer can retry
            assert key_deleted, "Dedup key must be deleted when processing fails"
            mock_free_user.update.assert_not_called()
            mock_send_email.assert_not_awaited()

            # --- Second call: plan is now available → succeeds ---
            settings.RAZORPAY_KEY_ID = original_key_id  # re-enable for completeness
            response2 = await client.post(
                "/api/v1/payments/verify",
                json={
                    "razorpay_order_id": order_id,
                    "razorpay_payment_id": payment_id,
                    "razorpay_signature": sig,
                },
            )
            assert response2.status_code == 200, response2.text
            assert response2.json()["status"] == "success"
            mock_free_user.update.assert_called_once()
            mock_send_email.assert_awaited_once()
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        settings.RAZORPAY_KEY_SECRET = original_secret
        settings.RAZORPAY_KEY_ID = original_key_id


@pytest.mark.anyio
async def test_verify_payment_idempotency_redis_down_falls_through(
    client: AsyncClient, mock_free_user, mock_mongo_db
):
    """
    When Redis is unavailable during the idempotency check, /verify must fall
    through and process the payment normally (fail-open).  The user must be
    upgraded and the receipt email must be sent.
    """
    from app.main import app
    from app.api.v1.auth import get_current_user
    from app.config import settings

    order_id = "order_IDEM_REDISDOWN001"
    payment_id = "pay_IDEM_REDISDOWN001"
    sig = _make_signature(order_id, payment_id)
    mock_client, _ = mock_mongo_db

    # Redis raises on every call, including the SET NX idempotency check
    broken_redis = AsyncMock()
    broken_redis.set = AsyncMock(side_effect=Exception("Redis connection refused"))
    broken_redis.get = AsyncMock(side_effect=Exception("Redis connection refused"))

    # MongoDB has the pending record so plan/amount resolve without Redis
    mock_pending = {"order_id": order_id, "plan": "pro", "amount": PLAN_PRICES["pro"]}
    mock_db = MagicMock()
    mock_db.payments_pending = MagicMock()
    mock_db.payments_pending.find_one = AsyncMock(return_value=mock_pending)
    mock_db.payments_pending.delete_one = AsyncMock()
    mock_db.payments = MagicMock()
    mock_db.payments.insert_one = AsyncMock()
    mongo_client = MagicMock()
    mongo_client.__getitem__ = MagicMock(return_value=mock_db)

    async def override_auth():
        return mock_free_user

    app.dependency_overrides[get_current_user] = override_auth
    original_secret = settings.RAZORPAY_KEY_SECRET
    settings.RAZORPAY_KEY_SECRET = TEST_KEY_SECRET

    try:
        with (
            patch("app.db.redis.get_redis", return_value=broken_redis),
            patch("app.db.mongo.get_mongo_client", return_value=mongo_client),
            patch(
                "app.services.comms.resend_client.send_first_purchase_receipt_email",
                new_callable=AsyncMock,
            ) as mock_send_email,
        ):
            response = await client.post(
                "/api/v1/payments/verify",
                json={
                    "razorpay_order_id": order_id,
                    "razorpay_payment_id": payment_id,
                    "razorpay_signature": sig,
                },
            )

        assert response.status_code == 200, response.text
        assert response.json()["status"] == "success"

        # User must be upgraded despite Redis being down
        mock_free_user.update.assert_called_once()
        # Receipt email must be sent
        mock_send_email.assert_awaited_once()
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        settings.RAZORPAY_KEY_SECRET = original_secret


# ---------------------------------------------------------------------------
# Tests: POST /payments/credit-topup/verify — email called correctly
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_credit_topup_verify_calls_receipt_email(
    client: AsyncClient, mock_free_user, mock_redis_for_topup
):
    """
    A successful POST /payments/credit-topup/verify must call
    send_credit_topup_receipt_email with (email, credits, amount_paise, order_id).
    """
    from app.main import app
    from app.api.v1.auth import get_current_user
    from app.config import settings

    order_id = "order_TOPUP001"
    payment_id = "pay_TOPUP001"
    sig = _make_signature(order_id, payment_id)

    async def override_auth():
        return mock_free_user

    app.dependency_overrides[get_current_user] = override_auth
    original_secret = settings.RAZORPAY_KEY_SECRET
    settings.RAZORPAY_KEY_SECRET = TEST_KEY_SECRET

    try:
        with (
            patch("app.db.redis.get_redis", return_value=mock_redis_for_topup),
            patch(
                "app.services.comms.resend_client.send_credit_topup_receipt_email",
                new_callable=AsyncMock,
            ) as mock_send_email,
        ):
            response = await client.post(
                "/api/v1/payments/credit-topup/verify",
                json={
                    "razorpay_order_id": order_id,
                    "razorpay_payment_id": payment_id,
                    "razorpay_signature": sig,
                },
            )

        assert response.status_code == 200, response.text
        data = response.json()
        assert data["status"] == "success"
        assert data["credits_granted"] == 50

        mock_send_email.assert_awaited_once()
        call_args = mock_send_email.call_args[0]
        assert call_args[0] == "buyer@example.com"   # email
        assert call_args[1] == 50                     # credits
        assert call_args[2] == 50 * 100               # amount in paise (1 credit = 1 INR = 100 paise)
        assert call_args[3] == order_id               # order_id
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        settings.RAZORPAY_KEY_SECRET = original_secret


# ---------------------------------------------------------------------------
# Tests: POST /payments/credit-topup/verify — email failure is non-fatal
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_credit_topup_verify_email_failure_does_not_cause_error(
    client: AsyncClient, mock_free_user, mock_redis_for_topup
):
    """
    If send_credit_topup_receipt_email raises, /credit-topup/verify must still
    return 200 with credits_granted — email failure is non-fatal.
    """
    from app.main import app
    from app.api.v1.auth import get_current_user
    from app.config import settings

    order_id = "order_TOPUPFAIL001"
    payment_id = "pay_TOPUPFAIL001"
    sig = _make_signature(order_id, payment_id)

    async def override_auth():
        return mock_free_user

    app.dependency_overrides[get_current_user] = override_auth
    original_secret = settings.RAZORPAY_KEY_SECRET
    settings.RAZORPAY_KEY_SECRET = TEST_KEY_SECRET

    try:
        with (
            patch("app.db.redis.get_redis", return_value=mock_redis_for_topup),
            patch(
                "app.services.comms.resend_client.send_credit_topup_receipt_email",
                new_callable=AsyncMock,
                side_effect=Exception("Email service down"),
            ),
        ):
            response = await client.post(
                "/api/v1/payments/credit-topup/verify",
                json={
                    "razorpay_order_id": order_id,
                    "razorpay_payment_id": payment_id,
                    "razorpay_signature": sig,
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["credits_granted"] == 50
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        settings.RAZORPAY_KEY_SECRET = original_secret


# ---------------------------------------------------------------------------
# Tests: POST /payments/credit-topup/verify — bad signature rejected
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_credit_topup_verify_invalid_signature_rejected(client: AsyncClient):
    """
    /credit-topup/verify returns 400 on a bad signature; email is never called.
    """
    from app.main import app
    from app.api.v1.auth import get_current_user
    from app.config import settings

    async def override_auth():
        mock = MagicMock()
        mock.id = "uid"
        mock.email = "x@x.com"
        mock.update = AsyncMock()
        return mock

    app.dependency_overrides[get_current_user] = override_auth
    original_secret = settings.RAZORPAY_KEY_SECRET
    settings.RAZORPAY_KEY_SECRET = TEST_KEY_SECRET

    try:
        with patch(
            "app.services.comms.resend_client.send_credit_topup_receipt_email",
            new_callable=AsyncMock,
        ) as mock_send_email:
            response = await client.post(
                "/api/v1/payments/credit-topup/verify",
                json={
                    "razorpay_order_id": "order_BAD",
                    "razorpay_payment_id": "pay_BAD",
                    "razorpay_signature": "bad_sig",
                },
            )

        assert response.status_code == 400
        mock_send_email.assert_not_awaited()
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        settings.RAZORPAY_KEY_SECRET = original_secret
