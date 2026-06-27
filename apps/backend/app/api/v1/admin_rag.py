"""
Admin RAG endpoints — ingestion, reindexing, job tracking, content editing.

All endpoints require admin session (router-level Depends on require_admin_session + csrf_guard).

Routes:
  POST /admin/rag/upload/book               — register + queue a book PDF
  POST /admin/rag/upload/syllabus           — register + queue a syllabus
  POST /admin/rag/upload/pyq                — register + queue a PYQ document
  POST /admin/rag/upload/chapter-questions  — register + queue chapter questions
  POST /admin/rag/reindex/:document_id      — delete chunks + re-ingest
  POST /admin/rag/reindex/subject/:id       — bulk re-index all chapters for a subject
  POST /admin/rag/reindex/chapter/:id       — re-index a single chapter from DB content
  GET  /admin/rag/jobs/:job_id              — poll job status + progress
  GET  /admin/rag/jobs                      — list recent jobs (paginated)
  GET  /admin/rag/documents                 — list RagDocuments (paginated)
  GET  /admin/rag/stats                     — chunk counts by medium/source_type
  POST /admin/rag/ingest-text               — one-shot text ingest (no file needed)
  GET  /admin/rag/vectorize/info            — CF Vectorize index info
  PATCH /admin/rag/content-nodes/:node_id   — update a ContentNode
  POST  /admin/rag/content-nodes/:node_id/publish — publish a ContentNode
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api.v1.admin import require_admin_session, csrf_guard

logger = logging.getLogger(__name__)
router = APIRouter(
    tags=["Admin RAG"],
    dependencies=[Depends(require_admin_session), Depends(csrf_guard)],
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Request / Response models ──────────────────────────────────────────────────

class UploadRequest(BaseModel):
    subject_id: str
    medium: str
    source_type: str
    chapter_id: Optional[str] = None
    topic_id: Optional[str] = None
    file_url: Optional[str] = None
    original_filename: Optional[str] = None
    page_count: Optional[int] = None
    content: Optional[str] = None


class IngestTextRequest(BaseModel):
    text: str
    medium: str
    subject_id: str
    source_type: str = "book_pdf"
    chapter_id: Optional[str] = None
    topic_id: Optional[str] = None
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    dry_run: bool = False


class ContentNodePatch(BaseModel):
    status: Optional[str] = None
    content: Optional[dict] = None
    node_type: Optional[str] = None


class BulkReindexRequest(BaseModel):
    source_type: str = "notes"
    parallelism: int = 3
    dry_run: bool = False
    chapter_ids: Optional[list] = None


class ChapterReindexRequest(BaseModel):
    source_type: str = "notes"
    dry_run: bool = False


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _create_document(req: UploadRequest, source_type: str) -> tuple:
    """Create a RagDocument + GenerationJob and return (doc, job)."""
    from app.models.rag import RagDocument, GenerationJob

    doc = RagDocument(
        subject_id=req.subject_id,
        chapter_id=req.chapter_id,
        medium=req.medium,
        source_type=source_type,
        file_url=req.file_url,
        original_filename=req.original_filename,
        page_count=req.page_count,
        status="pending",
    )
    await doc.insert()

    job = GenerationJob(
        job_type="ingest_document",
        subject_id=req.subject_id,
        chapter_id=req.chapter_id,
        topic_id=req.topic_id,
        document_id=str(doc.id),
        medium=req.medium,
        status="pending",
    )
    await job.insert()
    return doc, job


async def _kick_ingest(document_id: str, job_id: str, content: Optional[str]) -> None:
    """Fire-and-forget: run ingestion in the background."""
    from app.services.rag.ingestion_v2 import ingest_document
    try:
        await ingest_document(
            document_id=document_id,
            content_override=content,
            job_id=job_id,
        )
    except Exception as e:
        logger.error(f"Background ingest failed doc={document_id}: {e}")
        from app.models.rag import GenerationJob
        try:
            job = await GenerationJob.get(job_id)
            if job:
                await job.update({
                    "$set": {
                        "status": "failed",
                        "error_message": str(e),
                        "finished_at": _now(),
                        "updated_at": _now(),
                    }
                })
        except Exception:
            pass


# ── Upload endpoints ───────────────────────────────────────────────────────────

@router.post("/rag/upload/book")
async def upload_book(req: UploadRequest, request: Request):
    """Register an English or Assamese book PDF and queue it for ingestion."""

    doc, job = await _create_document(req, source_type="book_pdf")
    import asyncio
    asyncio.create_task(_kick_ingest(str(doc.id), str(job.id), req.content))
    return {
        "document_id": str(doc.id),
        "job_id": str(job.id),
        "status": "queued",
        "message": "Book queued for ingestion. Poll /admin/rag/jobs/{job_id} for progress.",
    }


@router.post("/rag/upload/syllabus")
async def upload_syllabus(req: UploadRequest, request: Request):
    """Register a syllabus document and queue for ingestion."""

    doc, job = await _create_document(req, source_type="syllabus")
    import asyncio
    asyncio.create_task(_kick_ingest(str(doc.id), str(job.id), req.content))
    return {
        "document_id": str(doc.id),
        "job_id": str(job.id),
        "status": "queued",
    }


@router.post("/rag/upload/pyq")
async def upload_pyq(req: UploadRequest, request: Request):
    """Register a Past Year Question paper and queue for ingestion."""

    doc, job = await _create_document(req, source_type="pyq")
    import asyncio
    asyncio.create_task(_kick_ingest(str(doc.id), str(job.id), req.content))
    return {
        "document_id": str(doc.id),
        "job_id": str(job.id),
        "status": "queued",
    }


@router.post("/rag/upload/chapter-questions")
async def upload_chapter_questions(req: UploadRequest, request: Request):
    """Register chapter-exercise questions and queue for ingestion."""

    doc, job = await _create_document(req, source_type="chapter_question")
    import asyncio
    asyncio.create_task(_kick_ingest(str(doc.id), str(job.id), req.content))
    return {
        "document_id": str(doc.id),
        "job_id": str(job.id),
        "status": "queued",
    }


# ── Reindex ────────────────────────────────────────────────────────────────────

@router.post("/rag/reindex/{document_id}")
async def reindex_document(document_id: str, request: Request):
    """
    Delete all chunks for a document from MongoDB + Vectorize, then re-ingest.
    The document's original content_override must be re-supplied via the body,
    OR the document must have file_url set (PDF extraction stub).
    """

    from app.models.rag import RagDocument, GenerationJob

    doc = await RagDocument.get(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    job = GenerationJob(
        job_type="reindex_document",
        subject_id=doc.subject_id,
        chapter_id=doc.chapter_id,
        document_id=document_id,
        medium=doc.medium,
        status="pending",
    )
    await job.insert()

    import asyncio
    asyncio.create_task(_kick_ingest(document_id, str(job.id), content=None))

    return {
        "document_id": document_id,
        "job_id": str(job.id),
        "status": "queued",
        "message": "Reindex queued. Old chunks will be purged then re-ingested.",
    }


# ── Bulk chapter re-index helpers ─────────────────────────────────────────────

async def _run_bulk_chapter_reindex(
    subject_id: str,
    chapters: list,
    source_type: str,
    parallelism: int,
    job_id: str,
    dry_run: bool,
) -> None:
    """
    Background task: concurrently re-index all chapters for a subject.

    Uses asyncio.Semaphore(parallelism) so at most `parallelism` chapters
    are being embedded + upserted at the same time, avoiding CF API rate limits.
    Progress is written back to the master GenerationJob so the caller can
    poll /admin/rag/jobs/{job_id}.
    """
    import asyncio as _asyncio
    from app.models.rag import GenerationJob as _Job
    from app.services.rag.ingestion_v2 import ingest_chapter_v2

    total = len(chapters)
    processed = 0
    errors: list[str] = []
    chapter_results: dict = {}

    try:
        job = await _Job.get(job_id)
        if job:
            await job.update({
                "$set": {
                    "status": "running",
                    "total_chunks": total,
                    "started_at": _now(),
                    "updated_at": _now(),
                }
            })
    except Exception:
        pass

    sem = _asyncio.Semaphore(max(1, min(parallelism, 10)))

    async def _process_one(chapter) -> None:
        nonlocal processed
        chapter_id = str(chapter.id)
        async with sem:
            try:
                result = await ingest_chapter_v2(
                    chapter_id=chapter_id,
                    content_en=chapter.content_en,
                    content_as=chapter.content_as,
                    metadata={"subject_id": subject_id},
                    source_type=source_type,
                    dry_run=dry_run,
                )
                en = result.get("en", {})
                as_ = result.get("as", {})
                chapter_results[chapter_id] = {
                    "title": chapter.title,
                    "en_chunks": en.get("chunks_total", 0),
                    "en_vectorized": en.get("vectorize_upserted", 0),
                    "as_chunks": as_.get("chunks_total", 0),
                    "as_vectorized": as_.get("vectorize_upserted", 0),
                    "errors": en.get("errors", []) + as_.get("errors", []),
                }
                logger.info(
                    f"[bulk-reindex] chapter={chapter_id} ({chapter.title}) "
                    f"en={en.get('chunks_total',0)}ch/{en.get('vectorize_upserted',0)}v "
                    f"as={as_.get('chunks_total',0)}ch/{as_.get('vectorize_upserted',0)}v"
                )
            except Exception as exc:
                err_msg = f"chapter={chapter_id} ({chapter.title}): {exc}"
                logger.error(f"[bulk-reindex] {err_msg}")
                errors.append(err_msg)
                chapter_results[chapter_id] = {
                    "title": chapter.title,
                    "error": str(exc),
                }
            finally:
                processed += 1
                progress = int(processed / max(total, 1) * 100)
                try:
                    _job = await _Job.get(job_id)
                    if _job:
                        await _job.update({
                            "$set": {
                                "processed_chunks": processed,
                                "progress": progress,
                                "updated_at": _now(),
                            }
                        })
                except Exception:
                    pass

    await _asyncio.gather(*[_process_one(ch) for ch in chapters], return_exceptions=True)

    total_en = sum(r.get("en_chunks", 0) for r in chapter_results.values() if isinstance(r, dict))
    total_as = sum(r.get("as_chunks", 0) for r in chapter_results.values() if isinstance(r, dict))
    total_vec = sum(
        r.get("en_vectorized", 0) + r.get("as_vectorized", 0)
        for r in chapter_results.values() if isinstance(r, dict)
    )
    summary = {
        "chapters_processed": processed,
        "chapters_total": total,
        "total_en_chunks": total_en,
        "total_as_chunks": total_as,
        "total_vectorized": total_vec,
        "error_count": len(errors),
        "errors": errors[:20],
        "chapters": chapter_results,
    }

    try:
        job = await _Job.get(job_id)
        if job:
            final_status = "done" if not errors or processed > 0 else "failed"
            await job.update({
                "$set": {
                    "status": final_status,
                    "progress": 100,
                    "result": summary,
                    "finished_at": _now(),
                    "updated_at": _now(),
                }
            })
    except Exception as e:
        logger.error(f"[bulk-reindex] failed to write final job status: {e}")


# ── Bulk chapter re-index by IDs ──────────────────────────────────────────────

class BulkChapterReindexRequest(BaseModel):
    chapter_ids: list[str]
    source_type: str = "notes"
    dry_run: bool = False


@router.post("/rag/bulk-reindex")
async def bulk_reindex_chapters(req: BulkChapterReindexRequest, request: Request):
    """
    Bulk re-index up to 50 chapters by their IDs.
    Creates a tracked GenerationJob, then runs reindexing as a background task.
    Returns job_id immediately — poll /admin/rag/jobs/{job_id} for progress.

    Used by the admin bulk-action bar "Reindex RAG" button.
    """
    import asyncio as _asyncio
    from app.models.content import Chapter
    from app.models.rag import GenerationJob
    from app.services.rag.ingestion_v2 import ingest_chapter_v2

    chapter_ids = req.chapter_ids[:50]
    if not chapter_ids:
        raise HTTPException(status_code=422, detail="chapter_ids must be non-empty")

    # Create the trackable job up-front so the frontend can poll immediately
    job = GenerationJob(
        job_type="bulk_reindex_chapters",
        status="pending",
        total_chunks=len(chapter_ids),
        processed_chunks=0,
        progress=0,
    )
    await job.insert()
    job_id = str(job.id)

    async def _run(cids=chapter_ids, _job_id=job_id):
        from app.models.rag import GenerationJob as _Job
        errors: list[str] = []
        processed = 0

        _job = await _Job.get(_job_id)
        if _job:
            await _job.update({"$set": {"status": "running", "started_at": _now(), "updated_at": _now()}})

        for chapter_id in cids:
            try:
                chapter = await Chapter.get(chapter_id)
                if not chapter:
                    errors.append(f"{chapter_id}: not_found")
                    processed += 1
                    continue
                ingest_en = getattr(chapter, "rag_text_en", None) or chapter.content_en
                ingest_as = getattr(chapter, "rag_text_as", None) or chapter.content_as
                if not ingest_en and not ingest_as:
                    errors.append(f"{chapter_id}: no_content")
                    processed += 1
                    continue
                await ingest_chapter_v2(
                    chapter_id=chapter_id,
                    content_en=ingest_en,
                    content_as=ingest_as,
                    metadata={"subject_id": str(chapter.subject_id)},
                    source_type=req.source_type,
                    dry_run=req.dry_run,
                )
                if not req.dry_run:
                    fresh = await Chapter.get(chapter_id)
                    if fresh:
                        fresh.rag_indexed_at = _now()
                        await fresh.save()
            except Exception as exc:
                logger.error(f"[bulk-reindex] chapter={chapter_id} error: {exc}")
                errors.append(f"{chapter_id}: {str(exc)[:120]}")
            finally:
                processed += 1
                pct = int(processed / len(cids) * 100)
                _job = await _Job.get(_job_id)
                if _job:
                    await _job.update({"$set": {
                        "processed_chunks": processed,
                        "progress": pct,
                        "updated_at": _now(),
                    }})

        final_status = "done" if not errors or processed > len(errors) else "failed"
        _job = await _Job.get(_job_id)
        if _job:
            await _job.update({"$set": {
                "status": final_status,
                "progress": 100,
                "finished_at": _now(),
                "updated_at": _now(),
                "result": {
                    "total": len(cids),
                    "succeeded": processed - len(errors),
                    "errors": errors[:20],
                    "dry_run": req.dry_run,
                },
            }})

    _asyncio.create_task(_run())

    return {
        "job_id": job_id,
        "queued": len(chapter_ids),
        "dry_run": req.dry_run,
        "status": "queued",
        "message": f"Reindexing {len(chapter_ids)} chapters. Poll /admin/rag/jobs/{job_id} for progress.",
    }


# ── Bulk subject re-index ──────────────────────────────────────────────────────

@router.post("/rag/reindex/subject/{subject_id}")
async def reindex_subject_chapters(
    subject_id: str,
    req: BulkReindexRequest,
    request: Request,
):
    """
    Bulk re-index all chapters for a subject from their MongoDB content.

    Iterates every Chapter document with `subject_id` that has `content_en`
    or `content_as`, runs `ingest_chapter_v2()` concurrently (bounded by
    `parallelism`, default 3), and writes results back to a GenerationJob
    you can poll at /admin/rag/jobs/{job_id}.

    Query params / body:
      source_type   — chunking strategy (notes/definition/pyq/mcqs). Default: notes.
      parallelism   — max concurrent chapter ingestions (1-10). Default: 3.
      dry_run       — embed + chunk but skip all writes. Default: false.
      chapter_ids   — if set, only re-index these specific chapter IDs.
    """
    import asyncio as _asyncio


    parallelism = max(1, min(req.parallelism, 10))

    from app.models.content import Chapter
    from app.models.rag import GenerationJob

    query: dict = {"subject_id": subject_id}
    all_chapters = (
        await Chapter.find(query)
        .sort([("chapter_number", 1)])
        .to_list()
    )

    if req.chapter_ids:
        requested = set(str(c) for c in req.chapter_ids)
        all_chapters = [ch for ch in all_chapters if str(ch.id) in requested]

    eligible = [
        ch for ch in all_chapters
        if ch.content_en or ch.content_as
    ]

    if not eligible:
        return {
            "subject_id": subject_id,
            "chapters_found": len(all_chapters),
            "chapters_eligible": 0,
            "status": "skipped",
            "message": "No chapters have content_en or content_as to ingest.",
        }

    job = GenerationJob(
        job_type="bulk_reindex_subject",
        subject_id=subject_id,
        status="pending",
        total_chunks=len(eligible),
    )
    await job.insert()
    job_id = str(job.id)

    _asyncio.create_task(
        _run_bulk_chapter_reindex(
            subject_id=subject_id,
            chapters=eligible,
            source_type=req.source_type,
            parallelism=parallelism,
            job_id=job_id,
            dry_run=req.dry_run,
        )
    )

    logger.info(
        f"[bulk-reindex] queued subject={subject_id} "
        f"chapters={len(eligible)} parallelism={parallelism} "
        f"source_type={req.source_type} dry_run={req.dry_run} job={job_id}"
    )

    return {
        "job_id": job_id,
        "subject_id": subject_id,
        "chapters_found": len(all_chapters),
        "chapters_eligible": len(eligible),
        "parallelism": parallelism,
        "source_type": req.source_type,
        "dry_run": req.dry_run,
        "status": "queued",
        "message": f"Re-indexing {len(eligible)} chapters. Poll /admin/rag/jobs/{job_id} for progress.",
    }


# ── Single chapter re-index from DB content ───────────────────────────────────

@router.post("/rag/reindex/chapter/{chapter_id}")
async def reindex_chapter(
    chapter_id: str,
    req: ChapterReindexRequest,
    request: Request,
):
    """
    Re-index a single chapter by reading its content_en / content_as from MongoDB.

    Purges existing chunks (MongoDB `chunks` collection + Cloudflare Vectorize),
    then runs the full ingest pipeline synchronously and returns the result.

    Use this for targeted re-indexing after editing a chapter's content in the
    admin CMS, or for testing chunking/embedding on individual chapters before
    running a full subject bulk-reindex.
    """


    from app.models.content import Chapter
    from app.services.rag.ingestion_v2 import ingest_chapter_v2

    chapter = await Chapter.get(chapter_id)
    if not chapter:
        raise HTTPException(status_code=404, detail=f"Chapter not found: {chapter_id}")

    # Prefer rag_text_* over content_* for ingestion (P2.3)
    # rag_text_* is the clean retrieval-optimised version written by admins;
    # fall back to content_* which is the reader-facing version.
    ingest_en = getattr(chapter, "rag_text_en", None) or chapter.content_en
    ingest_as = getattr(chapter, "rag_text_as", None) or chapter.content_as

    if not ingest_en and not ingest_as:
        raise HTTPException(
            status_code=422,
            detail="Chapter has no content to ingest (rag_text_en/as and content_en/as are all empty).",
        )

    subject_id = str(chapter.subject_id)

    rag_indicator = []
    if getattr(chapter, "rag_text_en", None):
        rag_indicator.append("rag_text_en")
    elif chapter.content_en:
        rag_indicator.append("content_en")
    if getattr(chapter, "rag_text_as", None):
        rag_indicator.append("rag_text_as")
    elif chapter.content_as:
        rag_indicator.append("content_as")

    logger.info(
        f"[chapter-reindex] chapter={chapter_id} ({chapter.title}) "
        f"subject={subject_id} source_type={req.source_type} dry_run={req.dry_run} "
        f"using_fields={rag_indicator}"
    )

    result = await ingest_chapter_v2(
        chapter_id=chapter_id,
        content_en=ingest_en,
        content_as=ingest_as,
        metadata={"subject_id": subject_id},
        source_type=req.source_type,
        dry_run=req.dry_run,
    )

    # Stamp rag_indexed_at so sync badges can reflect current state
    if not req.dry_run:
        try:
            fresh = await Chapter.get(chapter_id)
            if fresh:
                fresh.rag_indexed_at = _now()
                await fresh.save()
        except Exception as _stamp_err:
            logger.warning(f"[chapter-reindex] could not stamp rag_indexed_at: {_stamp_err}")

    en = result.get("en", {})
    as_ = result.get("as", {})
    all_errors = en.get("errors", []) + as_.get("errors", [])

    return {
        "chapter_id": chapter_id,
        "chapter_title": chapter.title,
        "subject_id": subject_id,
        "source_type": req.source_type,
        "dry_run": req.dry_run,
        "en": {
            "chunks_total": en.get("chunks_total", 0),
            "chunks_embedded": en.get("chunks_embedded", 0),
            "mongo_inserted": en.get("mongo_inserted", 0),
            "vectorize_upserted": en.get("vectorize_upserted", 0),
            "errors": en.get("errors", []),
        },
        "as": {
            "chunks_total": as_.get("chunks_total", 0),
            "chunks_embedded": as_.get("chunks_embedded", 0),
            "mongo_inserted": as_.get("mongo_inserted", 0),
            "vectorize_upserted": as_.get("vectorize_upserted", 0),
            "errors": as_.get("errors", []),
        },
        "total_chunks": en.get("chunks_total", 0) + as_.get("chunks_total", 0),
        "total_vectorized": en.get("vectorize_upserted", 0) + as_.get("vectorize_upserted", 0),
        "errors": all_errors,
        "status": "dry_run" if req.dry_run else ("ok" if not all_errors else "partial"),
    }


# ── One-shot text ingest ───────────────────────────────────────────────────────

@router.post("/rag/ingest-text")
async def ingest_text_endpoint(req: IngestTextRequest, request: Request):
    """
    Directly ingest a text block without creating a RagDocument record first.
    Useful for testing chunking/embedding before wiring up a full PDF pipeline.
    """

    from app.services.rag.ingestion_v2 import ingest_document_text

    result = await ingest_document_text(
        text=req.text,
        medium=req.medium,
        subject_id=req.subject_id,
        source_type=req.source_type,
        chapter_id=req.chapter_id,
        topic_id=req.topic_id,
        page_start=req.page_start,
        page_end=req.page_end,
        dry_run=req.dry_run,
    )
    return result


# ── Job polling ────────────────────────────────────────────────────────────────

@router.get("/rag/jobs/{job_id}")
async def get_job(job_id: str, request: Request):
    """Poll a GenerationJob for status and progress."""

    from app.models.rag import GenerationJob

    job = await GenerationJob.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    elapsed_s = None
    if job.started_at:
        end = job.finished_at or _now()
        elapsed_s = round((end - job.started_at).total_seconds(), 1)

    return {
        "job_id": str(job.id),
        "job_type": job.job_type,
        "status": job.status,
        "progress": job.progress,
        "total_chunks": job.total_chunks,
        "processed_chunks": job.processed_chunks,
        "medium": job.medium,
        "subject_id": job.subject_id,
        "chapter_id": job.chapter_id,
        "document_id": job.document_id,
        "error_message": job.error_message,
        "result": job.result,
        "elapsed_seconds": elapsed_s,
        "created_at": job.created_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }


@router.get("/rag/jobs")
async def list_jobs(
    request: Request,
    status: Optional[str] = None,
    job_type: Optional[str] = None,
    limit: int = 20,
    skip: int = 0,
):
    """List recent GenerationJobs, optionally filtered by status or job_type."""

    from app.models.rag import GenerationJob

    query: dict = {}
    if status:
        query["status"] = status
    if job_type:
        query["job_type"] = job_type

    jobs = (
        await GenerationJob.find(query)
        .sort([("created_at", -1)])
        .skip(skip)
        .limit(limit)
        .to_list()
    )
    total = await GenerationJob.find(query).count()

    return {
        "total": total,
        "limit": limit,
        "skip": skip,
        "jobs": [
            {
                "job_id": str(j.id),
                "job_type": j.job_type,
                "status": j.status,
                "progress": j.progress,
                "medium": j.medium,
                "subject_id": j.subject_id,
                "document_id": j.document_id,
                "created_at": j.created_at.isoformat(),
                "finished_at": j.finished_at.isoformat() if j.finished_at else None,
            }
            for j in jobs
        ],
    }


# ── Documents ──────────────────────────────────────────────────────────────────

@router.get("/rag/documents")
async def list_documents(
    request: Request,
    subject_id: Optional[str] = None,
    medium: Optional[str] = None,
    status: Optional[str] = None,
    source_type: Optional[str] = None,
    limit: int = 20,
    skip: int = 0,
):
    """List RagDocuments with optional filters."""

    from app.models.rag import RagDocument

    query: dict = {}
    if subject_id:
        query["subject_id"] = subject_id
    if medium:
        query["medium"] = medium
    if status:
        query["status"] = status
    if source_type:
        query["source_type"] = source_type

    docs = (
        await RagDocument.find(query)
        .sort([("created_at", -1)])
        .skip(skip)
        .limit(limit)
        .to_list()
    )
    total = await RagDocument.find(query).count()

    return {
        "total": total,
        "limit": limit,
        "skip": skip,
        "documents": [
            {
                "document_id": str(d.id),
                "subject_id": d.subject_id,
                "chapter_id": d.chapter_id,
                "medium": d.medium,
                "source_type": d.source_type,
                "status": d.status,
                "original_filename": d.original_filename,
                "page_count": d.page_count,
                "ingested_at": d.ingested_at.isoformat() if d.ingested_at else None,
                "created_at": d.created_at.isoformat(),
            }
            for d in docs
        ],
    }


# ── Stats ──────────────────────────────────────────────────────────────────────

@router.get("/rag/stats")
async def rag_stats(request: Request):
    """
    Return chunk counts broken down by medium and source_type.
    Also returns Vectorize index info if CF is configured.
    """

    from app.db.mongo import get_mongo_client
    from app.config import settings as _s

    client = get_mongo_client()
    db = client[_s.MONGODB_DB_NAME]

    pipeline = [
        {
            "$group": {
                "_id": {"medium": "$medium", "source_type": "$source_type"},
                "count": {"$sum": 1},
            }
        },
        {"$sort": {"_id.medium": 1, "_id.source_type": 1}},
    ]
    try:
        rows = await db["chunks"].aggregate(pipeline).to_list(length=100)
        chunk_breakdown = [
            {
                "medium": r["_id"]["medium"],
                "source_type": r["_id"]["source_type"],
                "count": r["count"],
            }
            for r in rows
        ]
        total_chunks_v2 = sum(r["count"] for r in chunk_breakdown)
    except Exception as e:
        chunk_breakdown = []
        total_chunks_v2 = 0
        logger.warning(f"Chunk stats query failed: {e}")

    try:
        total_rag_chunks_v1 = await db["rag_chunks"].count_documents({})
    except Exception:
        total_rag_chunks_v1 = 0

    try:
        total_docs = await db["rag_documents"].count_documents({})
        pending_docs = await db["rag_documents"].count_documents({"status": "pending"})
        processed_docs = await db["rag_documents"].count_documents({"status": "processed"})
        failed_docs = await db["rag_documents"].count_documents({"status": "failed"})
    except Exception:
        total_docs = pending_docs = processed_docs = failed_docs = 0

    vectorize_info = None
    try:
        from app.services.vectorize.client import vectorize_client
        vectorize_info = await vectorize_client.get_index_info()
    except Exception as e:
        vectorize_info = {"error": str(e)}

    return {
        "chunks_v2": {
            "total": total_chunks_v2,
            "breakdown": chunk_breakdown,
        },
        "chunks_v1_legacy": total_rag_chunks_v1,
        "documents": {
            "total": total_docs,
            "pending": pending_docs,
            "processed": processed_docs,
            "failed": failed_docs,
        },
        "vectorize_index": vectorize_info,
    }


# ── Vectorize index info ───────────────────────────────────────────────────────

@router.get("/rag/vectorize/info")
async def vectorize_info(request: Request):
    """Return raw Cloudflare Vectorize index metadata."""

    try:
        from app.services.vectorize.client import vectorize_client
        info = await vectorize_client.get_index_info()
        return info
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Vectorize API error: {e}")


# ── Content nodes ──────────────────────────────────────────────────────────────

@router.get("/rag/content-nodes")
async def list_content_nodes(
    request: Request,
    subject_id: Optional[str] = None,
    chapter_id: Optional[str] = None,
    medium: Optional[str] = None,
    status: Optional[str] = None,
    node_type: Optional[str] = None,
    limit: int = 20,
    skip: int = 0,
):
    """List ContentNodes with optional filters."""

    from app.models.rag import ContentNode

    query: dict = {}
    if subject_id:
        query["subject_id"] = subject_id
    if chapter_id:
        query["chapter_id"] = chapter_id
    if medium:
        query["medium"] = medium
    if status:
        query["status"] = status
    if node_type:
        query["node_type"] = node_type

    nodes = (
        await ContentNode.find(query)
        .sort([("updated_at", -1)])
        .skip(skip)
        .limit(limit)
        .to_list()
    )
    total = await ContentNode.find(query).count()

    return {
        "total": total,
        "limit": limit,
        "skip": skip,
        "nodes": [
            {
                "node_id": str(n.id),
                "subject_id": n.subject_id,
                "chapter_id": n.chapter_id,
                "topic_id": n.topic_id,
                "medium": n.medium,
                "node_type": n.node_type,
                "status": n.status,
                "version": n.version,
                "updated_at": n.updated_at.isoformat(),
            }
            for n in nodes
        ],
    }


@router.patch("/rag/content-nodes/{node_id}")
async def update_content_node(node_id: str, patch: ContentNodePatch, request: Request):
    """Update a ContentNode's status, content, or node_type."""

    from app.models.rag import ContentNode

    node = await ContentNode.get(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="ContentNode not found")

    updates: dict = {"updated_at": _now()}
    if patch.status is not None:
        updates["status"] = patch.status
    if patch.content is not None:
        updates["content"] = patch.content
    if patch.node_type is not None:
        updates["node_type"] = patch.node_type

    await node.update({"$set": updates})
    return {"node_id": node_id, "updated": list(updates.keys())}


