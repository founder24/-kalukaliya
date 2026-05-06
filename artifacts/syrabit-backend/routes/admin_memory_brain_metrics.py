"""Task #417 — Admin observability for the memory_brain hot path.

Exposes the rolling 24h counters captured by ``memory_brain_metrics``
so the admin dashboard can render a small write/read/failure tile
and an hourly sparkline next to the existing embed-stack health pill.

Why this is a separate route (not a field on
``/admin/health/embed-stack``):

  * The embed-stack endpoint asks each provider for a live ping, which
    can take 1–2s on a cold worker. The dashboard tile we want here
    polls more aggressively (every 30–60s) and must stay cheap — it's
    a pure in-memory aggregate. Keeping the routes split lets the
    front-end refresh them independently.
  * The metrics are per-worker (see ``memory_brain_metrics`` docstring),
    so the response includes ``scope: "per_worker"`` and the
    gunicorn worker pid so the operator knows what they're looking at.

The companion alert lives in ``metrics._alerting_loop`` and fires
``memory_brain_failure_rate`` when the failure rate over the
configured window crosses ``memory_brain_failure_rate_pct``.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, Query

from auth_deps import get_admin_user
import memory_brain_metrics as _mbm

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/admin/memory-brain/metrics")
async def admin_memory_brain_metrics(
    window_seconds: int = Query(
        default=24 * 3600,
        ge=60,
        le=24 * 3600,
        description="Rolling window for the aggregate counters. Capped "
                    "at 24h because the in-memory ring buffer doesn't "
                    "retain anything older.",
    ),
    hours: int = Query(
        default=24,
        ge=1,
        le=24,
        description="Number of 1-hour buckets to return for the sparkline.",
    ),
    admin: dict = Depends(get_admin_user),
) -> dict[str, Any]:
    """Return memory_brain write/read/failure counters for the dashboard.

    Shape::

        {
          "ok": True,
          "stats":   {...aggregate over `window_seconds`...},
          "buckets": [...one entry per hour, oldest first...],
          "worker_pid": int,
          "feature_enabled": bool,
        }

    ``feature_enabled`` mirrors ``MEMORY_BRAIN_CHAT_ENABLED``; when
    false the tile should render a "feature disabled" pill instead of
    a misleading green / zero-failures state.
    """
    enabled_raw = (os.environ.get("MEMORY_BRAIN_CHAT_ENABLED", "1") or "").strip().lower()
    feature_enabled = enabled_raw not in ("0", "false", "no", "off", "")

    # Surface the live alerting threshold so the frontend banner can
    # mirror the operator-tuned value instead of a hardcoded 25%.
    # When the alert thresholds module isn't importable for any reason
    # (test harness, partial init), fall back to the hardcoded default
    # so the tile still renders.
    try:
        from metrics import _ALERT_THRESHOLDS as _at
        failure_rate_pct = float(_at.get("memory_brain_failure_rate_pct", 25.0) or 25.0)
        failure_min_sample = int(_at.get("memory_brain_failure_min_sample", 20) or 20)
    except Exception:
        failure_rate_pct = 25.0
        failure_min_sample = 20

    return {
        "ok": True,
        "stats": _mbm.get_stats(window_seconds=window_seconds),
        "buckets": _mbm.get_hourly_buckets(hours=hours),
        "worker_pid": os.getpid(),
        "feature_enabled": feature_enabled,
        "alert_threshold": {
            "failure_rate_pct":   failure_rate_pct,
            "failure_min_sample": failure_min_sample,
        },
    }
