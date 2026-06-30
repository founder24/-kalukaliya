"""
ChatService: Encapsulates RAG chat business logic.

Responsibilities:
- Language resolution & model routing (Sarvam AI for all languages)
- RAG retrieval (embedding + MongoDB vector search)
- Prompt building with citation format
- LLM calling via Sarvam AI
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
from app.services.search.web_search import web_search as _web_search

logger = logging.getLogger(__name__)

# Redis cache TTL for conversation history (30 minutes)
_HISTORY_CACHE_TTL = 30 * 60

# Redis cache TTL for chat responses (10 minutes)
_RESPONSE_CACHE_TTL = 10 * 60

# Similarity threshold for filtering low-relevance RAG chunks
SIMILARITY_THRESHOLD = 0.60

# Confidence gate thresholds for the topic embedding match score.
# HIGH  (≥ 0.80): strong match → MongoDB fast path only, web skipped
# MID   (≥ 0.65): good match  → MongoDB fast path + light web in parallel
# LOW   (≥ 0.50): weak match  → retrieve_v2 full pipeline + web as scaffold
# NONE  (< 0.50): no match    → web + LLM only
CONFIDENCE_HIGH = 0.80
CONFIDENCE_MID = 0.65   # mirrors MATCH_THRESHOLD in topic_matcher
CONFIDENCE_LOW = 0.50

# Pattern for detecting generic/greeting queries that should skip RAG.
# Covers typos with repeated chars (hii, heyy, heyyy, helloo), common
# English affirmatives / farewells, and Assamese script greetings.
GENERIC_QUERY_PATTERN = re.compile(
    r"^("
    # ── English patterns ──────────────────────────────────────────────
    r"hi+|he+y+|he+llo+|helo+|"                              # hi / hey / hello variants
    r"thanks?|thank\s+you|ty|thnx|thx|"                     # thanks / thank you
    r"ok+a*y*|o+k+|k|"                                       # ok / okay / okk / k
    r"bye+|good\s*bye+|see\s+ya|see\s+you|cya|"             # bye / goodbye
    r"good\s+(?:morning|evening|night|day|afternoon)|gm|gn|" # time greetings + abbreviations
    r"how\s+are\s+(?:you|u)|how\s+r\s+u|"                   # how are you variants
    r"whats?\s+up|sup|"                                      # what's up
    r"what\s+can\s+you\s+do|"                               # what can you do
    r"who\s+are\s+you|what\s+are\s+you|"                    # identity questions
    r"nice|great|awesome|cool|perfect|"                      # short affirmatives
    r"sure|got\s+it|understood|noted|alright|alrite|"        # acknowledgements
    # ── Assamese script greetings (Unicode range \u0980–\u09FF) ───────
    r"নমস্কাৰ|নমস্কৰ|নমস্কাৰে|"                            # Namaskar variants
    r"হেলো|হেলৌ|হাই|"                                        # Hello / Hi in Assamese
    r"ধন্যবাদ|থেংকু|আভাৰী|বহুত\s+ধন্যবাদ|"                 # Thanks
    r"বিদায়|বাই|যাওঁ|"                                       # Bye
    r"ঠিক\s+আছে|ঠিকেই|বুজিলোঁ|বেছি\s+ভাল|"                 # Ok / Understood
    r"কেনে\s+আছা|কেমন\s+আছ|কেনে\s+আছে|ভাল\s+আছানে|"        # How are you
    r"শুভ\s+ৰাতিপুৱা|শুভ\s+প্ৰভাত|শুভ\s+গধূলি|শুভ\s+নিশা|শুভৰাত্ৰি"  # Time greetings
    r")[\s!?.,'\u0964\u09F7]*$",                             # trailing punctuation incl. ৷ ।
    re.IGNORECASE,
)


class ChatService:
    """Encapsulates all chat business logic: RAG, LLM routing, persistence."""

    # ------------------------------------------------------------------
    # Generic query detection
    # ------------------------------------------------------------------

    @staticmethod
    def is_generic_query(message: str) -> bool:
        """Detect generic/greeting queries that should skip RAG entirely.

        Two-gate check:
        1. Regex match against known greeting / chatter patterns (handles typos
           like 'hii', 'heyy', repeated chars).
        2. Ultra-short fallback: messages with ≤ 5 non-whitespace characters
           that contain no Assamese/Bengali script are almost never substantive
           educational questions ('k', 'ok', 'ty', '?', '??', etc.).
        """
        stripped = message.strip()
        if GENERIC_QUERY_PATTERN.match(stripped):
            return True
        # Fallback: ultra-short messages with no Assamese script
        non_ws = re.sub(r"\s+", "", stripped)
        if len(non_ws) <= 5 and not re.search(r"[\u0980-\u09FF]", stripped):
            return True
        return False

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
            target_model = settings.SARVAM_MODEL
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
        match, _ = await ChatService.check_topic_match_with_embedding(query)
        return match

    @staticmethod
    async def check_topic_match_with_embedding(
        query: str,
    ) -> tuple[Optional[dict], Optional[list[float]]]:
        """
        Generate an embedding for the user query and check against TopicMatcher.

        Returns (match_dict, embedding_vector) so callers can reuse the
        embedding in downstream retrieval without a second CF API call.
        match_dict is None when no topic clears the threshold.
        embedding_vector is None only on hard embedding failure.
        """
        try:
            from app.services.ai.embedder import generate_embedding_vector
            from app.services.ai.topic_matcher import topic_matcher

            query_embedding = await asyncio.wait_for(
                generate_embedding_vector(query), timeout=1.0
            )
            match = await topic_matcher.match_topic(query_embedding)
            return match, query_embedding
        except asyncio.TimeoutError:
            logger.warning("Topic match embedding call timed out (1.0s) — falling back to web")
            return None, None
        except Exception as e:
            logger.warning(f"Topic match check failed: {e}")
            return None, None

    @staticmethod
    async def build_source_card(
        topic_match: Optional[dict],
        context_chunks: list[dict],
        web_chunks: list[dict],
        rag_path: str,
        confidence_tier: str,
    ) -> "SourceCard":
        """
        Build a SourceCard from the best available retrieval signal.

        Priority: topic_match metadata > first chunk metadata > web > llm_only.
        topic_match keys from TopicMatcher: topic_id, topic_title, chapter_id,
        chapter_title, subject_slug, board_slug, class_level, score.

        subject_name is resolved from the Subject collection by slug so the SSE
        payload is self-contained and the frontend fallback is only backup.
        """
        from app.models.source_card import SourceCard

        if rag_path in ("fast", "mongodb"):
            source_type = "rag_chapter"
        elif rag_path == "vectorize":
            source_type = "rag_vectorize"
        elif rag_path in ("legacy_atlas",):
            source_type = "rag_atlas"
        elif rag_path in ("legacy_inmem",):
            source_type = "rag_inmem"
        elif rag_path == "web":
            source_type = "web"
        else:
            source_type = "llm_only"

        if topic_match:
            subject_slug = topic_match.get("subject_slug")
            board_slug = topic_match.get("board_slug")
            subject_name: Optional[str] = None
            if subject_slug:
                try:
                    from app.models.content import Subject
                    subj = await Subject.find_one({"slug": subject_slug})
                    if subj:
                        subject_name = subj.name
                except Exception as e:
                    logger.debug(f"build_source_card: subject lookup failed: {e}")

            # Derive board_name from slug for display: "ahsec" → "AHSEC"
            board_name = board_slug.upper() if board_slug else None

            return SourceCard(
                subject_name=subject_name,
                subject_slug=subject_slug,
                chapter_name=topic_match.get("chapter_title"),
                topic_name=topic_match.get("topic_title"),
                class_level=topic_match.get("class_level"),
                board_name=board_name,
                board_slug=board_slug,
                match_score=topic_match.get("score", 0.0),
                source_type=source_type,
                rag_path=rag_path,
                confidence_tier=confidence_tier,
                rag_chunks=len(context_chunks),
            )

        if context_chunks:
            first = context_chunks[0]
            return SourceCard(
                chapter_name=first.get("title"),
                chapter_slug=first.get("url", "").lstrip("/") or None,
                match_score=first.get("score", 0.0),
                source_type=source_type,
                rag_path=rag_path,
                confidence_tier=confidence_tier,
                rag_chunks=len(context_chunks),
            )

        if web_chunks:
            return SourceCard(
                source_type="web",
                rag_path="web",
                confidence_tier=confidence_tier,
            )

        return SourceCard(
            source_type="llm_only",
            rag_path="none",
            confidence_tier=confidence_tier,
        )

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

            # Prefer rag_text (pure prose, retrieval-optimised) over content_* (may
            # contain HTML/UI markup that degrades RAG quality).
            # Cross-lingual fallback: if Assamese rag_text absent, fall back to English.
            if detected_lang == "as":
                content = (
                    chapter.rag_text_as
                    or chapter.content_as
                    or chapter.rag_text_en
                    or chapter.content_en
                )
            else:
                content = chapter.rag_text_en or chapter.content_en

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
    async def retrieve_web_context(
        query: str,
        lang: str = "en",
    ) -> list[dict]:
        """
        Fetch web snippets via DuckDuckGo (no API key).

        Returns [] on any failure so the caller degrades gracefully.
        Budget: up to 4 snippets, capped to ~600 tokens in build_system_prompt.
        """
        try:
            chunks = await asyncio.wait_for(
                _web_search(query, lang=lang),
                timeout=1.5,
            )
            logger.info(f"web_context: {len(chunks)} snippets for lang={lang}")
            return chunks
        except asyncio.TimeoutError:
            logger.warning("retrieve_web_context timed out (1.5s) — skipping web fallback")
            return []
        except Exception as e:
            logger.warning(f"retrieve_web_context failed: {e}")
            return []

    @staticmethod
    async def retrieve_context(
        sanitized_message: str,
        user_tier: str,
        lang: str = "en",
        filters: Optional[dict] = None,
        embedding: Optional[list[float]] = None,
    ) -> tuple[list[dict], str]:
        """
        Full RAG retrieval: tries Vectorize (v2) first, falls back to legacy
        Atlas $vectorSearch → in-memory cosine on topic_embeddings.

        Args:
            embedding: Pre-computed query embedding from check_topic_match_with_embedding.
                       When supplied, retrieve_v2 skips the CF Workers AI embed call
                       (eliminates the double-embed latency hit).

        Returns:
            (chunks, rag_path) where rag_path ∈ {'fast','vectorize','legacy_atlas','legacy_inmem','empty'}

        Path priority (handled inside retrieve_v2):
          1. TopicMatcher fast path (in-memory, <5ms)
          2. CF Vectorize top-K + MongoDB chunk hydration (~120ms)
          3. Atlas $vectorSearch on rag_chunks (legacy v1)
          4. In-memory cosine on topic_embeddings (pre-ingest fallback)
        """
        try:
            async def _do_retrieval():
                from app.services.rag.retrieval_v2 import retrieve_v2
                chunks, path = await retrieve_v2(
                    query=sanitized_message,
                    lang=lang,
                    filters=filters or {},
                    limit=settings.MAX_CONTEXT_DOCS,
                    embedding=embedding,
                )
                chunks = [c for c in chunks if c.get("score", 0) >= SIMILARITY_THRESHOLD]
                logger.info(
                    f"retrieve_context: path={path} lang={lang} "
                    f"chunks_returned={len(chunks)}"
                )
                return truncate_chunks_to_budget(chunks, max_tokens=3000), path

            return await asyncio.wait_for(_do_retrieval(), timeout=3.0)
        except asyncio.TimeoutError:
            logger.warning("retrieve_context timed out after 3s — falling back to web")
            return [], "empty"
        except Exception as e:
            logger.error(f"retrieve_context failed: {e}")
            return [], "empty"

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------

    @staticmethod
    def build_system_prompt(
        detected_lang: str,
        context_chunks: list[dict],
        web_chunks: list[dict] | None = None,
        user_board: str | None = None,
        user_class: str | None = None,
    ) -> str:
        """
        Build weighted system prompt.

        Source priority and token budget:
          RAG available   → RAG 50% (~1500 tok) · Web 20% (~600 tok) · LLM 30%
          RAG unavailable → Web 50% (~1500 tok) · LLM 50%

        detected_lang is the RESPONSE language (driven by the user's language
        selector, not auto-detected from input).  The model must always reply
        in that language regardless of the input language.
        """
        web_chunks = web_chunks or []
        has_rag = bool(context_chunks)
        has_web = bool(web_chunks)

        # ── Language-specific base instruction ──────────────────────────────
        if detected_lang == "en":  # noqa: SIM108
            base = (
                "You are Syrabit, an educational AI assistant for AHSEC, SEBA, and CBSE students.\n"
                "LANGUAGE RULE: The student selected English mode. "
                "You MUST reply in English ONLY — never mix in Assamese, Hindi, or any other language. "
                "This rule is absolute: even if the student's question is written in Assamese, Hindi, or any other language, "
                "your entire response must be in English only. Do not switch languages mid-response.\n"
                "STYLE: Give a dense, syllabus-aligned answer covering every key fact, subtopic, and "
                "subpoint in as few words as possible. No filler phrases, no opening/closing pleasantries.\n"
                "ACCURACY: Prioritise curriculum sources. If unsure, say so clearly."
            )
            rag_header = "## Curriculum Knowledge (PRIMARY — weight 50%)"
            web_header = "## Supplementary Web Sources (weight 20%)"
            blend_rule_with_rag = (
                "SOURCE BLENDING RULES:\n"
                "1. Base your answer primarily on the Curriculum Knowledge (50% weight).\n"
                "2. Use Supplementary Web Sources only to add detail not in the curriculum (20% weight).\n"
                "3. Apply your own reasoning and knowledge to connect and explain (30% weight).\n"
                "4. Cite curriculum chunks as [C1], [C2]… and web snippets as [W1], [W2]… inline.\n"
                "5. If the curriculum context does not cover the question, say so in one sentence "
                "before using web/LLM knowledge."
            )
            blend_rule_web_only = (
                "SOURCE BLENDING RULES (no curriculum context available):\n"
                "1. Use the Web Sources below as your primary reference (50% weight).\n"
                "2. Apply your own knowledge and reasoning for the remaining 50%.\n"
                "3. Cite web snippets as [W1], [W2]… inline.\n"
                "4. Clearly flag any fact you are not certain about."
            )
            blend_rule_llm_only = (
                "No curriculum or web context is available for this query.\n"
                "Answer using your own knowledge, but clearly note that your answer "
                "is based on general knowledge, not a retrieved curriculum source.\n"
                "Keep the answer syllabus-aligned with AHSEC/SEBA/CBSE where applicable."
            )
            citation_note_rag = (
                "CITATIONS: cite inline as [C1], [C2]… for curriculum, [W1], [W2]… for web."
            )
        else:
            base = (
                "তুমি Syrabit — AHSEC, SEBA আৰু CBSE ৰ ছাত্ৰ-ছাত্ৰীৰ বাবে এটা শিক্ষামূলক AI সহায়ক।\n"
                "ভাষাৰ নিয়ম: ছাত্ৰই অসমীয়া ম'ড বাছি লৈছে। "
                "তুমি কেৱল সম্পূৰ্ণ অসমীয়া ভাষাতহে উত্তৰ দিবা — ইংৰাজী, হিন্দী বা অন্য কোনো ভাষা মিহলি নকৰিবা। "
                "এই নিয়ম নিৰপেক্ষ: ছাত্ৰই ইংৰাজী বা অন্য ভাষাত প্ৰশ্ন কৰিলেও তোমাৰ সম্পূৰ্ণ উত্তৰ কেৱল অসমীয়াত হ'ব লাগিব। "
                "মাজত ভাষা সলনি নকৰিবা — কেৱল প্ৰযুক্তিগত পৰিভাষা বা ব্ৰেণ্ড নামহে ইংৰাজীত ৰাখিব পাৰিবা।\n"
                "শৈলী: পাঠ্যক্ৰম-সংগতিপূৰ্ণ, ঘন আৰু তথ্যসমৃদ্ধ উত্তৰ দিয়া — সকলো মূল তথ্য আৰু উপবিষয় "
                "যথাসম্ভৱ কম শব্দত আৱৰিব। অপ্ৰয়োজনীয় ভূমিকা বা সমাপ্তি বাক্য লিখিব নালাগে।\n"
                "শুদ্ধতা: পাঠ্যক্ৰমৰ উৎসক অগ্ৰাধিকাৰ দিয়া। অনিশ্চিত হ'লে স্পষ্টকৈ কোৱা।"
            )
            rag_header = "## পাঠ্যক্ৰম জ্ঞান (মুখ্য উৎস — ৫০% গুৰুত্ব)"
            web_header = "## সম্পূৰক ৱেব উৎস (২০% গুৰুত্ব)"
            blend_rule_with_rag = (
                "উৎস সংমিশ্ৰণৰ নিয়ম:\n"
                "১. পাঠ্যক্ৰম জ্ঞানক মুখ্য ভিত্তি হিচাপে ব্যৱহাৰ কৰা (৫০% গুৰুত্ব)।\n"
                "২. ৱেব উৎস কেৱল অতিৰিক্ত বিৱৰণৰ বাবে ব্যৱহাৰ কৰা (২০% গুৰুত্ব)।\n"
                "৩. নিজৰ যুক্তি আৰু জ্ঞান প্ৰয়োগ কৰা (৩০% গুৰুত্ব)।\n"
                "৪. পাঠ্যক্ৰমৰ তথ্য [C1], [C2]… আৰু ৱেব তথ্য [W1], [W2]… হিচাপে উদ্ধৃত কৰা।\n"
                "৫. প্ৰসংগত উত্তৰ নাথাকিলে এটা বাক্যত কোৱা।"
            )
            blend_rule_web_only = (
                "উৎস সংমিশ্ৰণৰ নিয়ম (পাঠ্যক্ৰম প্ৰসংগ উপলব্ধ নহয়):\n"
                "১. তলৰ ৱেব উৎসক মুখ্য তথ্যসূত্ৰ হিচাপে ব্যৱহাৰ কৰা (৫০% গুৰুত্ব)।\n"
                "২. নিজৰ জ্ঞান আৰু যুক্তি বাকী ৫০%ত প্ৰয়োগ কৰা।\n"
                "৩. ৱেব তথ্য [W1], [W2]… হিচাপে উদ্ধৃত কৰা।"
            )
            blend_rule_llm_only = (
                "এই প্ৰশ্নৰ বাবে কোনো পাঠ্যক্ৰম বা ৱেব প্ৰসংগ উপলব্ধ নহয়।\n"
                "নিজৰ জ্ঞানৰ পৰা উত্তৰ দিয়া, কিন্তু স্পষ্টকৈ উল্লেখ কৰা যে উত্তৰটো সাধাৰণ জ্ঞানৰ ওপৰত ভিত্তি কৰি দিয়া হৈছে।\n"
                "AHSEC/SEBA/CBSE পাঠ্যক্ৰমৰ সৈতে সংগতি ৰক্ষা কৰা।"
            )
            citation_note_rag = (
                "উদ্ধৃতি: পাঠ্যক্ৰম তথ্যৰ বাবে [C1], [C2]… আৰু ৱেব তথ্যৰ বাবে [W1], [W2]… ইনলাইনত লিখক।"
            )

        # ── Student profile context (board / class) ──────────────────────────
        if user_board or user_class:
            profile_ctx = " · ".join(filter(None, [user_board, user_class]))
            base += (
                f"\nSTUDENT PROFILE: This student is from {profile_ctx}. "
                "Tailor all syllabus references, examples, and board-specific content accordingly. "
                "Prioritise chapters and exam patterns relevant to their board and class."
            )

        # ── No context at all ───────────────────────────────────────────────
        if not has_rag and not has_web:
            return f"{base}\n\n{blend_rule_llm_only}"

        # ── Build context sections ───────────────────────────────────────────
        sections: list[str] = [base]

        if has_rag:
            # Token budget for RAG: ~1500 tokens (50% of 3000 total)
            rag_chunks = truncate_chunks_to_budget(context_chunks, max_tokens=1500)
            rag_text = "\n".join(
                f"[C{i + 1}] {c['title']}"
                f"{' (' + c['hierarchy'] + ')' if c.get('hierarchy') else ''}: "
                f"{c['content']}"
                for i, c in enumerate(rag_chunks)
            )
            sections.append(f"{rag_header}\n{rag_text}")

        if has_web:
            # Token budget for web: ~1500 tok when RAG empty, else ~600 tok
            web_budget = 600 if has_rag else 1500
            web_capped = truncate_chunks_to_budget(web_chunks, max_tokens=web_budget)
            web_text = "\n".join(
                f"[W{i + 1}] {c['title']}: {c['content']}"
                for i, c in enumerate(web_capped)
            )
            sections.append(f"{web_header}\n{web_text}")

        # ── Blend rule ───────────────────────────────────────────────────────
        if has_rag:
            sections.append(blend_rule_with_rag)
            sections.append(citation_note_rag)
        else:
            sections.append(blend_rule_web_only)

        return "\n\n".join(sections)

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
            logger.error(f"Sarvam generate failed (lang={detected_lang}): {e}")
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
            logger.error(
                f"LLM stream failed (lang={detected_lang}): {e}",
                extra={"user_id": user_id, "error": str(e)},
            )
            try:
                from app.services.dead_letter import store_dead_letter

                await store_dead_letter(
                    user_id, request_message, detected_lang, str(e)
                )
            except Exception as dl_err:
                logger.warning(f"Dead-letter store failed: {dl_err}")
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
    def _generate_title(user_message: str) -> str:
        """Generate a short conversation title from the first user message.

        Takes the first 8 words, strips special characters, caps at 60 chars.
        Assamese/Bengali Unicode script is preserved ([\u0980-\u09FF]).
        """
        clean = re.sub(r"[^\w\s\u0980-\u09FF]", "", user_message.strip())
        words = clean.split()[:8]
        title = " ".join(words)
        return title[:60] if title else "Chat"

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
        source_card=None,
    ) -> None:
        """Persist chat to MongoDB. Designed to be called via asyncio.create_task."""
        rag_sources = ChatService._serialize_messages([
            {"doc_id": c["id"], "title": c["title"], "score": c["score"]}
            for c in context_chunks
        ])
        # Persist source card context so history page can reconstruct grounding.
        source_ctx: dict = {}
        if source_card is not None:
            try:
                d = source_card.to_sse_dict()
                source_ctx = {
                    "source_type": d.get("source_type"),
                    "confidence_tier": d.get("confidence_tier"),
                    "rag_path": d.get("rag_path"),
                    "match_score": d.get("match_score"),
                    "rag_subject_id": d.get("rag_subject_id"),
                    "rag_subject_name": d.get("rag_subject_name"),
                    "rag_subject_slug": d.get("rag_subject_slug"),
                    "rag_chapter_name": d.get("rag_chapter_name"),
                    "rag_chapter_slug": d.get("rag_chapter_slug"),
                    "ctx_board_name": d.get("ctx_board_name"),
                    "ctx_class_name": d.get("ctx_class_name"),
                    "ctx_stream_name": d.get("ctx_stream_name"),
                }
                # Remove None values to keep message docs clean.
                source_ctx = {k: v for k, v in source_ctx.items() if v is not None}
            except Exception:
                pass
        # Ensure session_id is never None — Chat.session_id is typed as str
        # and Pydantic v2 refuses None even when a default_factory is set.
        resolved_session_id = session_id or str(uuid.uuid4())

        try:
            from app.models.chat import Chat

            # Auto-title: set only on the first doc for this session.
            # Subsequent saves (multi-turn) leave title=None so the first
            # doc's title remains the canonical conversation name.
            existing = await Chat.find_one({"session_id": resolved_session_id})
            title = ChatService._generate_title(user_message) if not existing else None

            chat_doc = Chat(
                user_id=user_id,
                session_id=resolved_session_id,
                title=title,
            )
            chat_doc.add_message(role="user", content=user_message)
            chat_doc.add_message(
                role="assistant",
                content=assistant_response,
                model_used=target_model,
                latency_ms=latency_ms,
                rag_sources=rag_sources,
                source_ctx=source_ctx,
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
                    source_ctx=source_ctx,
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
