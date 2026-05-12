"""Task #109 Phase 5 — Admin proxy for the Workers Analytics Engine edge metrics.

Exposes:

* ``GET /admin/edge-analytics?range=<1h|6h|24h|7d>`` — proxies the edge
  worker's ``/api/edge/analytics`` endpoint, adding the shared
  ``X-Edge-Admin-Secret`` header (D1_SYNC_SECRET) so the secret never
  reaches the browser. Requires the admin role via ``get_admin_user``.

* ``GET /admin/edge/spa-title-misses?range=<1h|6h|24h|7d>`` (Task #12) —
  proxies the edge worker's ``/api/edge/spa-title-misses`` endpoint, which
  calls querySpaTitleMisses on the Analytics Engine and returns the top 20
  bot-crawled paths that received the generic SPA title (no route-specific
  injection match) for the given time window, ordered by hit count desc.

* ``GET /admin/edge/spa-title-miss-settings`` (Task #33) — returns the
  effective alert threshold and disabled flag (KV override if set, otherwise
  env-var defaults as reported by the edge worker).

* ``PATCH /admin/edge/spa-title-miss-settings`` (Task #33) — persists a new
  threshold and/or disabled flag to RATE_LIMIT KV on the edge worker so the
  alert can be tuned at runtime without a wrangler redeploy.

The edge worker queries the Analytics Engine GraphQL API using
``CF_ANALYTICS_TOKEN`` and returns aggregated cache/AI/rate-limit metrics
for the ``syrabit-edge-metrics`` dataset (see workers/edge-proxy/src/).
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from auth_deps import get_admin_user

logger = logging.getLogger(__name__)
router = APIRouter()

_DEFAULT_EDGE_URL = "https://api.syrabit.ai"
_FETCH_TIMEOUT_S  = 10.0

_VALID_RANGES = {"1h", "6h", "24h", "7d"}


def _edge_url() -> str:
    return (os.environ.get("CF_EDGE_PROXY_URL") or _DEFAULT_EDGE_URL).strip().rstrip("/")


def _edge_secret() -> str:
    return (os.environ.get("D1_SYNC_SECRET") or "").strip()


@router.get("/admin/edge/spa-title-misses")
async def admin_edge_spa_title_misses(
    range: str = Query(default="7d", description="Time window: 1h | 6h | 24h | 7d"),
    admin: dict = Depends(get_admin_user),
):
    """Proxy GET /api/edge/spa-title-misses from the Workers edge worker.

    Returns the top 20 bot-crawled SPA paths that did not match any title-
    injection pattern in _resolveSpaRouteMeta, ordered by hit count descending.
    Each entry: { "pathname": str, "count": int }.

    Returns ``{"configured": false}`` when the edge URL or secret is absent.
    Returns ``{"configured": true, "misses": []}`` when no data is available.
    """
    if range not in _VALID_RANGES:
        raise HTTPException(status_code=400, detail=f"Invalid range; expected one of {sorted(_VALID_RANGES)}")

    secret = _edge_secret()
    base   = _edge_url()
    if not secret or not base:
        return {
            "configured": False,
            "reason": "CF_EDGE_PROXY_URL or D1_SYNC_SECRET is not set",
            "misses": None,
        }

    url = f"{base}/api/edge/spa-title-misses"
    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_S) as client:
            resp = await client.get(
                url,
                params={"range": range},
                headers={"X-Edge-Admin-Secret": secret},
            )
        if resp.status_code == 503:
            return {
                "configured": True,
                "reason": "CF_ANALYTICS_TOKEN not set on edge worker — run: wrangler secret put CF_ANALYTICS_TOKEN",
                "misses": None,
            }
        if resp.status_code != 200:
            logger.warning("[edge-spa-title-misses] edge returned %s", resp.status_code)
            return {
                "configured": True,
                "reason": f"edge returned {resp.status_code}",
                "misses": None,
            }
        return {"configured": True, "misses": resp.json()}
    except Exception as exc:
        logger.warning("[edge-spa-title-misses] edge fetch failed: %s", exc)
        return {
            "configured": True,
            "reason": f"edge unreachable: {type(exc).__name__}",
            "misses": None,
        }


@router.get("/admin/edge/spa-title-miss-settings")
async def admin_edge_get_spa_title_miss_settings(
    admin: dict = Depends(get_admin_user),
):
    """Task #33 — Return effective SPA title-miss alert settings.

    Proxies GET /api/edge/spa-title-miss-settings from the edge worker.
    Returns the effective threshold and disabled flag (KV override if set,
    otherwise env-var defaults), plus metadata indicating whether a KV
    override is currently active.

    Returns ``{"configured": false}`` when the edge secret is not set.
    """
    secret = _edge_secret()
    base   = _edge_url()
    if not secret or not base:
        return {
            "configured": False,
            "reason": "CF_EDGE_PROXY_URL or D1_SYNC_SECRET is not set",
        }

    url = f"{base}/api/edge/spa-title-miss-settings"
    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_S) as client:
            resp = await client.get(url, headers={"X-Edge-Admin-Secret": secret})
        if resp.status_code == 503:
            return {
                "configured": True,
                "reason": "RATE_LIMIT KV not bound on edge worker",
                "threshold": 50,
                "disabled": False,
                "kv_override_set": False,
            }
        if resp.status_code != 200:
            logger.warning("[edge-spa-title-miss-settings GET] edge returned %s", resp.status_code)
            return {
                "configured": True,
                "reason": f"edge returned {resp.status_code}",
            }
        return {"configured": True, **resp.json()}
    except Exception as exc:
        logger.warning("[edge-spa-title-miss-settings GET] edge fetch failed: %s", exc)
        return {
            "configured": True,
            "reason": f"edge unreachable: {type(exc).__name__}",
        }


class SpaTitleMissSettingsPatch(BaseModel):
    threshold: Optional[int] = Field(
        default=None,
        ge=1,
        description="Alert threshold — paths with bot-hit count >= this value trigger the nightly alert.",
    )
    disabled: Optional[bool] = Field(
        default=None,
        description="Set true to pause the nightly SPA title-miss alert entirely.",
    )


@router.patch("/admin/edge/spa-title-miss-settings")
async def admin_edge_patch_spa_title_miss_settings(
    data: SpaTitleMissSettingsPatch,
    admin: dict = Depends(get_admin_user),
):
    """Task #33 — Persist SPA title-miss alert settings to edge KV.

    Proxies PUT /api/edge/spa-title-miss-settings to the edge worker, which
    writes the values to RATE_LIMIT KV.  Both fields are optional — only the
    provided fields are updated; the rest keep their current values.

    Returns the new effective settings on success.
    """
    secret = _edge_secret()
    base   = _edge_url()
    if not secret or not base:
        raise HTTPException(
            status_code=503,
            detail="CF_EDGE_PROXY_URL or D1_SYNC_SECRET is not set — cannot reach edge worker",
        )

    payload = data.model_dump(exclude_none=True)
    if not payload:
        raise HTTPException(status_code=400, detail="At least one of 'threshold' or 'disabled' must be provided")

    url = f"{base}/api/edge/spa-title-miss-settings"
    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_S) as client:
            resp = await client.put(
                url,
                json=payload,
                headers={"X-Edge-Admin-Secret": secret, "Content-Type": "application/json"},
            )
        if resp.status_code == 503:
            raise HTTPException(
                status_code=503,
                detail="RATE_LIMIT KV not bound on edge worker — settings cannot be persisted",
            )
        if resp.status_code == 400:
            raise HTTPException(status_code=400, detail=resp.json().get("error", "Bad request"))
        if resp.status_code != 200:
            logger.warning("[edge-spa-title-miss-settings PATCH] edge returned %s", resp.status_code)
            raise HTTPException(status_code=502, detail=f"Edge worker returned {resp.status_code}")
        return resp.json()
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("[edge-spa-title-miss-settings PATCH] edge fetch failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Edge worker unreachable: {type(exc).__name__}")


@router.get("/admin/edge-analytics")
async def admin_edge_analytics(
    range: str = Query(default="24h", description="Time window: 1h | 6h | 24h | 7d"),
    admin: dict = Depends(get_admin_user),
):
    """Proxy GET /api/edge/analytics from the Workers edge worker.

    Adds X-Edge-Admin-Secret so the D1_SYNC_SECRET never travels to the
    browser. Returns the same JSON payload the edge worker produces:
    totalRequests, cacheHitRate, aiRequests, topChapters, ragByProvider, etc.

    Returns ``{"configured": false}`` when the edge URL or secret is absent
    so the admin panel can show a clear setup-required state.
    """
    if range not in _VALID_RANGES:
        raise HTTPException(status_code=400, detail=f"Invalid range; expected one of {sorted(_VALID_RANGES)}")

    secret = _edge_secret()
    base   = _edge_url()
    if not secret or not base:
        return {
            "configured": False,
            "reason": "CF_EDGE_PROXY_URL or D1_SYNC_SECRET is not set",
            "metrics": None,
        }

    url = f"{base}/api/edge/analytics"
    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_S) as client:
            resp = await client.get(
                url,
                params={"range": range},
                headers={"X-Edge-Admin-Secret": secret},
            )
        if resp.status_code == 503:
            return {
                "configured": True,
                "reason": "CF_ANALYTICS_TOKEN not set on edge worker — run: wrangler secret put CF_ANALYTICS_TOKEN",
                "metrics": None,
            }
        if resp.status_code != 200:
            logger.warning("[edge-analytics] edge returned %s", resp.status_code)
            return {
                "configured": True,
                "reason": f"edge returned {resp.status_code}",
                "metrics": None,
            }
        return {"configured": True, "metrics": resp.json()}
    except Exception as exc:
        logger.warning("[edge-analytics] edge fetch failed: %s", exc)
        return {
            "configured": True,
            "reason": f"edge unreachable: {type(exc).__name__}",
            "metrics": None,
        }
