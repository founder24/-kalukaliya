"""aca_jobs.seo_baseline — Task #28.

Weekly Lambda-eligible job that runs ``scripts/seo_baseline.py``
against ``PUBLIC_BASE_URL``, persists the full report to
``db.seo_baseline_runs`` (the per-week history the admin tile reads),
computes the week-over-week delta against the previous run, and emits
two ``Syrabit/SEO`` CloudWatch metrics so the alarms in
``artifacts/syrabit/infra/aws/lambda-batch-jobs.tf`` can fire when the
median Lighthouse SEO score drops more than 5 points WoW or when more
than 2 sampled pages carry per-leg failures.

Schedule: ``cron(0 2 ? * MON *)`` (every Monday 02:00 UTC). The cadence
is weekly so the trend line is comparable to Search Console's own
weekly aggregation, and 02:00 UTC sits inside the daily cache-prewarm
+ FAQ-materialize window so the pages we measure are already warm.

Why the report is stored in Mongo (not the repo)
------------------------------------------------
The Task #28 brief asks for "archive the previous JSON to
``docs/seo/history/baseline-YYYY-MM-DD.json``". A Lambda cannot commit
to the git repo, so we treat ``db.seo_baseline_runs`` AS the history
(one doc per Monday, keyed by ``report_date``). The admin route
``/api/admin/seo/baseline-latest`` reads the latest doc + the prior
doc so the dashboard can render WoW deltas. ``scripts/seo_baseline.py``
is unchanged and still writes ``docs/seo/baseline-2026-Q2.json`` when
an engineer runs it locally — that file remains the canonical
"checked-in" baseline for offline diffing.

V4 §12 — *no silent fallbacks*: any Lighthouse / structured-data /
Rich-Results failure surfaces inside the per-page ``failures[]`` list
and bumps ``pages_with_failures``; a hard exception during
``run_baseline`` propagates so the Lambda exits non-zero and the
``treat_missing_data=breaching`` alarm fires.

Lambda handler lives at
``artifacts/syrabit/services/backend/lambda_batch/seo_baseline.py``.
The matching ``infra/aws/lambda/manifest.json`` row + Terraform
schedule are mandatory — ``scripts/check_dead_providers.py`` walks
``aca_jobs/*.py`` and CI-fails when a module here has no Lambda
counterpart.
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("aca_jobs.seo_baseline")

# Importing the canonical script: it lives under the repo-root
# ``scripts/`` directory. The Lambda image bundles ``scripts/`` at
# ``/var/task/scripts/`` (see lambda_batch/seo_baseline.py for the
# sys.path bootstrap). Local pytest exercises this same import path.
_SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


DEFAULT_BOARDS = ("ahsec", "ncert", "scert", "seba")
DEFAULT_CHAPTERS_PER_BOARD = 5
DEFAULT_PAGE_TYPE = "notes"


def _to_dict_safe(report: Any) -> Dict[str, Any]:
    """Convert a ``BaselineReport`` dataclass to a plain dict.

    Importing ``_to_dict`` from the script directly would pull the
    ``argparse``/CLI surface; instead we mirror the contract here so
    a future script-side rename does not silently break the Lambda.
    """
    from dataclasses import asdict

    return {
        "generated_at_utc": report.generated_at_utc,
        "public_base_url": report.public_base_url,
        "sampled_pages": report.sampled_pages,
        "summary": report.summary,
        "pages": [asdict(p) for p in report.pages],
    }


def _publish_metrics(summary: Dict[str, Any], wow_delta: Optional[float]) -> None:
    """Emit Syrabit/SEO CloudWatch datapoints for the weekly alarms.

    Two metrics:
      * ``MedianSeoScore`` — the absolute median Lighthouse SEO score
        for this run, so the dashboard can graph the trend.
      * ``PagesWithFailures`` — number of sampled pages whose
        ``failures[]`` is non-empty (Lighthouse timeout, schema parse
        error, Rich Results 5xx). Alarm fires at > 2.
      * ``MedianSeoScoreWoWDelta`` — current minus previous run's
        median SEO score. Alarm fires at < -5 (a >5pt regression).

    Boto3 is only imported here so unit tests that exercise the
    persistence/delta path do not require the AWS SDK.
    """
    namespace = "Syrabit/SEO"
    try:
        import boto3  # type: ignore
    except Exception as exc:  # pragma: no cover — dev/test path
        logger.warning("seo_baseline: boto3 unavailable, skipping CW publish (%s)", exc)
        return

    median = summary.get("median_seo_score")
    pages_failed = int(summary.get("pages_with_failures") or 0)

    metrics: list[dict[str, Any]] = []
    if median is not None:
        metrics.append({
            "MetricName": "MedianSeoScore",
            "Value": float(median),
            "Unit": "None",
        })
    metrics.append({
        "MetricName": "PagesWithFailures",
        "Value": float(pages_failed),
        "Unit": "Count",
    })
    if wow_delta is not None:
        metrics.append({
            "MetricName": "MedianSeoScoreWoWDelta",
            "Value": float(wow_delta),
            "Unit": "None",
        })

    try:
        cw = boto3.client("cloudwatch")
        cw.put_metric_data(Namespace=namespace, MetricData=metrics)
        logger.info("seo_baseline: published %d CloudWatch metrics to %s",
                    len(metrics), namespace)
    except Exception as exc:
        # V4 §12 — log loud but do not raise; the persisted doc is
        # the authoritative trail. The treat_missing_data=breaching
        # alarm catches a publish gap on its own.
        logger.exception("seo_baseline: CloudWatch publish failed: %s", exc)


async def run_baseline_publish(
    db: Any,
    *,
    base_url: Optional[str] = None,
    boards: tuple[str, ...] = DEFAULT_BOARDS,
    chapters_per_board: int = DEFAULT_CHAPTERS_PER_BOARD,
    page_type: str = DEFAULT_PAGE_TYPE,
    rich_results_key: Optional[str] = None,
    runner: Any = None,
) -> Dict[str, Any]:
    """Run one weekly baseline pass and persist the result.

    ``runner`` is injectable so unit tests can supply a stub that
    returns a fabricated ``BaselineReport`` without invoking
    Lighthouse. Production callers leave it ``None`` and we resolve
    ``scripts.seo_baseline.run_baseline`` lazily.

    Returns the persisted summary doc (without ``_id``) for the
    Lambda handler to log and for tests to assert against.
    """
    started_at = datetime.now(tz=timezone.utc)
    base_url = (base_url or os.environ.get("PUBLIC_BASE_URL") or "https://syrabit.ai").rstrip("/")
    rich_key = rich_results_key or os.environ.get("GOOGLE_RR_API_KEY")

    if runner is None:
        import seo_baseline as _script  # type: ignore — see sys.path bootstrap above
        runner = _script.run_baseline

    report = runner(
        base_url=base_url,
        boards=boards,
        chapters_per_board=chapters_per_board,
        page_type=page_type,
        rich_results_key=rich_key,
    )
    finished_at = datetime.now(tz=timezone.utc)
    payload = _to_dict_safe(report)
    summary = payload.get("summary") or {}

    # ── Pull the prior run's summary so we can compute WoW deltas
    # before persisting. The query is keyed on ``started_at`` so
    # backfilled / out-of-order docs do not poison the comparison.
    prior_summary: Optional[Dict[str, Any]] = None
    prior_started_at: Optional[datetime] = None
    try:
        prior_doc = await db.seo_baseline_runs.find_one(
            {}, sort=[("started_at", -1)],
        )
        if prior_doc:
            prior_summary = prior_doc.get("summary") or {}
            prior_started_at = prior_doc.get("started_at")
    except Exception as exc:
        logger.warning("seo_baseline: prior-run lookup failed (%s)", exc)

    wow_delta: Optional[float] = None
    if prior_summary and prior_summary.get("median_seo_score") is not None \
            and summary.get("median_seo_score") is not None:
        try:
            wow_delta = float(summary["median_seo_score"]) - float(prior_summary["median_seo_score"])
        except (TypeError, ValueError):
            wow_delta = None

    report_date = started_at.date().isoformat()
    doc = {
        "report_date":         report_date,
        "started_at":          started_at,
        "finished_at":         finished_at,
        "duration_s":          (finished_at - started_at).total_seconds(),
        "public_base_url":     base_url,
        "sampled_pages":       payload.get("sampled_pages", 0),
        "summary":             summary,
        "pages":               payload.get("pages", []),
        "wow_delta_seo_score": wow_delta,
        "prior_started_at":    prior_started_at,
        "task":                28,
    }

    try:
        # Idempotent upsert by report_date so a manual re-run on the
        # same Monday updates the row instead of duplicating it.
        await db.seo_baseline_runs.update_one(
            {"report_date": report_date},
            {"$set": doc},
            upsert=True,
        )
    except Exception as exc:
        # V4 §12 — fail loud. A persistence failure means the admin
        # tile + WoW alarm would silently regress.
        logger.exception("seo_baseline: persistence failed: %s", exc)
        raise

    _publish_metrics(summary, wow_delta)

    # Return shape the Lambda handler logs / tests assert on.
    return {
        "report_date":         report_date,
        "sampled_pages":       doc["sampled_pages"],
        "median_seo_score":    summary.get("median_seo_score"),
        "pages_with_failures": int(summary.get("pages_with_failures") or 0),
        "wow_delta_seo_score": wow_delta,
        "duration_s":          doc["duration_s"],
    }
