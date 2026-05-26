from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Literal
import hashlib
import logging
import time
import json
import asyncio
import httpx
from datetime import datetime, timezone

from app.models.user import User
from app.api.v1.auth import get_current_user, get_current_user_optional
from app.core.security import sanitize_user_input
from app.services.chat_service import ChatService
from app.api.deps.rate_limit import check_rate_limit
from app.utils.tracking import track_chat_completed

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Chat"])


# ═══════════════════════════════════════════════════════════════
# Request / Response Models
# ═══════════════════════════════════════════════════════════════


class ChatRequest(BaseModel):
    message: str
    lang: Optional[Literal["en", "as"]] = None  # Explicit language override
    session_id: Optional[str] = None
    context_messages: List[dict] = Field(default=[], max_length=10)

    @field_validator("message")
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


# ═══════════════════════════════════════════════════════════════
# CHAT ENDPOINT (non-streaming)
# ═══════════════════════════════════════════════════════════════


@router.post("/", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    user: Optional[User] = Depends(get_current_user_optional),
    http_request: Request = None,
):
    """
    Main chat endpoint with RAG support.
    Supports both authenticated and anonymous users.
    Handles language detection, hybrid search, and LLM routing.
    """
    start_time = time.time()

    # Get client IP for anonymous rate limiting
    client_ip = (
        http_request.client.host
        if http_request and hasattr(http_request, "client")
        else None
    )

    # User tier and ID - handle anonymous users gracefully
    user_tier = getattr(user, "subscription_tier", "free") if user else "free"
    user_id = str(user.id) if user else "anonymous"

    # Check rate limit
    allowed, current_count, limit, limit_type = await check_rate_limit(
        user_id, user_tier, client_ip, request=http_request
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Upgrade to Pro for unlimited messages.",
            headers={
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": "0",
                "Retry-After": "3600",
            },
        )

    try:

        async def _process_chat():
            # Sanitize input to prevent prompt injection
            sanitized_message = sanitize_user_input(request.message)

            # 1. Resolve language and model
            detected_lang, target_model = ChatService.resolve_language_and_model(
                sanitized_message, request.lang
            )

            logger.info(
                "chat_started",
                extra={
                    "user_id": user_id,
                    "lang": detected_lang,
                    "model": target_model,
                },
            )

            # 2. RAG retrieval + history load in parallel (independent I/O)
            context_chunks, history = await asyncio.gather(
                ChatService.retrieve_context(sanitized_message, user_tier),
                ChatService.load_conversation_history(request.session_id),
            )

            if not context_chunks:
                logger.warning(
                    "rag_empty_context",
                    extra={"user_id": user_id, "query": sanitized_message[:50]},
                )

            # 3. Build system prompt
            system_prompt = ChatService.build_system_prompt(
                detected_lang, context_chunks
            )

            # Include multi-turn conversation history
            if history:
                system_prompt = f"{system_prompt}\n\nPrevious conversation:\n{history}"

            # 4. Call LLM (with Sarvam -> Cloudflare AI fallback)
            response_text, actual_model = await ChatService.call_llm(
                system_prompt=system_prompt,
                sanitized_message=sanitized_message,
                target_model=target_model,
                detected_lang=detected_lang,
                user_id=user_id,
            )

            # Calculate latency
            latency_ms = int((time.time() - start_time) * 1000)

            # 5. Save chat to MongoDB (fire-and-forget)
            asyncio.create_task(
                ChatService.save_chat(
                    user_id=user_id if user else None,
                    session_id=request.session_id,
                    user_message=sanitized_message,
                    assistant_response=response_text,
                    target_model=actual_model,
                    latency_ms=latency_ms,
                    context_chunks=context_chunks,
                )
            )

            # 6. Update usage counter
            if user:
                try:
                    await user.update(
                        {
                            "$inc": {
                                "monthly_message_count": 1,
                                "total_lifetime_messages": 1,
                            },
                            "$set": {"updated_at": datetime.now(timezone.utc)},
                        }
                    )
                except Exception as e:
                    logger.error(f"Failed to update user usage counter: {e}")

            logger.info(
                "chat_completed",
                extra={
                    "user_id": user_id,
                    "lang": detected_lang,
                    "provider": "sarvam"
                    if "sarvam" in actual_model.lower()
                    or "openhathi" in actual_model.lower()
                    else "cloudflare",
                    "latency_ms": latency_ms,
                    "response_length": len(response_text),
                },
            )

            # Track in PostHog
            await track_chat_completed(
                request=http_request,
                user_id=user_id,
                lang=detected_lang,
                model=actual_model,
                latency_ms=latency_ms,
                user_tier=user_tier,
            )

            return ChatResponse(
                response=response_text,
                model_used=actual_model,
                latency_ms=latency_ms,
                sources=[
                    {
                        "doc_id": c["id"],
                        "title": c["title"],
                        "score": c["score"],
                        "url": c["url"],
                    }
                    for c in context_chunks
                ],
            )

        result = await asyncio.wait_for(_process_chat(), timeout=30.0)
        return result

    except HTTPException:
        raise
    except RuntimeError as e:
        error_msg = str(e)
        logger.error(
            "chat_upstream_failure", extra={"user_id": user_id, "error": error_msg}
        )
        if "embedding" in error_msg.lower():
            raise HTTPException(status_code=502, detail="Embedding service unavailable")
        elif "search" in error_msg.lower():
            raise HTTPException(
                status_code=503, detail="Knowledge base temporarily unavailable"
            )
        elif "timeout" in error_msg.lower():
            raise HTTPException(status_code=504, detail="Request timed out")
        else:
            raise HTTPException(
                status_code=502, detail="AI service temporarily unavailable"
            )
    except asyncio.TimeoutError:
        logger.error("chat_timeout", extra={"user_id": user_id})
        raise HTTPException(
            status_code=504, detail="Request timed out. Please try a shorter question."
        )
    except httpx.HTTPStatusError as e:
        logger.error(
            "chat_upstream_http_error",
            extra={"user_id": user_id, "status": e.response.status_code},
        )
        raise HTTPException(status_code=502, detail="Upstream service error")
    except ValueError as e:
        logger.warning("chat_value_error", extra={"user_id": user_id, "error": str(e)})
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(
            "chat_unexpected_error", extra={"user_id": user_id, "error": str(e)}
        )
        raise HTTPException(
            status_code=500,
            detail="An internal error occurred. Please try again later.",
        )


