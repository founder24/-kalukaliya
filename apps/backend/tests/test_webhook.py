import pytest
import hmac
import hashlib
import json
from unittest.mock import patch, AsyncMock
from httpx import AsyncClient


def sign_payload(body: bytes, secret: str) -> str:
    """Generate Razorpay webhook signature"""
    return hmac.HMAC(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def mock_redis():
    """Mock Redis for webhook tests"""
    mock = AsyncMock()
    mock.get = AsyncMock(return_value=None)
    mock.set = AsyncMock(return_value=True)
    with patch("app.db.redis.get_redis", return_value=mock):
        yield mock


@pytest.mark.anyio
async def test_webhook_missing_signature(client: AsyncClient):
    """Test webhook rejects requests without signature"""
    response = await client.post("/api/webhooks/razorpay", content=b"{}")
    assert response.status_code == 400
    assert "Missing Signature" in response.json()["detail"]


@pytest.mark.anyio
async def test_webhook_invalid_signature(client: AsyncClient):
    """Test webhook rejects invalid signatures"""
    response = await client.post(
        "/api/webhooks/razorpay",
        content=b'{"event": "test"}',
        headers={"X-Razorpay-Signature": "invalid_signature"},
    )
    assert response.status_code == 400
    assert "Invalid Signature" in response.json()["detail"]


@pytest.mark.anyio
async def test_webhook_valid_signature_payment_failed(client: AsyncClient, mock_redis):
    """Test webhook accepts valid HMAC signature for payment.failed event"""
    from app.config import settings

    secret = settings.RAZORPAY_WEBHOOK_SECRET or "test_webhook_secret"
    body = json.dumps(
        {"id": "evt_test_123", "event": "payment.failed", "payload": {"customer": {"id": "cust_1"}}}
    ).encode()
    sig = sign_payload(body, secret)

    response = await client.post(
        "/api/webhooks/razorpay", content=body, headers={"X-Razorpay-Signature": sig}
    )
    assert response.status_code == 200


@pytest.mark.anyio
async def test_webhook_missing_event_id(client: AsyncClient):
    """Test webhook rejects requests without event_id"""
    from app.config import settings

    secret = settings.RAZORPAY_WEBHOOK_SECRET or "test_webhook_secret"
    body = json.dumps(
        {"event": "payment.failed", "payload": {"customer": {"id": "cust_1"}}}
    ).encode()
    sig = sign_payload(body, secret)

    response = await client.post(
        "/api/webhooks/razorpay", content=body, headers={"X-Razorpay-Signature": sig}
    )
    assert response.status_code == 400
    assert "Missing event_id" in response.json()["detail"]


@pytest.mark.anyio
async def test_webhook_invalid_subscription_id(client: AsyncClient, mock_redis):
    """Test webhook rejects malformed subscription IDs"""
    from app.config import settings

    secret = settings.RAZORPAY_WEBHOOK_SECRET or "test_webhook_secret"
    body = json.dumps(
        {
            "id": "evt_test_456",
            "event": "subscription.charged",
            "payload": {
                "subscription": {"id": "INVALID; DROP TABLE"},
                "customer": {"id": "c1"},
                "payment": {"amount": 100},
            },
        }
    ).encode()
    sig = sign_payload(body, secret)

    response = await client.post(
        "/api/webhooks/razorpay", content=body, headers={"X-Razorpay-Signature": sig}
    )
    assert response.status_code == 400
