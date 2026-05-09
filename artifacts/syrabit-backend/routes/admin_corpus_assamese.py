"""Task #45 — admin tile + health endpoint for the Assamese corpus.

Single endpoint:

  GET /api/health/corpus/assamese  (admin-only)

Scope
-----
The payload reports two row groups under a single ``coverage.collections``
list:

  * **Gated** — the four Mongo collections the
    ``aca_jobs.as_translation_backfill`` driver actually owns
    (``subjects``, ``chapters``, ``seo_pages``, ``pyq_html_pages``).
    These are the SSR-feeding collections with an English source field
    + ``<field>_as`` sibling, which is what the 0.85 script-ratio gate
    measures. Only these rows are folded into ``coverage.overall_ratio``
    and only their `AssameseCoverage` series feeds the
    ``assamese-corpus-coverage-low`` CloudWatch alarm.
  * **AI-cache** — ``mcq`` / ``flashcards`` / ``definitions`` are
    appended as degraded rows (``status="ai_input_cache_only"``,
    ``ratio=0.0``) so the admin tile lists them next to the gated four.
    They live in the Redis-backed ``ai_input_cache`` (see
    ``artifacts/syrabit-backend/ai_input_cache.py``) and do not have a
    persistent English-source + ``_as`` sibling shape; their Assamese
    variants are produced by ``content_formatter.format_content`` at
    request time. The deterministic-cache hit ratio
    (``/api/health/cache``) is the correct observability surface for
    them and the UI links operators there for that reason.

The two row groups are also surfaced separately in the response payload
as ``coverage.gated_collections`` and ``coverage.ai_cache_collections``
so callers can render them under different headers without re-deriving
the split from row ``status``.

Returns per-collection coverage against the 0.85 script-ratio gate and
the most recent ``assamese_backfill_runs`` summary so the admin
dashboard can render:

  * the four largest collections (subjects, chapters, seo_pages,
    pyq_html_pages) with a target line at 0.85
  * the latest accept/reject counts + reject reasons (from the persisted
    run doc) so on-call can see *why* a collection's coverage is not
    moving (translator returning low-ratio output vs translator
    timing out vs source already passes).

Reads ``db.assamese_backfill_runs`` (most-recent doc by ``started_at``)
for the run summary and counts collections live for the coverage block
(cheap — uses the indexed ``<field>_as_script_ratio`` field that the
backfill writes alongside each accepted translation).
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from auth_deps import get_admin_user
from deps import db

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health/corpus/assamese")
async def admin_corpus_assamese(admin: dict = Depends(get_admin_user)) -> Dict[str, Any]:
    """Admin-only Assamese corpus coverage + last backfill run report."""
    try:
        from aca_jobs.as_translation_backfill import (
            compute_assamese_coverage,
            latest_run_report,
            COVERAGE_TARGET_RATIO,
            COVERAGE_ALARM_FLOOR,
            MIN_AS_SCRIPT_RATIO,
        )
    except Exception as e:
        logger.warning("admin_corpus_assamese: backfill import failed: %s", e)
        raise HTTPException(
            status_code=503,
            detail=f"as_translation_backfill import failed: {type(e).__name__}",
        )

    try:
        coverage = await compute_assamese_coverage(db)
    except Exception as e:
        logger.warning("admin_corpus_assamese: coverage compute failed: %s", e)
        raise HTTPException(
            status_code=503,
            detail=f"coverage compute failed: {type(e).__name__}",
        )

    last_run = await latest_run_report(db)

    return {
        "coverage":             coverage,
        "target_ratio":         COVERAGE_TARGET_RATIO,
        "alarm_floor":          COVERAGE_ALARM_FLOOR,
        "min_script_ratio":     MIN_AS_SCRIPT_RATIO,
        "last_run":             last_run,
    }
