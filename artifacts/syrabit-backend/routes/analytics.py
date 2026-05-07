"""Syrabit.ai — Analytics tracking routes"""
import re, asyncio, time, logging
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone, timedelta
from fastapi import (
    APIRouter, Depends, Body, Request, BackgroundTasks,
)
from starlette.requests import Request as StarletteRequest

from config import Configurator
from deps import (
    db,
    is_mongo_available,
)
from auth_deps import (
    get_admin_user, get_current_user_optional,
)
from db_ops import supa_list_users
from analytics_helpers import (
    get_library_analytics,
    get_pwa_stats,
    track_library_event,
    track_page_view,
    track_pwa_install,
)
import cloudflare_client

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/admin/analytics/cf-status")
async def admin_cf_status(admin: dict = Depends(get_admin_user)):
    """Surface Cloudflare Analytics token health for the admin UI banner.

    Returns:
      configured: env vars present?
      auth_ok: True/False/None (None = not yet probed since startup)
      needs_rotation: True when token is rejected by Cloudflare
      last_error, last_check_at, blocked_for_seconds, rotation_hint
    """
    # If we've never tried, probe once now so the UI gets a definitive answer.
    status_obj = cloudflare_client.get_auth_status()
    if status_obj.get("auth_ok") is None and status_obj.get("configured"):
        await cloudflare_client.get_visitor_stats_cf(days=1)
        status_obj = cloudflare_client.get_auth_status()
    return status_obj


@router.get("/admin/analytics/cf-overview")
async def admin_cf_overview(range: str = "7d", admin: dict = Depends(get_admin_user)):
    """Cloudflare-mirror analytics overview (Requests / Bandwidth / Visits /
    Page views) for the admin Traffic card. ``range`` is ``24h`` / ``7d`` / ``30d``
    matching the Cloudflare Account Analytics time selector."""
    if range not in ("24h", "7d", "30d"):
        range = "7d"
    overview = await cloudflare_client.get_cf_overview(range_key=range)
    if overview is None:
        # Pass auth/breaker status through so the UI can render the rotation banner
        return {"connected": False, "range": range, "status": cloudflare_client.get_auth_status()}
    return {"connected": True, **overview}


@router.post("/admin/analytics/cf-recheck")
async def admin_cf_recheck(admin: dict = Depends(get_admin_user)):
    """Reset the auth circuit breaker and re-probe Cloudflare immediately.
    Call this after rotating CF_ANALYTICS_API_TOKEN on Railway."""
    cloudflare_client.reset_auth_state()
    if cloudflare_client.is_configured():
        await cloudflare_client.get_visitor_stats_cf(days=1)
    return cloudflare_client.get_auth_status()


@router.get("/admin/analytics")
async def admin_analytics(days: int = 30, admin: dict = Depends(get_admin_user)):
    """Admin analytics — Cloudflare is the sole source of truth for
    visitor/page-view numbers. GA4/server-side/JS-tracked merge logic
    has been removed (Task #364)."""
    users = await supa_list_users()

    signup_range = min(days, 90)
    daily_signups = []
    for i in range(signup_range):
        day = (datetime.now(timezone.utc) - timedelta(days=signup_range-1-i)).strftime("%Y-%m-%d")
        count = sum(1 for u in users if u.get("created_at", "")[:10] == day)
        daily_signups.append({"date": day, "count": count})

    plan_usage = {}
    for u in users:
        p = u.get("plan", "free")
        plan_usage[p] = plan_usage.get(p, 0) + u.get("credits_used", 0)

    fetch_days = min(days, 90)
    cf_vs, cf_pages_res, library_stats = await asyncio.gather(
        cloudflare_client.get_visitor_stats_cf(days=fetch_days),
        cloudflare_client.get_top_pages_cf(),
        get_library_analytics(days=days),
        return_exceptions=True,
    )

    cf_data = cf_vs if isinstance(cf_vs, dict) and cf_vs else None
    cf_connected = cf_data is not None and any(
        k in cf_data for k in ("total_visitors", "visitors_today", "page_views_today", "daily_visitors")
    )
    ga4_connected = bool(Configurator.get("GA4_REFRESH_TOKEN"))

    visitor_stats: dict = {}
    if cf_connected:
        cloudflare_block = {
            "total_visitors": cf_data.get("total_visitors", 0),
            "visitors_today": cf_data.get("visitors_today", 0),
            "page_views_today": cf_data.get("page_views_today", 0),
            "total_page_views": cf_data.get("total_page_views", 0),
            "total_requests": cf_data.get("total_requests", 0),
            "requests_today": cf_data.get("requests_today", 0),
            "total_bytes": cf_data.get("total_bytes", 0),
            "bytes_today": cf_data.get("bytes_today", 0),
            "daily_visitors": cf_data.get("daily_visitors", []),
            "period_days": fetch_days,
        }
        visitor_stats = {
            **cloudflare_block,
            "cloudflare": cloudflare_block,
        }

    top_pages = cf_pages_res if isinstance(cf_pages_res, list) else []
    top_referrers: list = []  # CF GraphQL free tier does not expose referrer dimension

    return {
        "daily_signups": daily_signups,
        "plan_usage": plan_usage,
        "library": library_stats if isinstance(library_stats, dict) else {},
        "total_users": len(users),
        "active_users": sum(1 for u in users if u.get("credits_used", 0) > 0),
        "visitor_stats": visitor_stats,
        "top_pages": top_pages,
        "top_referrers": top_referrers,
        "ga4_connected": ga4_connected,
        "cf_connected": cf_connected,
    }


