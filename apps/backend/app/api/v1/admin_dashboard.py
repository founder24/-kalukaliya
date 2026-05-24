"""
Admin Dashboard Endpoints
Aggregated stats, health checks, and overview metrics.
"""
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
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
        from app.models.chat import Chat

        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        total_users = await User.count()
        active_today = await User.find({"updated_at": {"$gte": today_start}}).count()
        signups_today = await User.find({"created_at": {"$gte": today_start}}).count()
        pro_users = await User.find({"subscription_tier": "pro"}).count()
        free_users = await User.find({"subscription_tier": "free"}).count()

        # Total messages: sum message arrays across all chats
        all_chats = await Chat.find_all().to_list()
        total_messages = sum(len(c.messages) for c in all_chats)

        # Messages today
        today_chats = await Chat.find({"updated_at": {"$gte": today_start}}).to_list()
        messages_today = sum(len(c.messages) for c in today_chats)

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
            "chat_speedups": {"daily": [], "warm_runs": [], "totals": {}, "source": "placeholder"},
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
            "chat_speedups": {"daily": [], "warm_runs": [], "totals": {}, "source": "placeholder"},
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


@router.get("/alerts/unacknowledged-count")
async def alerts_unacknowledged_count(request: Request):
    """Return count of unacknowledged alerts."""
    _validate_admin_session(request)
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        count = await db.alerts.count_documents({"acknowledged": {"$ne": True}})
        return {"count": count}
    except Exception:
        return {"count": 0}


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
