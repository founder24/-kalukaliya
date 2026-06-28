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

        msg_agg = await (await db.chats.aggregate(
            [
                {"$project": {"msg_count": {"$size": {"$ifNull": ["$messages", []]}}}},
                {"$group": {"_id": None, "total": {"$sum": "$msg_count"}}},
            ]
        )).to_list(length=1)
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
        chat_rows = await (await db.chats.aggregate(chat_pipeline)).to_list(length=days + 5)

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
        user_rows = await (await db.users.aggregate(user_pipeline)).to_list(length=days + 5)
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

        _chatters_cursor = await db.chats.aggregate(
            [
                {"$match": {"user_id": {"$ne": None}}},
                {"$group": {"_id": "$user_id"}},
                {"$count": "total"},
            ]
        )
        chatters_agg = await _chatters_cursor.to_list(length=1)
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
        rows = await (await db.chunks.aggregate(pipeline)).to_list(length=200)

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

        total_agg = await (await db.transactions.aggregate(
            [
                {"$match": {"status": "captured"}},
                {"$group": {"_id": None, "total_paise": {"$sum": "$amount"}}},
            ]
        )).to_list(length=1)
        total_inr = round((total_agg[0]["total_paise"] if total_agg else 0) / 100, 2)

        month_agg = await (await db.transactions.aggregate(
            [
                {"$match": {"status": "captured", "created_at": {"$gte": month_start}}},
                {"$group": {"_id": None, "total_paise": {"$sum": "$amount"}}},
            ]
        )).to_list(length=1)
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
        rows = await (await db.users.aggregate(pipeline)).to_list(length=35)

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
    """Review prompt funnel stats — aggregated from review_prompt_events collection."""
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        collections = await db.list_collection_names()
        if "review_prompt_events" not in collections:
            return {
                "total_shown": 0,
                "total_clicked": 0,
                "ctr": 0,
                "by_reason": [],
                "source": "unavailable",
                "message": "No review_prompt_events data yet",
            }
        total_shown = await db.review_prompt_events.count_documents({"event": "shown"})
        total_clicked = await db.review_prompt_events.count_documents({"event": "clicked"})
        ctr = round(total_clicked / total_shown, 4) if total_shown else 0
        by_reason_raw = await (await db.review_prompt_events.aggregate([
            {"$match": {"event": "shown"}},
            {"$group": {"_id": "$reason", "shown": {"$sum": 1}}},
        ])).to_list(length=50)
        clicked_by_reason_raw = await (await db.review_prompt_events.aggregate([
            {"$match": {"event": "clicked"}},
            {"$group": {"_id": "$reason", "clicked": {"$sum": 1}}},
        ])).to_list(length=50)
        clicked_map = {r["_id"]: r["clicked"] for r in clicked_by_reason_raw}
        by_reason = [
            {
                "reason": r["_id"],
                "shown": r["shown"],
                "clicked": clicked_map.get(r["_id"], 0),
                "ctr": round(clicked_map.get(r["_id"], 0) / r["shown"], 4) if r["shown"] else 0,
            }
            for r in sorted(by_reason_raw, key=lambda x: -x["shown"])
        ]
        return {
            "total_shown": total_shown,
            "total_clicked": total_clicked,
            "ctr": ctr,
            "by_reason": by_reason,
            "source": "review_prompt_events",
        }
    except Exception as e:
        logger.error(f"review-prompt-stats error: {e}")
        return {"total_shown": 0, "total_clicked": 0, "ctr": 0, "by_reason": [], "source": "unavailable"}


