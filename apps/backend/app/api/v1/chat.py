from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator
from typing import Optional, List, Literal
import logging
import time
import json
import asyncio
from datetime import datetime, timedelta

from app.config import settings
from app.models.user import User
from app.services.ai.router import detect_language_and_route
from app.services.search.azure_search import search_service
from app.db.redis import get_redis
from app.api.v1.auth import get_current_user, get_current_user_optional
from app.core.security import sanitize_user_input

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Chat"])


class ChatRequest(BaseModel):
    message: str
    lang: Optional[Literal["en", "as"]] = None  # Explicit language override
    session_id: Optional[str] = None
    context_messages: List[dict] = []

    @field_validator('message')
    @classmethod
    def message_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message must not be empty")
        if len(v) > 2000:
            raise ValueError("message must not exceed 2000 characters")
        return v


class ChatResponse(BaseModel):
    response: str
    model_used: str
    latency_ms: int
    sources: List[dict] = []


async def check_rate_limit(user_id: str, user_tier: str, client_ip: str = None) -> bool:
    """Check if user has exceeded rate limit"""
    redis = get_redis()
    
    limit = settings.RATE_LIMIT_PRO_TIER if user_tier == "pro" else settings.RATE_LIMIT_FREE_TIER
    
    # Use IP-based tracking for anonymous users to prevent quota collision
    if user_id == "anonymous" and client_ip:
        key = f"rate_anon:{client_ip}:{time.strftime('%Y-%m')}"
    else:
        key = f"rate:{user_id}:{time.strftime('%Y-%m')}"
    
    current_count = await redis.incr(key)
    if current_count == 1:
        # Set expiry to end of month
        next_month = datetime.now().replace(day=28) + timedelta(days=4)
        expire_at = next_month.replace(day=1, hour=0, minute=0, second=0)
        ttl = int(expire_at.timestamp() - time.time())
        await redis.expire(key, ttl)
    
    return current_count <= limit


