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
        source_type="notes",  # canonical: notes | important_questions | pyq | definition | mcqs
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
from app.services.rag.source_types import normalize_source_type, DEFAULT_SOURCE_TYPE

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


async def _purge_legacy_chunk_id(chapter_id: str, medium: str) -> list[str]:
    """
    Delete chunks stored under the pre-scope-isolation document_id format
    ``<chapter_id>_<medium>`` (no source_type component).

    Called as a compatibility step whenever we reindex under the new
    ``<chapter_id>_<source_type>_<medium>`` scheme so that old vectors don't
    continue to surface in retrieval alongside the freshly-written scoped chunks.

    Returns the vector_ids that should also be deleted from Cloudflare Vectorize.
    """
    legacy_id = f"{chapter_id}_{medium}"
    _, vids = await _delete_existing_chunks(legacy_id)
    if vids:
        logger.debug(
            "_purge_legacy_chunk_id: removed %d legacy vectors for doc_id=%s",
            len(vids), legacy_id,
        )
    return vids


async def _delete_chunks_by_prefix(doc_id_prefix: str) -> tuple[int, list[str]]:
    """
    Delete ALL chunk documents whose document_id starts with the given prefix.

    Used before a structured-sections reindex to purge stale sections whose
    index no longer exists (e.g. staff deleted trailing sections). Without this,
    old higher-index section vectors persist in Vectorize and can still be
    retrieved by the AI.

    Returns (deleted_count, vector_ids).
    """
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings as _s
        import re as _re
        col = get_mongo_client()[_s.MONGODB_DB_NAME]["chunks"]
        # Match any document_id that starts with the prefix (exact prefix or prefix + "_")
        pattern = _re.compile(r"^" + _re.escape(doc_id_prefix))
        docs = await col.find(
            {"document_id": {"$regex": pattern}}, {"_id": 1, "vector_id": 1}
        ).to_list(length=None)
        if not docs:
            return 0, []
        vector_ids = [d["vector_id"] for d in docs if d.get("vector_id")]
        result = await col.delete_many({"document_id": {"$regex": pattern}})
        return result.deleted_count, vector_ids
    except Exception as e:
        logger.warning(f"Failed to delete chunks by prefix={doc_id_prefix}: {e}")
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
    source_type: str = DEFAULT_SOURCE_TYPE,
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

    source_type = normalize_source_type(source_type)
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


