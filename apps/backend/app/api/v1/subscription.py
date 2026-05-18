from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import logging

from app.models.user import User
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Subscription"])


class SubscriptionStatus(BaseModel):
    tier: str
    status: str
    current_period_end: str
    monthly_message_count: int
    monthly_limit: int


@router.get("/status", response_model=SubscriptionStatus)
async def get_subscription_status(user: User = None):
    """Get current subscription status"""
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    return SubscriptionStatus(
        tier=user.subscription_tier,
        status=user.subscription_status,
        current_period_end=user.current_period_end.isoformat() if user.current_period_end else "",
        monthly_message_count=user.monthly_message_count,
        monthly_limit=settings.RATE_LIMIT_PRO_TIER if user.is_pro() else settings.RATE_LIMIT_FREE_TIER,
    )


@router.post("/create-order")
async def create_subscription_order(user: User = None):
    """Create Razorpay subscription order for Pro plan"""
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    from app.services.payment.razorpay_client import create_subscription_order
    
    try:
        order = await create_subscription_order(user)
        return order
    except Exception as e:
        logger.error(f"Failed to create subscription order: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create order: {str(e)}")


@router.post("/cancel")
async def cancel_subscription(user: User = None):
    """Cancel subscription at end of billing period"""
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    if not user.razorpay_subscription_id:
        raise HTTPException(status_code=400, detail="No active subscription found")
    
    from app.services.payment.razorpay_client import cancel_razorpay_subscription
    
    try:
        await cancel_razorpay_subscription(user.razorpay_subscription_id)
        await user.update({"$set": {"cancel_at_period_end": True}})
        logger.info(f"Subscription cancelled for user {user.email}")
        return {"status": "success", "message": "Subscription will end at period end"}
    except Exception as e:
        logger.error(f"Failed to cancel subscription: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to cancel: {str(e)}")
