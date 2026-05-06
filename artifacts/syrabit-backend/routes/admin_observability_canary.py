"""routes.admin_observability_canary — Task #489 V4 §7 canary endpoint.

The CI workflow `.github/workflows/cross-cloud-trace-canary.yml`
posts a known `traceparent` header here every 6 h. The handler:

  1. Records the current trace context as a Sentry transaction so the
     ACA-side span lands under the test trace-id.
  2. Optionally fans out into the AWS event backbone via
     `sqs_fanout.enqueue` for each requested queue key — that drops a
     downstream Lambda span under the same trace-id (the in-image
     ADOT collector picks the W3C traceparent off the SQS message
     attributes).

Sentry's `events-trace` API then sees both spans under one trace and
the canary asserts cross-cloud propagation.

Authentication: admin-gated via `get_admin_user`. Never accept a
canary request without admin auth — minting fake spans would skew the
SLO calculations downstream.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, Request

from auth_deps import get_admin_user

logger = logging.getLogger(__name__)
router = APIRouter(tags=["admin-observability"])

_ALLOWED_FANOUT_KEYS = {"reembed", "s3_to_r2_sync"}


@router.post(
    "/admin/observability/trace-canary",
    summary="V4 §7 cross-cloud trace canary (Task #489)",
)
async def trace_canary(
    request: Request,
    payload: dict[str, Any] | None = None,
    _admin: dict = Depends(get_admin_user),
) -> dict[str, Any]:
    traceparent = request.headers.get("traceparent", "")
    fanout_keys: list[str] = []
    if isinstance(payload, dict):
        raw = payload.get("fanout") or []
        if isinstance(raw, list):
            fanout_keys = [k for k in raw if isinstance(k, str) and k in _ALLOWED_FANOUT_KEYS]

    enqueue_results: list[dict[str, Any]] = []
    for queue_key in fanout_keys:
        try:
            from sqs_fanout import enqueue as _sqs_enqueue
            msg_id = await _sqs_enqueue(
                queue_key,
                {
                    "_canary": True,
                    "traceparent": traceparent,
                    "queue_key": queue_key,
                    "service_namespace": os.environ.get("OTEL_SERVICE_NAMESPACE", "syrabit"),
                },
            )
            enqueue_results.append({"queue_key": queue_key, "ok": True, "msg_id": msg_id})
        except Exception as exc:
            logger.exception("trace-canary: enqueue %s failed: %s", queue_key, exc)
            enqueue_results.append({"queue_key": queue_key, "ok": False, "error": str(exc)})

    return {
        "ok": True,
        "traceparent": traceparent,
        "fanout": enqueue_results,
        "service_namespace": os.environ.get("OTEL_SERVICE_NAMESPACE", "syrabit"),
    }
