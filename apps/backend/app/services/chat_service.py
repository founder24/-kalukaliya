"""
ChatService: Encapsulates RAG chat business logic.

Responsibilities:
- Language resolution & model routing
- RAG retrieval (embedding + hybrid search)
- Prompt building with citation format
- LLM calling with Sarvam-to-Vertex fallback
- Chat persistence (fire-and-forget) with dead letter on double failure
- Conversation history loading with Redis caching
"""

import asyncio
import hashlib
import json
import logging
import re
import uuid
from typing import AsyncGenerator, Optional

from app.config import settings
from app.core.token_budget import truncate_chunks_to_budget
from app.db.redis import get_redis
from app.services.ai.router import detect_language_and_route
from app.services.search.mongo_vector_search import mongo_vector_search

logger = logging.getLogger(__name__)

# Redis cache TTL for conversation history (30 minutes)
_HISTORY_CACHE_TTL = 30 * 60

# Redis cache TTL for chat responses (10 minutes)
_RESPONSE_CACHE_TTL = 10 * 60

# Similarity threshold for filtering low-relevance RAG chunks
SIMILARITY_THRESHOLD = 0.60

# Pattern for detecting generic/greeting queries that should skip RAG
GENERIC_QUERY_PATTERN = re.compile(
    r"^(hi|hello|hey|thanks|thank you|ok|okay|bye|good morning|good evening|good night|how are you|what can you do|who are you|what are you)[\s!?.]*$",
    re.IGNORECASE,
)


