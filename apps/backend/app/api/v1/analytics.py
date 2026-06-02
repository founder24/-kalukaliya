"""
Analytics Endpoints - Lightweight session tracking stubs.
Accepts session-ping heartbeats and session-end signals from the frontend.
Data is logged for future aggregation. No heavy DB writes on the hot path.
"""

import logging
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Analytics"])


@router.post("/session-ping")
async def session_ping(request: Request):
    """Heartbeat from the frontend every 30s. Accepts and acknowledges."""
    try:
        body = await request.json()
        session_id = body.get("session_id", "")
        visitor_id = body.get("visitor_id", "")
        logger.debug(f"session-ping sid={session_id} vid={visitor_id}")
    except Exception:
        pass
    return JSONResponse({"status": "ok"})


@router.post("/session-end")
async def session_end(request: Request):
    """Session-end signal sent via sendBeacon on page unload."""
    try:
        body = await request.json()
        session_id = body.get("session_id", "")
        visitor_id = body.get("visitor_id", "")
        end_ts = body.get("end_timestamp", "")
        logger.debug(f"session-end sid={session_id} vid={visitor_id} end={end_ts}")
    except Exception:
        pass
    return JSONResponse({"status": "ok"})
