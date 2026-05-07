"""routes.admin_sarvam_health — Task #553.

Admin-only ``GET /api/admin/health/sarvam`` returning a JSON shape
mirroring the Vertex / Workers-AI tiles on the AdminHealth dashboard.
Wired into the admin "Inference providers" section via
``SarvamHealthCard.jsx``.

Response shape::

    {
      "configured": bool,            # SARVAM_API_KEY set in env
      "status":     "healthy" | "degraded" | "down" | "not_configured",
      "model":      "sarvam-m",
      "role":       "primary",       # in PROVIDER_PRIORITY['assamese_rag_chat']
      "fallback":   "workers_ai_indic",
      "window_s":   3600,
      "ok":         int,
      "err":        int,
      "total":      int,
      "success_rate":  float,        # 0.0..1.0 over the last hour
      "alert":      bool,            # success_rate < 0.95 with >=20 samples
      "alert_floor": 0.95,
      "per_user_monthly_cap": int,   # 30 by default; 0 = disabled
      "last_updated": iso8601,
      "error":      str | null,      # only populated when configured=False
    }

Status mapping:
  * not_configured  → SARVAM_API_KEY missing
  * down            → success_rate == 0 with at least min_samples
  * degraded        → alert=True (success_rate below floor) OR no traffic
                      in the last window AND configured=True (we can't
                      affirm health, so we surface "degraded" rather
                      than a misleading green pill)
  * healthy         → success_rate >= 0.95 with >= min_samples
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends

from auth_deps import get_admin_user
from providers.sarvam import (
    PER_USER_MONTHLY_CAP,
    SUCCESS_RATE_ALERT_FLOOR,
    success_rate_snapshot,
)

router = APIRouter()


def _classify(*, configured: bool, snap: dict) -> str:
    if not configured:
        return "not_configured"
    total = snap.get("total", 0)
    if total == 0:
        # Silent — nothing to confirm health from. Prefer "degraded"
        # over a misleading green pill (V4 §12 — fail loud).
        return "degraded"
    if snap.get("ok", 0) == 0:
        return "down"
    if snap.get("alert"):
        return "degraded"
    return "healthy"


@router.get("/admin/health/sarvam")
async def admin_sarvam_health(admin: dict = Depends(get_admin_user)) -> dict[str, Any]:
    """Return the Sarvam tile payload. Always 200 — failures show up
    inline so the dashboard never goes blank."""
    # Local import keeps the module import-safe under the test stub.
    try:
        from config import SARVAM_API_KEY as _key  # type: ignore
        configured = bool(_key)
    except Exception:
        configured = False

    snap = success_rate_snapshot()

    return {
        "configured": configured,
        "status": _classify(configured=configured, snap=snap),
        "model": "sarvam-m",
        "role": "primary",
        "fallback": "workers_ai_indic",
        "window_s": snap["window_s"],
        "ok": snap["ok"],
        "err": snap["err"],
        "total": snap["total"],
        "success_rate": snap["success_rate"],
        "alert": snap["alert"],
        "alert_floor": SUCCESS_RATE_ALERT_FLOOR,
        "min_samples": snap["min_samples"],
        "per_user_monthly_cap": PER_USER_MONTHLY_CAP,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "error": None if configured else "SARVAM_API_KEY not set",
    }