class ChatService:
    """Encapsulates all chat business logic: RAG, LLM routing, persistence."""

    # ------------------------------------------------------------------
    # Generic query detection
    # ------------------------------------------------------------------

    @staticmethod
    def is_generic_query(message: str) -> bool:
        """Detect generic/greeting queries that should skip RAG entirely."""
        return bool(GENERIC_QUERY_PATTERN.match(message.strip()))

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
                else settings.CF_AI_MODEL
            )
        else:
            detected_lang, target_model = detect_language_and_route(message)
        return detected_lang, target_model

    # ------------------------------------------------------------------
    # Response caching
    # ------------------------------------------------------------------

    @staticmethod
    def _make_cache_hash(
        sanitized_message: str, lang: str, user_tier: str = "free"
    ) -> str:
        """Generate a cache key hash from message, language, and user tier."""
        cache_input = f"{sanitized_message}:{lang}:{user_tier}"
        return hashlib.sha256(cache_input.encode()).hexdigest()

    @staticmethod
    async def get_cached_response(message_hash: str) -> Optional[dict]:
        """Check Redis for a cached chat response."""
        try:
            redis = get_redis()
            cache_key = f"chat_cache:{message_hash}"
            cached = await redis.get(cache_key)
            if cached:
                return json.loads(cached)
        except (RuntimeError, Exception) as e:
            logger.debug(f"Response cache lookup failed: {e}")
        return None

    @staticmethod
    async def set_cached_response(message_hash: str, response: str, model: str) -> None:
        """Store a chat response in Redis with TTL."""
        try:
            redis = get_redis()
            cache_key = f"chat_cache:{message_hash}"
            payload = json.dumps({"response": response, "model": model})
            await redis.set(cache_key, payload, ex=_RESPONSE_CACHE_TTL)
        except (RuntimeError, Exception) as e:
            logger.debug(f"Response cache write failed: {e}")

    # ------------------------------------------------------------------
    # Topic embedding match
    # ------------------------------------------------------------------

    @staticmethod
    async def check_topic_match(query: str) -> Optional[dict]:
        """
        Generate an embedding for the user query and check against TopicMatcher.

        Returns match info dict (with score, topic metadata) if a topic matches
        above the 0.65 threshold, otherwise None.
        """
        try:
            from app.services.ai.embedder import generate_embedding_vector
            from app.services.ai.topic_matcher import topic_matcher

            # Bound the embedding call to 2.0s — the 0.5s limit was too
            # tight on cold GCP instances and caused RAG to be silently
            # skipped. topic_matcher.match_topic also hits MongoDB on first
            # call, so we give the full round-trip 2s budget.
            query_embedding = await asyncio.wait_for(
                generate_embedding_vector(query), timeout=2.0
            )
            return await topic_matcher.match_topic(query_embedding)
        except asyncio.TimeoutError:
            logger.warning("Topic match embedding call timed out (2.0s)")
            return None
        except Exception as e:
            logger.warning(f"Topic match check failed: {e}")
            return None

    # ------------------------------------------------------------------
    # RAG retrieval
    # ------------------------------------------------------------------

    @staticmethod
    async def retrieve_context_from_chapter(
        chapter_id: Optional[str],
        chapter_title: str,
        detected_lang: str,
    ) -> list[dict]:
        """
        MongoDB fast path: fetch chapter content directly using chapter_id
        from topic_match.  Bypasses Vertex AI Search entirely.

        Latency comparison:
          MongoDB path  ~20-60 ms  (Motor async, same connection pool)
          Vertex Search ~800-3000 ms (sync gRPC in thread pool)

        Falls back gracefully (returns []) on any error so the caller
        can fall through to the full MongoDB vector search path.
        """
        if not chapter_id:
            return []

        import time as _time
        t0 = _time.time()
        try:
            from app.models.content import Chapter
            from beanie import PydanticObjectId

            chapter = None
            try:
                chapter = await Chapter.get(PydanticObjectId(chapter_id))
            except Exception:
                chapter = await Chapter.find_one({"_id": chapter_id})

            if not chapter:
                logger.debug(f"mongo_fast_path: chapter {chapter_id!r} not found")
                return []

            content = (
                chapter.content_as
                if detected_lang == "as" and chapter.content_as
                else chapter.content_en
            )
            if not content:
                logger.debug(
                    f"mongo_fast_path: chapter '{chapter.title}' has no content"
                )
                return []

            from app.services.content.search_indexer import search_indexer
            chunks = search_indexer.chunk_text(content, chunk_size=500)

            latency_ms = int((_time.time() - t0) * 1000)
            logger.info(
                f"mongo_fast_path_hit: chapter='{chapter.title}', "
                f"lang={detected_lang}, chunks={len(chunks)}, "
                f"latency_ms={latency_ms}"
            )

            return [
                {
                    "id": f"{chapter.slug}_chunk_{i}",
                    "title": chapter.title,
                    "content": chunk,
                    "score": 0.85,
                    "reranker_score": 0.85,
                    "url": f"/{chapter.slug}",
                    "hierarchy": "",
                    "source": "mongodb",
                }
                for i, chunk in enumerate(chunks[:5])
            ]
        except Exception as e:
            logger.warning(f"mongo_fast_path error: {e}")
            return []

    @staticmethod
    async def retrieve_context(sanitized_message: str, user_tier: str) -> list[dict]:
        """
        Full MongoDB vector search RAG: embed query → cosine match topics →
        fetch chapter content.  Replaces Vertex AI Search entirely.
        """
        try:
            async def _do_retrieval():
                chunks, _ = await mongo_vector_search.search_context(
                    query=sanitized_message,
                    lang="en",
                    limit=settings.MAX_CONTEXT_DOCS,
                )
                chunks = [
                    c for c in chunks
                    if c.get("score", 0) >= SIMILARITY_THRESHOLD
                ]
                return truncate_chunks_to_budget(chunks, max_tokens=3000)

            return await asyncio.wait_for(_do_retrieval(), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning("MongoDB vector RAG timed out after 5s")
            return []
        except Exception as e:
            logger.error(f"MongoDB vector RAG failed: {e}")
            return []

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------

    @staticmethod
    def build_system_prompt(detected_lang: str, context_chunks: list[dict]) -> str:
        """Build system prompt with numbered [#] citation format."""
        lang_instruction = (
            "IMPORTANT: You MUST respond in English only. Do NOT respond in any other language including Bengali, Assamese, or Hindi.\n"
            "You are Syrabit, an expert educational assistant for Assamese students.\n"
            "Use the following numbered context to answer. If the answer is not in the context, say so clearly.\n"
            "Cite sources using [#] format (e.g., [1], [2]). Respond in English."
            if detected_lang == "en"
            # Assamese prompt written in Assamese script so the model reasons
            # in Assamese (reasoning_content tokens start at ~150 ms and are
            # streamed to the user immediately, giving <3 s TTFB).
            # The model accepts any input language but MUST think and respond
            # entirely in Assamese script — English reasoning is forbidden.
            else (
                "তুমি Syrabit, অসমৰ AHSEC, SEBA আৰু CBSE ছাত্ৰ-ছাত্ৰীৰ বাবে এজন শিক্ষামূলক সহায়ক।\n"
                "যিকোনো ভাষাত প্ৰশ্ন গ্ৰহণ কৰিব পাৰা।\n"
                "তোমাৰ সকলো চিন্তা-ভাৱনা আৰু উত্তৰ সম্পূৰ্ণৰূপে অসমীয়া ভাষা আৰু অসমীয়া লিপিত লিখিব।\n"
                "ইংৰাজী বা অন্য ভাষা ব্যৱহাৰ কৰা সম্পূৰ্ণ নিষিদ্ধ।\n"
                "তলত দিয়া নম্বৰযুক্ত প্ৰসংগ ব্যৱহাৰ কৰি উত্তৰ দিয়া। প্ৰসংগত নাথাকিলে স্পষ্টকৈ কোৱা।\n"
                "উদ্ধৃতিৰ বাবে [#] বিন্যাস ব্যৱহাৰ কৰক (যেনে [1], [2])।"
            )
        )

        if not context_chunks:
            if detected_lang == "en":
                return (
                    "IMPORTANT: You MUST respond in English only. Do NOT respond in any other language including Bengali, Assamese, or Hindi. "
                    "You are Syrabit, an expert educational AI for Assamese students covering AHSEC, SEBA, and CBSE curricula. "
                    "Answer the student's question directly and thoroughly using your training knowledge. "
                    "Be accurate, educational, and helpful. Do not add unnecessary disclaimers."
                )
            else:
                return (
                    "তুমি Syrabit, অসমৰ AHSEC, SEBA আৰু CBSE ছাত্ৰ-ছাত্ৰীৰ বাবে এজন শিক্ষামূলক সহায়ক।\n"
                    "যিকোনো ভাষাত প্ৰশ্ন গ্ৰহণ কৰিব পাৰা।\n"
                    "তোমাৰ সকলো চিন্তা-ভাৱনা আৰু উত্তৰ সম্পূৰ্ণৰূপে অসমীয়া ভাষা আৰু অসমীয়া লিপিত লিখিব।\n"
                    "ইংৰাজী বা অন্য ভাষা ব্যৱহাৰ কৰা সম্পূৰ্ণ নিষিদ্ধ।\n"
                    "ছাত্ৰৰ প্ৰশ্নৰ সঠিক আৰু সহায়কাৰী উত্তৰ দিয়া। স্পষ্ট, তথ্যপূৰ্ণ আৰু শিক্ষামূলক ব্যাখ্যা দিয়া।"
                )

        context_text = "\n".join(
            f"[{i + 1}] {chunk['title']}{' (' + chunk['hierarchy'] + ')' if chunk.get('hierarchy') else ''}: {chunk['content']}"
            for i, chunk in enumerate(context_chunks)
        )
        return f"{lang_instruction}\n\nContext:\n{context_text}"

    # ------------------------------------------------------------------
    # LLM calling (with Sarvam -> Vertex AI fallback)
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
        Call the LLM. On Sarvam failure for Assamese, falls back to Vertex AI.
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
                logger.warning(f"Sarvam failed ({e}), falling back to Vertex AI")
                from app.services.ai.vertex_client import vertex_client

                actual_model = settings.VERTEX_GEMINI_MODEL
                # Use generate_direct to bypass the vertex_circuit_breaker — the
                # normal circuit-protected path may be tripped by concurrent failures,
                # but we still want to attempt Vertex AI for this Assamese fallback.
                response_text = await vertex_client.generate_direct(
                    system_prompt=system_prompt,
                    user_message=sanitized_message,
                )
                return response_text, actual_model
            else:
                # English: CF Workers AI failed — fall back to Sarvam AI
                logger.warning(
                    f"CF Workers AI failed for English ({e}), falling back to Sarvam AI"
                )
                from app.services.ai.sarvam_client import sarvam_client

                actual_model = settings.SARVAM_MODEL
                response_text = await sarvam_client.generate(
                    system_prompt=system_prompt,
                    user_message=sanitized_message,
                )
                return response_text, actual_model

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
        falls back to Vertex AI. On double failure, stores a dead letter.

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
                yield f"data: {json.dumps({'content': chunk, 'done': False})}\n\n"

        except Exception as e:
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
                    async for chunk in vertex_client.stream_generate_with_retry(
                        system_prompt, sanitized_message
                    ):
                        full_response += chunk
                        yield f"data: {json.dumps({'content': chunk, 'done': False})}\n\n"
                except Exception as fallback_err:
                    logger.error(f"Vertex fallback also failed: {fallback_err}")
                    from app.services.dead_letter import store_dead_letter

                    await store_dead_letter(
                        user_id, request_message, detected_lang, str(fallback_err)
                    )
                    yield f"data: {json.dumps({'error': 'Service temporarily unavailable. Please try again.'})}\n\n"
                    return
            else:
                # English: CF Workers AI failed — fall back to Sarvam AI
                logger.warning(
                    f"CF Workers AI stream failed for English ({e}), falling back to Sarvam AI"
                )
                logger.info(
                    "chat_fallback",
                    extra={
                        "user_id": user_id,
                        "error": str(e),
                        "fallback_provider": "sarvam",
                        "detected_lang": "en",
                    },
                )
                yield f"data: {json.dumps({'fallback': True, 'provider': 'sarvam', 'reason': str(e)})}\n\n"

                try:
                    from app.services.ai.sarvam_client import sarvam_client

                    actual_model = settings.SARVAM_MODEL
                    async for chunk in sarvam_client.stream_generate_with_retry(
                        system_prompt, sanitized_message
                    ):
                        full_response += chunk
                        yield f"data: {json.dumps({'content': chunk, 'done': False})}\n\n"
                except Exception as fallback_err:
                    logger.error(f"Sarvam fallback also failed for English: {fallback_err}")
                    from app.services.dead_letter import store_dead_letter

                    await store_dead_letter(
                        user_id, request_message, detected_lang, str(fallback_err)
                    )
                    yield f"data: {json.dumps({'error': 'Service temporarily unavailable. Please try again.'})}\n\n"
                    return

        # Emit the sentinel value so the router knows the model/response
        yield f"data: {json.dumps({'__syrabit_stream_complete_7f3a9b2e__': True, 'full_response': full_response, 'actual_model': actual_model})}\n\n"

    # ------------------------------------------------------------------
    # Chat persistence (fire-and-forget)
    # ------------------------------------------------------------------

    @staticmethod
    def _serialize_messages(messages: list) -> list:
        """Ensure all message dict values are JSON-serializable before Chat save.

        Round-trips through JSON to flush out non-serializable types such as
        ObjectId, datetime without isoformat, or Pydantic models.  The
        default=str fallback converts them to their string representation,
        which is safe since all downstream consumers treat messages as plain dicts.
        """
        try:
            return json.loads(json.dumps(messages, default=str))
        except Exception:
            return messages

    @staticmethod
    async def save_chat(
        user_id: str,
        session_id: Optional[str],
        user_message: str,
        assistant_response: str,
        target_model: str,
        latency_ms: int,
        context_chunks: list[dict],
        detected_lang: str = "unknown",
    ) -> None:
        """Persist chat to MongoDB. Designed to be called via asyncio.create_task."""
        rag_sources = ChatService._serialize_messages([
            {"doc_id": c["id"], "title": c["title"], "score": c["score"]}
            for c in context_chunks
        ])
        # Ensure session_id is never None — Chat.session_id is typed as str
        # and Pydantic v2 refuses None even when a default_factory is set.
        resolved_session_id = session_id or str(uuid.uuid4())

        try:
            from app.models.chat import Chat

            chat_doc = Chat(
                user_id=user_id,
                session_id=resolved_session_id,
            )
            chat_doc.add_message(role="user", content=user_message)
            chat_doc.add_message(
                role="assistant",
                content=assistant_response,
                model_used=target_model,
                latency_ms=latency_ms,
                rag_sources=rag_sources,
            )
            await chat_doc.save()

            # Invalidate history cache so next read refills from MongoDB
            if session_id:
                await ChatService._invalidate_history_cache(session_id)

        except Exception as e:
            logger.error(f"Failed to save chat (attempt 1): {e}")
            # Retry once
            try:
                from app.models.chat import Chat

                chat_doc = Chat(
                    user_id=user_id,
                    session_id=resolved_session_id,
                )
                chat_doc.add_message(role="user", content=user_message)
                chat_doc.add_message(
                    role="assistant",
                    content=assistant_response,
                    model_used=target_model,
                    latency_ms=latency_ms,
                    rag_sources=rag_sources,
                )
                await chat_doc.save()

                if session_id:
                    await ChatService._invalidate_history_cache(session_id)
            except Exception as retry_err:
                logger.error(f"Failed to save chat (attempt 2): {retry_err}")
                # Log the lost message payload (truncated) for manual recovery
                logger.error(
                    "chat_message_lost",
                    extra={
                        "user_id": user_id,
                        "session_id": session_id,
                        "user_message_truncated": user_message[:200],
                        "error": str(retry_err),
                    },
                )
                # Store dead letter for later recovery
                from app.services.dead_letter import store_dead_letter

                await store_dead_letter(
                    user_id, user_message, detected_lang, str(retry_err)
                )

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
                .to_list(length=5)
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
        """Invalidate all cached history entries for a session on new message save.
        Uses a Redis pipeline to batch all DELETEs in a single round-trip.

        Note (HF-014): The hardcoded max_turns values (3, 5, 10, 15, 20) cover all
        expected usage. The only production caller uses max_turns=5. If new callers
        use non-standard values, add them here or switch to wildcard pattern deletion.
        """
        try:
            redis = get_redis()
            pipe = redis.pipeline()
            for max_turns in (3, 5, 10, 15, 20):
                cache_key = f"chat_history:{session_id}:{max_turns}"
                pipe.delete(cache_key)
            await pipe.execute()
        except (RuntimeError, Exception):
            # Redis unavailable - silently skip
            pass
