"""Conversation CRUD endpoints for authenticated and anonymous users."""

from fastapi import APIRouter, HTTPException, Depends, Request
from typing import Optional
from datetime import datetime, timezone
import re
import logging

from app.models.user import User
from app.models.chat import Chat
from app.api.v1.auth import get_current_user, get_current_user_optional

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Conversations"])

ANON_ID_PATTERN = re.compile(r"^anon_[a-f0-9]{32}$")


def _validate_anon_id(anon_id: Optional[str]) -> str:
    """Validate anonymous user ID format."""
    if not anon_id or not ANON_ID_PATTERN.match(anon_id):
        raise HTTPException(status_code=400, detail="Invalid anonymous ID format")
    return anon_id


# ── Authenticated user conversations ─────────────────────────────────────────


@router.get("")
async def list_conversations(user: User = Depends(get_current_user)):
    """List all conversations for authenticated user."""
    chats = await Chat.find(
        {"user_id": str(user.id)}
    ).sort("-updated_at").limit(100).to_list()
    return [
        {
            "id": str(chat.id),
            "title": chat.title or "Untitled",
            "created_at": chat.created_at.isoformat() if chat.created_at else None,
            "updated_at": chat.updated_at.isoformat() if chat.updated_at else None,
            "message_count": len(chat.messages),
        }
        for chat in chats
    ]


@router.get("/{conversation_id}")
async def get_conversation(conversation_id: str, user: User = Depends(get_current_user)):
    """Get a specific conversation with messages."""
    chat = await Chat.get(conversation_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if chat.user_id != str(user.id):
        raise HTTPException(status_code=403, detail="Access denied")
    return {
        "id": str(chat.id),
        "title": chat.title or "Untitled",
        "messages": chat.messages,
        "created_at": chat.created_at.isoformat() if chat.created_at else None,
        "updated_at": chat.updated_at.isoformat() if chat.updated_at else None,
    }


@router.patch("/{conversation_id}")
async def update_conversation(conversation_id: str, request: Request, user: User = Depends(get_current_user)):
    """Update conversation metadata (title)."""
    chat = await Chat.get(conversation_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if chat.user_id != str(user.id):
        raise HTTPException(status_code=403, detail="Access denied")

    body = await request.json()
    update_data = {}
    if "title" in body:
        update_data["title"] = body["title"]
    if update_data:
        update_data["updated_at"] = datetime.now(timezone.utc)
        await chat.update({"$set": update_data})
    return {"message": "Conversation updated"}


@router.delete("/{conversation_id}")
async def delete_conversation(conversation_id: str, user: User = Depends(get_current_user)):
    """Delete a conversation."""
    chat = await Chat.get(conversation_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if chat.user_id != str(user.id):
        raise HTTPException(status_code=403, detail="Access denied")
    await chat.delete()
    return {"message": "Conversation deleted"}


# ── Anonymous conversations ──────────────────────────────────────────────────


@router.get("/anon")
async def list_anon_conversations(request: Request):
    """List conversations for anonymous user (by x-anon-id header)."""
    anon_id = _validate_anon_id(request.headers.get("x-anon-id"))
    chats = await Chat.find(
        {"user_id": anon_id}
    ).sort("-updated_at").limit(50).to_list()
    return [
        {
            "id": str(chat.id),
            "title": chat.title or "Untitled",
            "created_at": chat.created_at.isoformat() if chat.created_at else None,
            "updated_at": chat.updated_at.isoformat() if chat.updated_at else None,
            "message_count": len(chat.messages),
        }
        for chat in chats
    ]


@router.get("/anon/{conversation_id}")
async def get_anon_conversation(conversation_id: str, request: Request):
    """Get a specific anonymous conversation."""
    anon_id = _validate_anon_id(request.headers.get("x-anon-id"))
    chat = await Chat.get(conversation_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if chat.user_id != anon_id:
        raise HTTPException(status_code=403, detail="Access denied")
    return {
        "id": str(chat.id),
        "title": chat.title or "Untitled",
        "messages": chat.messages,
        "created_at": chat.created_at.isoformat() if chat.created_at else None,
        "updated_at": chat.updated_at.isoformat() if chat.updated_at else None,
    }


@router.delete("/anon/{conversation_id}")
async def delete_anon_conversation(conversation_id: str, request: Request):
    """Delete an anonymous conversation."""
    anon_id = _validate_anon_id(request.headers.get("x-anon-id"))
    chat = await Chat.get(conversation_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if chat.user_id != anon_id:
        raise HTTPException(status_code=403, detail="Access denied")
    await chat.delete()
    return {"message": "Conversation deleted"}
