"""
Admin Analytics Endpoints
Overview aggregation, time-series, revenue, and placeholder analytics.
"""
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Request, Query
from typing import Optional
import logging

from app.api.v1.admin import _validate_admin_session, _csrf_check

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/analytics")
async def analytics_overview(request: Request):
    """Overview aggregation from MongoDB."""
    _validate_admin_session(request)
    try:
        from app.models.user import User
        from app.models.chat import Chat
        from app.models.feedback import ChatFeedback

        total_users = await User.count()
        total_chats = await Chat.count()
        all_chats = await Chat.find_all().to_list()
        total_messages = sum(len(c.messages) for c in all_chats)
        avg_messages = round(total_messages / total_chats, 1) if total_chats > 0 else 0

        positive_fb = await ChatFeedback.find({"rating": 1}).count()
        negative_fb = await ChatFeedback.find({"rating": -1}).count()

        return {
            "total_users": total_users,
            "total_chats": total_chats,
            "total_messages": total_messages,
            "avg_messages_per_chat": avg_messages,
            "feedback_stats": {
                "positive": positive_fb,
                "negative": negative_fb,
                "total": positive_fb + negative_fb,
            },
        }
    except Exception as e:
        logger.error(f"Analytics overview error: {e}")
        return {
            "total_users": 0,
            "total_chats": 0,
            "total_messages": 0,
            "avg_messages_per_chat": 0,
            "feedback_stats": {"positive": 0, "negative": 0, "total": 0},
        }


@router.get("/analytics/daily")
async def analytics_daily(
    request: Request,
    days: int = Query(default=30, ge=1, le=90),
):
    """Time-series from Chat created_at grouped by day."""
    _validate_admin_session(request)
    try:
        from app.models.chat import Chat

        now = datetime.now(timezone.utc)
        start = now - timedelta(days=days)
        chats = await Chat.find({"created_at": {"$gte": start}}).to_list()

        daily = {}
        for chat in chats:
            day_key = chat.created_at.strftime("%Y-%m-%d") if chat.created_at else None
            if day_key:
                daily[day_key] = daily.get(day_key, 0) + 1

        series = [{"date": k, "chats": v} for k, v in sorted(daily.items())]
        return {"daily": series, "days": days}
    except Exception as e:
        logger.error(f"Analytics daily error: {e}")
        return {"daily": [], "days": days}


@router.get("/analytics/revenue")
async def analytics_revenue(request: Request):
    """Aggregate pro users * price placeholder."""
    _validate_admin_session(request)
    try:
        from app.models.user import User

        pro_users = await User.find({"subscription_tier": "pro"}).count()
        price = 299  # INR placeholder
        return {
            "pro_users": pro_users,
            "price_per_user": price,
            "mrr": pro_users * price,
            "arr": pro_users * price * 12,
        }
    except Exception as e:
        logger.error(f"Analytics revenue error: {e}")
        return {"pro_users": 0, "price_per_user": 299, "mrr": 0, "arr": 0}


@router.get("/analytics/predictor")
async def analytics_predictor(request: Request):
    """Placeholder predictor analytics."""
    _validate_admin_session(request)
    return {
        "predicted_growth_pct": 0,
        "predicted_churn_pct": 0,
        "confidence": 0,
        "source": "placeholder",
    }


@router.get("/analytics/cf-status")
async def analytics_cf_status(request: Request):
    """Placeholder CF analytics token status."""
    _validate_admin_session(request)
    return {"connected": False, "token_valid": False, "source": "placeholder"}


@router.post("/analytics/cf-recheck")
async def analytics_cf_recheck(request: Request):
    """Placeholder CF recheck."""
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"status": "ok", "connected": False, "source": "placeholder"}


@router.get("/analytics/cf-overview")
async def analytics_cf_overview(
    request: Request,
    range: Optional[str] = Query(default="7d"),
):
    """Placeholder CF overview analytics."""
    _validate_admin_session(request)
    return {
        "requests": 0,
        "bandwidth": 0,
        "threats": 0,
        "range": range,
        "source": "placeholder",
    }


@router.get("/analytics/bot-traffic")
async def analytics_bot_traffic(request: Request):
    """Placeholder bot traffic analytics."""
    _validate_admin_session(request)
    return {"bots": [], "total_bot_requests": 0, "source": "placeholder"}


@router.get("/analytics/hydrate-stats")
async def analytics_hydrate_stats(request: Request):
    """Placeholder hydrate stats."""
    _validate_admin_session(request)
    return {"hydrated": 0, "pending": 0, "failed": 0, "source": "placeholder"}


@router.get("/analytics/review-prompt-stats")
async def analytics_review_prompt_stats(request: Request):
    """Placeholder review prompt stats."""
    _validate_admin_session(request)
    return {"total_prompts": 0, "accepted": 0, "dismissed": 0, "source": "placeholder"}


@router.get("/analytics/review-prompt-stats/baseline-noise")
async def analytics_review_prompt_baseline_noise(request: Request):
    """Placeholder baseline noise stats."""
    _validate_admin_session(request)
    return {"baseline": 0, "noise_pct": 0, "source": "placeholder"}


@router.get("/analytics/review-prompt-stats/by-reason-trend")
async def analytics_review_prompt_by_reason_trend(request: Request):
    """Placeholder review prompt by-reason trend."""
    _validate_admin_session(request)
    return {"trends": [], "source": "placeholder"}


@router.get("/analytics/content-card-views")
async def analytics_content_card_views(request: Request):
    """Placeholder content card views."""
    _validate_admin_session(request)
    return {"views": [], "total": 0, "source": "placeholder"}


@router.get("/analytics/page-conversions")
async def analytics_page_conversions(request: Request):
    """Placeholder page conversions."""
    _validate_admin_session(request)
    return {"conversions": [], "total": 0, "rate": 0, "source": "placeholder"}


@router.get("/analytics/funnel")
async def analytics_funnel(request: Request):
    """Placeholder funnel analytics."""
    _validate_admin_session(request)
    return {"steps": [], "source": "placeholder"}


@router.get("/analytics/cf-ai-crawl-control")
async def analytics_cf_ai_crawl_control(request: Request):
    """Placeholder CF AI crawl control."""
    _validate_admin_session(request)
    return {"enabled": False, "rules": [], "source": "placeholder"}


@router.post("/analytics/review-prompt-weekly-digest/send")
async def analytics_review_prompt_weekly_digest_send(request: Request):
    """Placeholder weekly digest send."""
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"status": "ok", "sent_to": 0, "source": "placeholder"}