async def _bg_track_page_view(**kwargs):
    """Background wrapper — must never raise, telemetry is best-effort."""
    try:
        await track_page_view(**kwargs)
    except Exception as e:
        logger.debug(f"bg page-view write failed: {e}")


@router.post("/analytics/page-view")
async def track_page_view_endpoint(
    request: StarletteRequest,
    background_tasks: BackgroundTasks,
    path: str = Body(...),
    visitor_id: str = Body(...),
    referrer: str = Body(None),
    session_id: str = Body(None),
    user_agent: str = Body(None),
    screen_width: int = Body(None),
    is_404_hint: bool = Body(None),
    user: dict = Depends(get_current_user_optional)
):
    """
    Public endpoint to track a page view.
    Called from frontend on every route change.
    Returns 200 immediately; the Mongo write is deferred to a background
    task so the browser is not blocked on cross-region DB latency.
    """
    user_id = user.get("id") if user else None
    effective_ua = user_agent or request.headers.get("user-agent") or ""
    cf_country = request.headers.get("cf-ipcountry", "")
    x_forwarded = request.headers.get("x-forwarded-for", "")
    client_ip = x_forwarded.split(",")[0].strip() if x_forwarded else (request.client.host if request.client else "")
    background_tasks.add_task(
        _bg_track_page_view,
        path=path,
        visitor_id=visitor_id,
        user_id=user_id,
        referrer=referrer,
        user_agent=effective_ua,
        screen_width=screen_width,
        session_id=session_id,
        client_ip=client_ip if not cf_country else None,
        pre_resolved_country=cf_country or None,
        is_404_hint=is_404_hint,
    )
    return {"status": "ok"}


_public_stats_cache: Dict[str, Any] = {"data": None, "ts": 0}

@router.get("/analytics/public-stats")
async def public_stats():
    """Lightweight cached stats for landing page (no auth required)."""
    now = time.time()
    if _public_stats_cache["data"] and now - _public_stats_cache["ts"] < 300:
        return _public_stats_cache["data"]

    total_users = 0
    try:
        from db_ops import supa_list_users
        users = await supa_list_users()
        total_users = len(users)
    except Exception:
        pass

    total_subjects = 0
    try:
        if db is not None and await is_mongo_available():
            total_subjects = await db.subjects.count_documents({})
    except Exception:
        pass

    result = {
        "total_users": total_users,
        "total_subjects": total_subjects,
    }
    _public_stats_cache["data"] = result
    _public_stats_cache["ts"] = now
    return result


# ─────────────────────────────────────────────
# Top routes for build-time prerendering (Task #388)
# ─────────────────────────────────────────────
# Returns the most-visited routes over the last `days` days based on the
# `page_views` collection (same source as the admin /admin/analytics
# dashboard). Public + cached so the static-site build can fetch it
# without an admin token. Filtered to subject + chapter route shapes
# (3 or 4 path segments, lowercase slug-style) so consumers don't need
# to re-filter. Falls back to an empty list when Mongo is unavailable.

_top_routes_cache: Dict[str, Any] = {}
_TOP_ROUTES_TTL_SECONDS = 600  # 10 min

