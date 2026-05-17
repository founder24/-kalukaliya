"""Task #45 — admin tile + health endpoint for the Assamese corpus.

Single GET endpoint:

  GET /api/health/corpus/assamese  (admin-only)

On-demand backfill trigger:

  POST /api/admin/corpus/assamese/backfill  (admin-only)

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

    For ``chapters`` the backfill automatically excludes image-based PYQ /
    question-paper docs (``content_type`` in ``{"pyq", "question_paper"}``)
    because they have no translatable text; the coverage count and totals
    reported here reflect the same exclusion.

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

import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field

from auth_deps import get_admin_user
from deps import db

logger = logging.getLogger(__name__)
router = APIRouter()


# ── GET /health/corpus/assamese ──────────────────────────────────────────────

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


# ── POST /admin/corpus/assamese/backfill ─────────────────────────────────────

class BackfillRequest(BaseModel):
    collections: Optional[List[str]] = Field(
        default=None,
        description=(
            "Collections to translate. Defaults to all four gated collections "
            "(subjects, chapters, seo_pages, pyq_html_pages). "
            "For a notes-only run pass [\"chapters\"]."
        ),
    )
    max_docs: int = Field(
        default=200,
        ge=1,
        le=5000,
        description=(
            "Maximum docs to process per collection in this pass. "
            "The job is resumable: subsequent requests pick up where this one left off."
        ),
    )
    batch_size: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Docs per Mongo batch / bulk_write.",
    )
    force: bool = Field(
        default=False,
        description=(
            "When true, re-translates every document regardless of whether an "
            "_as sibling already exists or the source hash matches. Use this for "
            "the 'Regenerate All' admin action. Defaults to False (fill-missing "
            "only, which is safe to run repeatedly)."
        ),
    )


def _preflight_warnings() -> List[str]:
    """Return a list of human-readable warnings for missing translation credentials.

    Runs synchronously in the request handler (pure env-var reads, zero I/O)
    so the 202 body tells the caller immediately why a pass might produce zero
    translations, rather than burying the reason in server logs minutes later.

    Chain requirements (Task #291 locked order):
      Step 1 — IndicTrans2 via Workers AI:
        CLOUDFLARE_API_TOKEN    (API auth)
        CF_AI_GATEWAY_ACCOUNT_ID (account routing)
        → If either is absent every translation call returns empty and nothing
          is written to MongoDB. This is the highest-priority warning.
      Step 2 — Vertex / Gemini 2.5 Flash polish:
        VERTEX_PROJECT_ID or GOOGLE_APPLICATION_CREDENTIALS_JSON
        → If absent the polish step is skipped; IndicTrans2 raw output is
          still accepted and written (graceful degradation per V4 §4).

    Sarvam (SARVAM_API_KEY) is intentionally not checked here — it is scoped
    to assamese_rag_chat only (Task #291) and is not part of the bulk
    translate chain.
    """
    import os
    warnings: List[str] = []

    cf_token   = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    cf_account = os.environ.get("CF_AI_GATEWAY_ACCOUNT_ID", "").strip()
    if not cf_token and not cf_account:
        warnings.append(
            "CRITICAL — CLOUDFLARE_API_TOKEN and CF_AI_GATEWAY_ACCOUNT_ID are "
            "both unset: IndicTrans2 (primary translator) cannot run. Every "
            "translation call will return empty and nothing will be written to "
            "MongoDB. Set both env vars and restart the server before triggering "
            "a production backfill."
        )
    elif not cf_token:
        warnings.append(
            "CRITICAL — CLOUDFLARE_API_TOKEN is unset: IndicTrans2 cannot "
            "authenticate. Translations will silently return empty."
        )
    elif not cf_account:
        warnings.append(
            "CRITICAL — CF_AI_GATEWAY_ACCOUNT_ID is unset: Workers AI routing "
            "will fail. Translations will silently return empty."
        )

    vertex_project = os.environ.get("VERTEX_PROJECT_ID", "").strip()
    gcp_creds      = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON", "").strip()
    if not vertex_project and not gcp_creds:
        warnings.append(
            "WARN — VERTEX_PROJECT_ID and GOOGLE_APPLICATION_CREDENTIALS_JSON "
            "are both unset: the Vertex/Gemini 2.5 Flash polish step will be "
            "skipped. IndicTrans2 raw output will still be written (acceptable "
            "quality, no fluency polish)."
        )

    return warnings


async def _run_backfill_bg(
    collections: Optional[List[str]],
    max_docs: int,
    batch_size: int,
    force: bool = False,
) -> None:
    """Background coroutine that drives the translation pass.

    Runs entirely in the server's event loop; the HTTP response has already
    been returned (202) before this starts doing real work.  The driver's
    ``_run_lock`` ensures at most one pass is active at any time — a second
    concurrent POST returns 409 rather than spawning a second task.
    """
    try:
        from aca_jobs.as_translation_backfill import run_backfill
    except Exception as exc:
        logger.error("[admin_corpus_assamese] backfill import failed in bg task: %s", exc)
        return
    try:
        result = await run_backfill(
            db,
            collections=collections,
            max_docs=max_docs,
            batch_size=batch_size,
            force=force,
        )
        logger.info("[admin_corpus_assamese] on-demand backfill finished: %s", result)
    except Exception as exc:
        logger.error("[admin_corpus_assamese] on-demand backfill raised: %s", exc)


@router.post("/admin/corpus/assamese/backfill", status_code=202)
async def trigger_assamese_backfill(
    body: BackfillRequest,
    background_tasks: BackgroundTasks,
    admin: dict = Depends(get_admin_user),
) -> Dict[str, Any]:
    """Trigger an on-demand Assamese translation pass (non-blocking, admin-only).

    Returns 202 immediately; the actual translation work runs in the
    background.  The driver processes at most ``max_docs`` documents per
    collection, skips content that is already correctly translated, and
    automatically skips image-based PYQ chapters (``content_type`` in
    ``{"pyq", "question_paper"}``) — those have no translatable text.

    Poll ``GET /api/health/corpus/assamese`` to watch coverage progress.

    Concurrency: if a pass is already in flight (nightly Lambda or a
    previous POST), the background task will detect the lock and exit
    immediately without double-processing anything — safe to POST again.
    """
    try:
        from aca_jobs.as_translation_backfill import (
            _run_lock,
            FIELD_MAP,
            SKIP_CHAPTER_CONTENT_TYPES,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"as_translation_backfill import failed: {type(exc).__name__}",
        )

    bad = [c for c in (body.collections or []) if c not in FIELD_MAP]
    if bad:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown collection(s): {bad}. "
                f"Valid choices: {sorted(FIELD_MAP.keys())}"
            ),
        )

    already_running = _run_lock.locked()
    warnings = _preflight_warnings()

    background_tasks.add_task(
        _run_backfill_bg,
        collections=body.collections,
        max_docs=body.max_docs,
        batch_size=body.batch_size,
        force=body.force,
    )

    return {
        "status":           "accepted",
        "already_running":  already_running,
        "note": (
            "A translation pass is already in flight — this request will "
            "queue behind it."
            if already_running else
            "Regeneration pass started in the background."
            if body.force else
            "Translation pass started in the background."
        ),
        "collections":      body.collections or sorted(FIELD_MAP.keys()),
        "max_docs":         body.max_docs,
        "batch_size":       body.batch_size,
        "force":            body.force,
        "pyq_skip":         sorted(SKIP_CHAPTER_CONTENT_TYPES),
        "poll":             "GET /api/health/corpus/assamese",
        "preflight_warnings": warnings,
        "preflight_ok":     len(warnings) == 0,
    }


# ── GET /admin/corpus/assamese/progress ──────────────────────────────────────

@router.get("/admin/corpus/assamese/progress")
async def admin_corpus_assamese_progress(
    admin: dict = Depends(get_admin_user),
) -> Dict[str, Any]:
    """Per-collection progress counters and running state.

    Returns remaining-doc estimates and the ``running`` flag for each of
    the four backfill collections so the admin panel can show a live
    progress indicator without waiting for a full coverage recount.

    Cheaper than ``GET /health/corpus/assamese`` because it reads only
    the state docs and runs a single ``count_documents`` per collection,
    not the full script-ratio scan.
    """
    try:
        from aca_jobs.as_translation_backfill import get_progress, _run_lock
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"as_translation_backfill import failed: {type(exc).__name__}",
        )
    progress = await get_progress(db)
    progress["lock_held"] = _run_lock.locked()
    return progress
