"""
Admin Alerts Endpoints
Alert CRUD, acknowledgment, cooldowns, alert settings.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Request, HTTPException, Query
from typing import Optional
import logging

from app.api.v1.admin import _validate_admin_session, _csrf_check

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/alerts")
async def list_alerts(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    status: Optional[str] = Query(default=None),
):
    """List alerts, paginated and filterable by status."""
    _validate_admin_session(request)
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        query = {}
        if status == "acknowledged":
            query["acknowledged"] = True
        elif status == "unacknowledged":
            query["acknowledged"] = {"$ne": True}

        total = await db.alerts.count_documents(query)
        docs = (
            await db.alerts.find(query)
            .sort("created_at", -1)
            .skip(offset)
            .limit(limit)
            .to_list(length=limit)
        )
        for doc in docs:
            doc["_id"] = str(doc["_id"])
        return {"alerts": docs, "total": total}
    except Exception as e:
        logger.error(f"Error listing alerts: {e}")
        return {"alerts": [], "total": 0}


@router.get("/alerts/unacknowledged-count")
async def alerts_unacknowledged_count(request: Request):
    """Count unacknowledged alerts."""
    _validate_admin_session(request)
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        count = await db.alerts.count_documents({"acknowledged": {"$ne": True}})
        return {"count": count}
    except Exception:
        return {"count": 0}


@router.patch("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: str, request: Request):
    """Acknowledge a single alert."""
    _validate_admin_session(request)
    await _csrf_check(request)
    try:
        from bson import ObjectId
        from app.db.mongo import get_mongo_client
        from app.config import settings

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        result = await db.alerts.update_one(
            {"_id": ObjectId(alert_id)},
            {
                "$set": {
                    "acknowledged": True,
                    "acknowledged_at": datetime.now(timezone.utc),
                }
            },
        )
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Alert not found")
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error acknowledging alert: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/alerts/acknowledge-all")
async def acknowledge_all_alerts(request: Request):
    """Bulk acknowledge all unacknowledged alerts."""
    _validate_admin_session(request)
    await _csrf_check(request)
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        result = await db.alerts.update_many(
            {"acknowledged": {"$ne": True}},
            {
                "$set": {
                    "acknowledged": True,
                    "acknowledged_at": datetime.now(timezone.utc),
                }
            },
        )
        return {"status": "ok", "acknowledged_count": result.modified_count}
    except Exception as e:
        logger.error(f"Error bulk acknowledging alerts: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/alerts/backfill-thresholds")
async def backfill_thresholds(request: Request):
    """Placeholder backfill thresholds."""
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"status": "ok", "source": "placeholder"}


@router.get("/alerts/cooldowns")
async def list_cooldowns(request: Request):
    """List active cooldowns (placeholder)."""
    _validate_admin_session(request)
    return {"cooldowns": [], "source": "placeholder"}


@router.delete("/alerts/cooldowns/{dedup_key}")
async def delete_cooldown(dedup_key: str, request: Request):
    """Placeholder delete cooldown."""
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"status": "ok", "source": "placeholder"}


@router.get("/alert-settings")
async def get_alert_settings(request: Request):
    """Return alert settings from collection."""
    _validate_admin_session(request)
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        doc = await db.alert_settings.find_one({"_id": "main"})
        if doc:
            doc.pop("_id", None)
            return doc
        return {"email_enabled": True, "slack_enabled": False, "cooldown_minutes": 60}
    except Exception:
        return {"email_enabled": True, "slack_enabled": False, "cooldown_minutes": 60}


@router.put("/alert-settings")
async def update_alert_settings(request: Request):
    """Update alert settings."""
    _validate_admin_session(request)
    await _csrf_check(request)
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings

        body = await request.json()

        # Allow-list of permitted fields
        allowed_fields = {
            "email_enabled",
            "slack_enabled",
            "cooldown_minutes",
            "webhook_url",
            "severity_threshold",
            "notify_on_resolve",
        }
        body = {k: v for k, v in body.items() if k in allowed_fields}
        if not body:
            raise HTTPException(status_code=400, detail="No valid fields provided")

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        await db.alert_settings.update_one({"_id": "main"}, {"$set": body}, upsert=True)
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating alert settings: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/alert-settings/test-delivery")
async def test_alert_delivery(request: Request):
    """Placeholder test delivery."""
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"status": "ok", "delivered": False, "source": "placeholder"}
