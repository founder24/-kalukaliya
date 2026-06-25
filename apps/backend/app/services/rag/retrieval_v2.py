"""
RAG Retrieval Pipeline v2 — Cloudflare Vectorize + MongoDB chunk hydration.

Three retrieval paths, tried in order:

  1. Fast path  — TopicMatcher in-memory cosine → direct MongoDB chapter fetch
                  (~5ms match + ~40ms fetch = ~45ms total)

  2. Vectorize  — CF Vectorize metadata-filtered top-K query → hydrate chunk
                  text from MongoDB `chunks` collection
                  (~100ms Vectorize + ~20ms Mongo = ~120ms total)

  3. Legacy     — Atlas $vectorSearch on old `rag_chunks` collection + in-memory
                  cosine fallback (preserved for backward compat during migration)

All paths return the same chunk dict:
  { id, title, content, score, source_type, language, medium, source }

The caller (chat_service) consumes whichever list is non-empty first.
"""

from __future__ import annotations

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

_MEDIUM_MAP = {"en": "english", "as": "assamese"}


def _lang_to_medium(lang: str) -> str:
    return _MEDIUM_MAP.get(lang, lang)


async def retrieve_v2(
    query: str,
    lang: str = "en",
    filters: Optional[dict] = None,
    limit: int = MAX_CHUNKS,
) -> tuple[list[dict], str]:
    """
    Main retrieval entry point (v2).

    Args:
        query: User's question text.
        lang: 'en' or 'as'.
        filters: Optional narrowing hints:
                   { subject_id, chapter_id, topic_id, source_type }
        limit: Max chunks to return.

    Returns:
        (chunks, path_used)
        path_used ∈ {'fast', 'vectorize', 'legacy_atlas', 'legacy_inmem', 'empty'}
    """
    t0 = time.time()
    filters = filters or {}

    try:
        from app.services.ai.embedder import generate_embedding_vector
        query_embedding = await asyncio.wait_for(
            generate_embedding_vector(query), timeout=5.0
        )
    except asyncio.TimeoutError:
        logger.warning("retrieve_v2: embedding timed out (5s)")
        return [], "empty"
    except Exception as e:
        logger.error(f"retrieve_v2: embedding failed: {e}")
        return [], "empty"

    fast_chunks = await _fast_path(query_embedding, lang=lang, limit=limit)
    if fast_chunks:
        _log(t0, "fast", lang, len(fast_chunks))
        return fast_chunks, "fast"

    if _vectorize_available():
        vz_chunks = await _vectorize_path(
            query_embedding, lang=lang, filters=filters, limit=limit
        )
        if vz_chunks:
            _log(t0, "vectorize", lang, len(vz_chunks))
            return vz_chunks, "vectorize"

    legacy_chunks, legacy_path = await _legacy_path(
        query_embedding, lang=lang, filters=filters, limit=limit
    )
    _log(t0, legacy_path, lang, len(legacy_chunks))
    return legacy_chunks, legacy_path


def _vectorize_available() -> bool:
    token = (
        settings.CF_VECTORIZE_API_TOKEN
        or settings.CF_WORKER_AI_TOKEN
        or settings.CF_API_TOKEN
    )
    account = settings.CF_ACCOUNT_ID or settings.CLOUDFLARE_ACCOUNT_ID
    return bool(token and account)


def _log(t0: float, path: str, lang: str, n: int) -> None:
    ms = int((time.time() - t0) * 1000)
    logger.info(f"retrieval_v2 path={path} lang={lang} chunks={n} latency_ms={ms}")


async def _fast_path(
    query_embedding: list[float], lang: str, limit: int
) -> list[dict]:
    """TopicMatcher cosine match → direct chapter content fetch from MongoDB."""
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
        raw_chunks = chunk_content(cleaned, source_type="notes")[:CHUNKS_PER_CHAPTER]
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
                "medium": _lang_to_medium(lang),
                "url": f"/{chapter.slug}",
                "source": "fast_path",
            }
            for c in raw_chunks
        ][:limit]

    except Exception as e:
        logger.warning(f"_fast_path error: {e}")
        return []


