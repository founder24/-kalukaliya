from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Literal
import logging
import time
import json
import asyncio
import httpx

from app.config import settings
from app.models.user import User
from app.api.v1.auth import get_current_user, get_current_user_optional
from app.utils.posthog import get_posthog
from app.services.chat_service import ChatService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Chat"])

chat_service = ChatService()


def _extract_request_context(user, http_request):
    """Extract common request context: client_ip, user_tier, user_id."""
    client_ip = (
        http_request.client.host
        if http_request and hasattr(http_request, "client")
        else None
    )
    user_tier = getattr(user, "subscription_tier", "free") if user else "free"
    user_id = str(user.id) if user else "anonymous"
    return client_ip, user_tier, user_id


class ChatRequest(BaseModel):
    message: str
    lang: Optional[Literal["en", "as"]] = None
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


@router.post("/", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    user: Optional[User] = Depends(get_current_user_optional),
    http_request: Request = None,
):
    """Main chat endpoint with RAG support."""
    start_time = time.time()
    client_ip, user_tier, user_id = _extract_request_context(user, http_request)

    allowed, current_count, limit = await chat_service.check_rate_limit(
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
        result = await asyncio.wait_for(
            chat_service.process_chat(
                message=request.message,
                lang=request.lang,
                session_id=request.session_id,
                user_id=user_id,
                user_tier=user_tier,
                user=user,
                start_time=start_time,
            ),
            timeout=30.0,
        )

        # Track in PostHog
        posthog = get_posthog(http_request)
        if posthog:
            posthog.capture(
                distinct_id=user_id,
                event="chat_completed",
                properties={
                    "lang": result["detected_lang"],
                    "model": result["target_model"],
                    "latency_ms": result["latency_ms"],
                    "user_tier": user_tier,
                },
            )

        return ChatResponse(
            response=result["response_text"],
            model_used=result["target_model"],
            latency_ms=result["latency_ms"],
            sources=[
                {
                    "doc_id": c["id"],
                    "title": c["title"],
                    "score": c["score"],
                    "url": c["url"],
                }
                for c in result["context_chunks"]
            ],
        )

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


@router.post("/stream")
async def chat_stream(
    request: ChatRequest,
    user: Optional[User] = Depends(get_current_user_optional),
    http_request: Request = None,
):
    """Streaming chat endpoint - Server-Sent Events (SSE)."""
    start_time = time.time()
    client_ip, user_tier, user_id = _extract_request_context(user, http_request)

    allowed, current_count, limit = await chat_service.check_rate_limit(
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

    # Prepare stream context with OTel span
    from app.core.telemetry import get_tracer

    tracer = get_tracer()

    with tracer.start_as_current_span("chat.stream.rag_retrieval") as rag_span:
        rag_span.set_attribute("user.tier", user_tier)
        rag_span.set_attribute("user.id", user_id)

        ctx = await chat_service.prepare_stream_context(
            message=request.message,
            lang=request.lang,
            session_id=request.session_id,
            user_tier=user_tier,
        )

        rag_span.set_attribute("chat.lang", ctx["detected_lang"])
        rag_span.set_attribute("chat.model", ctx["target_model"])
        rag_span.set_attribute("rag.chunks_returned", len(ctx["context_chunks"]))
        rag_span.set_attribute(
            "rag.top_score",
            ctx["context_chunks"][0]["score"] if ctx["context_chunks"] else 0.0,
        )

    sanitized_message = ctx["sanitized_message"]
    detected_lang = ctx["detected_lang"]
    target_model = ctx["target_model"]
    context_chunks = ctx["context_chunks"]
    system_prompt = ctx["system_prompt"]

    if not context_chunks:
        logger.warning(
            "rag_empty_context",
            extra={"user_id": user_id, "query": sanitized_message[:50]},
        )

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
            if detected_lang == "as":
                logger.warning(f"Sarvam stream failed ({e}), falling back to Vertex AI")
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
                logger.error(f"Stream failed: {e}")
                yield f"data: {json.dumps({'error': 'Service temporarily unavailable. Please try again.'})}\n\n"
                return

        latency_ms = int((time.time() - start_time) * 1000)
        yield f"data: {json.dumps({'text': '', 'done': True, 'latency_ms': latency_ms, 'model': actual_model, 'lang': detected_lang, 'route_trace': {'decision': 'sarvam' if ('sarvam' in target_model.lower() or 'openhathi' in target_model.lower()) else 'vertex', 'lang': detected_lang, 'fallback': actual_model != target_model, 'model': actual_model}})}\n\n"

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

        asyncio.create_task(
            chat_service.save_chat(
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


@router.get("/history")
async def get_chat_history(
    skip: int = 0,
    limit: int = 20,
    user: User = Depends(get_current_user),
):
    """Get paginated chat history for the current user."""
    from app.models.chat import Chat

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
    """Get paginated messages for a specific chat session."""
    from app.models.chat import Chat

    chat = await Chat.find_one({"session_id": session_id})
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")

    if chat.user_id:
        if not user:
            raise HTTPException(status_code=401, detail="Authentication required")
        if chat.user_id != str(user.id):
            raise HTTPException(status_code=403, detail="Access denied")

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
