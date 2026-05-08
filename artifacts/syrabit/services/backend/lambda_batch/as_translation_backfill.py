"""Task #551 §B / Task #516 — Lambda handler for `syrabit-as-translation-backfill`.

Daily 03:00 UTC EventBridge cron. Wraps `aca_jobs.as_translation_backfill.run_backfill`
with a Lambda-friendly handler signature so we can reuse the existing
resumable IndicTrans2 → Vertex polish driver verbatim — this is the
scheduled runner that keeps the `/as/...` corpus current as new
`subjects` / `chapters` / `seo_pages` / `pyq_html_pages` documents are
created by editors and the AI pipeline.

The function image bundles the FastAPI backend code (same image used by
`lambda-workers.tf`), so the import below resolves to the in-tree
`artifacts/syrabit-backend/aca_jobs/` package.

Observability (Task #516)
-------------------------
After each pass the handler emits per-collection CloudWatch metrics
(`Translated`, `Failed`, `Skipped`, `Remaining`, `Processed`) plus an
aggregate `RemainingTotal` to the `Syrabit/BatchJobs` namespace under
the dimension `Job=as-translation-backfill`. The matching alarms in
`infra/aws/lambda-batch-jobs.tf` page on-call via the `ops_alerts` SNS
topic when the leftover-doc count refuses to drain (a "stuck" pass) or
when the `Failed` counter spikes — Lambda's built-in Errors alarm only
catches hard crashes, not a clean run that produces zero translations.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from . import _db

logger = logging.getLogger("lambda_batch.as_translation_backfill")
logger.setLevel(logging.INFO)

MAX_DOCS_PER_RUN = int(os.environ.get("MAX_DOCS_PER_RUN", "1000"))

METRIC_NAMESPACE = os.environ.get(
    "AS_BACKFILL_METRIC_NAMESPACE", "Syrabit/BatchJobs"
)
JOB_DIMENSION_VALUE = os.environ.get(
    "AS_BACKFILL_METRIC_JOB", "as-translation-backfill"
)


async def _run() -> dict:
    _db.bootstrap_env()
    # Task #560 — tag every Mongo state write made by this pass as
    # `driver=lambda` so `scripts/lambda_aca_shadow_reconcile.py` can
    # split per-driver outcomes during the 7-day shadow window.
    os.environ.setdefault("BATCH_JOB_DRIVER", "lambda")
    from aca_jobs.as_translation_backfill import run_backfill  # type: ignore
    db = _db.get_db()
    summary = await run_backfill(
        db,
        max_docs=MAX_DOCS_PER_RUN,
        batch_size=int(os.environ.get("AS_BACKFILL_BATCH_SIZE", "5")),
    )
    return summary


def _build_metric_data(summary: dict) -> list[dict[str, Any]]:
    """Translate the run_backfill summary into a CloudWatch PutMetricData payload.

    One MetricDatum per (collection, metric) pair — keeps the per-collection
    leftover count visible on its own line in the dashboard so a single
    sticky collection (e.g. `seo_pages`) doesn't get masked by zero
    remaining on the others. A `RemainingTotal` rollup metric (no
    `Collection` dimension) drives the stuck-pass alarm.
    """
    results = (summary or {}).get("results") or []
    data: list[dict[str, Any]] = []
    rollups = {"translated": 0, "failed": 0, "skipped": 0, "remaining": 0, "processed": 0}
    for r in results:
        collection = str(r.get("collection") or "unknown")
        for metric in ("translated", "failed", "skipped", "remaining", "processed"):
            try:
                value = float(r.get(metric, 0) or 0)
            except (TypeError, ValueError):
                value = 0.0
            data.append({
                "MetricName": metric.capitalize(),
                "Dimensions": [
                    {"Name": "Job", "Value": JOB_DIMENSION_VALUE},
                    {"Name": "Collection", "Value": collection},
                ],
                "Unit": "Count",
                "Value": value,
            })
            try:
                rollups[metric] += int(r.get(metric, 0) or 0)
            except (TypeError, ValueError):
                pass
    # Job-only rollups (no Collection dimension) — these are what the
    # CloudWatch alarms in `infra/aws/lambda-batch-jobs.tf` evaluate.
    # CloudWatch treats (MetricName, full Dimension set) as the metric
    # identity, so an alarm scoped to `Job` alone will NOT see the
    # per-collection series above; the rollup is required.
    for metric_name, total in (
        ("RemainingTotal",  rollups["remaining"]),
        ("FailedTotal",     rollups["failed"]),
        ("TranslatedTotal", rollups["translated"]),
        ("ProcessedTotal",  rollups["processed"]),
    ):
        data.append({
            "MetricName": metric_name,
            "Dimensions": [{"Name": "Job", "Value": JOB_DIMENSION_VALUE}],
            "Unit": "Count",
            "Value": float(total),
        })
    return data


def _emit_metrics(summary: dict) -> None:
    """Best-effort PutMetricData. Never raises — a metrics outage must
    not mask the actual translation work that already succeeded."""
    try:
        data = _build_metric_data(summary)
        if not data:
            return
        import boto3  # type: ignore
        cw = boto3.client("cloudwatch")
        # CloudWatch caps PutMetricData at 1000 datums per call; we'll
        # never approach that (5 metrics × 4 collections + 1 rollup = 21)
        # but chunk defensively in case FIELD_MAP grows.
        for i in range(0, len(data), 20):
            cw.put_metric_data(
                Namespace=METRIC_NAMESPACE,
                MetricData=data[i:i + 20],
            )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("as_translation_backfill metric emit failed: %s", exc)


def handler(event, context):  # noqa: ARG001
    """EventBridge invocation entry point."""
    logger.info("as_translation_backfill invoked: event=%s", json.dumps(event)[:300])
    try:
        summary = asyncio.run(_run())
    except Exception as exc:
        logger.exception("as_translation_backfill failed: %s", exc)
        raise
    logger.info("as_translation_backfill summary: %s", json.dumps(summary, default=str)[:600])
    _emit_metrics(summary)
    return {"ok": True, "summary": summary}
