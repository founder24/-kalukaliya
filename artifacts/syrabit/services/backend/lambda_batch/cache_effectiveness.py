"""Task #571 — nightly shipper for cache-effectiveness telemetry.

EventBridge cron `cron(15 3 * * ? *)` (daily 03:15 UTC, after the
Assamese translation backfill at 03:00 has finished and before any
human admin shows up). The handler:

  1. Mints a short-lived admin JWT from `ADMIN_JWT_SECRET` (sourced
     from Secrets Manager).
  2. Calls `GET https://<backend>/api/health/cache` and parses the
     `ai_input_cache` block.
  3. Publishes the per-content-type counters to the `Syrabit/Cache`
     CloudWatch namespace as PutMetricData with dimensions
     `(ContentType=<ct>)` and the totals as `(ContentType=Total)`.

The CloudWatch alarms (`infra/aws/lambda-batch-jobs.tf`):

  * `cache-ai-hitratio-low`  — Total HitRatio < 0.30 for 1 day.
  * `cache-cardinality-spike` — Total UniqueKeys24h > 3× the trailing
    7-day moving average; uses CW Metric Math.

Both fire to the existing `ops_alerts` SNS topic.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger("lambda_batch.cache_effectiveness")
logger.setLevel(logging.INFO)

NAMESPACE = "Syrabit/Cache"
BACKEND_URL = os.environ.get(
    "BACKEND_URL",
    "https://syrabit-backend.lemonstone-ce3c87e1.eastus.azurecontainerapps.io",
)
ADMIN_JWT_SECRET_ARN = os.environ.get("ADMIN_JWT_SECRET_ARN", "")


def _load_admin_jwt_secret() -> str:
    direct = os.environ.get("ADMIN_JWT_SECRET", "").strip()
    if direct:
        return direct
    if not ADMIN_JWT_SECRET_ARN:
        raise RuntimeError("ADMIN_JWT_SECRET / ADMIN_JWT_SECRET_ARN not set")
    import boto3  # type: ignore
    sm = boto3.client("secretsmanager")
    raw = (sm.get_secret_value(SecretId=ADMIN_JWT_SECRET_ARN).get("SecretString") or "").strip()
    if raw.startswith("{"):
        return json.loads(raw).get("secret", raw)
    return raw


def _mint_admin_jwt() -> str:
    import jwt  # type: ignore
    secret = _load_admin_jwt_secret()
    payload = {
        "sub": "lambda-cache-effectiveness",
        "role": "admin",
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def _fetch_snapshot() -> dict[str, Any]:
    import urllib.request as _ur
    token = _mint_admin_jwt()
    req = _ur.Request(
        f"{BACKEND_URL.rstrip('/')}/api/health/cache",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    with _ur.urlopen(req, timeout=10.0) as resp:
        if resp.status != 200:
            raise RuntimeError(f"cache health returned {resp.status}")
        return json.loads(resp.read().decode("utf-8"))


def _emit(cw, dims_ct: str, *, hits: int, misses: int, sets: int,
          hit_ratio: float, unique_keys: int, miss_reasons: dict[str, int]) -> None:
    """Publish one ContentType row to CloudWatch."""
    base = [{"Name": "ContentType", "Value": dims_ct}]
    metrics = [
        {"MetricName": "Hits",          "Value": float(hits),          "Unit": "Count",   "Dimensions": base},
        {"MetricName": "Misses",        "Value": float(misses),        "Unit": "Count",   "Dimensions": base},
        {"MetricName": "Sets",          "Value": float(sets),          "Unit": "Count",   "Dimensions": base},
        {"MetricName": "HitRatio",      "Value": float(hit_ratio),     "Unit": "None",    "Dimensions": base},
        {"MetricName": "UniqueKeys24h", "Value": float(unique_keys),   "Unit": "Count",   "Dimensions": base},
    ]
    for reason, n in (miss_reasons or {}).items():
        metrics.append({
            "MetricName": "MissReason",
            "Value": float(n),
            "Unit": "Count",
            "Dimensions": base + [{"Name": "Reason", "Value": reason}],
        })
    # CloudWatch caps PutMetricData at 1000 metrics / call — we are well under.
    cw.put_metric_data(Namespace=NAMESPACE, MetricData=metrics)


def handler(event, context):  # noqa: ARG001
    logger.info("cache_effectiveness invoked: event=%s", json.dumps(event)[:300])
    snapshot = _fetch_snapshot()
    aic = snapshot.get("ai_input_cache") or {}
    totals = aic.get("totals") or {}
    cts = aic.get("content_types") or {}

    import boto3  # type: ignore
    cw = boto3.client("cloudwatch")
    _emit(
        cw, "Total",
        hits=int(totals.get("hits", 0)),
        misses=int(totals.get("misses", 0)),
        sets=int(totals.get("sets", 0)),
        hit_ratio=float(totals.get("hit_ratio", 0.0)),
        unique_keys=int(totals.get("unique_keys_24h", 0)),
        miss_reasons={},  # totals row carries no reason breakdown
    )
    for ct, row in cts.items():
        _emit(
            cw, ct,
            hits=int(row.get("hits", 0)),
            misses=int(row.get("misses", 0)),
            sets=int(row.get("sets", 0)),
            hit_ratio=float(row.get("hit_ratio", 0.0)),
            unique_keys=int(row.get("unique_keys_24h", 0)),
            miss_reasons=row.get("miss_reasons") or {},
        )
    summary = {
        "totals": totals,
        "content_types_emitted": list(cts.keys()),
    }
    logger.info("cache_effectiveness summary: %s", json.dumps(summary, default=str)[:600])
    return {"ok": True, "summary": summary}