_SLUG_SEG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]*$")


def _is_prerender_candidate_path(path: str) -> bool:
    if not path or not path.startswith("/"):
        return False
    parts = path.strip("/").split("/")
    if len(parts) not in (3, 4):
        return False
    return all(_SLUG_SEG_RE.match(p) for p in parts)


@router.get("/analytics/top-routes")
async def top_routes(days: int = 30, limit: int = 200):
    """Top subject + chapter routes by real pageviews.

    Used by the static prerender step (Task #388) to pick which routes
    to bake into HTML based on actual demand instead of bundle order.
    No auth: returns aggregate counts only, no PII.
    """
    days = max(1, min(int(days or 30), 90))
    limit = max(1, min(int(limit or 200), 1000))

    cache_key = f"{days}:{limit}"
    now = time.time()
    cached = _top_routes_cache.get(cache_key)
    if cached and now - cached["ts"] < _TOP_ROUTES_TTL_SECONDS:
        return cached["data"]

    routes: List[Dict[str, Any]] = []
    try:
        if db is not None and await is_mongo_available():
            since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
            pipeline = [
                {"$match": {
                    "date": {"$gte": since},
                    "is_bot": {"$ne": True},
                    "is_404": {"$ne": True},
                }},
                {"$group": {"_id": "$path", "views": {"$sum": 1}}},
                {"$sort": {"views": -1}},
                {"$limit": limit * 4},  # over-fetch then filter
            ]
            rows = await db.page_views.aggregate(pipeline).to_list(limit * 4)
            for row in rows:
                p = row.get("_id") or ""
                if _is_prerender_candidate_path(p):
                    routes.append({"path": p, "views": int(row.get("views") or 0)})
                if len(routes) >= limit:
                    break
    except Exception as e:
        logger.debug(f"top_routes aggregation failed: {e}")
        routes = []

    result = {
        "days": days,
        "limit": limit,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "routes": routes,
    }
    _top_routes_cache[cache_key] = {"ts": now, "data": result}
    return result


async def _bg_session_ping(session_id: str, visitor_id: str):
    try:
        if db is not None and await is_mongo_available():
            now_iso = datetime.now(timezone.utc).isoformat()
            await db.sessions.update_one(
                {"session_id": session_id},
                {
                    "$setOnInsert": {
                        "session_id": session_id,
                        "visitor_id": visitor_id,
                        "start_time": now_iso,
                        "entry_path": "",
                        "page_count": 0,
                        "is_bot": False,
                    },
                    "$set": {"last_ping": now_iso},
                },
                upsert=True,
            )
    except Exception as e:
        logger.debug(f"session_ping failed: {e}")


@router.post("/analytics/session-ping")
async def session_ping_endpoint(
    background_tasks: BackgroundTasks,
    session_id: str = Body(...),
    visitor_id: str = Body(...),
):
    """Keep a session alive. Called every 30s from frontend heartbeat.
    Defers the Mongo upsert so the heartbeat returns instantly."""
    background_tasks.add_task(_bg_session_ping, session_id, visitor_id)
    return {"status": "ok"}


async def _bg_session_end(session_id: str, end_iso: str):
    try:
        if db is not None and await is_mongo_available():
            await db.sessions.update_one(
                {"session_id": session_id},
                {"$set": {"end_time": end_iso, "last_ping": end_iso}},
            )
    except Exception as e:
        logger.debug(f"session_end failed: {e}")


@router.post("/analytics/session-end")
async def session_end_endpoint(
    background_tasks: BackgroundTasks,
    session_id: str = Body(...),
    visitor_id: str = Body(None),
    end_timestamp: str = Body(None),
):
    """Record session end time. Called via sendBeacon on tab close.
    Validation runs synchronously; the Mongo write runs in the background."""
    end_iso = None
    if end_timestamp:
        try:
            datetime.fromisoformat(end_timestamp.replace("Z", "+00:00"))
            end_iso = end_timestamp
        except (ValueError, AttributeError):
            pass
    if not end_iso:
        end_iso = datetime.now(timezone.utc).isoformat()
    background_tasks.add_task(_bg_session_end, session_id, end_iso)
    return {"status": "ok"}


async def _bg_track_pwa_install(action: str, metadata: dict, user_id: Optional[str]):
    try:
        await track_pwa_install(action=action, metadata=metadata, user_id=user_id)
    except Exception as e:
        logger.debug(f"bg pwa_install track failed: {e}")


