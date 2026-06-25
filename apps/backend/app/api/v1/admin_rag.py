"""
Admin RAG endpoints — ingestion, reindexing, job tracking, content editing.

All endpoints require admin session (Bearer token via _validate_admin_session).

Routes:
  POST /admin/rag/upload/book               — register + queue a book PDF
  POST /admin/rag/upload/syllabus           — register + queue a syllabus
  POST /admin/rag/upload/pyq                — register + queue a PYQ document
  POST /admin/rag/upload/chapter-questions  — register + queue chapter questions
  POST /admin/rag/reindex/:document_id      — delete chunks + re-ingest
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

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.api.v1.admin import _validate_admin_session

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Admin RAG"])


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
    await _validate_admin_session(request)
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
    await _validate_admin_session(request)
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
    await _validate_admin_session(request)
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
    await _validate_admin_session(request)
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
    await _validate_admin_session(request)
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


# ── One-shot text ingest ───────────────────────────────────────────────────────

@router.post("/rag/ingest-text")
async def ingest_text_endpoint(req: IngestTextRequest, request: Request):
    """
    Directly ingest a text block without creating a RagDocument record first.
    Useful for testing chunking/embedding before wiring up a full PDF pipeline.
    """
    await _validate_admin_session(request)
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
    await _validate_admin_session(request)
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
    await _validate_admin_session(request)
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
    await _validate_admin_session(request)
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
    await _validate_admin_session(request)
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
    await _validate_admin_session(request)
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
    await _validate_admin_session(request)
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
    await _validate_admin_session(request)
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
    await _validate_admin_session(request)
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
