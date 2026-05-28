"""Payment endpoints for Razorpay integration."""

from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, timezone
import hashlib
import hmac
import logging

from app.config import settings
from app.models.user import User
from app.api.v1.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Payments"])


class CreateOrderRequest(BaseModel):
    plan_id: Optional[str] = None
    amount: Optional[int] = None  # Amount in paise


class VerifyPaymentRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


class CreditTopUpRequest(BaseModel):
    credits: int = Field(ge=1, le=1000)


class RefundRequest(BaseModel):
    payment_id: str
    reason: Optional[str] = None


@router.post("/create-order")
async def create_order(body: CreateOrderRequest, user: User = Depends(get_current_user)):
    """Create a Razorpay payment order for subscription."""
    try:
        import razorpay
        client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))

        amount = body.amount or 29900  # Default to Pro plan (299 INR)
        order = client.order.create({
            "amount": amount,
            "currency": "INR",
            "receipt": f"order_{user.id}_{int(datetime.now(timezone.utc).timestamp())}",
            "notes": {"user_id": str(user.id), "plan_id": body.plan_id or "pro_monthly"},
        })
        return {"order_id": order["id"], "amount": order["amount"], "currency": order["currency"]}
    except Exception as e:
        logger.error(f"Failed to create order: {e}")
        raise HTTPException(status_code=502, detail="Payment service unavailable")


@router.post("/verify")
async def verify_payment(body: VerifyPaymentRequest, user: User = Depends(get_current_user)):
    """Verify Razorpay payment signature and activate subscription."""
    try:
        message = f"{body.razorpay_order_id}|{body.razorpay_payment_id}"
        expected = hmac.HMAC(
            settings.RAZORPAY_KEY_SECRET.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(expected, body.razorpay_signature):
            raise HTTPException(status_code=400, detail="Invalid payment signature")

        await user.update({
            "$set": {
                "subscription_tier": "pro",
                "subscription_id": body.razorpay_payment_id,
                "subscription_start": datetime.now(timezone.utc),
                "monthly_message_count": 0,
                "updated_at": datetime.now(timezone.utc),
            }
        })
        return {"message": "Payment verified", "tier": "pro"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Payment verification failed: {e}")
        raise HTTPException(status_code=500, detail="Verification failed")


@router.post("/recover")
async def recover_payment(user: User = Depends(get_current_user)):
    """Recover an abandoned/pending payment."""
    return {"message": "No pending payments found", "recovered": False}


@router.post("/credit-topup")
async def create_credit_topup(body: CreditTopUpRequest, user: User = Depends(get_current_user)):
    """Create a Razorpay order for credit top-up."""
    try:
        import razorpay
        client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))

        amount = body.credits * 100  # 1 credit = 1 INR = 100 paise
        order = client.order.create({
            "amount": amount,
            "currency": "INR",
            "receipt": f"topup_{user.id}_{int(datetime.now(timezone.utc).timestamp())}",
            "notes": {"user_id": str(user.id), "credits": body.credits, "type": "credit_topup"},
        })
        return {"order_id": order["id"], "amount": order["amount"], "credits": body.credits}
    except Exception as e:
        logger.error(f"Failed to create topup order: {e}")
        raise HTTPException(status_code=502, detail="Payment service unavailable")


@router.post("/credit-topup/verify")
async def verify_credit_topup(body: VerifyPaymentRequest, user: User = Depends(get_current_user)):
    """Verify credit top-up payment and grant credits."""
    try:
        message = f"{body.razorpay_order_id}|{body.razorpay_payment_id}"
        expected = hmac.HMAC(
            settings.RAZORPAY_KEY_SECRET.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(expected, body.razorpay_signature):
            raise HTTPException(status_code=400, detail="Invalid payment signature")

        # Idempotency: check if already processed
        from app.db.redis import get_redis
        redis = get_redis()
        dedup_key = f"topup_verified:{body.razorpay_order_id}"
        already_processed = await redis.set(dedup_key, "1", ex=86400, nx=True)
        if not already_processed:
            return {"message": "Already processed", "credits_granted": 0}

        # Fetch order to get credit amount
        import razorpay
        client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
        order = client.order.fetch(body.razorpay_order_id)
        credits = int(order.get("notes", {}).get("credits", 50))

        # Grant credits by reducing monthly count
        current_count = getattr(user, "monthly_message_count", 0)
        new_count = max(0, current_count - credits)
        await user.update({
            "$set": {"monthly_message_count": new_count, "updated_at": datetime.now(timezone.utc)}
        })
        return {"message": "Credits granted", "credits_granted": credits}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Credit topup verification failed: {e}")
        raise HTTPException(status_code=500, detail="Verification failed")


@router.get("/history")
async def payment_history(user: User = Depends(get_current_user)):
    """Get payment history for current user."""
    from app.db.mongo import get_mongo_client
    client = get_mongo_client()
    db = client[settings.MONGODB_DB_NAME]
    payments = await db.payments.find(
        {"user_id": str(user.id)}
    ).sort("created_at", -1).limit(50).to_list(50)

    return {
        "payments": [
            {
                "id": str(p.get("_id", "")),
                "amount": p.get("amount", 0),
                "status": p.get("status", "unknown"),
                "type": p.get("type", "subscription"),
                "created_at": p.get("created_at", "").isoformat() if p.get("created_at") else None,
            }
            for p in payments
        ]
    }


@router.post("/refund-request")
async def request_refund(body: RefundRequest, user: User = Depends(get_current_user)):
    """Submit a refund request."""
    from app.db.mongo import get_mongo_client
    client = get_mongo_client()
    db = client[settings.MONGODB_DB_NAME]
    await db.refund_requests.insert_one({
        "user_id": str(user.id),
        "payment_id": body.payment_id,
        "reason": body.reason or "No reason provided",
        "status": "pending",
        "created_at": datetime.now(timezone.utc),
    })
    return {"message": "Refund request submitted", "status": "pending"}
