"""
Admin PYQ (Previous Year Question Paper) endpoints.
Stores PYQ records in the 'pyqs' MongoDB collection via raw motor (no Beanie model needed).
"""
import logging
import uuid as _uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from app.api.v1.admin import require_admin_session, csrf_guard
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Admin PYQ"],
    dependencies=[Depends(require_admin_session), Depends(csrf_guard)],
)


def _col():
    """Return the raw motor 'pyqs' collection."""
    from app.db.mongo import get_mongo_client
    return get_mongo_client()[settings.MONGODB_DB_NAME]["pyqs"]


def _fmt(doc: dict) -> dict:
    """Normalise _id → id for JSON responses."""
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id", ""))
    # Serialise datetimes
    for k in ("created_at", "updated_at"):
        if isinstance(doc.get(k), datetime):
            doc[k] = doc[k].isoformat()
    return doc


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("/pyq/by-chapter/{chapter_id}")
async def list_pyqs_by_chapter(request: Request, chapter_id: str):
    """List all PYQ records for a chapter."""
    col = _col()
    docs = await col.find({"chapter_id": chapter_id}).sort("created_at", -1).to_list(length=200)
    return {"pyqs": [_fmt(d) for d in docs]}


# ── Text upload ───────────────────────────────────────────────────────────────

class TextPyqRequest(BaseModel):
    text: str
    exam_year: int = 0
    paper_type: str = "major"
    subject_id: str = ""
    board_id: str = ""
    class_id: str = ""
    stream_id: str = ""
    chapter_id: str = ""


