"""``/api/health/otel`` — Task #558.

Returns the OTEL → GCP Cloud Trace exporter health (last successful
export ts, last error, ingestion lag) plus the Sentry errors-only init
snapshot. The admin Observability card on the AdminHealth panel polls
this; the GCP Cloud Trace pipeline is the sole tracing destination so
this endpoint is the single signal for "is tracing reaching its sink?".

Public (no auth) — only reports init/health metadata, no PII or
secrets. Mirrors the existing ``/api/health`` shape so the same
front-end pill can render it without an extra fetch wrapper.
"""
from __future__ import annotations

from fastapi import APIRouter

from tracing import get_otel_health
from observability import get_sentry_health

router = APIRouter()


@router.get("/api/health/otel")
async def health_otel() -> dict:
    return {
        "otel":   get_otel_health(),
        "sentry": get_sentry_health(),
    }
