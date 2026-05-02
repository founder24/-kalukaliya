"""routes.admin_credits — Provider credit-burn panel (Task #250).

GET /admin/credits/summary
  Returns per-provider rows: startup credit amount, current weight,
  estimated remaining credits (manually maintained), and "credits low"
  warning when the provider's PROVIDER_CREDITS value drops below 20% of
  the original reference amount.

  Operators update PROVIDER_CREDITS in config.py (or via env override)
  when topping up or consuming credits; this endpoint reads those values
  at request time (no DB required).

GET /admin/credits/provider-weights
  Returns the weighted pool for each of the 15 feature keys — useful for
  verifying that the weighted round-robin will actually send traffic to
  the expected providers.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends

from auth_deps import get_admin_user

logger = logging.getLogger(__name__)
router = APIRouter(tags=["admin-credits"])

# Reference (original programme) amounts — used to compute "credits low" %.
# These should only change when a new programme is activated or renewed.
_CREDIT_REFERENCE: dict[str, int] = {
    "vertex":        2000,
    "bedrock":       1000,
    "azure_openai":  2500,   # $2.5k Azure for Startups — weight is fixed at 1 though
    "sarvam":         500,
    "cartesia":       500,
    "elevenlabs":     500,
    "assemblyai":    1000,
    "cohere":        1000,
    "pinecone_ai":    500,
    "exa_ai":        1000,
    "tavily":         500,
    "mongodb_atlas":    0,
    "workers_ai":       0,
}

_PROGRAMME_NAMES: dict[str, str] = {
    "vertex":        "Google Cloud for Startups",
    "bedrock":       "AWS Activate",
    "azure_openai":  "Azure for Startups",
    "sarvam":        "Sarvam Startup Credits",
    "cartesia":      "Cartesia Startup Credits",
    "elevenlabs":    "ElevenLabs Startup Credits",
    "assemblyai":    "AssemblyAI Startup Credits",
    "cohere":        "Cohere Startup Credits",
    "pinecone_ai":   "Pinecone Startup Credits",
    "exa_ai":        "Exa Startup Credits",
    "tavily":        "Tavily Startup Credits",
    "mongodb_atlas": "MongoDB Atlas Free Tier",
    "workers_ai":    "Cloudflare Workers AI Free Tier",
}

_CREDITS_LOW_THRESHOLD = 0.20   # < 20% of original = "credits_low"


def _build_provider_rows() -> list[dict[str, Any]]:
    from config import PROVIDER_CREDITS
    rows = []
    for provider, ref_amount in _CREDIT_REFERENCE.items():
        current = PROVIDER_CREDITS.get(provider, 0)
        is_fallback_only = (current == 0)
        credits_low = (
            not is_fallback_only
            and ref_amount > 0
            and current < ref_amount * _CREDITS_LOW_THRESHOLD
        )
        pct_remaining = (
            round(current / ref_amount * 100, 1)
            if ref_amount > 0 and not is_fallback_only
            else None
        )
        rows.append({
            "provider":          provider,
            "programme":         _PROGRAMME_NAMES.get(provider, provider),
            "reference_credits": ref_amount,
            "current_credits":   current,
            "pct_remaining":     pct_remaining,
            "weight":            current,
            "fallback_only":     is_fallback_only,
            "credits_low":       credits_low,
        })
    # Sort: fallback-only last, then by current_credits descending.
    rows.sort(key=lambda r: (r["fallback_only"], -r["current_credits"]))
    return rows


def _build_feature_pools() -> dict[str, Any]:
    import random
    from config import PROVIDER_PRIORITY, PROVIDER_CREDITS

    result = {}
    for feature, providers in PROVIDER_PRIORITY.items():
        weighted = [
            {"provider": p, "weight": PROVIDER_CREDITS.get(p, 0)}
            for p in providers
            if PROVIDER_CREDITS.get(p, 0) > 0
        ]
        fallbacks = [
            p for p in providers
            if PROVIDER_CREDITS.get(p, 0) == 0
        ]
        total_weight = sum(w["weight"] for w in weighted)
        for entry in weighted:
            entry["selection_pct"] = (
                round(entry["weight"] / total_weight * 100, 1)
                if total_weight > 0 else 0.0
            )
        result[feature] = {
            "weighted_pool":   weighted,
            "fallback_order":  fallbacks,
            "total_weight":    total_weight,
        }
    return result


@router.get(
    "/admin/credits/summary",
    summary="Provider credit-burn summary",
    description=(
        "Returns per-provider rows showing startup credit programme, current weight, "
        "and a 'credits_low' flag when below 20% of original amount. "
        "Update PROVIDER_CREDITS in config.py when topping up."
    ),
)
async def admin_credits_summary(
    _admin: dict = Depends(get_admin_user),
):
    rows = _build_provider_rows()
    low_count = sum(1 for r in rows if r["credits_low"])
    return {
        "providers": rows,
        "total_providers": len(rows),
        "credits_low_count": low_count,
        "note": (
            "Update PROVIDER_CREDITS in config.py (and redeploy) to reflect "
            "programme top-ups or manual credit consumption. Weights are static."
        ),
    }


@router.get(
    "/admin/credits/provider-weights",
    summary="Weighted provider pools per feature",
    description=(
        "Shows the weighted round-robin pool for each of the 15 feature keys. "
        "Use this to verify that traffic will be sent to the expected providers."
    ),
)
async def admin_credits_provider_weights(
    _admin: dict = Depends(get_admin_user),
):
    pools = _build_feature_pools()
    return {
        "features": pools,
        "total_features": len(pools),
    }
