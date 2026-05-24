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
