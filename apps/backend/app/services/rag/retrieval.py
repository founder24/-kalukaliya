"""
RAG Retrieval Pipeline

Two retrieval paths, used in order of preference:

  1. Fast path  — topic_matcher hits a cached topic by cosine similarity →
                  fetch chapter content directly from MongoDB (~20-60ms total)

  2. Vector path — embed query via CF bge-m3 → Atlas $vectorSearch on rag_chunks
                  with metadata pre-filter (~150-300ms total, more precise)

Both paths return the same chunk dict format:
  { id, title, content, score, source_type, language, source }

The caller (chat_service) picks whichever path succeeds first.
"""

import asyncio
import logging
import time
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 0.60
TOP_K = 8
MAX_CHUNKS = 5
CHUNKS_PER_CHAPTER = 3


async def retrieve(
    query: str,
    lang: str = "en",
    filters: Optional[dict] = None,
    limit: int = MAX_CHUNKS,
) -> tuple[list[dict], str]:
    """
    Main retrieval entry point used by chat_service.

    Tries fast path first (topic_matcher → direct chapter fetch).
    Falls back to full vector search on rag_chunks if fast path misses.

    Args:
        query: User's question (cleaned but not embedded yet).
        lang: 'en' or 'as' — selects content_en vs content_as in fast path.
        filters: Optional pre-filter dict for vector search:
                   { subject_id, chapter_id, source_type, board, class_level }
        limit: Max chunks to return.

    Returns:
        (chunks, path_used) where path_used is 'fast' | 'vector' | 'empty'.
    """
    t0 = time.time()
    filters = filters or {}

    from app.services.ai.embedder import generate_embedding_vector

    try:
        query_embedding = await asyncio.wait_for(
            generate_embedding_vector(query), timeout=5.0
        )
    except asyncio.TimeoutError:
        logger.warning("RAG retrieval: embedding timed out (5s), returning empty")
        return [], "empty"
    except Exception as e:
        logger.error(f"RAG retrieval: embedding failed: {e}")
        return [], "empty"

    fast_chunks = await _fast_path(query_embedding, lang=lang, limit=limit)
    if fast_chunks:
        latency_ms = int((time.time() - t0) * 1000)
        logger.info(
            f"retrieval=fast lang={lang} chunks={len(fast_chunks)} "
            f"latency_ms={latency_ms}"
        )
        return fast_chunks, "fast"

    vector_chunks = await _vector_path(
        query_embedding, lang=lang, filters=filters, limit=limit
    )
    latency_ms = int((time.time() - t0) * 1000)
    path = "vector" if vector_chunks else "empty"
    logger.info(
        f"retrieval={path} lang={lang} chunks={len(vector_chunks)} "
        f"latency_ms={latency_ms}"
    )
    return vector_chunks, path


async def _fast_path(
    query_embedding: list[float], lang: str, limit: int
) -> list[dict]:
    """
    Fast path: topic_matcher cosine match → direct MongoDB chapter fetch.

    Uses the in-memory TopicEmbedding cache (populated from topic_embeddings
    collection). If a topic matches above MATCH_THRESHOLD, its chapter content
    is fetched directly — no Atlas $vectorSearch needed.
    """
    try:
        from app.services.ai.topic_matcher import topic_matcher

        match = await topic_matcher.match_topic(query_embedding)
        if not match:
            return []

        chapter_id = match.get("chapter_id")
        if not chapter_id:
            return []

        from app.models.content import Chapter
        from beanie import PydanticObjectId
        from app.services.content.search_indexer import search_indexer

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

        from app.services.rag.cleaner import clean_text
        from app.services.rag.chunker import chunk_content

        cleaned = clean_text(content)
        chunks = chunk_content(cleaned, source_type="notes")[:CHUNKS_PER_CHAPTER]
        score = round(match.get("score", 0.80), 3)

        return [
            {
                "id": f"{chapter.slug}_fast_{c['chunk_index']}",
                "title": chapter.title,
                "content": c["text"],
                "score": score,
                "reranker_score": score,
                "source_type": "notes",
                "language": lang,
                "url": f"/{chapter.slug}",
                "source": "fast_path",
            }
            for c in chunks
        ][:limit]

    except Exception as e:
        logger.warning(f"_fast_path error: {e}")
        return []


