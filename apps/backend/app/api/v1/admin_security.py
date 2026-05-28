"""
Admin Security Endpoints
Bot detection, IP blocking, and security monitoring stubs.
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from typing import Optional
import logging

from app.api.v1.admin import _validate_admin_session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin Security"])


class BlockIpRequest(BaseModel):
    ip_hash: str
    reason: str = "repeat_spoof_offender"
    expires_in: Optional[int] = None


class UnblockIpRequest(BaseModel):
    ip_hash: str


@router.get("/security/spoofed-bots")
async def get_spoofed_bots(request: Request):
    """Get list of detected spoofed bot user agents."""
    _validate_admin_session(request)
    return {"spoofed_bots": [], "total": 0}


@router.get("/security/blocked-ips")
async def get_blocked_ips(request: Request):
    """Get currently blocked IP hashes."""
    _validate_admin_session(request)
    return {"blocked_ips": [], "total": 0}


@router.get("/security/block-trends")
async def get_block_trends(request: Request):
    """Get IP block trends over time."""
    _validate_admin_session(request)
    return {"trends": [], "total_blocks": 0}


@router.post("/security/block-ip")
async def block_ip(body: BlockIpRequest, request: Request):
    """Block an IP hash."""
    _validate_admin_session(request)
    logger.info("IP block requested", extra={"ip_hash": body.ip_hash, "reason": body.reason})
    return {"status": "ok", "message": "IP blocked", "ip_hash": body.ip_hash}


@router.post("/security/unblock-ip")
async def unblock_ip(body: UnblockIpRequest, request: Request):
    """Unblock an IP hash."""
    _validate_admin_session(request)
    logger.info("IP unblock requested", extra={"ip_hash": body.ip_hash})
    return {"status": "ok", "message": "IP unblocked", "ip_hash": body.ip_hash}


@router.get("/security/ttl-monitor")
async def get_ttl_monitor(request: Request):
    """Get TTL monitor status for security collections."""
    _validate_admin_session(request)
    return {"collections": [], "status": "healthy"}


@router.get("/security/collection-size-history")
async def get_collection_size_history(request: Request):
    """Get collection size history for security monitoring."""
    _validate_admin_session(request)
    return {"history": [], "current_sizes": {}}
