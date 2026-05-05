"""Task #383 + #386 — unified ``/admin/cf-health`` panel.

A single admin route that returns the live state of every Cloudflare
workstream so on-call has one URL to hit instead of opening N panels.

Task #383 workstreams:
  * AI Gateway observability — counters + cache-hit ratio
  * Vectorize shadow         — recall overlap + latency parity
  * R2                       — health snapshot from existing helper
  * KV + Cache Reserve       — in-process LRU + edge KV mirror stats
  * Turnstile                — siteverify pass/fail counters
  * CF Web Analytics         — flag + recent pageviews (best-effort)
  * Cloudflare Tunnel        — flag + allowed IP ranges
  * Credit-burn meters       — primary kill-switch, kept here so the
                               on-call has a single page that answers
                               "is anything CF-related on fire?"

Task #386 (Cloudflare Tier 2) extensions:
  * SSR (Pages Functions)        — render success rate + flag state
  * Speed features (Polish/...)  — optimize state + cf-polished smoke
  * Smart Tiered Cache           — status + zone hit ratio
  * D1 mirror                    — extended-table sync lag
  * Durable Objects (chat)       — request counters + DO fallback ratio
  * Translation provider mix     — distribution of translate calls

Every workstream reports ``{enabled: bool, configured: bool, ...}`` so
the admin UI can render coloured pills regardless of which flags are
on. Failures inside one workstream's snapshot must not break the
others — each call is isolated in a try/except.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, Depends

from auth_deps import get_admin_user
from config import (
    CF_AIGW_OBS_ON, CF_EDGE_CACHE_ON, CF_TUNNEL_ALLOWED_IPS,
    CF_TUNNEL_ONLY_ON, CF_WEB_ANALYTICS_ON, GA4_ENABLED,
    R2_ENABLED, R2_PRIMARY_ON, TURNSTILE_ON, VECTORIZE_SHADOW_ON,
    # Task #386 ──
    TRANSLATE_PROVIDER, SSR_ENABLED, CF_SPEED_FEATURES_ON,
    CF_TIERED_CACHE_ON, D1_MIRROR_ON, DO_CHAT_ON,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _safe(label: str, fn) -> dict[str, Any]:
    """Run a snapshot getter, swallow + log any exception. Each
    workstream is wrapped so one bad import doesn't 500 the whole
    aggregate response."""
    try:
        return fn() or {}
    except Exception as exc:
        logger.warning("[cf-health] %s snapshot failed: %s", label, exc)
        return {"error": f"{type(exc).__name__}: {exc}"}


async def _safe_async(label: str, fn) -> dict[str, Any]:
    try:
        return await fn() or {}
    except Exception as exc:
        logger.warning("[cf-health] %s async snapshot failed: %s", label, exc)
        return {"error": f"{type(exc).__name__}: {exc}"}


def _ai_gateway_snapshot() -> dict[str, Any]:
    from ai_gateway_observability import snapshot as aig_snapshot
    return aig_snapshot()


def _vectorize_shadow_snapshot() -> dict[str, Any]:
    from vectorize_shadow import snapshot as vs_snapshot
    snap = vs_snapshot()
    snap["enabled"] = bool(VECTORIZE_SHADOW_ON)
    return snap


async def _r2_snapshot() -> dict[str, Any]:
    out: dict[str, Any] = {
        "enabled": bool(R2_ENABLED),
        "primary": bool(R2_PRIMARY_ON),
    }
    try:
        from r2_storage import r2_health
        out["health"] = await r2_health()
    except Exception as exc:
        out["health"] = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
    return out


def _kv_cache_snapshot() -> dict[str, Any]:
    from kv_cache import default_cache
    return default_cache().snapshot()


def _turnstile_snapshot() -> dict[str, Any]:
    from turnstile import snapshot as ts_snapshot
    return ts_snapshot()


async def _cf_web_analytics_snapshot() -> dict[str, Any]:
    from cf_web_analytics import frontend_config, fetch_recent_pageviews, is_enabled
    out: dict[str, Any] = {
        "enabled": is_enabled(),
        "config": frontend_config(),
    }
    pv = await fetch_recent_pageviews(hours=1)
    out["recent_pageviews"] = pv
    return out


def _tunnel_snapshot() -> dict[str, Any]:
    cidrs = [c.strip() for c in CF_TUNNEL_ALLOWED_IPS.split(",") if c.strip()]
    return {
        "enabled": bool(CF_TUNNEL_ONLY_ON),
        "allowed_cidrs": cidrs,
        "cidr_count": len(cidrs),
    }


def _credit_burn_snapshot() -> dict[str, Any]:
    """Best-effort lift from the existing credit_burn_meter_runtime
    singletons — keeps the answer to "is chat fallback active?" on
    the same page so on-call doesn't need a second tab."""
    try:
        from credit_burn_meter_runtime import is_fallback_active
        active = bool(is_fallback_active())
    except Exception as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}
    return {"available": True, "fallback_active": active}


# ── Task #386 snapshots ────────────────────────────────────────────────────
async def _ssr_snapshot() -> dict[str, Any]:
    from cf_ssr_health import snapshot as ssr_snap
    out = await ssr_snap(probe=False)
    # Pages env is authoritative for the actual SSR pipeline; surface
    # both flag sources so on-call sees a drift if the two disagree.
    pages_flag = os.environ.get("PAGES_SSR_ENABLED", "").lower()
    out["backend_flag"] = bool(SSR_ENABLED)
    out["pages_flag"] = pages_flag in ("1", "true", "on", "yes")
    out["flag_drift"] = (out["backend_flag"] != out["pages_flag"])
    return out