# ═══════════════════════════════════════════════════════════════
# STREAMING CHAT ENDPOINT
# ═══════════════════════════════════════════════════════════════


@router.post("/stream")
async def chat_stream(
    request: ChatRequest,
    user: Optional[User] = Depends(get_current_user_optional),
    http_request: Request = None,
):
    """
    Streaming chat endpoint - Server-Sent Events (SSE).

    Supports both authenticated and anonymous users:
    - Authenticated: rate limited by user_id (monthly quota)
    - Anonymous: rate limited by IP (same monthly quota for free tier)

    Sends normalized chunks: data: {"text": "...", "done": false}
    Final event includes: {"text": "", "done": true, "latency_ms": ..., "model": ..., "lang": ...}

    Features:
    - Explicit lang param (en/as) or auto-detection fallback
    - Sarvam -> Cloudflare AI fallback on failure for Assamese
    - Fire-and-forget MongoDB persistence after stream completes
    """
    start_time = time.time()

    # -- Auth & rate limit --
    client_ip = (
        http_request.client.host
        if http_request and hasattr(http_request, "client")
        else None
    )
    user_tier = getattr(user, "subscription_tier", "free") if user else "free"
    user_id = str(user.id) if user else "anonymous"

    allowed, current_count, limit, limit_type = await check_rate_limit(
        user_id, user_tier, client_ip, request=http_request
    )
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Upgrade to Pro for unlimited messages.",
            headers={
                "X-RateLimit-Limit": str(limit),
                "X-RateLimit-Remaining": "0",
                "Retry-After": "3600",
            },
        )

    # Sanitize input to prevent prompt injection
    sanitized_message = sanitize_user_input(request.message)

    if sanitized_message != request.message:
        raw_hash = hashlib.sha256(request.message.encode()).hexdigest()[:16]
        logger.info(
            "input_sanitized",
            extra={"user_id": user_id, "raw_hash": raw_hash},
        )

    # -- Resolve language & model --
    detected_lang, target_model = ChatService.resolve_language_and_model(
        sanitized_message, request.lang
    )

    # -- RAG retrieval (with OTel span) --
    from app.core.telemetry import get_tracer

    tracer = get_tracer()

    try:
        with tracer.start_as_current_span("chat.stream.rag_retrieval") as rag_span:
            rag_span.set_attribute("chat.lang", detected_lang)
            rag_span.set_attribute("chat.model", target_model)
            rag_span.set_attribute("user.tier", user_tier)
            rag_span.set_attribute("user.id", user_id)

            context_chunks, history = await asyncio.gather(
                ChatService.retrieve_context(sanitized_message, user_tier),
                ChatService.load_conversation_history(request.session_id),
            )
            rag_span.set_attribute("rag.chunks_returned", len(context_chunks))
            rag_span.set_attribute(
                "rag.top_score", context_chunks[0]["score"] if context_chunks else 0.0
            )
    except Exception as e:
        logger.error(f"RAG retrieval failed in stream: {e}")
        context_chunks = []
        history = ""

    if not context_chunks:
        logger.warning(
            "rag_empty_context",
            extra={"user_id": user_id, "query": sanitized_message[:50]},
        )

    # -- Build system prompt --
    system_prompt = ChatService.build_system_prompt(detected_lang, context_chunks)

    # Include multi-turn conversation history
    if history:
        system_prompt = f"{system_prompt}\n\nPrevious conversation:\n{history}"

    # -- Stream generator with Sarvam->Cloudflare fallback --
    async def event_stream():
        full_response = ""
        actual_model = target_model

        async for event in ChatService.stream_llm(
            system_prompt=system_prompt,
            sanitized_message=sanitized_message,
            target_model=target_model,
            detected_lang=detected_lang,
            user_id=user_id,
            request_message=sanitized_message,
        ):
            # Internal sentinel carries the full response and actual model
            if event.startswith("{") and '"__internal_complete"' in event:
                data = json.loads(event)
                full_response = data["full_response"]
                actual_model = data["actual_model"]
                continue
            yield event

        # -- Final event --
        latency_ms = int((time.time() - start_time) * 1000)
        yield f"data: {json.dumps({'text': '', 'done': True, 'latency_ms': latency_ms, 'model': actual_model, 'lang': detected_lang, 'route_trace': {'decision': 'sarvam' if ('sarvam' in target_model.lower() or 'openhathi' in target_model.lower()) else 'vertex', 'lang': detected_lang, 'fallback': actual_model != target_model, 'model': actual_model}})}\n\n"

        # Record final metrics in OTel span
        with tracer.start_as_current_span("chat.stream.complete") as final_span:
            final_span.set_attribute("chat.latency_ms", latency_ms)
            final_span.set_attribute("chat.response_length", len(full_response))
            final_span.set_attribute("chat.lang", detected_lang)
            final_span.set_attribute("chat.model", actual_model)
            final_span.set_attribute(
                "chat.provider",
                "sarvam"
                if "sarvam" in actual_model.lower()
                or "openhathi" in actual_model.lower()
                else "cloudflare",
            )

        # Track in PostHog
        await track_chat_completed(
            request=http_request,
            user_id=user_id,
            lang=detected_lang,
            model=actual_model,
            latency_ms=latency_ms,
            user_tier=user_tier,
            streaming=True,
        )

        # -- Persist chat (fire-and-forget) --
        asyncio.create_task(
            ChatService.save_chat(
                user_id=user_id,
                session_id=request.session_id,
                user_message=sanitized_message,
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


# ═══════════════════════════════════════════════════════════════
# HISTORY ENDPOINTS
# ═══════════════════════════════════════════════════════════════


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

    chats = (
        await Chat.find({"user_id": str(user.id)})
        .sort("-updated_at")
        .skip(skip)
        .limit(limit)
        .to_list()
    )

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
        },
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
    messages = chat.messages[skip : skip + limit]

    return {
        "messages": messages,
        "pagination": {
            "skip": skip,
            "limit": limit,
            "total": len(chat.messages),
            "has_more": skip + limit < len(chat.messages),
        },
    }


# ═══════════════════════════════════════════════════════════════
# LEGACY ALIASES
# ═══════════════════════════════════════════════════════════════


@router.get("/conversations", include_in_schema=False)
async def conversations_alias(
    skip: int = 0,
    limit: int = 20,
    user: User = Depends(get_current_user),
):
    """Alias for /history - supports frontend legacy route."""
    return await get_chat_history(skip=skip, limit=limit, user=user)
