from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel
import logging

from app.models.user import User
from app.config import settings
from app.api.v1.auth import get_current_user
from app.services.payment.razorpay_client import PaymentNotConfiguredError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Subscription"])


class SubscriptionStatus(BaseModel):
    tier: str
    status: str
    current_period_end: str
    monthly_message_count: int
    monthly_limit: int


@router.get("/status", response_model=SubscriptionStatus)
async def get_subscription_status(user: User = Depends(get_current_user)):
    """Get current subscription status"""
    return SubscriptionStatus(
        tier=user.subscription_tier,
        status=user.subscription_status,
        current_period_end=user.current_period_end.isoformat() if hasattr(user.current_period_end, 'isoformat') else str(user.current_period_end or ""),
        monthly_message_count=user.monthly_message_count,
        monthly_limit=settings.RATE_LIMIT_PRO_TIER
        if user.is_pro()
        else settings.RATE_LIMIT_FREE_TIER,
    )


@router.post("/create-order")
async def create_subscription_order(user: User = Depends(get_current_user)):
    """Create Razorpay subscription order for Pro plan"""
    from app.services.payment.razorpay_client import create_subscription_order

    try:
        order = await create_subscription_order(user)
        return order
    except PaymentNotConfiguredError as e:
        logger.error(f"Failed to create subscription order: {e}")
        raise HTTPException(status_code=503, detail="Payment service not configured")
    except RuntimeError as e:
        error_msg = str(e)
        logger.error(f"Failed to create subscription order: {error_msg}")
        raise HTTPException(status_code=502, detail="Payment gateway error")
    except Exception as e:
        logger.error(f"Failed to create subscription order: {e}")
        raise HTTPException(status_code=500, detail="Failed to create order")


@router.post("/cancel")
async def cancel_subscription(user: User = Depends(get_current_user)):
    """Cancel subscription at end of billing period"""
    if not user.razorpay_subscription_id:
        raise HTTPException(status_code=400, detail="No active subscription found")

    from app.services.payment.razorpay_client import cancel_razorpay_subscription

    try:
        await cancel_razorpay_subscription(user.razorpay_subscription_id)
        await user.update({"$set": {"cancel_at_period_end": True}})
        logger.info(f"Subscription cancelled for user {user.email}")
        return {"status": "success", "message": "Subscription will end at period end"}
    except PaymentNotConfiguredError as e:
        logger.error(f"Failed to cancel subscription: {e}")
        raise HTTPException(status_code=503, detail="Payment service not configured")
    except RuntimeError as e:
        error_msg = str(e)
        logger.error(f"Failed to cancel subscription: {error_msg}")
        raise HTTPException(status_code=502, detail="Payment gateway error")
    except Exception as e:
        logger.error(f"Failed to cancel subscription: {e}")
        raise HTTPException(status_code=500, detail="Failed to cancel subscription")


@router.post("/cron/downgrade-expired")
async def downgrade_expired_subscriptions(request: Request):
    """Cron endpoint to downgrade expired subscriptions. Protected by cron secret."""
    cron_secret = request.headers.get("X-Cron-Secret")
    if not cron_secret or cron_secret != settings.TRANSLATE_CRON_SECRET:
        raise HTTPException(status_code=403, detail="Unauthorized")

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)

    expired_users = await User.find(
        {"cancel_at_period_end": True, "current_period_end": {"$lt": now}}
    ).to_list()

    downgraded = 0
    for u in expired_users:
        await u.update({"$set": {
            "subscription_tier": "free",
            "subscription_status": "cancelled",
            "cancel_at_period_end": False,
        }})
        downgraded += 1

    logger.info(f"Downgraded {downgraded} expired subscriptions")
    return {"status": "ok", "downgraded": downgraded}
