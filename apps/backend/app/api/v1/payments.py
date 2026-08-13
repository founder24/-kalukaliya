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


def _make_receipt_token(order_id: str, payment_id: str) -> str:
    """Return a short opaque token proving that a real verify call succeeded.

    The token is HMAC-signed with RAZORPAY_KEY_SECRET so it cannot be forged
    without the server secret.  The frontend stores it in sessionStorage and
    PaymentSuccessPage checks for it before rendering.
    """
    from app.config import settings

    secret = (settings.RAZORPAY_KEY_SECRET or "fallback-receipt-secret").encode()
    msg = f"receipt:{order_id}:{payment_id}".encode()
    return hmac.HMAC(key=secret, msg=msg, digestmod=hashlib.sha256).hexdigest()[:32]


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

    # Idempotency: use Redis SET NX to prevent double-upgrade on retry / double-click.
    # The key is held as a processing lock; it is deleted on any failure so the
    # customer can retry.  Only a fully successful upgrade leaves the key in place.
    _dedup_redis = None
    _dedup_key = None
    try:
        from app.db.redis import get_redis

        _dedup_redis = get_redis()
        _dedup_key = f"sub_verify:{body.razorpay_order_id}"
        _was_new = await _dedup_redis.set(_dedup_key, "1", ex=604800, nx=True)
        if not _was_new:
            logger.info(
                "Duplicate subscription verify ignored",
                extra={"order_id": body.razorpay_order_id},
            )
            return {
                "status": "already_processed",
                "message": "Subscription upgrade already processed for this order",
            }
    except Exception as _e:
        # Fail-open: Redis unavailable → continue processing.
        # Double-upgrade is idempotent for subscription tier; receipt duplicate is
        # the only real harm, which is acceptable over blocking a legitimate upgrade.
        logger.warning(
            "Redis unavailable for subscription verify idempotency; processing anyway",
            extra={"error": str(_e)},
        )
        _dedup_redis = None
        _dedup_key = None

    try:
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

        # Last-resort fallback: fetch the order directly from Razorpay when both Redis and
        # MongoDB have no record (e.g. MongoDB write failed silently at order-creation time).
        # This mirrors the pattern used in /credit-topup/verify.
        if (not purchased_plan or payment_amount is None) and settings.RAZORPAY_KEY_ID:
            import razorpay as _razorpay

            try:
                _rz_client = _razorpay.Client(
                    auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
                )
                _rz_order = await asyncio.to_thread(
                    _rz_client.order.fetch, body.razorpay_order_id
                )
                _fetched_plan = _rz_order.get("notes", {}).get("plan")
                _fetched_amount = _rz_order.get("amount")
                if _fetched_plan and _fetched_plan in plan_prices:
                    if not purchased_plan:
                        purchased_plan = _fetched_plan
                        logger.info(
                            "Resolved plan from Razorpay API fallback (Redis + MongoDB miss)",
                            extra={
                                "order_id": body.razorpay_order_id,
                                "plan": purchased_plan,
                            },
                        )
                    if payment_amount is None and _fetched_amount is not None:
                        payment_amount = int(_fetched_amount)
            except Exception as e:
                logger.error(
                    f"Razorpay order fetch fallback failed for order "
                    f"{body.razorpay_order_id}: {e}"
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

        receipt_token = _make_receipt_token(body.razorpay_order_id, body.razorpay_payment_id)
        logger.info(f"Payment verified, user upgraded to {purchased_plan}", extra={"user_id": str(user.id)})
        return {
            "status": "success",
            "message": f"Payment verified, plan upgraded to {purchased_plan}",
            "receipt_token": receipt_token,
        }

    except Exception:
        # Processing failed after the idempotency key was set — delete the lock so the
        # customer can retry.  If Redis is already gone, ignore the cleanup error.
        if _dedup_redis is not None and _dedup_key is not None:
            try:
                await _dedup_redis.delete(_dedup_key)
            except Exception:
                pass
        raise


@router.post("/recover")
async def recover_payment(user: User = Depends(get_current_user)):
    """
    Return any pending (unverified) payments for the current user.

    A payments_pending record is written at order-creation time and deleted on
    successful verify.  If verify failed mid-flight the record survives and can
    be used to retry.  This endpoint surfaces those orphaned records so the
    client can offer a retry or show a helpful message.

    Note: records older than 2 days are auto-expired by MongoDB TTL.  Only
    live, retryable records are returned here.
    """
    from app.db.mongo import get_mongo_client
    from datetime import datetime, timezone

    try:
        mongo = get_mongo_client()
        db = mongo[settings.MONGODB_DB_NAME]
        now = datetime.now(timezone.utc)
        # Only return records that have not yet expired
        cursor = db.payments_pending.find(
            {"user_id": str(user.id), "expires_at": {"$gt": now}}
        ).sort("created_at", -1)
        records = await cursor.to_list(length=20)
        for r in records:
            r["_id"] = str(r["_id"])
            # Serialize datetime fields for JSON
            for field in ("created_at", "expires_at"):
                if field in r and isinstance(r[field], datetime):
                    r[field] = r[field].isoformat()
        return {"pending_payments": records}
    except Exception as e:
        logger.error(f"Failed to fetch pending payments for recovery: {e}")
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

    receipt_token = _make_receipt_token(body.razorpay_order_id, body.razorpay_payment_id)
    logger.info("Credits granted", extra={"user_id": str(user.id), "credits": credits})
    return {"status": "success", "credits_granted": credits, "receipt_token": receipt_token}


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


class CreateQRRequest(BaseModel):
    plan: str


async def _rz_post(path: str, data: dict) -> dict:
    """Direct Razorpay REST call (used for endpoints without SDK wrappers)."""
    import httpx

    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            f"https://api.razorpay.com/v1{path}",
            json=data,
            auth=(settings.RAZORPAY_KEY_ID or "", settings.RAZORPAY_KEY_SECRET or ""),
        )
        r.raise_for_status()
        return r.json()


