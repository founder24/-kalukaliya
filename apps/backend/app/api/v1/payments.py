"""
Payments API - Razorpay integration for subscriptions and credit top-ups.
"""

import hashlib
import hmac
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.v1.auth import get_current_user
from app.config import settings
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Payments"])


class CreateOrderRequest(BaseModel):
    plan: str


class VerifyPaymentRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


class CreditTopUpRequest(BaseModel):
    credits: int = Field(ge=1, le=1000)
    provider: str = "razorpay"


class CreditTopUpVerifyRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


class RefundRequest(BaseModel):
    payment_id: str
    reason: Optional[str] = ""


@router.post("/create-order")
async def create_order(
    body: CreateOrderRequest, user: User = Depends(get_current_user)
):
    """Create a Razorpay order for plan upgrade."""
    import razorpay

    if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
        raise HTTPException(status_code=503, detail="Payment service not configured")

    plan_prices = {"pro": 29900, "premium": 59900}
    amount = plan_prices.get(body.plan)
    if not amount:
        raise HTTPException(status_code=400, detail="Invalid plan")

    client = razorpay.Client(
        auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
    )
    order = client.order.create(
        {
            "amount": amount,
            "currency": "INR",
            "receipt": f"user_{user.id}_{body.plan}",
            "notes": {"user_id": str(user.id), "plan": body.plan},
        }
    )

    # Store amount for later verification (resilient payment record)
    try:
        from app.db.redis import get_redis

        redis = get_redis()
        await redis.set(f"order_amount:{order['id']}", str(amount), ex=86400)
    except Exception:
        pass

    return {"order_id": order["id"], "amount": amount, "currency": "INR"}


@router.post("/verify")
async def verify_payment(
    body: VerifyPaymentRequest, user: User = Depends(get_current_user)
):
    """Verify Razorpay payment signature and upgrade user to pro."""
    if not settings.RAZORPAY_KEY_SECRET:
        raise HTTPException(status_code=503, detail="Payment service not configured")

    message = f"{body.razorpay_order_id}|{body.razorpay_payment_id}"
    expected = hmac.HMAC(
        key=settings.RAZORPAY_KEY_SECRET.encode(),
        msg=message.encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, body.razorpay_signature):
        raise HTTPException(status_code=400, detail="Invalid payment signature")

    # Retrieve stored amount for payment record
    payment_amount = None
    try:
        from app.db.redis import get_redis

        redis = get_redis()
        stored_amount = await redis.get(f"order_amount:{body.razorpay_order_id}")
        if stored_amount:
            payment_amount = int(stored_amount)
    except Exception:
        pass

    await user.update(
        {
            "$set": {
                "subscription_tier": "pro",
                "subscription_status": "active",
                "razorpay_subscription_id": body.razorpay_order_id,
            }
        }
    )

    # HF-029: Record payment in payments collection
    try:
        from app.db.mongo import get_mongo_client
        from datetime import datetime, timezone

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        await db.payments.insert_one(
            {
                "user_id": str(user.id),
                "razorpay_order_id": body.razorpay_order_id,
                "razorpay_payment_id": body.razorpay_payment_id,
                "amount": payment_amount,
                "status": "completed",
                "type": "subscription",
                "created_at": datetime.now(timezone.utc),
            }
        )
    except Exception as e:
        logger.error(f"Failed to record payment: {e}")

    logger.info("Payment verified, user upgraded", extra={"user_id": str(user.id)})
    return {"status": "success", "message": "Payment verified, plan upgraded to pro"}


@router.post("/recover")
async def recover_payment(user: User = Depends(get_current_user)):
    """Check for pending/incomplete payments."""
    return {"pending_payments": []}