async def ingest_sections(
    chapter_id: str,
    sections: list[dict],
    medium: str,
    subject_id: str,
    source_type: str,
    chunk_type: str,
    metadata: Optional[dict] = None,
    job_id: Optional[str] = None,
    dry_run: bool = False,
) -> dict:
    """
    Ingest a list of structured RAG sections as individual chunks.

    For notes sections: each dict is {title, content}.
    For Q&A sections:  each dict is {section, question, answer, solution}.

    Each section gets its own document_id so it can be purged independently.
    The parent document_id namespace is <chapter_id>_<medium>_section_<idx>.
    """
    meta = metadata or {}
    errors: list[str] = []
    total: dict = {"chunks_total": 0, "chunks_embedded": 0, "mongo_inserted": 0, "vectorize_upserted": 0, "chunk_ids": []}

    # Build text per section
    section_texts: list[str] = []
    for s in sections:
        if source_type in ("notes", "definition"):
            title   = s.get("title", "").strip()
            content = s.get("content", "").strip()
            text    = f"{title}\n{content}".strip() if title else content
        else:  # qa / important_questions / pyq
            parts = []
            if s.get("section"):  parts.append(f"Section: {s['section']}")
            if s.get("question"): parts.append(f"Q: {s['question']}")
            if s.get("answer"):   parts.append(f"A: {s['answer']}")
            if s.get("solution"): parts.append(f"Solution: {s['solution']}")
            text = "\n".join(parts)
        section_texts.append(text.strip())

    # Pre-pass: purge ALL existing section chunks for this scope+medium so that
    # if staff deleted trailing sections the old higher-index vectors are removed
    # before we write the current section list.
    prefix = f"{chapter_id}_{source_type}_{medium}_sec_"
    pre_deleted, pre_vids = await _delete_chunks_by_prefix(prefix)
    # Compatibility purge: also remove legacy-format blob vectors stored as
    # <chapter_id>_<medium> (pre-scope-isolation) so they don't surface alongside
    # the freshly-written scoped sections in retrieval.
    legacy_vids = await _purge_legacy_chunk_id(chapter_id, medium)
    all_pre_vids = pre_vids + legacy_vids
    if all_pre_vids and not dry_run:
        try:
            from app.services.vectorize.client import vectorize_client
            await vectorize_client.delete(all_pre_vids)
            logger.debug(
                "ingest_sections: pre-purged %d stale+legacy vectors (prefix=%s)",
                len(all_pre_vids), prefix,
            )
        except Exception as e:
            logger.warning(f"ingest_sections: pre-purge vectorize delete failed: {e}")

    for idx, text in enumerate(section_texts):
        if not text:
            continue
        # Include source_type in the document ID so notes/qa/pyq sections never
        # collide with each other even when chapter_id + medium + idx are identical.
        doc_id = f"{chapter_id}_{source_type}_{medium}_sec_{idx}"
        # Individual doc purge is now a no-op (already cleared by prefix purge above),
        # but we keep the call for safety in case this function is called concurrently.
        deleted, old_vids = await _delete_existing_chunks(doc_id)
        if old_vids and not dry_run:
            try:
                from app.services.vectorize.client import vectorize_client
                await vectorize_client.delete(old_vids)
            except Exception as e:
                logger.warning(f"Section stale vector delete failed: {e}")

        # Embed and write this section as a single chunk (no further splitting for short sections)
        cleaned = clean_text(text)
        if not cleaned:
            continue
        lang   = detect_language(cleaned)
        # Use a minimal chunk list — one entry per section
        chunks = [{"text": cleaned, "token_count": max(1, len(cleaned) // 4), "chunk_index": 0}]
        try:
            embeddings = await embed_batch_chunked([cleaned], batch_size=1)
        except Exception as exc:
            errors.append(f"Section {idx} embed failed: {exc}")
            continue

        now = _now()
        cid = _chunk_id(doc_id, 0)
        doc = {
            "_id": cid,
            "document_id": doc_id,
            "subject_id": subject_id,
            "chapter_id": chapter_id,
            "topic_id": meta.get("topic_id"),
            "medium": medium,
            "source_type": source_type,
            "chunk_type": chunk_type,
            "chunk_text": cleaned,
            "chunk_index": idx,
            "token_count": chunks[0]["token_count"],
            "vector_id": cid,
            "embedding_model": "cf/baai/bge-m3",
            "embedding_dim": 1024,
            "language": lang,
            "created_at": now,
            "updated_at": now,
        }
        total["chunks_total"] += 1
        total["chunk_ids"].append(cid)

        if not dry_run:
            inserted = await _upsert_to_mongo([doc])
            total["mongo_inserted"] += inserted
            total["chunks_embedded"] += 1
            try:
                from app.services.vectorize.client import vectorize_client
                result = await vectorize_client.upsert([{
                    "id": cid,
                    "values": embeddings[0],
                    "metadata": _vectorize_metadata(doc),
                }])
                total["vectorize_upserted"] += result.get("count", 0)
            except Exception as exc:
                errors.append(f"Section {idx} vectorize failed: {exc}")

    total["errors"] = errors
    return total


async def ingest_chapter_v2(
    chapter_id: str,
    content_en: Optional[str] = None,
    content_as: Optional[str] = None,
    metadata: Optional[dict] = None,
    source_type: str = "notes",
    job_id: Optional[str] = None,
    dry_run: bool = False,
    sections_en: Optional[list[dict]] = None,
    sections_as: Optional[list[dict]] = None,
    section_chunk_type: str = "topic_section",
) -> dict:
    """
    Convenience wrapper: ingest both EN and AS content for a chapter.

    When sections_en / sections_as are provided and non-empty, each section is
    ingested as a separate chunk (structured dual-layer mode).  The blob
    content_en / content_as path is used as fallback when sections are absent.

    Creates ephemeral document IDs (<chapter_id>_en / <chapter_id>_as) so
    existing chunks can be purged cleanly on re-index.

    Args:
        chapter_id: Chapter identifier.
        content_en: English text blob (markdown OK) — used when sections_en is empty.
        content_as: Assamese text blob — used when sections_as is empty.
        metadata: Must include subject_id; optionally topic_id, board, etc.
        source_type: Chunking strategy (notes/definition/pyq/mcq/important_questions).
        job_id: GenerationJob._id for progress tracking.
        dry_run: Skip writes.
        sections_en: Structured section list for English (overrides blob when non-empty).
        sections_as: Structured section list for Assamese (overrides blob when non-empty).
        section_chunk_type: chunk_type tag written to each section chunk (topic_section / qa_pair).

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

    async def _run_blob(text: str, medium: str):
        # Include source_type so notes/qa/pyq blobs use separate document namespaces
        # and reindexing one scope cannot delete vectors from another scope.
        doc_id = f"{chapter_id}_{source_type}_{medium}"
        deleted, old_vids = await _delete_existing_chunks(doc_id)
        # Compatibility purge: also remove any legacy-format vectors stored as
        # <chapter_id>_<medium> (pre-scope-isolation) so they don't surface
        # alongside the freshly-written scoped chunks in retrieval.
        legacy_vids = await _purge_legacy_chunk_id(chapter_id, medium)
        all_stale_vids = old_vids + legacy_vids
        if all_stale_vids and not dry_run:
            try:
                from app.services.vectorize.client import vectorize_client
                await vectorize_client.delete(all_stale_vids)
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

    async def _run_sections(secs: list[dict], medium: str):
        return await ingest_sections(
            chapter_id=chapter_id,
            sections=secs,
            medium=medium,
            subject_id=subject_id,
            source_type=source_type,
            chunk_type=section_chunk_type,
            metadata=meta,
            job_id=job_id,
            dry_run=dry_run,
        )

    # EN path
    if sections_en:
        en_task = _run_sections(sections_en, "english")
    elif content_en:
        en_task = _run_blob(content_en, "english")
    else:
        en_task = _noop()

    # AS path
    if sections_as:
        as_task = _run_sections(sections_as, "assamese")
    elif content_as:
        as_task = _run_blob(content_as, "assamese")
    else:
        as_task = _noop()

    en_result, as_result = await asyncio.gather(en_task, as_task, return_exceptions=True)

    def _safe(r):
        if isinstance(r, Exception):
            return {
                "chunks_total": 0, "mongo_inserted": 0, "vectorize_upserted": 0,
                "errors": [str(r)], "chunk_ids": [],
            }
        return r

    return {"en": _safe(en_result), "as": _safe(as_result)}
