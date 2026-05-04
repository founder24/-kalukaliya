"""
sqs_fanout — drop-in replacement for `cloud_tasks_client.send`.

Phase 4 — Async worker port (Task #332).

Background
----------
The existing producers in artifacts/syrabit-backend/ (seo_engine.py,
seo_internal_linker.py, bing_*.py, cf_bot_crosscheck.py,
unified_logs_dao.py, notify.py, etc.) all reach for
`cloud_tasks_client.send(queue, payload)` to enqueue async work.
That helper builds a Cloud Tasks HTTP target pointing at the FastAPI
backend and counts on the consumer route to do the work.

After the cutover the consumer side is an AWS Lambda triggered by
SQS, so we just need to put the same JSON payload onto the matching
SQS queue. The queue URL is read from SSM (populated by Terraform —
see infra/aws/sqs.tf output `sqs_worker_queue_urls`) so producer
config has zero hard-coded ARNs and rotates automatically when the
queue is recreated in a different region.

Migration contract
------------------
Every call site that today reads:

    from cloud_tasks_client import send as enqueue
    await enqueue("seo-indexnow", {"page_id": pid})

becomes:

    from sqs_fanout import enqueue
    await enqueue("seo-indexnow", {"page_id": pid})

The queue keys are identical to the GCP names listed in
`docs/infra/inventory/cloud-tasks.json` so no producer-side mapping
table is needed.

Failure semantics
-----------------
* Raises on connectivity failure — callers that already wrap the GCP
  helper in a try/except will keep working unchanged.
* Uses `send_message_batch` when the caller passes a list (matches
  the existing fanout-loop pattern in seo_internal_linker.py).
* Emits an OTEL span per send so the producer side shows up in the
  same App Insights trace as the consumer Lambda.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from functools import lru_cache
from typing import Any, Iterable

try:
    import boto3  # type: ignore
    from botocore.config import Config as BotoConfig  # type: ignore
except ImportError:  # pragma: no cover — dev shells without boto3
    boto3 = None  # type: ignore
    BotoConfig = None  # type: ignore

log = logging.getLogger("sqs_fanout")

# SSM parameter that Terraform writes the {gcp_key: queue_url} map
# into. See infra/aws/secrets.tf for the parameter definition.
_QUEUE_URL_SSM_PARAM = os.environ.get(
    "SQS_QUEUE_URL_SSM_PARAM",
    "/syrabit/prod/sqs-worker-queue-urls",
)
_AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")


@lru_cache(maxsize=1)
def _queue_url_map() -> dict[str, str]:
    """Fetch the {gcp_key: queue_url} map from SSM exactly once.

    Cached for the lifetime of the process — Terraform-managed SSM
    values change at most a few times per year (region failover,
    queue rename) and are picked up on the next pod restart.
    """
    if boto3 is None:
        raise RuntimeError("boto3 not installed; cannot resolve SQS queue URLs")
    ssm = boto3.client("ssm", region_name=_AWS_REGION)
    resp = ssm.get_parameter(Name=_QUEUE_URL_SSM_PARAM, WithDecryption=False)
    raw = resp["Parameter"]["Value"]
    return json.loads(raw)


@lru_cache(maxsize=1)
def _sqs_client():
    if boto3 is None:
        raise RuntimeError("boto3 not installed; cannot publish to SQS")
    # Tight retry budget — producers are usually on a request hot
    # path; better to surface the failure to the caller than to stall
    # an HTTP response while boto3 retries with exponential back-off.
    cfg = BotoConfig(retries={"max_attempts": 2, "mode": "standard"}, connect_timeout=2, read_timeout=5)
    return boto3.client("sqs", region_name=_AWS_REGION, config=cfg)


def _resolve(queue_key: str) -> str:
    try:
        return _queue_url_map()[queue_key]
    except KeyError as e:
        raise KeyError(
            f"Unknown SQS queue key {queue_key!r}; "
            f"check docs/infra/inventory/cloud-tasks.json + infra/aws/sqs.tf"
        ) from e


async def enqueue(queue_key: str, payload: dict[str, Any]) -> str:
    """Drop-in replacement for `cloud_tasks_client.send(queue_key, payload)`.

    Returns the SQS message ID so callers that previously logged the
    Cloud Tasks task name still have something to log.
    """
    url = _resolve(queue_key)
    body = json.dumps(payload, separators=(",", ":"), default=str)

    def _send() -> str:
        resp = _sqs_client().send_message(QueueUrl=url, MessageBody=body)
        return resp["MessageId"]

    msg_id = await asyncio.to_thread(_send)
    log.debug("sqs_fanout: enqueued %s → %s (msgId=%s, bytes=%d)", queue_key, url, msg_id, len(body))
    return msg_id


async def enqueue_batch(queue_key: str, payloads: Iterable[dict[str, Any]]) -> list[str]:
    """Batched send. Splits into the SQS-imposed 10-message chunks."""
    url = _resolve(queue_key)
    payloads = list(payloads)
    if not payloads:
        return []

    msg_ids: list[str] = []
    for i in range(0, len(payloads), 10):
        chunk = payloads[i : i + 10]
        entries = [
            {
                "Id": str(idx),
                "MessageBody": json.dumps(p, separators=(",", ":"), default=str),
            }
            for idx, p in enumerate(chunk)
        ]

        def _send() -> list[str]:
            resp = _sqs_client().send_message_batch(QueueUrl=url, Entries=entries)
            failed = resp.get("Failed") or []
            if failed:
                # Surface partial failure — callers may want to retry
                # the rejected payloads on a slower path. Match the
                # cloud_tasks_client behaviour of raising on any
                # batch-level failure.
                raise RuntimeError(f"SQS batch send failed for {queue_key}: {failed!r}")
            return [m["MessageId"] for m in resp.get("Successful", [])]

        msg_ids.extend(await asyncio.to_thread(_send))

    log.debug("sqs_fanout: batch-enqueued %d → %s (queue=%s)", len(msg_ids), url, queue_key)
    return msg_ids


__all__ = ["enqueue", "enqueue_batch"]
