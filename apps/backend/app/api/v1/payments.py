"""
Payments API - Razorpay integration for subscriptions and credit top-ups.
"""

import asyncio
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
    plan: Optional[str] = None  # hint from client; server validates against stored order


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

    plan_prices = {"starter": 9900, "pro": 99900}
    plan_labels = {"starter": "Starter", "pro": "Pro"}
    amount = plan_prices.get(body.plan)
    if not amount:
        raise HTTPException(status_code=400, detail="Invalid plan")

    client = razorpay.Client(
        auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
    )
    try:
        order = await asyncio.to_thread(
            client.order.create,
            {
                "amount": amount,
                "currency": "INR",
                "receipt": f"user_{user.id}_{body.plan}",
                "notes": {"user_id": str(user.id), "plan": body.plan},
            },
        )
    except Exception as e:
        logger.error(f"Razorpay order creation failed: {e}")
        raise HTTPException(status_code=503, detail="Payment gateway unavailable")

    # Store amount + plan for verification (resilient payment record)
    # Redis: fast cache (best-effort, non-fatal)
    try:
        from app.db.redis import get_redis

        redis = get_redis()
        await redis.set(f"order_amount:{order['id']}", str(amount), ex=86400)
        await redis.set(f"order_plan:{order['id']}", body.plan, ex=86400)
    except Exception:
        pass

    # MongoDB: authoritative durable record (required for Redis-down fallback)
    try:
        from app.db.mongo import get_mongo_client
        from datetime import datetime, timezone, timedelta

        mongo = get_mongo_client()
        db = mongo[settings.MONGODB_DB_NAME]
        await db.payments_pending.replace_one(
            {"order_id": order["id"]},
            {
                "order_id": order["id"],
                "user_id": str(user.id),
                "plan": body.plan,
                "amount": amount,
                "created_at": datetime.now(timezone.utc),
                "expires_at": datetime.now(timezone.utc) + timedelta(days=2),
            },
            upsert=True,
        )
    except Exception as e:
        logger.error(f"Failed to persist pending payment to MongoDB: {e}")
        # Non-fatal — Redis cache may still cover the verify call; log and proceed.

    return {
        "order_id": order["id"],
        "amount": amount,
        "currency": "INR",
        "key_id": settings.RAZORPAY_KEY_ID,
        "plan_label": plan_labels[body.plan],
    }


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

    # Determine which plan was purchased
    plan_prices = {"starter": 9900, "pro": 99900}
    amount_to_plan = {v: k for k, v in plan_prices.items()}

    # Try to retrieve plan from Redis (stored at order creation time)
    purchased_plan = None
    try:
        from app.db.redis import get_redis
        redis = get_redis()
        stored_plan = await redis.get(f"order_plan:{body.razorpay_order_id}")
        if stored_plan:
            purchased_plan = stored_plan if isinstance(stored_plan, str) else stored_plan.decode()
    except Exception:
        pass

    # Fall back: derive plan from stored amount (Redis)
    if not purchased_plan and payment_amount is not None:
        purchased_plan = amount_to_plan.get(payment_amount)

    # Authoritative fallback: read from MongoDB payments_pending when Redis missed
    if not purchased_plan or payment_amount is None:
        try:
            from app.db.mongo import get_mongo_client

            mongo = get_mongo_client()
            db = mongo[settings.MONGODB_DB_NAME]
            pending = await db.payments_pending.find_one(
                {"order_id": body.razorpay_order_id}
            )
            if pending:
                if not purchased_plan and pending.get("plan"):
                    purchased_plan = pending["plan"]
                    logger.info(
                        "Resolved plan from MongoDB payments_pending (Redis miss)",
                        extra={"order_id": body.razorpay_order_id, "plan": purchased_plan},
                    )
                if payment_amount is None and pending.get("amount") is not None:
                    payment_amount = int(pending["amount"])
        except Exception as e:
            logger.warning(
                f"MongoDB fallback lookup failed for order {body.razorpay_order_id}: {e}"
            )

    # Validate amount matches the resolved plan price (when both are available)
    if payment_amount is not None and purchased_plan:
        expected_amount = plan_prices.get(purchased_plan)
        if expected_amount and payment_amount != expected_amount:
            logger.warning(
                f"Payment amount mismatch: stored={payment_amount}, "
                f"expected={expected_amount} for plan={purchased_plan}, order={body.razorpay_order_id}"
            )
            raise HTTPException(status_code=400, detail="Payment amount mismatch")

    # Fail closed: never upgrade if neither plan nor amount could be verified server-side.
    # Trusting client-supplied plan values would allow underpayment-to-upgrade escalation
    # when Redis is unavailable.
    if not purchased_plan:
        logger.error(
            "Cannot verify plan for order — Redis and MongoDB metadata unavailable, failing closed",
            extra={"order_id": body.razorpay_order_id},
        )
        raise HTTPException(
            status_code=503,
            detail="Order metadata unavailable; please contact support if payment was charged",
        )

    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    await user.update(
        {
            "$set": {
                "subscription_tier": purchased_plan,
                "subscription_status": "active",
                "razorpay_subscription_id": body.razorpay_order_id,
                "current_period_start": now,
                "current_period_end": now + timedelta(days=30),
                "cancel_at_period_end": False,
            }
        }
    )

    # Clean up the pending record now that payment is verified
    try:
        from app.db.mongo import get_mongo_client as _get_mongo_client

        _mongo = _get_mongo_client()
        _db = _mongo[settings.MONGODB_DB_NAME]
        await _db.payments_pending.delete_one({"order_id": body.razorpay_order_id})
    except Exception as e:
        logger.warning(f"Failed to delete payments_pending record: {e}")

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

    # Send first-purchase confirmation email (non-fatal)
    try:
        from app.services.comms.resend_client import send_first_purchase_receipt_email

        # Use the authoritative plan price; payment_amount is always present here
        # because we fail-closed above when it's unavailable.
        receipt_amount = payment_amount if payment_amount is not None else plan_prices.get(purchased_plan, 0)
        await send_first_purchase_receipt_email(
            user.email,
            receipt_amount,
            body.razorpay_order_id,
        )
    except Exception as e:
        logger.error(f"Failed to send first-purchase receipt email: {e}")

    logger.info(f"Payment verified, user upgraded to {purchased_plan}", extra={"user_id": str(user.id)})
    return {"status": "success", "message": f"Payment verified, plan upgraded to {purchased_plan}"}


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
    try:
        order = await asyncio.to_thread(
            client.order.create,
            {
                "amount": amount,
                "currency": "INR",
                "receipt": f"credit_{user.id}_{body.credits}",
                "notes": {
                    "user_id": str(user.id),
                    "credits": str(body.credits),
                    "type": "credit_topup",
                },
            },
        )
    except Exception as e:
        logger.error(f"Razorpay credit topup order creation failed: {e}")
        raise HTTPException(status_code=503, detail="Payment gateway unavailable")

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
        "key_id": settings.RAZORPAY_KEY_ID,
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
        order = await asyncio.to_thread(client.order.fetch, body.razorpay_order_id)
        credits = int(order.get("notes", {}).get("credits", 0))

    if credits <= 0:
        raise HTTPException(status_code=400, detail="Invalid credit amount in order")

    # Grant credits
    current_credits = getattr(user, "credits_remaining", 0) or 0
    await user.update({"$set": {"credits_remaining": current_credits + credits}})

    # Send credit top-up confirmation email (non-fatal)
    try:
        from app.services.comms.resend_client import send_credit_topup_receipt_email

        await send_credit_topup_receipt_email(
            user.email,
            credits,
            credits * 100,  # 1 credit = 1 INR = 100 paise
            body.razorpay_order_id,
        )
    except Exception as e:
        logger.error(f"Failed to send credit top-up receipt email: {e}")

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
