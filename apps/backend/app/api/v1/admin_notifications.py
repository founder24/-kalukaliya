"""
Admin Notifications Endpoints
Notification CRUD, triggers, preferences, push stats, IndexNow.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Request, HTTPException
import logging

from app.api.v1.admin import _validate_admin_session, _csrf_check

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/notifications")
async def list_notifications(request: Request):
    """List notifications from collection."""
    _validate_admin_session(request)
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        docs = await db.notifications.find().sort("created_at", -1).to_list(length=100)
        for doc in docs:
            doc["_id"] = str(doc["_id"])
        return {"notifications": docs}
    except Exception as e:
        logger.error(f"Error listing notifications: {e}")
        return {"notifications": []}


@router.post("/notifications")
async def create_notification(request: Request):
    """Create a notification."""
    _validate_admin_session(request)
    await _csrf_check(request)
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings

        body = await request.json()
        body["created_at"] = datetime.now(timezone.utc)
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        result = await db.notifications.insert_one(body)
        return {"status": "ok", "id": str(result.inserted_id)}
    except Exception as e:
        logger.error(f"Error creating notification: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/notifications/triggers")
async def list_triggers(request: Request):
    """List automation triggers."""
    _validate_admin_session(request)
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        docs = await db.notification_triggers.find().to_list(length=100)
        for doc in docs:
            doc["_id"] = str(doc["_id"])
        return {"triggers": docs}
    except Exception as e:
        logger.error(f"Error listing triggers: {e}")
        return {"triggers": []}


@router.post("/notifications/triggers")
async def create_trigger(request: Request):
    """Create automation trigger."""
    _validate_admin_session(request)
    await _csrf_check(request)
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings

        body = await request.json()
        body["created_at"] = datetime.now(timezone.utc)
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        result = await db.notification_triggers.insert_one(body)
        return {"status": "ok", "id": str(result.inserted_id)}
    except Exception as e:
        logger.error(f"Error creating trigger: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/notifications/triggers/{trigger_id}")
async def update_trigger(trigger_id: str, request: Request):
    """Update automation trigger."""
    _validate_admin_session(request)
    await _csrf_check(request)
    try:
        from bson import ObjectId
        from app.db.mongo import get_mongo_client
        from app.config import settings

        body = await request.json()

        # Allow-list of permitted fields
        allowed_fields = {
            "name",
            "event_type",
            "condition",
            "action",
            "enabled",
            "cooldown_minutes",
            "description",
            "priority",
        }
        body = {k: v for k, v in body.items() if k in allowed_fields}
        if not body:
            raise HTTPException(status_code=400, detail="No valid fields provided")

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        result = await db.notification_triggers.update_one(
            {"_id": ObjectId(trigger_id)}, {"$set": body}
        )
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Trigger not found")
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating trigger: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/notifications/triggers/{trigger_id}")
async def delete_trigger(trigger_id: str, request: Request):
    """Delete automation trigger."""
    _validate_admin_session(request)
    await _csrf_check(request)
    try:
        from bson import ObjectId
        from app.db.mongo import get_mongo_client
        from app.config import settings

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        result = await db.notification_triggers.delete_one(
            {"_id": ObjectId(trigger_id)}
        )
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Trigger not found")
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting trigger: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/notification-prefs")
async def get_notification_prefs(request: Request):
    """Return admin notification preferences."""
    _validate_admin_session(request)
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        doc = await db.notification_prefs.find_one({"_id": "admin"})
        if doc:
            doc.pop("_id", None)
            return doc
        return {"email": True, "push": True, "slack": False}
    except Exception:
        return {"email": True, "push": True, "slack": False}


@router.put("/notification-prefs")
async def update_notification_prefs(request: Request):
    """Update notification preferences."""
    _validate_admin_session(request)
    await _csrf_check(request)
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings

        body = await request.json()

        # Allow-list of permitted fields
        allowed_fields = {"email", "push", "slack", "sms", "digest_frequency"}
        body = {k: v for k, v in body.items() if k in allowed_fields}
        if not body:
            raise HTTPException(status_code=400, detail="No valid fields provided")

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        await db.notification_prefs.update_one(
            {"_id": "admin"}, {"$set": body}, upsert=True
        )
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating notification prefs: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/push/delivery-stats")
async def push_delivery_stats(request: Request):
    """Placeholder delivery stats."""
    _validate_admin_session(request)
    return {"delivered": 0, "failed": 0, "pending": 0, "source": "placeholder"}


@router.post("/indexnow/ping")
async def indexnow_ping(request: Request):
    """Placeholder IndexNow ping."""
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"status": "ok", "source": "placeholder"}


@router.get("/indexnow/status")
async def indexnow_status(request: Request):
    """Placeholder IndexNow status."""
    _validate_admin_session(request)
    return {"enabled": False, "last_ping": None, "source": "placeholder"}


@router.post("/indexnow/backfill-all")
async def indexnow_backfill_all(request: Request):
    """Placeholder IndexNow backfill."""
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"status": "triggered", "urls_queued": 0, "source": "placeholder"}


@router.get("/indexnow/backfill-progress")
async def indexnow_backfill_progress(request: Request):
    """Placeholder IndexNow backfill progress."""
    _validate_admin_session(request)
    return {"progress": 0, "total": 0, "completed": 0, "source": "placeholder"}


@router.post("/indexnow/submit-urls")
async def indexnow_submit_urls(request: Request):
    """Placeholder IndexNow URL submission."""
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"status": "ok", "submitted": 0, "source": "placeholder"}


@router.get("/indexnow/history")
async def indexnow_history(request: Request):
    """Placeholder IndexNow history."""
    _validate_admin_session(request)
    return {"history": [], "source": "placeholder"}