@router.post("/rag/content-nodes/{node_id}/publish")
async def publish_content_node(node_id: str, request: Request):
    """
    Publish a ContentNode: set status='published', bump version, record timestamp.
    """

    from app.models.rag import ContentNode

    node = await ContentNode.get(node_id)
    if not node:
        raise HTTPException(status_code=404, detail="ContentNode not found")

    now = _now()
    await node.update({
        "$set": {
            "status": "published",
            "published_at": now,
            "updated_at": now,
        },
        "$inc": {"version": 1},
    })
    return {
        "node_id": node_id,
        "status": "published",
        "version": node.version + 1,
        "published_at": now.isoformat(),
    }


@router.get("/rag/coverage")
async def rag_coverage():
    """
    RAG coverage summary per subject+medium.

    Returns chunk counts, document counts, and job stats grouped by (subject_id, medium).
    Use this to identify gaps (subjects/chapters with zero RAG chunks).
    """
    from app.db.mongo import get_mongo_client
    from app.config import settings

    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        # ── Chunk counts by (subject_id, medium) ────────────────────────────
        chunk_pipeline = [
            {
                "$group": {
                    "_id": {"subject_id": "$subject_id", "medium": "$medium"},
                    "chunk_count": {"$sum": 1},
                    "chapter_ids": {"$addToSet": "$chapter_id"},
                    "last_indexed_at": {"$max": "$created_at"},
                }
            },
            {"$sort": {"_id.subject_id": 1, "_id.medium": 1}},
        ]
        chunk_rows = await db.chunks.aggregate(chunk_pipeline).to_list(length=500)

        # ── Document counts by (subject_id, medium) ──────────────────────────
        doc_pipeline = [
            {
                "$group": {
                    "_id": {"subject_id": "$subject_id", "medium": "$medium"},
                    "doc_count": {"$sum": 1},
                    "source_types": {"$addToSet": "$source_type"},
                }
            }
        ]
        doc_rows = await db.rag_documents.aggregate(doc_pipeline).to_list(length=500)
        doc_map = {
            (r["_id"].get("subject_id"), r["_id"].get("medium")): r
            for r in doc_rows
        }

        # ── Active/failed job counts ──────────────────────────────────────────
        active_jobs = await db.generation_jobs.count_documents(
            {"status": {"$in": ["pending", "running"]}}
        )
        failed_jobs = await db.generation_jobs.count_documents({"status": "failed"})
        completed_jobs = await db.generation_jobs.count_documents({"status": "completed"})

        coverage = []
        for row in chunk_rows:
            key = (row["_id"].get("subject_id"), row["_id"].get("medium"))
            doc_info = doc_map.get(key, {})
            coverage.append(
                {
                    "subject_id": key[0],
                    "medium": key[1],
                    "chunk_count": row["chunk_count"],
                    "chapter_count": len(
                        [c for c in row.get("chapter_ids", []) if c]
                    ),
                    "document_count": doc_info.get("doc_count", 0),
                    "source_types": doc_info.get("source_types", []),
                    "last_indexed_at": row["last_indexed_at"].isoformat()
                    if row.get("last_indexed_at")
                    else None,
                }
            )

        total_chunks = sum(r["chunk_count"] for r in chunk_rows)
        english_chunks = sum(
            r["chunk_count"] for r in chunk_rows if r["_id"].get("medium") == "english"
        )
        assamese_chunks = sum(
            r["chunk_count"] for r in chunk_rows if r["_id"].get("medium") == "assamese"
        )

        return {
            "coverage": coverage,
            "summary": {
                "total_chunks": total_chunks,
                "english_chunks": english_chunks,
                "assamese_chunks": assamese_chunks,
                "subjects_covered": len({r["_id"].get("subject_id") for r in chunk_rows}),
                "active_jobs": active_jobs,
                "failed_jobs": failed_jobs,
                "completed_jobs": completed_jobs,
            },
        }
    except Exception as e:
        logger.error(f"RAG coverage error: {e}")
        return {
            "coverage": [],
            "summary": {
                "total_chunks": 0,
                "english_chunks": 0,
                "assamese_chunks": 0,
                "subjects_covered": 0,
                "active_jobs": 0,
                "failed_jobs": 0,
                "completed_jobs": 0,
            },
            "error": str(e),
        }
