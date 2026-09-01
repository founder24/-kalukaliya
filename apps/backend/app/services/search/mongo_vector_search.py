"""
MongoVectorSearchService — RAG retrieval using MongoDB + in-memory cosine similarity.

Uses pre-computed topic embeddings stored in the `topic_embeddings` collection:

  1. Generate a 1024-dim query embedding via CF Workers AI bge-m3.
  2. Vectorised cosine similarity against all topic embeddings (numpy, in-memory).
  3. Collect top-K topics above the similarity threshold.
  4. Fetch chapter content for each matched topic from the `chapters` collection.
  5. Return chunks in the same format as the retrieval pipeline.

Latency profile (warm cache):
  embedding     ~100-200 ms  (CF bge-m3 REST API, global CF network)
  cosine match  < 5 ms       (numpy vectorised, ~N embeddings in memory)
  chapter fetch ~20-60 ms    (Motor async, Atlas connection pool)
  ──────────────────────────────────────────────────────────────────
  total         ~130-270 ms  (vs Vertex Search 800-3 000 ms)

Note: the embedding call is the dominant cost.  When the caller already has
a query_embedding (e.g. from check_topic_match), it can pass it directly via
`search_with_embedding()` to skip the extra embedding round-trip.
"""

import asyncio
import logging
import time
from typing import Optional

import numpy as np
import sentry_sdk

from app.config import settings

logger = logging.getLogger(__name__)

# Retry configuration for transient Vertex AI embedding errors
_EMBED_MAX_ATTEMPTS = 3
_EMBED_BACKOFF_DELAYS = [1.0, 2.0, 4.0]  # seconds between attempts

# Similarity threshold — same as topic_matcher.MATCH_THRESHOLD
MATCH_THRESHOLD = 0.65

# How many matching topics to retrieve (top-K before chapter dedup)
TOP_K = 8

# Max chunks per chapter to include
CHUNKS_PER_CHAPTER = 3

# Max chunks returned overall
MAX_CHUNKS = 5


