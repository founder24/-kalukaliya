"""
Admin Notifications Endpoints
Manage system notifications.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
import logging

from app.api.v1.admin import _validate_admin_session, _csrf_check
from app.config import settings
from app.db.mongo import get_mongo_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin Notifications"])


@router.get("/notifications")
async def list_notifications(request: Request):
    """List all notifications."""
    _validate_admin_session(request)

    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        cursor = db.notifications.find({}).sort("created_at", -1).limit(100)
        notifications_raw = await cursor.to_list(length=100)

        notifications = []
        for n in notifications_raw:
            notifications.append({
                "id": str(n["_id"]),
                "title": n.get("title"),
                "message": n.get("message"),
                "type": n.get("type", "info"),
                "target": n.get("target", "all"),
                "read": n.get("read", False),
                "created_at": n.get("created_at", "").isoformat() if n.get("created_at") else None,
            })

        return {"notifications": notifications}
    except Exception as e:
        logger.error(f"List notifications error: {e}")
        return {"notifications": []}


@router.post("/notifications")
async def create_notification(request: Request):
    """Create a new notification."""
    _validate_admin_session(request)
    await _csrf_check(request)

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
