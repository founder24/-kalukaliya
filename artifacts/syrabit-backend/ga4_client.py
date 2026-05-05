"""
GA4 Analytics Data API helper for Syrabit.
Uses service-account-less OAuth with the Analytics Data API.
Falls back gracefully when credentials are missing/invalid.
"""
import logging
from datetime import datetime, timezone
from typing import Optional

from config import Configurator

logger = logging.getLogger(__name__)

_ga4_http: Optional["httpx.AsyncClient"] = None

def _get_ga4_client():
    global _ga4_http
    if _ga4_http is None:
        import httpx
        _ga4_http = httpx.AsyncClient(
            timeout=15,
            limits=httpx.Limits(max_connections=5, max_keepalive_connections=2),
        )
    return _ga4_http

_db_token_cache: dict = {"token": None, "loaded": False}

async def _load_db_refresh_token() -> str:
    """Load GA4 refresh token from MongoDB api_config (persisted by /admin/ga4/connect)."""
    if _db_token_cache["loaded"]:
        return _db_token_cache["token"] or ""
    try:
        from deps import db, is_mongo_available
        if db is not None and await is_mongo_available():
            cfg = await db.api_config.find_one({}, {"ga4": 1})
            token = (cfg or {}).get("ga4", {}).get("refresh_token", "")
            if token:
                _db_token_cache["token"] = token
                Configurator.set_runtime_env("GA4_REFRESH_TOKEN", token)
            _db_token_cache["loaded"] = True
            return token
    except Exception as e:
        logger.debug(f"GA4 db token load: {e}")
    return ""

def _cfg():
    """Read GA4 credentials from environment at call time (never cached at module level)."""
    return {
        "property_id":    Configurator.get("GA4_PROPERTY_ID", ""),
        "client_id":      Configurator.get("GOOGLE_OAUTH_CLIENT_ID", ""),
        "client_secret":  Configurator.get("GOOGLE_OAUTH_CLIENT_SECRET", ""),
        "refresh_token":  Configurator.get("GA4_REFRESH_TOKEN", ""),
    }


def _ga4_enabled() -> bool:
    """Task #383 — gate GA4 traffic behind ``GA4_ENABLED``.

    The flag defaults OFF when CF Web Analytics is on (see ``config.py``)
    so the analytics team can flip the entire GA4 path dormant in one
    place once the CF beacon is verified — without having to delete the
    refresh token. We re-read the env var on every call (rather than
    caching the import-time value) so an operator can flip it via
    ``Configurator.set_runtime_env`` without a restart, mirroring how
    every other GA4 credential is read here.
    """
    raw = Configurator.get("GA4_ENABLED", "")
    if raw:
        return raw.strip().lower() in ("1", "true", "yes", "on")
    # No explicit value at runtime — fall back to the boolean computed
    # at config import time (which already factors in CF_WEB_ANALYTICS_ON).
    try:
        from config import GA4_ENABLED as _flag_default
        return bool(_flag_default)
    except Exception:
        return True  # safest fallback — preserves legacy behaviour


def _is_configured() -> bool:
    if not _ga4_enabled():
        return False
    c = _cfg()
    return bool(c["property_id"] and c["client_secret"] and c["refresh_token"])


async def _ensure_configured() -> bool:
    """Check config; if refresh_token missing, try loading from DB."""
    if not _ga4_enabled():
        return False
    if _is_configured():
        return True
    await _load_db_refresh_token()
    return _is_configured()


async def _get_access_token() -> Optional[str]:
    """Exchange refresh token for an access token."""
    if not await _ensure_configured():
        return None
    c = _cfg()
    if not c["refresh_token"]:
        return None
    try:
        r = await _get_ga4_client().post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id":     c["client_id"],
                "client_secret": c["client_secret"],
                "refresh_token": c["refresh_token"],
                "grant_type":    "refresh_token",
            },
            timeout=10,
        )
        r.raise_for_status()
        return r.json().get("access_token")
    except Exception as e:
        logger.warning(f"GA4 token refresh failed: {e}")
        return None


