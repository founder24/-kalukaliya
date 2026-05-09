"""Task #42 — dev-only ring buffer of `[STREAM][ROUTER]` /
`[NON-STREAM][ROUTER]` log lines.

Task #41 made the QA badge render on fail-loud chat turns by piping
the router decision through the HTTP 503 detail body. The only way
to see *why* the underlying tool returned zero (the upstream
Pinecone / web-search call) is still to tail the backend log line
``[STREAM][ROUTER=web|rag] ... failing loud``. This thin endpoint
exposes a small in-process ring buffer of those router log lines so
the dev-only "router log" expander on the failed bubble can fetch
them without leaving the chat tab.

Strictly DEV/STAGING only. The endpoint refuses with 404 when
``ENV=production`` / ``ENVIRONMENT=production`` so it never leaks
internal log content to real users.
"""
from __future__ import annotations

import logging
import os
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)
router = APIRouter()

ROUTER_LOG_BUFFER_MAX = 500
ROUTER_LOG_TAGS = ("[STREAM][ROUTER", "[NON-STREAM][ROUTER")

_BUFFER: Deque[Dict[str, Any]] = deque(maxlen=ROUTER_LOG_BUFFER_MAX)


def _is_production() -> bool:
    env = (os.environ.get("ENV") or os.environ.get("ENVIRONMENT") or "").lower()
    return env in ("production", "prod")


class _RouterLogCaptureHandler(logging.Handler):
    """Captures any log record whose rendered message starts with one
    of ``ROUTER_LOG_TAGS`` into the in-process ring buffer."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:
            return
        if not any(tag in msg for tag in ROUTER_LOG_TAGS):
            return
        try:
            _BUFFER.append({
                "ts": datetime.fromtimestamp(
                    record.created, tz=timezone.utc
                ).isoformat(),
                "level": record.levelname.lower(),
                "logger": record.name,
                "message": msg,
            })
        except Exception:
            # Best-effort. A failure here must never break the caller's
            # logging path.
            pass


_HANDLER_INSTALLED = False


def install_router_log_capture() -> None:
    """Idempotent install of the capture handler on the root logger.

    Called once from ``server.py`` import time so it sees every
    `[STREAM][ROUTER]` line emitted by ``routes/ai_chat.py`` regardless
    of which logger name they use.
    """
    global _HANDLER_INSTALLED
    if _HANDLER_INSTALLED:
        return
    handler = _RouterLogCaptureHandler(level=logging.DEBUG)
    logging.getLogger().addHandler(handler)
    _HANDLER_INSTALLED = True


# Install on import so the buffer fills even before the first request
# (e.g. during the cold-start chat turn that triggers the first
# fail-loud router log line).
install_router_log_capture()


@router.get("/api/dev/router-logs/recent")
async def dev_router_logs_recent(
    conversation_id: Optional[str] = Query(None, max_length=128),
    q: Optional[str] = Query(None, max_length=200),
    limit: int = Query(50, ge=1, le=ROUTER_LOG_BUFFER_MAX),
) -> Dict[str, Any]:
    """Return the most recent captured router log lines.

    Filters (all optional, ANDed):
      * ``conversation_id`` — substring match on the message
        (the fail-loud sites embed ``cid=<conversation_id>``).
      * ``q`` — case-insensitive substring match on the message.

    Dev/staging only. Production returns 404 so this never becomes a
    user-visible log surface.
    """
    if _is_production():
        raise HTTPException(status_code=404, detail="Not found")

    items: List[Dict[str, Any]] = list(_BUFFER)
    if conversation_id:
        needle = conversation_id.strip()
        if needle:
            items = [it for it in items if needle in it["message"]]
    if q:
        needle = q.strip().lower()
        if needle:
            items = [it for it in items if needle in it["message"].lower()]

    # Most recent first, capped at ``limit``.
    items = items[-limit:][::-1]
    return {
        "logs": items,
        "buffer_size": len(_BUFFER),
        "buffer_max": ROUTER_LOG_BUFFER_MAX,
    }


def _reset_buffer_for_tests() -> None:
    _BUFFER.clear()