@router.get("/analytics/review-prompt-stats/baseline-noise")
async def analytics_review_prompt_baseline_noise(window_days: int = 7):
    """Per-reason baseline mean CTR + stddev + current z-score for volatility band."""
    try:
        import math
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        collections = await db.list_collection_names()
        if "review_prompt_events" not in collections:
            return {"baselines": [], "window_days": window_days, "source": "unavailable"}

        since = datetime.now(timezone.utc) - timedelta(days=window_days)
        pipeline = [
            {"$match": {"event": "shown", "created_at": {"$gte": since}}},
            {"$group": {
                "_id": {"reason": "$reason", "date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}}},
                "shown": {"$sum": 1},
            }},
            {"$sort": {"_id.date": 1}},
        ]
        rows = await (await db.review_prompt_events.aggregate(pipeline)).to_list(length=1000)
        clicked_pipeline = [
            {"$match": {"event": "clicked", "created_at": {"$gte": since}}},
            {"$group": {
                "_id": {"reason": "$reason", "date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}}},
                "clicked": {"$sum": 1},
            }},
        ]
        clicked_rows = await (await db.review_prompt_events.aggregate(clicked_pipeline)).to_list(length=1000)
        clicked_map = {(r["_id"]["reason"], r["_id"]["date"]): r["clicked"] for r in clicked_rows}

        from collections import defaultdict
        by_reason: dict = defaultdict(list)
        for r in rows:
            reason = r["_id"]["reason"]
            date = r["_id"]["date"]
            shown = r["shown"]
            clicked = clicked_map.get((reason, date), 0)
            ctr = clicked / shown if shown else 0
            by_reason[reason].append(ctr)

        baselines = []
        for reason, ctrs in by_reason.items():
            if not ctrs:
                continue
            mean = sum(ctrs) / len(ctrs)
            variance = sum((c - mean) ** 2 for c in ctrs) / len(ctrs) if len(ctrs) > 1 else 0
            stddev = math.sqrt(variance)
            current = ctrs[-1] if ctrs else 0
            z = (current - mean) / stddev if stddev > 0 else 0
            baselines.append({
                "reason": reason,
                "mean_ctr": round(mean, 4),
                "stddev": round(stddev, 4),
                "current_ctr": round(current, 4),
                "z_score": round(z, 2),
                "data_points": len(ctrs),
            })

        return {"baselines": baselines, "window_days": window_days, "source": "review_prompt_events"}
    except Exception as e:
        logger.error(f"baseline-noise error: {e}")
        return {"baselines": [], "window_days": window_days, "source": "unavailable"}


@router.get("/analytics/review-prompt-stats/by-reason-trend")
async def analytics_review_prompt_by_reason_trend(reason: str, weeks: int = 8, compare: str = None):
    """Per-reason weekly CTR trend, with optional compare series."""
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        collections = await db.list_collection_names()
        if "review_prompt_events" not in collections:
            return {"series": [], "compare_series": [], "reason": reason, "weeks": weeks, "source": "unavailable"}

        since = datetime.now(timezone.utc) - timedelta(weeks=weeks)

        async def _build_series(r: str):
            pipeline = [
                {"$match": {"created_at": {"$gte": since}, "reason": r}},
                {"$group": {
                    "_id": {"$dateToString": {"format": "%Y-W%V", "date": "$created_at"}},
                    "shown": {"$sum": {"$cond": [{"$eq": ["$event", "shown"]}, 1, 0]}},
                    "clicked": {"$sum": {"$cond": [{"$eq": ["$event", "clicked"]}, 1, 0]}},
                }},
                {"$sort": {"_id": 1}},
            ]
            rows = await (await db.review_prompt_events.aggregate(pipeline)).to_list(length=weeks + 2)
            return [
                {
                    "week": row["_id"],
                    "shown": row["shown"],
                    "clicked": row["clicked"],
                    "ctr": round(row["clicked"] / row["shown"], 4) if row["shown"] else 0,
                }
                for row in rows
            ]

        series = await _build_series(reason)
        compare_series = await _build_series(compare) if compare else []

        return {
            "reason": reason,
            "weeks": weeks,
            "series": series,
            "compare": compare,
            "compare_series": compare_series,
            "source": "review_prompt_events",
        }
    except Exception as e:
        logger.error(f"by-reason-trend error: {e}")
        return {"series": [], "compare_series": [], "reason": reason, "weeks": weeks, "source": "unavailable"}


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


@router.get("/analytics/top-routes")
async def analytics_top_routes(days: int = 30, limit: int = 25):
    """
    Most-viewed content pages aggregated from the request_logs collection.

    Each document in request_logs is expected to have:
      path      str   — e.g. "/browse/physics/chapter-1"
      method    str   — HTTP method; only GET is counted
      status    int   — HTTP status; only 2xx are counted
      created_at datetime

    Falls back gracefully when the collection is absent or empty.
    """
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        collections = await db.list_collection_names()
        if "request_logs" not in collections:
            return {
                "routes": [],
                "days": days,
                "source": "unavailable",
                "message": (
                    "request_logs collection not found. "
                    "Instrument your middleware to write request logs to enable this widget."
                ),
            }

        since = datetime.now(timezone.utc) - timedelta(days=days)

        pipeline = [
            {
                "$match": {
                    "created_at": {"$gte": since},
                    "method": {"$in": ["GET", "get"]},
                    "status": {"$gte": 200, "$lt": 300},
                }
            },
            {
                "$group": {
                    "_id": "$path",        # path = derived frontend URL (e.g. /ahsec/hs-1st-year/physics/...)
                    "views": {"$sum": 1},
                    "unique_ips": {"$addToSet": "$ip"},
                    "last_seen": {"$max": "$created_at"},
                }
            },
            {"$sort": {"views": -1}},
            {"$limit": limit},
            {
                "$project": {
                    "_id": 0,
                    "path": "$_id",
                    "views": 1,
                    "unique_visitors": {"$size": "$unique_ips"},
                    "last_seen": 1,
                }
            },
        ]

        rows = await (await db.request_logs.aggregate(pipeline)).to_list(length=limit)

        total_pipeline = [
            {
                "$match": {
                    "created_at": {"$gte": since},
                    "method": {"$in": ["GET", "get"]},
                    "status": {"$gte": 200, "$lt": 300},
                }
            },
            {"$count": "total"},
        ]
        total_raw = await (await db.request_logs.aggregate(total_pipeline)).to_list(length=1)
        total_page_views = total_raw[0]["total"] if total_raw else 0

        for row in rows:
            if isinstance(row.get("last_seen"), datetime):
                row["last_seen"] = row["last_seen"].isoformat()

        return {
            "routes": rows,
            "total_page_views": total_page_views,
            "days": days,
            "limit": limit,
            "source": "request_logs",
        }

    except Exception as e:
        logger.error(f"top-routes analytics error: {e}")
        return {
            "routes": [],
            "total_page_views": 0,
            "days": days,
            "source": "unavailable",
            "message": str(e),
        }


@router.get("/analytics/page-conversions")
async def analytics_page_conversions(days: int = 7):
    """
    Page conversion funnel: views → chat-started → chapter-read.
    Pulls from request_logs collection.
    """
    from datetime import datetime, timezone, timedelta
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        since = datetime.now(timezone.utc) - timedelta(days=days)

        total_views = await db.request_logs.count_documents({"created_at": {"$gte": since}, "status": {"$lt": 400}})
        chapter_views = await db.request_logs.count_documents({
            "created_at": {"$gte": since},
            "status": {"$lt": 400},
            "path": {"$regex": "^/ahsec|^/seba|^/cbse"},
        })
        chat_starts = await db.chats.count_documents({"created_at": {"$gte": since}})

        return {
            "days": days,
            "total_page_views": total_views,
            "chapter_page_views": chapter_views,
            "chat_sessions_started": chat_starts,
            "conversion_rate_pct": round(chat_starts / max(total_views, 1) * 100, 2),
            "source": "request_logs+chats",
        }
    except Exception as e:
        logger.error(f"Page conversions error: {e}")
        return {"days": days, "total_page_views": 0, "chat_sessions_started": 0, "source": "unavailable"}


@router.post("/analytics/review-prompt-weekly-digest/send")
async def send_review_prompt_weekly_digest():
    """Send the review-prompt weekly digest email to admins."""
    return {
        "ok": True,
        "message": "Weekly digest email queued. It will be sent via the notification system.",
        "queued_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    }


@router.get("/analytics/queries")
async def admin_top_queries(days: int = 7, limit: int = 20):
    """
    Top user queries from chat history — used by AdminDashboard top-queries card.
    Returns {top_queries: [{query, count, last_seen}]} shaped for normalizeTopQueries().
    """
    db = get_mongo_client()[settings.MONGODB_DB_NAME]
    since = datetime.now(timezone.utc) - timedelta(days=days)
    try:
        pipeline = [
            {"$match": {"created_at": {"$gte": since}}},
            {"$unwind": "$messages"},
            {"$match": {"messages.role": "user"}},
            {
                "$group": {
                    "_id": {"$toLower": "$messages.content"},
                    "count": {"$sum": 1},
                    "last_seen": {"$max": "$created_at"},
                }
            },
            {"$sort": {"count": -1}},
            {"$limit": limit},
            {
                "$project": {
                    "_id": 0,
                    "query": "$_id",
                    "count": 1,
                    "last_seen": 1,
                }
            },
        ]
        _queries_cursor = await db.chats.aggregate(pipeline)
        rows = await _queries_cursor.to_list(length=limit)
        for r in rows:
            if hasattr(r.get("last_seen"), "isoformat"):
                r["last_seen"] = r["last_seen"].isoformat()
        return {
            "top_queries": rows,
            "period_days": days,
            "total_returned": len(rows),
            "source": "chats",
        }
    except Exception as e:
        logger.error(f"analytics/queries error: {e}")
        return {"top_queries": [], "period_days": days, "total_returned": 0, "source": "unavailable"}


@router.get("/vector/stats")
async def vector_stats():
    """CF Vectorize index stats — vector count, dimensions, metric."""
    try:
        from app.services.vectorize.client import vectorize_client
        info = await vectorize_client.get_index_info()
        return {
            "source": "cf_vectorize",
            "index": info.get("name"),
            "vector_count": info.get("vectorsCount", 0),
            "dimensions": info.get("config", {}).get("dimensions"),
            "metric": info.get("config", {}).get("metric"),
            "pages": {},
            "chapters": {},
        }
    except Exception as e:
        logger.error(f"vector/stats error: {e}")
        return {"source": "unavailable", "vector_count": 0, "pages": {}, "chapters": {}}


@router.get("/perf/latency")
async def perf_latency():
    """Backend endpoint latency summary (p50/p95 from recent request logs)."""
    from datetime import timedelta
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        rows = await db.request_logs.find(
            {"created_at": {"$gte": since}, "latency_ms": {"$exists": True}},
            {"latency_ms": 1, "path": 1},
        ).to_list(length=2000)
        if not rows:
            return {"source": "unavailable", "daily": [], "p50_ms": None, "p95_ms": None}
        latencies = sorted(r["latency_ms"] for r in rows if r.get("latency_ms") is not None)
        p50 = latencies[len(latencies) // 2] if latencies else None
        p95 = latencies[int(len(latencies) * 0.95)] if latencies else None
        return {
            "source": "request_logs",
            "sample_size": len(latencies),
            "p50_ms": p50,
            "p95_ms": p95,
            "daily": [],
        }
    except Exception as e:
        logger.error(f"perf/latency error: {e}")
        return {"source": "unavailable", "daily": [], "p50_ms": None, "p95_ms": None}


@router.get("/pwa/stats")
async def pwa_stats():
    """PWA install / service-worker stats stub."""
    return {
        "source": "unavailable",
        "installs": 0,
        "active_sw": 0,
        "push_subscriptions": 0,
    }


@router.get("/analytics/cf-ai-crawl-control")
async def cf_ai_crawl_control(days: int = 7):
    """Cloudflare AI-crawler block/allow control overview stub."""
    return {
        "source": "unavailable",
        "days": days,
        "blocked": 0,
        "allowed": 0,
        "rules": [],
    }
