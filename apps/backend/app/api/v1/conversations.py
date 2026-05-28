"""
User Conversation CRUD Endpoints
Provides endpoints for authenticated and anonymous users to manage their chat conversations.
"""

from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone
from bson import ObjectId
import logging
import re

from app.models.user import User
from app.models.chat import Chat
from app.api.v1.auth import get_current_user

logger = logging.getLogger(__name__)

_ANON_ID_PATTERN = re.compile(r'^anon_[a-f0-9]{32}$')


def _validate_anon_id(anon_id: str) -> str:
    """Validate the anonymous ID format (must match frontend's getAnonId pattern)."""
    if not anon_id or not _ANON_ID_PATTERN.match(anon_id):
        raise HTTPException(
            status_code=400,
            detail="Invalid anonymous identifier format"
        )
    return anon_id


router = APIRouter(tags=["Conversations"])


class UpdateConversationRequest(BaseModel):
    title: Optional[str] = None


# --- Authenticated User Endpoints ---


@router.get("")
async def list_conversations(
    skip: int = 0,
    limit: int = 20,
    user: User = Depends(get_current_user),
):
    """List conversations for the authenticated user."""
    limit = min(limit, 100)

    chats = (
        await Chat.find({"user_id": str(user.id)})
        .sort("-updated_at")
        .skip(skip)
        .limit(limit)
        .to_list()
    )

    total = await Chat.find({"user_id": str(user.id)}).count()

    return {
        "conversations": [
            {
                "id": str(chat.id),
                "session_id": chat.session_id,
                "title": chat.title,
                "message_count": len(chat.messages),
                "created_at": chat.created_at.isoformat() if chat.created_at else None,
                "updated_at": chat.updated_at.isoformat() if chat.updated_at else None,
            }
            for chat in chats
        ],
        "pagination": {
            "skip": skip,
            "limit": limit,
            "total": total,
            "has_more": skip + limit < total,
        },
    }


@router.get("/anon")
async def list_anon_conversations(
    request: Request,
    skip: int = 0,
    limit: int = 20,
):
    """List conversations for anonymous users (identified by x-anon-id header)."""
    anon_id = _validate_anon_id(request.headers.get("x-anon-id") or "")

    limit = min(limit, 100)

    chats = (
        await Chat.find({"user_id": anon_id})
        .sort("-updated_at")
        .skip(skip)
        .limit(limit)
        .to_list()
    )

    total = await Chat.find({"user_id": anon_id}).count()

    return {
        "conversations": [
            {
                "id": str(chat.id),
                "session_id": chat.session_id,
                "title": chat.title,
                "message_count": len(chat.messages),
                "created_at": chat.created_at.isoformat() if chat.created_at else None,
                "updated_at": chat.updated_at.isoformat() if chat.updated_at else None,
            }
            for chat in chats
        ],
        "pagination": {
            "skip": skip,
            "limit": limit,
            "total": total,
            "has_more": skip + limit < total,
        },
    }


@router.get("/anon/{conversation_id}")
async def get_anon_conversation(
    conversation_id: str,
    request: Request,
):
    """Get a single anonymous conversation by ID."""
    anon_id = _validate_anon_id(request.headers.get("x-anon-id") or "")

    chat = await _find_chat_by_id(conversation_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if chat.user_id != anon_id:
        raise HTTPException(status_code=403, detail="Access denied")

    return _chat_to_response(chat)


@router.delete("/anon/{conversation_id}")
async def delete_anon_conversation(
    conversation_id: str,
    request: Request,
):
    """Delete an anonymous conversation."""
    anon_id = _validate_anon_id(request.headers.get("x-anon-id") or "")

    chat = await _find_chat_by_id(conversation_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if chat.user_id != anon_id:
        raise HTTPException(status_code=403, detail="Access denied")

    await chat.delete()
    return {"message": "Conversation deleted"}


@router.get("/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    user: User = Depends(get_current_user),
):
    """Get a single conversation by ID."""
    chat = await _find_chat_by_id(conversation_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if chat.user_id != str(user.id):
        raise HTTPException(status_code=403, detail="Access denied")

    return _chat_to_response(chat)


@router.delete("/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    user: User = Depends(get_current_user),
):
    """Delete a conversation."""
    chat = await _find_chat_by_id(conversation_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if chat.user_id != str(user.id):
        raise HTTPException(status_code=403, detail="Access denied")

    await chat.delete()
    return {"message": "Conversation deleted"}


@router.patch("/{conversation_id}")
async def update_conversation(
    conversation_id: str,
    body: UpdateConversationRequest,
    user: User = Depends(get_current_user),
):
    """Update conversation metadata (e.g., title)."""
    chat = await _find_chat_by_id(conversation_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Conversation not found")

    if chat.user_id != str(user.id):
        raise HTTPException(status_code=403, detail="Access denied")

    if body.title is not None:
        chat.title = body.title

    chat.updated_at = datetime.now(timezone.utc)
    await chat.save()

    return _chat_to_response(chat)


# --- Helpers ---


async def _find_chat_by_id(conversation_id: str) -> Optional[Chat]:
    """Find a chat by its MongoDB ObjectId or session_id."""
    if ObjectId.is_valid(conversation_id):
        chat = await Chat.get(conversation_id)
        if chat:
            return chat

    chat = await Chat.find_one({"session_id": conversation_id})
    return chat


def _chat_to_response(chat: Chat) -> dict:
    """Convert a Chat document to API response format."""
    return {
        "id": str(chat.id),
        "session_id": chat.session_id,
        "title": chat.title,
        "messages": chat.messages,
        "message_count": len(chat.messages),
        "created_at": chat.created_at.isoformat() if chat.created_at else None,
        "updated_at": chat.updated_at.isoformat() if chat.updated_at else None,
    }