async def _bg_track_library_event(**kwargs):
    try:
        await track_library_event(**kwargs)
    except Exception as e:
        logger.debug(f"bg library_event track failed: {e}")


@router.post("/analytics/track")
async def track_event(
    background_tasks: BackgroundTasks,
    event_type: str = Body(...),
    subject_id: str = Body(None),
    chapter_id: str = Body(None),
    search_query: str = Body(None),
    metadata: dict = Body(None),
    user: dict = Depends(get_current_user_optional)
):
    """
    Public endpoint for tracking library interactions.
    Called from frontend when user interacts with content.

    Event types:
    - search: User searched in library
    - subject_view: User opened a subject
    - chapter_view: User viewed a chapter
    - ask_ai_click: User clicked Ask AI button
    - document_open: User opened document viewer

    Mongo writes are deferred so this returns instantly.
    """
    user_id = user.get("id") if user else None

    if event_type == "pwa_install":
        action = (metadata or {}).get("action", "unknown")
        background_tasks.add_task(_bg_track_pwa_install, action, metadata, user_id)
        return {"status": "tracked"}

    background_tasks.add_task(
        _bg_track_library_event,
        event_type=event_type,
        subject_id=subject_id,
        chapter_id=chapter_id,
        user_id=user_id,
        search_query=search_query,
        metadata=metadata,
    )
    return {"status": "tracked"}


@router.get("/admin/pwa/stats")
async def admin_pwa_stats(admin: dict = Depends(get_admin_user)):
    return await get_pwa_stats()


# ─────────────────────────────────────────────────────────────────────────────
# Task #408: hydrate telemetry — server-side mirror of the client-side
# `hydrate_preload_failed` / `hydrate_recovered` / `hydrate_stalled` events.
# Stored separately from PostHog so the admin dashboard can render an
# "ops health" tile without a PostHog API integration. Documents are
# auto-deleted after 30 days via a TTL index (created lazily on first
# write to avoid startup churn).
# ─────────────────────────────────────────────────────────────────────────────

_HYDRATE_TTL_INDEX_READY = False
_HYDRATE_VALID_EVENTS = {
    "hydrate_preload_failed",
    "hydrate_recovered",
    "hydrate_stalled",
}


async def _ensure_hydrate_indexes():
    global _HYDRATE_TTL_INDEX_READY
    if _HYDRATE_TTL_INDEX_READY:
        return
    try:
        # 30-day TTL — operationally interesting window is the last 7d,
        # but we keep extra runway for incident postmortems.
        await db.hydrate_telemetry.create_index(
            "created_at", expireAfterSeconds=60 * 60 * 24 * 30,
        )
        await db.hydrate_telemetry.create_index([("event", 1), ("created_at", -1)])
        _HYDRATE_TTL_INDEX_READY = True
    except Exception as e:
        logger.warning(f"hydrate_telemetry index create failed (non-fatal): {e}")


@router.post("/analytics/hydrate-event")
async def track_hydrate_event(
    request: Request,
    event: str = Body(...),
    kind: Optional[str] = Body(None),
    path: Optional[str] = Body(None),
    auto_reload: Optional[bool] = Body(None),
    preload_failed: Optional[bool] = Body(None),
    message: Optional[str] = Body(None),
    name: Optional[str] = Body(None),
    elapsed_ms: Optional[int] = Body(None),
    ms_since_reload: Optional[int] = Body(None),
):
    """Public endpoint: persist a hydrate-lifecycle event for ops dashboards.

    Accepts only the three known event names; all other payloads are dropped
    so a misbehaving (or malicious) client cannot pollute the collection.
    Best-effort — never raises; analytics must not break page loads.
    """
    if event not in _HYDRATE_VALID_EVENTS:
        return {"status": "ignored"}
    # Build the document synchronously (validation/capping) but defer the
    # Mongo write so the page-load isn't blocked on cross-region latency.
    ua = request.headers.get("user-agent", "")[:300]
    doc = {
        "event": event,
        "kind": (kind or "")[:64] or None,
        "path": (path or "")[:200] or None,
        "auto_reload": bool(auto_reload) if auto_reload is not None else None,
        "preload_failed": bool(preload_failed) if preload_failed is not None else None,
        "message": (message or "")[:300] or None,
        "name": (name or "")[:64] or None,
        "elapsed_ms": int(elapsed_ms) if isinstance(elapsed_ms, (int, float)) else None,
        "ms_since_reload": int(ms_since_reload) if isinstance(ms_since_reload, (int, float)) else None,
        "ua": ua or None,
        "created_at": datetime.now(timezone.utc),
    }

    async def _bg_insert():
        try:
            await _ensure_hydrate_indexes()
            await db.hydrate_telemetry.insert_one(doc)
        except Exception as e:
            logger.debug(f"hydrate-event ingest failed: {e}")

    bg = getattr(request.state, "background_tasks", None)
    if bg is None:
        # Fall back to spawning a fire-and-forget task on the running loop
        # if no BackgroundTasks instance was injected via the route signature.
        asyncio.create_task(_bg_insert())
    else:
        bg.add_task(_bg_insert)
    return {"status": "tracked"}


