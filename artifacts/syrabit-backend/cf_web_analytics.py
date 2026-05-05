"""Task #383 — Cloudflare Web Analytics (RUM) integration helpers.

CF Web Analytics is the privacy-friendly, no-cookie pageview /
performance product. It runs entirely client-side: the operator drops
Cloudflare's beacon snippet into the page ``<head>`` and CF aggregates
RUM events into a dashboard + a GraphQL API.

This module owns the two server-side touchpoints that the rest of the
codebase needs:

  1. **Beacon snippet** — `beacon_snippet_html()` renders the official
     ``<script defer src="…/beacon.min.js" data-cf-beacon='{"token":…}'>``
     line for the SSR shell or any HTML response. Returns ``""`` when
     the flag is off so SEO crawlers see a clean page even mid-rollout.
  2. **Config endpoint helper** — `frontend_config()` returns the
     subset of CF Web Analytics settings the React app needs to render
     the same beacon dynamically.

There's also a best-effort `fetch_recent_pageviews()` against the CF
GraphQL Analytics API so the admin ``/admin/cf-health`` panel can
show "pageviews in the last hour" without operators leaving the
dashboard. It returns ``None`` when not configured / network fails so
the admin route can render a clean placeholder instead of erroring.
"""
from __future__ import annotations

import logging
import os
from html import escape
from typing import Any, Optional

from config import CF_WEB_ANALYTICS_ON, CF_WEB_ANALYTICS_TOKEN

logger = logging.getLogger(__name__)

_BEACON_URL = "https://static.cloudflareinsights.com/beacon.min.js"
_GRAPHQL_URL = "https://api.cloudflare.com/client/v4/graphql"
_GRAPHQL_TIMEOUT_S = 5.0


def is_enabled() -> bool:
    return bool(CF_WEB_ANALYTICS_ON and CF_WEB_ANALYTICS_TOKEN)


def beacon_snippet_html() -> str:
    """Return the inline HTML to add to a page ``<head>``. Empty when
    the flag is off so we don't ship a half-configured beacon."""
    if not is_enabled():
        return ""
    # The token is ASCII alphanumeric — escape anyway so a future
    # operator pasting a stray quote into the env can't break the page.
    token = escape(CF_WEB_ANALYTICS_TOKEN, quote=True)
    return (
        f'<script defer src="{_BEACON_URL}" '
        f'data-cf-beacon=\'{{"token":"{token}"}}\'></script>'
    )


def frontend_config() -> dict[str, Any]:
    """Tiny JSON the SPA reads on bootstrap. Hides the token when
    the flag is off so we don't leak the analytics namespace early."""
    return {
        "enabled": is_enabled(),
        "beacon_url": _BEACON_URL if is_enabled() else None,
        "token": CF_WEB_ANALYTICS_TOKEN if is_enabled() else None,
    }


async def fetch_recent_pageviews(
    *, hours: int = 1,
    http_client_factory=None,
) -> Optional[dict[str, Any]]:
    """Best-effort GraphQL query for total pageviews over the last
    ``hours``. Returns ``None`` when the flag is off, the API token
    isn't set, or the request fails — *never* raises. The admin route
    treats ``None`` as "show a setup hint".

    Requires a Cloudflare API token with the ``Account Analytics:Read``
    permission, set via ``CF_ANALYTICS_API_TOKEN``. The Web Analytics
    site tag (``CF_WEB_ANALYTICS_SITE_TAG``) is the GraphQL filter
    target — distinct from the *beacon* token above, which is public.
    """
    if not is_enabled():
        return None
    api_token = (os.environ.get("CF_ANALYTICS_API_TOKEN") or "").strip()
    site_tag = (os.environ.get("CF_WEB_ANALYTICS_SITE_TAG") or "").strip()
    if not api_token or not site_tag:
        return None

    from datetime import datetime, timedelta, timezone
    end = datetime.now(timezone.utc).replace(microsecond=0)
    start = end - timedelta(hours=max(1, int(hours)))
    query = """
    query Pageviews($siteTag: String!, $start: Time!, $end: Time!) {
      viewer {
        accounts {
          rumPageloadEventsAdaptiveGroups(
            limit: 1
            filter: { siteTag: $siteTag, datetime_geq: $start, datetime_leq: $end }
          ) { count }
        }
      }
    }
    """
    payload = {
        "query": query,
        "variables": {
            "siteTag": site_tag,
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
        },
    }
    try:
        if http_client_factory is None:
            import httpx
            http_client_factory = httpx.AsyncClient
        async with http_client_factory(timeout=_GRAPHQL_TIMEOUT_S) as client:
            resp = await client.post(_GRAPHQL_URL, json=payload, headers={
                "Authorization": f"Bearer {api_token}",
                "Content-Type": "application/json",
            })
        if resp.status_code != 200:
            return None
        body = resp.json()
        accounts = (body.get("data") or {}).get("viewer", {}).get("accounts", [])
        if not accounts:
            return {"hours": hours, "pageviews": 0, "source": "cf_web_analytics"}
        groups = accounts[0].get("rumPageloadEventsAdaptiveGroups") or []
        total = sum(int(g.get("count") or 0) for g in groups)
        return {
            "hours": hours,
            "pageviews": total,
            "source": "cf_web_analytics",
            "from": payload["variables"]["start"],
            "to": payload["variables"]["end"],
        }
    except Exception as exc:
        logger.warning("[cf-web-analytics] graphql fetch failed: %s", exc)
        return None


__all__ = ["is_enabled", "beacon_snippet_html", "frontend_config",
           "fetch_recent_pageviews"]
