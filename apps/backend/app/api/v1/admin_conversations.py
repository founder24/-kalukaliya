"""
Admin Conversations Endpoints
View chat sessions, extract FAQs, sentiment analysis, flagging.
"""
from fastapi import APIRouter, Request, HTTPException, Query
from typing import Optional
import logging

from app.api.v1.admin import _validate_admin_session, _csrf_check

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/conversations")
async def list_conversations(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """List chat conversations with user info."""
    _validate_admin_session(request)
    try:
        from app.models.chat import Chat
        from app.models.user import User

        chats = await Chat.find_all().skip(offset).limit(limit).sort("-updated_at").to_list()
        conversations = []
        for chat in chats:
            user_name = None
            user_email = None
            user_plan = None
            is_anonymous = True
            if chat.user_id:
                try:
                    from bson import ObjectId
                    user = await User.find_one({"_id": ObjectId(chat.user_id)})
                    if user:
                        user_name = user.name
                        user_email = user.email
                        user_plan = user.subscription_tier
                        is_anonymous = user.auth_provider == "anonymous"
                except Exception:
                    pass

            conversations.append({
                "id": str(chat.id),
                "title": chat.title,
                "user_name": user_name,
                "user_email": user_email,
                "user_plan": user_plan,
                "is_anonymous": is_anonymous,
                "messages": chat.messages,
                "created_at": chat.created_at.isoformat() if chat.created_at else None,
                "updated_at": chat.updated_at.isoformat() if chat.updated_at else None,
                "subject_name": None,
            })

        return conversations
    except Exception as e:
        logger.error(f"Error listing conversations: {e}")
        return []


@router.get("/conversations/extract-faqs")
async def extract_faqs(
    request: Request,
    limit: int = Query(default=100),
):
    """Placeholder FAQ extraction from conversations."""
    _validate_admin_session(request)
    return {
        "faqs": [],
        "total_questions_analyzed": 0,
        "subjects": [],
        "source": "placeholder",
    }


@router.get("/conversations/sentiment")
async def conversation_sentiment(request: Request):
    """Aggregate sentiment from ChatFeedback."""
    _validate_admin_session(request)
    try:
        from app.models.feedback import ChatFeedback

        positive = await ChatFeedback.find({"rating": 1}).count()
        negative = await ChatFeedback.find({"rating": -1}).count()
        total = positive + negative
        neutral = 0

        positive_pct = round((positive / total * 100), 1) if total > 0 else 0
        negative_pct = round((negative / total * 100), 1) if total > 0 else 0

        return {
            "positive": positive,
            "negative": negative,
            "neutral": neutral,
            "total": total,
            "positive_pct": positive_pct,
            "negative_pct": negative_pct,
        }
    except Exception as e:
        logger.error(f"Error aggregating sentiment: {e}")
        return {
            "positive": 0,
            "negative": 0,
            "neutral": 0,
            "total": 0,
            "positive_pct": 0,
            "negative_pct": 0,
        }


@router.post("/conversations/{conv_id}/flag")
async def flag_conversation(conv_id: str, request: Request):
    """Flag a conversation for review."""
    _validate_admin_session(request)
    await _csrf_check(request)
    try:
        from bson import ObjectId
        from app.db.mongo import get_mongo_client
        from app.config import settings

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        result = await db.chats.update_one(
            {"_id": ObjectId(conv_id)},
            {"$set": {"flagged": True}},
        )
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return {"status": "ok", "flagged": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error flagging conversation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/sync-conversations")
async def sync_conversations(request: Request):
    """Placeholder sync conversations."""
    _validate_admin_session(request)
    await _csrf_check(request)
    return {
        "inserted": 0,
        "updated": 0,
        "skipped": 0,
        "pg_with_messages_after": 0,
        "pg_total_messages_after": 0,
        "source": "placeholder",
    }
