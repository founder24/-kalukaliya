"""
Admin Security Endpoints
Bot detection, IP blocking, and security monitoring stubs.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
import logging

from app.api.v1.admin import require_admin_session, csrf_guard

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Admin Security"],
    dependencies=[Depends(require_admin_session), Depends(csrf_guard)],
)


class BlockIpRequest(BaseModel):
    ip_hash: str
    reason: str = "repeat_spoof_offender"
    expires_in: Optional[int] = None


class UnblockIpRequest(BaseModel):
    ip_hash: str


@router.get("/security/spoofed-bots")
async def get_spoofed_bots():
    """Get list of detected spoofed bot user agents."""
    return {"spoofed_bots": [], "total": 0}


@router.get("/security/blocked-ips")
async def get_blocked_ips():
    """Get currently blocked IP hashes."""
    return {"blocked_ips": [], "total": 0}


@router.get("/security/block-trends")
async def get_block_trends():
    """Get IP block trends over time."""
    return {"trends": [], "total_blocks": 0}


@router.post("/security/block-ip")
async def block_ip(body: BlockIpRequest):
    """Block an IP hash."""
    logger.info(
        "IP block requested", extra={"ip_hash": body.ip_hash, "reason": body.reason}
    )
    return {"status": "ok", "message": "IP blocked", "ip_hash": body.ip_hash}


@router.post("/security/unblock-ip")
async def unblock_ip(body: UnblockIpRequest):
    """Unblock an IP hash."""
    logger.info("IP unblock requested", extra={"ip_hash": body.ip_hash})
    return {"status": "ok", "message": "IP unblocked", "ip_hash": body.ip_hash}


@router.get("/security/ttl-monitor")
async def get_ttl_monitor():
    """Get TTL monitor status for security collections."""
    return {"collections": [], "status": "healthy"}


@router.get("/security/collection-size-history")
async def get_collection_size_history():
    """Get collection size history for security monitoring."""
    return {"history": [], "current_sizes": {}}
