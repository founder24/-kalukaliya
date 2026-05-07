"""Task #575 — `/api/health/season` season-aware cache TTL endpoint.

Public (un-authenticated) endpoint so the Cloudflare edge proxy can
poll it from a Worker Durable Object on a 60 s cadence and apply the
per-route ``exam_ttl_seconds`` overrides from
``workers/edge-proxy/monitored-urls.json`` when the calendar reports
exam / results mode. The same payload is consumed by the admin
Observability cache banner.

The response carries no PII or operational secrets — it's a pure
function of the YAML calendar checked into the repo. We mark it
``Cache-Control: public, max-age=60`` so a misbehaving Worker can't
stampede the FastAPI origin even if the in-DO cache is bypassed.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Response

import cache_calendar

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/health/season")
async def health_season(response: Response) -> dict:
    payload = cache_calendar.health_payload()
    # 60 s aligns with the worker-side in-DO refresh cadence — see
    # `workers/edge-proxy/src/season-cache.ts`. Anything shorter would
    # let a hot Worker hit the origin once per request; anything
    # longer delays the exam-window flip past the per-pop cache fill.
    response.headers["Cache-Control"] = "public, max-age=60, s-maxage=60"
    response.headers["X-Source"] = "cache-calendar"
    return payload
