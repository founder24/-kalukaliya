"""
ChatService: Encapsulates RAG chat business logic.

Responsibilities:
- Language resolution & model routing
- RAG retrieval (embedding + hybrid search)
- Prompt building with citation format
- LLM calling with Sarvam-to-Cloudflare fallback
- Chat persistence (fire-and-forget) with dead letter on double failure
- Conversation history loading with Redis caching
"""

import json
import logging
from typing import AsyncGenerator, Optional

from app.config import settings
from app.core.token_budget import truncate_chunks_to_budget
from app.db.redis import get_redis
from app.services.ai.router import detect_language_and_route
from app.services.search.azure_search import search_service

logger = logging.getLogger(__name__)

# Redis cache TTL for conversation history (30 minutes)
_HISTORY_CACHE_TTL = 30 * 60


class ChatService:
    """Encapsulates all chat business logic: RAG, LLM routing, persistence."""

    # ------------------------------------------------------------------
    # Language & model resolution
    # ------------------------------------------------------------------

    @staticmethod
    def resolve_language_and_model(
        message: str, lang_override: Optional[str] = None
    ) -> tuple[str, str]:
        """Resolve language and target model from message and optional override."""
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

    # ------------------------------------------------------------------
    # RAG retrieval
    # ------------------------------------------------------------------

    @staticmethod
    async def retrieve_context(sanitized_message: str, user_tier: str) -> list[dict]:
        """Generate embedding and perform hybrid search for RAG context."""
        from app.services.ai.embedder import generate_embedding

        embedding = await generate_embedding(sanitized_message)
        context_chunks = await search_service.search_context(
            query=sanitized_message,
            text=embedding,
            user_tier=user_tier,
            limit=settings.MAX_CONTEXT_DOCS,
        )
        return truncate_chunks_to_budget(context_chunks, max_tokens=3000)

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------

    @staticmethod
    def build_system_prompt(detected_lang: str, context_chunks: list[dict]) -> str:
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
            return (
                f"{lang_instruction}\n\n"
                "Note: Knowledge base results are currently unavailable. "
                "Answer based on your general knowledge and clearly state that "
                "you cannot verify the answer against the course material."
            )

        context_text = "\n".join(
            f"[{i + 1}] {chunk['title']}: {chunk['content']}"
            for i, chunk in enumerate(context_chunks)
        )
        return f"{lang_instruction}\n\nContext:\n{context_text}"

    # ------------------------------------------------------------------
    # LLM calling (with Sarvam -> Cloudflare AI fallback)
    # ------------------------------------------------------------------

    @staticmethod
    async def call_llm(
        system_prompt: str,
        sanitized_message: str,
        target_model: str,
        detected_lang: str,
        user_id: str,
    ) -> tuple[str, str]:
        """
        Call the LLM. On Sarvam failure for Assamese, falls back to Cloudflare AI.
        Returns (response_text, actual_model_used).
        """
        from app.services.ai.router import generate_response

        try:
            response_text = await generate_response(
                system_prompt=system_prompt,
                user_message=sanitized_message,
                model=target_model,
                stream=False,
            )
            return response_text, target_model
        except (RuntimeError, Exception) as e:
            if detected_lang == "as":
                logger.warning(f"Sarvam failed ({e}), falling back to Cloudflare AI")
                from app.services.ai.cloudflare_client import cloudflare_client

                actual_model = settings.CF_AI_MODEL
                response_text = await cloudflare_client.generate(
                    system_prompt=system_prompt,
                    user_message=sanitized_message,
                )
                return response_text, actual_model
            else:
                raise

    @staticmethod
    async def stream_llm(
        system_prompt: str,
        sanitized_message: str,
        target_model: str,
        detected_lang: str,
        user_id: str,
        request_message: str,
    ) -> AsyncGenerator[str, None]:
        """
        Stream LLM response as SSE events. On Sarvam failure for Assamese,
        falls back to Cloudflare AI. On double failure, stores a dead letter.

        Yields SSE-formatted data lines.
        """
        from app.services.ai.router import stream_response

        full_response = ""
        actual_model = target_model

        try:
            async for chunk in stream_response(
                system_prompt=system_prompt,
                user_message=sanitized_message,
                model=target_model,
            ):
                full_response += chunk
                yield f"data: {json.dumps({'text': chunk, 'done': False})}\n\n"

        except Exception as e:
            if detected_lang == "as":
                logger.warning(
                    f"Sarvam stream failed ({e}), falling back to Cloudflare AI"
                )
                logger.info(
                    "chat_fallback",
                    extra={
                        "user_id": user_id,
                        "error": str(e),
                        "fallback_provider": "cloudflare",
                    },
                )
                yield f"data: {json.dumps({'fallback': True, 'provider': 'cloudflare', 'reason': str(e)})}\n\n"

                try:
                    from app.services.ai.cloudflare_client import cloudflare_client

                    actual_model = settings.CF_AI_MODEL
                    async for chunk in cloudflare_client.stream_generate(
                        system_prompt, sanitized_message
                    ):
                        full_response += chunk
                        yield f"data: {json.dumps({'text': chunk, 'done': False})}\n\n"
                except Exception as fallback_err:
                    logger.error(f"Cloudflare fallback also failed: {fallback_err}")
                    from app.services.dead_letter import store_dead_letter

                    await store_dead_letter(
                        user_id, request_message, detected_lang, str(fallback_err)
                    )
                    yield f"data: {json.dumps({'error': 'Service temporarily unavailable. Please try again.'})}\n\n"
                    return
            else:
                logger.error(f"LLM stream failed: {e}")
                yield f"data: {json.dumps({'error': 'Service temporarily unavailable. Please try again.'})}\n\n"
                return

        # Emit the sentinel value so the router knows the model/response
        yield json.dumps(
            {
                "__internal_complete": True,
                "full_response": full_response,
                "actual_model": actual_model,
            }
        )

    # ------------------------------------------------------------------
    # Chat persistence (fire-and-forget)
    # ------------------------------------------------------------------

    @staticmethod
    async def save_chat(
        user_id: str,
        session_id: Optional[str],
        user_message: str,
        assistant_response: str,
        target_model: str,
        latency_ms: int,
        context_chunks: list[dict],
    ) -> None:
        """Persist chat to MongoDB. Designed to be called via asyncio.create_task."""
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

            # Invalidate conversation history cache for this session
            if session_id:
                await ChatService._invalidate_history_cache(session_id)

        except Exception as e:
            logger.error(f"Failed to save chat: {e}")

    # ------------------------------------------------------------------
    # Conversation history (with Redis caching, 30-min TTL)
    # ------------------------------------------------------------------

    @staticmethod
    async def load_conversation_history(
        session_id: Optional[str], max_turns: int = 5
    ) -> str:
        """
        Load recent conversation turns for multi-turn context.

        Aggregates across all Chat documents for a session_id, sorts by
        created_at, flattens messages, and returns the last N turns.
        Results are cached in Redis with a 30-minute TTL.
        """
        if not session_id:
            return ""

        # Try Redis cache first
        cached = await ChatService._get_history_from_cache(session_id, max_turns)
        if cached is not None:
            return cached

        # Cache miss - load from MongoDB
        try:
            from app.models.chat import Chat

            # Aggregate across all Chat documents for this session_id
            chats = (
                await Chat.find({"session_id": session_id})
                .sort("-created_at")
                .limit(5)
                .to_list()
            )
            # Reverse to chronological order for prompt building
            chats.reverse()
            if not chats:
                return ""

            # Flatten all messages across documents in chronological order
            all_messages = []
            for chat in chats:
                if chat.messages:
                    all_messages.extend(chat.messages)

            if not all_messages:
                return ""

            # Take last N turns (user + assistant pairs)
            recent = all_messages[-(max_turns * 2) :]
            history_lines = []
            for msg in recent:
                role = msg.get("role", "user")
                content = msg.get("content", "")[:500]  # Truncate long messages
                history_lines.append(f"{role.capitalize()}: {content}")

            # Cap total history to ~2000 chars
            history = "\n".join(history_lines)
            if len(history) > 2000:
                history = history[-2000:]

            # Cache the result
            await ChatService._set_history_cache(session_id, max_turns, history)

            return history
        except Exception:
            return ""

    @staticmethod
    async def _get_history_from_cache(session_id: str, max_turns: int) -> Optional[str]:
        """Attempt to retrieve conversation history from Redis cache."""
        try:
            redis = get_redis()
            cache_key = f"chat_history:{session_id}:{max_turns}"
            cached = await redis.get(cache_key)
            if cached is not None:
                return cached
        except (RuntimeError, Exception):
            # Redis unavailable - proceed without cache
            pass
        return None

    @staticmethod
    async def _set_history_cache(session_id: str, max_turns: int, history: str) -> None:
        """Store conversation history in Redis cache with TTL."""
        try:
            redis = get_redis()
            cache_key = f"chat_history:{session_id}:{max_turns}"
            await redis.set(cache_key, history, ex=_HISTORY_CACHE_TTL)
        except (RuntimeError, Exception):
            # Redis unavailable - silently skip caching
            pass

    @staticmethod
    async def _invalidate_history_cache(session_id: str) -> None:
        """Invalidate all cached history entries for a session on new message save."""
        try:
            redis = get_redis()
            # Invalidate common max_turns values
            for max_turns in (5, 10):
                cache_key = f"chat_history:{session_id}:{max_turns}"
                await redis.delete(cache_key)
        except (RuntimeError, Exception):
            # Redis unavailable - silently skip
            pass
