"""Task #476 — Admin endpoints for the Cloudflare Workers KV usage monitor.

Exposes two routes:

* ``GET /admin/kv-health`` — proxies the edge worker's
  ``/api/edge/kv-usage`` snapshot so the admin notifications panel can
  show live read/write/list/delete counters and warn before quotas are
  exhausted. Admin-gated via the existing dependency.

* ``POST /admin/kv-alerts`` — ingest endpoint the worker calls when a
  binding crosses the warning threshold. Authenticated by a shared
  secret header (``X-KV-Alert-Secret``) so it cannot be abused. Records
  a notification for the admin inbox and (best-effort) emails admins
  using the existing SES pipeline so the team can react before the
  quota is fully exhausted.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request

from auth_deps import get_admin_user
from db_ops import supa_insert_notification

logger = logging.getLogger(__name__)
router = APIRouter()

_DEFAULT_EDGE_URL = "https://api.syrabit.ai"
_FETCH_TIMEOUT_S = 5.0


def _edge_url() -> str:
    return (os.environ.get("CF_EDGE_PROXY_URL") or _DEFAULT_EDGE_URL).strip().rstrip("/")


def _kv_cache_edge_url() -> str:
    """Task #424 — secondary edge worker that owns the CF_EDGE_CACHE
    binding (artifacts/syrabit/workers/edge-proxy at dispatch.syrabit.ai).
    Its `/api/edge/kv-usage` snapshot only contains the CF_EDGE_CACHE
    row, which we merge into the primary snapshot's `bindings` list so
    the admin dashboard sees one unified panel.

    When unset OR equal to ``CF_EDGE_PROXY_URL`` the secondary fetch is
    skipped — either the dispatchKvCache routes were folded into the
    primary worker, or the operator hasn't wired the secondary up yet.
    """
    raw = (os.environ.get("CF_EDGE_KV_CACHE_URL") or "").strip().rstrip("/")
    if not raw:
        return ""
    if raw == _edge_url():
        return ""
    return raw


def _edge_secret() -> str:
    """Reuse ``D1_SYNC_SECRET`` — already shared with the worker for the
    D1 sync endpoints. No new secret to provision/leak."""
    return (os.environ.get("D1_SYNC_SECRET") or "").strip()


def _kv_alert_secret() -> str:
    return (os.environ.get("KV_ALERT_SECRET") or "").strip()


@router.get("/admin/kv-health")
async def admin_kv_health(admin: dict = Depends(get_admin_user)):
    """Return the current per-binding KV usage snapshot from the edge
    worker. Surfaces ``healthy / warning / exhausted`` per binding so the
    admin dashboard can render colored status pills + percentages and
    operators can react before the quota is fully exhausted.

    Returns ``{configured: false, ...}`` when the edge proxy URL or the
    shared secret is not configured so the UI can show a clear "not yet
    configured" state instead of an error.
    """
    secret = _edge_secret()
    base = _edge_url()
    if not secret or not base:
        return {
            "configured": False,
            "reason": "CF_EDGE_PROXY_URL or D1_SYNC_SECRET is not set",
            "snapshot": None,
        }
    url = f"{base}/api/edge/kv-usage"
    try:
        async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_S) as client:
            resp = await client.get(url, headers={"X-Edge-Admin-Secret": secret})
        if resp.status_code != 200:
            return {
                "configured": True,
                "reason": f"edge returned {resp.status_code}",
                "snapshot": None,
            }
        snapshot = resp.json()
    except Exception as exc:
        logger.warning(f"[kv-health] edge fetch failed: {exc}")
        return {
            "configured": True,
            "reason": f"edge unreachable: {type(exc).__name__}",
            "snapshot": None,
        }

    # Task #424 — merge the CF_EDGE_CACHE snapshot from the secondary
    # worker (artifacts/syrabit/workers/edge-proxy) when wired. The
    # secondary fetch is best-effort: a failure must not blank out the
    # primary RATE_LIMIT / BOT_HTML_CACHE rows the dashboard already
    # depends on.
    sec_url = _kv_cache_edge_url()
    if sec_url:
        try:
            async with httpx.AsyncClient(timeout=_FETCH_TIMEOUT_S) as client:
                # Task #510 — opt into the per-isolate breakdown so the
                # admin panel can show which isolate(s) are hottest under
                # the CF_EDGE_CACHE row. Backwards compatible: the worker
                # only adds the `isolates` field when the param is set.
                sec_resp = await client.get(
                    f"{sec_url}/api/edge/kv-usage",
                    params={"breakdown": "isolates"},
                    headers={"X-Edge-Admin-Secret": secret},
                )
            if sec_resp.status_code == 200:
                _merge_kv_snapshot(snapshot, sec_resp.json())
            else:
                logger.warning(
                    f"[kv-health] kv-cache edge returned {sec_resp.status_code}"
                )
        except Exception as exc:
            logger.warning(f"[kv-health] kv-cache edge fetch failed: {exc}")

    return {"configured": True, "snapshot": snapshot}


def _merge_kv_snapshot(primary: dict, secondary: dict) -> None:
    """Merge ``secondary['bindings']`` into ``primary['bindings']`` in
    place. Bindings already present in the primary snapshot are NOT
    overwritten (the worker that owns the binding is the source of
    truth for its counters — there is no scenario today where the same
    binding is wrapped by both workers, but the guard keeps the merge
    safe if one is added later)."""
    if not isinstance(primary, dict) or not isinstance(secondary, dict):
        return
    sec_bindings = secondary.get("bindings") or []
    if not isinstance(sec_bindings, list) or not sec_bindings:
        return
    primary_bindings = primary.setdefault("bindings", [])
    if not isinstance(primary_bindings, list):
        return
    seen = {b.get("binding") for b in primary_bindings if isinstance(b, dict)}
    for entry in sec_bindings:
        if not isinstance(entry, dict):
            continue
        name = entry.get("binding")
        if not name or name in seen:
            continue
        primary_bindings.append(entry)
        seen.add(name)


@router.post("/admin/kv-alerts")
async def kv_alert_ingest(
    request: Request,
    x_kv_alert_secret: str = Header(default=""),
):
    """Worker calls this when a KV binding crosses the warning (or
    exhausted) threshold. Records an admin notification and best-effort
    emails admins via the existing SES helper.

    The secret check is constant-time-ish — we compare lengths first to
    short-circuit obvious mismatches without leaking timing on the real
    secret length.
    """
    expected = _kv_alert_secret()
    if not expected:
        raise HTTPException(status_code=503, detail="KV_ALERT_SECRET not configured")
    if not x_kv_alert_secret or len(x_kv_alert_secret) != len(expected):
        raise HTTPException(status_code=401, detail="invalid secret")
    # Constant-time-ish comparison.
    if not _consteq(x_kv_alert_secret, expected):
        raise HTTPException(status_code=401, detail="invalid secret")

    payload = await request.json()
    binding = str(payload.get("binding") or "?")
    op = str(payload.get("op") or "?")
    used = int(payload.get("used") or 0)
    quota = int(payload.get("quota") or 0)
    pct = float(payload.get("percentage") or 0)
    severity = str(payload.get("severity") or "warning")
    utc_day = str(payload.get("utc_day") or datetime.now(timezone.utc).date().isoformat())

    title = f"KV {severity}: {binding}.{op} at {pct:.0f}%"
    msg = (
        f"Cloudflare Workers KV binding `{binding}` has used {used:,} of "
        f"{quota:,} {op} ops today (UTC {utc_day}) — {pct:.1f}% of the daily "
        f"quota. The edge worker has switched to the Cache API + in-memory "
        f"fallback so pages keep rendering, but writes will be deferred "
        f"until the quota resets at 00:00 UTC."
    )

    notif = {
        "id": str(uuid.uuid4()),
        "title": title,
        "message": msg,
        "type": "warning" if severity != "exhausted" else "error",
        "channel": "in_app",
        "audience": "admins",
        "status": "sent",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "meta": {
            "kind": "kv_quota_alert",
            "binding": binding,
            "op": op,
            "used": used,
            "quota": quota,
            "percentage": pct,
            "severity": severity,
            "utc_day": utc_day,
        },
    }
    try:
        await supa_insert_notification(notif)
    except Exception as exc:
        logger.warning(f"[kv-alerts] notification persist failed: {exc}")

    # Best-effort admin email.
    asyncio.create_task(_email_admins_about_kv_alert(title, msg))

    return {"ok": True, "notif_id": notif["id"]}


def _consteq(a: str, b: str) -> bool:
    if len(a) != len(b):
        return False
    diff = 0
    for x, y in zip(a, b):
        diff |= ord(x) ^ ord(y)
    return diff == 0


async def _email_admins_about_kv_alert(title: str, message: str) -> None:
    """Email every admin (best-effort). Reuses the SES wiring from
    ``email_templates`` so we don't add another sender; if that key isn't
    set the helper logs and skips so we don't fail noisily on local dev.
    """
    try:
        from email_templates import _send  # internal helper, intentional
    except Exception as exc:
        logger.debug(f"[kv-alerts] email helper unavailable: {exc}")
        return
    admins: list[str] = []
    try:
        # Reuse the same admin lookup pattern as the SEO summary
        # dispatcher (``seo_engine._resolve_seo_summary_audience``):
        # everyone with ``is_admin=True`` and a non-empty email.
        from deps import db as _mongo_db  # type: ignore
        if _mongo_db is not None:
            cursor = _mongo_db.users.find(
                {"is_admin": True}, {"_id": 0, "email": 1}
            )
            async for u in cursor:
                e = (u.get("email") or "").strip()
                if e:
                    admins.append(e)
    except Exception as exc:
        logger.debug(f"[kv-alerts] admin lookup failed: {exc}")
    html = (
        f"<h2 style='color:#dc2626;margin:0 0 8px;'>{title}</h2>"
        f"<p style='font-size:14px;line-height:1.6;color:#374151;'>{message}</p>"
        f"<p style='font-size:12px;color:#6b7280;'>This is an automated alert from "
        f"the Syrabit edge worker's KV usage monitor (Task #476).</p>"
    )
    for email in admins:
        if not email:
            continue
        try:
            await _send(email, title, html)
        except Exception as exc:
            logger.debug(f"[kv-alerts] email send failed for {email}: {exc}")