@router.get("/admin/analytics/hydrate-stats")
async def admin_hydrate_stats(
    days: int = 7, admin: dict = Depends(get_admin_user),
):
    """7-day (configurable) ops view of stale-build / hydration health.

    Returns counters + small breakdowns so the admin tile can render a
    healthy empty state when there's nothing to worry about.
    """
    days = max(1, min(int(days or 7), 90))
    since = datetime.now(timezone.utc) - timedelta(days=days)
    empty = {
        "days": days,
        "preload_failed_total": 0,
        "auto_reload_attempts": 0,
        "auto_reload_recoveries": 0,
        "auto_reload_success_rate_pct": None,
        "stalled_total": 0,
        "manual_failures": 0,  # preload_failed without auto_reload
        "top_kinds": [],
        "top_user_agents": [],
        "recent": [],
        "active_alerts": [],
    }
    if not await is_mongo_available():
        return empty
    try:
        coll = db.hydrate_telemetry
        base = {"created_at": {"$gte": since}}

        preload_failed_total = await coll.count_documents({
            **base, "event": "hydrate_preload_failed"
        })
        auto_reload_attempts = await coll.count_documents({
            **base, "event": "hydrate_preload_failed", "auto_reload": True,
        })
        auto_reload_recoveries = await coll.count_documents({
            **base, "event": "hydrate_recovered",
        })
        stalled_total = await coll.count_documents({
            **base, "event": "hydrate_stalled",
        })
        manual_failures = max(0, preload_failed_total - auto_reload_attempts)

        success_rate = None
        if auto_reload_attempts > 0:
            success_rate = round(
                (auto_reload_recoveries / auto_reload_attempts) * 100, 1,
            )

        async def _top(field: str, match: dict, limit: int = 5):
            try:
                cur = coll.aggregate([
                    {"$match": {**base, **match, field: {"$nin": [None, ""]}}},
                    {"$group": {"_id": f"${field}", "count": {"$sum": 1}}},
                    {"$sort": {"count": -1}},
                    {"$limit": limit},
                ])
                return [
                    {"value": doc["_id"], "count": doc["count"]}
                    async for doc in cur
                ]
            except Exception:
                return []

        top_kinds = await _top("kind", {"event": "hydrate_preload_failed"})
        top_user_agents = await _top("ua", {"event": "hydrate_preload_failed"})

        recent_cur = coll.find(
            {**base, "event": {"$in": list(_HYDRATE_VALID_EVENTS)}},
            {"_id": 0, "created_at": 1, "event": 1, "kind": 1, "path": 1,
             "auto_reload": 1, "message": 1, "name": 1},
        ).sort("created_at", -1).limit(15)
        recent = []
        async for doc in recent_cur:
            ts = doc.get("created_at")
            if isinstance(ts, datetime):
                doc["created_at"] = ts.isoformat()
            recent.append(doc)

        # Task #415: surface fired hydrate-scoped alerts so the admin tile
        # shows whether ops has already been paged about an in-progress
        # incident. One per-type query guarantees we return the truly
        # newest unacknowledged alert per type even when one type
        # dominates the recent-alerts stream.
        active_alerts: List[Dict[str, Any]] = []
        for atype in ("hydrate_failure_spike", "hydrate_recovery_low"):
            try:
                adoc = await db.alerts.find_one(
                    {"type": atype, "acknowledged": False},
                    {"type": 1, "title": 1, "body": 1, "fired_at": 1,
                     "threshold_snapshot": 1},
                    sort=[("fired_at", -1)],
                )
                if adoc:
                    adoc["_id"] = str(adoc["_id"])
                    active_alerts.append(adoc)
            except Exception as e:
                logger.debug(f"hydrate active-alerts query failed for {atype}: {e}")

        return {
            "days": days,
            "preload_failed_total": preload_failed_total,
            "auto_reload_attempts": auto_reload_attempts,
            "auto_reload_recoveries": auto_reload_recoveries,
            "auto_reload_success_rate_pct": success_rate,
            "stalled_total": stalled_total,
            "manual_failures": manual_failures,
            "top_kinds": top_kinds,
            "top_user_agents": top_user_agents,
            "recent": recent,
            "active_alerts": active_alerts,
        }
    except Exception as e:
        logger.warning(f"hydrate-stats query failed: {e}")
        return empty


