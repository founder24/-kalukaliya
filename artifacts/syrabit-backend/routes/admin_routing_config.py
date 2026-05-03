"""Task #297 — `GET /admin/routing-config`.

Surfaces the locked PROVIDER_PRIORITY / PROVIDER_CREDITS / POOL_WEIGHTS
configuration to the admin UI so operators can confirm at a glance which
providers actually serve each feature pool. The ``share_pct`` field is
computed with the same draw math ``select_provider`` uses (per-pool
weight override → PROVIDER_CREDITS fallback; weight-0 providers are
fallback-only and report share_pct=0).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from auth_deps import get_admin_user
from config import PROVIDER_PRIORITY, PROVIDER_CREDITS, POOL_WEIGHTS

router = APIRouter()


def _build_pool(feature: str, providers: list[str]) -> dict[str, Any]:
    """Mirror ``select_provider``'s strict-primary lock contract.

    Strict-primary lock fires only when there is a *unique* maximum weight
    that dominates the next-highest by ≥10x (or the next-highest is zero).
    Under the lock, the primary draws 100% of the time and secondaries are
    only reachable when the primary is excluded — so we report secondary
    share_pct=0 to match the actual draw distribution. Weight-0 providers
    are always fallback-only and report share_pct=0.
    """
    overrides = POOL_WEIGHTS.get(feature, {})
    weights: list[int] = []
    weighted_sum = 0
    for name in providers:
        w = overrides.get(name, PROVIDER_CREDITS.get(name, 0))
        weights.append(w)
        weighted_sum += max(w, 0)

    max_w = max(weights) if weights else 0
    top_count = sum(1 for w in weights if w == max_w)
    second_w = max((w for w in weights if w < max_w), default=0)
    # Unique max + 10x dominance over runner-up = strict primary lock.
    strict_primary = (
        max_w > 0
        and top_count == 1
        and (second_w == 0 or max_w >= 10 * second_w)
    )

    rows: list[dict[str, Any]] = []
    for name, w in zip(providers, weights):
        if w == 0:
            share_pct = 0.0
            role = "fallback_only"
        elif strict_primary and w == max_w:
            share_pct = 100.0
            role = "primary"
        elif strict_primary:
            # Secondary under strict lock — never drawn while primary healthy.
            share_pct = 0.0
            role = "secondary"
        else:
            share_pct = round((w / weighted_sum) * 100.0, 2) if weighted_sum else 0.0
            role = "primary" if w == max_w else "secondary"
        rows.append({
            "name": name,
            "weight": w,
            "share_pct": share_pct,
            "role": role,
        })
    return {
        "feature": feature,
        "providers": rows,
        "strict_primary_lock": bool(strict_primary and len(providers) > 1),
    }


@router.get("/admin/routing-config", summary="Provider routing config snapshot")
async def get_routing_config(_admin: dict = Depends(get_admin_user)) -> dict:
    pools = [_build_pool(f, p) for f, p in PROVIDER_PRIORITY.items()]
    return {
        "pools": pools,
        "credits": dict(PROVIDER_CREDITS),
        "pool_weights": {k: dict(v) for k, v in POOL_WEIGHTS.items()},
    }
