"""
RAG Ingestion Pipeline

Full pipeline: source document → clean → chunk → embed → upsert to MongoDB.

Designed to run:
  - On-demand via admin API endpoint (single chapter)
  - Batch via cron/CLI (full corpus re-index)

Metadata stored per chunk (used for Atlas Vector Search pre-filtering):
  board, class_level, stream, subject_id, chapter_id, topic_id,
  source_type, language, year (PYQ only), marks (PYQ only)

Usage:
    from app.services.rag.ingestion import ingest_chapter

    result = await ingest_chapter(
        chapter_id="c42",
        content_en="...",
        content_as="...",
        metadata={
            "board": "ahsec",
            "class_level": "12",
            "stream": "science",
            "subject_id": "s13",
            "chapter_id": "c42",
            "source_type": "notes",
        }
    )
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.services.rag.cleaner import clean_text, detect_language
from app.services.rag.chunker import chunk_content
from app.services.ai.embedder import embed_batch_chunked

logger = logging.getLogger(__name__)

_EMBED_BATCH_SIZE = 50


def _build_chunk_doc(
    chunk: dict,
    metadata: dict,
    language: str,
    embedding: list[float],
) -> dict:
    """Build a MongoDB document for a single RAG chunk."""
    now = datetime.now(timezone.utc)
    return {
        "_id": str(uuid.uuid4()),
        "content": chunk["text"],
        "token_count": chunk["token_count"],
        "chunk_index": chunk["chunk_index"],
        "embedding": embedding,
        "embedding_model": "cf/baai/bge-m3",
        "embedding_dim": 1024,
        "language": language,
        # Metadata for pre-filtering
        "board": metadata.get("board", ""),
        "class_level": metadata.get("class_level", ""),
        "stream": metadata.get("stream", ""),
        "subject_id": metadata.get("subject_id", ""),
        "chapter_id": metadata.get("chapter_id", ""),
        "chapter_slug": metadata.get("chapter_slug", ""),
        "topic_id": metadata.get("topic_id", ""),
        "source_type": metadata.get("source_type", "notes"),
        "year": metadata.get("year"),
        "marks": metadata.get("marks"),
        "created_at": now,
        "updated_at": now,
    }


async def ingest_text(
    text: str,
    metadata: dict,
    language: Optional[str] = None,
    source_type: Optional[str] = None,
    dry_run: bool = False,
) -> dict:
    """
    Ingest a single text block: clean → chunk → embed → upsert.

    Args:
        text: Raw content (markdown OK).
        metadata: Dict with board, class_level, subject_id, chapter_id, source_type, etc.
        language: 'en' or 'as' — auto-detected if not provided.
        source_type: Override chunking strategy; falls back to metadata['source_type'].
        dry_run: If True, skip MongoDB upsert and return chunks+embeddings only.

    Returns:
        Dict with keys: chunks_total, chunks_embedded, upserted, errors.
    """
    src_type = source_type or metadata.get("source_type", "notes")
    cleaned = clean_text(text)
    if not cleaned:
        return {"chunks_total": 0, "chunks_embedded": 0, "upserted": 0, "errors": []}

    lang = language or detect_language(cleaned)
    chunks = chunk_content(cleaned, source_type=src_type)
    if not chunks:
        return {"chunks_total": 0, "chunks_embedded": 0, "upserted": 0, "errors": []}

    texts = [c["text"] for c in chunks]
    errors: list[str] = []
    embeddings: list[list[float]] = []

    try:
        embeddings = await embed_batch_chunked(texts, batch_size=_EMBED_BATCH_SIZE)
    except Exception as e:
        logger.error(f"Embedding failed for chapter_id={metadata.get('chapter_id')}: {e}")
        return {
            "chunks_total": len(chunks),
            "chunks_embedded": 0,
            "upserted": 0,
            "errors": [str(e)],
        }

    docs = [
        _build_chunk_doc(chunk, {**metadata, "source_type": src_type}, lang, emb)
        for chunk, emb in zip(chunks, embeddings)
    ]

    if dry_run:
        return {
            "chunks_total": len(chunks),
            "chunks_embedded": len(embeddings),
            "upserted": 0,
            "errors": errors,
            "dry_run_docs": docs,
        }

    upserted = await _upsert_chunks(docs, chapter_id=metadata.get("chapter_id", ""))
    return {
        "chunks_total": len(chunks),
        "chunks_embedded": len(embeddings),
        "upserted": upserted,
        "errors": errors,
    }


async def ingest_chapter(
    chapter_id: str,
    content_en: Optional[str] = None,
    content_as: Optional[str] = None,
    metadata: Optional[dict] = None,
    source_type: str = "notes",
    dry_run: bool = False,
) -> dict:
    """
    Ingest both English and Assamese content for a chapter.

    Runs EN and AS ingestion concurrently.
    Deletes existing chunks for this chapter_id before upserting (re-index).

    Args:
        chapter_id: The chapter's ID (used to delete stale chunks before re-index).
        content_en: English content (markdown OK).
        content_as: Assamese content (markdown OK).
        metadata: Base metadata dict; chapter_id will be injected automatically.
        source_type: Chunking strategy for both languages.
        dry_run: Skip MongoDB upsert.

    Returns:
        Dict with 'en' and 'as' ingestion results.
    """
    meta = {**(metadata or {}), "chapter_id": chapter_id}

    if not dry_run:
        await _delete_existing_chunks(chapter_id)

    async def _noop() -> dict:
        return {"chunks_total": 0, "chunks_embedded": 0, "upserted": 0, "errors": []}

    tasks = [
        ingest_text(content_en, meta, language="en", source_type=source_type, dry_run=dry_run)
        if content_en else _noop(),
        ingest_text(content_as, meta, language="as", source_type=source_type, dry_run=dry_run)
        if content_as else _noop(),
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    def _safe(r):
        if isinstance(r, Exception):
            return {"chunks_total": 0, "chunks_embedded": 0, "upserted": 0, "errors": [str(r)]}
        return r

    return {"en": _safe(results[0]), "as": _safe(results[1])}


def _get_rag_collection():
    """Return the rag_chunks Motor collection."""
    from app.db.mongo import get_mongo_client
    from app.config import settings as _settings
    client = get_mongo_client()
    return client[_settings.MONGODB_DB_NAME]["rag_chunks"]


async def _delete_existing_chunks(chapter_id: str) -> int:
    """Delete all RAG chunks for a chapter before re-indexing."""
    try:
        collection = _get_rag_collection()
        result = await collection.delete_many({"chapter_id": chapter_id})
        deleted = result.deleted_count
        if deleted:
            logger.info(f"Deleted {deleted} stale chunks for chapter_id={chapter_id}")
        return deleted
    except Exception as e:
        logger.warning(f"Failed to delete stale chunks for {chapter_id}: {e}")
        return 0


async def _upsert_chunks(docs: list[dict], chapter_id: str) -> int:
    """Bulk insert chunk documents into the rag_chunks collection."""
    if not docs:
        return 0
    try:
        collection = _get_rag_collection()
        result = await collection.insert_many(docs, ordered=False)
        upserted = len(result.inserted_ids)
        logger.info(f"Upserted {upserted} chunks for chapter_id={chapter_id}")
        return upserted
    except Exception as e:
        logger.error(f"Chunk upsert failed for chapter_id={chapter_id}: {e}")
        return 0


async def rebuild_topic_embedding(
    topic_id: str,
    topic_title: str,
    chapter_id: str,
    chapter_title: str,
    subject_slug: str,
    board_slug: str,
    class_level: str,
) -> bool:
    """
    Re-embed a topic title using CF bge-m3 and upsert into topic_embeddings.

    This is what populates the TopicMatcher in-memory cache.
    Call this when a topic/chapter title changes or on first ingest.

    Returns True on success.
    """
    from app.services.ai.embedder import generate_embedding_vector
    from app.models.content import TopicEmbedding
    from datetime import datetime, timezone

    try:
        embedding = await generate_embedding_vector(topic_title)
    except Exception as e:
        logger.error(f"Failed to embed topic_title='{topic_title}': {e}")
        return False

    now = datetime.now(timezone.utc)
    try:
        existing = await TopicEmbedding.find_one({"topic_id": topic_id})
        if existing:
            await existing.update({
                "$set": {
                    "embedding": embedding,
                    "chapter_id": chapter_id,
                    "chapter_title": chapter_title,
                    "subject_slug": subject_slug,
                    "board_slug": board_slug,
                    "class_level": class_level,
                    "updated_at": now,
                }
            })
        else:
            doc = TopicEmbedding(
                topic_id=topic_id,
                topic_title=topic_title,
                chapter_id=chapter_id,
                chapter_title=chapter_title,
                subject_slug=subject_slug,
                board_slug=board_slug,
                class_level=class_level,
                embedding=embedding,
                created_at=now,
                updated_at=now,
            )
            await doc.insert()

        from app.services.ai.topic_matcher import topic_matcher
        topic_matcher.invalidate_cache()
        return True
    except Exception as e:
        logger.error(f"Failed to upsert TopicEmbedding for topic_id={topic_id}: {e}")
        return False