# ─────────────────────────────────────────────────────────────────────────────
# Task #412: alert ops when stale-build failures spike.
#
# Background loop ticks every HYDRATE_ALERT_INTERVAL_S; reads the last hour
# of `hydrate_telemetry` and dispatches at most one alert per incident type
# per HYDRATE_ALERT_COOLDOWN_S window (suppress dupes for ~60 min).
#
# Two independent alert types, both wired through metrics._dispatch_alert
# (SES email + persisted to db.alerts + Slack/webhook if configured):
#
#   1. ``hydrate_failure_spike`` — more than HYDRATE_FAILURE_THRESHOLD
#      `hydrate_preload_failed` events in the last hour. Indicates a CDN
#      mis-config / asset-upload gap.
#   2. ``hydrate_recovery_low`` — auto-reload success rate below
#      HYDRATE_RECOVERY_MIN_RATE_PCT with ≥ HYDRATE_RECOVERY_MIN_ATTEMPTS
#      attempts in the last hour. Indicates the new build is also broken
#      and the loop guard is firing.
# ─────────────────────────────────────────────────────────────────────────────

#
# These three constants are *defaults*. The effective values used by the
# alert loop come from db.api_config.alert_settings.thresholds (loaded into
# metrics._ALERT_THRESHOLDS by metrics._load_alert_settings) so admins can
# tune them from the Alert Settings panel without a deploy. The constants
# are still exported because the test suite uses them as a baseline.
HYDRATE_FAILURE_THRESHOLD = 50          # hydrate_preload_failed events / hour
HYDRATE_RECOVERY_MIN_ATTEMPTS = 10      # min auto-reload attempts before judging
HYDRATE_RECOVERY_MIN_RATE_PCT = 50.0    # success rate floor (%)


def _effective_hydrate_thresholds() -> tuple:
    """Return (failure_per_hour, recovery_min_rate_pct, recovery_min_attempts)
    using the admin-configured values from metrics._ALERT_THRESHOLDS, falling
    back to the module-level defaults when unset or invalid."""
    failure = HYDRATE_FAILURE_THRESHOLD
    rate = HYDRATE_RECOVERY_MIN_RATE_PCT
    attempts = HYDRATE_RECOVERY_MIN_ATTEMPTS
    try:
        from metrics import _ALERT_THRESHOLDS
        try:
            failure = float(_ALERT_THRESHOLDS.get("hydrate_failure_per_hour", failure))
        except (TypeError, ValueError):
            pass
        try:
            rate = float(_ALERT_THRESHOLDS.get("hydrate_recovery_min_rate_pct", rate))
        except (TypeError, ValueError):
            pass
        try:
            attempts = int(float(_ALERT_THRESHOLDS.get("hydrate_recovery_min_attempts", attempts)))
        except (TypeError, ValueError):
            pass
    except Exception:
        pass
    return failure, rate, attempts
HYDRATE_ALERT_COOLDOWN_S = 60 * 60      # 60 min per incident type
HYDRATE_ALERT_INTERVAL_S = 5 * 60       # poll every 5 min
_HYDRATE_ALERT_DASHBOARD_URL = "https://syrabit.ai/admin/dashboard?tab=overview#hydrate-health"

_HYDRATE_ALERT_LAST_FIRED: Dict[str, float] = {}


