"""Cron run heartbeats — write side.

Phase 4 — Cron port (Task #332).

Called by `services/cron-jobs/run.py` at the END of every job
execution to record `{job_name, status, duration_ms, error, ts}` so
`routes/admin_azure_cron.py` has a Mongo-backed floor independent
of the Azure Resource Manager control plane.

This module is intentionally tiny — it only does writes. The read
side lives in `routes/admin_azure_cron.py:_heartbeat_snapshot` so
the cron-jobs container does not pull route handlers into its image.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_TTL_DAYS = int(os.environ.get("CRON_HEARTBEAT_TTL_DAYS", "30"))


async def _collection():
    """Resolve the Mongo collection lazily so unit tests can monkey-patch."""
    # Import inside the function — server.py owns the Motor client.
    from server import db  # type: ignore
    if db is None:
        return None
    return db.cron_heartbeats


async def ensure_indexes() -> None:
    """Create the TTL + lookup index. Safe to call repeatedly.

    Called once from server.py startup (when RUN_LEGACY_LOOPS is
    false, i.e. cron has been migrated and the heartbeat reader is
    the source of truth for the admin card).
    """
    coll = await _collection()
    if coll is None:
        return
    try:
        await coll.create_index("job_name")
        await coll.create_index("ts", expireAfterSeconds=_TTL_DAYS * 24 * 3600)
    except Exception:
        logger.exception("cron_heartbeats.ensure_indexes failed")


async def record(*, job_name: str, status: str, duration_ms: int, error: Optional[str] = None) -> None:
    """Insert a heartbeat row. Best-effort — never raises."""
    coll = await _collection()
    if coll is None:
        return
    doc = {
        "job_name":    job_name,
        "status":      status,
        "duration_ms": int(duration_ms),
        "error":       error,
        "ts":          datetime.now(timezone.utc),
    }
    try:
        await coll.insert_one(doc)
    except Exception:
        logger.exception("cron_heartbeats.record failed for %s (status=%s)", job_name, status)