async def _rz_get(path: str, params: dict | None = None) -> dict:
    import httpx

    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(
            f"https://api.razorpay.com/v1{path}",
            params=params,
            auth=(settings.RAZORPAY_KEY_ID or "", settings.RAZORPAY_KEY_SECRET or ""),
        )
        r.raise_for_status()
        return r.json()


@router.post("/create-qr")
async def create_payment_qr(
    body: CreateQRRequest, user: User = Depends(get_current_user)
):
    """
    Create a Razorpay UPI QR code with the plan amount pre-loaded.
    Returns image_url (a direct PNG hosted by Razorpay) for display in the
    payment modal.  No Razorpay checkout.js required — works in all browsers.
    """
    import time as _time

    if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
        raise HTTPException(status_code=503, detail="Payment service not configured")

    plan_prices = {"starter": 9900, "pro": 99900}
    plan_labels = {"starter": "Starter", "pro": "Pro"}
    amount = plan_prices.get(body.plan)
    if not amount:
        raise HTTPException(status_code=400, detail="Invalid plan")

    expires_in = 1800  # 30 minutes

    try:
        qr = await _rz_post(
            "/payments/qr_codes",
            {
                "type": "upi_qr",
                "name": f"Syrabit {plan_labels[body.plan]}",
                "usage": "single_use",
                "fixed_amount": True,
                "payment_amount": amount,
                "description": f"Syrabit {plan_labels[body.plan]} — ₹{amount // 100}",
                "close_by": int(_time.time()) + expires_in,
                "customer": {
                    "name": user.name or "Student",
                    "email": user.email,
                },
            },
        )
    except Exception as e:
        logger.error(f"Razorpay QR code creation failed: {e}")
        raise HTTPException(status_code=503, detail="Payment gateway unavailable")

    qr_id = qr.get("id")
    image_url = qr.get("image_url")
    if not qr_id or not image_url:
        logger.error(f"Unexpected Razorpay QR response: {qr}")
        raise HTTPException(status_code=503, detail="Payment gateway returned unexpected response")

    # Persist for polling / user association
    from app.db.mongo import get_mongo_client
    from datetime import datetime, timezone, timedelta

    try:
        mongo = get_mongo_client()
        db = mongo[settings.MONGODB_DB_NAME]
        await db.payments_pending.replace_one(
            {"qr_code_id": qr_id},
            {
                "qr_code_id": qr_id,
                "user_id": str(user.id),
                "plan": body.plan,
                "amount": amount,
                "created_at": datetime.now(timezone.utc),
                "expires_at": datetime.now(timezone.utc) + timedelta(seconds=expires_in),
            },
            upsert=True,
        )
    except Exception as e:
        logger.error(f"Failed to persist QR code record: {e}")

    return {
        "qr_code_id": qr_id,
        "image_url": image_url,
        "amount": amount,
        "expires_in": expires_in,
    }


