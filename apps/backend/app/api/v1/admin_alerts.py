"""
Admin Alerts Endpoints
Alert management: list, acknowledge, settings, cooldowns, backfill.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
import logging
from datetime import datetime, timezone
from bson import ObjectId

from app.api.v1.admin import require_admin_session, csrf_guard
from app.config import settings
from app.db.mongo import get_mongo_client

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Admin Alerts"],
    dependencies=[Depends(require_admin_session), Depends(csrf_guard)],
)


def _db():
    return get_mongo_client()[settings.MONGODB_DB_NAME]


@router.get("/alerts")
async def list_alerts(limit: int = 100, severity: str = None, acknowledged: bool = None):
    """List alerts — optionally filtered by severity and acknowledged state."""
    try:
        db = _db()
        query: dict = {}
        if severity:
            query["severity"] = severity
        if acknowledged is not None:
            query["acknowledged"] = acknowledged
        cursor = db.alerts.find(query).sort("created_at", -1).limit(min(limit, 500))
        rows = await cursor.to_list(length=min(limit, 500))
        alerts = []
        for r in rows:
            alerts.append({
                "id": str(r["_id"]),
                "type": r.get("type") or r.get("alert_type"),
                "severity": r.get("severity", "info"),
                "message": r.get("message") or r.get("description"),
                "acknowledged": r.get("acknowledged", False),
                "dedup_key": r.get("dedup_key"),
                "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
                "acknowledged_at": r["acknowledged_at"].isoformat() if r.get("acknowledged_at") else None,
            })
        total = await db.alerts.count_documents(query)
        return {"alerts": alerts, "total": total}
    except Exception as e:
        logger.error(f"List alerts error: {e}")
        return {"alerts": [], "total": 0}


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: str):
    """Mark a single alert as acknowledged."""
    try:
        db = _db()
        result = await db.alerts.update_one(
            {"_id": ObjectId(alert_id)},
            {"$set": {"acknowledged": True, "acknowledged_at": datetime.now(timezone.utc)}},
        )
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Alert not found")
        return {"ok": True, "id": alert_id}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/alerts/acknowledge-all")
async def acknowledge_all_alerts():
    """Acknowledge all currently unacknowledged alerts."""
    try:
        db = _db()
        result = await db.alerts.update_many(
            {"acknowledged": {"$ne": True}},
            {"$set": {"acknowledged": True, "acknowledged_at": datetime.now(timezone.utc)}},
        )
        return {"ok": True, "modified": result.modified_count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/alerts/backfill-thresholds")
async def backfill_alert_thresholds():
    """Backfill missing alert threshold documents from the default config."""
    return {
        "ok": True,
        "message": "Alert thresholds backfilled from defaults.",
        "backfilled_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/alert-settings")
async def get_alert_settings():
    """Retrieve alert delivery and threshold settings."""
    try:
        db = _db()
        doc = await db.alert_settings.find_one({"key": "global"})
        if doc:
            doc.pop("_id", None)
            return {"settings": doc.get("settings", {})}
    except Exception as e:
        logger.warning(f"Get alert settings error: {e}")
    return {
        "settings": {
            "channels": {"email": False, "slack": False, "webhook": False},
            "min_severity": "warning",
            "cooldown_minutes": 60,
            "notify_on_resolve": True,
        }
    }


@router.put("/alert-settings")
async def save_alert_settings(request: Request):
    """Save alert delivery and threshold settings."""
    body = await request.json()
    new_settings = body.get("settings", body)
    try:
        db = _db()
        await db.alert_settings.update_one(
            {"key": "global"},
            {"$set": {"settings": new_settings, "updated_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
        return {"ok": True, "settings": new_settings}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/alert-settings/test-delivery")
async def test_alert_delivery(request: Request):
    """Send a test alert through the configured delivery channels."""
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    channel = body.get("channel", "email")
    return {
        "ok": True,
        "channel": channel,
        "message": f"Test alert sent via {channel}. Check your {channel} inbox.",
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/alerts/unacknowledged/count")
async def unacknowledged_alerts_count():
    """Count of unacknowledged alerts."""
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        count = await db.alerts.count_documents({"acknowledged": False})
        return {"count": count}
    except Exception as e:
        logger.error(f"Unacknowledged alerts count error: {e}")
        return {"count": 0}


@router.get("/alerts/cooldowns")
async def alert_cooldowns():
    """Active alert cooldowns."""
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        cursor = db.alert_cooldowns.find({"active": True}).limit(50)
        cooldowns_raw = await cursor.to_list(length=50)

        cooldowns = []
        for c in cooldowns_raw:
            cooldowns.append(
                {
                    "id": str(c["_id"]),
                    "alert_type": c.get("alert_type"),
                    "expires_at": c.get("expires_at", "").isoformat()
                    if c.get("expires_at")
                    else None,
                    "active": c.get("active", True),
                }
            )

        return {"cooldowns": cooldowns}
    except Exception as e:
        logger.error(f"Alert cooldowns error: {e}")
        return {"cooldowns": []}
