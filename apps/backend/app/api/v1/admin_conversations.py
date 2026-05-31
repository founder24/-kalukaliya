"""
Admin Conversations Endpoints
View chat sessions and individual conversation messages.
"""

from fastapi import APIRouter, HTTPException, Request
import logging

from app.api.v1.admin import _validate_admin_session
from app.config import settings
from app.db.mongo import get_mongo_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin Conversations"])


@router.get("/conversations")
async def list_conversations(
    request: Request,
    limit: int = 20,
    offset: int = 0,
    search: str = "",
):
    """Paginated list of chat sessions."""
    await _validate_admin_session(request)
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
    await _validate_admin_session(request)

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
