"""
Admin Logs Explorer Endpoints
Streams and queries the request_logs collection; provides log rotation control.
Routes: /logs, /logs/status, /logs/export, /logs/trace/{id}
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
import logging
import io
import csv
from datetime import datetime, timezone, timedelta
from typing import Optional
from bson import ObjectId

from app.api.v1.admin import require_admin_session, csrf_guard
from app.config import settings
from app.db.mongo import get_mongo_client

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Admin Logs"],
    dependencies=[Depends(require_admin_session), Depends(csrf_guard)],
)


def _db():
    return get_mongo_client()[settings.MONGODB_DB_NAME]


@router.get("/logs")
async def list_logs(
    limit: int = 100,
    level: Optional[str] = None,
    path: Optional[str] = None,
    method: Optional[str] = None,
    status_min: Optional[int] = None,
    status_max: Optional[int] = None,
    hours: int = 24,
):
    """
    Paginated request log viewer. Queries request_logs collection.
    Supports filtering by level, path, method, status range, and time window.
    """
    try:
        db = _db()
        since = datetime.now(timezone.utc) - timedelta(hours=min(hours, 168))
        query: dict = {"created_at": {"$gte": since}}
        if path:
            query["path"] = {"$regex": path, "$options": "i"}
        if method:
            query["method"] = method.upper()
        if status_min is not None or status_max is not None:
            query["status"] = {}
            if status_min is not None:
                query["status"]["$gte"] = status_min
            if status_max is not None:
                query["status"]["$lte"] = status_max

        total = await db.request_logs.count_documents(query)
        cursor = db.request_logs.find(query).sort("created_at", -1).limit(min(limit, 500))
        rows = await cursor.to_list(length=min(limit, 500))

        entries = []
        for r in rows:
            entries.append({
                "id": str(r["_id"]),
                "path": r.get("path"),
                "api_path": r.get("api_path"),
                "method": r.get("method"),
                "status": r.get("status"),
                "latency_ms": r.get("latency_ms"),
                "ip": r.get("ip"),
                "user_agent": r.get("user_agent"),
                "referer": r.get("referer"),
                "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
            })

        return {
            "entries": entries,
            "total": total,
            "limit": limit,
            "hours": hours,
            "source": "request_logs",
        }
    except Exception as e:
        logger.error(f"Admin logs list error: {e}")
        return {"entries": [], "total": 0, "limit": limit, "hours": hours, "source": "unavailable"}


@router.get("/logs/status")
async def logs_status():
    """Log collection stats — document count, oldest/newest entry, disk estimate."""
    try:
        db = _db()
        total = await db.request_logs.count_documents({})
        stats_raw = await db.command("collStats", "request_logs")
        size_bytes = stats_raw.get("size", 0)

        oldest = newest = None
        if total > 0:
            oldest_doc = await db.request_logs.find_one({}, sort=[("created_at", 1)])
            newest_doc = await db.request_logs.find_one({}, sort=[("created_at", -1)])
            oldest = oldest_doc["created_at"].isoformat() if oldest_doc and oldest_doc.get("created_at") else None
            newest = newest_doc["created_at"].isoformat() if newest_doc and newest_doc.get("created_at") else None

        return {
            "total_documents": total,
            "size_bytes": size_bytes,
            "size_mb": round(size_bytes / 1024 / 1024, 2),
            "oldest_entry": oldest,
            "newest_entry": newest,
            "ttl_days": 90,
            "source": "request_logs",
        }
    except Exception as e:
        logger.warning(f"Logs status error: {e}")
        return {"total_documents": 0, "size_bytes": 0, "size_mb": 0, "source": "unavailable"}


@router.get("/logs/export")
async def logs_export(hours: int = 24):
    """Export request logs as CSV for the given time window (max 168h / 7 days)."""
    try:
        db = _db()
        since = datetime.now(timezone.utc) - timedelta(hours=min(hours, 168))
        cursor = db.request_logs.find({"created_at": {"$gte": since}}).sort("created_at", -1).limit(10000)
        rows = await cursor.to_list(length=10000)

        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=["created_at", "path", "method", "status", "latency_ms", "ip"])
        writer.writeheader()
        for r in rows:
            writer.writerow({
                "created_at": r.get("created_at", ""),
                "path": r.get("path", ""),
                "method": r.get("method", ""),
                "status": r.get("status", ""),
                "latency_ms": r.get("latency_ms", ""),
                "ip": r.get("ip", ""),
            })

        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=request_logs_{hours}h.csv"},
        )
    except Exception as e:
        logger.error(f"Logs export error: {e}")
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/logs/pause")
async def logs_pause():
    """Pause request logging (sets a flag in the admin_config collection)."""
    try:
        db = _db()
        await db.admin_config.update_one(
            {"key": "request_logging"},
            {"$set": {"paused": True, "paused_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
        return {"ok": True, "paused": True}
    except Exception as e:
        logger.error(f"Logs pause error: {e}")
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/logs/resume")
async def logs_resume():
    """Resume request logging."""
    try:
        db = _db()
        await db.admin_config.update_one(
            {"key": "request_logging"},
            {"$set": {"paused": False, "resumed_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
        return {"ok": True, "paused": False}
    except Exception as e:
        logger.error(f"Logs resume error: {e}")
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/logs/rotate-token")
async def logs_rotate_token():
    """Rotate the correlation token used to group log entries."""
    import secrets
    new_token = secrets.token_hex(16)
    try:
        db = _db()
        await db.admin_config.update_one(
            {"key": "correlation_token"},
            {"$set": {"token": new_token, "rotated_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
        return {"ok": True, "token": new_token}
    except Exception as e:
        logger.error(f"Logs rotate-token error: {e}")
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/logs/trace/{correlation_id}")
async def logs_trace(correlation_id: str):
    """Retrieve all log entries sharing a correlation/request ID."""
    try:
        db = _db()
        cursor = db.request_logs.find(
            {"$or": [{"request_id": correlation_id}, {"correlation_id": correlation_id}]}
        ).sort("created_at", 1).limit(200)
        rows = await cursor.to_list(length=200)
        entries = []
        for r in rows:
            entries.append({
                "id": str(r["_id"]),
                "path": r.get("path"),
                "method": r.get("method"),
                "status": r.get("status"),
                "latency_ms": r.get("latency_ms"),
                "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
            })
        return {"correlation_id": correlation_id, "entries": entries}
    except Exception as e:
        logger.error(f"Logs trace error: {e}")
        return {"correlation_id": correlation_id, "entries": []}