async def _vectorize_path(
    query_embedding: list[float],
    lang: str,
    filters: dict,
    limit: int,
) -> list[dict]:
    """
    Query CF Vectorize → hydrate chunk text from MongoDB `chunks` collection.

    Metadata filter maps lang → medium ('en' → 'english', 'as' → 'assamese')
    because CF Vectorize stores 'medium' not 'language'.
    """
    try:
        from app.services.vectorize.client import vectorize_client

        cf_filter: dict = {"medium": {"$eq": _lang_to_medium(lang)}}
        if filters.get("subject_id"):
            cf_filter["subjectId"] = {"$eq": filters["subject_id"]}
        if filters.get("chapter_id"):
            cf_filter["chapterId"] = {"$eq": filters["chapter_id"]}
        if filters.get("topic_id"):
            cf_filter["topicId"] = {"$eq": filters["topic_id"]}
        if filters.get("source_type"):
            cf_filter["sourceType"] = {"$eq": filters["source_type"]}

        matches = await asyncio.wait_for(
            vectorize_client.query(
                vector=query_embedding,
                top_k=min(limit * 2, 20),
                filter=cf_filter,
                return_metadata=True,
            ),
            timeout=8.0,
        )

        if not matches:
            return []

        above_threshold = [m for m in matches if m.get("score", 0) >= SIMILARITY_THRESHOLD]
        if not above_threshold:
            return []

        chunk_ids = [m["id"] for m in above_threshold[:limit]]
        id_to_score = {m["id"]: m.get("score", 0.0) for m in above_threshold}

        chunks = await _hydrate_chunks(chunk_ids)

        return [
            {
                "id": c["_id"],
                "title": c.get("chapter_id", ""),
                "content": c["chunk_text"],
                "score": round(id_to_score.get(str(c["_id"]), 0.0), 4),
                "reranker_score": round(id_to_score.get(str(c["_id"]), 0.0), 4),
                "source_type": c.get("source_type", ""),
                "language": lang,
                "medium": c.get("medium", _lang_to_medium(lang)),
                "source": "vectorize",
                "subject_id": c.get("subject_id"),
                "chapter_id": c.get("chapter_id"),
                "topic_id": c.get("topic_id"),
            }
            for c in chunks
            if c.get("chunk_text")
        ][:limit]

    except asyncio.TimeoutError:
        logger.warning("_vectorize_path: Vectorize query timed out (8s)")
        return []
    except Exception as e:
        logger.warning(f"_vectorize_path error: {e}")
        return []


async def _hydrate_chunks(chunk_ids: list[str]) -> list[dict]:
    """Fetch chunk documents from MongoDB `chunks` collection by ID."""
    if not chunk_ids:
        return []
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings as _s
        col = get_mongo_client()[_s.MONGODB_DB_NAME]["chunks"]
        docs = await col.find(
            {"_id": {"$in": chunk_ids}},
            {
                "_id": 1, "chunk_text": 1, "source_type": 1,
                "medium": 1, "subject_id": 1, "chapter_id": 1,
                "topic_id": 1, "page_start": 1, "page_end": 1,
            },
        ).to_list(length=len(chunk_ids))
        id_map = {str(d["_id"]): d for d in docs}
        return [id_map[cid] for cid in chunk_ids if cid in id_map]
    except Exception as e:
        logger.warning(f"_hydrate_chunks error: {e}")
        return []


async def _legacy_path(
    query_embedding: list[float],
    lang: str,
    filters: dict,
    limit: int,
) -> tuple[list[dict], str]:
    """
    Legacy v1 paths: Atlas $vectorSearch on rag_chunks → in-memory cosine fallback.
    Preserved for backward compatibility while the v2 pipeline is being populated.
    """
    try:
        from app.services.rag.retrieval import _atlas_vector_search, _vector_path
        atlas_chunks = await _atlas_vector_search(
            query_embedding, lang=lang, filters=filters, limit=limit
        )
        if atlas_chunks:
            return atlas_chunks, "legacy_atlas"

        from app.services.search.mongo_vector_search import mongo_vector_search
        chunks = await mongo_vector_search.search_with_embedding(
            query_embedding, lang=lang, limit=limit
        )
        filtered = [c for c in chunks if c.get("score", 0) >= SIMILARITY_THRESHOLD]
        return filtered, ("legacy_inmem" if filtered else "empty")
    except Exception as e:
        logger.warning(f"_legacy_path error: {e}")
        return [], "empty"
