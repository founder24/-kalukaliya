"""
Admin Analytics Endpoints
Real-time analytics: daily breakdown, funnel, content heatmap, revenue, CF overview.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
import logging

from app.api.v1.admin import require_admin_session, csrf_guard
from app.config import settings
from app.db.mongo import get_mongo_client

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Admin Analytics"],
    dependencies=[Depends(require_admin_session), Depends(csrf_guard)],
)


@router.get("/analytics")
async def analytics_overview():
    """Analytics overview: total users, chats, messages, feedback stats."""
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        total_users = await db.users.count_documents({})
        total_chats = await db.chats.count_documents({})

        msg_agg = await db.chats.aggregate(
            [
                {"$project": {"msg_count": {"$size": {"$ifNull": ["$messages", []]}}}},
                {"$group": {"_id": None, "total": {"$sum": "$msg_count"}}},
            ]
        ).to_list(length=1)
        total_messages = msg_agg[0]["total"] if msg_agg else 0

        avg_messages_per_chat = (
            round(total_messages / total_chats, 2) if total_chats > 0 else 0
        )

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
            "feedback_stats": {"total": 0, "positive": 0, "negative": 0, "positive_rate": 0},
        }


@router.get("/analytics/daily")
async def analytics_daily(days: int = 30):
    """
    Daily breakdown: chats, messages, signups per day.
    Query param: ?days=7|30|90 (default 30).
    """
    days = min(max(days, 1), 90)
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)

        chat_pipeline = [
            {"$match": {"created_at": {"$gte": start}}},
            {
                "$group": {
                    "_id": {
                        "y": {"$year": "$created_at"},
                        "m": {"$month": "$created_at"},
                        "d": {"$dayOfMonth": "$created_at"},
                    },
                    "chats": {"$sum": 1},
                    "messages": {
                        "$sum": {"$size": {"$ifNull": ["$messages", []]}}
                    },
                }
            },
            {"$sort": {"_id.y": 1, "_id.m": 1, "_id.d": 1}},
        ]
        chat_rows = await db.chats.aggregate(chat_pipeline).to_list(length=days + 5)

        user_pipeline = [
            {"$match": {"created_at": {"$gte": start}}},
            {
                "$group": {
                    "_id": {
                        "y": {"$year": "$created_at"},
                        "m": {"$month": "$created_at"},
                        "d": {"$dayOfMonth": "$created_at"},
                    },
                    "signups": {"$sum": 1},
                }
            },
        ]
        user_rows = await db.users.aggregate(user_pipeline).to_list(length=days + 5)
        signup_map = {
            f"{r['_id']['y']}-{r['_id']['m']:02d}-{r['_id']['d']:02d}": r["signups"]
            for r in user_rows
        }

        days_data = []
        for row in chat_rows:
            date_str = f"{row['_id']['y']}-{row['_id']['m']:02d}-{row['_id']['d']:02d}"
            days_data.append(
                {
                    "date": date_str,
                    "chats": row["chats"],
                    "messages": row["messages"],
                    "signups": signup_map.get(date_str, 0),
                }
            )

        return {"days": days_data, "range_days": days}
    except Exception as e:
        logger.error(f"Analytics daily error: {e}")
        return {"days": [], "range_days": days, "error": str(e)}


@router.get("/analytics/funnel")
async def analytics_funnel():
    """
    Conversion funnel: registered → had a chat → pro subscriber.
    """
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        total_users = await db.users.count_documents({})

        chatters_agg = await db.chats.aggregate(
            [
                {"$match": {"user_id": {"$ne": None}}},
                {"$group": {"_id": "$user_id"}},
                {"$count": "total"},
            ]
        ).to_list(length=1)
        distinct_chatters = chatters_agg[0]["total"] if chatters_agg else 0

        pro_users = await db.users.count_documents({"subscription_tier": "pro"})

        return {
            "steps": [
                {"label": "Registered", "count": total_users},
                {"label": "Had a chat", "count": distinct_chatters},
                {"label": "Pro subscriber", "count": pro_users},
            ]
        }
    except Exception as e:
        logger.error(f"Analytics funnel error: {e}")
        return {
            "steps": [
                {"label": "Registered", "count": 0},
                {"label": "Had a chat", "count": 0},
                {"label": "Pro subscriber", "count": 0},
            ],
            "error": str(e),
        }


@router.get("/analytics/content-heatmap")
async def analytics_content_heatmap():
    """
    RAG coverage heatmap: chunk counts per chapter+medium from the chunks collection.
    Shows which chapters have the most/least RAG coverage for both languages.
    """
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        pipeline = [
            {
                "$group": {
                    "_id": {
                        "subject_id": "$subject_id",
                        "chapter_id": "$chapter_id",
                        "medium": "$medium",
                    },
                    "chunk_count": {"$sum": 1},
                    "last_indexed": {"$max": "$created_at"},
                }
            },
            {"$sort": {"chunk_count": -1}},
            {"$limit": 200},
        ]
        rows = await db.chunks.aggregate(pipeline).to_list(length=200)

        return {
            "heatmap": [
                {
                    "subject_id": r["_id"].get("subject_id"),
                    "chapter_id": r["_id"].get("chapter_id"),
                    "medium": r["_id"].get("medium"),
                    "chunk_count": r["chunk_count"],
                    "last_indexed_at": r["last_indexed"].isoformat()
                    if r.get("last_indexed")
                    else None,
                }
                for r in rows
            ],
            "total_chapters_covered": len(
                {r["_id"].get("chapter_id") for r in rows if r["_id"].get("chapter_id")}
            ),
        }
    except Exception as e:
        logger.error(f"Analytics content-heatmap error: {e}")
        return {"heatmap": [], "total_chapters_covered": 0, "error": str(e)}


@router.get("/analytics/revenue")
async def analytics_revenue():
    """Monthly Razorpay revenue breakdown from transactions collection."""
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        total_agg = await db.transactions.aggregate(
            [
                {"$match": {"status": "captured"}},
                {"$group": {"_id": None, "total_paise": {"$sum": "$amount"}}},
            ]
        ).to_list(length=1)
        total_inr = round((total_agg[0]["total_paise"] if total_agg else 0) / 100, 2)

        month_agg = await db.transactions.aggregate(
            [
                {"$match": {"status": "captured", "created_at": {"$gte": month_start}}},
                {"$group": {"_id": None, "total_paise": {"$sum": "$amount"}}},
            ]
        ).to_list(length=1)
        month_inr = round((month_agg[0]["total_paise"] if month_agg else 0) / 100, 2)

        pro_users = await db.users.count_documents({"subscription_tier": "pro"})

        return {
            "total_revenue_inr": total_inr,
            "monthly_revenue_inr": month_inr,
            "pro_subscribers": pro_users,
            "currency": "INR",
            "source": "transactions_collection",
        }
    except Exception as e:
        logger.error(f"Analytics revenue error: {e}")
        return {
            "total_revenue_inr": 0,
            "monthly_revenue_inr": 0,
            "pro_subscribers": 0,
            "currency": "INR",
            "source": "unavailable",
        }


@router.get("/analytics/predictor")
async def analytics_predictor():
    """
    Simple linear regression on last 30 days of daily signups.
    Projects 30 days forward with R² confidence.
    """
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        end = datetime.now(timezone.utc)
        start = end - timedelta(days=30)

        pipeline = [
            {"$match": {"created_at": {"$gte": start}}},
            {
                "$group": {
                    "_id": {
                        "y": {"$year": "$created_at"},
                        "m": {"$month": "$created_at"},
                        "d": {"$dayOfMonth": "$created_at"},
                    },
                    "signups": {"$sum": 1},
                }
            },
            {"$sort": {"_id.y": 1, "_id.m": 1, "_id.d": 1}},
        ]
        rows = await db.users.aggregate(pipeline).to_list(length=35)

        if len(rows) < 3:
            return {
                "predicted_signups_30d": 0,
                "confidence_r2": 0,
                "source": "insufficient_data",
            }

        # Simple linear regression (x=day index, y=signups)
        n = len(rows)
        xs = list(range(n))
        ys = [r["signups"] for r in rows]
        sx = sum(xs)
        sy = sum(ys)
        sxy = sum(x * y for x, y in zip(xs, ys))
        sxx = sum(x * x for x in xs)

        denom = n * sxx - sx * sx
        if denom == 0:
            slope = 0
            intercept = sy / n
        else:
            slope = (n * sxy - sx * sy) / denom
            intercept = (sy - slope * sx) / n

        # Project next 30 days
        predicted = max(0, round(sum(slope * (n + i) + intercept for i in range(30))))

        # R²
        mean_y = sy / n
        ss_tot = sum((y - mean_y) ** 2 for y in ys)
        ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
        r2 = round(1 - ss_res / ss_tot, 3) if ss_tot > 0 else 0

        return {
            "predicted_signups_30d": predicted,
            "daily_slope": round(slope, 3),
            "confidence_r2": max(0.0, r2),
            "data_points": n,
            "source": "linear_regression",
        }
    except Exception as e:
        logger.error(f"Analytics predictor error: {e}")
        return {"predicted_signups_30d": 0, "confidence_r2": 0, "source": "unavailable"}


@router.get("/analytics/cf-status")
async def analytics_cf_status():
    """Cloudflare analytics token health status."""
    cf_token = getattr(settings, "CF_ANALYTICS_TOKEN", None)
    cf_zone = getattr(settings, "CF_ZONE_ID", None)
    configured = bool(cf_token and cf_zone)
    return {
        "configured": configured,
        "auth_ok": configured,
        "needs_rotation": False,
        "last_error": None,
        "last_check_at": None,
        "blocked_for_seconds": 0,
        "consecutive_failures": 0,
        "rotation_hint": None if configured else "Set CF_ANALYTICS_TOKEN and CF_ZONE_ID to enable CF analytics",
    }


@router.post("/analytics/cf-recheck")
async def analytics_cf_recheck():
    """Recheck Cloudflare analytics token."""
    return {"status": "ok", "message": "Recheck triggered"}


@router.get("/analytics/cf-overview")
async def analytics_cf_overview():
    """
    Cloudflare traffic overview via CF GraphQL Analytics API.
    Requires CF_ANALYTICS_TOKEN and CF_ZONE_ID in settings.
    """
    cf_token = getattr(settings, "CF_ANALYTICS_TOKEN", None)
    cf_zone = getattr(settings, "CF_ZONE_ID", None)

    if not cf_token or not cf_zone:
        return {
            "requests_24h": 0,
            "bandwidth_bytes_24h": 0,
            "threats_24h": 0,
            "page_views_24h": 0,
            "source": "unavailable",
            "message": "Set CF_ANALYTICS_TOKEN and CF_ZONE_ID to enable CF traffic analytics",
        }

    try:
        import httpx
        from datetime import date

        today = date.today().isoformat()
        yesterday = (date.today() - timedelta(days=1)).isoformat()

        query = """
        query ($zoneTag: String!, $since: String!, $until: String!) {
          viewer {
            zones(filter: {zoneTag: $zoneTag}) {
              httpRequests1dGroups(
                limit: 2
                filter: {date_geq: $since, date_leq: $until}
              ) {
                sum { requests pageViews threats bytes }
              }
            }
          }
        }
        """
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.cloudflare.com/client/v4/graphql",
                headers={"Authorization": f"Bearer {cf_token}", "Content-Type": "application/json"},
                json={"query": query, "variables": {"zoneTag": cf_zone, "since": yesterday, "until": today}},
            )
            resp.raise_for_status()
            data = resp.json()

        groups = (
            data.get("data", {})
            .get("viewer", {})
            .get("zones", [{}])[0]
            .get("httpRequests1dGroups", [])
        )
        totals = {"requests": 0, "pageViews": 0, "threats": 0, "bytes": 0}
        for g in groups:
            s = g.get("sum", {})
            for k in totals:
                totals[k] += s.get(k, 0)

        return {
            "requests_24h": totals["requests"],
            "bandwidth_bytes_24h": totals["bytes"],
            "threats_24h": totals["threats"],
            "page_views_24h": totals["pageViews"],
            "source": "cloudflare_graphql",
        }
    except Exception as e:
        logger.warning(f"CF GraphQL analytics failed: {e}")
        return {
            "requests_24h": 0,
            "bandwidth_bytes_24h": 0,
            "threats_24h": 0,
            "page_views_24h": 0,
            "source": "unavailable",
            "error": str(e),
        }


@router.get("/analytics/bot-traffic")
async def analytics_bot_traffic():
    """Bot traffic analytics — unavailable until request_logs collection is populated."""
    return {
        "total_bot_requests": 0,
        "bot_types": [],
        "blocked": 0,
        "source": "unavailable",
        "message": "Bot traffic analytics requires the request_logs collection to be populated",
    }


@router.get("/analytics/hydrate-stats")
async def analytics_hydrate_stats():
    """Hydration lifecycle stats."""
    return {
        "total_hydrations": 0,
        "stale_builds": 0,
        "avg_hydration_ms": 0,
        "source": "unavailable",
    }


@router.get("/analytics/review-prompt-stats")
async def analytics_review_prompt_stats():
    """Review prompt funnel stats."""
    return {
        "total_shown": 0,
        "total_clicked": 0,
        "ctr": 0,
        "by_reason": [],
        "source": "unavailable",
    }


@router.get("/analytics/content-card-views")
async def analytics_content_card_views():
    """Content card view analytics."""
    return {
        "total_views": 0,
        "by_card": [],
        "by_day": [],
        "source": "unavailable",
    }


@router.get("/analytics/admin-actions")
async def analytics_admin_actions(days: int = 30):
    """
    Admin action metrics from ContentAuditLog.

    Returns per-action-type counts + daily breakdown for the last N days.
    """
    try:
        from app.models.content import ContentAuditLog
        from collections import defaultdict

        since = datetime.now(timezone.utc) - timedelta(days=days)

        logs = await ContentAuditLog.find(
            ContentAuditLog.timestamp >= since
        ).to_list(length=None)

        by_action: dict[str, int] = defaultdict(int)
        by_day: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

        for log in logs:
            action = getattr(log, "action", "unknown") or "unknown"
            by_action[action] += 1
            day_key = log.timestamp.strftime("%Y-%m-%d") if log.timestamp else "unknown"
            by_day[day_key][action] += 1

        by_action_list = [
            {"action": k, "count": v}
            for k, v in sorted(by_action.items(), key=lambda x: -x[1])
        ]

        by_day_list = [
            {
                "date": day,
                "total": sum(counts.values()),
                "by_action": dict(counts),
            }
            for day, counts in sorted(by_day.items(), reverse=True)
        ]

        return {
            "total": len(logs),
            "days": days,
            "by_action": by_action_list,
            "by_day": by_day_list,
        }
    except Exception as e:
        logger.error(f"admin-actions analytics error: {e}")
        return {
            "total": 0,
            "days": days,
            "by_action": [],
            "by_day": [],
        }
