"""
ChatService: Business logic for chat operations.
Extracted from chat.py to keep the router thin (ARCH-06).
Includes Redis caching for search results (PERF-04) and conversation history (PERF-05).
"""

import hashlib
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

from app.config import settings
from app.db.redis import get_redis
from app.services.search.azure_search import search_service
from app.services.ai.router import detect_language_and_route
from app.core.security import sanitize_user_input
from app.core.token_budget import truncate_chunks_to_budget

logger = logging.getLogger(__name__)


class ChatService:
    """Business logic for chat operations, extracted from the chat router."""

    async def check_rate_limit(
        self, user_id: str, user_tier: str, client_ip: str = None
    ) -> tuple[bool, int, int]:
        """Check if user has exceeded rate limit. Returns (allowed, current_count, limit)."""
        redis = get_redis()

        limit = (
            settings.RATE_LIMIT_PRO_TIER
            if user_tier == "pro"
            else settings.RATE_LIMIT_FREE_TIER
        )

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

    async def load_conversation_history(
        self, session_id: Optional[str], max_turns: int = 5
    ) -> str:
        """Load recent conversation history with Redis caching (PERF-05)."""
        if not session_id:
            return ""

        cache_key = f"conv:{session_id}"

        # Check Redis cache
        try:
            redis = get_redis()
            cached = await redis.get(cache_key)
            if cached:
                return cached
        except Exception:
            pass

        # Load from MongoDB
        try:
            from app.models.chat import Chat

            chat = await Chat.find_one({"session_id": session_id})
            if not chat or not chat.messages:
                return ""
            recent = chat.messages[-(max_turns * 2) :]
            history_lines = []
            for msg in recent:
                role = msg.get("role", "user")
                content = msg.get("content", "")[:500]
                history_lines.append(f"{role.capitalize()}: {content}")
            history = "\n".join(history_lines)
            if len(history) > 2000:
                history = history[-2000:]
        except Exception:
            return ""

        # Cache the result
        try:
            redis = get_redis()
            await redis.set(cache_key, history, ex=1800)  # 30 min TTL
        except Exception:
            pass

        return history

    async def search_with_cache(
        self, query: str, text: str, user_tier: str, limit: int = 5
    ) -> list[dict]:
        """Search Azure with Redis caching (PERF-04). TTL 5 minutes."""
        cache_key = f"search:{hashlib.sha256((query + text + user_tier + str(limit)).encode()).hexdigest()[:32]}"

        try:
            redis = get_redis()
            cached = await redis.get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception:
            pass  # Cache miss or Redis unavailable - proceed with search

        results = await search_service.search_context(
            query=query, text=text, user_tier=user_tier, limit=limit
        )

        try:
            redis = get_redis()
            await redis.set(cache_key, json.dumps(results), ex=300)  # 5 min TTL
        except Exception:
            pass  # Cache write failure is non-critical

        return results

    def resolve_lang_and_model(
        self, message: str, lang_override: Optional[str] = None
    ) -> tuple[str, str]:
        """Resolve language and target model."""
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

    def build_system_prompt(
        self, detected_lang: str, context_chunks: list[dict]
    ) -> str:
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

    async def save_chat(
        self,
        user_id: str,
        session_id: Optional[str],
        user_message: str,
        assistant_response: str,
        target_model: str,
        latency_ms: int,
        context_chunks: list[dict],
    ) -> None:
        """Save chat to MongoDB and invalidate conversation cache."""
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
            logger.error(f"Failed to save chat: {e}")

        # Invalidate conversation cache
        await self.invalidate_conversation_cache(session_id)

    async def invalidate_conversation_cache(self, session_id: Optional[str]) -> None:
        """Invalidate conversation history cache after new message."""
        if not session_id:
            return
        try:
            redis = get_redis()
            await redis.delete(f"conv:{session_id}")
        except Exception as e:
            logger.debug(f"Failed to invalidate conversation cache: {e}")

    async def process_chat(
        self,
        message: str,
        lang: Optional[str],
        session_id: Optional[str],
        user_id: str,
        user_tier: str,
        user: object = None,
        start_time: float = None,
    ) -> dict:
        """
        Full non-streaming chat pipeline: sanitize, search, build prompt, call LLM, save.
        Returns dict with response_text, target_model, latency_ms, context_chunks.
        """
        sanitized_message = sanitize_user_input(message)

        # Resolve language and model
        detected_lang, target_model = self.resolve_lang_and_model(
            sanitized_message, lang
        )

        logger.info(
            "chat_started",
            extra={
                "user_id": user_id,
                "lang": detected_lang,
                "model": target_model,
            },
        )

        # Generate embedding for RAG
        from app.services.ai.embedder import generate_embedding

        query_text = await generate_embedding(sanitized_message)

        # Hybrid search with Redis caching (PERF-04)
        context_chunks = await self.search_with_cache(
            query=sanitized_message,
            text=query_text,
            user_tier=user_tier,
            limit=settings.MAX_CONTEXT_DOCS,
        )

        # Apply token budget to context chunks
        context_chunks = truncate_chunks_to_budget(context_chunks, max_tokens=3000)

        # Build system prompt with context
        system_prompt = self.build_system_prompt(detected_lang, context_chunks)

        # Include multi-turn conversation history (PERF-05)
        history = await self.load_conversation_history(session_id)
        if history:
            system_prompt = f"{system_prompt}\n\nPrevious conversation:\n{history}"

        # Call LLM
        from app.services.ai.router import generate_response

        response_text = await generate_response(
            system_prompt=system_prompt,
            user_message=sanitized_message,
            model=target_model,
            stream=False,
        )

        latency_ms = int((time.time() - start_time) * 1000) if start_time else 0

        # Save chat to MongoDB and invalidate cache
        await self.save_chat(
            user_id=user_id,
            session_id=session_id,
            user_message=sanitized_message,
            assistant_response=response_text,
            target_model=target_model,
            latency_ms=latency_ms,
            context_chunks=context_chunks,
        )

        # Update usage counter
        if user:
            from datetime import timezone as tz

            await user.update(
                {
                    "$inc": {
                        "monthly_message_count": 1,
                        "total_lifetime_messages": 1,
                    },
                    "$set": {"updated_at": datetime.now(tz.utc)},
                }
            )

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

        return {
            "response_text": response_text,
            "target_model": target_model,
            "detected_lang": detected_lang,
            "latency_ms": latency_ms,
            "context_chunks": context_chunks,
        }

    async def prepare_stream_context(
        self,
        message: str,
        lang: Optional[str],
        session_id: Optional[str],
        user_tier: str,
    ) -> dict:
        """
        Prepare context for streaming: sanitize, embed, search (cached), build prompt.
        Returns dict with sanitized_message, detected_lang, target_model,
        context_chunks, system_prompt.
        """
        sanitized_message = sanitize_user_input(message)
        detected_lang, target_model = self.resolve_lang_and_model(
            sanitized_message, lang
        )

        from app.services.ai.embedder import generate_embedding

        embedding = await generate_embedding(sanitized_message)
        context_chunks = await self.search_with_cache(
            query=sanitized_message,
            text=embedding,
            user_tier=user_tier,
            limit=settings.MAX_CONTEXT_DOCS,
        )
        context_chunks = truncate_chunks_to_budget(context_chunks, max_tokens=3000)

        system_prompt = self.build_system_prompt(detected_lang, context_chunks)

        history = await self.load_conversation_history(session_id)
        if history:
            system_prompt = f"{system_prompt}\n\nPrevious conversation:\n{history}"

        return {
            "sanitized_message": sanitized_message,
            "detected_lang": detected_lang,
            "target_model": target_model,
            "context_chunks": context_chunks,
            "system_prompt": system_prompt,
        }
