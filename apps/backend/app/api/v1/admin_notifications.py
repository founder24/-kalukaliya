"""
Admin Notifications Endpoints
Manage system notifications.
"""

from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request
import logging

from app.api.v1.admin import require_admin_session, csrf_guard
from app.config import settings
from app.db.mongo import get_mongo_client

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Admin Notifications"],
    dependencies=[Depends(require_admin_session), Depends(csrf_guard)],
)


@router.get("/notifications")
async def list_notifications():
    """List all notifications."""
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        cursor = db.notifications.find({}).sort("created_at", -1).limit(100)
        notifications_raw = await cursor.to_list(length=100)

        notifications = []
        for n in notifications_raw:
            notifications.append(
                {
                    "id": str(n["_id"]),
                    "title": n.get("title"),
                    "message": n.get("message"),
                    "type": n.get("type", "info"),
                    "target": n.get("target", "all"),
                    "read": n.get("read", False),
                    "created_at": n.get("created_at", "").isoformat()
                    if n.get("created_at")
                    else None,
                }
            )

        return {"notifications": notifications}
    except Exception as e:
        logger.error(f"List notifications error: {e}")
        return {"notifications": []}


@router.post("/notifications")
async def create_notification(request: Request):
    """Create a new notification."""
    body = await request.json()
    title = body.get("title")
    message = body.get("message")

    if not title or not message:
        raise HTTPException(status_code=400, detail="Title and message are required")

    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        notification = {
            "title": title,
            "message": message,
            "type": body.get("type", "info"),
            "target": body.get("target", "all"),
            "read": False,
            "created_at": datetime.now(timezone.utc),
        }

        result = await db.notifications.insert_one(notification)

        return {
            "status": "ok",
            "notification_id": str(result.inserted_id),
        }
    except Exception as e:
        logger.error(f"Create notification error: {e}")
        raise HTTPException(status_code=500, detail="Failed to create notification")


# ── Notification Triggers ─────────────────────────────────────────────────────

@router.get("/notifications/triggers")
async def list_notification_triggers():
    """List all notification trigger rules."""
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        cursor = db.notification_triggers.find({}).sort("created_at", -1).limit(100)
        rows = await cursor.to_list(length=100)
        return {
            "triggers": [
                {
                    "id": str(r["_id"]),
                    "name": r.get("name"),
                    "event": r.get("event"),
                    "channel": r.get("channel"),
                    "enabled": r.get("enabled", True),
                    "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
                }
                for r in rows
            ]
        }
    except Exception as e:
        logger.error(f"List triggers error: {e}")
        return {"triggers": []}


@router.post("/notifications/triggers")
async def create_notification_trigger(request: Request):
    """Create a notification trigger rule."""
    body = await request.json()
    if not body.get("name") or not body.get("event"):
        raise HTTPException(status_code=400, detail="name and event are required")
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        doc = {
            "name": body["name"],
            "event": body["event"],
            "channel": body.get("channel", "email"),
            "template": body.get("template"),
            "filters": body.get("filters", {}),
            "enabled": body.get("enabled", True),
            "created_at": datetime.now(timezone.utc),
        }
        result = await db.notification_triggers.insert_one(doc)
        return {"ok": True, "id": str(result.inserted_id)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/notifications/triggers/{trigger_id}")
async def update_notification_trigger(trigger_id: str, request: Request):
    """Update a notification trigger rule."""
    body = await request.json()
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        update = {k: v for k, v in body.items() if k not in ("_id", "id")}
        update["updated_at"] = datetime.now(timezone.utc)
        result = await db.notification_triggers.update_one({"_id": ObjectId(trigger_id)}, {"$set": update})
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Trigger not found")
        return {"ok": True, "id": trigger_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/notifications/triggers/{trigger_id}")
async def delete_notification_trigger(trigger_id: str):
    """Delete a notification trigger rule."""
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        result = await db.notification_triggers.delete_one({"_id": ObjectId(trigger_id)})
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Trigger not found")
        return {"ok": True, "id": trigger_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
