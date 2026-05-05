"""Task #404 — public ``/api/turnstile/config`` endpoint.

The React auth/signup/password-reset forms (``artifacts/syrabit/src``)
fetch this on first render and, when the flag is on, mount the
Cloudflare Turnstile widget using the returned ``site_key``. Keeping
the site key here (instead of baking it into the build) lets ops flip
``TURNSTILE_ON`` and rotate ``TURNSTILE_SITE_KEY`` without rebuilding
the SPA — the same pattern used by ``/api/cf-web-analytics/config``.

Returns ``{enabled: false, site_key: null}`` when the flag is off so
the runtime widget can fail closed silently — it renders nothing and
the form submits without a token, which the dormant
``require_turnstile`` dependency lets through.
"""
from __future__ import annotations

from fastapi import APIRouter

from turnstile import frontend_config

router = APIRouter()


@router.get("/turnstile/config")
async def turnstile_config() -> dict:
    return frontend_config()
