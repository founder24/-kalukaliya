from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Literal
import logging
import time
import json
import asyncio
import httpx
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.models.user import User
from app.services.ai.router import detect_language_and_route
from app.services.search.azure_search import search_service
from app.db.redis import get_redis
from app.api.v1.auth import get_current_user, get_current_user_optional
from app.core.security import sanitize_user_input
from app.core.token_budget import truncate_chunks_to_budget
from app.utils.posthog import get_posthog

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Chat"])


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

class ChatService:
    """
    Encapsulates chat business logic: rate limiting, RAG retrieval,
    LLM routing, streaming, and persistence.
    """

    def __init__(self):
        pass

    async def _load_conversation_history(
        self, session_id: Optional[str], max_turns: int = 5
    ) -> str:
        """Load recent conversation turns for multi-turn context with Redis caching."""
        if not session_id:
            return ""

        # Check Redis cache first
        cache_key = f"conv_history:{session_id}"
        try:
            redis = get_redis()
            cached = await redis.get(cache_key)
            if cached:
                return cached
        except Exception:
            pass

        try:
            from app.models.chat import Chat

            chat = await Chat.find_one({"session_id": session_id})
            if not chat or not chat.messages:
                return ""
            # Get last N turns (user + assistant pairs)
            recent = chat.messages[-(max_turns * 2):]
            history_lines = []
            for msg in recent:
                role = msg.get("role", "user")
                content = msg.get("content", "")[:500]  # Truncate long messages
                history_lines.append(f"{role.capitalize()}: {content}")
            # Cap total history to ~2000 chars
            history = "\n".join(history_lines)
            if len(history) > 2000:
                history = history[-2000:]

            # Cache in Redis with 30-minute TTL
            try:
                redis = get_redis()
                await redis.set(cache_key, history, ex=1800)
            except Exception:
                pass

            return history
        except Exception:
            return ""

    async def check_rate_limit(
        self, user_id: str, user_tier: str, client_ip: str = None
    ) -> tuple[bool, int, int]:
        """Check if user has exceeded rate limit. Returns (allowed, current_count, limit)."""
        limit = (
            settings.RATE_LIMIT_PRO_TIER
            if user_tier == "pro"
            else settings.RATE_LIMIT_FREE_TIER
        )

        try:
            redis = get_redis()
        except RuntimeError:
            # Redis unavailable - allow request through (graceful degradation)
            logger.warning("Redis unavailable - rate limiting disabled")
            return True, 0, limit

        try:
            # Use IP-based tracking for anonymous users to prevent quota collision
            month_key = time.strftime("%Y-%m", time.gmtime())
            if user_id == "anonymous" and client_ip:
                key = f"rate_anon:{client_ip}:{month_key}"
            else:
                key = f"rate:{user_id}:{month_key}"

            current_count = await redis.incr(key)
            if current_count == 1:
                # Set expiry to end of month
                next_month = datetime.now().replace(day=28) + timedelta(days=4)
                expire_at = next_month.replace(day=1, hour=0, minute=0, second=0)
                ttl = int(expire_at.timestamp() - time.time())
                await redis.expire(key, ttl)

            return current_count <= limit, current_count, limit
        except Exception as e:
            logger.warning(f"Rate limit check failed: {e} - allowing request")
            return True, 0, limit

    def _resolve_lang_and_model(
        self, message: str, lang_override: Optional[str] = None
    ) -> tuple[str, str]:
        """Resolve language and target model from message and optional language override."""
        if lang_override:
            detected_lang = lang_override
            target_model = (
                settings.SARVAM_MODEL
                if lang_override == "as"
                else settings.VERTEX_GEMINI_MODEL
            )
        else:
            detected_lang, target_model = detect_language_and_route(message)
        return detected_lang, target_model

    def _build_system_prompt(self, detected_lang: str, context_chunks: list[dict]) -> str:
        """Build system prompt with numbered [#] citation format."""
        lang_instruction = (
            "You are Syrabit, an expert educational assistant for Assamese students.\n"
            "Use the following numbered context to answer. If the answer is not in the context, say so clearly.\n"
            "Cite sources using [#] format (e.g., [1], [2]). Respond in English."
            if detected_lang == "en"
            else "\u0986\u09aa\u09c1\u09a8\u09bf Syrabit, \u0985\u09b8\u09ae\u09c0\u09af\u09bc\u09be \u099b\u09be\u09a4\u09cd\u09f0-\u099b\u09be\u09a4\u09cd\u09f0\u09c0\u09f0 \u09ac\u09be\u09ac\u09c7 \u098f\u099c\u09a8 \u09ac\u09bf\u09b6\u09c7\u09b7\u099c\u09cd\u099e \u09b6\u09bf\u0995\u09cd\u09b7\u09be \u09b8\u09b9\u09be\u09af\u09bc\u0995\u0964\n"
            "\u09a8\u09bf\u09ae\u09cd\u09a8\u09b2\u09bf\u0996\u09bf\u09a4 \u09a8\u09ae\u09cd\u09ac\u09f0\u09af\u09c1\u0995\u09cd\u09a4 \u09aa\u09cd\u09f0\u09b8\u0982\u0997 \u09ac\u09cd\u09af\u09f1\u09b9\u09be\u09f0 \u0995\u09f0\u09bf \u0989\u09a4\u09cd\u09a4\u09f0 \u09a6\u09bf\u09af\u09bc\u0995\u0964 \u09aa\u09cd\u09f0\u09b8\u0982\u0997\u09a4 \u09a8\u09be\u09a5\u09be\u0995\u09bf\u09b2\u09c7 \u09b8\u09cd\u09aa\u09b7\u09cd\u099f\u0995\u09c8 \u0995\u0993\u0995\u0964\n"
            "\u0989\u09a6\u09cd\u09a7\u09c3\u09a4\u09bf\u09f0 \u09ac\u09be\u09ac\u09c7 [#] \u09ac\u09bf\u09a8\u09cd\u09af\u09be\u09b8 \u09ac\u09cd\u09af\u09f1\u09b9\u09be\u09f0 \u0995\u09f0\u0995 (\u09af\u09c7\u09a8\u09c7 [1], [2])\u0964 \u0985\u09b8\u09ae\u09c0\u09af\u09bc\u09be\u09a4 \u0989\u09a4\u09cd\u09a4\u09f0 \u09a6\u09bf\u09af\u09bc\u0995\u0964"
        )

        if not context_chunks:
            return f"{lang_instruction}\n\nNote: Knowledge base results are currently unavailable. Answer based on your general knowledge and clearly state that you cannot verify the answer against the course material."

        context_text = "\n".join(
            f"[{i + 1}] {chunk['title']}: {chunk['content']}"
            for i, chunk in enumerate(context_chunks)
        )
        return f"{lang_instruction}\n\nContext:\n{context_text}"

    async def _save_chat_async(
        self,
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

            # Invalidate conversation history cache so next turn sees fresh data
            if session_id:
                try:
                    redis = get_redis()
                    await redis.delete(f"conv_history:{session_id}")
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"Failed to save streamed chat: {e}")

    async def process_chat(
        self,
        request: ChatRequest,
        user: Optional[User],
        http_request: Request,
    ) -> ChatResponse:
        """
        Main chat processing with RAG support.
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
        allowed, current_count, limit = await self.check_rate_limit(
            user_id, user_tier, client_ip
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

                # 1. Resolve language: explicit param > auto-detection
                detected_lang, target_model = self._resolve_lang_and_model(
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

                # 2. Generate embedding for RAG
                from app.services.ai.embedder import generate_embedding

                query_text = await generate_embedding(sanitized_message)

                # 3. Hybrid search with semantic reranking
                context_chunks = await search_service.search_context(
                    query=sanitized_message,
                    text=query_text,
                    user_tier=user_tier,
                    limit=settings.MAX_CONTEXT_DOCS,
                )

                # Apply token budget to context chunks
                context_chunks = truncate_chunks_to_budget(context_chunks, max_tokens=3000)

                # 4. Build prompt with context
                system_prompt = self._build_system_prompt(detected_lang, context_chunks)

                if not context_chunks:
                    logger.warning(
                        "rag_empty_context",
                        extra={"user_id": user_id, "query": sanitized_message[:50]},
                    )

                # Include multi-turn conversation history
                history = await self._load_conversation_history(request.session_id)
                if history:
                    system_prompt = f"{system_prompt}\n\nPrevious conversation:\n{history}"

                # 5. Call LLM
                from app.services.ai.router import generate_response

                try:
                    response_text = await generate_response(
                        system_prompt=system_prompt,
                        user_message=sanitized_message,
                        model=target_model,
                        stream=False,
                    )
                except (RuntimeError, Exception) as e:
                    if detected_lang == "as":
                        logger.warning(f"Sarvam failed ({e}), falling back to Vertex AI")
                        from app.services.ai.vertex_client import vertex_client

                        target_model = settings.VERTEX_GEMINI_MODEL
                        response_text = await vertex_client.generate(
                            system_prompt=system_prompt,
                            user_message=sanitized_message,
                        )
                    else:
                        raise

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
                    content=sanitized_message,
                )
                chat_doc.add_message(
                    role="assistant",
                    content=response_text,
                    model_used=target_model,
                    latency_ms=latency_ms,
                    rag_sources=[
                        {"doc_id": c["id"], "title": c["title"], "score": c["score"]}
                        for c in context_chunks
                    ],
                )
                try:
                    await chat_doc.save()
                    # Invalidate conversation history cache so next turn sees fresh data
                    if request.session_id:
                        try:
                            redis = get_redis()
                            await redis.delete(f"conv_history:{request.session_id}")
                        except Exception:
                            pass
                except Exception as e:
                    logger.error(f"Failed to save chat to MongoDB: {e}")

                # 7. Update usage counter
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
                        if "sarvam" in target_model.lower()
                        or "openhathi" in target_model.lower()
                        else "vertex",
                        "latency_ms": latency_ms,
                        "response_length": len(response_text),
                    },
                )

                # Track in PostHog
                posthog = get_posthog(http_request)
                if posthog:
                    posthog.capture(
                        distinct_id=user_id,
                        event="chat_completed",
                        properties={
                            "lang": detected_lang,
                            "model": target_model,
                            "latency_ms": latency_ms,
                            "user_tier": user_tier,
                        },
                    )

                return ChatResponse(
                    response=response_text,
                    model_used=target_model,
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

    async def process_stream(
        self,
        request: ChatRequest,
        user: Optional[User],
        http_request: Request,
    ) -> StreamingResponse:
        """
        Streaming chat endpoint - Server-Sent Events (SSE).

        Supports both authenticated and anonymous users.
        Sends normalized chunks: data: {"text": "...", "done": false}
        Final event includes latency, model, and lang info.
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

        allowed, current_count, limit = await self.check_rate_limit(
            user_id, user_tier, client_ip
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

        # -- Resolve language & model --
        detected_lang, target_model = self._resolve_lang_and_model(
            sanitized_message, request.lang
        )

        # -- RAG retrieval (with OTel span) --
        from app.services.ai.embedder import generate_embedding
        from app.core.telemetry import get_tracer

        tracer = get_tracer()

        try:
            with tracer.start_as_current_span("chat.stream.rag_retrieval") as rag_span:
                rag_span.set_attribute("chat.lang", detected_lang)
                rag_span.set_attribute("chat.model", target_model)
                rag_span.set_attribute("user.tier", user_tier)
                rag_span.set_attribute("user.id", user_id)

                embedding = await generate_embedding(sanitized_message)
                context_chunks = await search_service.search_context(
                    query=sanitized_message,
                    text=embedding,
                    user_tier=user_tier,
                    limit=settings.MAX_CONTEXT_DOCS,
                )
                rag_span.set_attribute("rag.chunks_returned", len(context_chunks))
                rag_span.set_attribute(
                    "rag.top_score", context_chunks[0]["score"] if context_chunks else 0.0
                )
        except Exception as e:
            logger.error(f"RAG retrieval failed in stream: {e}")
            context_chunks = []

        # Apply token budget to context chunks
        context_chunks = truncate_chunks_to_budget(context_chunks, max_tokens=3000)

        if not context_chunks:
            logger.warning(
                "rag_empty_context",
                extra={"user_id": user_id, "query": sanitized_message[:50]},
            )

        # -- Build system prompt --
        system_prompt = self._build_system_prompt(detected_lang, context_chunks)

        # Include multi-turn conversation history
        history = await self._load_conversation_history(request.session_id)
        if history:
            system_prompt = f"{system_prompt}\n\nPrevious conversation:\n{history}"

        # -- Stream generator with Sarvam->Vertex fallback --
        async def event_stream():
            full_response = ""
            actual_model = target_model

            try:
                from app.services.ai.router import stream_response

                async for chunk in stream_response(
                    system_prompt=system_prompt,
                    user_message=sanitized_message,
                    model=target_model,
                ):
                    full_response += chunk
                    yield f"data: {json.dumps({'text': chunk, 'done': False})}\n\n"

            except Exception as e:
                # FALLBACK: If Assamese (Sarvam) fails, fall back to Vertex
                if detected_lang == "as":
                    logger.warning(f"Sarvam stream failed ({e}), falling back to Vertex AI")
                    logger.info(
                        "chat_fallback",
                        extra={
                            "user_id": user_id,
                            "error": str(e),
                            "fallback_provider": "vertex",
                        },
                    )
                    yield f"data: {json.dumps({'fallback': True, 'provider': 'vertex', 'reason': str(e)})}\n\n"

                    try:
                        from app.services.ai.vertex_client import vertex_client

                        actual_model = settings.VERTEX_GEMINI_MODEL
                        async for chunk in vertex_client.stream_generate(
                            system_prompt, sanitized_message
                        ):
                            full_response += chunk
                            yield f"data: {json.dumps({'text': chunk, 'done': False})}\n\n"
                    except Exception as fallback_err:
                        logger.error(f"Vertex fallback also failed: {fallback_err}")
                        from app.services.dead_letter import store_dead_letter

                        await store_dead_letter(
                            user_id, request.message, detected_lang, str(fallback_err)
                        )
                        yield f"data: {json.dumps({'error': 'Service temporarily unavailable. Please try again.'})}\n\n"
                        return
                else:
                    logger.error(f"Vertex stream failed: {e}")
                    yield f"data: {json.dumps({'error': 'Service temporarily unavailable. Please try again.'})}\n\n"
                    return

            # -- Final event --
            latency_ms = int((time.time() - start_time) * 1000)
            final_event = {
                "text": "",
                "done": True,
                "latency_ms": latency_ms,
                "model": actual_model,
                "lang": detected_lang,
                "route_trace": {
                    "decision": "sarvam" if ("sarvam" in target_model.lower() or "openhathi" in target_model.lower()) else "vertex",
                    "lang": detected_lang,
                    "fallback": actual_model != target_model,
                    "model": actual_model,
                },
            }
            yield f"data: {json.dumps(final_event)}\n\n"

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
            posthog = get_posthog(http_request)
            if posthog:
                posthog.capture(
                    distinct_id=user_id,
                    event="chat_completed",
                    properties={
                        "lang": detected_lang,
                        "model": actual_model,
                        "latency_ms": latency_ms,
                        "user_tier": user_tier,
                        "streaming": True,
                    },
                )

            # -- Persist chat (fire-and-forget) --
            asyncio.create_task(
                self._save_chat_async(
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


# Module-level singleton
chat_service = ChatService()


# ─── Router Endpoints (thin wrappers around ChatService) ─────────────────────


@router.post("/", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    user: Optional[User] = Depends(get_current_user_optional),
    http_request: Request = None,
):
    """
    Main chat endpoint with RAG support.
    Supports both authenticated and anonymous users.
    """
    return await chat_service.process_chat(request, user, http_request)


@router.post("/stream")
async def chat_stream(
    request: ChatRequest,
    user: Optional[User] = Depends(get_current_user_optional),
    http_request: Request = None,
):
    """
    Streaming chat endpoint - Server-Sent Events (SSE).
    """
    return await chat_service.process_stream(request, user, http_request)


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
    messages = chat.messages[skip: skip + limit]

    return {
        "messages": messages,
        "pagination": {
            "skip": skip,
            "limit": limit,
            "total": len(chat.messages),
            "has_more": skip + limit < len(chat.messages),
        },
    }
