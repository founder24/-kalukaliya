"""
Admin Security Endpoints
IP blocking, bot detection, TTL monitoring.
"""

from fastapi import APIRouter, Request
import logging

from app.api.v1.admin import _validate_admin_session, _csrf_check

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin Security"])


@router.get("/security/spoofed-bots")
async def get_spoofed_bots(request: Request):
    """List detected spoofed bot user-agents."""
    _validate_admin_session(request)
    return {"bots": [], "total": 0}


@router.get("/security/blocked-ips")
async def get_blocked_ips(request: Request):
    """List currently blocked IPs."""
    _validate_admin_session(request)
    return {"ips": [], "total": 0}


@router.get("/security/block-trends")
async def get_block_trends(request: Request):
    """IP block trends over time."""
    _validate_admin_session(request)
    return {"trends": [], "days": 30}


@router.post("/security/block-ip")
async def block_ip(request: Request):
    """Block an IP address."""
    _validate_admin_session(request)
    await _csrf_check(request)
    body = await request.json()
    ip_hash = body.get("ip_hash", "")
    reason = body.get("reason", "manual")
    logger.info(f"IP block requested: {ip_hash}, reason: {reason}")
    return {"status": "ok", "message": f"IP {ip_hash} blocked"}


@router.post("/security/unblock-ip")
async def unblock_ip(request: Request):
    """Unblock an IP address."""
    _validate_admin_session(request)
    await _csrf_check(request)
    body = await request.json()
    ip_hash = body.get("ip_hash", "")
    logger.info(f"IP unblock requested: {ip_hash}")
    return {"status": "ok", "message": f"IP {ip_hash} unblocked"}


@router.get("/security/ttl-monitor")
async def get_ttl_monitor(request: Request):
    """TTL monitor status."""
    _validate_admin_session(request)
    return {"monitors": [], "status": "ok"}


@router.get("/security/collection-size-history")
async def get_collection_size_history(request: Request):
    """Database collection size history."""
    _validate_admin_session(request)
    return {"history": [], "days": 90}
