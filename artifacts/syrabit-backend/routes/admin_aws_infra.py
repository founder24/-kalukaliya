"""Admin proxy for the AWS SQS + Lambda worker tier (Task #332).

The React `AdminAwsInfraCard` polls `GET /admin/aws/workers/health`;
this module is its server-side implementation. We keep AWS
credentials server-side (read from the runtime managed-identity
chain — `boto3` resolves them automatically in the DO API container
which gets a federated AWS role via the GitHub OIDC config in
`infra/aws/iam-github-oidc.tf`) so the bundle never holds an AWS
key.

Endpoints
---------
GET /admin/aws/workers/health
    Composite + per-queue snapshot. Aggregates:

      • CloudWatch metrics:
          - AWS/SQS  ApproximateNumberOfMessagesVisible (queue + DLQ)
          - AWS/Lambda Invocations + Errors (per consumer)
      • CloudWatch DescribeAlarms for the per-queue alarms named in
        `infra/aws/sqs-alarms.tf` (composite name
        `syrabit-workers-degraded`).

POST /admin/aws/workers/{queue_key}/replay-dlq
    Calls the SQS DLQ-redrive API for the named queue. Idempotent —
    SQS skips messages already in-flight on the source queue.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

try:
    import boto3  # type: ignore
    from botocore.config import Config as BotoConfig  # type: ignore
except ImportError:  # pragma: no cover — dev shells without boto3
    boto3 = None  # type: ignore
    BotoConfig = None  # type: ignore

from auth_deps import get_admin_user as require_admin

logger = logging.getLogger(__name__)
router = APIRouter()

# Mirror of `local.sqs_worker_queues` from infra/aws/sqs.tf. Kept
# here so the route does not need an extra round-trip to Terraform
# state on every render. Any drift is caught by the dispatch CI test
# (services/cron-jobs/tests/test_dispatch_imports.py) — same pattern.
# Queue + consumer names MUST match infra/aws/sqs.tf and
# infra/aws/lambda-workers.tf exactly:
#   • queue name    = local.sqs_worker_queues[<key>].aws
#   • consumer name = "${local.lz_project}-${<key>}-consumer"
#                     (i.e. "syrabit-${each.key}-consumer")
# A mismatch silently makes CloudWatch GetMetricData target
# nonexistent function names, so the admin card error rates are
# wrong without any visible failure. CI guard:
# tests/test_admin_aws_infra_naming.py compares this map against
# the TF source.
_QUEUE_INVENTORY: dict[str, dict[str, Any]] = {
    "seo-indexnow":            {"queue": "syrabit-seo-indexnow",         "consumer": "syrabit-seo-indexnow-consumer",            "backlog_threshold": 500},
    "seo-internal-linker":     {"queue": "syrabit-seo-internal-linker",  "consumer": "syrabit-seo-internal-linker-consumer",     "backlog_threshold": 200},
    "discovery-engine-ingest": {"queue": "syrabit-discovery-ingest",     "consumer": "syrabit-discovery-engine-ingest-consumer", "backlog_threshold": 200},
    "bing-keyword-refresh":    {"queue": "syrabit-bing-keyword",         "consumer": "syrabit-bing-keyword-refresh-consumer",    "backlog_threshold": 50},
    "bing-submit":             {"queue": "syrabit-bing-submit",          "consumer": "syrabit-bing-submit-consumer",             "backlog_threshold": 200},
    "cf-bot-crosscheck":       {"queue": "syrabit-cf-bot-crosscheck",    "consumer": "syrabit-cf-bot-crosscheck-consumer",       "backlog_threshold": 100},
    "unified-logs-cf-pull":    {"queue": "syrabit-unified-logs-pull",    "consumer": "syrabit-unified-logs-cf-pull-consumer",    "backlog_threshold": 50},
    # `email-fallback` REUSES the existing `aws_lambda_function.email_worker`
    # (function_name = "syrabit-email-worker") rather than getting its
    # own per-key consumer Lambda. See infra/aws/lambda-workers.tf §
    # "Email-fallback wiring (reuses existing email-worker Lambda)"
    # and the special-case in test_admin_aws_infra_naming.py.
    "email-fallback":          {"queue": "syrabit-email-fallback",       "consumer": "syrabit-email-worker",                     "backlog_threshold": 100},
}
_AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")
_COMPOSITE_ALARM = "syrabit-workers-degraded"


def _aws_clients():
    if boto3 is None:
        raise HTTPException(status_code=503, detail="boto3 not installed in runtime")
    cfg = BotoConfig(retries={"max_attempts": 2, "mode": "standard"}, connect_timeout=2, read_timeout=5)
    cw = boto3.client("cloudwatch", region_name=_AWS_REGION, config=cfg)
    sqs = boto3.client("sqs", region_name=_AWS_REGION, config=cfg)
    return cw, sqs


def _composite_from(queues: list[dict[str, Any]], composite_state: str | None) -> str:
    """Map per-queue + composite alarm into the 4-state UI bucket."""
    if composite_state == "ALARM":
        return "failed"
    if any(q.get("dlqDepth", 0) > 0 for q in queues):
        return "failed"
    if any(q.get("alarmState") == "ALARM" for q in queues):
        return "degraded"
    if any(q.get("backlog", 0) > q.get("backlogThreshold", 0) * 0.5 for q in queues):
        return "degraded"
    if not queues:
        return "unknown"
    return "ok"


def _gather(cw, sqs) -> dict[str, Any]:
    """One blocking pass over CloudWatch + SQS APIs. Called via `asyncio.to_thread`."""
    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=5)

    queues_out: list[dict[str, Any]] = []
    for key, info in _QUEUE_INVENTORY.items():
        q_url = sqs.get_queue_url(QueueName=info["queue"])["QueueUrl"]
        attrs = sqs.get_queue_attributes(QueueUrl=q_url, AttributeNames=["ApproximateNumberOfMessages"])
        backlog = int(attrs["Attributes"].get("ApproximateNumberOfMessages", "0"))
        try:
            dlq_url = sqs.get_queue_url(QueueName=f"{info['queue']}-dlq")["QueueUrl"]
            dlq_attrs = sqs.get_queue_attributes(QueueUrl=dlq_url, AttributeNames=["ApproximateNumberOfMessages"])
            dlq_depth = int(dlq_attrs["Attributes"].get("ApproximateNumberOfMessages", "0"))
        except Exception:
            dlq_depth = 0

        # Lambda error rate over the last 5 min.
        err_resp = cw.get_metric_statistics(
            Namespace="AWS/Lambda",
            MetricName="Errors",
            Dimensions=[{"Name": "FunctionName", "Value": info["consumer"]}],
            StartTime=start, EndTime=end,
            Period=300, Statistics=["Sum"],
        )
        inv_resp = cw.get_metric_statistics(
            Namespace="AWS/Lambda",
            MetricName="Invocations",
            Dimensions=[{"Name": "FunctionName", "Value": info["consumer"]}],
            StartTime=start, EndTime=end,
            Period=300, Statistics=["Sum"],
        )
        errs = sum(d["Sum"] for d in err_resp.get("Datapoints", []))
        invs = sum(d["Sum"] for d in inv_resp.get("Datapoints", []))
        err_rate = (errs / invs) if invs > 0 else 0.0

        # Per-queue alarm state (backlog alarm carries the same name
        # convention as in sqs-alarms.tf).
        try:
            alarm_resp = cw.describe_alarms(AlarmNames=[f"{info['queue']}-backlog", f"{info['queue']}-dlq-not-empty"])
            states = {a["AlarmName"]: a["StateValue"] for a in alarm_resp.get("MetricAlarms", [])}
            # Worst-of for the row.
            order = ["ALARM", "INSUFFICIENT_DATA", "OK"]
            row_state = sorted(states.values(), key=lambda s: order.index(s) if s in order else 99)[0] if states else "INSUFFICIENT_DATA"
        except Exception:
            row_state = "INSUFFICIENT_DATA"

        queues_out.append({
            "key":               key,
            "queueName":         info["queue"],
            "dlqName":           f"{info['queue']}-dlq",
            "backlog":           backlog,
            "dlqDepth":          dlq_depth,
            "consumerName":      info["consumer"],
            "consumerErrorRate": err_rate,
            "alarmState":        row_state,
            "backlogThreshold":  info["backlog_threshold"],
        })

    composite_state = None
    try:
        comp = cw.describe_alarms(AlarmNames=[_COMPOSITE_ALARM], AlarmTypes=["CompositeAlarm"])
        if comp.get("CompositeAlarms"):
            composite_state = comp["CompositeAlarms"][0]["StateValue"]
    except Exception:
        composite_state = None

    return {
        "asOf":             end.isoformat(),
        "composite":        _composite_from(queues_out, composite_state),
        "queues":           queues_out,
        "compositeAlarmArn": None,  # reserved — populated when ARN export is wired
    }


@router.get("/admin/aws/workers/health")
async def workers_health(_admin: dict = Depends(require_admin)) -> dict[str, Any]:
    cw, sqs = _aws_clients()
    try:
        return await asyncio.to_thread(_gather, cw, sqs)
    except Exception as e:
        logger.exception("workers_health failed")
        raise HTTPException(status_code=502, detail=f"AWS probe failed: {type(e).__name__}: {e}")


@router.post("/admin/aws/workers/{queue_key}/replay-dlq")
async def replay_dlq(queue_key: str, _admin: dict = Depends(require_admin)) -> dict[str, Any]:
    info = _QUEUE_INVENTORY.get(queue_key)
    if not info:
        raise HTTPException(status_code=404, detail=f"unknown queue_key {queue_key!r}")
    if boto3 is None:
        raise HTTPException(status_code=503, detail="boto3 not installed in runtime")
    sqs = boto3.client("sqs", region_name=_AWS_REGION)

    def _start() -> dict[str, Any]:
        dlq_url = sqs.get_queue_url(QueueName=f"{info['queue']}-dlq")["QueueUrl"]
        dlq_arn = sqs.get_queue_attributes(QueueUrl=dlq_url, AttributeNames=["QueueArn"])["Attributes"]["QueueArn"]
        return sqs.start_message_move_task(SourceArn=dlq_arn)

    try:
        resp = await asyncio.to_thread(_start)
    except Exception as e:
        logger.exception("replay_dlq failed for %s", queue_key)
        raise HTTPException(status_code=502, detail=f"DLQ redrive failed: {type(e).__name__}: {e}")

    return {"status": "ok", "queue_key": queue_key, "task_handle": resp.get("TaskHandle"), "started_at": int(time.time())}
