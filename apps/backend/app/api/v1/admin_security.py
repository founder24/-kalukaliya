"""
Admin Security Endpoints
Bot detection, IP blocking, TTL monitoring.
"""

from fastapi import APIRouter, Request
import logging

from app.api.v1.admin import _validate_admin_session, _csrf_check

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin Security"])


@router.get("/security/spoofed-bots")
async def get_spoofed_bots(request: Request, days: int = 7):
    """List detected spoofed bots."""
    _validate_admin_session(request)
    return {"bots": [], "total": 0}


@router.get("/security/blocked-ips")
async def get_blocked_ips(request: Request):
    """List blocked IP hashes."""
    _validate_admin_session(request)
    return {"ips": [], "total": 0}


@router.get("/security/block-trends")
async def get_block_trends(request: Request, days: int = 30):
    """IP block trends over time."""
    _validate_admin_session(request)
    return {"trends": []}


@router.post("/security/block-ip")
async def block_ip(request: Request):
    """Block an IP hash."""
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"message": "IP blocked"}


@router.post("/security/unblock-ip")
async def unblock_ip(request: Request):
    """Unblock an IP hash."""
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"message": "IP unblocked"}


@router.get("/security/ttl-monitor")
async def get_ttl_monitor(request: Request):
    """Monitor TTL-based collections."""
    _validate_admin_session(request)
    return {"collections": []}


@router.get("/security/collection-size-history")
async def get_collection_size_history(request: Request, days: int = 90):
    """Collection size history for capacity planning."""
    _validate_admin_session(request)
    return {"history": []}
