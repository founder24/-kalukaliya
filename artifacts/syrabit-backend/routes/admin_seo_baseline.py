"""Task #28 — admin tile for the weekly SEO baseline.

Single endpoint:

  GET /api/admin/seo/baseline-latest

Returns the most recent ``aca_jobs.seo_baseline.run_baseline_publish``
run summary the admin dashboard renders into the
``/admin/seo/baseline-latest`` tile:

  {
    "report_date":         "2026-05-11",
    "started_at":          "2026-05-11T02:00:00+00:00",
    "duration_s":          312.4,
    "public_base_url":     "https://syrabit.ai",
    "sampled_pages":       20,
    "summary":             { ...full summary block from seo_baseline.py... },
    "wow_delta_seo_score": -3.0,
    "prior": {
      "report_date":      "2026-05-04",
      "median_seo_score": 91
    },
    "samples_failed": [{"url": "...", "failures": [...]}, ...]
  }

Reads ``db.seo_baseline_runs`` (most-recent doc by ``started_at``).
When the collection is empty the response carries ``report_date:
null`` so the admin UI can render a clear "no run yet" state.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException

from auth_deps import get_admin_user
from deps import db

logger = logging.getLogger(__name__)
router = APIRouter()


def _samples_failed(pages: List[Dict[str, Any]], limit: int = 10) -> List[Dict[str, Any]]:
    """Project the per-page docs down to the URLs that actually failed
    a leg, capped at ``limit`` so the admin payload stays small."""
    out: List[Dict[str, Any]] = []
    for p in pages or []:
        failures = p.get("failures") or []
        if not failures:
            continue
        out.append({
            "url":          p.get("url"),
            "board":        p.get("board"),
            "chapter_slug": p.get("chapter_slug"),
            "page_type":    p.get("page_type"),
            "failures":     failures[:5],  # cap per-page failure list too
        })
        if len(out) >= limit:
            break
    return out


@router.post("/api/admin/seo/baseline-publish")
async def admin_baseline_publish(
    payload: dict = Body(...),
    admin: dict = Depends(get_admin_user),
):
    """Receive a weekly SEO baseline summary from the Lambda.

    The seo-baseline Lambda mints a short-lived admin JWT (same
    pattern the cache-effectiveness Lambda uses against
    ``/api/health/cache``) and POSTs the full report here so the
    admin dashboard has a single, explicit "publish" semantics —
    the brief asks for the Lambda to *post results to the admin
    observability tile*, not just persist to Mongo behind it.

    Body shape mirrors ``aca_jobs.seo_baseline`` ``run_baseline_publish``
    return + a ``pages`` array. Idempotent upsert on ``report_date``.
    """
    report_date = (payload.get("report_date") or "").strip()
    if not report_date:
        raise HTTPException(
            status_code=400,
            detail="missing required field: report_date",
        )
    # Round-2 reviewer fix: defensive merge — only $set fields the
    # caller actually supplied. This protects the canonical doc that
    # ``aca_jobs.seo_baseline.run_baseline_publish`` already wrote
    # against a thin POST that would otherwise blank out
    # ``summary`` / ``pages`` on a same-``report_date`` upsert.
    SET_FIELDS = (
        "started_at", "finished_at", "duration_s", "public_base_url",
        "sampled_pages", "summary", "pages", "wow_delta_seo_score",
        "prior_started_at",
    )
    set_doc: Dict[str, Any] = {
        "report_date":   report_date,
        "task":          28,
        "published_via": "post",
        "published_at":  datetime.now(tz=timezone.utc),
    }
    for field in SET_FIELDS:
        if field in payload and payload[field] is not None:
            # Empty containers (e.g. `pages: []` from a degenerate
            # run) ARE meaningful — treat them as explicit signals.
            set_doc[field] = payload[field]
    try:
        await db.seo_baseline_runs.update_one(
            {"report_date": report_date},
            {"$set": set_doc},
            upsert=True,
        )
    except Exception as exc:
        logger.exception("admin_baseline_publish: persistence failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail=f"seo_baseline_runs write failed: {type(exc).__name__}",
        )
    return {"ok": True, "report_date": report_date}


@router.get("/api/admin/seo/baseline-latest")
async def admin_baseline_latest(admin: dict = Depends(get_admin_user)):
    """Return the latest weekly SEO-baseline summary for the admin tile."""
    try:
        doc = await db.seo_baseline_runs.find_one(
            {}, sort=[("started_at", -1)],
        )
    except Exception as e:
        # V4 §12 — surface the failure instead of pretending the
        # collection is empty. The admin UI distinguishes 503 from a
        # "no run yet" body.
        logger.warning("admin_baseline_latest: read failed: %s", e)
        raise HTTPException(
            status_code=503,
            detail=f"seo_baseline_runs read failed: {type(e).__name__}",
        )
    if not doc:
        return {
            "report_date":         None,
            "started_at":          None,
            "duration_s":          0.0,
            "public_base_url":     None,
            "sampled_pages":       0,
            "summary":             {},
            "wow_delta_seo_score": None,
            "prior":               None,
            "samples_failed":      [],
        }

    # Pull the prior doc *only* to surface its key fields in the
    # response payload. The WoW delta is already pre-computed by the
    # Lambda and persisted on the latest doc, so the admin UI does
    # not need to re-derive it.
    prior_block: Optional[Dict[str, Any]] = None
    try:
        prior = await db.seo_baseline_runs.find_one(
            {"started_at": {"$lt": doc.get("started_at")}},
            sort=[("started_at", -1)],
            projection={"report_date": 1, "summary.median_seo_score": 1, "started_at": 1},
        )
        if prior:
            prior_summary = (prior.get("summary") or {})
            prior_block = {
                "report_date":      prior.get("report_date"),
                "started_at":       prior.get("started_at"),
                "median_seo_score": prior_summary.get("median_seo_score"),
            }
    except Exception as exc:
        logger.warning("admin_baseline_latest: prior lookup failed: %s", exc)

    summary = doc.get("summary") or {}
    return {
        "report_date":         doc.get("report_date"),
        "started_at":          doc.get("started_at"),
        "finished_at":         doc.get("finished_at"),
        "duration_s":          float(doc.get("duration_s") or 0.0),
        "public_base_url":     doc.get("public_base_url"),
        "sampled_pages":       int(doc.get("sampled_pages") or 0),
        "summary":             summary,
        "wow_delta_seo_score": doc.get("wow_delta_seo_score"),
        "prior":               prior_block,
        "samples_failed":      _samples_failed(doc.get("pages") or []),
    }
