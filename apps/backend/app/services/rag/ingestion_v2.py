"""
RAG Ingestion Pipeline v2 — dual-write to MongoDB + Cloudflare Vectorize.

Pipeline per document:
  1. Clean text (Unicode NFC, Bijoy→Unicode, boilerplate strip)
  2. Chunk (source-type-aware strategy)
  3. Embed in batches via CF Workers AI @cf/baai/bge-m3 (1024-dim)
  4. Write chunk metadata to MongoDB `chunks` collection
  5. Upsert embeddings + metadata to Cloudflare Vectorize
  6. Update RagDocument.status and GenerationJob.progress

The old `rag_chunks` collection (v1 pipeline) is kept untouched.
This pipeline writes to the new `chunks` + `rag_documents` collections only.

Usage:
    from app.services.rag.ingestion_v2 import ingest_document, ingest_document_text

    # From an existing RagDocument already saved to MongoDB:
    result = await ingest_document(document_id="doc_001")

    # One-shot (text + metadata, no RagDocument record needed):
    result = await ingest_document_text(
        text="...",
        medium="english",
        subject_id="subj_phy",
        chapter_id="chap_phy_01",
        source_type="book_pdf",
    )
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.services.rag.cleaner import clean_text, detect_language
from app.services.rag.chunker import chunk_content
from app.services.ai.embedder import embed_batch_chunked

logger = logging.getLogger(__name__)

_EMBED_BATCH = 50
_VECTORIZE_BATCH = 100


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _chunk_id(document_id: str, idx: int) -> str:
    """Stable, unique ID for a chunk: used as MongoDB _id AND Vectorize vector ID."""
    return f"{document_id}_c{idx:04d}"


def _vectorize_metadata(
    chunk_doc: dict,
) -> dict:
    """
    Build the CF Vectorize metadata dict for a chunk.

    Only include fields that have a CF metadata index — non-indexed fields are
    silently ignored by Vectorize but waste payload space.

    Indexed fields (created via wrangler vectorize create-metadata-index):
      subjectId, chapterId, topicId, medium, sourceType, chunkType
    """
    meta: dict = {
        "subjectId": chunk_doc.get("subject_id", ""),
        "medium": chunk_doc.get("medium", ""),
        "sourceType": chunk_doc.get("source_type", ""),
        "chunkType": chunk_doc.get("chunk_type", "topic_chunk"),
    }
    if chunk_doc.get("chapter_id"):
        meta["chapterId"] = chunk_doc["chapter_id"]
    if chunk_doc.get("topic_id"):
        meta["topicId"] = chunk_doc["topic_id"]
    if chunk_doc.get("page_start") is not None:
        meta["pageStart"] = chunk_doc["page_start"]
    if chunk_doc.get("page_end") is not None:
        meta["pageEnd"] = chunk_doc["page_end"]
    return meta


async def _upsert_to_mongo(chunk_docs: list[dict]) -> int:
    """Bulk-insert chunk docs into the `chunks` collection via Motor."""
    if not chunk_docs:
        return 0
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings as _s
        col = get_mongo_client()[_s.MONGODB_DB_NAME]["chunks"]
        result = await col.insert_many(chunk_docs, ordered=False)
        return len(result.inserted_ids)
    except Exception as e:
        logger.error(f"MongoDB chunk insert failed: {e}")
        return 0


async def _delete_existing_chunks(document_id: str) -> tuple[int, list[str]]:
    """
    Delete all chunks for a document from MongoDB and collect their vector IDs
    so the caller can purge them from Cloudflare Vectorize.

    Returns (deleted_count, vector_ids).
    """
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings as _s
        col = get_mongo_client()[_s.MONGODB_DB_NAME]["chunks"]
        docs = await col.find(
            {"document_id": document_id}, {"_id": 1, "vector_id": 1}
        ).to_list(length=None)
        if not docs:
            return 0, []
        ids = [str(d["_id"]) for d in docs]
        vector_ids = [d["vector_id"] for d in docs if d.get("vector_id")]
        result = await col.delete_many({"document_id": document_id})
        return result.deleted_count, vector_ids
    except Exception as e:
        logger.warning(f"Failed to delete existing chunks for doc={document_id}: {e}")
        return 0, []


async def _update_job(job_id: Optional[str], **fields) -> None:
    if not job_id:
        return
    try:
        from app.models.rag import GenerationJob
        job = await GenerationJob.get(job_id)
        if job:
            await job.update({"$set": {**fields, "updated_at": _now()}})
    except Exception:
        pass


async def ingest_document_text(
    text: str,
    medium: str,
    subject_id: str,
    source_type: str = "book_pdf",
    chapter_id: Optional[str] = None,
    topic_id: Optional[str] = None,
    page_start: Optional[int] = None,
    page_end: Optional[int] = None,
    document_id: Optional[str] = None,
    job_id: Optional[str] = None,
    dry_run: bool = False,
) -> dict:
    """
    Ingest a raw text block into MongoDB `chunks` + Cloudflare Vectorize.

    This is the atomic unit — one language, one source block.

    Returns:
        {
          chunks_total, chunks_embedded, mongo_inserted,
          vectorize_upserted, errors, chunk_ids
        }
    """
    document_id = document_id or str(uuid.uuid4())
    errors: list[str] = []

    cleaned = clean_text(text)
    if not cleaned:
        return {
            "chunks_total": 0, "chunks_embedded": 0,
            "mongo_inserted": 0, "vectorize_upserted": 0,
            "errors": [], "chunk_ids": [],
        }

    lang = detect_language(cleaned)
    chunks = chunk_content(cleaned, source_type=source_type)
    if not chunks:
        return {
            "chunks_total": 0, "chunks_embedded": 0,
            "mongo_inserted": 0, "vectorize_upserted": 0,
            "errors": [], "chunk_ids": [],
        }

    texts = [c["text"] for c in chunks]
    try:
        embeddings = await embed_batch_chunked(texts, batch_size=_EMBED_BATCH)
    except Exception as e:
        msg = f"Embedding failed: {e}"
        logger.error(msg)
        return {
            "chunks_total": len(chunks), "chunks_embedded": 0,
            "mongo_inserted": 0, "vectorize_upserted": 0,
            "errors": [msg], "chunk_ids": [],
        }

    now = _now()
    chunk_docs: list[dict] = []
    vector_records: list[dict] = []

    for idx, (chunk, emb) in enumerate(zip(chunks, embeddings)):
        cid = _chunk_id(document_id, idx)
        doc = {
            "_id": cid,
            "document_id": document_id,
            "subject_id": subject_id,
            "chapter_id": chapter_id,
            "topic_id": topic_id,
            "medium": medium,
            "source_type": source_type,
            "chunk_type": "topic_chunk",
            "chunk_text": chunk["text"],
            "chunk_index": chunk["chunk_index"],
            "token_count": chunk["token_count"],
            "page_start": page_start,
            "page_end": page_end,
            "vector_id": cid,
            "embedding_model": "cf/baai/bge-m3",
            "embedding_dim": 1024,
            "language": lang,
            "created_at": now,
            "updated_at": now,
        }
        chunk_docs.append(doc)
        vector_records.append({
            "id": cid,
            "values": emb,
            "metadata": _vectorize_metadata(doc),
        })

    if dry_run:
        return {
            "chunks_total": len(chunks),
            "chunks_embedded": len(embeddings),
            "mongo_inserted": 0,
            "vectorize_upserted": 0,
            "errors": errors,
            "chunk_ids": [d["_id"] for d in chunk_docs],
            "dry_run": True,
        }

    mongo_inserted = await _upsert_to_mongo(chunk_docs)

    vectorize_upserted = 0
    try:
        from app.services.vectorize.client import vectorize_client
        result = await vectorize_client.upsert(vector_records)
        vectorize_upserted = result.get("count", 0)
    except Exception as e:
        msg = f"Vectorize upsert failed (non-fatal): {e}"
        logger.warning(msg)
        errors.append(msg)

    await _update_job(
        job_id,
        processed_chunks=mongo_inserted,
        progress=min(100, int(mongo_inserted / max(len(chunks), 1) * 100)),
    )

    return {
        "chunks_total": len(chunks),
        "chunks_embedded": len(embeddings),
        "mongo_inserted": mongo_inserted,
        "vectorize_upserted": vectorize_upserted,
        "errors": errors,
        "chunk_ids": [d["_id"] for d in chunk_docs],
    }


async def ingest_document(
    document_id: str,
    content_override: Optional[str] = None,
    job_id: Optional[str] = None,
    dry_run: bool = False,
) -> dict:
    """
    Ingest a RagDocument that already exists in MongoDB.

    Looks up the RagDocument by ID, extracts text (from content_override or
    file_url — PDF extraction is a stub for now), and runs the full pipeline.

    For PDF extraction, set content_override with the pre-extracted text.

    Returns:
        {document_id, status, result, errors}
    """
    from app.models.rag import RagDocument

    try:
        doc = await RagDocument.get(document_id)
    except Exception as e:
        return {"document_id": document_id, "status": "error", "errors": [f"Document not found: {e}"]}

    if not doc:
        return {"document_id": document_id, "status": "error", "errors": ["Document not found"]}

    await _update_job(job_id, status="running", started_at=_now())
    await doc.update({"$set": {"status": "processing", "updated_at": _now()}})

    deleted, old_vector_ids = await _delete_existing_chunks(document_id)
    if old_vector_ids:
        try:
            from app.services.vectorize.client import vectorize_client
            await vectorize_client.delete(old_vector_ids)
            logger.info(f"Deleted {len(old_vector_ids)} stale Vectorize vectors for doc={document_id}")
        except Exception as e:
            logger.warning(f"Failed to delete stale Vectorize vectors: {e}")

    text = content_override
    if not text:
        if doc.file_url:
            raise NotImplementedError(
                "PDF text extraction not yet implemented. "
                "Pass content_override with pre-extracted text, or use "
                "ingest_document_text() directly."
            )
        return {"document_id": document_id, "status": "error", "errors": ["No text content available"]}

    try:
        result = await ingest_document_text(
            text=text,
            medium=doc.medium,
            subject_id=doc.subject_id,
            source_type=doc.source_type,
            chapter_id=doc.chapter_id,
            document_id=document_id,
            job_id=job_id,
            dry_run=dry_run,
        )
    except Exception as e:
        msg = str(e)
        await doc.update({"$set": {"status": "failed", "error_message": msg, "updated_at": _now()}})
        await _update_job(job_id, status="failed", error_message=msg, finished_at=_now())
        return {"document_id": document_id, "status": "error", "errors": [msg]}

    if not dry_run:
        await doc.update({
            "$set": {
                "status": "processed",
                "ingested_at": _now(),
                "updated_at": _now(),
            }
        })
        await _update_job(
            job_id,
            status="done",
            progress=100,
            total_chunks=result.get("chunks_total", 0),
            processed_chunks=result.get("mongo_inserted", 0),
            result=result,
            finished_at=_now(),
        )

    return {
        "document_id": document_id,
        "status": "processed" if not dry_run else "dry_run",
        "result": result,
        "errors": result.get("errors", []),
    }


async def ingest_chapter_v2(
    chapter_id: str,
    content_en: Optional[str] = None,
    content_as: Optional[str] = None,
    metadata: Optional[dict] = None,
    source_type: str = "notes",
    job_id: Optional[str] = None,
    dry_run: bool = False,
) -> dict:
    """
    Convenience wrapper: ingest both EN and AS content for a chapter.

    Creates ephemeral document IDs (<chapter_id>_en / <chapter_id>_as) so
    existing chunks can be purged cleanly on re-index.

    Args:
        chapter_id: Chapter identifier.
        content_en: English text (markdown OK).
        content_as: Assamese text.
        metadata: Must include subject_id; optionally topic_id, board, etc.
        source_type: Chunking strategy (notes/definition/pyq/mcq).
        job_id: GenerationJob._id for progress tracking.
        dry_run: Skip writes.

    Returns:
        {"en": result, "as": result}
    """
    meta = metadata or {}
    subject_id = meta.get("subject_id", "unknown")

    async def _noop():
        return {
            "chunks_total": 0, "chunks_embedded": 0,
            "mongo_inserted": 0, "vectorize_upserted": 0,
            "errors": [], "chunk_ids": [],
        }

    async def _run(text: str, medium: str):
        doc_id = f"{chapter_id}_{medium}"
        deleted, old_vids = await _delete_existing_chunks(doc_id)
        if old_vids and not dry_run:
            try:
                from app.services.vectorize.client import vectorize_client
                await vectorize_client.delete(old_vids)
            except Exception as e:
                logger.warning(f"Stale vector delete failed: {e}")
        return await ingest_document_text(
            text=text,
            medium=medium,
            subject_id=subject_id,
            source_type=source_type,
            chapter_id=chapter_id,
            topic_id=meta.get("topic_id"),
            document_id=doc_id,
            job_id=job_id,
            dry_run=dry_run,
        )

    en_task = _run(content_en, "english") if content_en else _noop()
    as_task = _run(content_as, "assamese") if content_as else _noop()

    en_result, as_result = await asyncio.gather(en_task, as_task, return_exceptions=True)

    def _safe(r):
        if isinstance(r, Exception):
            return {
                "chunks_total": 0, "mongo_inserted": 0, "vectorize_upserted": 0,
                "errors": [str(r)], "chunk_ids": [],
            }
        return r

    return {"en": _safe(en_result), "as": _safe(as_result)}
