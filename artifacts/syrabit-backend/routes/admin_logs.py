"""Task #944 — Unified log explorer admin routes + ingest endpoint
+ Cloudflare GraphQL pull loop.

Endpoints
---------
* ``POST /api/logs/ingest``                       — token-auth (edge worker)
* ``GET  /api/admin/logs``                        — admin filter / list
* ``GET  /api/admin/logs/export``                 — admin streaming export
* ``GET  /api/admin/logs/trace/{cid}``            — admin trace lookup
* ``GET  /api/admin/logs/status``                 — admin config snapshot
* ``POST /api/admin/logs/pause``                  — admin kill-switch on
* ``POST /api/admin/logs/resume``                 — admin kill-switch off
* ``POST /api/admin/logs/rotate-token``           — admin token rotation
* ``DELETE /api/admin/logs``                      — admin destructive purge
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import logging
import os
import random
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from auth_deps import get_admin_user
from deps import db
import unified_logs_dao as _dao
from db_ops import supa_insert_activity_log

logger = logging.getLogger(__name__)
router = APIRouter()

LOG_INGEST_TOKEN_HEADER = "x-logs-ingest-token"
LOG_INGEST_API_CONFIG_KEY = "unified_logs_ingest_token"
LOG_PAUSE_API_CONFIG_KEY = "unified_logs_paused"

CF_PULL_LOCK_ID = "unified_logs_cf_pull_lock"
CF_PULL_CURSOR_FIELD = "cursor"
CF_PULL_INTERVAL_S = int(os.environ.get("UNIFIED_LOGS_CF_PULL_INTERVAL_S", "60") or "60")
CF_PULL_LOOKBACK_MIN = int(os.environ.get("UNIFIED_LOGS_CF_PULL_LOOKBACK_MIN", "5") or "5")
CF_PULL_MAX_LOOKBACK_MIN = 60
CF_PULL_LIMIT = int(os.environ.get("UNIFIED_LOGS_CF_PULL_LIMIT", "200") or "200")
# Task #948 — CF httpRequestsAdaptiveGroups asks for AT MOST
# `CF_PULL_LIMIT` grouped buckets per call. On busy hours a single
# window can produce far more distinct (path, status, colo, host,
# country, cache, method) buckets than that, and the surplus is
# silently dropped — but the cursor still advances past the window,
# so the surplus is never backfilled.
#
# CF's GraphQL `httpRequestsAdaptiveGroups` does not expose proper
# cursor pagination, so we paginate by recursively halving the time
# window on minute boundaries (CF aggregates per minute, so 60s is
# the natural floor — we cannot subdivide finer than that without
# losing the time dimension entirely). When the floor is reached and
# the bucket count STILL hits the limit, we record the saturation in
# the cursor doc so an operator sees the gap instead of silently
# losing data.
CF_PULL_MIN_WINDOW_S = 60
CF_PULL_MAX_SUBDIVISIONS = int(
    os.environ.get("UNIFIED_LOGS_CF_PULL_MAX_SUBDIVISIONS", "12") or "12"
)

# Task #953 — rolling 24h pagination-cost history. Each tick appends a
# small ``{ts, calls, subdivisions, saturated}`` entry to ``cf_pull_history``
# in the cursor doc; the admin /status endpoint computes the trend
# aggregate (totals, max, % subdivided) over the last 24h. Without
# this, an operator only ever sees the LAST tick's numbers and would
# miss a slow drift from "1 call/tick" to "8 calls/tick" as traffic
# grows.
#
# Window: 24 hours (matches the CF GraphQL daily quota cycle).
# Hard cap: 2000 entries — safety guard against a misconfigured
# ``CF_PULL_INTERVAL_S`` (e.g. 1s) blowing up the cursor doc. At the
# default 60s interval, 24h = 1440 entries (well under the cap); at
# the 30s minimum interval, 24h = 2880 → cap kicks in and trims to the
# most recent 2000 (~16h trend, still useful).
CF_PULL_HISTORY_WINDOW_S = 24 * 3600
CF_PULL_HISTORY_MAX_ENTRIES = 2000

# Task #947 — Mongo-backed lease so only ONE replica runs the CF pull
# loop at any moment, even when Railway scales the backend out to N
# copies. The previous file-lock approach (`_is_leader` in server.py)
# is per-machine, so two Railway replicas would each consider
# themselves "the leader" and double-poll the CF GraphQL API.
#
# The lease is refreshed on every loop tick; if the owning replica
# dies (or is scaled down between ticks) any other replica can take
# over after ``CF_PULL_LEASE_TTL_S`` of silence on the doc. TTL is
# 3× the pull interval so a single missed tick (transient network
# blip) doesn't trigger a needless leader fail-over.
#
# Task #950 — the actual lease state machine now lives in
# ``background_lease`` so it can be reused by every leader-gated
# background loop (LLM cache pre-warm, CF bot report, CF Pages
# nightly deploy, internal-linker maintenance, …). The two wrappers
# below keep the original names/signatures so the existing call sites
# (and unit tests) stay unchanged.
CF_PULL_LEASE_TTL_S = max(180, int(CF_PULL_INTERVAL_S) * 3)
import background_lease as _bglease
CF_PULL_LEASE_OWNER_FIELD = _bglease.LEASE_OWNER_FIELD
CF_PULL_LEASE_EXPIRES_FIELD = _bglease.LEASE_EXPIRES_FIELD
# Stable per-process id so renewals from the same replica don't trip
# the takeover branch of the CAS.
_CF_PULL_LEASE_OWNER_ID = _bglease.make_owner_id("cf-pull")


# ─────────────────────────────────────────────────────────────────────────────
# Token storage — env first, then api_config (admin can rotate at runtime)
# ─────────────────────────────────────────────────────────────────────────────


async def _resolve_ingest_token() -> Optional[str]:
    """Effective ingest token: api_config override beats env."""
    if db is not None:
        try:
            doc = await db.api_config.find_one({"_id": LOG_INGEST_API_CONFIG_KEY})
            if doc and isinstance(doc.get("token"), str) and doc["token"].strip():
                return doc["token"].strip()
        except Exception:
            pass
    raw = (os.environ.get("LOG_INGEST_TOKEN") or "").strip()
    return raw or None


async def _persist_ingest_token(token: str) -> None:
    if db is None:
        return
    await db.api_config.update_one(
        {"_id": LOG_INGEST_API_CONFIG_KEY},
        {"$set": {
            "token": token,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }},
        upsert=True,
    )


async def _persist_pause_state(paused: bool) -> None:
    if db is None:
        return
    await db.api_config.update_one(
        {"_id": LOG_PAUSE_API_CONFIG_KEY},
        {"$set": {
            "paused": bool(paused),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }},
        upsert=True,
    )


async def _hydrate_pause_state_from_db() -> None:
    """Boot-time pause hydration so a runtime toggle survives restarts."""
    if db is None:
        return
    try:
        doc = await db.api_config.find_one({"_id": LOG_PAUSE_API_CONFIG_KEY})
    except Exception:
        return
    if doc and "paused" in doc:
        _dao.set_runtime_pause(bool(doc["paused"]))


# ─────────────────────────────────────────────────────────────────────────────
# Ingest
# ─────────────────────────────────────────────────────────────────────────────


@router.post("/api/logs/ingest")
async def ingest_logs(request: Request) -> JSONResponse:
    """Token-authenticated bulk ingest endpoint for the edge worker.

    Body shape::
        {
            "source": "edge",   # optional; defaults applied per-record
            "logs":   [ {...}, ... ]
        }

    Returns ``{accepted, dropped, paused}``. When the kill switch is
    on, every record is counted as ``dropped`` and ``paused=True`` so
    the worker stops retrying instantly.

    Auth: ``X-Logs-Ingest-Token`` header must equal the resolved token.
    The endpoint is intentionally NOT behind admin JWT — the worker
    cannot mint admin tokens. Defense in depth: the worker also passes
    ``X-Origin-Auth`` (the Cloud Run origin secret), which must already
    succeed before this handler runs.
    """
    token_provided = (request.headers.get(LOG_INGEST_TOKEN_HEADER) or "").strip()
    expected = await _resolve_ingest_token()
    if not expected:
        # No token configured → ingest is closed (fail-secure).
        raise HTTPException(status_code=503, detail="Ingest token not configured")
    if not token_provided or not secrets.compare_digest(token_provided, expected):
        raise HTTPException(status_code=401, detail="Invalid ingest token")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Body must be an object")
    raw_logs = payload.get("logs")
    if not isinstance(raw_logs, list):
        raise HTTPException(status_code=400, detail="`logs` must be an array")
    if len(raw_logs) > _dao.MAX_INGEST_BATCH:
        raise HTTPException(
            status_code=413,
            detail=f"Batch too large (max {_dao.MAX_INGEST_BATCH})",
        )
    default_source = (payload.get("source") or "edge").strip() or "edge"
    if default_source not in _dao.ALLOWED_SOURCES:
        default_source = "edge"

    if _dao._logs_paused_env():
        return JSONResponse(
            status_code=202,
            content={"accepted": 0, "dropped": len(raw_logs), "paused": True},
        )

    result = await _dao.insert_logs(db, raw_logs, default_source=default_source)
    return JSONResponse(
        status_code=202,
        content={"accepted": result["accepted"], "dropped": result["dropped"],
                 "paused": False},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Admin filter / list
# ─────────────────────────────────────────────────────────────────────────────


def _parse_csv_list(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [s.strip().lower() for s in value.split(",") if s.strip()]


@router.get("/api/admin/logs")
async def admin_list_logs(
    sources: Optional[str] = Query(None, description="comma-sep source filter"),
    levels: Optional[str] = Query(None, description="comma-sep level filter"),
    status_min: Optional[int] = Query(None, ge=0, le=999),
    status_max: Optional[int] = Query(None, ge=0, le=999),
    route_prefix: Optional[str] = Query(None),
    correlation_id: Optional[str] = Query(None),
    q: Optional[str] = Query(None, max_length=200),
    since: Optional[str] = Query(None),
    until: Optional[str] = Query(None),
    before: Optional[str] = Query(None, description="cursor: timestamp from prev page"),
    limit: int = Query(_dao.DEFAULT_QUERY_LIMIT, ge=1, le=_dao.MAX_QUERY_LIMIT),
    admin: dict = Depends(get_admin_user),
):
    filters = {
        "sources": _parse_csv_list(sources),
        "levels": _parse_csv_list(levels),
        "status_min": status_min,
        "status_max": status_max,
        "route_prefix": route_prefix,
        "correlation_id": correlation_id,
        "q": q,
        "since": since,
        "until": until,
    }
    rows = await _dao.query_logs(db, filters=filters, limit=limit, before=before)
    total = await _dao.count_logs(db, filters)
    next_before = rows[-1]["timestamp"] if rows and len(rows) >= limit else None
    return {
        "logs": rows,
        "total": total,
        "total_capped": total >= _dao.MAX_COUNT,
        "next_before": next_before,
        "limit": limit,
    }


@router.get("/api/admin/logs/trace/{correlation_id}")
async def admin_trace_logs(correlation_id: str, admin: dict = Depends(get_admin_user)):
    rows = await _dao.fetch_trace(db, correlation_id)
    return {"correlation_id": correlation_id, "logs": rows, "total": len(rows)}


@router.get("/api/admin/logs/export")
async def admin_export_logs(
    fmt: str = Query("ndjson", pattern="^(csv|ndjson)$"),
    sources: Optional[str] = Query(None),
    levels: Optional[str] = Query(None),
    status_min: Optional[int] = Query(None, ge=0, le=999),
    status_max: Optional[int] = Query(None, ge=0, le=999),
    route_prefix: Optional[str] = Query(None),
    correlation_id: Optional[str] = Query(None),
    q: Optional[str] = Query(None, max_length=200),
    since: Optional[str] = Query(None),
    until: Optional[str] = Query(None),
    limit: int = Query(5000, ge=1, le=50_000),
    admin: dict = Depends(get_admin_user),
):
    filters = {
        "sources": _parse_csv_list(sources),
        "levels": _parse_csv_list(levels),
        "status_min": status_min,
        "status_max": status_max,
        "route_prefix": route_prefix,
        "correlation_id": correlation_id,
        "q": q,
        "since": since,
        "until": until,
    }
    if fmt == "csv":
        return StreamingResponse(
            _stream_csv(filters, limit),
            media_type="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename=unified_logs_{int(time.time())}.csv",
                "Cache-Control": "no-store",
            },
        )
    return StreamingResponse(
        _stream_ndjson(filters, limit),
        media_type="application/x-ndjson",
        headers={
            "Content-Disposition": f"attachment; filename=unified_logs_{int(time.time())}.ndjson",
            "Cache-Control": "no-store",
        },
    )


_CSV_FIELDS = (
    "timestamp", "source", "level", "status", "duration_ms", "method",
    "route", "country", "colo", "cache", "ray_id", "correlation_id",
    "user_agent", "message",
)


async def _stream_csv(filters: Dict[str, Any], limit: int):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=_CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    yield buf.getvalue().encode("utf-8")
    buf.seek(0); buf.truncate(0)
    async for doc in _dao.iter_export(db, filters, limit=limit):
        writer.writerow({k: doc.get(k) for k in _CSV_FIELDS})
        yield buf.getvalue().encode("utf-8")
        buf.seek(0); buf.truncate(0)


async def _stream_ndjson(filters: Dict[str, Any], limit: int):
    async for doc in _dao.iter_export(db, filters, limit=limit):
        # Drop the datetime-only ``expire_at`` (already excluded by
        # iter_export's projection) and serialize defensively.
        yield (json.dumps(doc, default=str) + "\n").encode("utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# Status / pause / rotate-token / clear
# ─────────────────────────────────────────────────────────────────────────────


def _compute_cf_pull_24h_aggregate(
    history: List[Dict[str, Any]],
    now: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """Task #953 — collapse the rolling per-tick history into a single
    24h aggregate the admin dashboard can render as a "CF pull cost"
    widget. Returns None when there is no usable history (so the UI
    can hide the widget on a fresh deploy).

    The aggregate exposes:
      * ``ticks``                — number of pulls in the window
      * ``total_calls``          — sum of GraphQL calls across all ticks
      * ``total_subdivisions``   — sum of times we halved a window to
                                   stay under CF_PULL_LIMIT
      * ``total_saturated``      — sum of minute-buckets that hit the
                                   limit even at the floor (i.e. lost
                                   data)
      * ``max_calls``            — worst-tick GraphQL fan-out (for the
                                   "we suddenly went from 1→50" drift)
      * ``max_subdivisions``     — worst-tick subdivision depth
      * ``subdivided_ticks``     — count of ticks that paginated at all
      * ``subdivided_pct``       — % of ticks that paginated (0..100)
      * ``window_s``             — actual span covered (oldest→newest),
                                   so the UI can label "X% of 24h"
                                   when the deploy is younger than 24h
      * ``oldest_ts`` / ``newest_ts`` — for tooltips
    """
    cutoff_now = now or datetime.now(timezone.utc)
    cutoff = cutoff_now - timedelta(seconds=CF_PULL_HISTORY_WINDOW_S)
    in_window: List[Dict[str, Any]] = []
    for entry in history or []:
        if not isinstance(entry, dict):
            continue
        ts_raw = entry.get("ts")
        try:
            ts_dt = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        except Exception:
            continue
        if ts_dt < cutoff:
            continue
        in_window.append({
            "ts_dt": ts_dt,
            "calls": int(entry.get("calls") or 0),
            "subdivisions": int(entry.get("subdivisions") or 0),
            "saturated": int(entry.get("saturated") or 0),
        })
    if not in_window:
        return None
    total_calls = sum(e["calls"] for e in in_window)
    total_subdivisions = sum(e["subdivisions"] for e in in_window)
    total_saturated = sum(e["saturated"] for e in in_window)
    max_calls = max(e["calls"] for e in in_window)
    max_subdivisions = max(e["subdivisions"] for e in in_window)
    subdivided_ticks = sum(1 for e in in_window if e["subdivisions"] > 0)
    ticks = len(in_window)
    oldest = min(e["ts_dt"] for e in in_window)
    newest = max(e["ts_dt"] for e in in_window)
    return {
        "ticks": ticks,
        "total_calls": total_calls,
        "total_subdivisions": total_subdivisions,
        "total_saturated": total_saturated,
        "max_calls": max_calls,
        "max_subdivisions": max_subdivisions,
        "subdivided_ticks": subdivided_ticks,
        "subdivided_pct": round(100.0 * subdivided_ticks / ticks, 1),
        "window_s": int((newest - oldest).total_seconds()),
        "oldest_ts": oldest.isoformat(),
        "newest_ts": newest.isoformat(),
    }


# Cap on the per-tick datapoints returned to the admin sparkline. At the
# default 60s pull interval this is the last ~2h of pulls — enough to
# spot a sudden 1→50 calls/tick drift without bloating the /status JSON
# (each entry is ~60 bytes, so 120 entries ≈ 7KB).
CF_PULL_HISTORY_RECENT_MAX_POINTS = 120


def _extract_cf_pull_history_recent(
    history: List[Dict[str, Any]],
    now: Optional[datetime] = None,
    max_points: int = CF_PULL_HISTORY_RECENT_MAX_POINTS,
) -> Optional[List[Dict[str, Any]]]:
    """Task #960 — return the most recent per-tick datapoints from the
    rolling ``cf_pull_history``, suitable for an inline sparkline in the
    admin Logs Explorer's "CF pull cost" widget.

    Filters out entries older than the 24h aggregation window (matching
    ``_compute_cf_pull_24h_aggregate``), sorts oldest→newest so the
    sparkline plots left-to-right in time, and returns at most
    ``max_points`` entries (most recent kept) so the JSON payload stays
    small even if the cursor history is full. Returns ``None`` when no
    usable entries exist so the UI hides the sparkline on a fresh
    deploy instead of rendering an empty chart.
    """
    def _safe_nonneg_int(v: Any) -> int:
        # Telemetry rows are written by the CF pull loop in this same
        # process, so the values SHOULD already be ints — but a stray
        # string / float / None from a forwards-compat schema bump
        # must not crash the dashboard.
        try:
            n = int(v or 0)
        except (TypeError, ValueError):
            return 0
        return n if n > 0 else 0

    cutoff_now = now or datetime.now(timezone.utc)
    cutoff = cutoff_now - timedelta(seconds=CF_PULL_HISTORY_WINDOW_S)
    items: List[Dict[str, Any]] = []
    for entry in history or []:
        if not isinstance(entry, dict):
            continue
        ts_raw = entry.get("ts")
        try:
            ts_dt = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        except Exception:
            continue
        if ts_dt < cutoff:
            continue
        items.append({
            "ts_dt": ts_dt,
            "calls": _safe_nonneg_int(entry.get("calls")),
            "subdivisions": _safe_nonneg_int(entry.get("subdivisions")),
            "saturated": _safe_nonneg_int(entry.get("saturated")),
        })
    if not items:
        return None
    items.sort(key=lambda e: e["ts_dt"])
    if max_points > 0 and len(items) > max_points:
        items = items[-max_points:]
    return [
        {
            "ts": e["ts_dt"].isoformat(),
            "calls": e["calls"],
            "subdivisions": e["subdivisions"],
            "saturated": e["saturated"],
        }
        for e in items
    ]


@router.get("/api/admin/logs/status")
async def admin_logs_status(admin: dict = Depends(get_admin_user)):
    shipper = _dao.get_backend_shipper()
    paused = _dao._logs_paused_env()
    last_cf_pull: Optional[str] = None
    cf_cursor: Optional[str] = None
    # Task #947 — expose lease state so an operator can see WHICH
    # replica is currently driving the CF pull (and how stale the
    # lease is). Without this, "did the leader die / did fail-over
    # happen?" is invisible from the dashboard in a multi-replica
    # Railway deploy.
    cf_lease_owner: Optional[str] = None
    cf_lease_expires_at: Optional[str] = None
    cf_lease_age_s: Optional[int] = None
    cf_lease_is_self: Optional[bool] = None
    # Task #948 — pagination telemetry from the most recent pull.
    cf_last_calls: Optional[int] = None
    cf_last_subdivisions: Optional[int] = None
    cf_last_saturated_windows: Optional[List[Any]] = None
    # Task #953 — rolling 24h pagination-cost aggregate.
    cf_pull_24h: Optional[Dict[str, Any]] = None
    # Task #960 — per-tick datapoints (most recent ~120) so the admin
    # widget can render an inline sparkline of GraphQL fan-out and
    # spot a sudden 1→50 calls/tick drift visually.
    cf_pull_history_recent: Optional[List[Dict[str, Any]]] = None
    # Task #952 — rolling 24h count of distinct saturated minutes,
    # backed by the ``cf_pull_saturated_minutes`` collection (TTL 25h).
    # Surfaced here so the AdminLogsExplorer can show a trend at a
    # glance instead of forcing the operator to dig through Mongo.
    cf_saturated_minutes_24h: int = 0
    if db is not None:
        try:
            lock = await db.job_locks.find_one({"_id": CF_PULL_LOCK_ID})
            if lock:
                last_cf_pull = lock.get("updated_at")
                cf_cursor = lock.get(CF_PULL_CURSOR_FIELD)
                cf_lease_owner = lock.get(CF_PULL_LEASE_OWNER_FIELD)
                expires = lock.get(CF_PULL_LEASE_EXPIRES_FIELD)
                if isinstance(expires, datetime):
                    cf_lease_expires_at = expires.isoformat()
                    cf_lease_age_s = max(
                        0,
                        int((datetime.now(timezone.utc) - expires).total_seconds())
                        + CF_PULL_LEASE_TTL_S,
                    )
                cf_lease_is_self = (cf_lease_owner == _CF_PULL_LEASE_OWNER_ID)
                cf_last_calls = lock.get("last_calls")
                cf_last_subdivisions = lock.get("last_subdivisions")
                cf_last_saturated_windows = lock.get("last_saturated_windows")
                cf_pull_24h = _compute_cf_pull_24h_aggregate(
                    lock.get("cf_pull_history") or []
                )
                cf_pull_history_recent = _extract_cf_pull_history_recent(
                    lock.get("cf_pull_history") or []
                )
        except Exception:
            pass
        try:
            from routes.admin_logs_cf_pull_saturation_alerts import (
                count_saturated_minutes_24h,
            )
            cf_saturated_minutes_24h = await count_saturated_minutes_24h(db)
        except Exception:
            cf_saturated_minutes_24h = 0
    counts: Dict[str, Any] = {}
    if db is not None:
        try:
            for src in _dao.ALLOWED_SOURCES:
                counts[src] = await db[_dao.UNIFIED_LOGS_COLLECTION].count_documents(
                    {"source": src}, limit=_dao.MAX_COUNT,
                )
        except Exception:
            pass
    expected_token = await _resolve_ingest_token()
    return {
        "paused": paused,
        "ttl_days": _dao._ttl_days(),
        "max_ingest_batch": _dao.MAX_INGEST_BATCH,
        "edge_sample_rate_env": "EDGE_LOG_SAMPLE_RATE",
        "backend_sample_rate": shipper.sample_rate,
        "ingest_token_configured": bool(expected_token),
        "cf_pull_interval_s": CF_PULL_INTERVAL_S,
        "cf_pull_last_run": last_cf_pull,
        "cf_pull_cursor": cf_cursor,
        # Task #947 — lease telemetry. ``cf_pull_lease_is_self`` lets the
        # admin UI tell "this replica is currently driving the pull"
        # apart from "another replica owns it" without needing to know
        # the per-process owner id format.
        "cf_pull_lease_owner": cf_lease_owner,
        "cf_pull_lease_expires_at": cf_lease_expires_at,
        "cf_pull_lease_age_s": cf_lease_age_s,
        "cf_pull_lease_ttl_s": CF_PULL_LEASE_TTL_S,
        "cf_pull_lease_is_self": cf_lease_is_self,
        # Task #948 — pagination telemetry. ``last_calls`` shows how
        # many GraphQL calls the most recent tick made (1 means the
        # window fit; >1 means we subdivided to avoid dropping
        # buckets). ``last_saturated_windows`` is the list of minute
        # buckets whose row count hit the limit even at the floor —
        # if it's consistently non-empty the dimension cut needs to
        # be widened.
        "cf_pull_limit": CF_PULL_LIMIT,
        "cf_pull_max_subdivisions": CF_PULL_MAX_SUBDIVISIONS,
        "cf_pull_last_calls": cf_last_calls,
        "cf_pull_last_subdivisions": cf_last_subdivisions,
        "cf_pull_last_saturated_windows": cf_last_saturated_windows,
        # Task #953 — rolling 24h pagination-cost trend (None on a
        # fresh deploy with no completed pulls yet; the UI hides the
        # widget in that case).
        "cf_pull_24h": cf_pull_24h,
        # Task #960 — per-tick datapoints for the inline sparkline
        # rendered next to the totals. Same nullability rule as
        # ``cf_pull_24h``: None on a fresh deploy so the chart hides
        # instead of rendering an empty plot.
        "cf_pull_history_recent": cf_pull_history_recent,
        # Task #952 — rolling 24h count of saturated minutes (TTL'd
        # to 25h in ``cf_pull_saturated_minutes``). Lets the operator
        # see at a glance whether saturation is a one-off spike or a
        # structural problem that needs the dimension cut widened.
        "cf_pull_saturated_minutes_24h": cf_saturated_minutes_24h,
        "shipper_stats": {
            "accepted": shipper.accepted,
            "flushed": shipper.flushed,
            "dropped_full": shipper.dropped_full,
            "dropped_paused": shipper.dropped_paused,
        },
        "counts": counts,
    }


@router.get("/api/admin/logs/sources/stats")
async def admin_logs_sources_stats(admin: dict = Depends(get_admin_user)):
    """Per-source counters (lighter than ``/status`` — used by the live
    tail header to show how many records each producer has ingested
    without re-fetching the full status payload)."""
    counts: Dict[str, Any] = {}
    last_seen: Dict[str, Optional[str]] = {}
    if db is not None:
        for src in _dao.ALLOWED_SOURCES:
            try:
                counts[src] = await db[_dao.UNIFIED_LOGS_COLLECTION].count_documents(
                    {"source": src}, limit=_dao.MAX_COUNT,
                )
            except Exception:
                counts[src] = 0
            try:
                doc = await db[_dao.UNIFIED_LOGS_COLLECTION].find_one(
                    {"source": src}, sort=[("timestamp", -1)],
                    projection={"timestamp": 1, "_id": 0},
                )
                last_seen[src] = (doc or {}).get("timestamp") if doc else None
            except Exception:
                last_seen[src] = None
    return {"counts": counts, "last_seen": last_seen}


@router.post("/api/admin/logs/cf/pull")
async def admin_logs_cf_pull(admin: dict = Depends(get_admin_user)):
    """Manual trigger of one Cloudflare GraphQL pull tick. Useful when
    debugging or after rotating the CF token — the admin doesn't have
    to wait for the next scheduled iteration of the background loop."""
    res = await _try_run_cf_pull_once()
    await supa_insert_activity_log({
        "id": str(uuid.uuid4()),
        "action": "unified_logs_cf_pull_manual",
        "details": (
            f"Manual CF pull: ok={res.get('ok')} accepted={res.get('accepted', 0)} "
            f"dropped={res.get('dropped', 0)} reason={res.get('reason') or '-'}"
        ),
        "level": "info",
        "admin_name": admin.get("name") or admin.get("username") or "Admin",
        "admin_email": admin.get("email", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return res


@router.post("/api/admin/logs/pause")
async def admin_logs_pause(admin: dict = Depends(get_admin_user)):
    _dao.set_runtime_pause(True)
    await _persist_pause_state(True)
    await supa_insert_activity_log({
        "id": str(uuid.uuid4()),
        "action": "unified_logs_paused",
        "details": "Unified logs ingest paused via admin panel",
        "level": "warning",
        "admin_name": admin.get("name") or admin.get("username") or "Admin",
        "admin_email": admin.get("email", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"paused": True}


@router.post("/api/admin/logs/resume")
async def admin_logs_resume(admin: dict = Depends(get_admin_user)):
    _dao.set_runtime_pause(False)
    await _persist_pause_state(False)
    await supa_insert_activity_log({
        "id": str(uuid.uuid4()),
        "action": "unified_logs_resumed",
        "details": "Unified logs ingest resumed via admin panel",
        "level": "info",
        "admin_name": admin.get("name") or admin.get("username") or "Admin",
        "admin_email": admin.get("email", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"paused": False}


@router.post("/api/admin/logs/rotate-token")
async def admin_logs_rotate_token(admin: dict = Depends(get_admin_user)):
    """Generate + persist a new ingest token. The plaintext is returned
    ONCE — there is no read-back endpoint, so the admin must copy it
    to the worker secret store right away.

    The activity-log breadcrumb records WHO rotated the token, not the
    token itself, so the audit trail does not leak the secret.
    """
    new_token = secrets.token_urlsafe(48)
    await _persist_ingest_token(new_token)
    await supa_insert_activity_log({
        "id": str(uuid.uuid4()),
        "action": "unified_logs_token_rotated",
        "details": "Rotated unified-logs ingest token; copy to edge worker now",
        "level": "warning",
        "admin_name": admin.get("name") or admin.get("username") or "Admin",
        "admin_email": admin.get("email", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"token": new_token, "rotated_at": datetime.now(timezone.utc).isoformat()}


@router.delete("/api/admin/logs")
async def admin_clear_logs(
    sources: Optional[str] = Query(None),
    admin: dict = Depends(get_admin_user),
):
    """Destructive purge. Optionally scope by ``sources`` so an admin
    can clear (e.g.) only ``edge`` while keeping ``backend`` history.
    Drops a self-audit breadcrumb in activity_log.

    SAFETY: if the caller supplied ``sources=`` but every value was
    rejected (typo, unknown name), we 400 instead of silently
    broadening to a full purge. A full purge requires explicitly
    omitting the parameter altogether — never ``sources=garbage``.
    """
    parsed_sources = _parse_csv_list(sources)
    # Distinguish "user passed nothing → full purge is intentional"
    # from "user passed a value that we couldn't honour → accidental
    # full purge". The raw query string presence is the source of truth,
    # NOT whether the parsed list is empty. Crucially, ``?sources=`` or
    # ``?sources=   `` (present-but-empty / whitespace-only) is also
    # treated as invalid — operators must omit the parameter entirely
    # to do a full purge, never pass an empty string.
    if sources is not None:
        if not parsed_sources:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Refusing destructive purge: 'sources' was supplied but "
                    "empty (whitespace/comma only). To purge ALL sources, "
                    "OMIT the 'sources' query param entirely; do not pass "
                    "an empty string."
                ),
            )
        valid_sources = [s for s in parsed_sources if s in _dao.ALLOWED_SOURCES]
        invalid_sources = [s for s in parsed_sources if s not in _dao.ALLOWED_SOURCES]
        if not valid_sources:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Refusing destructive purge: 'sources' was supplied but "
                    f"none of {invalid_sources or [sources]} are recognised. "
                    f"Allowed values: {sorted(_dao.ALLOWED_SOURCES)}. "
                    f"To purge ALL sources, omit the 'sources' query param entirely."
                ),
            )
        # Drop unknown values silently from this point on so the DAO
        # only ever sees recognised source names.
        parsed_sources = valid_sources
    filters = {"sources": parsed_sources}
    deleted = await _dao.clear_logs(db, filters=filters)
    await supa_insert_activity_log({
        "id": str(uuid.uuid4()),
        "action": "unified_logs_cleared",
        "details": (
            f"Cleared {deleted} unified-log entr{'y' if deleted == 1 else 'ies'}"
            + (f" (sources={sources})" if sources else "")
        ),
        "level": "danger",
        "admin_name": admin.get("name") or admin.get("username") or "Admin",
        "admin_email": admin.get("email", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    # Echo back the *validated* sources list (parsed_sources is the
    # post-guard, post-filter list — never the raw user input). This
    # avoids operator confusion when a mixed valid/invalid input was
    # supplied: the response shows precisely what we honoured.
    return {"deleted": deleted, "sources": parsed_sources}


# ─────────────────────────────────────────────────────────────────────────────
# Cloudflare GraphQL pull loop
# ─────────────────────────────────────────────────────────────────────────────


def normalize_cf_http_request_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Translate a single ``httpRequestsAdaptiveGroups`` row into the
    unified record shape (without inserting it).

    Pulled out into a pure helper so unit tests can lock down the
    field mapping without spinning up Mongo.
    """
    dim = row.get("dimensions") or {}
    avg = (row.get("avg") or {})
    # ``httpRequestsAdaptiveGroups`` exposes the per-bucket request count
    # at the **top level** as ``count`` (not under ``sum.requests`` — that
    # field does not exist in the adaptive sampling schema and yields a
    # GraphQL validation error). We tolerate either shape so older callers
    # don't break, but the live CF response uses ``count``.
    request_count = row.get("count")
    if request_count is None:
        request_count = (row.get("sum") or {}).get("requests")
    status = dim.get("edgeResponseStatus") or dim.get("originResponseStatus")
    cache_status = (dim.get("cacheStatus") or "").lower() or None
    cache: Optional[str]
    if cache_status in ("hit", "miss", "bypass", "expired", "stale"):
        cache = cache_status
    elif cache_status in ("dynamic", "none"):
        cache = "dynamic"
    else:
        cache = None
    minute_iso = (dim.get("datetime") or dim.get("datetimeMinute")
                  or dim.get("date") or "")
    method = (dim.get("clientRequestHTTPMethodName")
              or dim.get("clientRequestHTTPMethod") or "")
    path = dim.get("clientRequestPath") or dim.get("requestPath") or ""
    colo = dim.get("coloCode") or dim.get("colo") or ""
    # The idempotency key MUST include every dimension the GraphQL
    # ``httpRequestsAdaptiveGroups`` query groups by — otherwise two
    # legitimately-distinct buckets (e.g. same minute+path+status from
    # two different countries or hosts) would collide on the same _id
    # and the second one would be silently dropped as an E11000
    # duplicate. The dimensions list MUST stay in lockstep with the
    # ``dimensions`` block in ``_CF_QUERY``.
    host = dim.get("clientRequestHTTPHost") or dim.get("host") or ""
    country = dim.get("clientCountryName") or dim.get("countryName") or ""
    edge_status = dim.get("edgeResponseStatus")
    origin_status = dim.get("originResponseStatus")
    cache_status_raw = dim.get("cacheStatus") or ""
    idem = (f"cf|{minute_iso}|{method}|{path}|{host}|{country}|{colo}"
            f"|{edge_status}|{origin_status}|{cache_status_raw}")
    rec_id = "cf_" + hashlib.sha1(idem.encode("utf-8"), usedforsecurity=False).hexdigest()
    return {
        "_id": rec_id,
        "source": "cloudflare",
        "level": _level_for_status_int(status),
        "timestamp": dim.get("datetime") or dim.get("datetimeMinute") or dim.get("date"),
        "message": _cf_message(dim, status, request_count or 1),
        "status": status,
        "duration_ms": _coerce_origin_duration(avg.get("originResponseDurationMs")
                                               or dim.get("originResponseDurationMs")),
        "method": dim.get("clientRequestHTTPMethodName") or dim.get("clientRequestHTTPMethod"),
        "route": dim.get("clientRequestPath") or dim.get("requestPath"),
        "country": dim.get("clientCountryName") or dim.get("countryName"),
        "colo": dim.get("coloCode") or dim.get("colo"),
        "cache": cache,
        # NOTE: ``httpRequestsAdaptiveGroups`` is an *aggregated* dataset
        # so per-request ray identifiers are not exposed by the schema
        # (the field ``rayName`` only exists on the per-event Logpush
        # dataset, which requires Enterprise). Leave the cid empty for
        # cloudflare rows — correlation comes from the worker shipper
        # which DOES have access to ``cf-ray`` per request.
        "ray_id": None,
        "correlation_id": None,
        "extra": {
            "host": dim.get("clientRequestHTTPHost") or dim.get("host"),
            "edge_status": dim.get("edgeResponseStatus"),
            "origin_status": dim.get("originResponseStatus"),
            "request_count": request_count,
        },
    }