async def _speed_features_snapshot() -> dict[str, Any]:
    from cf_speed_smoke import snapshot as speed_snap
    return await speed_snap()


async def _tiered_cache_snapshot() -> dict[str, Any]:
    from cf_tiered_cache import snapshot as tc_snap
    return await tc_snap()


def _d1_mirror_snapshot() -> dict[str, Any]:
    from d1_mirror import lag_snapshot
    return lag_snapshot()


def _do_chat_snapshot() -> dict[str, Any]:
    from do_chat import snapshot as do_snap
    return do_snap()


def _translate_provider_snapshot() -> dict[str, Any]:
    from translate_provider_metrics import snapshot as tp_snap
    snap = tp_snap()
    snap["flag"] = TRANSLATE_PROVIDER
    return snap


def _cache_rules_snapshot() -> dict[str, Any]:
    """Per-route-group cache contract + D1 read-prefer counters."""
    from cf_cache_rules import policy_payload
    from d1_mirror import read_counters_snapshot
    payload = policy_payload()
    payload["d1_read_counters"] = read_counters_snapshot()
    return payload


@router.post("/admin/cf-tier2/apply")
async def admin_cf_tier2_apply(admin: dict = Depends(get_admin_user)):
    """Push Speed features + Tiered Cache + Cache Rules to the live CF
    zone. Each step is gated by its own flag (CF_SPEED_FEATURES_ON,
    CF_TIERED_CACHE_ON) and returns its own result block so on-call
    can see exactly which sub-step succeeded."""
    out: dict[str, Any] = {}
    try:
        from cf_speed_smoke import apply_speed_features
        out["speed_features"] = await apply_speed_features()
    except Exception as exc:
        out["speed_features"] = {"applied": False,
                                 "error": f"{type(exc).__name__}: {exc}"}
    try:
        from cf_tiered_cache import apply_tiered_cache
        out["tiered_cache"] = await apply_tiered_cache()
    except Exception as exc:
        out["tiered_cache"] = {"applied": False,
                               "error": f"{type(exc).__name__}: {exc}"}
    try:
        from cf_cache_rules import apply_rules_via_api
        out["cache_rules"] = await apply_rules_via_api()
    except Exception as exc:
        out["cache_rules"] = {"applied": False,
                              "error": f"{type(exc).__name__}: {exc}"}
    return out


@router.get("/admin/cf-health")
async def admin_cf_health(admin: dict = Depends(get_admin_user)):
    """One-stop aggregate health for every Cloudflare workstream.

    Returns 200 even when individual workstreams are unconfigured —
    each block carries its own ``enabled`` / ``configured`` shape so
    the admin UI can render coloured status pills rather than blank
    placeholders.
    """
    cf_web_analytics = await _safe_async("cf_web_analytics",
                                         _cf_web_analytics_snapshot)
    r2 = await _safe_async("r2", _r2_snapshot)
    # Task #386 — async snapshots are gathered to keep the route
    # under the same single-digit-second budget as before.
    ssr = await _safe_async("ssr", _ssr_snapshot)
    speed_features = await _safe_async("speed_features", _speed_features_snapshot)
    tiered_cache = await _safe_async("tiered_cache", _tiered_cache_snapshot)

    return {
        "flags": {
            "CF_AIGW_OBS_ON": bool(CF_AIGW_OBS_ON),
            "VECTORIZE_SHADOW_ON": bool(VECTORIZE_SHADOW_ON),
            "R2_PRIMARY_ON": bool(R2_PRIMARY_ON),
            "CF_EDGE_CACHE_ON": bool(CF_EDGE_CACHE_ON),
            "TURNSTILE_ON": bool(TURNSTILE_ON),
            "CF_WEB_ANALYTICS_ON": bool(CF_WEB_ANALYTICS_ON),
            "CF_TUNNEL_ONLY_ON": bool(CF_TUNNEL_ONLY_ON),
            "GA4_ENABLED": bool(GA4_ENABLED),
            # Task #386 ──
            "TRANSLATE_PROVIDER": str(TRANSLATE_PROVIDER),
            "SSR_ENABLED": bool(SSR_ENABLED),
            "CF_SPEED_FEATURES_ON": bool(CF_SPEED_FEATURES_ON),
            "CF_TIERED_CACHE_ON": bool(CF_TIERED_CACHE_ON),
            "D1_MIRROR_ON": bool(D1_MIRROR_ON),
            "DO_CHAT_ON": bool(DO_CHAT_ON),
        },
        "ai_gateway": _safe("ai_gateway", _ai_gateway_snapshot),
        "vectorize_shadow": _safe("vectorize_shadow",
                                  _vectorize_shadow_snapshot),
        "r2": r2,
        "kv_cache": _safe("kv_cache", _kv_cache_snapshot),
        "turnstile": _safe("turnstile", _turnstile_snapshot),
        "cf_web_analytics": cf_web_analytics,
        "tunnel": _safe("tunnel", _tunnel_snapshot),
        "credit_burn": _safe("credit_burn", _credit_burn_snapshot),
        # Task #386 rows
        "ssr": ssr,
        "speed_features": speed_features,
        "tiered_cache": tiered_cache,
        "d1_mirror": _safe("d1_mirror", _d1_mirror_snapshot),
        "do_chat": _safe("do_chat", _do_chat_snapshot),
        "translate_provider": _safe("translate_provider",
                                    _translate_provider_snapshot),
        "cache_rules": _safe("cache_rules", _cache_rules_snapshot),
    }
