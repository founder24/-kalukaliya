"""
Admin Security Endpoints
Spoofed bots, blocked IPs, block trends, TTL monitor.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Request, HTTPException
import logging

from app.api.v1.admin import _validate_admin_session, _csrf_check

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/security/spoofed-bots")
async def spoofed_bots(request: Request):
    """Placeholder spoofed bots."""
    _validate_admin_session(request)
    return {"bots": [], "total": 0, "source": "placeholder"}


@router.get("/security/blocked-ips")
async def blocked_ips(request: Request):
    """List blocked IPs from collection."""
    _validate_admin_session(request)
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        docs = await db.blocked_ips.find().to_list(length=500)
        for doc in docs:
            doc["_id"] = str(doc["_id"])
        return {"blocked_ips": docs}
    except Exception as e:
        logger.error(f"Error listing blocked IPs: {e}")
        return {"blocked_ips": []}


@router.get("/security/block-trends")
async def block_trends(request: Request):
    """Placeholder block trends."""
    _validate_admin_session(request)
    return {"trends": [], "source": "placeholder"}


@router.post("/security/block-ip")
async def block_ip(request: Request):
    """Add IP to blocked list."""
    _validate_admin_session(request)
    await _csrf_check(request)
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings

        body = await request.json()
        ip = body.get("ip")
        if not ip:
            raise HTTPException(status_code=400, detail="IP address required")

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        await db.blocked_ips.update_one(
            {"ip": ip},
            {"$set": {"ip": ip, "blocked_at": datetime.now(timezone.utc), "reason": body.get("reason", "")}},
            upsert=True,
        )
        return {"status": "ok", "ip": ip}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error blocking IP: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/security/unblock-ip")
async def unblock_ip(request: Request):
    """Remove IP from blocked list."""
    _validate_admin_session(request)
    await _csrf_check(request)
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings

        body = await request.json()
        ip = body.get("ip")
        if not ip:
            raise HTTPException(status_code=400, detail="IP address required")

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        result = await db.blocked_ips.delete_one({"ip": ip})
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="IP not found in blocked list")
        return {"status": "ok", "ip": ip}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error unblocking IP: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/security/ttl-monitor")
async def ttl_monitor(request: Request):
    """Placeholder TTL monitor."""
    _validate_admin_session(request)
    return {"collections": [], "source": "placeholder"}


@router.get("/security/collection-size-history")
async def collection_size_history(request: Request):
    """Placeholder collection size history."""
    _validate_admin_session(request)
    return {"history": [], "source": "placeholder"}
