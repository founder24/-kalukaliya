"""Public API for the Documents/Library feature — read-only, published docs only."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from app.api.v1.auth import get_current_user
from app.config import settings
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Documents"])


@router.get("/documents")
async def list_published_documents(
    _user: User = Depends(get_current_user),
    q: Optional[str] = Query(None, description="Search by title"),
    category: Optional[str] = Query(None),
    sort: str = Query("newest", description="newest | oldest | title_asc | title_desc"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """Return all published documents for the public Library page."""
    from app.db.mongo import get_mongo_client

    client = get_mongo_client()
    col = client[settings.MONGODB_DB_NAME]["library_documents"]

    query: dict = {"status": "published"}
    if q:
        query["title"] = {"$regex": q, "$options": "i"}
    if category:
        query["category"] = category

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
                "pdf_url": d.get("pdf_url", ""),
                "pdf_filename": d.get("pdf_filename", ""),
                "pdf_size_bytes": d.get("pdf_size_bytes", 0),
                "cover_url": d.get("cover_url"),
                "created_at": d["created_at"].isoformat() if d.get("created_at") else None,
            }
            for d in docs
        ],
    }


@router.get("/documents/categories")
async def list_document_categories(
    _user: User = Depends(get_current_user),
):
    """Return distinct categories that have at least one published document."""
    from app.db.mongo import get_mongo_client

    client = get_mongo_client()
    col = client[settings.MONGODB_DB_NAME]["library_documents"]
    cats = await col.distinct("category", {"status": "published", "category": {"$ne": None}})
    return {"categories": sorted(c for c in cats if c)}


@router.get("/documents/{doc_id}/download")
async def get_document_download_url(
    doc_id: str,
    _user: User = Depends(get_current_user),
):
    """Return a short-lived signed URL for downloading a published document PDF."""
    from bson import ObjectId
    from app.db.mongo import get_mongo_client

    client = get_mongo_client()
    col = client[settings.MONGODB_DB_NAME]["library_documents"]

    try:
        oid = ObjectId(doc_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Document not found")

    doc = await col.find_one({"_id": oid, "status": "published"})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    pdf_url: str = doc.get("pdf_url", "")
    filename: str = doc.get("pdf_filename", "document.pdf")

    # Try to generate a short-lived signed URL (requires GCS SA credentials).
    if "documents/pdf/" in pdf_url:
        try:
            from app.services.content.gcs_store import gcs_content_store
            bucket = gcs_content_store._get_bucket()
            # Extract blob_name: everything from "documents/pdf/" onwards
            idx = pdf_url.index("documents/pdf/")
            blob_name = pdf_url[idx:]
            blob = bucket.blob(blob_name)
            signed_url = blob.generate_signed_url(
                expiration=timedelta(minutes=15),
                method="GET",
                version="v4",
            )
            return {"download_url": signed_url, "filename": filename, "expires_in": 900}
        except Exception as exc:
            logger.warning(f"Signed URL generation failed for doc {doc_id}: {exc}")

    # Fallback: return stored URL (dev mode or if GCS signing unavailable)
    return {"download_url": pdf_url, "filename": filename, "expires_in": None}


@router.get("/documents/{doc_id}")
async def get_published_document(
    doc_id: str,
    _user: User = Depends(get_current_user),
):
    """Return a single published document by ID."""
    from bson import ObjectId
    from app.db.mongo import get_mongo_client

    client = get_mongo_client()
    col = client[settings.MONGODB_DB_NAME]["library_documents"]

    try:
        oid = ObjectId(doc_id)
    except Exception:
        raise HTTPException(status_code=404, detail="Document not found")

    doc = await col.find_one({"_id": oid, "status": "published"})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    return {
        "id": str(doc["_id"]),
        "title": doc.get("title", ""),
        "description": doc.get("description"),
        "category": doc.get("category"),
        "pdf_url": doc.get("pdf_url", ""),
        "pdf_filename": doc.get("pdf_filename", ""),
        "pdf_size_bytes": doc.get("pdf_size_bytes", 0),
        "cover_url": doc.get("cover_url"),
        "created_at": doc["created_at"].isoformat() if doc.get("created_at") else None,
    }
