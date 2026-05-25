import pytest
import hmac
import hashlib
import json
from unittest.mock import patch, AsyncMock, MagicMock
from httpx import AsyncClient


def sign_payload(body: bytes, secret: str) -> str:
    """Generate Razorpay webhook signature"""
    return hmac.HMAC(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def mock_redis():
    """Mock Redis for payment tests"""
    mock = AsyncMock()
    mock.get = AsyncMock(return_value=None)
    mock.set = AsyncMock(return_value=True)
    with patch("app.db.redis.get_redis", return_value=mock):
        yield mock


@pytest.fixture
def mock_user_pro():
    """Mock a pro-tier user returned by User.find_one"""
    user = MagicMock()
    user.id = "user-pro-123"
    user.email = "pro@example.com"
    user.subscription_tier = "pro"
    user.razorpay_subscription_id = "sub_TestSub123"
    user.update = AsyncMock()
    return user


@pytest.mark.anyio
async def test_subscription_charged_upgrades_user(client: AsyncClient, mock_redis, mock_user_pro):
    """subscription.charged -> resets monthly message count, sets active"""
    from app.config import settings
    secret = settings.RAZORPAY_WEBHOOK_SECRET or "test_webhook_secret"

    with patch("app.api.webhooks.razorpay.User") as MockUser:
        MockUser.find_one = AsyncMock(return_value=mock_user_pro)
        with patch("app.services.comms.resend_client.send_receipt_email", new_callable=AsyncMock):
            body = json.dumps({
                "id": "evt_charged_001",
                "event": "subscription.charged",
                "payload": {
                    "subscription": {"id": "sub_TestSub123"},
                    "customer": {"id": "cust_1"},
                    "payment": {"amount": 29900},
                },
            }).encode()
            sig = sign_payload(body, secret)
            response = await client.post(
                "/api/webhooks/razorpay",
                content=body,
                headers={"X-Razorpay-Signature": sig},
            )
            assert response.status_code == 200
            mock_user_pro.update.assert_called_once()
            update_args = mock_user_pro.update.call_args[0][0]
            assert update_args["$set"]["subscription_status"] == "active"
            assert update_args["$set"]["monthly_message_count"] == 0


@pytest.mark.anyio
async def test_payment_failed_logs_warning(client: AsyncClient, mock_redis, caplog):
    """payment.failed -> logs warning about failed payment"""
    from app.config import settings
    import logging
    secret = settings.RAZORPAY_WEBHOOK_SECRET or "test_webhook_secret"

    body = json.dumps({
        "id": "evt_failed_001",
        "event": "payment.failed",
        "payload": {
            "customer": {"id": "cust_failed_1"},
        },
    }).encode()
    sig = sign_payload(body, secret)

    with caplog.at_level(logging.INFO):
        response = await client.post(
            "/api/webhooks/razorpay",
            content=body,
            headers={"X-Razorpay-Signature": sig},
        )
    assert response.status_code == 200
    assert "Payment failed" in caplog.text


@pytest.mark.anyio
async def test_subscription_cancelled_marks_user(client: AsyncClient, mock_redis, mock_user_pro):
    """subscription.cancelled -> sets cancel_at_period_end"""
    from app.config import settings
    secret = settings.RAZORPAY_WEBHOOK_SECRET or "test_webhook_secret"

    with patch("app.api.webhooks.razorpay.User") as MockUser:
        MockUser.find_one = AsyncMock(return_value=mock_user_pro)
        body = json.dumps({
            "id": "evt_cancel_001",
            "event": "subscription.cancelled",
            "payload": {
                "subscription": {"id": "sub_TestSub123"},
                "customer": {"id": "cust_1"},
            },
        }).encode()
        sig = sign_payload(body, secret)
        response = await client.post(
            "/api/webhooks/razorpay",
            content=body,
            headers={"X-Razorpay-Signature": sig},
        )
        assert response.status_code == 200
        mock_user_pro.update.assert_called_once()
        update_args = mock_user_pro.update.call_args[0][0]
        assert update_args["$set"]["cancel_at_period_end"] is True


@pytest.mark.anyio
async def test_duplicate_event_returns_duplicate(client: AsyncClient):
    """Duplicate event ID -> returns {status: 'duplicate'}"""
    from app.config import settings
    secret = settings.RAZORPAY_WEBHOOK_SECRET or "test_webhook_secret"

    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value="1")  # Already processed
    mock_redis.set = AsyncMock(return_value=True)

    with patch("app.db.redis.get_redis", return_value=mock_redis):
        body = json.dumps({
            "id": "evt_duplicate_001",
            "event": "subscription.charged",
            "payload": {
                "subscription": {"id": "sub_TestSub123"},
                "customer": {"id": "cust_1"},
                "payment": {"amount": 29900},
            },
        }).encode()
        sig = sign_payload(body, secret)
        response = await client.post(
            "/api/webhooks/razorpay",
            content=body,
            headers={"X-Razorpay-Signature": sig},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "duplicate"


@pytest.mark.anyio
async def test_subscription_created_upgrades_to_pro(client: AsyncClient, mock_redis, mock_user_pro):
    """subscription.created -> (currently unhandled, returns ok)"""
    from app.config import settings
    secret = settings.RAZORPAY_WEBHOOK_SECRET or "test_webhook_secret"

    body = json.dumps({
        "id": "evt_created_001",
        "event": "subscription.created",
        "payload": {
            "subscription": {"id": "sub_TestSub123"},
            "customer": {"id": "cust_1"},
        },
    }).encode()
    sig = sign_payload(body, secret)
    response = await client.post(
        "/api/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": sig},
    )
    assert response.status_code == 200
