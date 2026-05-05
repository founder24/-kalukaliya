"""Cloudflare Polish/Mirage/AutoMinify activation gate + smoke check
that probes the ``cf-polished`` response header. Surfaced in
``/admin/cf-health.speed_features``."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# Asset used by the smoke check. Must be a CF-cached image that lives
# at the apex zone so Polish has the chance to optimise it. The opengraph
# JPEG is shipped by every Pages deploy and is small enough that the
# probe cost is negligible.
DEFAULT_SMOKE_URL = os.environ.get(
    "CF_POLISH_SMOKE_URL",
    "https://syrabit.ai/opengraph.jpg",
).strip()


def is_enabled() -> bool:
    """True iff CF_SPEED_FEATURES_ON is set in the environment."""
    from config import CF_SPEED_FEATURES_ON
    return bool(CF_SPEED_FEATURES_ON)


async def apply_speed_features() -> dict[str, Any]:
    """Apply Polish + Mirage + Auto Minify (and the rest of the
    enterprise Speed package) when the flag is on.

    No-op when ``CF_SPEED_FEATURES_ON`` is unset; returns
    ``{"applied": False, "reason": "flag_off"}`` so callers can
    surface the state without branching.
    """
    if not is_enabled():
        return {"applied": False, "reason": "flag_off"}
    try:
        from cf_enterprise import speed_optimize_all
    except Exception as exc:
        return {"applied": False, "reason": f"import_failed: {exc}"}
    result = await speed_optimize_all()
    return {"applied": True, "result": result}


async def polish_smoke(url: str | None = None, timeout_s: float = 6.0) -> dict[str, Any]:
    """Fetch a small image and report whether Polish + Mirage are live.

    Returns a dict with the headers we care about so the admin panel
    can render a coloured pill::

        {
          "url": "...",
          "ok": True,
          "status": 200,
          "cf_polished": "qual=85",      # missing => Polish off
          "cf_bgj": "imgq=85",           # Mirage / image optimisations
          "cf_cache_status": "HIT",
          "cf_ray": "...",
        }

    Failure modes are captured into ``ok=False`` rather than raised so
    the cf-health route stays 200.
    """
    target = (url or DEFAULT_SMOKE_URL).strip()
    if not target:
        return {"ok": False, "reason": "no_smoke_url"}
    headers = {
        # Force Polish to evaluate the request rather than serve a
        # pre-existing browser-cached copy.
        "Accept": "image/webp,image/avif,image/*;q=0.8",
        "User-Agent": "syrabit-cf-health/1.0 (polish-smoke)",
        "Cache-Control": "no-cache",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout_s, follow_redirects=True) as client:
            resp = await client.get(target, headers=headers)
    except Exception as exc:
        return {"ok": False, "url": target, "reason": f"{type(exc).__name__}: {exc}"}

    out: dict[str, Any] = {
        "url": target,
        "ok": resp.status_code < 400,
        "status": resp.status_code,
        "cf_polished": resp.headers.get("cf-polished"),
        "cf_bgj": resp.headers.get("cf-bgj"),
        "cf_cache_status": resp.headers.get("cf-cache-status"),
        "cf_ray": resp.headers.get("cf-ray"),
        "content_type": resp.headers.get("content-type"),
    }
    out["polish_active"] = bool(out["cf_polished"])
    out["mirage_active"] = bool(out["cf_bgj"])
    return out


async def snapshot() -> dict[str, Any]:
    """Aggregate snapshot for ``/admin/cf-health``."""
    enabled = is_enabled()
    out: dict[str, Any] = {
        "enabled": enabled,
        "configured": False,
        "smoke": None,
        "settings": None,
    }
    if not enabled:
        return out

    smoke, settings = await asyncio.gather(
        polish_smoke(),
        _safe_speed_status(),
        return_exceptions=True,
    )
    out["smoke"] = smoke if not isinstance(smoke, Exception) else {"ok": False, "reason": str(smoke)}
    out["settings"] = settings if not isinstance(settings, Exception) else {"error": str(settings)}
    out["configured"] = bool(isinstance(settings, dict) and settings.get("configured"))
    return out


async def _safe_speed_status() -> dict[str, Any]:
    try:
        from cf_enterprise import speed_status
        return await speed_status()
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
