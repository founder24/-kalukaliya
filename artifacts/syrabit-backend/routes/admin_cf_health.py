"""Task #383 — unified ``/admin/cf-health`` panel.

A single admin route that returns the live state of every Cloudflare
workstream we activated in Task #383, so on-call has one URL to hit
instead of opening seven panels:

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

Every workstream reports ``{enabled: bool, configured: bool, ...}`` so
the admin UI can render coloured pills regardless of which flags are
on. Failures inside one workstream's snapshot must not break the
others — each call is isolated in a try/except.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends

from auth_deps import get_admin_user
from config import (
    CF_AIGW_OBS_ON, CF_EDGE_CACHE_ON, CF_TUNNEL_ALLOWED_IPS,
    CF_TUNNEL_ONLY_ON, CF_WEB_ANALYTICS_ON, GA4_ENABLED,
    R2_ENABLED, R2_PRIMARY_ON, TURNSTILE_ON, VECTORIZE_SHADOW_ON,
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
    }


async def _safe_async(label: str, fn) -> dict[str, Any]:
    try:
        return await fn() or {}
    except Exception as exc:
        logger.warning("[cf-health] %s async snapshot failed: %s", label, exc)
        return {"error": f"{type(exc).__name__}: {exc}"}