async def _gather_hydrate_alert_window(window_seconds: int = 3600) -> Dict[str, Any]:
    """Aggregate the last `window_seconds` of hydrate telemetry into the
    counters required by the threshold check. Always returns a stable
    shape; on Mongo failure returns zeros so the alert loop just no-ops.
    """
    out: Dict[str, Any] = {
        "since": datetime.now(timezone.utc) - timedelta(seconds=window_seconds),
        "preload_failed_total": 0,
        "auto_reload_attempts": 0,
        "auto_reload_recoveries": 0,
        "success_rate_pct": None,
        "top_kind": None,
        "sample_message": None,
    }
    if not await is_mongo_available():
        return out
    try:
        coll = db.hydrate_telemetry
        base = {"created_at": {"$gte": out["since"]}}

        out["preload_failed_total"] = await coll.count_documents({
            **base, "event": "hydrate_preload_failed",
        })
        out["auto_reload_attempts"] = await coll.count_documents({
            **base, "event": "hydrate_preload_failed", "auto_reload": True,
        })
        out["auto_reload_recoveries"] = await coll.count_documents({
            **base, "event": "hydrate_recovered",
        })
        if out["auto_reload_attempts"] > 0:
            out["success_rate_pct"] = round(
                (out["auto_reload_recoveries"] / out["auto_reload_attempts"]) * 100, 1,
            )

        # Top failing chunk kind in the window (e.g. "chunk", "css").
        try:
            cur = coll.aggregate([
                {"$match": {**base, "event": "hydrate_preload_failed",
                            "kind": {"$nin": [None, ""]}}},
                {"$group": {"_id": "$kind", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
                {"$limit": 1},
            ])
            async for d in cur:
                out["top_kind"] = {"value": d["_id"], "count": d["count"]}
                break
        except Exception:
            pass

        # Sample error message — most recent non-empty one in the window.
        try:
            sample = await coll.find_one(
                {**base, "event": "hydrate_preload_failed",
                 "message": {"$nin": [None, ""]}},
                {"_id": 0, "message": 1, "name": 1, "path": 1, "created_at": 1},
                sort=[("created_at", -1)],
            )
            if sample:
                out["sample_message"] = sample
        except Exception:
            pass
    except Exception as e:
        logger.debug(f"hydrate alert window aggregation failed: {e}")
    return out


def _format_hydrate_sample(sample: Optional[dict]) -> str:
    if not sample:
        return ""
    parts = []
    if sample.get("name"):
        parts.append(str(sample["name"]))
    if sample.get("message"):
        parts.append(str(sample["message"]))
    line = ": ".join(parts) if parts else ""
    if sample.get("path"):
        line = f"{line} (path: {sample['path']})" if line else f"path: {sample['path']}"
    return line[:300]


async def _evaluate_hydrate_alerts(now_ts: Optional[float] = None) -> List[Dict[str, Any]]:
    """Pure helper used by the loop and tests. Returns the list of alerts
    that *should* be dispatched right now (after cooldown checks). Does
    NOT mutate ``_HYDRATE_ALERT_LAST_FIRED`` — the loop is responsible for
    marking cooldown only after a successful dispatch (so a transient
    SES/webhook failure doesn't suppress the next alert for 60 min).
    Each entry is the kwargs dict for ``metrics._dispatch_alert``.
    """
    if now_ts is None:
        now_ts = time.time()
    snap = await _gather_hydrate_alert_window()
    alerts: List[Dict[str, Any]] = []
    failure_threshold, recovery_min_rate, recovery_min_attempts = _effective_hydrate_thresholds()

    sample_str = _format_hydrate_sample(snap.get("sample_message"))
    top_kind = snap.get("top_kind") or {}
    top_kind_str = (
        f"{top_kind.get('value')} ({top_kind.get('count')} events)"
        if top_kind else "n/a"
    )
    dashboard_line = f"Dashboard: {_HYDRATE_ALERT_DASHBOARD_URL}"

    def _cooldown_ok(key: str) -> bool:
        last = _HYDRATE_ALERT_LAST_FIRED.get(key)
        return last is None or (now_ts - last) >= HYDRATE_ALERT_COOLDOWN_S

    # ── (1) hydrate_failure_spike
    failures = int(snap.get("preload_failed_total") or 0)
    if failures > failure_threshold:
        if _cooldown_ok("hydrate_failure_spike"):
            body_lines = [
                f"{failures} hydrate_preload_failed events in the last hour "
                f"(threshold: {failure_threshold:g}).",
                f"Top failing asset kind: {top_kind_str}.",
            ]
            if sample_str:
                body_lines.append(f"Sample error: {sample_str}")
            body_lines.append(dashboard_line)
            alerts.append({
                "alert_type": "hydrate_failure_spike",
                "title": "Stale-build hydration failures spiked",
                "body": "\n".join(body_lines),
                "threshold_snapshot": {
                    "metric": "hydrate_preload_failed_per_hour",
                    "value": failure_threshold,
                    "actual": failures,
                    "top_kind": top_kind.get("value") if top_kind else None,
                    "auto_reload_attempts": int(snap.get("auto_reload_attempts") or 0),
                    "auto_reload_recoveries": int(snap.get("auto_reload_recoveries") or 0),
                },
            })

    # ── (2) hydrate_recovery_low
    attempts = int(snap.get("auto_reload_attempts") or 0)
    rate = snap.get("success_rate_pct")
    if attempts >= recovery_min_attempts and rate is not None and rate < recovery_min_rate:
        if _cooldown_ok("hydrate_recovery_low"):
            recoveries = int(snap.get("auto_reload_recoveries") or 0)
            body_lines = [
                f"Auto-reload success rate is {rate:.1f}% "
                f"({recoveries}/{attempts}) over the last hour, below the "
                f"{recovery_min_rate:.0f}% floor with "
                f"≥ {recovery_min_attempts} attempts.",
                "The new build may also be broken — clients are likely hitting "
                "the loop guard.",
                f"Top failing asset kind: {top_kind_str}.",
            ]
            if sample_str:
                body_lines.append(f"Sample error: {sample_str}")
            body_lines.append(dashboard_line)
            alerts.append({
                "alert_type": "hydrate_recovery_low",
                "title": "Auto-reload recovery rate is low",
                "body": "\n".join(body_lines),
                "threshold_snapshot": {
                    "metric": "auto_reload_success_rate_pct",
                    "value": recovery_min_rate,
                    "actual": rate,
                    "auto_reload_attempts": attempts,
                    "auto_reload_recoveries": recoveries,
                    "top_kind": top_kind.get("value") if top_kind else None,
                },
            })

    return alerts


async def _hydrate_alert_loop():
    """Background loop: poll hydrate telemetry and fire admin alerts when
    last-hour counters cross thresholds. Modeled on
    ``routes.bot_discovery._seo_health_alert_loop``. Best-effort; swallows
    its own errors so a flaky Mongo can't kill the task.
    """
    # Stagger start so we don't pile onto the boot-time burst alongside
    # the other alert loops.
    await asyncio.sleep(150)
    while True:
        try:
            # Refresh persisted alert settings BEFORE evaluation so admin
            # threshold changes (failure/hour, recovery rate floor, min
            # attempts) take effect within the next tick — same pattern
            # the metrics._alerting_loop uses.
            try:
                from metrics import _load_alert_settings
                await _load_alert_settings()
            except Exception:
                pass
            alerts = await _evaluate_hydrate_alerts()
            if alerts:
                try:
                    from metrics import _dispatch_alert, _alert_last_fired
                    for a in alerts:
                        # Bypass the shared 30-min metrics cooldown — we
                        # already gate ourselves at 60 min per type.
                        _alert_last_fired.pop(a["alert_type"], None)
                        try:
                            await _dispatch_alert(
                                a["alert_type"], a["title"], a["body"],
                                threshold_snapshot=a.get("threshold_snapshot"),
                            )
                        except Exception as dexc:
                            # Don't advance our cooldown on failure — let
                            # the next tick retry. Matches the per-alert
                            # behaviour of _seo_health_alert_loop.
                            logger.warning(
                                f"hydrate alert {a['alert_type']} dispatch failed; "
                                f"will retry next tick: {dexc}"
                            )
                            continue
                        _HYDRATE_ALERT_LAST_FIRED[a["alert_type"]] = time.time()
                except Exception as exc:
                    logger.warning(f"hydrate alert dispatch failed: {exc}")
        except Exception as exc:
            logger.debug(f"hydrate alert loop error: {exc}")
        await asyncio.sleep(HYDRATE_ALERT_INTERVAL_S)


# ─────────────────────────────────────────────
# ADMIN CONTENT MANAGEMENT — Boards / Classes / Streams
# ─────────────────────────────────────────────

# GET aliases — admin UI reads from these (proxy to public handlers)
