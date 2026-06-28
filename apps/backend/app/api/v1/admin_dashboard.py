"""
Admin Dashboard Endpoints
Aggregate KPI stats for the admin panel overview card.

Duplicate endpoints removed per audit:
  - GET /admin/health     → use GET /health/deep (canonical)
  - GET /admin/cf-overview → use GET /admin/analytics/cf-overview (canonical)
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
import logging

from app.api.v1.admin import require_admin_session, csrf_guard
from app.config import settings
from app.db.mongo import get_mongo_client

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Admin Dashboard"],
    dependencies=[Depends(require_admin_session), Depends(csrf_guard)],
)


@router.get("/dashboard")
async def admin_dashboard(request: Request):
    """Aggregate KPI stats for the admin dashboard overview."""
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        # ── Core counts ───────────────────────────────────────────────────────
        total_users = await db.users.count_documents({})
        active_today = await db.users.count_documents(
            {"updated_at": {"$gte": today_start}}
        )
        signups_today = await db.users.count_documents(
            {"created_at": {"$gte": today_start}}
        )
        pro_users = await db.users.count_documents({"subscription_tier": "pro"})
        free_users = await db.users.count_documents({"subscription_tier": "free"})

        # ── Message counts ────────────────────────────────────────────────────
        total_messages_agg = await (await db.chats.aggregate(
            [
                {"$project": {"msg_count": {"$size": {"$ifNull": ["$messages", []]}}}},
                {"$group": {"_id": None, "total": {"$sum": "$msg_count"}}},
            ]
        )).to_list(length=1)
        total_messages = total_messages_agg[0]["total"] if total_messages_agg else 0

        messages_today_agg = await (await db.chats.aggregate(
            [
                {"$match": {"updated_at": {"$gte": today_start}}},
                {"$project": {"msg_count": {"$size": {"$ifNull": ["$messages", []]}}}},
                {"$group": {"_id": None, "total": {"$sum": "$msg_count"}}},
            ]
        )).to_list(length=1)
        messages_today = messages_today_agg[0]["total"] if messages_today_agg else 0

        # ── Revenue: sum captured Razorpay transactions ───────────────────────
        try:
            rev_agg = await (await db.transactions.aggregate(
                [
                    {"$match": {"status": "captured"}},
                    {"$group": {"_id": None, "total_paise": {"$sum": "$amount"}}},
                ]
            )).to_list(length=1)
            revenue_total = round((rev_agg[0]["total_paise"] if rev_agg else 0) / 100, 2)

            rev_month_agg = await (await db.transactions.aggregate(
                [
                    {"$match": {"status": "captured", "created_at": {"$gte": month_start}}},
                    {"$group": {"_id": None, "total_paise": {"$sum": "$amount"}}},
                ]
            )).to_list(length=1)
            revenue_month = round(
                (rev_month_agg[0]["total_paise"] if rev_month_agg else 0) / 100, 2
            )
            revenue_source = "transactions_collection"
        except Exception:
            revenue_total = 0
            revenue_month = 0
            revenue_source = "unavailable"

        # ── Vectorize index info ──────────────────────────────────────────────
        vector_stats: dict = {"source": "unavailable"}
        try:
            from app.services.vectorize.client import vectorize_client
            info = await vectorize_client.get_index_info()
            vector_stats = {
                "source": "cf_vectorize",
                "index": info.get("name"),
                "vector_count": info.get("vectorsCount", 0),
                "dimensions": info.get("config", {}).get("dimensions"),
                "metric": info.get("config", {}).get("metric"),
            }
        except Exception:
            pass

        # ── Token spend (last 24h from ai_usage_logs) ─────────────────────────
        token_spend: dict = {"source": "unavailable"}
        try:
            from datetime import timedelta
            since = now - timedelta(hours=24)
            spend_agg = await (await db.ai_usage_logs.aggregate(
                [
                    {"$match": {"created_at": {"$gte": since}}},
                    {
                        "$group": {
                            "_id": "$provider",
                            "calls": {"$sum": 1},
                            "input": {"$sum": "$input_tokens"},
                            "output": {"$sum": "$output_tokens"},
                        }
                    },
                ]
            )).to_list(length=10)
            if spend_agg:
                token_spend = {
                    "source": "ai_usage_logs",
                    "window_hours": 24,
                    "providers": [
                        {"provider": r["_id"], "calls": r["calls"],
                         "input_tokens": r["input"], "output_tokens": r["output"]}
                        for r in spend_agg
                    ],
                }
        except Exception:
            pass

        # ── Feedback stats ────────────────────────────────────────────────────
        total_fb = await db.chat_feedback.count_documents({})
        pos_fb = await db.chat_feedback.count_documents({"rating": 1})
        feedback_stats = {
            "total": total_fb,
            "positive": pos_fb,
            "positive_rate": round(pos_fb / total_fb, 3) if total_fb else 0,
        }

        return {
            "total_users": total_users,
            "active_today": active_today,
            "total_messages": total_messages,
            "messages_today": messages_today,
            "revenue_total": revenue_total,
            "revenue_total_source": revenue_source,
            "revenue_month": revenue_month,
            "pro_users": pro_users,
            "free_users": free_users,
            "system_health": "ok",
            "signups_today": signups_today,
            "feedback": feedback_stats,
            "vector_stats": vector_stats,
            "token_spend": token_spend,
            "top_queries": {"source": "unavailable"},
            "chat_fallbacks": {"source": "unavailable"},
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
            "feedback": {"total": 0, "positive": 0, "positive_rate": 0},
            "vector_stats": {"source": "unavailable"},
            "token_spend": {"source": "unavailable"},
            "top_queries": {"source": "unavailable"},
            "chat_fallbacks": {"source": "unavailable"},
        }


@router.get("/dashboard/metrics")
async def admin_dashboard_metrics():
    """
    Heavier metrics block: revenue, user tier split, SEO page counts, bot render stats.
    Polled every 60 s by AdminDashboard. Uses _meta.heavy_cached_at as a freshness signal.
    """
    db = get_mongo_client()[settings.MONGODB_DB_NAME]
    now = datetime.now(timezone.utc)
    try:
        paid_count = await db.users.count_documents({"subscription_tier": {"$in": ["pro", "premium"]}})
        free_count = await db.users.count_documents({"subscription_tier": {"$nin": ["pro", "premium"]}})

        total_revenue = 0
        mrr = 0
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        async for p in db.payments.find({"status": "captured"}, {"amount": 1, "created_at": 1}):
            amt = p.get("amount", 0) or 0
            total_revenue += amt
            if p.get("created_at") and p["created_at"] >= month_start:
                mrr += amt

        published_chapters = await db.chapters.count_documents({"is_published": True})
        topics = await db.chapters.count_documents({"is_published": True, "topics": {"$exists": True, "$not": {"$size": 0}}})

        request_count = await db.request_logs.count_documents(
            {"created_at": {"$gte": now - __import__("datetime").timedelta(days=1)}}
        ) if "request_logs" in await db.list_collection_names() else 0

        return {
            "response_time_ms": None,
            "revenue": {
                "total_inr": total_revenue,
                "mrr_inr": mrr,
            },
            "users": {
                "paid": paid_count,
                "free": free_count,
            },
            "seo": {
                "published_pages": published_chapters,
                "topics": topics,
            },
            "bot_render": {
                "total_requests": request_count,
                "by_page_type": {},
            },
            "dependencies": {},
            "_meta": {
                "heavy_cached_at": now.timestamp(),
                "source": "mongodb",
            },
        }
    except Exception as e:
        logger.error(f"dashboard/metrics error: {e}")
        return {
            "response_time_ms": None,
            "revenue": {"total_inr": 0, "mrr_inr": 0},
            "users": {"paid": 0, "free": 0},
            "seo": {"published_pages": 0, "topics": 0},
            "bot_render": {"total_requests": 0, "by_page_type": {}},
            "dependencies": {},
            "_meta": {"heavy_cached_at": now.timestamp(), "source": "unavailable"},
        }
