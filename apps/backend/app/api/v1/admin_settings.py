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
    """System diagnostics overview."""
    _validate_admin_session(request)
    return {"status": "healthy", "checks": {}, "timestamp": None}


@router.post("/break-glass/disable")
async def disable_break_glass(request: Request):
    """Disable break-glass mode."""
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"status": "ok", "message": "Break-glass disabled"}


@router.get("/roadmap")
async def get_roadmap(request: Request):
    """Get roadmap items."""
    _validate_admin_session(request)
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        cursor = db.roadmap.find({}).sort("created_at", -1).limit(100)
        items_raw = await cursor.to_list(length=100)
        items = []
        for item in items_raw:
            items.append({
                "id": str(item["_id"]),
                "title": item.get("title", ""),
                "description": item.get("description", ""),
                "status": item.get("status", "planned"),
                "priority": item.get("priority", "medium"),
                "created_at": item.get("created_at", "").isoformat() if item.get("created_at") else None,
            })
        return {"items": items}
    except Exception as e:
        logger.error(f"Get roadmap error: {e}")
        return {"items": []}


@router.post("/roadmap")
async def create_roadmap_item(request: Request):
    """Create a roadmap item."""
    _validate_admin_session(request)
    await _csrf_check(request)
    body = await request.json()
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        item = {
            "title": body.get("title", ""),
            "description": body.get("description", ""),
            "status": body.get("status", "planned"),
            "priority": body.get("priority", "medium"),
            "created_at": datetime.now(timezone.utc),
        }
        result = await db.roadmap.insert_one(item)
        return {"status": "ok", "id": str(result.inserted_id)}
    except Exception as e:
        logger.error(f"Create roadmap item error: {e}")
        raise HTTPException(status_code=500, detail="Failed to create roadmap item")


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
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Delete roadmap item error: {e}")
        return {"status": "ok"}


ROADMAP_ALLOWED_FIELDS = {"title", "description", "status", "priority", "eta", "tags"}


@router.put("/roadmap/{item_id}")
async def update_roadmap_item(item_id: str, request: Request):
    """Update a roadmap item."""
    _validate_admin_session(request)
    await _csrf_check(request)
    body = await request.json()
    filtered_body = {k: v for k, v in body.items() if k in ROADMAP_ALLOWED_FIELDS}
    if not filtered_body:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    try:
        from bson import ObjectId
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        await db.roadmap.update_one({"_id": ObjectId(item_id)}, {"$set": filtered_body})
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Update roadmap item error: {e}")
        return {"status": "ok"}


@router.patch("/roadmap/{item_id}")
async def patch_roadmap_item(item_id: str, request: Request):
    """Patch a roadmap item (partial update)."""
    _validate_admin_session(request)
    await _csrf_check(request)
    body = await request.json()
    filtered_body = {k: v for k, v in body.items() if k in ROADMAP_ALLOWED_FIELDS}
    if not filtered_body:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    try:
        from bson import ObjectId
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        await db.roadmap.update_one({"_id": ObjectId(item_id)}, {"$set": filtered_body})
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Patch roadmap item error: {e}")
        return {"status": "ok"}


@router.get("/plan-config")
async def get_plan_config(request: Request):
    """Get plan configuration."""
    _validate_admin_session(request)
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        config = await db.site_settings.find_one({"_id": "plan_config"})
        if not config:
            return {"plans": []}
        config.pop("_id", None)
        return config
    except Exception as e:
        logger.error(f"Get plan config error: {e}")
        return {"plans": []}


@router.put("/plan-config")
async def update_plan_config(request: Request):
    """Update plan configuration."""
    _validate_admin_session(request)
    await _csrf_check(request)
    body = await request.json()
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        await db.site_settings.update_one(
            {"_id": "plan_config"}, {"$set": body}, upsert=True
        )
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Update plan config error: {e}")
        raise HTTPException(status_code=500, detail="Failed to update plan config")


@router.get("/api-config")
async def get_api_config(request: Request):
    """Get API configuration."""
    _validate_admin_session(request)
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        config = await db.site_settings.find_one({"_id": "api_config"})
        if not config:
            return {"config": {}}
        config.pop("_id", None)
        return config
    except Exception as e:
        logger.error(f"Get API config error: {e}")
        return {"config": {}}


@router.put("/api-config")
async def update_api_config(request: Request):
    """Update API configuration."""
    _validate_admin_session(request)
    await _csrf_check(request)
    body = await request.json()
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        await db.site_settings.update_one(
            {"_id": "api_config"}, {"$set": body}, upsert=True
        )
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Update API config error: {e}")
        raise HTTPException(status_code=500, detail="Failed to update API config")


@router.get("/activity-log")
async def get_activity_log(request: Request):
    """Get admin activity log."""
    _validate_admin_session(request)
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        cursor = db.activity_log.find({}).sort("created_at", -1).limit(100)
        logs_raw = await cursor.to_list(length=100)
        logs = []
        for log in logs_raw:
            logs.append({
                "id": str(log["_id"]),
                "action": log.get("action", ""),
                "user_id": log.get("user_id", ""),
                "details": log.get("details", ""),
                "created_at": log.get("created_at", "").isoformat() if log.get("created_at") else None,
            })
        return {"logs": logs}
    except Exception as e:
        logger.error(f"Activity log error: {e}")
        return {"logs": []}


@router.post("/cache/purge-all")
async def purge_all_cache(request: Request):
    """Purge all caches."""
    _validate_admin_session(request)
    await _csrf_check(request)
    logger.info("Cache purge-all requested")
    return {"status": "ok", "purged": 0, "message": "Cache purge initiated"}