def _level_for_status_int(status: Any) -> str:
    try:
        s = int(status)
    except (TypeError, ValueError):
        return "info"
    if s >= 500:
        return "error"
    if s >= 400:
        return "warn"
    return "info"


def _coerce_origin_duration(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _cf_message(dim: Dict[str, Any], status: Any, count: Any) -> str:
    method = dim.get("clientRequestHTTPMethodName") or "GET"
    path = dim.get("clientRequestPath") or "/"
    suffix = f" → {status}" if status else ""
    if count and int(count) > 1:
        suffix += f" ×{count}"
    return f"{method} {path}{suffix}"


_CF_QUERY = """
query UnifiedLogsPull($zone: String!, $since: Time!, $until: Time!, $limit: Int!) {
  viewer {
    zones(filter: {zoneTag: $zone}) {
      httpRequestsAdaptiveGroups(
        limit: $limit,
        filter: {datetime_geq: $since, datetime_lt: $until},
        orderBy: [datetimeMinute_DESC]
      ) {
        dimensions {
          datetimeMinute
          edgeResponseStatus
          originResponseStatus
          cacheStatus
          clientRequestPath
          clientRequestHTTPMethodName
          clientRequestHTTPHost
          clientCountryName
          coloCode
        }
        avg { originResponseDurationMs }
        count
      }
    }
  }
}
""".strip()


async def _fetch_cf_window(graphql_callable, since: datetime,
                           until: datetime) -> List[Dict[str, Any]]:
    """Single GraphQL call for one [since, until) window.

    Returns the raw ``httpRequestsAdaptiveGroups`` rows (un-normalized)
    so the caller can detect saturation by row count *before* the row
    shape is collapsed into the unified-log schema. Raises whatever
    the underlying GraphQL client raises so the caller can decide
    between hard-fail and partial-success behaviour.
    """
    from config import CF_ZONE_ID
    resp = await graphql_callable(_CF_QUERY, {
        "zone": CF_ZONE_ID,
        "since": since.isoformat().replace("+00:00", "Z"),
        "until": until.isoformat().replace("+00:00", "Z"),
        "limit": CF_PULL_LIMIT,
    })
    out: List[Dict[str, Any]] = []
    zones = (((resp or {}).get("data") or {}).get("viewer") or {}).get("zones") or []
    for z in zones:
        for grp in (z.get("httpRequestsAdaptiveGroups") or []):
            out.append(grp)
    return out


async def _pull_cf_window_paginated(
    graphql_callable, since: datetime, until: datetime,
    depth: int = 0,
) -> Dict[str, Any]:
    """Recursively fetch [since, until) by halving the window on
    minute boundaries whenever the limit is hit.

    The CF GraphQL ``httpRequestsAdaptiveGroups`` query has no native
    cursor/offset support so we cannot page in the usual REST sense.
    Instead, when we get back exactly ``CF_PULL_LIMIT`` rows — the
    signal that the response was likely truncated — we split the
    window in half on a minute boundary (the dataset's aggregation
    granularity) and pull each half independently. Recursion bottoms
    out at ``CF_PULL_MIN_WINDOW_S`` (60s, one CF minute bucket); if
    even a single-minute window is saturated we surface that to the
    caller via ``saturated_windows`` so the operator can see the gap.

    Returns ``{rows, calls, subdivisions, saturated_windows}``:
        * ``rows`` — raw, deduped (by deterministic id) groups
        * ``calls`` — total GraphQL calls made for this window
        * ``subdivisions`` — number of split events that fired
        * ``saturated_windows`` — list of (since_iso, until_iso) tuples
          where the limit was hit at the minimum window size

    The two halves use disjoint ranges (`datetime_geq` / `datetime_lt`
    on minute boundaries), so de-dup is defensive only — but bounding
    by deterministic id makes the result independent of split order.
    """
    rows = await _fetch_cf_window(graphql_callable, since, until)
    span_s = (until - since).total_seconds()

    # Strict less-than would miss the (uncommon but real) case where
    # the response is exactly CF_PULL_LIMIT distinct buckets with no
    # truncation; subdividing in that edge case costs one extra call
    # which then returns < limit and the recursion stops cleanly.
    if len(rows) < CF_PULL_LIMIT:
        return {
            "rows": rows, "calls": 1, "subdivisions": 0,
            "saturated_windows": [],
        }

    # Hit the cap. Try to subdivide unless we'd cross the floor or
    # blow past the recursion guard.
    if span_s <= CF_PULL_MIN_WINDOW_S or depth >= CF_PULL_MAX_SUBDIVISIONS:
        logger.warning(
            "[unified_logs] CF pull window saturated at minimum granularity: "
            "since=%s until=%s rows=%d limit=%d depth=%d — bucket loss "
            "possible for this minute",
            since.isoformat(), until.isoformat(), len(rows), CF_PULL_LIMIT,
            depth,
        )
        return {
            "rows": rows, "calls": 1, "subdivisions": 0,
            "saturated_windows": [(since.isoformat(), until.isoformat())],
        }

    # Snap the midpoint to the nearest whole minute so each half is
    # composed of clean CF buckets — the GraphQL filter is
    # datetime_geq/datetime_lt, so a minute aligned with `mid` falls
    # into the right half exactly once.
    mid = since + (until - since) / 2
    mid = mid.replace(second=0, microsecond=0)
    if mid <= since or mid >= until:
        # Snapping collapsed the span (e.g. <2 minute window where
        # both ends share a minute boundary). Cannot subdivide
        # without crossing the floor — accept saturation.
        logger.warning(
            "[unified_logs] CF pull window cannot be subdivided "
            "(snapping collapsed midpoint): since=%s until=%s rows=%d",
            since.isoformat(), until.isoformat(), len(rows),
        )
        return {
            "rows": rows, "calls": 1, "subdivisions": 0,
            "saturated_windows": [(since.isoformat(), until.isoformat())],
        }

    left = await _pull_cf_window_paginated(
        graphql_callable, since, mid, depth + 1)
    right = await _pull_cf_window_paginated(
        graphql_callable, mid, until, depth + 1)

    # Defensive de-dup: even though the halves are disjoint by
    # construction, the row payload includes the per-minute bucket id
    # so collapsing on it makes us robust to any future change in how
    # CF interprets the boundary.
    merged: Dict[str, Dict[str, Any]] = {}
    for grp in (*left["rows"], *right["rows"]):
        try:
            key = normalize_cf_http_request_row(grp)["_id"]
        except Exception:
            key = id(grp)  # fall back to object identity rather than crash
        merged.setdefault(key, grp)

    return {
        "rows": list(merged.values()),
        "calls": 1 + left["calls"] + right["calls"],
        "subdivisions": 1 + left["subdivisions"] + right["subdivisions"],
        "saturated_windows": (
            left["saturated_windows"] + right["saturated_windows"]
        ),
    }


async def _try_run_cf_pull_once(now_utc: Optional[datetime] = None,
                                graphql_callable=None,
                                lease_owner: Optional[str] = None) -> Dict[str, Any]:
    """One iteration of the Cloudflare pull. Factored out so tests can
    inject a fake ``_graphql_query`` and assert on the return shape.

    Returns ``{ok, accepted, dropped, since, until, calls,
    subdivisions, saturated_windows, reason?}``. The pagination
    telemetry (``calls``/``subdivisions``/``saturated_windows``) is
    persisted to the cursor doc so the admin status endpoint can
    surface "this hour was so busy we ran out of granularity" without
    digging through logs.

    ``lease_owner`` (Task #947 fencing): when supplied, the cursor
    write at the end is conditioned on the lease still being held by
    the same owner. This protects against the rare slow-pull race
    where this iteration started under our lease but a peer replica
    took over (after lease expiry) before the GraphQL call returned —
    in that case the *new* leader has already advanced the cursor and
    we MUST NOT roll it backwards. The manual admin endpoint passes
    ``None`` (unfenced) so an operator can force a pull regardless of
    lease state.
    """
    if db is None:
        return {"ok": False, "reason": "no_db"}
    from config import CF_ZONE_ID, CF_ANALYTICS_API_TOKEN
    if not CF_ZONE_ID or not CF_ANALYTICS_API_TOKEN:
        return {"ok": False, "reason": "cf_not_configured"}
    if graphql_callable is None:
        from cloudflare_client import _graphql_query
        graphql_callable = _graphql_query

    now = now_utc or datetime.now(timezone.utc)
    # Cursor in Mongo so an isolate restart doesn't re-pull a window.
    try:
        lock = await db.job_locks.find_one({"_id": CF_PULL_LOCK_ID}) or {}
    except Exception:
        lock = {}
    cursor_iso = lock.get(CF_PULL_CURSOR_FIELD)
    if cursor_iso:
        try:
            since = datetime.fromisoformat(cursor_iso.replace("Z", "+00:00"))
        except Exception:
            since = now - timedelta(minutes=CF_PULL_LOOKBACK_MIN)
    else:
        since = now - timedelta(minutes=CF_PULL_LOOKBACK_MIN)
    # CF aggregates by minute. Stop the window at the previous minute
    # to avoid double-counting an in-flight bucket.
    until = now.replace(second=0, microsecond=0)
    if until <= since:
        return {"ok": True, "accepted": 0, "dropped": 0,
                "since": since.isoformat(), "until": until.isoformat(),
                "calls": 0, "subdivisions": 0, "saturated_windows": [],
                "reason": "empty_window"}
    # Cap lookback so a long isolate sleep doesn't pull a huge window.
    if (until - since) > timedelta(minutes=CF_PULL_MAX_LOOKBACK_MIN):
        since = until - timedelta(minutes=CF_PULL_MAX_LOOKBACK_MIN)

    try:
        pulled = await _pull_cf_window_paginated(graphql_callable, since, until)
    except Exception as exc:
        logger.warning("[unified_logs] CF pull GraphQL failed: %s", exc)
        return {"ok": False, "reason": "graphql_error"}

    rows: List[Dict[str, Any]] = []
    try:
        for grp in pulled["rows"]:
            rows.append(normalize_cf_http_request_row(grp))
    except Exception as exc:
        logger.warning("[unified_logs] CF pull parse failed: %s", exc)
        return {"ok": False, "reason": "parse_error"}

    result = {"accepted": 0, "dropped": 0}
    if rows:
        result = await _dao.insert_logs(db, rows, default_source="cloudflare")
    # Advance cursor to ``until`` so the next iteration picks up only
    # new data.
    #
    # Task #947 fencing: when we're running under a lease, the cursor
    # write is keyed on `lease_owner == <us>` so a slow pull that
    # spilled past TTL into a peer's term cannot rewind the cursor.
    # ``upsert=True`` is intentionally kept ONLY for the unfenced
    # (admin-manual) path so the very first pull on a brand-new
    # deployment still bootstraps the cursor doc.
    cursor_filter: Dict[str, Any] = {"_id": CF_PULL_LOCK_ID}
    if lease_owner is not None:
        cursor_filter[CF_PULL_LEASE_OWNER_FIELD] = lease_owner
    # Task #953/#961 — append this tick to the rolling 24h
    # pagination-cost history.
    #
    # The new entry is pushed via an atomic ``$push`` with
    # ``$slice: -CF_PULL_HISTORY_MAX_ENTRIES`` rather than a Python
    # read-modify-write. This makes the append race-proof: the
    # background leader runs under the Task #947 lease, but the
    # manual admin "run CF pull now" endpoint bypasses the lease, so
    # an operator click during a normal tick used to be able to drop
    # one entry of history.  The atomic operator means both writers
    # land their entries; ``$slice`` keeps the list bounded.
    #
    # 24h pruning is handled at READ time inside
    # ``_compute_cf_pull_24h_aggregate`` (which already filters
    # entries older than ``CF_PULL_HISTORY_WINDOW_S``). The
    # ``$slice`` here only enforces the absolute size cap so the
    # cursor doc cannot grow without bound under a misconfigured
    # tick interval (e.g. dev mode at 10s/tick = 8640 ticks/day).
    new_history_entry = {
        "ts": now.isoformat(),
        "calls": int(pulled.get("calls") or 0),
        "subdivisions": int(pulled.get("subdivisions") or 0),
        "saturated": len(pulled.get("saturated_windows") or []),
    }
    try:
        await db.job_locks.update_one(
            cursor_filter,
            {
                "$set": {
                    CF_PULL_CURSOR_FIELD: until.isoformat(),
                    "updated_at": now.isoformat(),
                    "last_accepted": result["accepted"],
                    "last_dropped": result["dropped"],
                    # Task #948 — pagination telemetry. ``last_calls``
                    # lets an operator notice "we suddenly went from 1
                    # call/tick to 50" (i.e. someone is hammering the
                    # zone). ``last_saturated_windows`` lists the
                    # minute-buckets that hit the cap even at the floor
                    # — if it's consistently non-empty we need a finer
                    # dimension cut.
                    "last_calls": pulled["calls"],
                    "last_subdivisions": pulled["subdivisions"],
                    "last_saturated_windows": pulled["saturated_windows"],
                },
                # Task #953 — rolling 24h window of per-tick pagination
                # cost so the admin dashboard can render a trend (not
                # just the most recent tick).
                # Task #961 — atomic append. ``$slice: -N`` keeps only
                # the last N entries so two simultaneous pushes (e.g.
                # background leader + manual admin trigger) both land
                # without either losing its entry.
                "$push": {
                    "cf_pull_history": {
                        "$each": [new_history_entry],
                        "$slice": -CF_PULL_HISTORY_MAX_ENTRIES,
                    },
                },
            },
            upsert=(lease_owner is None),
        )
    except Exception as exc:
        logger.warning("[unified_logs] CF pull cursor write failed: %s", exc)

    # Task #952 — fire-and-forget: persist any saturated minutes to the
    # rolling-24h collection and page on-call when fresh saturations
    # are observed (debounced to one page per 24h). Wrapped in its own
    # ``create_task`` so a slow Mongo / email provider on the alert
    # side cannot stall the CF pull loop or block the cursor advance
    # above. The handler itself is best-effort and never raises.
    if pulled.get("saturated_windows"):
        try:
            from routes.admin_logs_cf_pull_saturation_alerts import (
                record_and_maybe_alert,
            )
            asyncio.create_task(record_and_maybe_alert(
                db, pulled["saturated_windows"], now,
            ))
        except Exception as exc:
            logger.debug(
                "[unified_logs] saturation alert schedule failed: %s", exc,
            )

    return {
        "ok": True,
        "accepted": result["accepted"],
        "dropped": result["dropped"],
        "since": since.isoformat(),
        "until": until.isoformat(),
        "calls": pulled["calls"],
        "subdivisions": pulled["subdivisions"],
        "saturated_windows": pulled["saturated_windows"],
    }


async def _try_acquire_cf_pull_lease(
    db_handle, now: Optional[datetime] = None,
    owner_id: Optional[str] = None,
    ttl_s: Optional[int] = None,
) -> bool:
    """CF-pull-specific wrapper around the shared lease helper.

    Kept as a thin shim so existing call sites and the Task #947
    regression suite (``tests/test_unified_logs_cf_pull.py``) can keep
    importing this name unchanged. See ``background_lease`` for the
    underlying state-machine docstring (Task #950).
    """
    return await _bglease.try_acquire_lease(
        db_handle,
        CF_PULL_LOCK_ID,
        owner_id or _CF_PULL_LEASE_OWNER_ID,
        int(ttl_s if ttl_s is not None else CF_PULL_LEASE_TTL_S),
        now=now,
    )


async def _release_cf_pull_lease(
    db_handle, owner_id: Optional[str] = None,
) -> None:
    """CF-pull-specific wrapper around the shared release helper.

    Kept as a thin shim so existing call sites and the Task #947
    regression suite can keep importing this name unchanged.
    """
    await _bglease.release_lease(
        db_handle,
        CF_PULL_LOCK_ID,
        owner_id or _CF_PULL_LEASE_OWNER_ID,
    )


async def _unified_logs_cf_pull_loop():
    """Periodic Cloudflare pull. Runs every ``CF_PULL_INTERVAL_S``
    seconds, idempotent across multiple isolates via the cursor doc.

    Cross-replica dedup is handled by ``_try_acquire_cf_pull_lease``
    (Task #947): every replica may run this loop, but only the one
    holding the Mongo-backed lease actually fires the GraphQL pull.
    Followers wake up at a sub-interval cadence so they can take
    over within seconds if the leader dies, rather than waiting out
    the full lease TTL.

    On consecutive failures (raised exceptions OR ``ok=False`` results
    that aren't a benign no-op like ``empty_window``/``cf_not_configured``)
    we back off exponentially with jitter so a flapping CF endpoint
    doesn't burn the analytics quota. The backoff is capped at 30 min
    and resets to the base interval on the next successful tick.
    """
    # 30s warmup so the loop doesn't race ensure_indexes during
    # cold-start.
    await asyncio.sleep(30)
    consecutive_failures = 0
    base_interval = max(15, int(CF_PULL_INTERVAL_S))
    # Followers poll the lease at a sub-interval (capped at 30s) so
    # leader fail-over kicks in promptly without hammering Mongo.
    follower_interval = max(15, min(30, base_interval // 2 or base_interval))
    max_backoff = 30 * 60  # 30 min ceiling
    # Outer try/finally ensures lease release runs no matter where the
    # CancelledError lands — including (and especially) during the
    # post-iteration ``asyncio.sleep`` which is the most likely state
    # at SIGTERM. Without this, a leader that's mid-sleep at shutdown
    # would hold the lease until full TTL, defeating the "clean
    # handover on restart/scale-down" criterion of Task #947.
    try:
        while True:
            try:
                now = datetime.now(timezone.utc)
                have_lease = await _try_acquire_cf_pull_lease(db, now=now)
                if not have_lease:
                    # Another replica owns the lease and it's still fresh;
                    # stand down for one follower-interval and re-check.
                    # Crucially, this branch does NOT call the GraphQL
                    # pull, so the CF analytics quota cost stays at 1×
                    # regardless of replica count.
                    await asyncio.sleep(follower_interval)
                    continue
                res = await _try_run_cf_pull_once(
                    now_utc=now,
                    lease_owner=_CF_PULL_LEASE_OWNER_ID,
                )
                reason = (res or {}).get("reason")
                # ``empty_window`` and ``cf_not_configured`` are not actual
                # failures — treat them as success for backoff purposes so
                # we don't punish a quiet zone or a deployment without CF.
                if (res or {}).get("ok") or reason in ("empty_window", "cf_not_configured"):
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
            except asyncio.CancelledError:
                # Re-raise so the outer ``finally`` runs the release.
                raise
            except Exception as exc:
                consecutive_failures += 1
                logger.warning("[unified_logs] CF pull loop tick failed: %s", exc)

            if consecutive_failures == 0:
                sleep_for = base_interval
            else:
                # 2^n backoff, capped, with ±25% jitter to spread retries
                # across replicas.
                backoff = min(max_backoff, base_interval * (2 ** min(consecutive_failures, 8)))
                jitter = backoff * 0.25 * (random.random() * 2 - 1)
                sleep_for = max(base_interval, int(backoff + jitter))
                logger.info(
                    "[unified_logs] CF pull backoff: failures=%d sleeping=%ds",
                    consecutive_failures, sleep_for,
                )
            await asyncio.sleep(sleep_for)
    finally:
        # Best-effort release. Wrapped in shield+try so a second
        # cancellation (rare, e.g. SIGKILL) or a flaky Mongo doesn't
        # mask the outer CancelledError. Only releases if WE still
        # hold the lease — the scoped CAS in _release_cf_pull_lease
        # makes this safe even if a peer has already taken over.
        try:
            await asyncio.shield(_release_cf_pull_lease(db))
        except Exception:
            pass


# Test-only knob: lets pytest force the loop body without actually
# starting the periodic task.
_run_cf_pull_once = _try_run_cf_pull_once