async def _vector_path(
    query_embedding: list[float],
    lang: str,
    filters: dict,
    limit: int,
) -> list[dict]:
    """
    Full vector path: Atlas $vectorSearch on rag_chunks with metadata pre-filter.

    Falls back to in-memory cosine search on topic_embeddings when rag_chunks
    collection is empty (pre-ingest state), so the system works before the
    full ingestion pipeline has been run.
    """
    atlas_chunks = await _atlas_vector_search(
        query_embedding, lang=lang, filters=filters, limit=limit
    )
    if atlas_chunks:
        return atlas_chunks

    from app.services.search.mongo_vector_search import mongo_vector_search
    chunks = await mongo_vector_search.search_with_embedding(
        query_embedding, lang=lang, limit=limit
    )
    return [c for c in chunks if c.get("score", 0) >= SIMILARITY_THRESHOLD]


async def _atlas_vector_search(
    query_embedding: list[float],
    lang: str,
    filters: dict,
    limit: int,
) -> list[dict]:
    """
    Run $vectorSearch aggregation on the rag_chunks collection.

    Requires an Atlas Vector Search index named 'rag_chunks_vector' with:
      - path: embedding (1024 dims, cosine similarity)
      - filter fields: language, source_type, subject_id, chapter_id, board, class_level

    Atlas index definition (create via Atlas UI or API):
    {
      "fields": [
        { "type": "vector", "path": "embedding",
          "numDimensions": 1024, "similarity": "cosine" },
        { "type": "filter", "path": "language" },
        { "type": "filter", "path": "source_type" },
        { "type": "filter", "path": "subject_id" },
        { "type": "filter", "path": "chapter_id" },
        { "type": "filter", "path": "board" },
        { "type": "filter", "path": "class_level" }
      ]
    }
    """
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings as _settings
        client = get_mongo_client()
        collection = client[_settings.MONGODB_DB_NAME]["rag_chunks"]

        pre_filter: dict = {"language": lang}
        for field in ("subject_id", "chapter_id", "source_type", "board", "class_level"):
            if filters.get(field):
                pre_filter[field] = filters[field]

        pipeline = [
            {
                "$vectorSearch": {
                    "index": "rag_chunks_vector",
                    "path": "embedding",
                    "queryVector": query_embedding,
                    "numCandidates": limit * 10,
                    "limit": limit,
                    "filter": pre_filter,
                }
            },
            {
                "$project": {
                    "_id": 1,
                    "content": 1,
                    "chunk_index": 1,
                    "source_type": 1,
                    "language": 1,
                    "chapter_id": 1,
                    "chapter_slug": 1,
                    "score": {"$meta": "vectorSearchScore"},
                }
            },
        ]

        docs = await collection.aggregate(pipeline).to_list(length=limit)
        return [
            {
                "id": str(doc["_id"]),
                "title": doc.get("chapter_slug", ""),
                "content": doc["content"],
                "score": round(doc.get("score", 0.0), 4),
                "reranker_score": round(doc.get("score", 0.0), 4),
                "source_type": doc.get("source_type", "notes"),
                "language": doc.get("language", lang),
                "url": f"/{doc.get('chapter_slug', '')}",
                "source": "atlas_vector",
            }
            for doc in docs
            if doc.get("score", 0) >= SIMILARITY_THRESHOLD
        ]

    except Exception as e:
        if "index" in str(e).lower() or "vectorSearch" in str(e):
            logger.debug(
                "Atlas Vector Search index 'rag_chunks_vector' not yet created — "
                "falling back to in-memory cosine search. "
                "Create the index via Atlas UI to enable Atlas Vector Search."
            )
        else:
            logger.warning(f"_atlas_vector_search error: {e}")
        return []
