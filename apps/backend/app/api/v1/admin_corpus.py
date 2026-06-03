"""
Admin Corpus Endpoints — Assamese content coverage stats and backfill trigger.
Used by the AssameseBackfillPanel frontend component.
"""

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request

from app.api.v1.admin import _validate_admin_session
from app.models.content import Chapter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin Corpus"])


# ─── Public health endpoint ────────────────────────────────────────────────────

@router.get("/health/corpus/assamese")
async def corpus_assamese_health(request: Request):
    """
    Returns Assamese content coverage stats for all tracked collections.
    Public endpoint — used by monitoring and the admin panel.
    """
    try:
        chapters_total = await Chapter.find(
            {"content_en": {"$nin": [None, ""]}}
        ).count()
        chapters_translated = await Chapter.find(
            {
                "content_en": {"$nin": [None, ""]},
                "content_as": {"$nin": [None, ""]},
            }
        ).count()
        chapters_ratio = (
            chapters_translated / chapters_total if chapters_total else 0.0
        )
        chapters_remaining = chapters_total - chapters_translated

        collections = [
            {
                "collection": "chapters",
                "ratio": round(chapters_ratio, 4),
                "translated_docs": chapters_translated,
                "total_docs": chapters_total,
                "remaining": chapters_remaining,
                "status": "ok",
            },
        ]

        overall_ratio = chapters_ratio

        last_run = getattr(request.app.state, "corpus_assamese_last_run", None)

        return {
            "coverage": {
                "overall_ratio": round(overall_ratio, 4),
                "collections": collections,
            },
            "target_ratio": 0.85,
            "alarm_floor": 0.80,
            "last_run": last_run,
        }
    except Exception as e:
        logger.warning(f"corpus/assamese health query failed: {e}")
        return {
            "coverage": {"overall_ratio": 0.0, "collections": []},
            "target_ratio": 0.85,
            "alarm_floor": 0.80,
            "last_run": None,
            "error": str(e),
        }


# ─── Admin: live progress ──────────────────────────────────────────────────────

@router.get("/admin/corpus/assamese/progress")
async def corpus_assamese_progress(request: Request):
    """Returns real-time backfill progress (fast, no DB queries)."""
    await _validate_admin_session(request)
    progress = getattr(request.app.state, "corpus_assamese_progress", {})
    lock_held = any(c.get("running") for c in progress.values())
    return {
        "lock_held": lock_held,
        "collections": progress,
    }


# ─── Admin: trigger backfill ───────────────────────────────────────────────────

@router.post("/admin/corpus/assamese/backfill")
async def corpus_assamese_backfill(request: Request):
    """
    Trigger an Assamese content backfill pass using Sarvam AI.
    Runs in the background. Returns immediately.
    """
    await _validate_admin_session(request)

    body = await request.json() if await request.body() else {}
    max_docs = int(body.get("max_docs", 50))
    force = bool(body.get("force", False))

    progress = getattr(request.app.state, "corpus_assamese_progress", {})
    if progress.get("chapters", {}).get("running"):
        return {
            "already_running": True,
            "note": "Backfill already in progress",
            "preflight_ok": True,
            "preflight_warnings": [],
        }

    from app.services.ai.sarvam_client import sarvam_client

    preflight_warnings = []
    preflight_ok = True

    if not sarvam_client:
        preflight_warnings.append("Sarvam AI client not available")
        preflight_ok = False

    if not preflight_ok:
        return {
            "already_running": False,
            "preflight_ok": False,
            "preflight_warnings": preflight_warnings,
            "note": "Pre-flight checks failed — backfill not started",
        }

    app_state = request.app.state

    async def _run():
        from app.services.content.chapter_translator import chapter_translator

        try:
            result = await chapter_translator.bulk_translate(
                app_state,
                max_docs=max_docs,
                force=force,
            )
            finished_at = datetime.now(timezone.utc).isoformat()
            app_state.corpus_assamese_last_run = {
                "finished_at": finished_at,
                "results": [result],
            }
            logger.info(f"Assamese backfill complete: {result}")
        except Exception as e:
            logger.error(f"Assamese backfill error: {e}")

    asyncio.create_task(_run())

    return {
        "already_running": False,
        "preflight_ok": True,
        "preflight_warnings": [],
        "note": f"Backfill started — up to {max_docs} chapters (force={force})",
    }
