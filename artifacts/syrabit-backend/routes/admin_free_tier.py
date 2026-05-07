"""routes/admin_free_tier — Task #581 §L8 admin observability endpoint.

`GET /api/health/free-tier-dispatch` (admin-only) returns the rolling
24h `free_tier_dispatch.snapshot(...)` breakdown for English + Assamese
plus the live free-tier MeterD ladder state and the long-context
paywall settings. Wired into the admin Observability panel as the
"Free-tier dispatch breakdown" tile.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from auth_deps import get_admin_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/health", tags=["admin", "observability"])


@router.get("/free-tier-dispatch")
async def free_tier_dispatch_snapshot(_admin=Depends(get_admin_user)) -> dict:
    """Return the rolling-24h free-tier dispatch breakdown.

    Response shape:
        {
          "en": { ...free_tier_dispatch.snapshot("en")... },
          "as": { ...free_tier_dispatch.snapshot("as")... },
          "ladder": {
            "spend_fraction": 0.42,
            "state": { ...cost_caps.free_tier_dispatch_state(...)... },
            "thresholds": {
              "tighten_1": 0.40, "tighten_2": 0.50,
              "tighten_3": 0.55, "tighten_4": 0.58,
              "monthly_cap_usd": 100.0,
            },
          },
          "long_context_paywall_input_tokens": 8000,
          "alarm_target_paid_escalation_pct": 0.05,
        }
    """
    try:
        import free_tier_dispatch as ftd
        from cost_caps import (
            free_tier_dispatch_state,
            DEGRADATION_PCT_FREE_TIGHTEN_1,
            DEGRADATION_PCT_FREE_TIGHTEN_2,
            DEGRADATION_PCT_FREE_TIGHTEN_3,
            DEGRADATION_PCT_FREE_TIGHTEN_4,
            LONG_CONTEXT_FREE_MAX_INPUT_TOKENS,
            _monthly_total_usd_cap,
        )
        from credit_burn_meter_runtime import monthly_spend_fraction
    except Exception as exc:
        logger.error("[admin-free-tier] import failed: %s", exc)
        raise HTTPException(status_code=500, detail="dispatch snapshot unavailable")

    spend_frac = monthly_spend_fraction()
    return {
        "en": ftd.snapshot(lang="en"),
        "as": ftd.snapshot(lang="as"),
        "ladder": {
            "spend_fraction": round(spend_frac, 4),
            "state": free_tier_dispatch_state(spend_frac),
            "thresholds": {
                "tighten_1": DEGRADATION_PCT_FREE_TIGHTEN_1,
                "tighten_2": DEGRADATION_PCT_FREE_TIGHTEN_2,
                "tighten_3": DEGRADATION_PCT_FREE_TIGHTEN_3,
                "tighten_4": DEGRADATION_PCT_FREE_TIGHTEN_4,
                "monthly_cap_usd": _monthly_total_usd_cap(),
            },
        },
        "long_context_paywall_input_tokens": LONG_CONTEXT_FREE_MAX_INPUT_TOKENS,
        "alarm_target_paid_escalation_pct": 0.05,
    }


__all__ = ["router"]
