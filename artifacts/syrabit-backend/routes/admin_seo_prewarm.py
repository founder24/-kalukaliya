"""Task #13 — admin tile for SEO prewarm coverage.

Single endpoint:

  GET /api/admin/seo/prewarm-coverage

Returns the most recent ``aca_jobs.prewarm_seo_routes`` run summary
the admin dashboard renders into the ``/admin/seo/prewarm-coverage``
tile:

  {
    "last_run_at":   "2026-05-09T01:00:00+00:00",
    "duration_s":    412.5,
    "scanned":       4982,
    "urls_attempted": 34874,
    "urls_warmed":    34102,
    "urls_failed":    772,
    "success_rate":   0.9779,
    "season":         "exam",
    "by_board": [
      {"board": "AHSEC", "warmed": 18200, "failed": 102, "success_rate": 0.9944},
      ...
    ],
    "samples_failed": [{"url": "...", "status": 502, "reason": "non_2xx"}, ...]
  }

Reads ``db.seo_prewarm_runs`` (most-recent doc by ``started_at``).
When the collection is empty the response carries ``last_run_at:
null`` so the admin UI can render a clear "no run yet" state.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException

from auth_deps import get_admin_user
from deps import db

logger = logging.getLogger(__name__)
router = APIRouter()


def _per_board_rows(by_board: Dict[str, Dict[str, int]]) -> List[Dict[str, Any]]:
    """Convert the persisted ``by_board`` dict into a list the
    admin grid can render directly. Sorted alphabetically so the
    grid is stable across re-renders even when underlying counts
    swing run-to-run."""
    rows: List[Dict[str, Any]] = []
    for board, row in sorted((by_board or {}).items()):
        attempted = int(row.get("attempted", 0) or 0)
        warmed = int(row.get("warmed", 0) or 0)
        failed = int(row.get("failed", 0) or 0)
        rows.append({
            "board":        board,
            "attempted":    attempted,
            "warmed":       warmed,
            "failed":       failed,
            "success_rate": round(warmed / attempted, 4) if attempted else 0.0,
        })
    return rows


@router.get("/api/admin/seo/prewarm-coverage")
async def admin_prewarm_coverage(admin: dict = Depends(get_admin_user)):
    """Return the latest SEO-prewarm run summary for the admin tile."""
    try:
        doc = await db.seo_prewarm_runs.find_one(
            {}, sort=[("started_at", -1)],
        )
    except Exception as e:
        # V4 §12 — surface the failure instead of pretending the
        # collection is empty. The admin UI distinguishes 503 from a
        # "no run yet" body.
        logger.warning("admin_prewarm_coverage: read failed: %s", e)
        raise HTTPException(
            status_code=503,
            detail=f"seo_prewarm_runs read failed: {type(e).__name__}",
        )
    if not doc:
        return {
            "last_run_at":     None,
            "scanned":         0,
            "urls_attempted":  0,
            "urls_warmed":     0,
            "urls_failed":     0,
            "success_rate":    0.0,
            "season":          None,
            "by_board":        [],
            "samples_failed":  [],
            "skip_reasons":    {},
            "duration_s":      0.0,
        }
    doc.pop("_id", None)
    return {
        "last_run_at":    doc.get("finished_at") or doc.get("started_at"),
        "scanned":        int(doc.get("scanned") or 0),
        "urls_attempted": int(doc.get("urls_attempted") or 0),
        "urls_warmed":    int(doc.get("urls_warmed") or 0),
        "urls_failed":    int(doc.get("urls_failed") or 0),
        "success_rate":   float(doc.get("success_rate") or 0.0),
        "season":         doc.get("season"),
        "duration_s":     float(doc.get("duration_s") or 0.0),
        "by_board":       _per_board_rows(doc.get("by_board") or {}),
        "samples_failed": (doc.get("samples_failed") or [])[:10],
        "skip_reasons":   doc.get("skip_reasons") or {},
    }