@router.post("/pyq/upload-text")
async def upload_text_pyq(request: Request, body: TextPyqRequest):
    """Save raw pasted question-paper text as a PYQ record."""
    col = _col()
    pyq_id = _uuid.uuid4().hex
    doc = {
        "_id": pyq_id,
        "chapter_id": body.chapter_id,
        "subject_id": body.subject_id,
        "board_id": body.board_id,
        "class_id": body.class_id,
        "stream_id": body.stream_id,
        "exam_year": body.exam_year,
        "paper_type": body.paper_type,
        "filename": "text_pyq",
        "file_url": "",
        "is_image": False,
        "is_pdf": False,
        "is_text": True,
        "processing_status": "ocr_done",
        "question_count": body.text.count("?"),
        "text_content": body.text.strip(),
        "seo_url": None,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    await col.insert_one(doc)
    return {"status": "ok", "id": pyq_id}


# ── File upload ───────────────────────────────────────────────────────────────

@router.post("/pyq/upload")
async def upload_pyq_files(
    request: Request,
    files: list[UploadFile] = File(...),
    exam_year: int = Form(0),
    paper_type: str = Form("major"),
    subject_id: str = Form(""),
    board_id: str = Form(""),
    class_id: str = Form(""),
    stream_id: str = Form(""),
    chapter_id: str = Form(""),
):
    """Upload PDF / image PYQ files. Stores them on GCS if configured."""
    col = _col()
    created_ids: list[str] = []

    for file in files:
        data = await file.read()
        if len(data) > 50 * 1024 * 1024:
            raise HTTPException(status_code=413, detail=f"File '{file.filename}' exceeds 50 MB limit")
        fname = file.filename or "upload"
        ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
        ct = file.content_type or ""
        is_pdf = ext == "pdf" or ct == "application/pdf"
        allowed_image_exts = {"jpg", "jpeg", "png", "webp", "gif", "bmp", "tiff", "tif"}
        is_image = ct.startswith("image/") or ext in allowed_image_exts
        if not is_pdf and not is_image:
            raise HTTPException(status_code=400, detail=f"Unsupported file type '{ext}' — only PDF and images allowed")

        file_url = ""
        try:
            from app.services.content.gcs_store import gcs_content_store
            blob_name = f"pyq-uploads/{chapter_id}/{_uuid.uuid4().hex}/{fname}"
            bucket = gcs_content_store._get_bucket()
            blob = bucket.blob(blob_name)
            blob.upload_from_string(data, content_type=ct or "application/octet-stream")
            blob.make_public()
            file_url = blob.public_url
        except Exception as exc:
            logger.warning(f"GCS pyq upload failed: {exc}")

        pyq_id = _uuid.uuid4().hex
        doc = {
            "_id": pyq_id,
            "chapter_id": chapter_id,
            "subject_id": subject_id,
            "board_id": board_id,
            "class_id": class_id,
            "stream_id": stream_id,
            "exam_year": exam_year,
            "paper_type": paper_type,
            "filename": fname,
            "file_url": file_url,
            "is_image": is_image,
            "is_pdf": is_pdf,
            "is_text": False,
            "processing_status": "uploaded",
            "question_count": 0,
            "text_content": None,
            "seo_url": None,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
        await col.insert_one(doc)
        created_ids.append(pyq_id)

    return {"status": "ok", "ids": created_ids, "count": len(created_ids)}


# ── Processing ────────────────────────────────────────────────────────────────

class ProcessPyqRequest(BaseModel):
    pyq_id: str


async def _extract_text_from_pyq(doc: dict) -> str:
    """Fetch and extract text from a PYQ document's file_url.

    Only PDF files support automated text extraction.
    Images require OCR which is not yet implemented — they are left as-is.
    Text/markdown files are decoded directly.
    """
    # Already has text content — nothing to do
    if doc.get("text_content"):
        return doc["text_content"]

    file_url = doc.get("file_url", "")
    if not file_url:
        return ""

    is_pdf = doc.get("is_pdf", False)
    is_image = doc.get("is_image", False)

    # Images: OCR is not used — return empty string so the document is
    # stored as image-only and staff can still attach it as a PDF reference.
    if is_image:
        logger.info(
            f"Image-based PYQ {doc.get('_id')} — no OCR configured; stored as image reference only"
        )
        return ""

    if not is_pdf:
        # For text PYQs the text_content field should already be set
        return ""

    try:
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(file_url)
            r.raise_for_status()
        import pypdf
        from io import BytesIO
        reader = pypdf.PdfReader(BytesIO(r.content))
        pages = [p.extract_text() or "" for p in reader.pages]
        return "\n\n".join(pages).strip()
    except Exception as exc:
        logger.warning(f"PYQ text extraction failed for {doc.get('_id')}: {exc}")
        return ""


@router.post("/pyq/agentic-process")
async def process_one_pyq(request: Request, body: ProcessPyqRequest):
    """Extract text from a single PYQ file and update its record."""
    col = _col()
    doc = await col.find_one({"_id": body.pyq_id})
    if not doc:
        raise HTTPException(status_code=404, detail="PYQ not found")

    await col.update_one(
        {"_id": body.pyq_id},
        {"$set": {"processing_status": "ocr_running", "updated_at": datetime.now(timezone.utc)}},
    )
    try:
        text = await _extract_text_from_pyq(doc)
        q_count = text.count("?") if text else 0
        await col.update_one(
            {"_id": body.pyq_id},
            {"$set": {
                "processing_status": "ocr_done",
                "text_content": text,
                "question_count": q_count,
                "updated_at": datetime.now(timezone.utc),
            }},
        )
        return {"status": "ok", "question_count": q_count}
    except Exception as exc:
        await col.update_one(
            {"_id": body.pyq_id},
            {"$set": {"processing_status": "ocr_error", "updated_at": datetime.now(timezone.utc)}},
        )
        raise HTTPException(status_code=500, detail=str(exc))


class BatchProcessRequest(BaseModel):
    pyq_ids: list[str]


@router.post("/pyq/batch-process")
async def batch_process_pyqs(request: Request, body: BatchProcessRequest):
    """Process multiple PYQ files sequentially."""
    col = _col()
    succeeded = 0
    for pyq_id in body.pyq_ids:
        doc = await col.find_one({"_id": pyq_id})
        if not doc:
            continue
        await col.update_one(
            {"_id": pyq_id},
            {"$set": {"processing_status": "ocr_running", "updated_at": datetime.now(timezone.utc)}},
        )
        try:
            text = await _extract_text_from_pyq(doc)
            q_count = text.count("?") if text else 0
            await col.update_one(
                {"_id": pyq_id},
                {"$set": {
                    "processing_status": "ocr_done",
                    "text_content": text,
                    "question_count": q_count,
                    "updated_at": datetime.now(timezone.utc),
                }},
            )
            succeeded += 1
        except Exception as exc:
            logger.warning(f"Batch process failed for {pyq_id}: {exc}")
            await col.update_one(
                {"_id": pyq_id},
                {"$set": {"processing_status": "ocr_error", "updated_at": datetime.now(timezone.utc)}},
            )

    return {"succeeded": succeeded, "total": len(body.pyq_ids)}


# ── Delete ────────────────────────────────────────────────────────────────────

@router.delete("/pyq/{pyq_id}")
async def delete_pyq(request: Request, pyq_id: str):
    """Delete a PYQ record."""
    col = _col()
    result = await col.delete_one({"_id": pyq_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="PYQ not found")
    return {"status": "ok"}