@router.get("/poll-qr/{qr_code_id}")
async def poll_qr_payment(
    qr_code_id: str, user: User = Depends(get_current_user)
):
    """
    Poll for payment on a UPI QR code.  Called every few seconds by the
    payment modal.  Returns { status: "pending" } until the payment is
    captured; then upgrades the user, records the payment, and returns
    { status: "paid", receipt_token }.
    """
    if not settings.RAZORPAY_KEY_ID or not settings.RAZORPAY_KEY_SECRET:
        raise HTTPException(status_code=503, detail="Payment service not configured")

    # Security: verify QR code belongs to the requesting user
    from app.db.mongo import get_mongo_client

    mongo = get_mongo_client()
    db = mongo[settings.MONGODB_DB_NAME]
    record = await db.payments_pending.find_one({"qr_code_id": qr_code_id})
    if not record or record.get("user_id") != str(user.id):
        raise HTTPException(status_code=404, detail="QR code not found")

    plan = record.get("plan")
    expected_amount = record.get("amount")

    # Fetch payments for this QR code from Razorpay
    try:
        resp = await _rz_get(f"/payments/qr_codes/{qr_code_id}/payments")
    except Exception as e:
        logger.warning(f"Razorpay QR poll failed: {e}")
        return {"status": "pending"}

    items = resp.get("items", [])
    payment = None
    for p in items:
        if p.get("status") == "captured" and p.get("amount") == expected_amount:
            payment = p
            break

    if not payment:
        return {"status": "pending"}

    payment_id = payment["id"]

    # Idempotency: Redis SET NX (non-fatal if Redis is down)
    try:
        from app.db.redis import get_redis

        redis = get_redis()
        dedup_key = f"qr_verify:{qr_code_id}"
        was_new = await redis.set(dedup_key, "1", ex=604800, nx=True)
        if not was_new:
            # Already processed — still return paid so frontend can redirect
            return {"status": "paid", "plan": plan, "payment_id": payment_id, "amount": expected_amount}
    except Exception:
        pass

    # Upgrade user
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    plan_prices = {"starter": 9900, "pro": 99900}
    await user.update(
        {
            "$set": {
                "subscription_tier": plan,
                "subscription_status": "active",
                "razorpay_subscription_id": qr_code_id,
                "current_period_start": now,
                "current_period_end": now + timedelta(days=30),
                "cancel_at_period_end": False,
            }
        }
    )

    # Durable payment record
    try:
        await db.payments.insert_one(
            {
                "user_id": str(user.id),
                "razorpay_order_id": qr_code_id,
                "razorpay_payment_id": payment_id,
                "amount": expected_amount,
                "status": "completed",
                "type": "subscription",
                "created_at": now,
            }
        )
        await db.payments_pending.delete_one({"qr_code_id": qr_code_id})
    except Exception as e:
        logger.error(f"Failed to record QR payment: {e}")

    # Receipt email (non-fatal)
    try:
        from app.services.comms.resend_client import send_first_purchase_receipt_email

        await send_first_purchase_receipt_email(user.email, expected_amount, qr_code_id)
    except Exception as e:
        logger.error(f"Failed to send QR receipt email: {e}")

    receipt_token = _make_receipt_token(qr_code_id, payment_id)
    logger.info(
        f"QR payment verified, user upgraded to {plan}",
        extra={"user_id": str(user.id), "qr_code_id": qr_code_id},
    )
    return {
        "status": "paid",
        "plan": plan,
        "payment_id": payment_id,
        "amount": expected_amount,
        "receipt_token": receipt_token,
    }


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
