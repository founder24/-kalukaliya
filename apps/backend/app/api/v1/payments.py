from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone
import hashlib
import hmac
import logging

from app.models.user import User
from app.config import settings
from app.api.v1.auth import get_current_user
from app.services.payment.razorpay_client import razorpay_client, create_subscription_order, PaymentNotConfiguredError
from app.db.mongo import get_mongo_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Payments"])


class CreateOrderRequest(BaseModel):
    plan: str = "pro"


class VerifyPaymentRequest(BaseModel):
    razorpay_payment_id: str
    razorpay_subscription_id: str
    razorpay_signature: str


class CreditTopUpRequest(BaseModel):
    credits: int
    provider: str = "razorpay"


class CreditTopUpVerifyRequest(BaseModel):
    razorpay_payment_id: str
    razorpay_order_id: str
    razorpay_signature: str


class RefundRequest(BaseModel):
    payment_id: str
    reason: Optional[str] = ""


@router.post("/create-order")
async def create_payment_order(body: CreateOrderRequest, user: User = Depends(get_current_user)):
    """Create a payment order for subscription"""
    try:
        order = await create_subscription_order(user)
        return order
    except PaymentNotConfiguredError:
        raise HTTPException(status_code=503, detail="Payment service not configured")
    except RuntimeError as e:
        logger.error(f"Create order failed: {e}")
        raise HTTPException(status_code=502, detail="Payment gateway error")


@router.post("/verify")
async def verify_payment(body: VerifyPaymentRequest, user: User = Depends(get_current_user)):
    """Verify Razorpay payment signature and activate subscription"""
    if not settings.RAZORPAY_KEY_SECRET:
        raise HTTPException(status_code=503, detail="Payment service not configured")

    # Verify signature
    message = f"{body.razorpay_payment_id}|{body.razorpay_subscription_id}"
    expected_sig = hmac.HMAC(
        settings.RAZORPAY_KEY_SECRET.encode(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_sig, body.razorpay_signature):
        raise HTTPException(status_code=400, detail="Invalid payment signature")

    # Activate subscription
    await user.update({
        "$set": {
            "subscription_tier": "pro",
            "subscription_status": "active",
            "razorpay_subscription_id": body.razorpay_subscription_id,
            "monthly_message_count": 0,
        }
    })

    # Record payment
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        await db.payments.insert_one({
            "user_id": str(user.id),
            "payment_id": body.razorpay_payment_id,
            "subscription_id": body.razorpay_subscription_id,
            "type": "subscription",
            "status": "captured",
            "created_at": datetime.now(timezone.utc),
        })
    except Exception as e:
        logger.error(f"Failed to record payment: {e}")

    return {"status": "ok", "message": "Payment verified, subscription activated"}


@router.post("/recover")
async def recover_payment(user: User = Depends(get_current_user)):
    """Check for pending/incomplete payments"""
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        pending = await db.payments.find_one({
            "user_id": str(user.id),
            "status": "pending",
        })
        if pending:
            return {"status": "pending", "payment_id": pending.get("payment_id")}
        return {"status": "none"}
    except Exception as e:
        logger.error(f"Payment recovery check failed: {e}")
        return {"status": "none"}


@router.post("/credit-topup")
async def create_credit_topup(body: CreditTopUpRequest, user: User = Depends(get_current_user)):
    """Create a one-time order for credit top-up"""
    if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
        raise HTTPException(status_code=503, detail="Payment service not configured")

    # Calculate amount based on credits (e.g., 1 INR per credit)
    amount = body.credits * 100  # amount in paise

    try:
        import httpx
        async with httpx.AsyncClient(
            auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET),
            timeout=30.0,
        ) as client:
            response = await client.post(
                "https://api.razorpay.com/v1/orders",
                json={
                    "amount": amount,
                    "currency": "INR",
                    "receipt": f"credit_{user.id}_{int(datetime.now(timezone.utc).timestamp())}",
                    "notes": {
                        "user_id": str(user.id),
                        "type": "credit_topup",
                        "credits": body.credits,
                    },
                },
            )
            response.raise_for_status()
            order = response.json()

        return {
            "order_id": order["id"],
            "amount": order["amount"],
            "currency": order["currency"],
            "credits": body.credits,
        }
    except Exception as e:
        logger.error(f"Credit topup order failed: {e}")
        raise HTTPException(status_code=502, detail="Payment gateway error")


@router.post("/credit-topup/verify")
async def verify_credit_topup(body: CreditTopUpVerifyRequest, user: User = Depends(get_current_user)):
    """Verify credit top-up payment and credit the user"""
    if not settings.RAZORPAY_KEY_SECRET:
        raise HTTPException(status_code=503, detail="Payment service not configured")

    # Verify signature
    message = f"{body.razorpay_order_id}|{body.razorpay_payment_id}"
    expected_sig = hmac.HMAC(
        settings.RAZORPAY_KEY_SECRET.encode(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_sig, body.razorpay_signature):
        raise HTTPException(status_code=400, detail="Invalid payment signature")

    # Get order details to determine credits
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        # Record payment and credit user
        await db.payments.insert_one({
            "user_id": str(user.id),
            "payment_id": body.razorpay_payment_id,
            "order_id": body.razorpay_order_id,
            "type": "credit_topup",
            "status": "captured",
            "created_at": datetime.now(timezone.utc),
        })

        # Reset/reduce message count (effectively adding credits)
        current_count = user.monthly_message_count
        new_count = max(0, current_count - 50)  # Add 50 credits
        await user.update({"$set": {"monthly_message_count": new_count}})

    except Exception as e:
        logger.error(f"Credit topup verification failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to process credit topup")

    return {"status": "ok", "message": "Credits added successfully"}


@router.get("/history")
async def get_payment_history(user: User = Depends(get_current_user)):
    """Get payment history for current user"""
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        cursor = db.payments.find(
            {"user_id": str(user.id)}
        ).sort("created_at", -1).limit(50)
        payments_raw = await cursor.to_list(length=50)

        payments = []
        for p in payments_raw:
            payments.append({
                "id": str(p["_id"]),
                "payment_id": p.get("payment_id"),
                "type": p.get("type", "subscription"),
                "status": p.get("status"),
                "amount": p.get("amount"),
                "created_at": p.get("created_at", "").isoformat() if p.get("created_at") else None,
            })

        return {"payments": payments}
    except Exception as e:
        logger.error(f"Payment history error: {e}")
        return {"payments": []}


@router.post("/refund-request")
async def request_refund(body: RefundRequest, user: User = Depends(get_current_user)):
    """Submit a refund request"""
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        await db.refund_requests.insert_one({
            "user_id": str(user.id),
            "payment_id": body.payment_id,
            "reason": body.reason,
            "status": "pending",
            "created_at": datetime.now(timezone.utc),
        })

        return {"status": "ok", "message": "Refund request submitted"}
    except Exception as e:
        logger.error(f"Refund request failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to submit refund request")
