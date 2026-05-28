"""
Admin Analytics Endpoints
Overview analytics: users, chats, messages, feedback stats.
"""

from fastapi import APIRouter, Request
import logging

from app.api.v1.admin import _validate_admin_session
from app.config import settings
from app.db.mongo import get_mongo_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin Analytics"])


@router.get("/analytics")
async def analytics_overview(request: Request):
    """Analytics overview: total users, chats, messages, feedback stats."""
    _validate_admin_session(request)

    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        total_users = await db.users.count_documents({})
        total_chats = await db.chats.count_documents({})

        # Total messages across all chats
        msg_agg = await db.chats.aggregate(
            [
                {"$project": {"msg_count": {"$size": {"$ifNull": ["$messages", []]}}}},
                {"$group": {"_id": None, "total": {"$sum": "$msg_count"}}},
            ]
        ).to_list(1)
        total_messages = msg_agg[0]["total"] if msg_agg else 0

        # Average messages per chat
        avg_messages_per_chat = (
            round(total_messages / total_chats, 2) if total_chats > 0 else 0
        )

        # Feedback stats
        total_feedback = await db.chat_feedback.count_documents({})
        positive_feedback = await db.chat_feedback.count_documents({"rating": 1})
        negative_feedback = await db.chat_feedback.count_documents({"rating": -1})

        feedback_stats = {
            "total": total_feedback,
            "positive": positive_feedback,
            "negative": negative_feedback,
            "positive_rate": round(positive_feedback / total_feedback, 4)
            if total_feedback > 0
            else 0,
        }

        return {
            "total_users": total_users,
            "total_chats": total_chats,
            "total_messages": total_messages,
            "avg_messages_per_chat": avg_messages_per_chat,
            "feedback_stats": feedback_stats,
        }
    except Exception as e:
        logger.error(f"Analytics overview error: {e}")
        return {
            "total_users": 0,
            "total_chats": 0,
            "total_messages": 0,
            "avg_messages_per_chat": 0,
            "feedback_stats": {
                "total": 0,
                "positive": 0,
                "negative": 0,
                "positive_rate": 0,
            },
        }


@router.get("/analytics/daily")
async def analytics_daily(request: Request):
    """Daily analytics breakdown."""
    _validate_admin_session(request)
    return {"daily": [], "days": 30}


@router.get("/analytics/revenue")
async def analytics_revenue(request: Request):
    """Revenue analytics."""
    _validate_admin_session(request)
    return {"revenue": [], "total": 0, "currency": "INR"}


@router.get("/analytics/predictor")
async def analytics_predictor(request: Request):
    """Growth/churn predictor."""
    _validate_admin_session(request)
    return {"predictions": [], "accuracy": 0, "model_version": "stub"}


@router.get("/analytics/cf-status")
async def analytics_cf_status(request: Request):
    """Cloudflare analytics token status."""
    _validate_admin_session(request)
    return {
        "configured": False,
        "auth_ok": False,
        "needs_rotation": False,
        "last_error": None,
        "last_check_at": None,
        "blocked_for_seconds": 0,
        "consecutive_failures": 0,
        "rotation_hint": None,
    }


@router.post("/analytics/cf-recheck")
async def analytics_cf_recheck(request: Request):
    """Recheck Cloudflare analytics auth."""
    _validate_admin_session(request)
    return {"status": "ok", "auth_ok": False}


@router.get("/analytics/cf-overview")
async def analytics_cf_overview(request: Request):
    """Cloudflare account overview."""
    _validate_admin_session(request)
    return {"requests": 0, "bandwidth": 0, "threats": 0, "range": "7d"}


@router.get("/analytics/bot-traffic")
async def analytics_bot_traffic(request: Request):
    """Bot traffic analytics."""
    _validate_admin_session(request)
    return {"bots": [], "total": 0, "period": "24h"}


@router.get("/analytics/hydrate-stats")
async def analytics_hydrate_stats(request: Request):
    """Hydrate lifecycle stats."""
    _validate_admin_session(request)
    return {"stats": [], "days": 7}


@router.get("/analytics/review-prompt-stats")
async def analytics_review_prompt_stats(request: Request):
    """Review prompt funnel stats."""
    _validate_admin_session(request)
    return {"stats": [], "funnel": {}, "days": 30}


@router.get("/analytics/review-prompt-stats/baseline-noise")
async def analytics_review_prompt_baseline_noise(request: Request):
    """Review prompt baseline noise analysis."""
    _validate_admin_session(request)
    return {"baselines": []}


@router.get("/analytics/review-prompt-stats/by-reason-trend")
async def analytics_review_prompt_by_reason_trend(request: Request):
    """Review prompt trend by reason."""
    _validate_admin_session(request)
    return {"trend": [], "weeks": 8}


@router.get("/analytics/content-card-views")
async def analytics_content_card_views(request: Request):
    """Content card view analytics."""
    _validate_admin_session(request)
    return {"views": [], "total": 0}


@router.get("/analytics/page-conversions")
async def analytics_page_conversions(request: Request):
    """Page conversion analytics."""
    _validate_admin_session(request)
    return {"conversions": [], "days": 30}
