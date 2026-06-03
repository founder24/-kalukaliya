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


@router.post("/page-view")
async def page_view(request: Request):
    """Page-view signal fired on every SPA route change and session resume."""
    try:
        body = await request.json()
        path = body.get("path", "")
        visitor_id = body.get("visitor_id", "")
        session_id = body.get("session_id", "")
        referrer = body.get("referrer", "")
        is_404 = body.get("is_404_hint", False)
        logger.debug(
            "page-view",
            extra={
                "path": path,
                "vid": visitor_id,
                "sid": session_id,
                "referrer": referrer,
                "is_404": is_404,
            },
        )
    except Exception:
        pass
    return JSONResponse({"status": "ok"})


@router.post("/review-prompt-event")
async def review_prompt_event(request: Request):
    """Review-prompt funnel events mirrored from the frontend."""
    try:
        body = await request.json()
        event = body.get("event", "")
        reason = body.get("reason", None)
        logger.debug(f"review-prompt-event event={event} reason={reason}")
    except Exception:
        pass
    return JSONResponse({"status": "ok"})


@router.post("/ad-impression")
async def ad_impression(request: Request):
    """Ad-viewability impression events mirrored from the frontend."""
    try:
        body = await request.json()
        placement = body.get("placement", "")
        network = body.get("network", "")
        logger.debug(f"ad-impression placement={placement} network={network}")
    except Exception:
        pass
    return JSONResponse({"status": "ok"})
