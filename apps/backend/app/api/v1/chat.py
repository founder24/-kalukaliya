from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Literal
import hashlib
import io
import logging
import re
import time
import json
import asyncio
import httpx
from datetime import datetime, timezone

from fastapi import File, UploadFile, Form
from app.models.user import User
from app.api.v1.auth import get_current_user, get_current_user_optional
from app.core.security import sanitize_user_input
from app.services.chat_service import ChatService
from app.api.deps.rate_limit import check_rate_limit
from app.utils.tracking import track_chat_completed
from app.services.ai.cloudflare_client import cloudflare_client
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Chat"])


def _log_task_exception(task: asyncio.Task) -> None:
    """Log unhandled exceptions from fire-and-forget background tasks."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.error(f"Background task failed: {type(exc).__name__}: {exc}")


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

    @field_validator("session_id")
    @classmethod
    def validate_session_id(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        # Allow UUID format or alphanumeric with hyphens/underscores (1-64 chars)
        if not re.match(r"^[a-zA-Z0-9_-]{1,64}$", v):
            raise ValueError(
                "session_id must be 1-64 alphanumeric characters, hyphens, or underscores"
            )
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
    user_id = (
        str(user.id)
        if user
        else (http_request.headers.get("X-Anon-ID") or "anonymous")
        if http_request
        else "anonymous"
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

            # 2. RAG retrieval + history load + rate limit in parallel
            # Reset monthly message count if we're in a new month (atomic with precondition)
            if user and hasattr(user, "last_reset_date") and user.last_reset_date:
                now = datetime.now(timezone.utc)
                if (
                    user.last_reset_date.month != now.month
                    or user.last_reset_date.year != now.year
                ):
                    # Atomic: only reset if last_reset_date hasn't been updated by another request
                    result = await User.find_one(
                        {"_id": user.id, "last_reset_date": user.last_reset_date}
                    )
                    if result:
                        await result.update(
                            {
                                "$set": {
                                    "monthly_message_count": 0,
                                    "last_reset_date": now,
                                }
                            }
                        )
                        user.monthly_message_count = 0
                        user.last_reset_date = now

            # Using return_exceptions=True so one failure does not cancel others
            skip_rag = ChatService.is_generic_query(sanitized_message)

            async def _maybe_retrieve():
                if skip_rag:
                    logger.info(
                        "generic_query_detected",
                        extra={"user_id": user_id, "query": sanitized_message[:30]},
                    )
                    return []
                # Topic embedding match: only proceed with RAG if query matches a topic
                topic_match = await ChatService.check_topic_match(sanitized_message)
                if topic_match is None:
                    logger.info(
                        "no_topic_match",
                        extra={"user_id": user_id, "query": sanitized_message[:30]},
                    )
                    return []
                logger.info(
                    "topic_matched",
                    extra={
                        "user_id": user_id,
                        "topic": topic_match.get("topic_title"),
                        "score": topic_match.get("score"),
                    },
                )
                return await ChatService.retrieve_context(sanitized_message, user_tier)

            results = await asyncio.gather(
                _maybe_retrieve(),
                ChatService.load_conversation_history(request.session_id),
                check_rate_limit(user_id, user_tier, client_ip, request=http_request),
                return_exceptions=True,
            )

            # Unpack results, treating exceptions as safe defaults
            context_chunks = results[0] if not isinstance(results[0], Exception) else []
            if isinstance(results[0], Exception):
                logger.error(f"RAG retrieval failed: {results[0]}")

            history = results[1] if not isinstance(results[1], Exception) else ""
            if isinstance(results[1], Exception):
                logger.error(f"History load failed: {results[1]}")

            if isinstance(results[2], Exception):
                logger.warning(
                    f"Rate limit check failed: {results[2]} - allowing request"
                )
                rate_result = (True, 0, 100, "monthly")
            else:
                rate_result = results[2]

            # Check rate limit result - always enforced, even for cache hits
            allowed, current_count, limit, limit_type = rate_result
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

            # 2b. Check response cache after rate limit enforcement.
            # NOTE: Cache key is hash(message:lang) and intentionally ignores
            # user/session identity. This is acceptable because cached responses
            # are only stored when context_chunks is empty (no RAG context),
            # meaning the response is generic and not personalized. If user-tier
            # specific behavior diverges in the future, include user_tier in the key.
            # HF-015: Only serve cached responses when no active conversation
            cached = None
            if not request.session_id:
                message_hash = ChatService._make_cache_hash(
                    sanitized_message, detected_lang
                )
                cached = await ChatService.get_cached_response(message_hash)
            else:
                message_hash = ChatService._make_cache_hash(
                    sanitized_message, detected_lang
                )

            # Fast-path for greetings: return instant response without LLM
            if skip_rag:
                greeting = ChatService.get_greeting_response(sanitized_message, detected_lang)
                if greeting:
                    latency_ms = int((time.time() - start_time) * 1000)
                    # Cache it for consistency
                    asyncio.create_task(
                        ChatService.set_cached_response(message_hash, greeting, "cache")
                    )
                    return ChatResponse(
                        response=greeting,
                        model_used="cache",
                        latency_ms=latency_ms,
                        sources=[],
                    )

            if cached:
                latency_ms = int((time.time() - start_time) * 1000)
                logger.info(
                    "chat_cache_hit",
                    extra={"user_id": user_id, "latency_ms": latency_ms},
                )
                return ChatResponse(
                    response=cached["response"],
                    model_used=cached["model"],
                    latency_ms=latency_ms,
                    sources=[],
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

            # HF-018: Context window overflow protection
            from app.core.token_budget import estimate_tokens

            max_context = (
                4096
                if "sarvam" in target_model.lower()
                or "openhathi" in target_model.lower()
                else 32000
            )
            total_tokens = estimate_tokens(system_prompt) + estimate_tokens(
                sanitized_message
            )
            if total_tokens > max_context - 1000:  # Leave room for response
                # Trim history to fit
                if history:
                    system_prompt = ChatService.build_system_prompt(
                        detected_lang, context_chunks
                    )

            # 4. Call LLM (with Sarvam -> Vertex AI fallback)
            response_text, actual_model = await ChatService.call_llm(
                system_prompt=system_prompt,
                sanitized_message=sanitized_message,
                target_model=target_model,
                detected_lang=detected_lang,
                user_id=user_id,
            )

            # Calculate latency
            latency_ms = int((time.time() - start_time) * 1000)

            # 4b. Cache response when no RAG context (static answers)
            if not context_chunks:
                asyncio.create_task(
                    ChatService.set_cached_response(
                        message_hash, response_text, actual_model
                    )
                )

            # 5. Save chat to MongoDB (fire-and-forget)
            task = asyncio.create_task(
                ChatService.save_chat(
                    user_id=user_id if user else None,
                    session_id=request.session_id,
                    user_message=sanitized_message,
                    assistant_response=response_text,
                    target_model=actual_model,
                    latency_ms=latency_ms,
                    context_chunks=context_chunks,
                    detected_lang=detected_lang,
                )
            )
            task.add_done_callback(_log_task_exception)

            # 6. Update usage counter
            # NOTE: Redis is the authoritative source for rate limiting.
            # This MongoDB increment is for analytics/display purposes only.
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
                    else "vertex",
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

        result = await asyncio.wait_for(_process_chat(), timeout=15.0)
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

    Sends normalized chunks: data: {"content": "...", "done": false}
    Final event includes: {"content": "", "done": true, "latency_ms": ..., "model": ..., "lang": ...}

    Features:
    - Explicit lang param (en/as) or auto-detection fallback
    - Sarvam -> Vertex AI fallback on failure for Assamese
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
    user_id = (
        str(user.id)
        if user
        else (http_request.headers.get("X-Anon-ID") or "anonymous")
        if http_request
        else "anonymous"
    )

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

    # Fast-path for greetings in stream mode
    greeting = ChatService.get_greeting_response(sanitized_message, detected_lang)
    if greeting:
        async def greeting_stream():
            latency_ms = int((time.time() - start_time) * 1000)
            # Fire-and-forget save before yields (so client disconnect doesn't skip it)
            task = asyncio.create_task(
                ChatService.save_chat(
                    user_id=user_id,
                    session_id=request.session_id,
                    user_message=sanitized_message,
                    assistant_response=greeting,
                    target_model="cache",
                    latency_ms=latency_ms,
                    context_chunks=[],
                    detected_lang=detected_lang,
                )
            )
            task.add_done_callback(_log_task_exception)
            yield f"data: {json.dumps({'content': greeting, 'done': False})}\n\n"
            yield f"data: {json.dumps({'content': '', 'done': True, 'event': 'syrabit_done', 'latency_ms': latency_ms, 'model': 'cache', 'lang': detected_lang})}\n\n"
        return StreamingResponse(
            greeting_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "Connection": "keep-alive", "X-Content-Type-Options": "nosniff", "X-Accel-Buffering": "no"},
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

            async def _noop_context():
                return []

            if ChatService.is_generic_query(sanitized_message):
                logger.info(
                    "generic_query_skip_rag",
                    extra={"user_id": user_id, "query": sanitized_message[:30]},
                )
                context_chunks, history = await asyncio.gather(
                    _noop_context(),
                    ChatService.load_conversation_history(request.session_id),
                )
            else:
                # Run topic match in parallel with history load
                # so that embedding latency overlaps with history I/O.
                topic_match, history = await asyncio.gather(
                    ChatService.check_topic_match(sanitized_message),
                    ChatService.load_conversation_history(request.session_id),
                )
                if topic_match is None:
                    logger.info(
                        "no_topic_match_stream",
                        extra={"user_id": user_id, "query": sanitized_message[:30]},
                    )
                    context_chunks = []
                else:
                    logger.info(
                        "topic_matched_stream",
                        extra={
                            "user_id": user_id,
                            "topic": topic_match.get("topic_title"),
                            "score": topic_match.get("score"),
                        },
                    )
                    context_chunks = await ChatService.retrieve_context(
                        sanitized_message, user_tier
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

    # HF-018: Context window overflow protection
    from app.core.token_budget import estimate_tokens

    max_context = (
        4096
        if "sarvam" in target_model.lower() or "openhathi" in target_model.lower()
        else 32000
    )
    total_tokens = estimate_tokens(system_prompt) + estimate_tokens(sanitized_message)
    if total_tokens > max_context - 1000:  # Leave room for response
        # Trim history to fit
        if history:
            system_prompt = ChatService.build_system_prompt(
                detected_lang, context_chunks
            )

    # -- Stream generator with Sarvam->Vertex fallback --
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
            # Internal sentinel carries the full response and actual model.
            # Parse JSON structurally to avoid substring collision with user content.
            raw = event
            if raw.startswith("data: "):
                raw = raw[6:].strip()
            try:
                data = json.loads(raw)
                if (
                    isinstance(data, dict)
                    and "__syrabit_stream_complete_7f3a9b2e__" in data
                ):
                    full_response = data["full_response"]
                    actual_model = data["actual_model"]
                    continue
            except (json.JSONDecodeError, ValueError):
                pass
            yield event

        # -- Final event --
        latency_ms = int((time.time() - start_time) * 1000)
        yield f"data: {json.dumps({'content': '', 'done': True, 'event': 'syrabit_done', 'latency_ms': latency_ms, 'model': actual_model, 'lang': detected_lang, 'route_trace': {'decision': 'sarvam' if ('sarvam' in target_model.lower() or 'openhathi' in target_model.lower()) else 'vertex', 'lang': detected_lang, 'fallback': actual_model != target_model, 'model': actual_model}})}\n\n"

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
                else "vertex",
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
        task = asyncio.create_task(
            ChatService.save_chat(
                user_id=user_id,
                session_id=request.session_id,
                user_message=sanitized_message,
                assistant_response=full_response,
                target_model=actual_model,
                latency_ms=latency_ms,
                context_chunks=context_chunks,
                detected_lang=detected_lang,
            )
        )
        task.add_done_callback(_log_task_exception)

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


ANON_HISTORY_LIMIT = 5

_ANON_ID_PATTERN = re.compile(r"^anon_[a-f0-9]{32}$")


@router.get("/history")
async def get_chat_history(
    skip: int = 0,
    limit: int = 20,
    user: Optional[User] = Depends(get_current_user_optional),
    http_request: Request = None,
):
    """Get paginated chat history for the current user or anonymous user."""
    from app.models.chat import Chat

    if user:
        # Authenticated user: full paginated access
        limit = min(limit, 100)

        chats = (
            await Chat.find({"user_id": str(user.id)})
            .sort("-updated_at")
            .skip(skip)
            .limit(limit)
            .to_list()
        )

        total = await Chat.find({"user_id": str(user.id)}).count()
    else:
        # Anonymous user: read anon_id from header, hard-cap at 5
        anon_id = None
        if http_request:
            anon_id = http_request.headers.get("x-anon-id") or http_request.headers.get(
                "X-Anon-ID"
            )

        if not anon_id or not _ANON_ID_PATTERN.match(anon_id):
            return {
                "chats": [],
                "pagination": {"skip": 0, "limit": 0, "total": 0, "has_more": False},
            }

        limit = ANON_HISTORY_LIMIT
        skip = 0

        chats = (
            await Chat.find({"user_id": anon_id})
            .sort("-updated_at")
            .skip(skip)
            .limit(limit)
            .to_list()
        )

        total = await Chat.find({"user_id": anon_id}).count()

    return {
        "chats": [
            {
                "id": str(chat.id),
                "session_id": chat.session_id,
                "title": chat.title
                or (
                    f"Chat: {chat.messages[0].get('content', '')[:40]}..."
                    if chat.messages
                    else "New chat"
                ),
                "message_count": sum(
                    1 for m in chat.messages if m.get("role") in ("user", "assistant")
                ),
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

    # HF-096: Filter to user-facing fields only (exclude internal rag_sources, feedback)
    filtered_messages = [
        {
            "role": m.get("role"),
            "content": m.get("content"),
            "timestamp": m.get("timestamp"),
            "model_used": m.get("model_used"),
        }
        for m in messages
    ]

    return {
        "messages": filtered_messages,
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
    user: Optional[User] = Depends(get_current_user_optional),
    http_request: Request = None,
):
    """Alias for /history - supports frontend legacy route."""
    return await get_chat_history(
        skip=skip, limit=limit, user=user, http_request=http_request
    )


# ═══════════════════════════════════════════════════════════════
# OCR / IMAGE ANALYSIS ENDPOINT
# ═══════════════════════════════════════════════════════════════


class ImageAnalysisResponse(BaseModel):
    text: str
    model: str


@router.post("/image", response_model=ImageAnalysisResponse)
async def analyze_image(
    file: UploadFile = File(...),
    prompt: str = Form(default="Extract all text from this image"),
    user: User = Depends(get_current_user),
    http_request: Request = None,
):
    """Analyze an image using Cloudflare Workers AI vision model (OCR)."""
    user_id = str(user.id)
    user_tier = getattr(user, "subscription_tier", "free")
    client_ip = (
        http_request.client.host if http_request and http_request.client else None
    )

    allowed, current_count, limit, limit_type = await check_rate_limit(
        user_id, user_tier, client_ip, request=http_request
    )
    if not allowed:
        raise HTTPException(status_code=429, detail="Rate limit exceeded.")

    sanitized_prompt = sanitize_user_input(prompt)

    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="File must be an image")

    allowed_types = {"image/jpeg", "image/png", "image/gif", "image/webp"}
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail="Unsupported image type. Allowed: JPEG, PNG, GIF, WebP",
        )

    image_bytes = await file.read()
    if len(image_bytes) > 4 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image must be less than 4MB")

    try:
        result = await cloudflare_client.vision_analyze(image_bytes, sanitized_prompt)
        return ImageAnalysisResponse(text=result, model=settings.CF_AI_VISION_MODEL)
    except RuntimeError as e:
        logger.error(f"Vision analysis failed: {e}")
        raise HTTPException(
            status_code=502, detail="AI vision service temporarily unavailable"
        )


# ═══════════════════════════════════════════════════════════════
# TEXT-TO-SPEECH ENDPOINT
# ═══════════════════════════════════════════════════════════════


class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000)
    lang: str = Field(default="en", pattern="^(en|as)$")


@router.post("/tts")
async def text_to_speech(
    request: TTSRequest,
    user: User = Depends(get_current_user),
    http_request: Request = None,
):
    """Convert text to speech using Cloudflare Workers AI TTS model."""
    user_id = str(user.id)
    user_tier = getattr(user, "subscription_tier", "free")
    client_ip = (
        http_request.client.host if http_request and http_request.client else None
    )

    allowed, current_count, limit, limit_type = await check_rate_limit(
        user_id, user_tier, client_ip, request=http_request
    )
    if not allowed:
        raise HTTPException(status_code=429, detail="Rate limit exceeded.")

    try:
        audio_bytes = await cloudflare_client.text_to_speech(request.text, request.lang)
        return StreamingResponse(
            io.BytesIO(audio_bytes),
            media_type="audio/wav",
            headers={"Content-Disposition": "attachment; filename=speech.wav"},
        )
    except RuntimeError as e:
        logger.error(f"TTS failed: {e}")
        raise HTTPException(
            status_code=502, detail="AI TTS service temporarily unavailable"
        )
