"""
Syrabit.ai — AI Chat Routes

Chat endpoint with:
- Anonymous user conversation history support
- Registered user persistent chat storage
- Rate limiting enforcement
- Multi-language AI routing (Vertex/Sarvam)
"""
import os, logging, uuid, json
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
from fastapi import APIRouter, Request, Response, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.python_auth import decode_token, get_user_by_id
from services.python_auth.anon_chat_history import (
    save_conversation,
    get_conversation,
    list_conversations,
    delete_conversation,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    conversation_id: Optional[str] = None
    messages: List[ChatMessage]
    subject_name: Optional[str] = None
    board_id: Optional[str] = None
    class_id: Optional[str] = None
    language: str = "en"  # "en" or "as" (Assamese)


class ChatHistoryResponse(BaseModel):
    id: str
    title: str
    preview: str
    subject_name: Optional[str]
    created_at: str
    updated_at: str
    message_count: int


@router.post("/message")
async def send_message(request: ChatRequest, req: Request):
    """
    Send a chat message and get AI response
    
    Supports both anonymous and registered users.
    Automatically saves conversation to MongoDB.
    """
    # Get user context from token or device cookie
    auth_header = req.headers.get("Authorization", "")
    user_id = None
    is_anonymous = False
    anon_id = None
    
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
        payload = decode_token(token)
        if payload:
            if payload.get("role") == "anonymous":
                is_anonymous = True
                anon_id = payload.get("sub")
            else:
                user_id = payload.get("sub")
    
    # Fallback to device cookie for anonymous users
    if not user_id and not anon_id:
        device_id = req.cookies.get("device_id")
        if device_id:
            is_anonymous = True
            anon_id = device_id
    
    if not user_id and not anon_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    try:
        # Generate or use existing conversation ID
        conv_id = request.conversation_id or str(uuid.uuid4())
        
        # Prepare messages for AI
        messages_list = [
            {"role": msg.role, "content": msg.content} 
            for msg in request.messages
        ]
        
        # TODO: Call AI service (Vertex AI or Sarvam based on language)
        # This is a placeholder - integrate with your existing AI gateway
        ai_response = await call_ai_service(
            messages=messages_list,
            language=request.language,
            user_plan="free"  # Get from user profile
        )
        
        # Save conversation to MongoDB
        if is_anonymous:
            await save_conversation(
                anon_id=anon_id,
                conv_id=conv_id,
                title=messages_list[0]["content"][:50] if messages_list else "New Conversation",
                messages=messages_list + [{"role": "assistant", "content": ai_response}],
                subject_name=request.subject_name,
                board_id=request.board_id,
                class_id=request.class_id,
                ttl_days=7
            )
        else:
            # TODO: Save to registered user conversations collection
            logger.info(f"Saving conversation for user {user_id}")
        
        return JSONResponse(content={
            "success": True,
            "conversation_id": conv_id,
            "response": {
                "role": "assistant",
                "content": ai_response
            },
            "anonymous": is_anonymous
        })
        
    except Exception as e:
        logger.error(f"Chat error: {e}")
        raise HTTPException(status_code=500, detail="Failed to process message")


@router.get("/history")
async def get_chat_history(
    limit: int = 20,
    skip: int = 0,
    req: Request = None
):
    """
    Get user's chat history
    
    Returns different results for anonymous vs registered users
    """
    # Get user context
    auth_header = req.headers.get("Authorization", "")
    user_id = None
    anon_id = None
    
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
        payload = decode_token(token)
        if payload:
            if payload.get("role") == "anonymous":
                anon_id = payload.get("sub")
            else:
                user_id = payload.get("sub")
    
    if not user_id and not anon_id:
        device_id = req.cookies.get("device_id")
        if device_id:
            anon_id = device_id
    
    if not user_id and not anon_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    try:
        if anon_id:
            # Get anonymous user conversations
            conversations = await list_conversations(anon_id, limit=limit, skip=skip)
            return JSONResponse(content={
                "success": True,
                "anonymous": True,
                "conversations": conversations,
                "total": len(conversations)
            })
        else:
            # TODO: Get registered user conversations from MongoDB
            return JSONResponse(content={
                "success": True,
                "anonymous": False,
                "conversations": [],
                "total": 0
            })
            
    except Exception as e:
        logger.error(f"History error: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve history")


@router.get("/conversation/{conv_id}")
async def get_single_conversation(conv_id: str, req: Request):
    """
    Get a specific conversation by ID
    """
    # Get user context
    auth_header = req.headers.get("Authorization", "")
    anon_id = None
    
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
        payload = decode_token(token)
        if payload and payload.get("role") == "anonymous":
            anon_id = payload.get("sub")
    
    if not anon_id:
        device_id = req.cookies.get("device_id")
        if device_id:
            anon_id = device_id
    
    if not anon_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    try:
        conversation = await get_conversation(anon_id, conv_id)
        
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")
        
        return JSONResponse(content={
            "success": True,
            "conversation": conversation
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Get conversation error: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve conversation")


@router.delete("/conversation/{conv_id}")
async def delete_single_conversation(conv_id: str, req: Request):
    """
    Delete a specific conversation
    """
    # Get user context
    auth_header = req.headers.get("Authorization", "")
    anon_id = None
    
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
        payload = decode_token(token)
        if payload and payload.get("role") == "anonymous":
            anon_id = payload.get("sub")
    
    if not anon_id:
        device_id = req.cookies.get("device_id")
        if device_id:
            anon_id = device_id
    
    if not anon_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    
    try:
        success = await delete_conversation(anon_id, conv_id)
        
        if not success:
            raise HTTPException(status_code=404, detail="Conversation not found")
        
        return JSONResponse(content={
            "success": True,
            "message": "Conversation deleted"
        })
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Delete conversation error: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete conversation")


async def call_ai_service(
    messages: List[Dict],
    language: str = "en",
    user_plan: str = "free"
) -> str:
    """
    Call AI service (Vertex AI or Sarvam) based on language preference
    
    This is a placeholder - integrate with your existing AI gateway
    """
    # TODO: Replace with actual AI service integration
    # Example logic:
    # if language == "as":
    #     return await call_sarvam_ai(messages)
    # else:
    #     return await call_vertex_ai(messages)
    
    logger.info(f"Calling AI service for {language} language")
    
    # Placeholder response
    last_message = messages[-1]["content"] if messages else ""
    return f"[AI Response] Received: {last_message[:50]}..."


__all__ = ["router"]
