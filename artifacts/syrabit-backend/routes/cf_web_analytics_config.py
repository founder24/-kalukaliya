"""Task #383 — public ``/api/cf-web-analytics/config`` endpoint.

The frontend ``index.html`` fetches this on boot and, when the flag is
on, appends the Cloudflare Web Analytics beacon ``<script>`` to the
DOM. Keeping the token here (instead of baking it into the build) lets
ops flip ``CF_WEB_ANALYTICS_ON`` and rotate ``CF_WEB_ANALYTICS_TOKEN``
without rebuilding the SPA.

Returns ``{enabled: false, beacon_url: null, token: null}`` when the
flag is off so the runtime injector can fail closed silently.
"""
from __future__ import annotations

from fastapi import APIRouter

from cf_web_analytics import frontend_config

router = APIRouter()


@router.get("/cf-web-analytics/config")
async def cf_web_analytics_config() -> dict:
    return frontend_config()
