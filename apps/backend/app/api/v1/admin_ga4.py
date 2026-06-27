"""
Admin GA4 Endpoints
Google Analytics 4 connection status, OAuth flow initiation, and connection test.
GA4 is an optional diagnostic integration — Cloudflare powers the headline metrics.
Requires: GA4_PROPERTY_ID, GA4_CLIENT_ID, GA4_CLIENT_SECRET, GA4_REFRESH_TOKEN secrets.
"""

from fastapi import APIRouter, Depends, Request
import logging
from datetime import datetime, timezone

from app.api.v1.admin import require_admin_session, csrf_guard
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Admin GA4"],
    dependencies=[Depends(require_admin_session), Depends(csrf_guard)],
)


def _ga4_config():
    return {
        "property_id": getattr(settings, "GA4_PROPERTY_ID", None),
        "client_id": getattr(settings, "GA4_CLIENT_ID", None),
        "client_secret": getattr(settings, "GA4_CLIENT_SECRET", None),
        "refresh_token": getattr(settings, "GA4_REFRESH_TOKEN", None),
    }


@router.get("/ga4/status")
async def ga4_status():
    """
    GA4 connection status.
    Returns connected:true only when all four secrets are present AND the token
    can be refreshed successfully.
    """
    cfg = _ga4_config()
    property_id = cfg["property_id"]
    client_id = cfg["client_id"]
    client_secret = cfg["client_secret"]
    refresh_token = cfg["refresh_token"]

    client_id_set = bool(client_id)
    client_secret_set = bool(client_secret)

    if not all([property_id, client_id, client_secret, refresh_token]):
        return {
            "connected": False,
            "property_id": property_id,
            "client_id_set": client_id_set,
            "client_secret_set": client_secret_set,
            "refresh_token_set": bool(refresh_token),
            "last_checked": None,
            "message": (
                "GA4 is not fully configured. "
                "Add GA4_PROPERTY_ID, GA4_CLIENT_ID, GA4_CLIENT_SECRET, and "
                "GA4_REFRESH_TOKEN secrets to enable GA4 connection checks."
            ),
        }

    try:
        import httpx
        async with httpx.AsyncClient(timeout=10.0) as client:
            token_resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                },
            )
            connected = token_resp.is_success
            return {
                "connected": connected,
                "property_id": property_id,
                "client_id_set": True,
                "client_secret_set": True,
                "refresh_token_set": True,
                "last_checked": datetime.now(timezone.utc).isoformat(),
                "message": "Connected" if connected else f"Token refresh failed: {token_resp.status_code}",
            }
    except Exception as e:
        logger.warning(f"GA4 status check failed: {e}")
        return {
            "connected": False,
            "property_id": property_id,
            "client_id_set": client_id_set,
            "client_secret_set": client_secret_set,
            "refresh_token_set": bool(refresh_token),
            "last_checked": datetime.now(timezone.utc).isoformat(),
            "message": f"GA4 connectivity check failed: {e}",
        }


@router.get("/ga4/auth-url")
async def ga4_auth_url(redirect_uri: str):
    """
    Generate GA4 OAuth consent URL.
    The user visits this URL, grants access, and Google redirects with ?code=...
    The code must be exchanged server-side (via the GA4 OAuth callback route).
    """
    cfg = _ga4_config()
    client_id = cfg["client_id"]
    if not client_id:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=400,
            detail="GA4_CLIENT_ID is not configured. Add it as a secret first.",
        )

    import urllib.parse

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "https://www.googleapis.com/auth/analytics.readonly",
        "access_type": "offline",
        "prompt": "consent",
    }
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    return {"auth_url": auth_url, "redirect_uri": redirect_uri}


@router.get("/ga4/test")
async def ga4_test():
    """
    Live GA4 connectivity test — fetches a minimal report from the Data API.
    Returns ok:true with a sample metric on success.
    """
    cfg = _ga4_config()
    property_id = cfg["property_id"]
    client_id = cfg["client_id"]
    client_secret = cfg["client_secret"]
    refresh_token = cfg["refresh_token"]

    if not all([property_id, client_id, client_secret, refresh_token]):
        return {"ok": False, "reason": "GA4 secrets not fully configured"}

    try:
        import httpx

        async with httpx.AsyncClient(timeout=15.0) as client:
            token_resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                },
            )
            if not token_resp.is_success:
                return {"ok": False, "reason": f"Token refresh failed ({token_resp.status_code})"}

            access_token = token_resp.json()["access_token"]

            report_resp = await client.post(
                f"https://analyticsdata.googleapis.com/v1beta/properties/{property_id}:runReport",
                headers={"Authorization": f"Bearer {access_token}"},
                json={
                    "dateRanges": [{"startDate": "7daysAgo", "endDate": "today"}],
                    "metrics": [{"name": "activeUsers"}],
                },
            )
            if not report_resp.is_success:
                return {
                    "ok": False,
                    "reason": f"Data API error ({report_resp.status_code}): {report_resp.text[:200]}",
                }

            report = report_resp.json()
            rows = report.get("rows", [])
            active_users = int(rows[0]["metricValues"][0]["value"]) if rows else 0
            return {
                "ok": True,
                "active_users_7d": active_users,
                "property_id": property_id,
                "tested_at": datetime.now(timezone.utc).isoformat(),
            }

    except Exception as e:
        logger.warning(f"GA4 test failed: {e}")
        return {"ok": False, "reason": str(e)}
