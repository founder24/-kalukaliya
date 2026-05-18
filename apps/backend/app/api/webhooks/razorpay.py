from fastapi import APIRouter, Request, HTTPException, status
from app.config import settings
import hashlib
import hmac
import json
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["Payments"])


def calculate_next_billing_date() -> str:
    """Calculate next billing date (1 month from now)"""
    from datetime import datetime, timedelta
    return (datetime.utcnow() + timedelta(days=30)).isoformat()


@router.post("/razorpay")
async def handle_razorpay_webhook(request: Request):
    """
    Handle Razorpay Payment Webhooks
    Verifies signature and updates subscription status
    """
    body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature")

    if not signature:
        logger.warning("Missing Razorpay signature")
        raise HTTPException(status_code=400, detail="Missing Signature")

    # 1. Verify Signature
    expected_sig = hmac.new(
        settings.RAZORPAY_WEBHOOK_SECRET.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_sig, signature):
        logger.warning("Invalid Razorpay Signature")
        raise HTTPException(status_code=400, detail="Invalid Signature")

    event = json.loads(body.decode())
    payload = event.get("payload", {})

    # 2. Handle Event Types
    if event.get("event") == "subscription.charged":
        sub_id = payload["subscription"]["id"]
        customer_id = payload["customer"]["id"]
        amount = payload["payment"]["amount"]

        # Find User
        from app.db.mongo import get_mongo_client
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        
        user = await db.users.find_one(
            {"razorpay_subscription_id": sub_id}
        )
        
        if not user:
            logger.error(f"User not found for sub {sub_id}")
            return {"status": "ignored", "reason": "user_not_found"}

        # Update Subscription Status
        await db.users.update_one(
            {"_id": user["_id"]},
            {
                "$set": {
                    "subscription_status": "active",
                    "current_period_end": calculate_next_billing_date(),
                    "monthly_message_count": 0,  # Reset usage on new charge
                }
            },
        )

        # Send Receipt Email (async)
        try:
            from app.services.comms.resend_client import send_receipt_email
            await send_receipt_email(user["email"], amount, event["id"])
            logger.info(f"Subscription renewed for user {user['email']}")
        except Exception as e:
            logger.error(f"Failed to send receipt email: {e}")

    elif event.get("event") == "payment.failed":
        # Handle dunning logic (optional: downgrade user after N failures)
        logger.info(f"Payment failed for customer {payload.get('customer', {}).get('id')}")
        # Could implement retry logic or user notification here

    elif event.get("event") == "subscription.cancelled":
        # Mark subscription as cancelled at period end
        sub_id = payload["subscription"]["id"]
        from app.db.mongo import get_mongo_client
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        
        await db.users.update_one(
            {"razorpay_subscription_id": sub_id},
            {"$set": {"cancel_at_period_end": True}},
        )
        logger.info(f"Subscription cancelled: {sub_id}")

    return {"status": "ok"}
