"""
Admin Alerts Endpoints
Alert management: unacknowledged count, cooldowns.
"""

from fastapi import APIRouter, Request
import logging

from app.api.v1.admin import _validate_admin_session
from app.config import settings
from app.db.mongo import get_mongo_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin Alerts"])


@router.get("/alerts/unacknowledged/count")
async def unacknowledged_alerts_count(request: Request):
    """Count of unacknowledged alerts."""
    _validate_admin_session(request)

    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        count = await db.alerts.count_documents({"acknowledged": False})
        return {"count": count}
    except Exception as e:
        logger.error(f"Unacknowledged alerts count error: {e}")
        return {"count": 0}


@router.get("/alerts/cooldowns")
async def alert_cooldowns(request: Request):
    """Active alert cooldowns."""
    _validate_admin_session(request)

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
