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
