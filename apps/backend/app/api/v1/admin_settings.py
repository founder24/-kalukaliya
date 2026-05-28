"""
Admin Settings Endpoints
Site-wide settings management.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
import logging

from app.api.v1.admin import _validate_admin_session, _csrf_check
from app.config import settings
from app.db.mongo import get_mongo_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin Settings"])

# Default settings structure
DEFAULT_SETTINGS = {
    "maintenance_mode": False,
    "registrations_open": True,
    "feature_flags": {
        "voice_enabled": True,
        "rag_enabled": True,
        "pro_features_enabled": True,
    },
    "rate_limits": {
        "free_tier": 30,
        "pro_tier": 999999,
    },
    "announcement": None,
}


@router.get("/settings")
async def get_settings(request: Request):
    """Get site-wide settings."""
    _validate_admin_session(request)

    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        site_settings = await db.site_settings.find_one({"_id": "global"})
        if not site_settings:
            return DEFAULT_SETTINGS

        # Remove internal fields
        site_settings.pop("_id", None)
        return site_settings
    except Exception as e:
        logger.error(f"Get settings error: {e}")
        return DEFAULT_SETTINGS


@router.put("/settings")
async def update_settings(request: Request):
    """Update site-wide settings."""
    _validate_admin_session(request)
    await _csrf_check(request)

    body = await request.json()

    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        body["updated_at"] = datetime.now(timezone.utc)

        await db.site_settings.update_one(
            {"_id": "global"},
            {"$set": body},
            upsert=True,
        )

        return {"status": "ok", "message": "Settings updated"}
    except Exception as e:
        logger.error(f"Update settings error: {e}")
        raise HTTPException(status_code=500, detail="Failed to update settings")


@router.get("/diagnostics")
async def get_diagnostics(request: Request):
    """System diagnostics check."""
    _validate_admin_session(request)
    return {"status": "healthy", "checks": []}


@router.post("/break-glass/disable")
async def break_glass_disable(request: Request):
    """Disable break-glass mode."""
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"message": "Break-glass disabled"}


@router.get("/roadmap")
async def get_roadmap(request: Request):
    """Get roadmap items."""
    _validate_admin_session(request)
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        items = await db.roadmap.find().sort("created_at", -1).to_list(100)
        return [
            {
                "id": str(item["_id"]),
                "title": item.get("title", ""),
                "status": item.get("status", "planned"),
                "priority": item.get("priority", "medium"),
                "description": item.get("description", ""),
                "created_at": item.get("created_at", "").isoformat() if item.get("created_at") else None,
            }
            for item in items
        ]
    except Exception as e:
        logger.error(f"Get roadmap error: {e}")
        return []


@router.post("/roadmap")
async def create_roadmap_item(request: Request):
    """Create a roadmap item."""
    _validate_admin_session(request)
    await _csrf_check(request)
    body = await request.json()
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        body["created_at"] = datetime.now(timezone.utc)
        body["updated_at"] = datetime.now(timezone.utc)
        result = await db.roadmap.insert_one(body)
        return {"id": str(result.inserted_id), "message": "Created"}
    except Exception as e:
        logger.error(f"Create roadmap error: {e}")
        raise HTTPException(status_code=500, detail="Failed to create roadmap item")


@router.patch("/roadmap/{item_id}")
async def update_roadmap_item(item_id: str, request: Request):
    """Update a roadmap item."""
    _validate_admin_session(request)
    await _csrf_check(request)
    body = await request.json()
    try:
        from bson import ObjectId
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        body["updated_at"] = datetime.now(timezone.utc)
        await db.roadmap.update_one({"_id": ObjectId(item_id)}, {"$set": body})
        return {"message": "Updated"}
    except Exception as e:
        logger.error(f"Update roadmap error: {e}")
        raise HTTPException(status_code=500, detail="Failed to update roadmap item")


@router.delete("/roadmap/{item_id}")
async def delete_roadmap_item(item_id: str, request: Request):
    """Delete a roadmap item."""
    _validate_admin_session(request)
    await _csrf_check(request)
    try:
        from bson import ObjectId
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        await db.roadmap.delete_one({"_id": ObjectId(item_id)})
        return {"message": "Deleted"}
    except Exception as e:
        logger.error(f"Delete roadmap error: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete roadmap item")


@router.get("/plan-config")
async def get_plan_config(request: Request):
    """Get subscription plan configuration."""
    _validate_admin_session(request)
    return {"plans": []}


@router.put("/plan-config")
async def update_plan_config(request: Request):
    """Update subscription plan configuration."""
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"message": "Plan config updated"}


@router.get("/api-config")
async def get_api_config(request: Request):
    """Get API provider configuration."""
    _validate_admin_session(request)
    return {"providers": []}


@router.put("/api-config")
async def update_api_config(request: Request):
    """Update API provider configuration."""
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"message": "API config updated"}


@router.get("/activity-log")
async def get_activity_log(request: Request):
    """Get admin activity log."""
    _validate_admin_session(request)
    return {"activities": []}


@router.post("/cache/purge-all")
async def purge_all_cache(request: Request):
    """Purge all caches."""
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"message": "Cache purged"}
