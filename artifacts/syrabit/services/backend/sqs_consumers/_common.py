"""Shared SQS-handler helpers (Task #332).

`run_batch()` is the only public surface — every per-queue module
loads its async per-message coroutine and forwards to it. Centralising
the batch-failure protocol here keeps the handlers themselves a
one-line dispatch.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

log = logging.getLogger("sqs_consumer")


def run_batch(event: dict[str, Any], handler: Callable[[dict[str, Any]], Awaitable[None]]) -> dict[str, Any]:
    """Run ``handler(body)`` for every Record in an SQS Lambda event.

    Returns the ReportBatchItemFailures-formatted response so SQS
    redelivers only the messages whose handler raised.
    """
    failures: list[dict[str, str]] = []
    records = event.get("Records") or []
    log.info("sqs batch received: %d messages", len(records))

    async def _drive() -> None:
        for record in records:
            mid = record.get("messageId", "?")
            raw = record.get("body") or "{}"
            try:
                body = json.loads(raw)
            except json.JSONDecodeError as e:
                log.error("messageId=%s — body is not JSON: %s", mid, e)
                failures.append({"itemIdentifier": mid})
                continue
            # Task #332 — `_smoke` short-circuit. The
            # `services/cron-jobs/scripts/smoke_sqs_lambda.sh` smoke
            # script sends `{"_smoke": true, ...}` envelopes to every
            # queue to verify the SQS → Lambda → log path end-to-end
            # without invoking the real backend handler (which would
            # otherwise reject the synthetic payload and pile up in
            # the DLQ). The smoke envelope is acked as success and a
            # marker line is logged so the smoke script's CloudWatch
            # tail can grep for it.
            if isinstance(body, dict) and body.get("_smoke") is True:
                log.info("messageId=%s — SMOKE-OK (synthetic _smoke envelope ack'd)", mid)
                continue
            try:
                await handler(body)
            except Exception:  # noqa: BLE001 — top-level guard
                log.exception("messageId=%s — handler raised; will be retried by SQS", mid)
                failures.append({"itemIdentifier": mid})

    asyncio.run(_drive())
    return {"batchItemFailures": failures}