async def run_report(dimensions: list, metrics: list,
                     date_ranges: list, order_bys: list = None,
                     limit: int = 30) -> Optional[dict]:
    """Run a GA4 Data API report. Returns raw response or None."""
    if not await _ensure_configured():
        return None
    token = await _get_access_token()
    if not token:
        return None
    try:
        body = {
            "dimensions": [{"name": d} for d in dimensions],
            "metrics": [{"name": m} for m in metrics],
            "dateRanges": date_ranges,
            "limit": limit,
        }
        if order_bys:
            body["orderBys"] = order_bys
        url = f"https://analyticsdata.googleapis.com/v1beta/properties/{_cfg()['property_id']}:runReport"
        r = await _get_ga4_client().post(url, json=body,
            headers={"Authorization": f"Bearer {token}"}, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.warning(f"GA4 report failed: {e}")
        return None


async def get_visitor_stats_ga4(days: int = 7) -> Optional[dict]:
    """
    Fetch daily visitors + page views from GA4.
    Returns None if GA4 not configured.
    """
    if not await _ensure_configured():
        return None

    today = datetime.now(timezone.utc)
    date_ranges = [{"startDate": f"{days}daysAgo", "endDate": "today"}]

    # Daily breakdown
    daily_resp = await run_report(
        dimensions=["date"],
        metrics=["activeUsers", "screenPageViews"],
        date_ranges=date_ranges,
        order_bys=[{"dimension": {"dimensionName": "date"}}],
        limit=days + 1,
    )

    # Totals
    total_resp = await run_report(
        dimensions=[],
        metrics=["activeUsers", "screenPageViews", "newUsers"],
        date_ranges=[{"startDate": "365daysAgo", "endDate": "today"}],
        limit=1,
    )

    # Today
    today_resp = await run_report(
        dimensions=[],
        metrics=["activeUsers", "screenPageViews"],
        date_ranges=[{"startDate": "today", "endDate": "today"}],
        limit=1,
    )

    daily_visitors = []
    if daily_resp:
        for row in daily_resp.get("rows", []):
            raw_date = row["dimensionValues"][0]["value"]
            formatted = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
            daily_visitors.append({
                "date": formatted,
                "visitors": int(row["metricValues"][0]["value"]),
                "page_views": int(row["metricValues"][1]["value"]),
            })

    total_visitors = 0
    if total_resp and total_resp.get("rows"):
        total_visitors = int(total_resp["rows"][0]["metricValues"][0]["value"])

    visitors_today = 0
    page_views_today = 0
    if today_resp and today_resp.get("rows"):
        visitors_today = int(today_resp["rows"][0]["metricValues"][0]["value"])
        page_views_today = int(today_resp["rows"][0]["metricValues"][1]["value"])

    return {
        "total_visitors": total_visitors,
        "visitors_today": visitors_today,
        "page_views_today": page_views_today,
        "daily_visitors": daily_visitors,
        "source": "ga4",
    }


async def get_top_pages_ga4(limit: int = 20) -> Optional[list]:
    """Fetch top pages from GA4 by page views."""
    if not await _ensure_configured():
        return None
    resp = await run_report(
        dimensions=["pagePath"],
        metrics=["screenPageViews", "activeUsers"],
        date_ranges=[{"startDate": "30daysAgo", "endDate": "today"}],
        order_bys=[{"metric": {"metricName": "screenPageViews"}, "desc": True}],
        limit=limit,
    )
    if not resp:
        return None
    pages = []
    for row in resp.get("rows", []):
        pages.append({
            "path": row["dimensionValues"][0]["value"],
            "views": int(row["metricValues"][0]["value"]),
            "unique_visitors": int(row["metricValues"][1]["value"]),
        })
    return pages


async def get_top_referrers_ga4(limit: int = 15) -> Optional[list]:
    """Fetch top traffic sources from GA4."""
    if not await _ensure_configured():
        return None
    resp = await run_report(
        dimensions=["sessionSource"],
        metrics=["sessions"],
        date_ranges=[{"startDate": "30daysAgo", "endDate": "today"}],
        order_bys=[{"metric": {"metricName": "sessions"}, "desc": True}],
        limit=limit,
    )
    if not resp:
        return None
    refs = []
    for row in resp.get("rows", []):
        src = row["dimensionValues"][0]["value"]
        if src and src != "(direct)":
            refs.append({
                "source": src,
                "count": int(row["metricValues"][0]["value"]),
            })
    return refs


def get_oauth_url(redirect_uri: str) -> str:
    """Generate the GA4 OAuth consent URL."""
    from urllib.parse import urlencode
    c = _cfg()
    params = {
        "client_id": c["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "https://www.googleapis.com/auth/analytics.readonly",
        "access_type": "offline",
        "prompt": "consent",
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params)


async def exchange_code_for_tokens(code: str, redirect_uri: str) -> Optional[dict]:
    """Exchange OAuth code for access + refresh tokens."""
    c = _cfg()
    try:
        r = await _get_ga4_client().post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id":     c["client_id"],
                "client_secret": c["client_secret"],
                "redirect_uri":  redirect_uri,
                "code":          code,
                "grant_type":    "authorization_code",
            },
            timeout=15,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"GA4 code exchange failed: {e}")
        return None
