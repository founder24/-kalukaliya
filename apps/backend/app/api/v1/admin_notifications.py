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


# ── Notification Preferences ──────────────────────────────────────────────────

_DEFAULT_NOTIF_PREFS = {
    "sound_enabled": True,
    "push_enabled": False,
    "chime_tone": "default",
    "sound_severities": ["high_error_rate", "high_latency", "spoofed_bot_surge",
                         "high_fallback_rate", "endpoint_down", "auto_block_expired"],
    "push_severities": ["high_error_rate", "spoofed_bot_surge",
                        "endpoint_down", "auto_block_expired"],
    "custom_chime_url": None,
}


@router.get("/notification-prefs")
async def get_notification_prefs():
    """Return the admin notification preferences document."""
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        doc = await db.admin_notification_prefs.find_one({"_id": "singleton"})
        if not doc:
            return _DEFAULT_NOTIF_PREFS
        doc.pop("_id", None)
        return {**_DEFAULT_NOTIF_PREFS, **doc}
    except Exception as e:
        logger.error(f"get notification-prefs error: {e}")
        return _DEFAULT_NOTIF_PREFS


@router.put("/notification-prefs")
async def put_notification_prefs(request: Request):
    """Upsert admin notification preferences."""
    body = await request.json()
    allowed = {"sound_enabled", "push_enabled", "chime_tone",
                "sound_severities", "push_severities", "custom_chime_url"}
    update = {k: v for k, v in body.items() if k in allowed}
    if not update:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        await db.admin_notification_prefs.update_one(
            {"_id": "singleton"},
            {"$set": {**update, "updated_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
        doc = await db.admin_notification_prefs.find_one({"_id": "singleton"})
        doc.pop("_id", None)
        return {**_DEFAULT_NOTIF_PREFS, **doc}
    except Exception as e:
        logger.error(f"put notification-prefs error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/notification-prefs/upload-chime")
async def upload_chime(request: Request):
    """Accept a custom chime audio upload (stub — returns ok)."""
    return {"ok": True, "message": "Custom chime upload is not configured on this server."}


@router.delete("/notification-prefs/custom-chime")
async def delete_custom_chime():
    """Remove custom chime, revert to built-in tone."""
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        await db.admin_notification_prefs.update_one(
            {"_id": "singleton"},
            {"$unset": {"custom_chime_url": ""}},
            upsert=True,
        )
        return {"ok": True}
    except Exception as e:
        logger.error(f"delete custom-chime error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Push Delivery Stats / Log ─────────────────────────────────────────────────

@router.get("/push/delivery-stats")
async def push_delivery_stats(days: int = 7):
    """Summarise push notification dispatch outcomes for the given window."""
    from datetime import timedelta
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        since = datetime.now(timezone.utc) - timedelta(days=days)

        pipeline = [
            {"$match": {"dispatched_at": {"$gte": since}}},
            {"$group": {
                "_id": "$status",
                "count": {"$sum": 1},
            }},
        ]
        rows = await (await db.push_dispatch_log.aggregate(pipeline)).to_list(length=20)
        by_status = {r["_id"]: r["count"] for r in rows}

        total = sum(by_status.values())
        return {
            "days": days,
            "total": total,
            "delivered": by_status.get("delivered", 0),
            "failed": by_status.get("failed", 0),
            "pending": by_status.get("pending", 0),
            "by_status": by_status,
            "source": "mongodb" if rows else "empty",
        }
    except Exception as e:
        logger.error(f"push/delivery-stats error: {e}")
        return {"days": days, "total": 0, "delivered": 0, "failed": 0,
                "pending": 0, "by_status": {}, "source": "unavailable"}


@router.get("/push/delivery-log")
async def push_delivery_log(limit: int = 50):
    """Return the most recent push dispatch log entries."""
    limit = min(limit, 200)
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        rows = await db.push_dispatch_log.find({}).sort(
            "dispatched_at", -1
        ).limit(limit).to_list(length=limit)
        return {
            "dispatches": [
                {
                    "id": str(r["_id"]),
                    "status": r.get("status"),
                    "title": r.get("title"),
                    "body": r.get("body"),
                    "dispatched_at": r["dispatched_at"].isoformat() if r.get("dispatched_at") else None,
                    "recipient_count": r.get("recipient_count", 0),
                    "severity": r.get("severity"),
                }
                for r in rows
            ],
            "total": len(rows),
        }
    except Exception as e:
        logger.error(f"push/delivery-log error: {e}")
        return {"dispatches": [], "total": 0}


@router.get("/push/delivery-log/{dispatch_id}")
async def push_delivery_log_detail(dispatch_id: str):
    """Return a single push dispatch log entry with full detail."""
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        row = await db.push_dispatch_log.find_one({"_id": ObjectId(dispatch_id)})
        if not row:
            raise HTTPException(status_code=404, detail="Dispatch not found")
        row["id"] = str(row.pop("_id"))
        if row.get("dispatched_at"):
            row["dispatched_at"] = row["dispatched_at"].isoformat()
        return row
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"push/delivery-log/{dispatch_id} error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
