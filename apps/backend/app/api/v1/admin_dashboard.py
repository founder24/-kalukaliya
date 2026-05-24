"""
Admin Dashboard Endpoints
Aggregated stats, health checks, and overview metrics.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Request
import logging

from app.api.v1.admin import _validate_admin_session

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/dashboard")
async def admin_dashboard(request: Request):
    """Aggregate stats for admin dashboard overview."""
    _validate_admin_session(request)
    try:
        from app.models.user import User
        from app.db.mongo import get_mongo_client
        from app.config import settings

        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        total_users = await User.count()
        active_today = await User.find({"updated_at": {"$gte": today_start}}).count()
        signups_today = await User.find({"created_at": {"$gte": today_start}}).count()
        pro_users = await User.find({"subscription_tier": "pro"}).count()
        free_users = await User.find({"subscription_tier": "free"}).count()

        # Total messages: use aggregation pipeline to sum message array lengths server-side
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        pipeline_total = [
            {"$project": {"msg_count": {"$size": {"$ifNull": ["$messages", []]}}}},
            {"$group": {"_id": None, "total": {"$sum": "$msg_count"}}},
        ]
        agg_result = await db.chats.aggregate(pipeline_total).to_list(length=1)
        total_messages = agg_result[0]["total"] if agg_result else 0

        # Messages today: aggregate only today's chats
        pipeline_today = [
            {"$match": {"updated_at": {"$gte": today_start}}},
            {"$project": {"msg_count": {"$size": {"$ifNull": ["$messages", []]}}}},
            {"$group": {"_id": None, "total": {"$sum": "$msg_count"}}},
        ]
        agg_today = await db.chats.aggregate(pipeline_today).to_list(length=1)
        messages_today = agg_today[0]["total"] if agg_today else 0

        # System health
        system_health = {"status": "ok", "checks": {}}
        try:
            from app.api.v1.health import mongo_ping, redis_ping

            system_health["checks"]["mongodb"] = await mongo_ping()
            system_health["checks"]["redis"] = await redis_ping()
        except Exception:
            pass

        return {
            "total_users": total_users,
            "active_today": active_today,
            "total_messages": total_messages,
            "messages_today": messages_today,
            "revenue_total": 0,
            "revenue_month": 0,
            "pro_users": pro_users,
            "free_users": free_users,
            "system_health": system_health,
            "signups_today": signups_today,
            "chat_fallbacks": {"daily": [], "source": "placeholder"},
            "latency": {"daily": [], "source": "placeholder"},
            "token_spend": {"daily": [], "totals": {}, "source": "placeholder"},
            "top_queries": {"top_queries": [], "source": "placeholder"},
            "chat_speedups": {
                "daily": [],
                "warm_runs": [],
                "totals": {},
                "source": "placeholder",
            },
            "vector_stats": {"pages": {}, "chapters": {}, "source": "placeholder"},
        }
    except Exception as e:
        logger.error(f"Dashboard aggregation error: {e}")
        return {
            "total_users": 0,
            "active_today": 0,
            "total_messages": 0,
            "messages_today": 0,
            "revenue_total": 0,
            "revenue_month": 0,
            "pro_users": 0,
            "free_users": 0,
            "system_health": {"status": "degraded", "checks": {}},
            "signups_today": 0,
            "chat_fallbacks": {"daily": [], "source": "placeholder"},
            "latency": {"daily": [], "source": "placeholder"},
            "token_spend": {"daily": [], "totals": {}, "source": "placeholder"},
            "top_queries": {"top_queries": [], "source": "placeholder"},
            "chat_speedups": {
                "daily": [],
                "warm_runs": [],
                "totals": {},
                "source": "placeholder",
            },
            "vector_stats": {"pages": {}, "chapters": {}, "source": "placeholder"},
        }


@router.get("/health")
async def admin_health(request: Request):
    """Detailed dependency health check for admin panel."""
    _validate_admin_session(request)
    checks = {}
    try:
        from app.api.v1.health import mongo_ping, redis_ping

        checks["mongodb"] = await mongo_ping()
        checks["redis"] = await redis_ping()
    except Exception as e:
        checks["error"] = str(e)

    all_healthy = all(
        c.get("status") == "healthy" for c in checks.values() if isinstance(c, dict)
    )
    return {
        "status": "healthy" if all_healthy else "degraded",
        "checks": checks,
    }


@router.get("/cf-overview")
async def cf_overview(request: Request):
    """Placeholder Cloudflare overview stats."""
    _validate_admin_session(request)
    return {
        "requests_total": 0,
        "bandwidth_total": 0,
        "threats_total": 0,
        "page_views": 0,
        "unique_visitors": 0,
        "source": "placeholder",
    }
