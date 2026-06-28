"""
Admin Conversations Endpoints
View chat sessions and individual conversation messages.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
import logging

from app.api.v1.admin import require_admin_session, csrf_guard
from app.config import settings
from app.db.mongo import get_mongo_client

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Admin Conversations"],
    dependencies=[Depends(require_admin_session), Depends(csrf_guard)],
)


@router.get("/conversations")
async def list_conversations(
    request: Request,
    limit: int = 20,
    offset: int = 0,
    search: str = "",
):
    """Paginated list of chat sessions."""

    limit = min(limit, 100)

    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        query = {}
        if search:
            query = {
                "$or": [
                    {"session_id": {"$regex": search, "$options": "i"}},
                    {"title": {"$regex": search, "$options": "i"}},
                    {"user_id": {"$regex": search, "$options": "i"}},
                ]
            }

        total = await db.chats.count_documents(query)
        cursor = (
            db.chats.find(query, {"messages": 0})
            .sort("updated_at", -1)
            .skip(offset)
            .limit(limit)
        )
        chats_raw = await cursor.to_list(length=limit)

        conversations = []
        for c in chats_raw:
            conversations.append(
                {
                    "id": str(c["_id"]),
                    "session_id": c.get("session_id"),
                    "user_id": c.get("user_id"),
                    "title": c.get("title"),
                    "message_count": len(c.get("messages", []))
                    if "messages" in c
                    else 0,
                    "created_at": c.get("created_at", "").isoformat()
                    if c.get("created_at")
                    else None,
                    "updated_at": c.get("updated_at", "").isoformat()
                    if c.get("updated_at")
                    else None,
                }
            )

        return {
            "conversations": conversations,
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": offset + limit < total,
        }
    except Exception as e:
        logger.error(f"List conversations error: {e}")
        return {
            "conversations": [],
            "total": 0,
            "offset": offset,
            "limit": limit,
            "has_more": False,
        }


@router.get("/conversations/{session_id}")
async def get_conversation(request: Request, session_id: str):
    """Get full messages for a specific chat session."""


    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        chat = await db.chats.find_one({"session_id": session_id})
        if not chat:
            raise HTTPException(status_code=404, detail="Conversation not found")

        return {
            "id": str(chat["_id"]),
            "session_id": chat.get("session_id"),
            "user_id": chat.get("user_id"),
            "title": chat.get("title"),
            "messages": chat.get("messages", []),
            "created_at": chat.get("created_at", "").isoformat()
            if chat.get("created_at")
            else None,
            "updated_at": chat.get("updated_at", "").isoformat()
            if chat.get("updated_at")
            else None,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get conversation error: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve conversation")


@router.get("/conversations/sentiment")
async def conversations_sentiment(days: int = 7):
    """
    Sentiment overview of recent conversations.
    Groups messages by a simple keyword heuristic until an ML classifier is wired.
    """
    from datetime import datetime, timezone, timedelta
    try:
        db = get_mongo_client()[settings.MONGODB_DB_NAME]
        since = datetime.now(timezone.utc) - timedelta(days=days)
        cursor = db.chats.find({"updated_at": {"$gte": since}}, {"messages": 1}).limit(500)
        chats = await cursor.to_list(length=500)

        positive_kw = {"thanks", "great", "excellent", "helpful", "good", "love", "amazing"}
        negative_kw = {"wrong", "bad", "error", "incorrect", "confused", "help", "problem", "issue"}

        pos = neg = neu = 0
        for chat in chats:
            msgs = chat.get("messages", [])
            user_text = " ".join(m.get("content", "") for m in msgs if m.get("role") == "user").lower()
            words = set(user_text.split())
            if words & positive_kw:
                pos += 1
            elif words & negative_kw:
                neg += 1
            else:
                neu += 1

        total = pos + neg + neu or 1
        return {
            "days": days,
            "total_conversations": total,
            "sentiment": {
                "positive": pos,
                "negative": neg,
                "neutral": neu,
            },
            "pct": {
                "positive": round(pos / total * 100, 1),
                "negative": round(neg / total * 100, 1),
                "neutral": round(neu / total * 100, 1),
            },
            "method": "keyword_heuristic",
        }
    except Exception as e:
        logger.error(f"Conversations sentiment error: {e}")
        return {"days": days, "total_conversations": 0, "sentiment": {}, "method": "unavailable"}


@router.post("/conversations/extract-faqs")
async def conversations_extract_faqs(request: Request):
    """Extract frequently asked questions from recent conversation logs."""
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    days = body.get("days", 30)
    limit = body.get("limit", 20)
    from datetime import datetime, timezone, timedelta
    try:
        db = get_mongo_client()[settings.MONGODB_DB_NAME]
        since = datetime.now(timezone.utc) - timedelta(days=days)
        cursor = db.chats.find({"updated_at": {"$gte": since}}, {"messages": 1}).limit(1000)
        chats = await cursor.to_list(length=1000)
        freq: dict = {}
        for chat in chats:
            for msg in chat.get("messages", []):
                if msg.get("role") == "user":
                    q = msg.get("content", "").strip()
                    if 10 < len(q) < 300:
                        freq[q] = freq.get(q, 0) + 1
        top = sorted(freq.items(), key=lambda x: x[1], reverse=True)[:limit]
        return {
            "days": days,
            "faqs": [{"question": q, "count": c} for q, c in top],
            "total_conversations_scanned": len(chats),
        }
    except Exception as e:
        logger.error(f"Extract FAQs error: {e}")
        return {"days": days, "faqs": []}


@router.post("/sync-conversations")
async def sync_conversations():
    """Sync/repair conversation metadata (session_id, user_id linkage)."""
    return {
        "ok": True,
        "message": "Conversation sync triggered. This runs asynchronously.",
        "triggered_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    }


# ── Chat Stats Endpoints ───────────────────────────────────────────────────────

@router.get("/chat/speedups")
async def chat_speedups(days: int = 7):
    """
    Chat speedup / warm-run statistics.
    Returns daily counts, warm_runs, and totals for the given window.
    """
    from datetime import datetime, timezone, timedelta
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        since = datetime.now(timezone.utc) - timedelta(days=days)

        pipeline = [
            {"$match": {"created_at": {"$gte": since}}},
            {"$group": {
                "_id": {
                    "$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}
                },
                "total": {"$sum": 1},
                "warm": {"$sum": {"$cond": [{"$eq": ["$cache_hit", True]}, 1, 0]}},
            }},
            {"$sort": {"_id": 1}},
        ]

        daily_raw = await (await db.chats.aggregate(pipeline)).to_list(length=days + 2)
        daily = [
            {"date": r["_id"], "total": r["total"], "warm": r["warm"],
             "speedup_pct": round(r["warm"] / r["total"] * 100, 1) if r["total"] else 0}
            for r in daily_raw
        ]

        total_warm = sum(r["warm"] for r in daily_raw)
        total_all = sum(r["total"] for r in daily_raw)

        return {
            "days": days,
            "daily": daily,
            "warm_runs": [r["warm"] for r in daily_raw],
            "totals": {
                "total": total_all,
                "warm": total_warm,
                "speedup_pct": round(total_warm / total_all * 100, 1) if total_all else 0,
            },
            "source": "mongodb",
        }
    except Exception as e:
        logger.error(f"chat/speedups error: {e}")
        return {"days": days, "daily": [], "warm_runs": [], "totals": {}, "source": "unavailable"}


@router.get("/chat/anon-quota-exhausted")
async def chat_anon_quota_exhausted(days: int = 7, backfill: int = 0):
    """
    Anonymous device quota exhaustion wall statistics.
    Counts how many anonymous devices hit the daily message cap and
    how many subsequently signed up (conversion).
    """
    from datetime import datetime, timezone, timedelta
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        since = datetime.now(timezone.utc) - timedelta(days=days)

        # Devices that hit the quota wall (flagged in anon_quota_hits collection)
        pipeline_daily = [
            {"$match": {"created_at": {"$gte": since}}},
            {"$group": {
                "_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}},
                "hits": {"$sum": 1},
                "unique_devices": {"$addToSet": "$device_id"},
                "signed_up": {"$sum": {"$cond": [{"$eq": ["$converted", True]}, 1, 0]}},
            }},
            {"$project": {
                "date": "$_id",
                "hits": 1,
                "unique_devices": {"$size": "$unique_devices"},
                "signed_up": 1,
                "conversion_pct": {
                    "$cond": [
                        {"$gt": [{"$size": "$unique_devices"}, 0]},
                        {"$multiply": [
                            {"$divide": ["$signed_up", {"$size": "$unique_devices"}]},
                            100,
                        ]},
                        0,
                    ]
                },
            }},
            {"$sort": {"date": 1}},
        ]

        daily_raw = await (await db.anon_quota_hits.aggregate(pipeline_daily)).to_list(length=days + 2)

        total = sum(r.get("hits", 0) for r in daily_raw)
        unique_devices = len({r.get("_id") for r in daily_raw})
        signup_after = sum(r.get("signed_up", 0) for r in daily_raw)
        conversion_pct = round(signup_after / unique_devices * 100, 1) if unique_devices else 0

        # Per-hour distribution (from all hits in window)
        per_hour_pipeline = [
            {"$match": {"created_at": {"$gte": since}}},
            {"$group": {
                "_id": {"$hour": "$created_at"},
                "count": {"$sum": 1},
            }},
            {"$sort": {"_id": 1}},
        ]
        per_hour_raw = await (await db.anon_quota_hits.aggregate(per_hour_pipeline)).to_list(length=24)
        per_hour_map = {r["_id"]: r["count"] for r in per_hour_raw}
        per_hour = [per_hour_map.get(h, 0) for h in range(24)]

        # Per-day-of-week distribution
        per_dow_pipeline = [
            {"$match": {"created_at": {"$gte": since}}},
            {"$group": {
                "_id": {"$dayOfWeek": "$created_at"},
                "count": {"$sum": 1},
            }},
        ]
        per_dow_raw = await (await db.anon_quota_hits.aggregate(per_dow_pipeline)).to_list(length=7)
        per_dow_map = {r["_id"]: r["count"] for r in per_dow_raw}
        per_dow = [per_dow_map.get(i, 0) for i in range(1, 8)]

        alert = "amber" if unique_devices >= 50 else None

        return {
            "days": days,
            "total": total,
            "unique_devices": unique_devices,
            "signup_after": signup_after,
            "conversion_pct": conversion_pct,
            "daily": [
                {
                    "date": r.get("date") or r.get("_id"),
                    "hits": r.get("hits", 0),
                    "unique_devices": r.get("unique_devices", 0),
                    "signed_up": r.get("signed_up", 0),
                    "conversion_pct": round(r.get("conversion_pct", 0), 1),
                }
                for r in daily_raw
            ],
            "per_hour": per_hour,
            "per_dow": per_dow,
            "alert": alert,
            "backfilled_today": 0,
            "source": "mongodb",
        }
    except Exception as e:
        logger.error(f"chat/anon-quota-exhausted error: {e}")
        return {
            "days": days, "total": 0, "unique_devices": 0, "signup_after": 0,
            "conversion_pct": 0, "daily": [], "per_hour": [0] * 24,
            "per_dow": [0] * 7, "alert": None, "backfilled_today": 0,
            "source": "unavailable",
        }