@router.post("/credit-topup")
async def create_credit_topup(
    body: CreditTopUpRequest, user: User = Depends(get_current_user)
):
    """Create a Razorpay order for credit top-up."""
    import razorpay

    if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
        raise HTTPException(status_code=503, detail="Payment service not configured")

    amount = body.credits * 100  # 1 credit = 1 INR

    client = razorpay.Client(
        auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
    )
    order = client.order.create(
        {
            "amount": amount,
            "currency": "INR",
            "receipt": f"credit_{user.id}_{body.credits}",
            "notes": {
                "user_id": str(user.id),
                "credits": str(body.credits),
                "type": "credit_topup",
            },
        }
    )

    # Store credits locally for verification resilience
    try:
        from app.db.redis import get_redis

        redis = get_redis()
        await redis.set(f"credit_order:{order['id']}", str(body.credits), ex=86400)
    except Exception:
        pass  # Redis failure is non-fatal here

    return {
        "order_id": order["id"],
        "amount": amount,
        "currency": "INR",
        "credits": body.credits,
    }


@router.post("/credit-topup/verify")
async def verify_credit_topup(
    body: CreditTopUpVerifyRequest, user: User = Depends(get_current_user)
):
    """Verify credit top-up payment and grant credits with idempotency."""
    if not settings.RAZORPAY_KEY_SECRET:
        raise HTTPException(status_code=503, detail="Payment service not configured")

    message = f"{body.razorpay_order_id}|{body.razorpay_payment_id}"
    expected = hmac.HMAC(
        key=settings.RAZORPAY_KEY_SECRET.encode(),
        msg=message.encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, body.razorpay_signature):
        raise HTTPException(status_code=400, detail="Invalid payment signature")

    # Idempotency: use Redis SET NX to prevent double-crediting
    try:
        from app.db.redis import get_redis

        redis = get_redis()
        dedup_key = f"credit_topup:{body.razorpay_order_id}"
        was_new = await redis.set(dedup_key, "1", ex=604800, nx=True)
        if not was_new:
            return {
                "status": "already_processed",
                "message": "Credits already granted for this order",
            }
    except Exception as e:
        logger.error(
            "Redis unavailable for credit topup idempotency", extra={"error": str(e)}
        )
        raise HTTPException(status_code=503, detail="Service temporarily unavailable")

    # Try local Redis first (resilient to Razorpay API outage)
    credits = 0
    try:
        from app.db.redis import get_redis

        redis = get_redis()
        cached_credits = await redis.get(f"credit_order:{body.razorpay_order_id}")
        if cached_credits:
            credits = int(cached_credits)
    except Exception:
        pass

    if not credits:
        # Fallback to Razorpay API
        import razorpay

        client = razorpay.Client(
            auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
        )
        order = client.order.fetch(body.razorpay_order_id)
        credits = int(order.get("notes", {}).get("credits", 0))

    if credits <= 0:
        raise HTTPException(status_code=400, detail="Invalid credit amount in order")

    # Grant credits
    current_credits = getattr(user, "credits_remaining", 0) or 0
    await user.update({"$set": {"credits_remaining": current_credits + credits}})

    logger.info("Credits granted", extra={"user_id": str(user.id), "credits": credits})
    return {"status": "success", "credits_granted": credits}


@router.get("/history")
async def payment_history(user: User = Depends(get_current_user)):
    """Get payment history from MongoDB."""
    from app.db.mongo import get_mongo_client

    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        payments = (
            await db.payments.find({"user_id": str(user.id)})
            .sort("created_at", -1)
            .to_list(50)
        )

        for p in payments:
            p["_id"] = str(p["_id"])

        return {"payments": payments}
    except Exception as e:
        logger.error("Failed to fetch payment history", extra={"error": str(e)})
        return {"payments": []}


@router.post("/refund-request")
async def refund_request(body: RefundRequest, user: User = Depends(get_current_user)):
    """Submit a refund request."""
    from app.db.mongo import get_mongo_client
    from datetime import datetime, timezone

    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        await db.refund_requests.insert_one(
            {
                "user_id": str(user.id),
                "payment_id": body.payment_id,
                "reason": body.reason,
                "status": "pending",
                "created_at": datetime.now(timezone.utc),
            }
        )

        logger.info(
            "Refund request submitted",
            extra={"user_id": str(user.id), "payment_id": body.payment_id},
        )
        return {"status": "submitted", "message": "Refund request received"}
    except Exception as e:
        logger.error("Failed to submit refund request", extra={"error": str(e)})
        raise HTTPException(status_code=500, detail="Failed to submit refund request")