class MongoVectorSearchService:
    """
    RAG retrieval using MongoDB topic embeddings + chapter content.

    Drop-in alternative to VertexSearchService.search_context().
    """

    def is_available(self) -> bool:
        """Returns True when MongoDB is initialised and embeddings can be fetched."""
        try:
            from app.models.content import TopicEmbedding  # noqa: F401
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def search_context(
        self,
        query: str,
        lang: str = "en",
        limit: int = MAX_CHUNKS,
    ) -> tuple[list[dict], float]:
        """
        Full pipeline: embed query → match topics → fetch chapters → chunks.

        Returns (chunks, latency_ms).
        """
        t0 = time.time()

        from app.services.ai.embedder import generate_embedding_vector

        query_embedding: list[float] | None = None
        last_exc: Exception | None = None

        for attempt in range(1, _EMBED_MAX_ATTEMPTS + 1):
            try:
                query_embedding = await asyncio.wait_for(
                    generate_embedding_vector(query), timeout=5.0
                )
                break
            except (ValueError, RuntimeError) as exc:
                # ValueError = empty text; config RuntimeError = not transient
                if isinstance(exc, ValueError) or "not configured" in str(exc):
                    logger.error(
                        "mongo_vector_embedding_non_retryable",
                        extra={"error_class": type(exc).__name__},
                    )
                    return [], 0.0
                last_exc = exc
            except Exception as exc:
                last_exc = exc

            if attempt < _EMBED_MAX_ATTEMPTS:
                delay = _EMBED_BACKOFF_DELAYS[attempt - 1]
                error_class = type(last_exc).__name__ if last_exc else "UnknownError"
                sentry_sdk.add_breadcrumb(
                    category="search",
                    message="MongoVectorSearch embedding attempt failed; retrying",
                    level="warning",
                    data={
                        "attempt": attempt,
                        "max_attempts": _EMBED_MAX_ATTEMPTS,
                        "retry_delay_seconds": delay,
                        "error_class": error_class,
                    },
                )
                logger.warning(
                    "mongo_vector_embedding_retry",
                    extra={
                        "attempt": attempt,
                        "max_attempts": _EMBED_MAX_ATTEMPTS,
                        "retry_delay_seconds": delay,
                        "error_class": error_class,
                    },
                )
                await asyncio.sleep(delay)

        if query_embedding is None:
            error_class = type(last_exc).__name__ if last_exc else "UnknownError"
            sentry_sdk.add_breadcrumb(
                category="search",
                message="MongoVectorSearch embedding failed after retries",
                level="error",
                data={
                    "attempts": _EMBED_MAX_ATTEMPTS,
                    "error_class": error_class,
                },
            )
            logger.error(
                "mongo_vector_embedding_failed",
                extra={
                    "attempts": _EMBED_MAX_ATTEMPTS,
                    "error_class": error_class,
                },
            )
            return [], 0.0

        chunks = await self.search_with_embedding(query_embedding, lang=lang, limit=limit)
        latency_ms = (time.time() - t0) * 1000
        return chunks, latency_ms

    async def search_with_embedding(
        self,
        query_embedding: list[float],
        lang: str = "en",
        limit: int = MAX_CHUNKS,
    ) -> list[dict]:
        """
        Match topics using a pre-computed embedding, then fetch chapter content.

        Skips the embedding network call — useful when the caller already has
        the embedding (e.g. topic_match was just run in the chat pipeline).
        """
        matched_topics = await self._match_top_k(query_embedding, k=TOP_K)
        if not matched_topics:
            return []

        chunks = await self._fetch_chapter_chunks(matched_topics, lang=lang, limit=limit)
        return chunks

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _match_top_k(
        self, query_embedding: list[float], k: int
    ) -> list[dict]:
        """
        Return top-k topics above MATCH_THRESHOLD sorted by descending similarity.
        Uses the TopicMatcher's in-memory cache (loaded once from MongoDB).
        """
        try:
            from app.services.ai.topic_matcher import topic_matcher

            # Ensure cache is warm
            if not topic_matcher._is_cache_valid():
                async with topic_matcher._load_lock:
                    if not topic_matcher._is_cache_valid():
                        await topic_matcher._load_embeddings()

            if (
                not topic_matcher._embeddings
                or topic_matcher._vectors is None
                or topic_matcher._vectors.size == 0
            ):
                return []

            q = np.array(query_embedding, dtype=np.float32)
            q_norm = np.linalg.norm(q)
            if q_norm == 0:
                return []

            vecs = topic_matcher._vectors
            norms = np.linalg.norm(vecs, axis=1)
            valid = norms > 0
            sims = np.zeros(len(topic_matcher._embeddings), dtype=np.float32)
            if valid.any():
                sims[valid] = np.dot(vecs[valid], q) / (norms[valid] * q_norm)

            # Top-k indices above threshold
            above = np.where(sims >= MATCH_THRESHOLD)[0]
            if len(above) == 0:
                return []

            top_idx = above[np.argsort(sims[above])[::-1][:k]]
            results = []
            seen_chapters: set[str] = set()
            for idx in top_idx:
                meta = dict(topic_matcher._embeddings[idx])
                meta["score"] = float(sims[idx])
                cid = meta.get("chapter_id", "")
                if cid not in seen_chapters:
                    seen_chapters.add(cid)
                    results.append(meta)
            return results

        except Exception as e:
            logger.error(
                "mongo_vector_match_failed",
                extra={"error_class": type(e).__name__},
            )
            return []

    async def _fetch_chapter_chunks(
        self, topics: list[dict], lang: str, limit: int
    ) -> list[dict]:
        """
        For each matched topic, fetch its chapter from MongoDB and chunk the content.
        Returns up to `limit` chunks total.
        """
        from app.models.content import Chapter
        from beanie import PydanticObjectId
        from app.services.content.search_indexer import search_indexer

        chunks: list[dict] = []
        seen_chapters: set[str] = set()

        async def _fetch_one(topic: dict) -> list[dict]:
            chapter_id = topic.get("chapter_id", "")
            if not chapter_id or chapter_id in seen_chapters:
                return []

            try:
                chapter = None
                try:
                    chapter = await Chapter.get(PydanticObjectId(chapter_id))
                except Exception:
                    chapter = await Chapter.find_one({"_id": chapter_id})

                if not chapter:
                    return []

                content = (
                    chapter.content_as
                    if lang == "as" and chapter.content_as
                    else chapter.content_en
                )
                if not content:
                    return []

                raw_chunks = search_indexer.chunk_text(content, chunk_size=500)
                score = round(topic.get("score", 0.75), 3)
                return [
                    {
                        "id": f"{chapter.slug}_chunk_{i}",
                        "title": chapter.title,
                        "content": chunk,
                        "score": score,
                        "reranker_score": score,
                        "url": f"/{chapter.slug}",
                        "topic": topic.get("topic_title", ""),
                        "source": "mongodb_vector",
                    }
                    for i, chunk in enumerate(raw_chunks[:CHUNKS_PER_CHAPTER])
                ]
            except Exception as e:
                logger.warning(
                    "mongo_vector_chapter_fetch_failed",
                    extra={"error_class": type(e).__name__},
                )
                return []

        # Fetch chapters concurrently but deduplicate eagerly
        tasks = [_fetch_one(t) for t in topics]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for r in results:
            if isinstance(r, list):
                for chunk in r:
                    if len(chunks) >= limit:
                        break
                    chunks.append(chunk)
                if len(chunks) >= limit:
                    break

        return chunks[:limit]


# Singleton
mongo_vector_search = MongoVectorSearchService()
