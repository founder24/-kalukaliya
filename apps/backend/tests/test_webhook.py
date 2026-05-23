import pytest
import hmac
import hashlib
import json
from httpx import AsyncClient


def sign_payload(body: bytes, secret: str) -> str:
    """Generate Razorpay webhook signature"""
    return hmac.HMAC(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.mark.anyio
async def test_webhook_missing_signature(client: AsyncClient):
    """Test webhook rejects requests without signature"""
    response = await client.post("/api/webhooks/razorpay", content=b'{}')
    assert response.status_code == 400
    assert "Missing Signature" in response.json()["detail"]


@pytest.mark.anyio
async def test_webhook_invalid_signature(client: AsyncClient):
    """Test webhook rejects invalid signatures"""
    response = await client.post(
        "/api/webhooks/razorpay",
        content=b'{"event": "test"}',
        headers={"X-Razorpay-Signature": "invalid_signature"}
    )
    assert response.status_code == 400
    assert "Invalid Signature" in response.json()["detail"]


@pytest.mark.anyio
async def test_webhook_valid_signature_payment_failed(client: AsyncClient):
    """Test webhook accepts valid HMAC signature for payment.failed event"""
    from app.config import settings
    secret = settings.RAZORPAY_WEBHOOK_SECRET or "test_webhook_secret"
    body = json.dumps({
        "event": "payment.failed",
        "payload": {"customer": {"id": "cust_1"}}
    }).encode()
    sig = sign_payload(body, secret)

    response = await client.post(
        "/api/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": sig}
    )
    assert response.status_code == 200


@pytest.mark.anyio
async def test_webhook_invalid_subscription_id(client: AsyncClient):
    """Test webhook rejects malformed subscription IDs"""
    from app.config import settings
    secret = settings.RAZORPAY_WEBHOOK_SECRET or "test_webhook_secret"
    body = json.dumps({
        "event": "subscription.charged",
        "payload": {
            "subscription": {"id": "INVALID; DROP TABLE"},
            "customer": {"id": "c1"},
            "payment": {"amount": 100}
        }
    }).encode()
    sig = sign_payload(body, secret)

    response = await client.post(
        "/api/webhooks/razorpay",
        content=body,
        headers={"X-Razorpay-Signature": sig}
    )
    assert response.status_code == 400
