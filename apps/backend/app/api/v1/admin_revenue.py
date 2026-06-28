"""
Admin Revenue Endpoints
Revenue overview and subscription management.
"""

from fastapi import APIRouter, Depends
import logging

from app.api.v1.admin import require_admin_session, csrf_guard
from app.config import settings
from app.db.mongo import get_mongo_client

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Admin Revenue"],
    dependencies=[Depends(require_admin_session), Depends(csrf_guard)],
)


@router.get("/revenue/overview")
async def revenue_overview():
    """Revenue overview: pro users, Razorpay transaction sum."""
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        pro_users = await db.users.count_documents(
            {"subscription_tier": "pro", "subscription_status": "active"}
        )

        # Sum captured Razorpay transactions (amount is in paise)
        txn_agg = await (await db.transactions.aggregate(
            [
                {"$match": {"status": "captured"}},
                {"$group": {"_id": None, "total_paise": {"$sum": "$amount"}}},
            ]
        )).to_list(length=1)
        total_inr = round((txn_agg[0]["total_paise"] if txn_agg else 0) / 100, 2)

        # Current month
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        month_agg = await (await db.transactions.aggregate(
            [
                {"$match": {"status": "captured", "created_at": {"$gte": month_start}}},
                {"$group": {"_id": None, "total_paise": {"$sum": "$amount"}}},
            ]
        )).to_list(length=1)
        month_inr = round((month_agg[0]["total_paise"] if month_agg else 0) / 100, 2)

        txn_count = await db.transactions.count_documents({"status": "captured"})

        return {
            "pro_users": pro_users,
            "revenue_total_inr": total_inr,
            "revenue_month_inr": month_inr,
            "transaction_count": txn_count,
            "currency": "INR",
            "source": "transactions_collection",
        }
    except Exception as e:
        logger.error(f"Revenue overview error: {e}")
        return {
            "pro_users": 0,
            "revenue_total_inr": 0,
            "revenue_month_inr": 0,
            "transaction_count": 0,
            "currency": "INR",
            "source": "unavailable",
        }


@router.get("/revenue/subscriptions")
async def list_subscriptions():
    """List active subscriptions."""
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        cursor = (
            db.users.find(
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
            )
            .sort("created_at", -1)
            .limit(100)
        )

        users_raw = await cursor.to_list(length=100)

        subscriptions = []
        for u in users_raw:
            subscriptions.append(
                {
                    "user_id": str(u["_id"]),
                    "email": u.get("email"),
                    "name": u.get("name"),
                    "tier": u.get("subscription_tier"),
                    "status": u.get("subscription_status"),
                    "razorpay_id": u.get("razorpay_subscription_id"),
                    "period_start": u.get("current_period_start", "").isoformat()
                    if u.get("current_period_start")
                    else None,
                    "period_end": u.get("current_period_end", "").isoformat()
                    if u.get("current_period_end")
                    else None,
                }
            )

        return {"subscriptions": subscriptions, "total": len(subscriptions)}
    except Exception as e:
        logger.error(f"List subscriptions error: {e}")
        return {"subscriptions": [], "total": 0}


@router.get("/monetization/funnel")
async def monetization_funnel():
    """
    Conversion funnel: anonymous → registered → pro subscriber.
    """
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        total_users = await db.users.count_documents({})
        registered = await db.users.count_documents(
            {"auth_provider": "local", "email": {"$exists": True, "$ne": None}}
        )
        pro = await db.users.count_documents({"subscription_tier": "pro"})
        anon = total_users - registered
        return {
            "funnel": [
                {"stage": "anonymous", "count": anon},
                {"stage": "registered", "count": registered},
                {"stage": "pro", "count": pro},
            ],
            "conversion": {
                "anon_to_registered_pct": round(registered / total_users * 100, 1) if total_users else 0,
                "registered_to_pro_pct": round(pro / registered * 100, 1) if registered else 0,
            },
            "source": "mongodb",
        }
    except Exception as e:
        logger.error(f"monetization/funnel error: {e}")
        return {
            "funnel": [],
            "conversion": {"anon_to_registered_pct": 0, "registered_to_pro_pct": 0},
            "source": "unavailable",
        }
