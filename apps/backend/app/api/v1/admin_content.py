"""
Admin Content Management Endpoints
Manage subjects, chapters, sitemap, and version history.
"""
from fastapi import APIRouter, Request, HTTPException
import logging

from app.api.v1.admin import _validate_admin_session, _csrf_check

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/content/draft-served-subjects")
async def draft_served_subjects(request: Request):
    """Query subjects collection for status=draft docs."""
    _validate_admin_session(request)
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        docs = await db.subjects.find({"status": "draft"}).to_list(length=100)
        for doc in docs:
            doc["_id"] = str(doc["_id"])
        return {"subjects": docs}
    except Exception as e:
        logger.error(f"Error fetching draft subjects: {e}")
        return {"subjects": []}


@router.patch("/content/subjects/{subject_id}")
async def update_subject(subject_id: str, request: Request):
    """Update subject document fields."""
    _validate_admin_session(request)
    await _csrf_check(request)
    try:
        from bson import ObjectId
        from app.db.mongo import get_mongo_client
        from app.config import settings

        body = await request.json()
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        result = await db.subjects.update_one(
            {"_id": ObjectId(subject_id)},
            {"$set": body},
        )
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Subject not found")
        return {"status": "ok", "modified_count": result.modified_count}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating subject: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/content/regenerate-sitemap")
async def regenerate_sitemap(request: Request):
    """Placeholder: regenerate sitemap."""
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"status": "ok", "message": "Sitemap regeneration triggered", "source": "placeholder"}


@router.post("/content/auto-heal")
async def auto_heal(request: Request):
    """Placeholder: auto-heal content issues."""
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"status": "ok", "message": "Auto-heal triggered", "source": "placeholder"}


@router.get("/content/version-history/{chapter_id}")
async def version_history(chapter_id: str, request: Request):
    """Query version history for a chapter."""
    _validate_admin_session(request)
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        docs = await db.version_history.find({"chapter_id": chapter_id}).sort("created_at", -1).to_list(length=50)
        for doc in docs:
            doc["_id"] = str(doc["_id"])
        return {"versions": docs}
    except Exception as e:
        logger.error(f"Error fetching version history: {e}")
        return {"versions": []}
