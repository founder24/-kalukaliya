"""
Admin Content Endpoints
Manage educational content subjects.
"""

from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request
import logging

from app.api.v1.admin import _validate_admin_session, _csrf_check
from app.config import settings
from app.db.mongo import get_mongo_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin Content"])


@router.get("/content/draft-served-subjects")
async def get_draft_served_subjects(request: Request):
    """Get subjects with status='draft' that are being served."""
    _validate_admin_session(request)

    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        # Check if subjects collection exists
        subjects = await db.subjects.find({"status": "draft"}).to_list(100)

        result = []
        for s in subjects:
            result.append({
                "id": str(s["_id"]),
                "name": s.get("name"),
                "status": s.get("status"),
                "category": s.get("category"),
                "created_at": s.get("created_at", "").isoformat() if s.get("created_at") else None,
                "updated_at": s.get("updated_at", "").isoformat() if s.get("updated_at") else None,
            })

        return {"subjects": result, "total": len(result)}
    except Exception as e:
        logger.error(f"Get draft subjects error: {e}")
        return {"subjects": [], "total": 0}


@router.patch("/content/subjects/{subject_id}")
async def update_subject(request: Request, subject_id: str):
    """Update subject fields."""
    _validate_admin_session(request)
    await _csrf_check(request)

    body = await request.json()

    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        # Build update from allowed fields
        allowed_fields = ["name", "status", "category", "description", "grade_level"]
        update_fields = {}
        for field in allowed_fields:
            if field in body:
                update_fields[field] = body[field]

        if not update_fields:
            raise HTTPException(status_code=400, detail="No valid fields to update")

        update_fields["updated_at"] = datetime.now(timezone.utc)

        result = await db.subjects.update_one(
            {"_id": ObjectId(subject_id)},
            {"$set": update_fields},
        )
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Subject not found")

        return {"status": "ok", "subject_id": subject_id, "updated_fields": list(update_fields.keys())}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Update subject error: {e}")
        raise HTTPException(status_code=500, detail="Failed to update subject")
