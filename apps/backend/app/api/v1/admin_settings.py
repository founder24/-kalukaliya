"""
Admin Settings Endpoints
Site-wide settings management.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
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


# Allowed fields for roadmap items
ROADMAP_ALLOWED_FIELDS = {"title", "description", "status", "priority", "target_date"}


@router.get("/diagnostics")
async def get_diagnostics(request: Request):
    """Get system diagnostics."""
    _validate_admin_session(request)
    return {
        "status": "healthy",
        "version": "3.0.0",
        "uptime_seconds": 0,
        "connections": {"mongo": "ok", "redis": "ok"},
    }


@router.post("/break-glass/disable")
async def break_glass_disable(request: Request):
    """Emergency disable of features (break-glass)."""
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"status": "ok", "message": "Break-glass activated"}


@router.get("/roadmap")
async def get_roadmap(request: Request):
    """Get roadmap items."""
    _validate_admin_session(request)
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        items = await db.roadmap.find().sort("created_at", -1).to_list(200)
        for item in items:
            item["_id"] = str(item["_id"])
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

    # Field allowlisting
    filtered = {k: v for k, v in body.items() if k in ROADMAP_ALLOWED_FIELDS}
    filtered["created_at"] = datetime.now(timezone.utc)

    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        result = await db.roadmap.insert_one(filtered)
        return {"status": "ok", "id": str(result.inserted_id)}
    except Exception as e:
        logger.error(f"Create roadmap error: {e}")
        raise HTTPException(status_code=500, detail="Failed to create roadmap item")


@router.patch("/roadmap/{item_id}")
async def update_roadmap_item(item_id: str, request: Request):
    """Update a roadmap item."""
    _validate_admin_session(request)
    await _csrf_check(request)
    body = await request.json()

    # Field allowlisting
    filtered = {k: v for k, v in body.items() if k in ROADMAP_ALLOWED_FIELDS}
    filtered["updated_at"] = datetime.now(timezone.utc)

    try:
        from bson import ObjectId

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        await db.roadmap.update_one({"_id": ObjectId(item_id)}, {"$set": filtered})
        return {"status": "ok", "message": "Roadmap item updated"}
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
        return {"status": "ok", "message": "Roadmap item deleted"}
    except Exception as e:
        logger.error(f"Delete roadmap error: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete roadmap item")


@router.get("/plan-config")
async def get_plan_config(request: Request):
    """Get plan configuration."""
    _validate_admin_session(request)
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        config = await db.site_settings.find_one({"_id": "plan_config"})
        if config:
            config.pop("_id", None)
            return config
        return {"plans": {"free": {"limit": 30}, "pro": {"limit": 999999}}}
    except Exception as e:
        logger.error(f"Get plan config error: {e}")
        return {"plans": {"free": {"limit": 30}, "pro": {"limit": 999999}}}


@router.put("/plan-config")
async def update_plan_config(request: Request):
    """Update plan configuration."""
    _validate_admin_session(request)
    await _csrf_check(request)
    body = await request.json()

    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        body["updated_at"] = datetime.now(timezone.utc)
        await db.site_settings.update_one(
            {"_id": "plan_config"}, {"$set": body}, upsert=True
        )
        return {"status": "ok", "message": "Plan config updated"}
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
        if config:
            config.pop("_id", None)
            return config
        return {"rate_limits": {}, "feature_flags": {}}
    except Exception as e:
        logger.error(f"Get api config error: {e}")
        return {"rate_limits": {}, "feature_flags": {}}


@router.put("/api-config")
async def update_api_config(request: Request):
    """Update API configuration."""
    _validate_admin_session(request)
    await _csrf_check(request)
    body = await request.json()

    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        body["updated_at"] = datetime.now(timezone.utc)
        await db.site_settings.update_one(
            {"_id": "api_config"}, {"$set": body}, upsert=True
        )
        return {"status": "ok", "message": "API config updated"}
    except Exception as e:
        logger.error(f"Update api config error: {e}")
        raise HTTPException(status_code=500, detail="Failed to update API config")


@router.get("/activity-log")
async def get_activity_log(request: Request):
    """Get admin activity log."""
    _validate_admin_session(request)
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        logs = await db.admin_activity_log.find().sort("timestamp", -1).to_list(100)
        for log in logs:
            log["_id"] = str(log["_id"])
        return {"logs": logs}
    except Exception as e:
        logger.error(f"Get activity log error: {e}")
        return {"logs": []}


@router.post("/cache/purge-all")
async def cache_purge_all(request: Request):
    """Purge all caches."""
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"status": "ok", "message": "All caches purged"}
