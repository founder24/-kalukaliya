"""
TopicMatcher - In-memory cosine similarity matching against pre-computed topic embeddings.

Loads all TopicEmbedding documents from MongoDB on first access, caches them in memory,
and provides fast cosine similarity lookup for incoming user queries.
"""

import asyncio
import logging
import time
from typing import Optional

import numpy as np

from app.models.content import TopicEmbedding

logger = logging.getLogger(__name__)

# Similarity threshold: only return matches at or above this score
MATCH_THRESHOLD = 0.70

# Cache TTL in seconds (5 minutes)
_CACHE_TTL = 300

# Shorter TTL when a load fails (10 seconds), so we retry quickly
_ERROR_CACHE_TTL = 10


def cosine_similarity(vec_a: list[float], vec_b: np.ndarray) -> float:
    """Compute cosine similarity between a query vector and a pre-normalized vector."""
    a = np.array(vec_a, dtype=np.float32)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(vec_b)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, vec_b) / (norm_a * norm_b))


class TopicMatcher:
    """Loads topic embeddings from MongoDB and matches queries by cosine similarity."""

    def __init__(self):
        self._embeddings: Optional[list[dict]] = None
        self._vectors: Optional[np.ndarray] = None
        self._last_load: float = 0
        self._load_lock = asyncio.Lock()
        self._load_failed: bool = False

    def _is_cache_valid(self) -> bool:
        """Check if the in-memory cache is still fresh."""
        if self._embeddings is None:
            return False
        ttl = _ERROR_CACHE_TTL if self._load_failed else _CACHE_TTL
        return (time.time() - self._last_load) < ttl

    async def _load_embeddings(self) -> None:
        """Load all TopicEmbedding documents from MongoDB into memory."""
        try:
            docs = await TopicEmbedding.find_all().to_list()
        except Exception as e:
            logger.error(f"Failed to load topic embeddings from MongoDB: {e}")
            # Issue #6: Use short TTL on error so we retry quickly
            self._embeddings = []
            self._vectors = np.array([], dtype=np.float32)
            self._last_load = time.time()
            self._load_failed = True
            return

        self._embeddings = []
        vectors = []

        for doc in docs:
            if not doc.embedding:
                continue
            self._embeddings.append({
                "topic_id": doc.topic_id,
                "topic_title": doc.topic_title,
                "chapter_id": str(doc.chapter_id),
                "chapter_title": doc.chapter_title,
                "subject_slug": doc.subject_slug,
                "board_slug": doc.board_slug,
                "class_level": doc.class_level,
            })
            vectors.append(doc.embedding)

        if vectors:
            self._vectors = np.array(vectors, dtype=np.float32)
        else:
            self._vectors = np.array([], dtype=np.float32)

        self._last_load = time.time()
        self._load_failed = False
        logger.info(f"Loaded {len(self._embeddings)} topic embeddings into cache")

    def invalidate_cache(self) -> None:
        """Force reload on next match call."""
        self._embeddings = None
        self._vectors = None
        self._last_load = 0
        self._load_failed = False

    async def match_topic(self, query_embedding: list[float]) -> Optional[dict]:
        """
        Find the best matching topic for a query embedding.

        Args:
            query_embedding: 768-dim embedding vector for the user query.

        Returns:
            Dict with topic metadata and score if best match >= threshold, else None.
        """
        if not self._is_cache_valid():
            # Issue #4: Use lock to prevent concurrent cache reloads
            async with self._load_lock:
                # Double-check after acquiring lock
                if not self._is_cache_valid():
                    await self._load_embeddings()

        if not self._embeddings or self._vectors is None or self._vectors.size == 0:
            return None

        # Vectorized cosine similarity against all stored embeddings
        query_vec = np.array(query_embedding, dtype=np.float32)
        query_norm = np.linalg.norm(query_vec)
        if query_norm == 0:
            return None

        # Compute dot products with all vectors at once
        norms = np.linalg.norm(self._vectors, axis=1)
        # Avoid division by zero
        valid_mask = norms > 0
        similarities = np.zeros(len(self._embeddings), dtype=np.float32)
        if valid_mask.any():
            similarities[valid_mask] = (
                np.dot(self._vectors[valid_mask], query_vec)
                / (norms[valid_mask] * query_norm)
            )

        best_idx = int(np.argmax(similarities))
        best_score = float(similarities[best_idx])

        if best_score >= MATCH_THRESHOLD:
            result = dict(self._embeddings[best_idx])
            result["score"] = best_score
            return result

        return None


# Singleton instance
topic_matcher = TopicMatcher()
