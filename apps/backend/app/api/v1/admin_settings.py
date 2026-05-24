"""
Admin Settings Endpoints
Site settings, diagnostics, roadmap, plan config, API config, activity log, cache.
"""

from datetime import datetime, timezone
import sys

from fastapi import APIRouter, Request, HTTPException
import logging

from app.api.v1.admin import _validate_admin_session, _csrf_check

logger = logging.getLogger(__name__)

router = APIRouter()

_DEFAULT_SETTINGS = {
    "registrations_open": True,
    "maintenance_mode": False,
    "app_name": "Syrabit.ai",
    "tagline": "AI-Powered AHSEC Exam Prep",
    "crawl_coverage_red": 30,
    "crawl_coverage_yellow": 50,
    "bot_missing_days": 3,
}


@router.get("/settings")
async def get_settings(request: Request):
    """Read site settings from collection or return defaults."""
    _validate_admin_session(request)
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        doc = await db.site_settings.find_one({"_id": "main"})
        if doc:
            doc.pop("_id", None)
            return doc
        return _DEFAULT_SETTINGS
    except Exception as e:
        logger.error(f"Error reading settings: {e}")
        return _DEFAULT_SETTINGS


@router.patch("/settings")
async def update_settings(request: Request):
    """Update site settings (upsert)."""
    _validate_admin_session(request)
    await _csrf_check(request)
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings

        body = await request.json()

        # Allow-list of permitted fields
        allowed_fields = {
            "registrations_open",
            "maintenance_mode",
            "app_name",
            "tagline",
            "crawl_coverage_red",
            "crawl_coverage_yellow",
            "bot_missing_days",
            "support_email",
            "analytics_enabled",
            "theme",
            "logo_url",
        }
        body = {k: v for k, v in body.items() if k in allowed_fields}
        if not body:
            raise HTTPException(status_code=400, detail="No valid fields provided")

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        await db.site_settings.update_one(
            {"_id": "main"},
            {"$set": body},
            upsert=True,
        )
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating settings: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/diagnostics")
async def diagnostics(request: Request):
    """Return system diagnostics."""
    _validate_admin_session(request)
    return {
        "python_version": sys.version,
        "platform": sys.platform,
        "uptime": "placeholder",
        "source": "placeholder",
    }


@router.post("/break-glass/disable")
async def break_glass_disable(request: Request):
    """Placeholder break-glass disable."""
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"status": "ok", "source": "placeholder"}


@router.get("/roadmap")
async def get_roadmap(request: Request):
    """List roadmap items."""
    _validate_admin_session(request)
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        docs = await db.roadmap.find().sort("created_at", -1).to_list(length=100)
        for doc in docs:
            doc["_id"] = str(doc["_id"])
        return {"items": docs}
    except Exception as e:
        logger.error(f"Error fetching roadmap: {e}")
        return {"items": []}


@router.post("/roadmap")
async def create_roadmap_item(request: Request):
    """Create a roadmap item."""
    _validate_admin_session(request)
    await _csrf_check(request)
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings

        body = await request.json()
        body["created_at"] = datetime.now(timezone.utc)
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        result = await db.roadmap.insert_one(body)
        return {"status": "ok", "id": str(result.inserted_id)}
    except Exception as e:
        logger.error(f"Error creating roadmap item: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/roadmap/{item_id}")
async def update_roadmap_item(item_id: str, request: Request):
    """Update a roadmap item."""
    _validate_admin_session(request)
    await _csrf_check(request)
    try:
        from bson import ObjectId
        from app.db.mongo import get_mongo_client
        from app.config import settings

        body = await request.json()

        # Allow-list of permitted fields
        allowed_fields = {
            "title",
            "description",
            "status",
            "priority",
            "eta",
            "category",
        }
        body = {k: v for k, v in body.items() if k in allowed_fields}
        if not body:
            raise HTTPException(status_code=400, detail="No valid fields provided")

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        result = await db.roadmap.update_one(
            {"_id": ObjectId(item_id)},
            {"$set": body},
        )
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Item not found")
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating roadmap item: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/roadmap/{item_id}")
async def delete_roadmap_item(item_id: str, request: Request):
    """Delete a roadmap item."""
    _validate_admin_session(request)
    await _csrf_check(request)
    try:
        from bson import ObjectId
        from app.db.mongo import get_mongo_client
        from app.config import settings

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        result = await db.roadmap.delete_one({"_id": ObjectId(item_id)})
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Item not found")
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting roadmap item: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/plan-config")
async def get_plan_config(request: Request):
    """Return plan tier config from collection or defaults."""
    _validate_admin_session(request)
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        doc = await db.plan_config.find_one({"_id": "main"})
        if doc:
            doc.pop("_id", None)
            return doc
        return {
            "free": {"message_limit": 30, "price": 0},
            "pro": {"message_limit": 999999, "price": 299},
        }
    except Exception:
        return {
            "free": {"message_limit": 30, "price": 0},
            "pro": {"message_limit": 999999, "price": 299},
        }


@router.put("/plan-config")
async def update_plan_config(request: Request):
    """Update plan config."""
    _validate_admin_session(request)
    await _csrf_check(request)
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings

        body = await request.json()

        # Allow-list of permitted fields
        allowed_fields = {"free", "pro", "starter"}
        body = {k: v for k, v in body.items() if k in allowed_fields}
        if not body:
            raise HTTPException(status_code=400, detail="No valid fields provided")

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        await db.plan_config.update_one(
            {"_id": "main"},
            {"$set": body},
            upsert=True,
        )
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating plan config: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/api-config")
async def get_api_config(request: Request):
    """Return API provider config (placeholder)."""
    _validate_admin_session(request)
    return {
        "vertex_ai": {"enabled": True, "model": "gemini-1.5-pro"},
        "sarvam_ai": {"enabled": True, "model": "openhathi-7b"},
        "source": "placeholder",
    }


@router.put("/api-config")
async def update_api_config(request: Request):
    """Update API config (placeholder)."""
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"status": "ok", "source": "placeholder"}


@router.get("/activity-log")
async def activity_log(request: Request):
    """Return recent admin activity from audit collection."""
    _validate_admin_session(request)
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        docs = await db.audit_log.find().sort("timestamp", -1).to_list(length=50)
        for doc in docs:
            doc["_id"] = str(doc["_id"])
        return {"entries": docs}
    except Exception as e:
        logger.error(f"Error fetching activity log: {e}")
        return {"entries": []}


@router.post("/cache/purge-all")
async def cache_purge_all(request: Request):
    """Placeholder cache purge."""
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"status": "ok", "purged": True, "source": "placeholder"}
