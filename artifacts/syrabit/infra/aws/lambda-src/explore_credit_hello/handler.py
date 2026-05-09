"""syrabit-explore-credit-hello — Task #4 §2 (AWS Explore credit claim).

Minimal Lambda used solely to satisfy the "Build a serverless app"
activity in the AWS Explore promo. Has a public Function URL so the
console activity check sees a live HTTP endpoint, but it is NOT wired
into any production code path (verified by grep against
`workers/edge-proxy/src/index.ts` and `routes/`).

Returns a deterministic JSON payload so a smoke probe can confirm the
function is alive after the credit-claim flow completes. Do not extend
this handler — if a real serverless workload is needed, add a separate
function that goes through the canonical-delegation review.
"""
from __future__ import annotations

import json


def handler(event, context):  # noqa: ARG001 — Lambda contract
    return {
        "statusCode": 200,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(
            {
                "ok": True,
                "service": "syrabit",
                "purpose": "explore-credit-hello",
                "wired_into_production": False,
            }
        ),
    }