@router.post("/", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    user: Optional[User] = Depends(get_current_user_optional),
    http_request: Request = None
):
    """
    Main chat endpoint with RAG support.
    Supports both authenticated and anonymous users.
    Handles language detection, hybrid search, and LLM routing.
    """
    start_time = time.time()
    
    # Get client IP for anonymous rate limiting
    client_ip = http_request.client.host if http_request and hasattr(http_request, 'client') else None
    
    # User tier and ID — handle anonymous users gracefully
    user_tier = getattr(user, "subscription_tier", "free") if user else "free"
    user_id = str(user.id) if user else "anonymous"
    
    # Check rate limit
    if not await check_rate_limit(user_id, user_tier, client_ip):
        raise HTTPException(
            status_code=429, 
            detail="Rate limit exceeded. Upgrade to Pro for unlimited messages."
        )
    
    try:
        # Sanitize input to prevent prompt injection
        sanitized_message = sanitize_user_input(request.message)

        # 1. Resolve language: explicit param > auto-detection
        if request.lang:
            detected_lang = request.lang
            target_model = settings.SARVAM_MODEL if request.lang == "as" else settings.VERTEX_GEMINI_MODEL
        else:
            detected_lang, target_model = detect_language_and_route(sanitized_message)
        
        # 2. Generate embedding for RAG
        from app.services.ai.embedder import generate_embedding
        embedding = await generate_embedding(sanitized_message)
        
        # 3. Hybrid search with semantic reranking
        context_chunks = await search_service.search_context(
            query=sanitized_message,
            embedding=embedding,
            user_tier=user_tier,
            limit=settings.MAX_CONTEXT_DOCS
        )
        
        # 4. Build prompt with context (numbered [#] citation format)
        lang_instruction = (
            "You are Syrabit, an expert educational assistant for Assamese students.\n"
            "Use the following numbered context to answer. If the answer is not in the context, say so clearly.\n"
            "Cite sources using [#] format (e.g., [1], [2]). Respond in English."
            if detected_lang == "en" else
            "আপুনি Syrabit, অসমীয়া ছাত্ৰ-ছাত্ৰীৰ বাবে এজন বিশেষজ্ঞ শিক্ষা সহায়ক।\n"
            "নিম্নলিখিত নম্বৰযুক্ত প্ৰসংগ ব্যৱহাৰ কৰি উত্তৰ দিয়ক। প্ৰসংগত নাথাকিলে স্পষ্টকৈ কওক।\n"
            "উদ্ধৃতিৰ বাবে [#] বিন্যাস ব্যৱহাৰ কৰক (যেনে [1], [2])। অসমীয়াত উত্তৰ দিয়ক।"
        )
        context_text = "\n".join(
            f"[{i+1}] {chunk['title']}: {chunk['content']}"
            for i, chunk in enumerate(context_chunks)
        )
        system_prompt = f"{lang_instruction}\n\nContext:\n{context_text}"
        
        # 5. Call LLM
        from app.services.ai.router import generate_response
        response_text = await generate_response(
            system_prompt=system_prompt,
            user_message=request.message,
            model=target_model,
            stream=False
        )
        
        # Calculate latency
        latency_ms = int((time.time() - start_time) * 1000)
        
        # 6. Save chat to MongoDB (async background task)
        from app.models.chat import Chat
        chat_doc = Chat(
            user_id=user_id if user else None,
            session_id=request.session_id,
        )
        chat_doc.add_message(
            role="user",
            content=request.message,
        )
        chat_doc.add_message(
            role="assistant",
            content=response_text,
            model_used=target_model,
            latency_ms=latency_ms,
            rag_sources=[{"doc_id": c["id"], "title": c["title"], "score": c["score"]} for c in context_chunks]
        )
        await chat_doc.save()
        
        # 7. Update usage counter
        if user:
            await user.update({
                "$inc": {"monthly_message_count": 1, "total_lifetime_messages": 1},
                "$set": {"updated_at": time.strftime('%Y-%m-%dT%H:%M:%SZ')}
            })
        
        logger.info(f"Chat completed for user {user_id} in {latency_ms}ms")
        
        return ChatResponse(
            response=response_text,
            model_used=target_model,
            latency_ms=latency_ms,
            sources=[{"doc_id": c["id"], "title": c["title"], "score": c["score"], "url": c["url"]} for c in context_chunks]
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Chat error: {str(e)}")
        raise HTTPException(status_code=500, detail="An internal error occurred. Please try again later.")



# ═══════════════════════════════════════════════════════════════
# STREAMING CHAT ENDPOINT
# ═══════════════════════════════════════════════════════════════


def _resolve_lang_and_model(request: ChatRequest) -> tuple[str, str]:
    """Resolve language and target model from request."""
    if request.lang:
        detected_lang = request.lang
        target_model = settings.SARVAM_MODEL if request.lang == "as" else settings.VERTEX_GEMINI_MODEL
    else:
        detected_lang, target_model = detect_language_and_route(request.message)
    return detected_lang, target_model


def _build_system_prompt(detected_lang: str, context_chunks: list[dict]) -> str:
    """Build system prompt with numbered [#] citation format."""
    lang_instruction = (
        "You are Syrabit, an expert educational assistant for Assamese students.\n"
        "Use the following numbered context to answer. If the answer is not in the context, say so clearly.\n"
        "Cite sources using [#] format (e.g., [1], [2]). Respond in English."
        if detected_lang == "en" else
        "আপুনি Syrabit, অসমীয়া ছাত্ৰ-ছাত্ৰীৰ বাবে এজন বিশেষজ্ঞ শিক্ষা সহায়ক।\n"
        "নিম্নলিখিত নম্বৰযুক্ত প্ৰসংগ ব্যৱহাৰ কৰি উত্তৰ দিয়ক। প্ৰসংগত নাথাকিলে স্পষ্টকৈ কওক।\n"
        "উদ্ধৃতিৰ বাবে [#] বিন্যাস ব্যৱহাৰ কৰক (যেনে [1], [2])। অসমীয়াত উত্তৰ দিয়ক।"
    )
    context_text = "\n".join(
        f"[{i+1}] {chunk['title']}: {chunk['content']}"
        for i, chunk in enumerate(context_chunks)
    )
    return f"{lang_instruction}\n\nContext:\n{context_text}"


async def _save_chat_async(
    user_id: str,
    session_id: Optional[str],
    user_message: str,
    assistant_response: str,
    target_model: str,
    latency_ms: int,
    context_chunks: list[dict],
):
    """Fire-and-forget chat persistence to MongoDB."""
    try:
        from app.models.chat import Chat
        chat_doc = Chat(
            user_id=user_id,
            session_id=session_id,
        )
        chat_doc.add_message(role="user", content=user_message)
        chat_doc.add_message(
            role="assistant",
            content=assistant_response,
            model_used=target_model,
            latency_ms=latency_ms,
            rag_sources=[
                {"doc_id": c["id"], "title": c["title"], "score": c["score"]}
                for c in context_chunks
            ],
        )
        await chat_doc.save()
    except Exception as e:
        logger.error(f"Failed to save streamed chat: {e}")


@router.post("/stream")
async def chat_stream(
    request: ChatRequest,
    user: Optional[User] = Depends(get_current_user_optional),
    http_request: Request = None,
):
    """
    Streaming chat endpoint — Server-Sent Events (SSE).

    Supports both authenticated and anonymous users:
    - Authenticated: rate limited by user_id (monthly quota)
    - Anonymous: rate limited by IP (same monthly quota for free tier)

    Sends normalized chunks: data: {"text": "...", "done": false}
    Final event includes: {"text": "", "done": true, "latency_ms": ..., "model": ..., "lang": ...}

    Features:
    - Explicit lang param (en/as) or auto-detection fallback
    - Sarvam → Vertex fallback on failure for Assamese
    - Fire-and-forget MongoDB persistence after stream completes
    """
    start_time = time.time()

    # ── Auth & rate limit ──
    client_ip = http_request.client.host if http_request and hasattr(http_request, "client") else None
    user_tier = getattr(user, "subscription_tier", "free") if user else "free"
    user_id = str(user.id) if user else "anonymous"

    if not await check_rate_limit(user_id, user_tier, client_ip):
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Upgrade to Pro for unlimited messages.")

    # Sanitize input to prevent prompt injection
    sanitized_message = sanitize_user_input(request.message)

    # ── Resolve language & model ──
    detected_lang, target_model = _resolve_lang_and_model(request)

    # ── RAG retrieval (with OTel span) ──
    from app.services.ai.embedder import generate_embedding
    from app.core.telemetry import get_tracer

    tracer = get_tracer()

    with tracer.start_as_current_span("chat.stream.rag_retrieval") as rag_span:
        rag_span.set_attribute("chat.lang", detected_lang)
        rag_span.set_attribute("chat.model", target_model)
        rag_span.set_attribute("user.tier", user_tier)
        rag_span.set_attribute("user.id", user_id)

        embedding = await generate_embedding(sanitized_message)
        context_chunks = await search_service.search_context(
            query=sanitized_message,
            embedding=embedding,
            user_tier=user_tier,
            limit=settings.MAX_CONTEXT_DOCS,
        )
        rag_span.set_attribute("rag.chunks_returned", len(context_chunks))
        rag_span.set_attribute("rag.top_score", context_chunks[0]["score"] if context_chunks else 0.0)

    # ── Build system prompt ──
    system_prompt = _build_system_prompt(detected_lang, context_chunks)

    # ── Stream generator with Sarvam→Vertex fallback ──
    async def event_stream():
        full_response = ""
        actual_model = target_model

        try:
            from app.services.ai.router import stream_response

            async for chunk in stream_response(
                system_prompt=system_prompt,
                user_message=request.message,
                model=target_model,
            ):
                full_response += chunk
                yield f"data: {json.dumps({'text': chunk, 'done': False})}\n\n"

        except Exception as e:
            # FALLBACK: If Assamese (Sarvam) fails, fall back to Vertex
            if detected_lang == "as":
                logger.warning(f"Sarvam stream failed ({e}), falling back to Vertex AI")
                yield f"data: {json.dumps({'fallback': True, 'provider': 'vertex', 'reason': str(e)})}\n\n"

                try:
                    from app.services.ai.vertex_client import vertex_client

                    actual_model = settings.VERTEX_GEMINI_MODEL
                    async for chunk in vertex_client.stream_generate(system_prompt, request.message):
                        full_response += chunk
                        yield f"data: {json.dumps({'text': chunk, 'done': False})}\n\n"
                except Exception as fallback_err:
                    logger.error(f"Vertex fallback also failed: {fallback_err}")
                    yield f"data: {json.dumps({'error': f'Both providers failed: {fallback_err}'})}\n\n"
                    return
            else:
                logger.error(f"Vertex stream failed: {e}")
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
                return

        # ── Final event ──
        latency_ms = int((time.time() - start_time) * 1000)
        yield f"data: {json.dumps({'text': '', 'done': True, 'latency_ms': latency_ms, 'model': actual_model, 'lang': detected_lang})}\n\n"

        # Record final metrics in OTel span
        with tracer.start_as_current_span("chat.stream.complete") as final_span:
            final_span.set_attribute("chat.latency_ms", latency_ms)
            final_span.set_attribute("chat.response_length", len(full_response))
            final_span.set_attribute("chat.lang", detected_lang)
            final_span.set_attribute("chat.model", actual_model)
            final_span.set_attribute("chat.provider", "sarvam" if "sarvam" in actual_model.lower() or "openhathi" in actual_model.lower() else "vertex")

        # ── Persist chat (fire-and-forget) ──
        asyncio.create_task(
            _save_chat_async(
                user_id=user_id,
                session_id=request.session_id,
                user_message=request.message,
                assistant_response=full_response,
                target_model=actual_model,
                latency_ms=latency_ms,
                context_chunks=context_chunks,
            )
        )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "Connection": "keep-alive",
            "X-Content-Type-Options": "nosniff",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/history")
async def get_chat_history(
    skip: int = 0,
    limit: int = 20,
    user: User = Depends(get_current_user),
):
    """Get paginated chat history for the current user"""
    from app.models.chat import Chat

    # Clamp limit to prevent abuse
    limit = min(limit, 100)

    chats = await Chat.find(
        {"user_id": str(user.id)}
    ).sort("-updated_at").skip(skip).limit(limit).to_list()

    total = await Chat.find({"user_id": str(user.id)}).count()

    return {
        "chats": [
            {
                "id": str(chat.id),
                "session_id": chat.session_id,
                "title": chat.title,
                "message_count": len(chat.messages),
                "updated_at": chat.updated_at.isoformat(),
            }
            for chat in chats
        ],
        "pagination": {
            "skip": skip,
            "limit": limit,
            "total": total,
            "has_more": skip + limit < total,
        }
    }


@router.get("/{session_id}/messages")
async def get_chat_messages(
    session_id: str,
    skip: int = 0,
    limit: int = 50,
    user: Optional[User] = Depends(get_current_user_optional),
):
    """Get paginated messages for a specific chat session"""
    from app.models.chat import Chat

    chat = await Chat.find_one({"session_id": session_id})
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    # Verify ownership: authenticated chats require the owner to be logged in
    if chat.user_id:
        if not user:
            raise HTTPException(status_code=401, detail="Authentication required")
        if chat.user_id != str(user.id):
            raise HTTPException(status_code=403, detail="Access denied")

    # Paginate messages
    limit = min(limit, 200)
    messages = chat.messages[skip:skip + limit]

    return {
        "messages": messages,
        "pagination": {
            "skip": skip,
            "limit": limit,
            "total": len(chat.messages),
            "has_more": skip + limit < len(chat.messages),
        }
    }
