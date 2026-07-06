"""Admin API for Document/Library management."""
from __future__ import annotations

import uuid as _uuid
import logging
from datetime import datetime, timezone
from typing import Optional

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel

from app.api.v1.admin import require_admin_session, csrf_guard
from app.config import settings
from app.models.document import LibraryDocument

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Admin Documents"],
    dependencies=[Depends(require_admin_session), Depends(csrf_guard)],
)

MAX_PDF_BYTES = 50 * 1024 * 1024   # 50 MB
MAX_COVER_BYTES = 5 * 1024 * 1024  # 5 MB


# ── Pydantic request/response models ────────────────────────────────────────

class DocumentCreate(BaseModel):
    title: str
    description: Optional[str] = None
    category: Optional[str] = None
    status: str = "draft"
    pdf_url: str
    pdf_filename: str
    pdf_size_bytes: int
    cover_url: Optional[str] = None


class DocumentUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    status: Optional[str] = None
    pdf_url: Optional[str] = None
    pdf_filename: Optional[str] = None
    pdf_size_bytes: Optional[int] = None
    cover_url: Optional[str] = None


# ── Helper ───────────────────────────────────────────────────────────────────

def _doc_to_dict(doc: LibraryDocument) -> dict:
    return {
        "id": str(doc.id),
        "title": doc.title,
        "description": doc.description,
        "category": doc.category,
        "status": doc.status,
        "pdf_url": doc.pdf_url,
        "pdf_filename": doc.pdf_filename,
        "pdf_size_bytes": doc.pdf_size_bytes,
        "cover_url": doc.cover_url,
        "created_by": doc.created_by,
        "created_at": doc.created_at.isoformat(),
        "updated_at": doc.updated_at.isoformat(),
    }


async def _gcs_delete_blob(blob_name: str) -> None:
    """Best-effort GCS blob deletion (non-fatal if it fails)."""
    try:
        from app.services.content.gcs_store import gcs_content_store
        bucket = gcs_content_store._get_bucket()
        blob = bucket.blob(blob_name)
        blob.delete()
    except Exception as exc:
        logger.warning(f"GCS blob deletion failed for {blob_name!r}: {exc}")


def _blob_name_from_url(url: str, prefix: str) -> Optional[str]:
    """Extract the GCS blob name from a public URL, if the prefix matches."""
    try:
        idx = url.index(prefix)
        return url[idx:]
    except ValueError:
        return None


# ── File upload endpoints ────────────────────────────────────────────────────

@router.post("/documents/upload-pdf")
async def upload_pdf(
    request: Request,
    file: UploadFile = File(...),
    _admin: dict = Depends(require_admin_session),
):
    """Upload a PDF file to GCS and return its public URL, filename, and size."""
    if file.content_type not in ("application/pdf", "application/x-pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")

    data = await file.read()
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(status_code=400, detail="File too large (max 50 MB)")

    fname = file.filename or "document.pdf"
    blob_name = (
        f"documents/pdf/{datetime.now(timezone.utc).strftime('%Y/%m')}"
        f"/{_uuid.uuid4().hex}.pdf"
    )
    try:
        from app.services.content.gcs_store import gcs_content_store
        bucket = gcs_content_store._get_bucket()
        blob = bucket.blob(blob_name)
        blob.upload_from_string(data, content_type="application/pdf")
        blob.make_public()
        return {
            "url": blob.public_url,
            "filename": fname,
            "size_bytes": len(data),
            "blob_name": blob_name,
        }
    except Exception as exc:
        logger.error(f"GCS PDF upload failed: {exc}")
        raise HTTPException(status_code=500, detail="File upload failed. Check GCS configuration.")


