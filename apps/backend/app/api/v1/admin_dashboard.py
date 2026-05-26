"""
Admin Dashboard Endpoints
Aggregate stats, health checks, and Cloudflare overview.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Request
import logging

from app.api.v1.admin import _validate_admin_session
from app.config import settings
from app.db.mongo import get_mongo_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin Dashboard"])


@router.get("/dashboard")
async def admin_dashboard(request: Request):
    """Aggregate stats for the admin dashboard overview."""
    _validate_admin_session(request)

    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        total_users = await db.users.count_documents({})
        active_today = await db.users.count_documents(
            {"updated_at": {"$gte": today_start}}
        )
        signups_today = await db.users.count_documents(
            {"created_at": {"$gte": today_start}}
        )

        total_messages = await db.chats.aggregate(
            [
                {"$project": {"msg_count": {"$size": {"$ifNull": ["$messages", []]}}}},
                {"$group": {"_id": None, "total": {"$sum": "$msg_count"}}},
            ]
        ).to_list(1)
        total_messages = total_messages[0]["total"] if total_messages else 0

        messages_today_result = await db.chats.aggregate(
            [
                {"$match": {"updated_at": {"$gte": today_start}}},
                {"$project": {"msg_count": {"$size": {"$ifNull": ["$messages", []]}}}},
                {"$group": {"_id": None, "total": {"$sum": "$msg_count"}}},
            ]
        ).to_list(1)
        messages_today = (
            messages_today_result[0]["total"] if messages_today_result else 0
        )

        pro_users = await db.users.count_documents({"subscription_tier": "pro"})
        free_users = await db.users.count_documents({"subscription_tier": "free"})

        return {
            "total_users": total_users,
            "active_today": active_today,
            "total_messages": total_messages,
            "messages_today": messages_today,
            "revenue_total": 0,
            "revenue_month": 0,
            "pro_users": pro_users,
            "free_users": free_users,
            "system_health": "ok",
            "signups_today": signups_today,
            "chat_fallbacks": {"source": "placeholder"},
            "latency": {"source": "placeholder"},
            "token_spend": {"source": "placeholder"},
            "top_queries": {"source": "placeholder"},
            "chat_speedups": {"source": "placeholder"},
            "vector_stats": {"source": "placeholder"},
        }
    except Exception as e:
        logger.error(f"Dashboard stats error: {e}")
        return {
            "total_users": 0,
            "active_today": 0,
            "total_messages": 0,
            "messages_today": 0,
            "revenue_total": 0,
            "revenue_month": 0,
            "pro_users": 0,
            "free_users": 0,
            "system_health": "degraded",
            "signups_today": 0,
            "chat_fallbacks": {"source": "placeholder"},
            "latency": {"source": "placeholder"},
            "token_spend": {"source": "placeholder"},
            "top_queries": {"source": "placeholder"},
            "chat_speedups": {"source": "placeholder"},
            "vector_stats": {"source": "placeholder"},
        }


@router.get("/health")
async def admin_health(request: Request):
    """Detailed dependency health check for admin panel."""
    _validate_admin_session(request)

    health = {"mongo": "unknown", "redis": "unknown"}

    try:
        client = get_mongo_client()
        await client.admin.command("ping")
        health["mongo"] = "healthy"
    except Exception as e:
        health["mongo"] = f"unhealthy: {str(e)}"

    try:
        from app.db.redis import get_redis

        redis = get_redis()
        if redis:
            await redis.ping()
            health["redis"] = "healthy"
        else:
            health["redis"] = "not configured"
    except Exception as e:
        health["redis"] = f"unhealthy: {str(e)}"

    return health


@router.get("/cf-overview")
async def admin_cf_overview(request: Request):
    """Placeholder Cloudflare stats."""
    _validate_admin_session(request)

    return {
        "source": "placeholder",
        "requests_24h": 0,
        "bandwidth_24h": 0,
        "threats_blocked": 0,
        "cache_hit_ratio": 0,
    }
