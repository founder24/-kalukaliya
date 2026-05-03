"""Task #315 — Admin proxy for the R2 cold-storage watchdog snapshot.

The monthly Task #314 watchdog (workers/edge-proxy/src/r2-storage-class-
alert.ts) persists its last-evaluated state to KV under
``r2_storage_class_alert:state``. Operators currently have to wait for
the next cron tick or open the Cloudflare dashboard to confirm the rules
are still in place. This module exposes that state via the existing
admin auth gate so the dashboard can render an "R2 cold storage" tile
alongside the KV / bot-cache panels, and lets an operator manually
re-trigger an evaluation after re-applying the rules.

Routes:

* ``GET /admin/r2-storage-health`` — proxies the worker's
  ``/api/edge/r2-storage-health`` snapshot. Read-only.

* ``POST /admin/r2-storage-health/run`` — proxies the worker's
  ``/api/edge/r2-storage-health/run`` endpoint to re-execute
  ``runR2StorageClassAlert`` on demand. The worker enforces a
  short cooldown so the button can't be spammed past the 28-day
  cooldown anchor that protects against duplicate paging.

* ``POST /admin/r2-storage-health/reset-watchdog`` — Task #322.
  Proxies the worker's ``/api/edge/r2-storage-health/reset-watchdog``
  endpoint to clear the secondary ``consecutive_query_failures`` +
  ``query_fail_last_fired_at`` fields after an operator has rotated
  ``R2_STORAGE_ANALYTICS_TOKEN``, so the red watchdog-blind badge on
  the admin tile clears immediately instead of waiting ~30 days for
  the next monthly evaluation. The operator email is logged here for
  the audit trail (the worker doesn't have an identity to log).

Both routes reuse ``D1_SYNC_SECRET`` for the worker handshake (same
shared secret as ``/admin/kv-health``). No new secret to provision.
"""
from __future__ import annotations

import logging
import os

import httpx
from fastapi import APIRouter, Depends, HTTPException

from auth_deps import get_admin_user

logger = logging.getLogger(__name__)
router = APIRouter()

_DEFAULT_EDGE_URL = "https://api.syrabit.ai"
_FETCH_TIMEOUT_S = 5.0
_RUN_TIMEOUT_S = 30.0


def _edge_url() -> str:
    return (os.environ.get("CF_EDGE_PROXY_URL") or _DEFAULT_EDGE_URL).strip().rstrip("/")


def _edge_secret() -> str:
    return (os.environ.get("D1_SYNC_SECRET") or "").strip()


@router.get("/admin/r2-storage-health")
async def admin_r2_storage_health(admin: dict = Depends(get_admin_user)):
    """Return the persisted R2 cold-storage watchdog state. Returns
    ``{configured: false, ...}`` when the edge proxy URL or shared
    secret is not configured so the UI can render a clear placeholder
    instead of erroring out."""
    secret = _edge_secret()
    base = _edge_url()
    if not secret or not base:
        return {
            "configured": False,
            "reason": "CF_EDGE_PROXY_URL or D1_SYNC_SECRET is not set",
            "state": None,
        }
    url = f"{base}/api/edge/r2-storage-health"
    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_S) as client:
            resp = await client.get(url, headers={"X-Edge-Admin-Secret": secret})
        if resp.status_code != 200:
            return {
                "configured": True,
                "reason": f"edge returned {resp.status_code}",
                "state": None,
            }
        return resp.json()
    except Exception as exc:  # noqa: BLE001 — surface as configured-but-degraded
        logger.warning(f"[r2-storage-health] edge fetch failed: {exc}")
        return {
            "configured": True,
            "reason": f"edge unreachable: {type(exc).__name__}",
            "state": None,
        }


@router.post("/admin/r2-storage-health/run")
async def admin_r2_storage_health_run(admin: dict = Depends(get_admin_user)):
    """Re-trigger the R2 cold-storage watchdog on demand. The worker
    enforces a short cooldown (returns 429) so the button can't be
    spammed; the 28-day per-alert cooldown inside the watchdog itself
    still protects against duplicate pages.
    """
    secret = _edge_secret()
    base = _edge_url()
    if not secret or not base:
        raise HTTPException(
            status_code=503,
            detail="CF_EDGE_PROXY_URL or D1_SYNC_SECRET is not set",
        )
    url = f"{base}/api/edge/r2-storage-health/run"
    try:
        async with httpx.AsyncClient(timeout=_RUN_TIMEOUT_S) as client:
            resp = await client.post(url, headers={"X-Edge-Admin-Secret": secret})
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[r2-storage-health/run] edge POST failed: {exc}")
        raise HTTPException(
            status_code=502,
            detail=f"edge unreachable: {type(exc).__name__}",
        ) from exc
    # Pass the worker's status code straight through so the UI can
    # surface the 429 cooldown / 503 misconfig states without the
    # backend having to translate them.
    if resp.status_code == 200:
        return resp.json()
    try:
        body = resp.json()
    except Exception:
        body = {"detail": resp.text[:300]}
    raise HTTPException(status_code=resp.status_code, detail=body)


@router.post("/admin/r2-storage-health/reset-watchdog")
async def admin_r2_storage_health_reset_watchdog(
    admin: dict = Depends(get_admin_user),
):
    """Task #322 — clear the secondary "watchdog blind" counter in KV
    after the operator has rotated ``R2_STORAGE_ANALYTICS_TOKEN``.

    The worker performs the actual KV mutation; we proxy with the
    shared ``D1_SYNC_SECRET`` handshake (same gate as the re-evaluate
    button) and log the operator email for the audit trail.
    """
    secret = _edge_secret()
    base = _edge_url()
    if not secret or not base:
        raise HTTPException(
            status_code=503,
            detail="CF_EDGE_PROXY_URL or D1_SYNC_SECRET is not set",
        )
    url = f"{base}/api/edge/r2-storage-health/reset-watchdog"
    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_S) as client:
            resp = await client.post(url, headers={"X-Edge-Admin-Secret": secret})
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"[r2-storage-health/reset-watchdog] edge POST failed: {exc}"
        )
        raise HTTPException(
            status_code=502,
            detail=f"edge unreachable: {type(exc).__name__}",
        ) from exc
    if resp.status_code == 200:
        actor = (
            admin.get("email") or admin.get("id") or admin.get("sub") or "unknown"
        )
        logger.info(
            f"[r2-storage-health/reset-watchdog] watchdog-blind counter reset "
            f"by admin={actor}"
        )
        return resp.json()
    try:
        body = resp.json()
    except Exception:
        body = {"detail": resp.text[:300]}
    raise HTTPException(status_code=resp.status_code, detail=body)
