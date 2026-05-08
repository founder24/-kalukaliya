"""Task #565 — Daily GCP credit-runway snapshot publisher.

The Task #554 selector in `cost_caps._select_chat_primary` flips the
English chat dispatch chain (Vertex Gemini 2.5 Flash ↔ Workers-AI
Llama-3.2-3B) when the projected GCP credit runway falls to ≤ 90 days.
Today the runway signal is read from `CHAT_CREDIT_RUNWAY_DAYS` (or
computed on the fly from `GCP_CREDITS_REMAINING_USD` + MeterD MTD burn),
but nothing currently writes either value — production is therefore
pinned on the default chain forever, which silently keeps Vertex as the
head even after the GCP startup credits drain (V4 §12 — no silent
fallbacks).

This Lambda fixes that. EventBridge cron `cron(30 3 * * ? *)` (daily
03:30 UTC, after the 03:00 Assamese-translation pass and the 03:15
cache-effectiveness shipper, before the 04:00 weekly Comprehend
sampler):

  1. Cold-start hydrates GOOGLE_APPLICATION_CREDENTIALS_JSON +
     UPSTASH_REDIS_REST_URL/TOKEN + SENTRY_DSN from Secrets Manager via
     `lambda_batch._db.bootstrap_env`.
  2. Queries the GCP Billing BigQuery export
     (`<GCP_BILLING_PROJECT>.<GCP_BILLING_DATASET>.gcp_billing_export_v1_*`)
     for trailing 30-day total cost AND lifetime cumulative cost since
     `GCP_CREDITS_START_DATE`.
  3. Computes:
        remaining_credits = GCP_TOTAL_CREDITS_USD − cumulative_cost
        daily_burn_30d    = total_cost_30d / 30
        runway_days       = round(remaining_credits / daily_burn_30d)
  4. Writes the integer to Upstash Redis at key `chat:credit_runway_days`
     with a 48 h TTL (the backend selector reads it via the existing
     `redis_client` on its 60 s in-process cache; 48 h means a single
     missed Lambda run still surfaces fresh data, two missed runs trip
     the >24 h freshness alarm below).
  5. Publishes the same value to CloudWatch as
     `Syrabit/Cost::ChatCreditRunwayDays` so the alarm
     `chat-credit-runway-stale` (`treat_missing_data=breaching`,
     period=86400) pages on-call when the metric goes silent for >24 h
     and the alarm `chat-credit-runway-low` fires when runway < 60 d
     (early warning for the 90-day flip threshold).
  6. On any exception (BQ query failure, missing config, Redis write
     failure) captures a Sentry message tagged `task=565` so the
     fail-loud contract holds (V4 §12).

Operator overrides (env, no redeploy needed when set on the Lambda):
  * `GCP_TOTAL_CREDITS_USD` — total credit pool size in USD (default
    `0`, in which case the handler skips the compute and Sentry-alerts
    "missing config"). Founder-supplied; updated whenever Google grants
    additional credits.
  * `GCP_CREDITS_START_DATE` — `YYYY-MM-DD` of when the credit pool
    started accumulating burn (default `2025-08-01`).
  * `GCP_BILLING_PROJECT` / `GCP_BILLING_DATASET` /
    `GCP_BILLING_TABLE_PREFIX` — BigQuery export coordinates.
  * `RUNWAY_REDIS_KEY` — Redis key the value is written to (default
    `chat:credit_runway_days`; matches `cost_caps._RUNWAY_REDIS_KEY`).
  * `RUNWAY_REDIS_TTL_S` — Redis TTL (default 172800 = 48 h).

Out of scope (deliberately): the Lambda does NOT mutate ACA env. The
selector reads from Redis on the same `deps.redis_client` used by
MeterD, so no Azure REST call / ACA revision push is required. ACA
restart-free updates were a hard requirement of the task description.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from . import _db

logger = logging.getLogger("lambda_batch.chat_credit_runway")
logger.setLevel(logging.INFO)

NAMESPACE = "Syrabit/Cost"
METRIC_NAME = "ChatCreditRunwayDays"
DEFAULT_REDIS_KEY = "chat:credit_runway_days"
DEFAULT_REDIS_TTL_S = 172_800  # 48 h
DEFAULT_CREDITS_START = "2025-08-01"


# ── Sentry --------------------------------------------------------------------
def _init_sentry() -> Any | None:
    dsn = (os.environ.get("SENTRY_DSN") or "").strip()
    if not dsn:
        return None
    try:
        import sentry_sdk  # type: ignore

        sentry_sdk.init(
            dsn=dsn,
            traces_sample_rate=0.0,  # Task #558 — errors-only Sentry
            release=os.environ.get("LAMBDA_RELEASE", "chat-credit-runway"),
        )
        sentry_sdk.set_tag("task", "565")
        sentry_sdk.set_tag("lambda", "chat-credit-runway")
        return sentry_sdk
    except Exception as exc:  # pragma: no cover - sentry optional
        logger.warning("sentry init failed: %s", exc)
        return None


def _sentry_capture(message: str, level: str = "error", extra: dict | None = None) -> None:
    try:
        import sentry_sdk  # type: ignore
    except Exception:
        return
    try:
        with sentry_sdk.push_scope() as scope:
            scope.set_tag("task", "565")
            for k, v in (extra or {}).items():
                scope.set_extra(k, v)
            sentry_sdk.capture_message(message, level=level)
    except Exception as exc:  # pragma: no cover - never raise from telemetry
        logger.warning("sentry capture failed: %s", exc)


# ── BigQuery billing-export reader -------------------------------------------
def _bq_client():
    """Construct a BigQuery client from the GCP SA JSON in the env.

    The SA JSON is hydrated from Secrets Manager by `_db.bootstrap_env`
    into `GOOGLE_APPLICATION_CREDENTIALS_JSON`. `google-cloud-bigquery`
    is pinned in `artifacts/syrabit-backend/requirements.txt` (Task #565)
    and ships in the shared sqs_consumers Lambda image.
    """
    raw = (os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON") or "").strip()
    if not raw:
        raise RuntimeError("GOOGLE_APPLICATION_CREDENTIALS_JSON missing — cannot query GCP billing export")
    info = json.loads(raw)
    from google.oauth2 import service_account  # type: ignore
    from google.cloud import bigquery  # type: ignore

    credentials = service_account.Credentials.from_service_account_info(
        info,
        scopes=["https://www.googleapis.com/auth/bigquery.readonly"],
    )
    project = (
        os.environ.get("GCP_BILLING_PROJECT")
        or info.get("project_id")
        or ""
    ).strip()
    if not project:
        raise RuntimeError("GCP_BILLING_PROJECT missing and SA JSON has no project_id")
    return bigquery.Client(project=project, credentials=credentials), project


def _query_billing_costs(*, since_iso: str) -> tuple[float, float]:
    """Return (total_cost_30d_usd, cumulative_cost_since_start_usd)."""
    project = os.environ.get("GCP_BILLING_PROJECT", "").strip()
    dataset = os.environ.get("GCP_BILLING_DATASET", "").strip()
    table_prefix = (
        os.environ.get("GCP_BILLING_TABLE_PREFIX") or "gcp_billing_export_v1"
    ).strip()
    if not (project and dataset):
        raise RuntimeError(
            "GCP_BILLING_PROJECT / GCP_BILLING_DATASET must be set to read the billing export"
        )

    client, _proj = _bq_client()
    table_glob = f"`{project}.{dataset}.{table_prefix}_*`"
    sql = f"""
        SELECT
          SUM(IF(usage_start_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY),
                 cost, 0)) AS cost_30d,
          SUM(IF(usage_start_time >= TIMESTAMP(@since), cost, 0)) AS cost_total
        FROM {table_glob}
        WHERE usage_start_time >= TIMESTAMP(@since)
    """
    from google.cloud import bigquery  # type: ignore

    job = client.query(
        sql,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("since", "STRING", since_iso)]
        ),
    )
    row = next(iter(job.result()), None)
    if row is None:
        return 0.0, 0.0
    cost_30d = float(row.get("cost_30d") or 0.0)
    cost_total = float(row.get("cost_total") or 0.0)
    return cost_30d, cost_total


# ── Pure compute (testable without BQ / Redis) -------------------------------
def compute_runway_days(
    *,
    total_credits_usd: float,
    cumulative_cost_usd: float,
    cost_30d_usd: float,
) -> int | None:
    """Return integer runway-days estimate, or None if it cannot be computed.

    Returns 0 (not None) when the credit pool is exhausted so the
    selector flips immediately. Returns None when the trailing burn is
    zero (no traffic yet) — the selector treats that as "no signal" and
    keeps the default chain.
    """
    remaining = float(total_credits_usd) - float(cumulative_cost_usd)
    if remaining <= 0:
        return 0
    daily_burn = float(cost_30d_usd) / 30.0
    if daily_burn <= 0:
        return None
    return max(0, int(round(remaining / daily_burn)))


# ── Upstash Redis (REST API) -------------------------------------------------
def _redis_setex(key: str, ttl_s: int, value: str) -> None:
    url = (os.environ.get("UPSTASH_REDIS_REST_URL") or "").strip()
    token = (os.environ.get("UPSTASH_REDIS_REST_TOKEN") or "").strip()
    if not (url and token):
        raise RuntimeError(
            "UPSTASH_REDIS_REST_URL / UPSTASH_REDIS_REST_TOKEN missing — cannot publish runway"
        )
    import urllib.request as _ur

    req = _ur.Request(
        f"{url.rstrip('/')}/setex/{key}/{int(ttl_s)}/{value}",
        headers={"Authorization": f"Bearer {token}"},
        method="POST",
    )
    with _ur.urlopen(req, timeout=10.0) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Upstash SETEX returned {resp.status}")
        body = resp.read().decode("utf-8")
        try:
            payload = json.loads(body)
        except Exception:
            payload = {}
        if (payload.get("result") or "").upper() != "OK":
            raise RuntimeError(f"Upstash SETEX unexpected payload: {body[:200]}")


# ── CloudWatch ---------------------------------------------------------------
def _cw_put_runway(value: int) -> None:
    import boto3  # type: ignore

    cw = boto3.client("cloudwatch")
    cw.put_metric_data(
        Namespace=NAMESPACE,
        MetricData=[
            {
                "MetricName": METRIC_NAME,
                "Value": float(value),
                "Unit": "Count",
                "Dimensions": [{"Name": "Source", "Value": "lambda"}],
            }
        ],
    )


# ── Handler ------------------------------------------------------------------
def handler(event, context):  # noqa: ARG001
    logger.info("chat_credit_runway invoked: event=%s", json.dumps(event or {})[:200])
    _db.bootstrap_env()
    _init_sentry()

    total_credits = float(os.environ.get("GCP_TOTAL_CREDITS_USD") or 0.0)
    if total_credits <= 0:
        msg = (
            "GCP_TOTAL_CREDITS_USD not configured — cannot compute runway. "
            "Operator must set the credit pool size on the Lambda env."
        )
        logger.error(msg)
        _sentry_capture(msg, level="error", extra={"task": 565})
        return {"ok": False, "error": "missing GCP_TOTAL_CREDITS_USD"}

    since_iso = (os.environ.get("GCP_CREDITS_START_DATE") or DEFAULT_CREDITS_START).strip()
    try:
        cost_30d, cost_total = _query_billing_costs(since_iso=since_iso)
    except Exception as exc:
        logger.exception("BigQuery billing-export query failed")
        _sentry_capture(
            f"chat_credit_runway: BigQuery query failed: {exc}",
            extra={"since": since_iso, "task": 565},
        )
        return {"ok": False, "error": f"bq_query_failed: {exc}"}

    runway = compute_runway_days(
        total_credits_usd=total_credits,
        cumulative_cost_usd=cost_total,
        cost_30d_usd=cost_30d,
    )
    if runway is None:
        msg = (
            "trailing-30d burn is zero — runway cannot be projected. "
            "Skipping publish (selector keeps the default chain)."
        )
        logger.warning(msg)
        _sentry_capture(msg, level="warning", extra={
            "cost_30d": cost_30d, "cost_total": cost_total, "task": 565,
        })
        return {"ok": True, "runway_days": None, "skipped": "no_burn"}

    redis_key = (os.environ.get("RUNWAY_REDIS_KEY") or DEFAULT_REDIS_KEY).strip()
    redis_ttl = int(os.environ.get("RUNWAY_REDIS_TTL_S") or DEFAULT_REDIS_TTL_S)

    try:
        _redis_setex(redis_key, redis_ttl, str(runway))
    except Exception as exc:
        logger.exception("Upstash SETEX failed")
        _sentry_capture(
            f"chat_credit_runway: Upstash publish failed: {exc}",
            extra={"key": redis_key, "ttl_s": redis_ttl, "task": 565},
        )
        return {"ok": False, "error": f"redis_publish_failed: {exc}", "runway_days": runway}

    try:
        _cw_put_runway(runway)
    except Exception as exc:  # CW failure does not invalidate the run
        logger.warning("CloudWatch PutMetricData failed: %s", exc)
        _sentry_capture(
            f"chat_credit_runway: CloudWatch put failed: {exc}",
            level="warning",
            extra={"runway_days": runway, "task": 565},
        )

    summary = {
        "ok": True,
        "runway_days": runway,
        "remaining_usd": round(total_credits - cost_total, 2),
        "cost_30d_usd": round(cost_30d, 2),
        "cost_total_usd": round(cost_total, 2),
        "since": since_iso,
        "redis_key": redis_key,
        "redis_ttl_s": redis_ttl,
        "ts": int(time.time()),
    }
    logger.info("chat_credit_runway summary: %s", json.dumps(summary))
    return summary


# ── Sentry-backed >24h freshness alert (Task #565 acceptance) ───────────────
# The CloudWatch `chat-credit-runway-stale` alarm catches missing-metric on
# the AWS/SNS side, but the task explicitly requires a Sentry alert when the
# value is missing >24h. We can't have the daily publisher detect its own
# absence (if it failed to invoke, no detector either), so we wire a
# *separate* hourly EventBridge cron at `freshness_handler` that reads the
# Redis key directly and `sentry_sdk.capture_message`s the moment the key
# stops being fresh. Hourly cadence × Sentry's first-event dedup means a
# stuck publisher pages on-call within ~1h, well inside the 24h SLO.

DEFAULT_FRESHNESS_THRESHOLD_S = 24 * 3600


def _redis_get(key: str) -> str | None:
    url = (os.environ.get("UPSTASH_REDIS_REST_URL") or "").strip()
    token = (os.environ.get("UPSTASH_REDIS_REST_TOKEN") or "").strip()
    if not (url and token):
        raise RuntimeError("UPSTASH_REDIS_REST_URL / TOKEN missing — cannot read runway")
    import urllib.request as _ur

    req = _ur.Request(
        f"{url.rstrip('/')}/get/{key}",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    with _ur.urlopen(req, timeout=10.0) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Upstash GET returned {resp.status}")
        body = resp.read().decode("utf-8")
    try:
        payload = json.loads(body)
    except Exception:
        return None
    result = payload.get("result")
    return None if result in (None, "") else str(result)


def _redis_pttl(key: str) -> int | None:
    """Returns remaining TTL in milliseconds; -2 = missing, -1 = no TTL."""
    url = (os.environ.get("UPSTASH_REDIS_REST_URL") or "").strip()
    token = (os.environ.get("UPSTASH_REDIS_REST_TOKEN") or "").strip()
    if not (url and token):
        return None
    import urllib.request as _ur

    req = _ur.Request(
        f"{url.rstrip('/')}/pttl/{key}",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    with _ur.urlopen(req, timeout=10.0) as resp:
        if resp.status != 200:
            return None
        body = resp.read().decode("utf-8")
    try:
        return int(json.loads(body).get("result"))
    except Exception:
        return None


def freshness_handler(event, context):  # noqa: ARG001
    """Hourly cron — Sentry-alerts when `chat:credit_runway_days` is missing.

    The publisher stamps a 48h TTL on every successful publish, so the key's
    *remaining* TTL is a direct proxy for how long ago the last publish
    happened: `age = ttl_at_publish − ttl_now`. When `age` exceeds 24h (or
    the key is missing entirely / has no TTL), we capture a Sentry event so
    on-call sees the stale-runway condition without having to watch the
    CloudWatch alarm.
    """
    logger.info("chat_credit_runway.freshness invoked")
    _db.bootstrap_env()
    _init_sentry()

    redis_key = (os.environ.get("RUNWAY_REDIS_KEY") or DEFAULT_REDIS_KEY).strip()
    threshold_s = int(os.environ.get("RUNWAY_FRESHNESS_THRESHOLD_S")
                      or DEFAULT_FRESHNESS_THRESHOLD_S)
    publish_ttl_s = int(os.environ.get("RUNWAY_REDIS_TTL_S") or DEFAULT_REDIS_TTL_S)

    try:
        value = _redis_get(redis_key)
        pttl_ms = _redis_pttl(redis_key)
    except Exception as exc:
        logger.exception("freshness probe: Upstash read failed")
        _sentry_capture(
            f"chat_credit_runway.freshness: Upstash read failed: {exc}",
            extra={"key": redis_key, "task": 565},
        )
        return {"ok": False, "error": f"redis_read_failed: {exc}"}

    if value is None or pttl_ms is None or pttl_ms < 0:
        _sentry_capture(
            "chat_credit_runway.freshness: Redis key missing — daily publisher "
            "has not run successfully. Chain-flip selector is operating on "
            "the env-derived fallback only.",
            extra={"key": redis_key, "pttl_ms": pttl_ms, "task": 565},
        )
        return {"ok": False, "stale": True, "reason": "missing"}

    age_s = max(0, publish_ttl_s - int(pttl_ms / 1000))
    if age_s > threshold_s:
        _sentry_capture(
            f"chat_credit_runway.freshness: Redis runway value is "
            f"{age_s}s old (>{threshold_s}s). Daily publisher likely failed.",
            extra={
                "key": redis_key,
                "value": value,
                "age_s": age_s,
                "threshold_s": threshold_s,
                "task": 565,
            },
        )
        return {"ok": False, "stale": True, "age_s": age_s, "value": value}

    return {"ok": True, "stale": False, "age_s": age_s, "value": value}