@router.post("/documents/upload-cover")
async def upload_cover(
    request: Request,
    file: UploadFile = File(...),
    _admin: dict = Depends(require_admin_session),
):
    """Upload a cover image for a document."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image files are allowed")

    data = await file.read()
    if len(data) > MAX_COVER_BYTES:
        raise HTTPException(status_code=400, detail="Cover image too large (max 5 MB)")

    fname = file.filename or "cover.jpg"
    ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else "jpg"
    blob_name = (
        f"documents/covers/{datetime.now(timezone.utc).strftime('%Y/%m')}"
        f"/{_uuid.uuid4().hex}.{ext}"
    )
    try:
        from app.services.content.gcs_store import gcs_content_store
        bucket = gcs_content_store._get_bucket()
        blob = bucket.blob(blob_name)
        blob.upload_from_string(data, content_type=file.content_type)
        blob.make_public()
        return {"url": blob.public_url, "blob_name": blob_name}
    except Exception as exc:
        logger.error(f"GCS cover upload failed: {exc}")
        raise HTTPException(status_code=500, detail="Cover upload failed. Check GCS configuration.")


# ── CRUD endpoints ───────────────────────────────────────────────────────────

@router.get("/documents")
async def list_documents(
    _admin: dict = Depends(require_admin_session),
    q: Optional[str] = Query(None, description="Search by title"),
    category: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="published | draft | all"),
    sort: str = Query("newest", description="newest | oldest | title_asc | title_desc"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List all documents with optional search, filter, and sort."""
    from motor.motor_asyncio import AsyncIOMotorCollection
    from app.db.mongo import get_mongo_client

    client = get_mongo_client()
    col: AsyncIOMotorCollection = client[settings.MONGODB_DB_NAME]["library_documents"]

    query: dict = {}
    if q:
        query["title"] = {"$regex": q, "$options": "i"}
    if category:
        query["category"] = category
    if status and status != "all":
        query["status"] = status

    sort_map = {
        "newest": [("created_at", -1)],
        "oldest": [("created_at", 1)],
        "title_asc": [("title", 1)],
        "title_desc": [("title", -1)],
    }
    sort_spec = sort_map.get(sort, [("created_at", -1)])

    total = await col.count_documents(query)
    cursor = col.find(query).sort(sort_spec).skip(offset).limit(limit)
    docs = await cursor.to_list(length=limit)

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": [
            {
                "id": str(d["_id"]),
                "title": d.get("title", ""),
                "description": d.get("description"),
                "category": d.get("category"),
                "status": d.get("status", "draft"),
                "pdf_url": d.get("pdf_url", ""),
                "pdf_filename": d.get("pdf_filename", ""),
                "pdf_size_bytes": d.get("pdf_size_bytes", 0),
                "cover_url": d.get("cover_url"),
                "created_by": d.get("created_by", ""),
                "created_at": d["created_at"].isoformat() if d.get("created_at") else None,
                "updated_at": d["updated_at"].isoformat() if d.get("updated_at") else None,
            }
            for d in docs
        ],
    }


@router.post("/documents")
async def create_document(
    body: DocumentCreate,
    _admin: dict = Depends(require_admin_session),
):
    """Create a new document record."""
    if body.status not in ("published", "draft"):
        raise HTTPException(status_code=400, detail="status must be 'published' or 'draft'")

    doc = LibraryDocument(
        title=body.title.strip(),
        description=body.description,
        category=body.category,
        status=body.status,
        pdf_url=body.pdf_url,
        pdf_filename=body.pdf_filename,
        pdf_size_bytes=body.pdf_size_bytes,
        cover_url=body.cover_url,
        created_by=_admin.get("email", "admin"),
    )
    await doc.insert()
    return _doc_to_dict(doc)


@router.get("/documents/{doc_id}")
async def get_document(
    doc_id: str,
    _admin: dict = Depends(require_admin_session),
):
    """Get a single document by ID."""
    try:
        doc = await LibraryDocument.get(PydanticObjectId(doc_id))
    except Exception:
        raise HTTPException(status_code=404, detail="Document not found")
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return _doc_to_dict(doc)


@router.put("/documents/{doc_id}")
async def update_document(
    doc_id: str,
    body: DocumentUpdate,
    _admin: dict = Depends(require_admin_session),
):
    """Update document metadata (title, description, category, status, cover, PDF replacement)."""
    try:
        doc = await LibraryDocument.get(PydanticObjectId(doc_id))
    except Exception:
        raise HTTPException(status_code=404, detail="Document not found")
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    changes = body.model_dump(exclude_none=True)
    if "status" in changes and changes["status"] not in ("published", "draft"):
        raise HTTPException(status_code=400, detail="status must be 'published' or 'draft'")

    for field, value in changes.items():
        setattr(doc, field, value)
    doc.updated_at = datetime.now(timezone.utc)
    await doc.save()
    return _doc_to_dict(doc)


@router.delete("/documents/{doc_id}")
async def delete_document(
    doc_id: str,
    _admin: dict = Depends(require_admin_session),
):
    """Delete a document and attempt to remove its files from GCS."""
    try:
        doc = await LibraryDocument.get(PydanticObjectId(doc_id))
    except Exception:
        raise HTTPException(status_code=404, detail="Document not found")
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Best-effort GCS cleanup
    if doc.pdf_url and "documents/pdf/" in doc.pdf_url:
        blob_name = _blob_name_from_url(doc.pdf_url, "documents/pdf/")
        if blob_name:
            await _gcs_delete_blob(blob_name)
    if doc.cover_url and "documents/covers/" in doc.cover_url:
        blob_name = _blob_name_from_url(doc.cover_url, "documents/covers/")
        if blob_name:
            await _gcs_delete_blob(blob_name)

    await doc.delete()
    return {"ok": True, "id": doc_id}


@router.get("/documents/categories/list")
async def list_categories(
    _admin: dict = Depends(require_admin_session),
):
    """Return distinct category values for filter dropdowns."""
    from app.db.mongo import get_mongo_client
    client = get_mongo_client()
    col = client[settings.MONGODB_DB_NAME]["library_documents"]
    cats = await col.distinct("category", {"category": {"$ne": None}})
    return {"categories": sorted(c for c in cats if c)}
