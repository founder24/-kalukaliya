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
from config import (
    PROVIDER_PRIORITY,
    PROVIDER_CREDITS,
    POOL_WEIGHTS,
    MONGO_URL,
    _DEEPGRAM_KEY,
    _COHERE_KEY,
    _BASETEN_KEY,
    _ASSEMBLYAI_KEY,
    _ELEVENLABS_KEY,
    _VOYAGE_AI_KEY,
    _SARVAM_LLM_KEY,
    _XAI_KEY,
    _OPENAI_KEY,
)

router = APIRouter()


def _key_status_for(name: str) -> dict[str, Any]:
    """Surface credential presence for each routed provider.

    Returns ``{configured: bool, source: str}`` where ``source`` names the
    env var (or BYOK slug) that supplied the credential. Used by the admin
    UI to render an at-a-glance "Configured / Missing" badge per provider
    card without exposing the actual secret value.
    """
    import os
    from config import _GEMINI_KEY_RAW, BYOK_PLACEHOLDER

    def _present(val: str | None) -> bool:
        return bool(val) and val != BYOK_PLACEHOLDER

    if name == "vertex":
        return {"configured": bool(os.environ.get("VERTEX_PROJECT_ID")), "source": "VERTEX_PROJECT_ID"}
    if name in ("gemini", "google_ai_studio"):
        return {"configured": _present(_GEMINI_KEY_RAW), "source": "GEMINI_API_KEY"}
    if name == "azure_openai":
        return {"configured": bool(os.environ.get("AZURE_OPENAI_ENDPOINT")), "source": "AZURE_OPENAI_ENDPOINT"}
    if name == "workers_ai" or name == "workers_ai_indic":
        cf = os.environ.get("CLOUDFLARE_API_TOKEN") or os.environ.get("CF_API_TOKEN")
        return {"configured": bool(cf), "source": "CLOUDFLARE_API_TOKEN"}
    if name == "sarvam":
        return {"configured": _present(_SARVAM_LLM_KEY), "source": "SARVAM_API_KEY"}
    if name == "deepgram":
        return {"configured": _present(_DEEPGRAM_KEY), "source": "DEEPGRAM_API_KEY"}
    if name == "elevenlabs":
        return {"configured": _present(_ELEVENLABS_KEY), "source": "ELEVENLABS_API_KEY"}
    if name == "assemblyai":
        return {"configured": _present(_ASSEMBLYAI_KEY), "source": "ASSEMBLYAI_API_KEY"}
    if name == "cohere":
        return {"configured": _present(_COHERE_KEY), "source": "COHERE_API_KEY"}
    if name == "voyage_ai":
        return {"configured": _present(_VOYAGE_AI_KEY), "source": "VOYAGE_AI_API_KEY"}
    if name == "baseten":
        return {"configured": _present(_BASETEN_KEY), "source": "BASETEN_API_KEY"}
    if name == "pinecone_ai":
        return {"configured": bool(os.environ.get("PINECONE_API_KEY")), "source": "PINECONE_API_KEY"}
    if name == "mongodb_atlas":
        return {"configured": bool(MONGO_URL and "localhost" not in MONGO_URL), "source": "MONGO_URL"}
    if name == "xai":
        return {"configured": _present(_XAI_KEY), "source": "XAI_API_KEY"}
    if name == "openai":
        return {"configured": _present(_OPENAI_KEY), "source": "OPENAI_API_KEY"}
    if name == "bedrock":
        return {"configured": bool(os.environ.get("AWS_REGION")), "source": "AWS_REGION"}
    return {"configured": False, "source": "unknown"}


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
    # Distinct provider names across all pools — the admin UI renders one
    # card per provider showing every pool it participates in plus the
    # credential-presence badge from ``_key_status_for``.
    seen: set[str] = set()
    for pool in pools:
        for row in pool["providers"]:
            seen.add(row["name"])
    key_status = {name: _key_status_for(name) for name in sorted(seen)}
    return {
        "pools": pools,
        "credits": dict(PROVIDER_CREDITS),
        "pool_weights": {k: dict(v) for k, v in POOL_WEIGHTS.items()},
        "key_status": key_status,
    }
