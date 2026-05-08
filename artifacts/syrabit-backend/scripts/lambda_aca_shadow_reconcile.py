"""Task #560 — Lambda ↔ ACA shadow-mode reconciliation.

During the 7-day shadow window declared in ``infra/aws/lambda/manifest.json``
(``shadow_start`` … ``shadow_end``) every batch job runs **twice**:

  • the existing in-process loop inside the FastAPI backend
    (``server.py:_start_aca_jobs``) — historical driver, still authoritative.
  • the new EventBridge-scheduled Lambda
    (``infra/aws/lambda-batch-jobs.tf``) — proposed driver.

This script runs once per day (GH Actions schedule below) and does
**per-document parity** — not coarse aggregate counts. For every
migrated job we compute, over the trailing ``RECONCILE_LOOKBACK_HOURS``
window:

  • ``aca_keys``     — set of document IDs touched by ``BATCH_JOB_DRIVER=aca``.
  • ``lambda_keys``  — set of document IDs touched by ``BATCH_JOB_DRIVER=lambda``.
  • ``jaccard``      — |∩| / |∪|; 1.0 means both drivers picked the
    exact same work-set (the strong cutover signal).
  • ``hash_match_rate`` — for the IDs in the intersection, the fraction
    where the two drivers produced the SAME stable output fingerprint
    (translation `_as_src_hash`, sentiment label, embedding marker).
    A driver that scribbles different output for the same input is the
    failure mode that aggregate counts would miss.
  • ``match_rate = min(jaccard, hash_match_rate)`` — the gate metric.

When ``match_rate`` ≥ ``MATCH_RATE_TARGET`` (default 0.99) for 7
consecutive days, ops flips ``ACA_JOB_BATCHES_DISABLED=1`` on the ACA
env (see Bicep) and updates ``infra/aws/lambda/manifest.json``:
``cutover_status: live``. Rollback = unset the env var; nothing in
this script makes a destructive change.

Driver discriminators (Task #560 round-3, per-doc)
--------------------------------------------------
The job writers stamp every Mongo write with the active
``BATCH_JOB_DRIVER`` env value (Lambda wrapper sets ``lambda``;
ACA loop defaults to ``aca``):

  • as_translation_backfill: per-doc field ``<field>_as_translated_by``
    (alongside the existing ``_as_src_hash`` + ``_as_translated_at``).
    Reconciler keys on the ``title`` field across migrated collections.
  • embed_backfill: per-chunk field ``embedded_by`` next to
    ``embedded_at`` + ``embedding_source``. Reconciler keys on chunk
    ``_id`` and uses ``embedding_source`` as the per-doc fingerprint
    (constant string, but its presence proves the marker round-trip).
  • comprehend_sampler: ``content_analytics`` rows already carry
    ``scored_by`` (driver) + ``sentiment`` (label) + ``chapter_id``
    (key). Reconciler keys on ``chapter_id`` and uses ``sentiment`` as
    the per-doc fingerprint.

Shadow-window gating
--------------------
The script is a no-op (exit 0) when no migrated job is in its
``shadow_start..shadow_end`` window AND ``cutover_status`` is still
``pending_shadow``. ``--force`` bypasses the gate for ad-hoc runs.

Exit codes
----------
  • 0 — every in-window job's match-rate ≥ ``MATCH_RATE_TARGET``
    (or no jobs in window).
  • 2 — at least one in-window job is below target. The GH Actions
    wrapper marks the run as failed.
  • 1 — script-level failure (Mongo unreachable, malformed manifest, …).

Required env
------------
  MONGO_URL, AWS_REGION, SLACK_ONCALL_WEBHOOK_URL (optional),
  MATCH_RATE_TARGET (default 0.99), RECONCILE_LOOKBACK_HOURS (24),
  RECONCILE_DRY_RUN (1 → skip Slack + CloudWatch writes),
  RECONCILE_INTERSECT_SAMPLE_LIMIT (default 500 — cap the per-job
  intersection scan so we don't OOM on a multi-million-doc collection).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, asdict, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib import error as urlerr
from urllib import request as urlreq

logger = logging.getLogger("lambda_aca_shadow_reconcile")
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MANIFEST = REPO_ROOT / "infra" / "aws" / "lambda" / "manifest.json"

MATCH_RATE_TARGET = float(os.environ.get("MATCH_RATE_TARGET", "0.99"))
LOOKBACK_HOURS = int(os.environ.get("RECONCILE_LOOKBACK_HOURS", "24"))
DRY_RUN = os.environ.get("RECONCILE_DRY_RUN", "").strip() == "1"
CW_NAMESPACE = os.environ.get("RECONCILE_METRIC_NAMESPACE", "Syrabit/BatchJobs")
INTERSECT_SAMPLE_LIMIT = int(os.environ.get("RECONCILE_INTERSECT_SAMPLE_LIMIT", "500"))

# Per-collection probe field for the per-doc reconciliation. Mirrors
# the first entry of `aca_jobs.as_translation_backfill.FIELD_MAP` for
# each collection — it's the field the writer is guaranteed to touch
# whenever the doc qualifies for translation. (Subjects don't have a
# `title` field; their first translatable field is `name`.)
TRANSLATION_PROBE_FIELDS: dict[str, str] = {
    "subjects":       "name",
    "chapters":       "title",
    "seo_pages":      "title",
    "pyq_html_pages": "title",
}


# ── Data class ──────────────────────────────────────────────────────────────
@dataclass
class JobReconciliation:
    job: str
    aca_count: int
    lambda_count: int
    intersect_count: int
    jaccard: float
    hash_match_count: int
    hash_compare_count: int
    hash_match_rate: float
    match_rate: float
    detail: str
    breakdown: dict = field(default_factory=dict)

    @property
    def passes(self) -> bool:
        return self.match_rate >= MATCH_RATE_TARGET


# ── Helpers ─────────────────────────────────────────────────────────────────
def _utc_window() -> tuple[datetime, datetime]:
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=LOOKBACK_HOURS)
    return start, end


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    return round(len(a & b) / len(union), 4) if union else 1.0


def _summarize(
    job: str,
    aca_keys: set,
    lambda_keys: set,
    aca_fp: dict,
    lambda_fp: dict,
    detail: str,
    breakdown: Optional[dict] = None,
) -> JobReconciliation:
    """Common assembler for the three per-doc reconciliations.

    ``aca_fp`` / ``lambda_fp`` map doc-key → stable output fingerprint
    (translation src_hash / sentiment label / embedding marker). For
    every key in the intersection we compare the two fingerprints; a
    mismatch is the divergence the cutover gate must catch."""
    intersect = aca_keys & lambda_keys
    hash_compare = 0
    hash_match = 0
    for k in intersect:
        a = aca_fp.get(k)
        b = lambda_fp.get(k)
        if a is None or b is None:
            continue
        hash_compare += 1
        if a == b:
            hash_match += 1
    # When the intersection is empty the hash check has no opinion;
    # treat it as 1.0 so `match_rate` collapses to `jaccard`. When
    # both keysets are also empty (idle window), `_jaccard` returns
    # 1.0 and the job trivially passes — that is the correct signal
    # because neither driver was supposed to do work.
    hash_rate = round(hash_match / hash_compare, 4) if hash_compare else 1.0
    jaccard = _jaccard(aca_keys, lambda_keys)
    match_rate = round(min(jaccard, hash_rate), 4)
    return JobReconciliation(
        job=job,
        aca_count=len(aca_keys),
        lambda_count=len(lambda_keys),
        intersect_count=len(intersect),
        jaccard=jaccard,
        hash_match_count=hash_match,
        hash_compare_count=hash_compare,
        hash_match_rate=hash_rate,
        match_rate=match_rate,
        detail=detail,
        breakdown=breakdown or {},
    )


def _load_manifest(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"manifest not found: {path}")
    return json.loads(path.read_text())


def _migrated_job_keys(manifest: dict) -> list[str]:
    out: list[str] = []
    for entry in manifest.get("migrated_jobs", []):
        fn = str(entry.get("lambda_function_name", "") or "")
        if fn.startswith("syrabit-"):
            out.append(fn[len("syrabit-"):])
    return out


def _job_in_shadow_window(manifest: dict, job_key: str, today: date) -> bool:
    fn_name = f"syrabit-{job_key}"
    for entry in manifest.get("migrated_jobs", []):
        if str(entry.get("lambda_function_name", "")) != fn_name:
            continue
        if str(entry.get("cutover_status", "")).lower() != "pending_shadow":
            return False
        try:
            start = date.fromisoformat(str(entry["shadow_start"]))
            end = date.fromisoformat(str(entry["shadow_end"]))
        except (KeyError, ValueError):
            return False
        return start <= today <= end
    return False


# ── Per-document reconciliations ────────────────────────────────────────────
async def _reconcile_as_translation(db: Any) -> JobReconciliation:
    """Per-doc parity on the `title` field across every migrated
    collection. Keys = ``f"{collection}:{_id}"``; fingerprint =
    ``title_as_src_hash`` (the SHA-1 of the source string the driver
    actually translated — equal hashes mean both drivers translated
    the same input)."""
    start, _end = _utc_window()
    start_iso = start.isoformat() + "Z"
    aca_keys: set[str] = set()
    lambda_keys: set[str] = set()
    aca_fp: dict[str, str] = {}
    lambda_fp: dict[str, str] = {}
    breakdown: dict[str, dict] = {}
    for cname, probe in TRANSLATION_PROBE_FIELDS.items():
        coll = db[cname]
        per_aca = 0
        per_lam = 0
        at_field = f"{probe}_as_translated_at"
        by_field = f"{probe}_as_translated_by"
        hash_field = f"{probe}_as_src_hash"
        cursor = coll.find(
            {at_field: {"$gte": start_iso}},
            {"_id": 1, hash_field: 1, by_field: 1},
        ).limit(INTERSECT_SAMPLE_LIMIT)
        async for doc in cursor:
            key = f"{cname}:{doc['_id']}"
            driver = str(doc.get(by_field) or "aca").lower()
            fp = str(doc.get(hash_field) or "")
            if driver == "lambda":
                lambda_keys.add(key)
                lambda_fp[key] = fp
                per_lam += 1
            else:
                aca_keys.add(key)
                aca_fp[key] = fp
                per_aca += 1
        breakdown[cname] = {"aca": per_aca, "lambda": per_lam}
    detail = (
        f"per-coll: " + ", ".join(
            f"{c}=aca:{breakdown[c]['aca']}/lam:{breakdown[c]['lambda']}"
            for c in TRANSLATION_PROBE_FIELDS
        )
    )
    return _summarize(
        job="as-translation-backfill",
        aca_keys=aca_keys,
        lambda_keys=lambda_keys,
        aca_fp=aca_fp,
        lambda_fp=lambda_fp,
        detail=detail,
        breakdown=breakdown,
    )


async def _reconcile_embed_backfill(db: Any) -> JobReconciliation:
    """Per-chunk parity on ``chunks.embedded_at`` >= window_start.
    Keys = chunk ``_id``; fingerprint = ``embedding_source`` +
    ``embedding_dim`` concatenated (constant in production, but a
    drift in either field is still a real divergence we want to
    catch)."""
    start, _end = _utc_window()
    coll = db["chunks"]
    aca_keys: set[str] = set()
    lambda_keys: set[str] = set()
    aca_fp: dict[str, str] = {}
    lambda_fp: dict[str, str] = {}
    cursor = coll.find(
        {"embedded_at": {"$gte": start}},
        {
            "_id":              1,
            "embedded_by":      1,
            "embedding_source": 1,
            "embedding_dim":    1,
        },
    ).limit(INTERSECT_SAMPLE_LIMIT)
    async for doc in cursor:
        key = str(doc["_id"])
        driver = str(doc.get("embedded_by") or "aca").lower()
        fp = f"{doc.get('embedding_source')}|{doc.get('embedding_dim')}"
        if driver == "lambda":
            lambda_keys.add(key)
            lambda_fp[key] = fp
        else:
            aca_keys.add(key)
            aca_fp[key] = fp
    detail = (
        f"chunks window={LOOKBACK_HOURS}h "
        f"aca={len(aca_keys)} lambda={len(lambda_keys)} "
        f"sample_cap={INTERSECT_SAMPLE_LIMIT}"
    )
    return _summarize(
        job="embed-backfill",
        aca_keys=aca_keys,
        lambda_keys=lambda_keys,
        aca_fp=aca_fp,
        lambda_fp=lambda_fp,
        detail=detail,
    )


async def _reconcile_comprehend(db: Any) -> JobReconciliation:
    """Per-row parity on ``content_analytics.scored_at`` >= window_start.
    Keys = ``chapter_id``; fingerprint = ``sentiment`` label string.
    A driver that flips a sentiment for the same chapter is the
    real failure mode here."""
    start, _end = _utc_window()
    start_iso = start.isoformat()
    coll = db["content_analytics"]
    aca_keys: set[str] = set()
    lambda_keys: set[str] = set()
    aca_fp: dict[str, str] = {}
    lambda_fp: dict[str, str] = {}
    cursor = coll.find(
        {"scored_at": {"$gte": start_iso}},
        {"chapter_id": 1, "sentiment": 1, "scored_by": 1},
    ).limit(INTERSECT_SAMPLE_LIMIT)
    async for doc in cursor:
        cid = doc.get("chapter_id")
        if cid is None:
            continue
        key = str(cid)
        # Legacy rows (pre-Task #560) have no `scored_by` and are
        # bucketed conservatively as `aca` so a Lambda-only pass cannot
        # piggyback on them through the gate.
        driver = str(doc.get("scored_by") or "aca").lower()
        fp = str(doc.get("sentiment") or "")
        if driver == "lambda":
            lambda_keys.add(key)
            lambda_fp[key] = fp
        else:
            aca_keys.add(key)
            aca_fp[key] = fp
    detail = (
        f"content_analytics window={LOOKBACK_HOURS}h "
        f"aca={len(aca_keys)} lambda={len(lambda_keys)} "
        f"sample_cap={INTERSECT_SAMPLE_LIMIT}"
    )
    return _summarize(
        job="comprehend-sampler",
        aca_keys=aca_keys,
        lambda_keys=lambda_keys,
        aca_fp=aca_fp,
        lambda_fp=lambda_fp,
        detail=detail,
    )


# ── CloudWatch + Slack ──────────────────────────────────────────────────────
def _emit_match_rate(results: Iterable[JobReconciliation]) -> None:
    if DRY_RUN:
        logger.info("DRY_RUN=1 — skipping CloudWatch PutMetricData")
        return
    try:
        import boto3  # type: ignore
        cw = boto3.client("cloudwatch", region_name=os.environ.get("AWS_REGION") or None)
        data: list[dict] = []
        for r in results:
            for metric, value in (
                ("ShadowMatchRate",   r.match_rate),
                ("ShadowJaccard",     r.jaccard),
                ("ShadowHashMatch",   r.hash_match_rate),
            ):
                data.append({
                    "MetricName": metric,
                    "Dimensions": [{"Name": "Job", "Value": r.job}],
                    "Unit":       "None",
                    "Value":      float(value),
                })
        if data:
            cw.put_metric_data(Namespace=CW_NAMESPACE, MetricData=data)
            logger.info("PutMetricData(%s) %d datums", CW_NAMESPACE, len(data))
    except Exception as exc:
        logger.warning("PutMetricData failed: %s", exc)


def _slack_payload(results: list[JobReconciliation]) -> dict:
    overall_pass = all(r.passes for r in results)
    icon = ":white_check_mark:" if overall_pass else ":rotating_light:"
    title = (
        f"{icon} Lambda↔ACA shadow reconciliation "
        f"({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')})"
    )
    rows = []
    for r in results:
        marker = ":white_check_mark:" if r.passes else ":x:"
        rows.append(
            f"{marker} *{r.job}*  match={r.match_rate:.2%}  "
            f"jaccard={r.jaccard:.2%} hash={r.hash_match_count}/{r.hash_compare_count}\n"
            f"      aca_keys={r.aca_count} lambda_keys={r.lambda_count} "
            f"intersect={r.intersect_count}\n"
            f"      _{r.detail}_"
        )
    body = "\n".join(rows) if rows else "_no jobs to reconcile (all out of shadow window)_"
    target = (
        f"\nTarget = {MATCH_RATE_TARGET:.0%} match-rate "
        "(min(jaccard, hash_match)). ≥99% for 7 consecutive days → "
        "set `ACA_JOB_BATCHES_DISABLED=1` in Bicep + flip "
        "`cutover_status: live` in `infra/aws/lambda/manifest.json`."
    )
    return {"text": f"{title}\n{body}{target}"}


def _post_slack(payload: dict) -> None:
    url = os.environ.get("SLACK_ONCALL_WEBHOOK_URL", "").strip()
    if DRY_RUN or not url:
        logger.info("Slack POST skipped (DRY_RUN=%s, url_set=%s)", DRY_RUN, bool(url))
        logger.info("Slack payload: %s", json.dumps(payload)[:1500])
        return
    data = json.dumps(payload).encode("utf-8")
    req = urlreq.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urlreq.urlopen(req, timeout=10) as resp:
            if resp.status >= 300:
                logger.warning("Slack POST returned HTTP %s", resp.status)
    except urlerr.URLError as exc:
        logger.warning("Slack POST failed: %s", exc)


# ── Driver ──────────────────────────────────────────────────────────────────
async def _run(force: bool = False) -> int:
    from motor.motor_asyncio import AsyncIOMotorClient  # type: ignore

    uri = os.environ.get("MONGO_URL", "").strip()
    if not uri:
        logger.error("MONGO_URL is not set")
        return 1
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=10_000)
    db = client[os.environ.get("MONGO_DB_NAME", "syrabit")]

    manifest = _load_manifest(Path(os.environ.get("MANIFEST_PATH") or DEFAULT_MANIFEST))
    job_keys = _migrated_job_keys(manifest)
    today = datetime.now(timezone.utc).date()

    in_window = [k for k in job_keys if force or _job_in_shadow_window(manifest, k, today)]
    if not in_window:
        logger.info(
            "No migrated jobs are currently in their shadow window "
            "(today=%s, jobs=%s) — exiting clean.", today, job_keys,
        )
        _post_slack({
            "text": (
                f":information_source: Lambda↔ACA reconciliation skipped "
                f"({today.isoformat()}): no jobs in shadow window. "
                "Flip `cutover_status: live` in `infra/aws/lambda/manifest.json` "
                "once the operational cutover is signed off."
            )
        })
        return 0

    logger.info("In-window jobs: %s (skipped: %s)",
                in_window, sorted(set(job_keys) - set(in_window)))

    results: list[JobReconciliation] = []
    if "as-translation-backfill" in in_window:
        results.append(await _reconcile_as_translation(db))
    if "embed-backfill" in in_window:
        results.append(await _reconcile_embed_backfill(db))
    if "comprehend-sampler" in in_window:
        results.append(await _reconcile_comprehend(db))

    for r in results:
        logger.info("RESULT %s", json.dumps(asdict(r), default=str))

    _emit_match_rate(results)
    _post_slack(_slack_payload(results))

    failed = [r for r in results if not r.passes]
    if failed:
        logger.error(
            "%d job(s) below match-rate target %.2f: %s",
            len(failed), MATCH_RATE_TARGET, [r.job for r in failed],
        )
        return 2
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Skip Slack + CloudWatch writes; log the payload only.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Bypass the manifest shadow-window gate (run every migrated job).",
    )
    args = parser.parse_args()
    if args.dry_run:
        os.environ["RECONCILE_DRY_RUN"] = "1"
        global DRY_RUN
        DRY_RUN = True

    import asyncio
    return asyncio.run(_run(force=args.force))


if __name__ == "__main__":
    sys.exit(main())
