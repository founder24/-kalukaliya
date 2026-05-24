"""
Admin Revenue Endpoints
Revenue overview and subscription management.
"""

from fastapi import APIRouter, Request
import logging

from app.api.v1.admin import _validate_admin_session
from app.config import settings
from app.db.mongo import get_mongo_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin Revenue"])


@router.get("/revenue/overview")
async def revenue_overview(request: Request):
    """Revenue overview: pro users, monthly revenue estimate."""
    _validate_admin_session(request)

    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        pro_users = await db.users.count_documents(
            {"subscription_tier": "pro", "subscription_status": "active"}
        )

        # Placeholder revenue calculation
        # In production, integrate with Razorpay API
        estimated_monthly_revenue = pro_users * 299  # INR per month estimate

        return {
            "pro_users": pro_users,
            "monthly_revenue": estimated_monthly_revenue,
            "currency": "INR",
            "source": "estimate",
        }
    except Exception as e:
        logger.error(f"Revenue overview error: {e}")
        return {
            "pro_users": 0,
            "monthly_revenue": 0,
            "currency": "INR",
            "source": "estimate",
        }


@router.get("/revenue/subscriptions")
async def list_subscriptions(request: Request):
    """List active subscriptions."""
    _validate_admin_session(request)

    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        cursor = db.users.find(
            {"subscription_tier": "pro"},
            {
                "email": 1,
                "name": 1,
                "subscription_tier": 1,
                "subscription_status": 1,
                "razorpay_subscription_id": 1,
                "current_period_start": 1,
                "current_period_end": 1,
                "created_at": 1,
            },
        ).sort("created_at", -1).limit(100)

        users_raw = await cursor.to_list(length=100)

        subscriptions = []
        for u in users_raw:
            subscriptions.append({
                "user_id": str(u["_id"]),
                "email": u.get("email"),
                "name": u.get("name"),
                "tier": u.get("subscription_tier"),
                "status": u.get("subscription_status"),
                "razorpay_id": u.get("razorpay_subscription_id"),
                "period_start": u.get("current_period_start", "").isoformat() if u.get("current_period_start") else None,
                "period_end": u.get("current_period_end", "").isoformat() if u.get("current_period_end") else None,
            })

        return {"subscriptions": subscriptions, "total": len(subscriptions)}
    except Exception as e:
        logger.error(f"List subscriptions error: {e}")
        return {"subscriptions": [], "total": 0}
