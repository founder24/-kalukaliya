from fastapi import APIRouter, Request, HTTPException
from app.config import settings
from app.models.user import User
import hashlib
import hmac
import json
import logging
import re

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Payments"])


_RAZORPAY_SUBSCRIPTION_ID_RE = re.compile(r"^sub_[A-Za-z0-9_]+$")


def calculate_next_billing_date() -> str:
    """Calculate next billing date (1 month from now)"""
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()


def _validate_subscription_id(value) -> str:
    if not isinstance(value, str) or not _RAZORPAY_SUBSCRIPTION_ID_RE.fullmatch(value):
        raise HTTPException(status_code=400, detail="Invalid subscription id")
    return value


@router.post("/razorpay")
async def handle_razorpay_webhook(request: Request):
    """
    Handle Razorpay Payment Webhooks
    Verifies signature and updates subscription status
    """
    if not settings.RAZORPAY_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Webhook not configured")

    body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature")

    if not signature:
        logger.warning("Missing Razorpay signature")
        raise HTTPException(status_code=400, detail="Missing Signature")

    # 1. Verify Signature
    expected_sig = hmac.HMAC(
        key=settings.RAZORPAY_WEBHOOK_SECRET.encode(),
        msg=body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_sig, signature):
        logger.warning("Invalid Razorpay Signature")
        raise HTTPException(status_code=400, detail="Invalid Signature")

    event = json.loads(body.decode())
    payload = event.get("payload", {})

    # Idempotency check: skip duplicate events
    event_id = event.get("id")
    if not event_id:
        raise HTTPException(status_code=400, detail="Missing event_id")

    try:
        from app.db.redis import get_redis

        redis = get_redis()
        exists = await redis.get(f"webhook_processed:{event_id}")
        if exists:
            return {"status": "duplicate", "event_id": event_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Redis unavailable for webhook idempotency check: {e}")
        raise HTTPException(status_code=503, detail="Webhook processing unavailable")

    # 2. Handle Event Types
    if event.get("event") == "subscription.charged":
        sub_id = _validate_subscription_id(payload["subscription"]["id"])
        amount = payload["payment"]["amount"]

        # Find User
        user = await User.find_one({"razorpay_subscription_id": sub_id})

        if not user:
            logger.error(f"User not found for sub {sub_id}")
            return {"status": "ignored", "reason": "user_not_found"}

        # Update Subscription Status
        await user.update(
            {
                "$set": {
                    "subscription_status": "active",
                    "current_period_end": calculate_next_billing_date(),
                    "monthly_message_count": 0,  # Reset usage on new charge
                }
            }
        )

        # Send Receipt Email (async)
        try:
            from app.services.comms.resend_client import send_receipt_email

            await send_receipt_email(user.email, amount, event["id"])
            logger.info(f"Subscription renewed for user {user.email}")
        except Exception as e:
            logger.error(f"Failed to send receipt email: {e}")

    elif event.get("event") == "payment.failed":
        # Handle dunning logic (optional: downgrade user after N failures)
        logger.info(
            f"Payment failed for customer {payload.get('customer', {}).get('id')}"
        )
        # Could implement retry logic or user notification here

    elif event.get("event") == "subscription.cancelled":
        # Mark subscription as cancelled at period end
        sub_id = _validate_subscription_id(payload["subscription"]["id"])

        user = await User.find_one({"razorpay_subscription_id": sub_id})
        if user:
            await user.update({"$set": {"cancel_at_period_end": True}})
        logger.info(f"Subscription cancelled: {sub_id}")

    # Mark event as processed for idempotency
    try:
        from app.db.redis import get_redis

        redis = get_redis()
        await redis.set(f"webhook_processed:{event_id}", "1", ex=604800)
    except Exception as e:
        logger.error(f"Redis unavailable for webhook idempotency storage: {e}")

    return {"status": "ok"}
