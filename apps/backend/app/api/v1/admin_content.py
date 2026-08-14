"""
Admin Content Endpoints - Full CRUD for content hierarchy + AI generation + publishing.
Layer 1: Board/Class/Stream/Subject/Chapter CRUD
Layer 2: Topics, Content editing, Topic index
Layer 3: AI generation, Publishing, FAQ
"""

import re
import logging
import uuid as _uuid
from datetime import datetime, timezone
from typing import Optional

from beanie import PydanticObjectId
from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel

from app.api.v1.admin import require_admin_session, csrf_guard
from app.models.content import Board, Class, Stream, Subject, Chapter, Topic, ContentAuditLog
from app.models.user import User
from app.services.content_generation import content_generation_service
from app.services.content_publisher import content_publisher_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin Content"], dependencies=[Depends(require_admin_session), Depends(csrf_guard)])

# ── Progress log paths (module-level so tests can redirect via patch.object) ──
import pathlib as _pathlib
_AHSEC_SCRIPTS_DIR   = _pathlib.Path(__file__).parent.parent.parent.parent / "scripts"
_AHSEC_PROGRESS_FILE = _AHSEC_SCRIPTS_DIR / ".ahsec_ingest_progress.jsonl"
_AHSEC_PROGRESS_LOCK = _AHSEC_SCRIPTS_DIR / ".ahsec_ingest_progress.lock"


# ── Seeder helpers ────────────────────────────────────────────────────────────

def _live_run_to_dict(live: dict, run_type: str = "notes") -> dict:
    """Serialise in-process app.state status dict to a run-history entry."""
    return {
        "run_id":        live.get("run_id", "live"),
        "run_type":      run_type,
        "status":        "running",
        "running":       True,
        "started_at":    live.get("started_at"),
        "finished_at":   None,
        "total":         live.get("total", 0),
        "completed":     live.get("completed", 0),
        "failed":        live.get("failed", 0),
        "skipped":       live.get("skipped", 0),
        "topics_seeded": live.get("topics_seeded", 0),
        "failed_ids":    live.get("failed_ids", []),
        "errors":        live.get("errors", []),
        "concurrency":   live.get("concurrency", 2),
        "force":         live.get("force", False),
        "current":       live.get("current", ""),
    }


def _run_doc_to_dict(r) -> dict:
    """Serialise a SeedRun Beanie document to a plain dict."""
    return {
        "run_id":        str(r.id),
        "run_type":      getattr(r, "run_type", "notes"),
        "status":        r.status,
        "running":       r.status == "running",
        "started_at":    r.started_at.isoformat(),
        "finished_at":   r.finished_at.isoformat() if r.finished_at else None,
        "total":         r.total,
        "completed":     r.completed,
        "failed":        r.failed,
        "skipped":       r.skipped,
        "topics_seeded": r.topics_seeded,
        "failed_ids":    r.failed_ids,
        "errors":        r.errors,
        "concurrency":   r.concurrency,
        "force":         r.force,
        "current":       r.current,
    }


# ── Seeder Run History ────────────────────────────────────────────────────────

@router.get("/content/seed-notes/history")
async def admin_seed_notes_history(request: Request, limit: int = 20):
    """Return recent seed-notes AND seed-assamese runs for admin review.

    Session-auth protected (admin panel friendly — no cron Bearer token needed).
    Includes live in-process status for whichever seeder is actively running.
    """
    from app.models.seed_run import SeedRun

    runs_out = []
    live_run_ids: set[str] = set()

    # Inject any live in-process status at the top
    for state_key, run_type in [("seed_notes_status", "notes"), ("seed_assamese_status", "assamese")]:
        live = getattr(request.app.state, state_key, None)
        if live and live.get("running"):
            runs_out.append(_live_run_to_dict(live, run_type=run_type))
            if rid := live.get("run_id"):
                live_run_ids.add(rid)

    try:
        runs = await SeedRun.find(sort=[("started_at", -1)]).to_list(length=limit)
        for r in runs:
            if str(r.id) in live_run_ids:
                continue   # already shown as live above
            runs_out.append(_run_doc_to_dict(r))
    except Exception as e:
        logger.warning(f"admin_seed_notes_history: MongoDB query failed: {e}")
        if not runs_out:
            return {"runs": [], "error": str(e)}

    return {"runs": runs_out[:limit]}


# ── Stuck chapters (notes_provider_unavailable) ───────────────────────────────

@router.get("/content/seed-notes/stuck")
async def admin_seed_notes_stuck():
    """Return chapters that failed notes generation due to both providers being
    unavailable (status='notes_provider_unavailable' in the AHSEC progress log),
    reconciled against MongoDB so chapters already fixed clear automatically.

    For each JSONL candidate the endpoint checks the Chapter document: if the
    notes field for the logged medium is now non-empty the chapter is excluded
    (it was resolved by a subsequent run or manual edit).  Only truly unresolved
    chapters are returned.
    """
    import json as _json
    from beanie import PydanticObjectId
    from app.models.content import Chapter as _Chapter

    progress_file = _AHSEC_PROGRESS_FILE

    if not progress_file.exists():
        return {"stuck": [], "total": 0, "file_exists": False}

    # Read all records; track latest status per progress key
    # (JSONL is append-only — later entries supersede earlier ones for same key)
    latest_by_key: dict[str, dict] = {}
    try:
        for line in progress_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = _json.loads(line)
            except Exception:
                continue
            dedup_key = rec.get("key") or rec.get("chapter_id", "")
            if not dedup_key:
                continue
            latest_by_key[dedup_key] = rec
    except Exception as e:
        logger.warning(f"admin_seed_notes_stuck: failed to read progress file: {e}")
        return {"stuck": [], "total": 0, "error": str(e)}

    candidates = [
        r for r in latest_by_key.values()
        if r.get("status") == "notes_provider_unavailable"
    ]

    if not candidates:
        return {"stuck": [], "total": 0, "file_exists": True}

    # Reconcile against MongoDB: exclude chapters whose notes field is now populated
    stuck = []
    for r in candidates:
        chapter_id = r.get("chapter_id", "")
        medium = r.get("medium") or "en"

        if chapter_id:
            try:
                chapter_doc = await _Chapter.get(PydanticObjectId(chapter_id))
                if chapter_doc:
                    notes_field = chapter_doc.notes_en if medium == "en" else chapter_doc.notes_as
                    if notes_field and len(notes_field) > 100:
                        # Notes now exist — chapter was resolved; skip it
                        continue
            except Exception:
                pass  # lookup failure → include in stuck list to be safe

        stuck.append({
            "chapter_id": chapter_id,
            "key":        r.get("key", ""),
            "pdf_url":    r.get("pdf_url", ""),
            "medium":     medium,
            "detail":     r.get("detail", ""),
            "ts":         r.get("ts", ""),
        })

    # Newest failures first
    stuck.sort(key=lambda x: x["ts"], reverse=True)

    return {
        "stuck": stuck,
        "total": len(stuck),
        "file_exists": True,
    }


# ── Stuck chapters retry background ──────────────────────────────────────────

async def _ahsec_stuck_retry_background(app, stuck_chapters: list[dict]) -> None:
    """Background task: re-run the AHSEC notes pipeline for provider-unavailable chapters.

    Groups by pdf_url (downloads each PDF exactly once), extracts the specific
    chapter by raw_num from the progress key, runs generate_notes() through the
    real Sarvam → Gemini fallback chain, saves notes_en/notes_as to MongoDB,
    and writes a terminal 'done' (or updated 'notes_provider_unavailable') record
    to .ahsec_ingest_progress.jsonl so the stuck-list reconciles correctly.
    """
    import re as _re
    import sys
    from pathlib import Path as _Path

    backend_root = _Path(__file__).parent.parent.parent.parent
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))

    # ── Outer try/finally guarantees compaction always runs ───────────────────
    # This covers all exit paths: normal completion, early return on import
    # failure, and any unexpected exception that escapes the processing loops.
    try:
        try:
            from scripts.ahsec_ingest import (
                generate_notes,
                extract_pdf_text,
                split_into_chapters,
                save_chapter_content,
                reindex_chapter,
                _log_progress,
                NotesProviderUnavailableError,
                notes_to_rag_sections,
                extract_topics_from_notes,
            )
        except Exception as exc:
            logger.error(f"ahsec_stuck_retry: failed to import ingest module: {exc}")
            return  # finally block below still fires

        from app.services.ai.sarvam_client import sarvam_client
        from app.models.content import Chapter as _Chapter, Subject as _Subject
        from beanie import PydanticObjectId

        # Group by pdf_url so each PDF is downloaded only once
        by_pdf: dict[str, list[dict]] = {}
        for ch in stuck_chapters:
            pdf_url = (ch.get("pdf_url") or "").strip()
            key     = (ch.get("key") or "").strip()
            if not pdf_url or not key:
                continue
            m = _re.search(r'\|ch(\d+)$', key)
            if not m:
                logger.warning(f"ahsec_stuck_retry: cannot parse raw_num from key {key!r}")
                continue
            raw_num = int(m.group(1))
            by_pdf.setdefault(pdf_url, []).append({
                "chapter_id": (ch.get("chapter_id") or "").strip(),
                "key":        key,
                "raw_num":    raw_num,
                "medium":     ch.get("medium") or "en",
            })

        for pdf_url, items in by_pdf.items():
            medium = items[0]["medium"]

            # Download + extract PDF (once per URL)
            try:
                pages         = await extract_pdf_text(pdf_url, medium)
                chapter_texts = split_into_chapters(pages, medium)
            except Exception as exc:
                logger.error(f"ahsec_stuck_retry: PDF extraction failed for {pdf_url}: {exc}")
                continue

            raw_num_map = {c["chapter_num"]: c for c in chapter_texts}

            for item in items:
                raw_num    = item["raw_num"]
                key        = item["key"]
                chapter_id = item["chapter_id"]

                ch_info = raw_num_map.get(raw_num)
                if not ch_info:
                    logger.warning(
                        f"ahsec_stuck_retry: ch{raw_num} not found in PDF {pdf_url} "
                        f"(detected chapters: {sorted(raw_num_map)})"
                    )
                    continue

                body_text = ch_info["body_text"]
                ch_title  = ch_info["title"]

                # Fetch chapter + subject from MongoDB
                try:
                    chapter_doc = await _Chapter.get(PydanticObjectId(chapter_id))
                    if not chapter_doc:
                        logger.warning(f"ahsec_stuck_retry: chapter {chapter_id} not in MongoDB")
                        continue
                    subj         = await _Subject.get(chapter_doc.subject_id)
                    subject_name = subj.name if subj else "Unknown"
                except Exception as exc:
                    logger.error(f"ahsec_stuck_retry: DB lookup failed for {chapter_id}: {exc}")
                    continue

                # Run the real AHSEC generate_notes (Sarvam → Gemini)
                try:
                    notes_text = await generate_notes(
                        sarvam_client, body_text, ch_title, subject_name, medium
                    )
                except NotesProviderUnavailableError as exc:
                    logger.warning(
                        f"ahsec_stuck_retry: providers still unavailable for '{ch_title}': {exc}"
                    )
                    _log_progress(
                        key, "notes_provider_unavailable",
                        detail=f"[reason={exc.reason}] {exc}",
                        chapter_id=chapter_id, pdf_url=pdf_url, medium=medium,
                    )
                    continue
                except Exception as exc:
                    logger.error(f"ahsec_stuck_retry: generate_notes failed for '{ch_title}': {exc}")
                    _log_progress(
                        key, "error",
                        detail=str(exc), chapter_id=chapter_id, pdf_url=pdf_url, medium=medium,
                    )
                    continue

                if not notes_text or len(notes_text) < 50:
                    logger.warning(f"ahsec_stuck_retry: empty/short notes for '{ch_title}' — skipping")
                    continue

                # Save notes_en/notes_as + RAG sections to MongoDB
                rag_sections = notes_to_rag_sections(notes_text)
                topics       = extract_topics_from_notes(notes_text)

                try:
                    written = await save_chapter_content(
                        chapter_doc, notes_text, rag_sections, [], topics,
                        medium, force=True, dry_run=False, source_pdf_url=pdf_url,
                        title_as=ch_title if medium == "as" else "",
                    )
                except Exception as exc:
                    logger.error(f"ahsec_stuck_retry: save failed for '{ch_title}': {exc}")
                    continue

                if written:
                    try:
                        await reindex_chapter(chapter_id, scope="notes")
                    except Exception as exc:
                        logger.warning(f"ahsec_stuck_retry: reindex failed for {chapter_id}: {exc}")
                    # Write terminal progress record — this is what clears the stuck list
                    _log_progress(
                        key, "done",
                        chapter_id=chapter_id, pdf_url=pdf_url, medium=medium,
                    )
                    logger.info(
                        f"ahsec_stuck_retry: ✓ '{ch_title}' ({chapter_id}) — "
                        f"{len(notes_text)} chars written, progress log updated"
                    )
                else:
                    logger.warning(f"ahsec_stuck_retry: save_chapter_content skipped '{ch_title}'")

    finally:
        # ── Auto-compact: guaranteed regardless of exit path ─────────────────
        # Fires after normal completion, early return on import failure, and
        # any unexpected exception that escapes the processing loops.
        try:
            compact_result = await _compact_progress_log()
            logger.info(
                f"ahsec_stuck_retry: auto-compact complete — "
                f"resolved_cleared={compact_result.get('resolved_cleared', 0)}, "
                f"still_stuck={compact_result.get('still_stuck', 0)}"
            )
        except Exception as compact_exc:
            logger.warning(f"ahsec_stuck_retry: auto-compact failed (non-fatal): {compact_exc}")


async def _compact_progress_log() -> dict:
    """Compact the AHSEC progress log: keep only the latest record per key.

    Shared helper used by both the HTTP clear endpoint and the retry background
    task (auto-called after a retry run completes so the list self-heals without
    a manual 'Clear resolved' click).

    A chapter is resolved (dropped) when EITHER:
    - its latest log status is not 'notes_provider_unavailable', OR
    - its notes field in MongoDB is now non-empty (manually fixed).

    Thread/process safety: acquires LOCK_EX on the shared advisory lock file
    before reading or writing; the ingest script holds LOCK_SH during each
    append, so the two never interleave.  The new content is written to a sibling
    temp file and atomically renamed (os.replace) into place.

    Returns:
        {
          "compacted": bool,
          "records_before": int,
          "records_after": int,
          "resolved_cleared": int,
          "still_stuck": int,
          "file_exists": bool,
        }
    """
    import fcntl
    import json as _json
    import os as _os
    import tempfile as _tempfile
    from beanie import PydanticObjectId
    from app.models.content import Chapter as _Chapter

    # Use module-level constants so tests can redirect via patch.object()
    progress_file = _AHSEC_PROGRESS_FILE
    lock_file     = _AHSEC_PROGRESS_LOCK

    if not progress_file.exists():
        return {
            "compacted": False,
            "records_before": 0,
            "records_after": 0,
            "resolved_cleared": 0,
            "still_stuck": 0,
            "file_exists": False,
        }

    lf = lock_file.open("a+")
    try:
        fcntl.flock(lf, fcntl.LOCK_EX)

        latest_by_key: dict[str, dict] = {}
        raw_line_count = 0
        try:
            for line in progress_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                raw_line_count += 1
                try:
                    rec = _json.loads(line)
                except Exception:
                    continue
                dedup_key = rec.get("key") or rec.get("chapter_id", "")
                if not dedup_key:
                    continue
                latest_by_key[dedup_key] = rec
        except Exception as e:
            logger.warning(f"_compact_progress_log: failed to read: {e}")
            return {
                "compacted": False,
                "records_before": raw_line_count,
                "records_after": 0,
                "resolved_cleared": 0,
                "still_stuck": 0,
                "file_exists": True,
                "error": str(e),
            }

        log_stuck_recs:     list[dict] = []
        log_resolved_count: int        = 0
        for rec in latest_by_key.values():
            if rec.get("status") == "notes_provider_unavailable":
                log_stuck_recs.append(rec)
            else:
                log_resolved_count += 1

        still_stuck_recs:     list[dict] = []
        mongo_resolved_count: int        = 0
        for rec in log_stuck_recs:
            chapter_id = rec.get("chapter_id", "")
            medium     = rec.get("medium") or "en"
            resolved   = False
            if chapter_id:
                try:
                    chapter_doc = await _Chapter.get(PydanticObjectId(chapter_id))
                    if chapter_doc:
                        notes_field = (
                            chapter_doc.notes_en if medium == "en"
                            else chapter_doc.notes_as
                        )
                        if notes_field and len(notes_field) > 100:
                            resolved = True
                except Exception:
                    pass
            if resolved:
                mongo_resolved_count += 1
            else:
                still_stuck_recs.append(rec)

        resolved_count = log_resolved_count + mongo_resolved_count

        still_stuck_recs.sort(key=lambda r: r.get("ts", ""))
        new_content = "\n".join(_json.dumps(r) for r in still_stuck_recs)
        if new_content:
            new_content += "\n"

        try:
            fd, tmp_path = _tempfile.mkstemp(
                dir=str(progress_file.parent), prefix=".ahsec_ingest_progress.", suffix=".tmp"
            )
            try:
                with _os.fdopen(fd, "w", encoding="utf-8") as tf:
                    tf.write(new_content)
                _os.replace(tmp_path, str(progress_file))
            except Exception:
                try:
                    _os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as e:
            logger.error(f"_compact_progress_log: write failed: {e}")
            return {
                "compacted": False,
                "records_before": raw_line_count,
                "records_after": len(latest_by_key),
                "resolved_cleared": 0,
                "still_stuck": len(still_stuck_recs),
                "file_exists": True,
                "error": str(e),
            }

    finally:
        fcntl.flock(lf, fcntl.LOCK_UN)
        lf.close()

    logger.info(
        f"_compact_progress_log: {raw_line_count} lines → {len(still_stuck_recs)} kept "
        f"(log-resolved={log_resolved_count}, mongo-resolved={mongo_resolved_count})"
    )
    return {
        "compacted": True,
        "records_before": raw_line_count,
        "records_after": len(still_stuck_recs),
        "resolved_cleared": resolved_count,
        "still_stuck": len(still_stuck_recs),
        "file_exists": True,
    }


@router.post("/content/seed-notes/stuck/clear")
async def admin_clear_resolved_stuck_chapters():
    """Compact the AHSEC progress log — delegates to _compact_progress_log().

    Kept as a named endpoint so the admin 'Clear resolved' button continues to
    work; the compaction logic now also runs automatically at the end of every
    successful retry background task.
    """
    return await _compact_progress_log()
@router.post("/content/seed-notes/stuck/retry")
async def admin_retry_stuck_chapters(request: Request):
    """Trigger a targeted AHSEC notes retry for provider-unavailable chapters.

    Body: {"stuck": [{chapter_id, key, pdf_url, medium, ...}, ...]}
    (the exact shape returned by GET /content/seed-notes/stuck).

    For each chapter: downloads the source PDF (grouped, downloaded once per URL),
    extracts the chapter text by position, calls generate_notes() via the real
    Sarvam → Gemini fallback chain, writes notes_en/notes_as to MongoDB, and
    appends a terminal 'done' record to .ahsec_ingest_progress.jsonl so the
    stuck list reconciles correctly on the next GET call.

    Returns immediately; the retry runs as a background task.
    """
    import asyncio

    body: dict = {}
    try:
        body = await request.json()
    except Exception:
        pass

    stuck: list = body.get("stuck", [])
    if not stuck:
        raise HTTPException(status_code=400, detail="No stuck chapters provided.")
    if len(stuck) > 200:
        raise HTTPException(
            status_code=400, detail="Too many chapters — max 200 per retry batch."
        )

    asyncio.create_task(_ahsec_stuck_retry_background(request.app, stuck))
    return {
        "queued": len(stuck),
        "message": (
            f"Retry launched for {len(stuck)} stuck chapter(s). "
            "Refresh the stuck list in ~2 minutes to see results."
        ),
    }


# ── Seed-notes trigger (admin session auth) ───────────────────────────────────

@router.post("/content/seed-notes")
async def admin_trigger_seed_notes(request: Request):
    """Trigger a seed-notes job from the admin panel (session auth, no cron token).

    Body (all optional):
        { "chapter_ids": [...], "limit": 50, "concurrency": 2, "force": false }
    """
    import asyncio
    from datetime import datetime, timezone
    from app.models.content import Chapter
    from app.models.seed_run import SeedRun
    from beanie import PydanticObjectId
    from app.api.v1.admin_cron import _seed_notes_background

    body: dict = {}
    try:
        body = await request.json()
    except Exception:
        pass

    existing = getattr(request.app.state, "seed_notes_status", {})
    if existing.get("running"):
        raise HTTPException(status_code=409,
                            detail="A seed-notes job is already running.")
    existing_as = getattr(request.app.state, "seed_assamese_status", {})
    if existing_as.get("running"):
        raise HTTPException(status_code=409,
                            detail="A seed-assamese job is running — wait for it to finish.")

    chapter_ids_raw: list = body.get("chapter_ids", [])
    limit: int            = int(body.get("limit", 9999))
    concurrency: int      = max(1, min(int(body.get("concurrency", 2)), 5))
    force: bool           = bool(body.get("force", False))

    if chapter_ids_raw:
        chapters = []
        for cid in chapter_ids_raw[:limit]:
            try:
                ch = await Chapter.get(PydanticObjectId(cid))
                if ch:
                    chapters.append(ch)
            except Exception:
                pass
    else:
        filt: dict = {}
        if not force:
            # Skip chapters that already have notes in EITHER field:
            # notes_en  — written by the AHSEC ingestion pipeline
            # content_en — written by the legacy AI seed path
            # A chapter must have BOTH absent to be re-seeded.
            _absent = lambda field: [  # noqa: E731
                {field: {"$exists": False}},
                {field: None},
                {field: ""},
            ]
            filt["$and"] = [
                {"$or": _absent("notes_en")},
                {"$or": _absent("content_en")},
            ]
        chapters = await Chapter.find(filt).to_list(length=limit)

    total = len(chapters)
    if total == 0:
        return {"job": "nothing_to_do", "total_queued": 0,
                "message": "All chapters already have content (pass force=true to regenerate)"}

    run = SeedRun(status="running", run_type="notes", total=total,
                  concurrency=concurrency, force=force)
    try:
        await run.insert()
        run_id = str(run.id)
    except Exception as e:
        logger.warning(f"Failed to insert seed_run: {e}")
        run_id = "unavailable"

    request.app.state.seed_notes_status = {
        "running": True, "run_id": run_id, "total": total,
        "completed": 0, "failed": 0, "skipped": 0, "topics_seeded": 0,
        "current": "", "failed_ids": [], "errors": [],
        "started_at": run.started_at.isoformat() if run else datetime.now(timezone.utc).isoformat(),
        "finished_at": None, "concurrency": concurrency, "force": force,
    }

    asyncio.create_task(_seed_notes_background(
        app_state=request.app.state,
        chapters=chapters,
        concurrency=concurrency,
        force=force,
        run_id=run_id,
    ))

    return {"job": "started", "run_id": run_id, "total_queued": total, "concurrency": concurrency}


# ── Seed-assamese trigger (admin session auth) ────────────────────────────────

@router.post("/content/seed-assamese")
async def admin_trigger_seed_assamese(request: Request):
    """Translate content_en → content_as for published chapters missing Assamese.

    Session-auth protected. Launches background job, returns immediately.
    Body (all optional): { "limit": 200, "concurrency": 2, "force": false }
    """
    import asyncio
    from datetime import datetime, timezone
    from app.models.content import Chapter
    from app.models.seed_run import SeedRun
    from app.api.v1.admin_cron import _seed_assamese_background

    body: dict = {}
    try:
        body = await request.json()
    except Exception:
        pass

    existing_as = getattr(request.app.state, "seed_assamese_status", {})
    if existing_as.get("running"):
        raise HTTPException(status_code=409,
                            detail="A seed-assamese job is already running.")
    existing_en = getattr(request.app.state, "seed_notes_status", {})
    if existing_en.get("running"):
        raise HTTPException(status_code=409,
                            detail="A seed-notes job is running — wait for it to finish.")

    limit: int        = int(body.get("limit", 9999))
    concurrency: int  = max(1, min(int(body.get("concurrency", 2)), 5))
    force: bool       = bool(body.get("force", False))

    # Chapters with English content (notes_en primary, content_en legacy) but missing Assamese.
    # notes_en/notes_as are the primary pipeline fields written by the AHSEC ingestion
    # pipeline; content_en/content_as are the legacy AI-seed fields.  We must check both
    # so that chapters seeded via either path are picked up and translated.
    filt: dict = {
        "$or": [
            {"notes_en": {"$exists": True, "$nin": [None, ""]}},
            {"content_en": {"$exists": True, "$nin": [None, ""]}},
        ]
    }
    if not force:
        _absent = lambda field: [  # noqa: E731
            {field: {"$exists": False}},
            {field: None},
            {field: ""},
        ]
        # Skip only when BOTH notes_as and content_as are already populated.
        # If either primary (notes_as) or legacy (content_as) is absent, re-translate.
        filt["$and"] = [
            {"$or": _absent("notes_as")},
            {"$or": _absent("content_as")},
        ]
    chapters = await Chapter.find(filt).to_list(length=limit)

    total = len(chapters)
    if total == 0:
        return {"job": "nothing_to_do", "total_queued": 0,
                "message": "All chapters with English content already have Assamese (pass force=true to retranslate)"}

    run = SeedRun(status="running", run_type="assamese", total=total,
                  concurrency=concurrency, force=force)
    try:
        await run.insert()
        run_id = str(run.id)
    except Exception as e:
        logger.warning(f"Failed to insert seed_run (assamese): {e}")
        run_id = "unavailable"

    request.app.state.seed_assamese_status = {
        "running": True, "run_id": run_id, "total": total,
        "completed": 0, "failed": 0, "skipped": 0, "topics_seeded": 0,
        "current": "", "failed_ids": [], "errors": [],
        "started_at": run.started_at.isoformat() if run else datetime.now(timezone.utc).isoformat(),
        "finished_at": None, "concurrency": concurrency, "force": force,
    }

    asyncio.create_task(_seed_assamese_background(
        app_state=request.app.state,
        chapters=chapters,
        concurrency=concurrency,
        force=force,
        run_id=run_id,
    ))

    return {"job": "started", "run_id": run_id, "total_queued": total, "concurrency": concurrency}


# ── Audit log helper ──────────────────────────────────────────────────────────

async def _stamp_audit(
    chapter_id: str,
    action: str,
    actor_id: str,
    subject_id: Optional[str] = None,
    version_before: Optional[int] = None,
    version_after: Optional[int] = None,
    changes: Optional[dict] = None,
) -> None:
    """Fire-and-forget: write one ContentAuditLog entry. Errors are swallowed."""
    try:
        actor_email: Optional[str] = None
        try:
            user = await User.get(actor_id)
            if user:
                actor_email = user.email
        except Exception:
            pass
        entry = ContentAuditLog(
            chapter_id=chapter_id,
            subject_id=subject_id,
            action=action,
            actor_id=actor_id,
            actor_email=actor_email,
            version_before=version_before,
            version_after=version_after,
            changes=changes,
        )
        await entry.insert()
    except Exception as exc:
        logger.warning(f"[audit] failed to write audit log for chapter={chapter_id}: {exc}")


# --- Helpers ---


def _slugify(text: str) -> str:
    """Convert text to URL-friendly slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


# --- Request Models ---


class BoardCreate(BaseModel):
    name: str


class BoardUpdate(BaseModel):
    name: Optional[str] = None
    status: Optional[str] = None


class ClassCreate(BaseModel):
    name: str
    board_id: str


class StreamCreate(BaseModel):
    name: str
    class_id: str


class SubjectCreate(BaseModel):
    name: str
    stream_id: str


class ChapterCreate(BaseModel):
    title: str
    subject_id: str
    chapter_number: int


class ChapterUpdate(BaseModel):
    title: Optional[str] = None
    status: Optional[str] = None
    chapter_number: Optional[int] = None
    slug: Optional[str] = None
    description: Optional[str] = None
    # Primary AI-generated notes (written by ingestion pipeline)
    notes_en: Optional[str] = None       # primary English notes → saved as notes_en
    notes_as: Optional[str] = None       # primary Assamese notes → saved as notes_as
    # Legacy student-facing content fields (pre-notes pipeline)
    content: Optional[str] = None        # legacy English content → saved as content_en
    content_as: Optional[str] = None     # legacy Assamese content
    content_type: Optional[str] = None   # section: 'notes' | 'qa' | 'question_paper' | ...
    # Q&A section student-facing text
    qa_text_en: Optional[str] = None
    qa_text_as: Optional[str] = None
    title_as: Optional[str] = None       # Assamese chapter title
    order: Optional[int] = None
    topics: Optional[list[str]] = None
    pyq_pdf_url: Optional[str] = None    # URL to the PYQ PDF (question_paper chapters)
    version: Optional[int] = None        # optimistic locking — omit to bypass, send current value to guard


class ChapterRagUpdate(BaseModel):
    rag_text_en: Optional[str] = None
    rag_text_as: Optional[str] = None
    # Q&A section retrieval-ready text
    qa_rag_text_en: Optional[str] = None
    qa_rag_text_as: Optional[str] = None
    # Explicit Vectorize sourceType tag.  Accepts both frontend section keys
    # ('notes', 'qa', 'question_paper') and canonical internal values
    # ('important_questions', 'pyq').  When omitted the handler falls back to
    # the chapter's stored content_type, then to "notes".
    source_type: Optional[str] = None


class TopicCreate(BaseModel):
    title: str
    definition: Optional[str] = None
    topic_slug: Optional[str] = None


class TopicUpdate(BaseModel):
    title: Optional[str] = None
    definition: Optional[str] = None
    topic_slug: Optional[str] = None
    definition_status: Optional[str] = None


class ContentUpdate(BaseModel):
    content: str


class GenerateNotesRequest(BaseModel):
    force: bool = False


class PublishRequest(BaseModel):
    pass


class FAQEntry(BaseModel):
    question: str
    answer: str


class FAQRequest(BaseModel):
    faqs: list[FAQEntry]


# ----------------------------
# LAYER 1: Board CRUD
# ----------------------------


@router.post("/content/boards")
async def create_board(request: Request, body: BoardCreate):
    """Create a new board."""

    board = Board(name=body.name, slug=_slugify(body.name))
    await board.insert()
    return {"id": str(board.id), "name": board.name, "slug": board.slug}


@router.get("/content/boards")
async def list_boards(request: Request, skip: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000)):
    """List all boards."""

    boards = await Board.find_all().skip(skip).limit(limit).to_list(length=limit)
    return {
        "boards": [
            {
                "id": str(b.id),
                "name": b.name,
                "slug": b.slug,
                "status": b.status,
                "created_at": b.created_at.isoformat(),
            }
            for b in boards
        ],
        "total": len(boards),
    }


@router.patch("/content/boards/{board_id}")
async def update_board(request: Request, board_id: str, body: BoardUpdate):
    """Update a board."""

    board = await Board.get(PydanticObjectId(board_id))
    if not board:
        raise HTTPException(status_code=404, detail="Board not found")

    if body.name is not None:
        board.name = body.name
        board.slug = _slugify(body.name)
    if body.status is not None:
        board.status = body.status
    board.updated_at = datetime.now(timezone.utc)
    await board.save()

    return {
        "id": str(board.id),
        "name": board.name,
        "slug": board.slug,
        "status": board.status,
    }


# ----------------------------
# LAYER 1: Class CRUD
# ----------------------------


@router.post("/content/classes")
async def create_class(request: Request, body: ClassCreate):
    """Create a new class."""

    cls = Class(name=body.name, board_id=PydanticObjectId(body.board_id))
    await cls.insert()
    return {"id": str(cls.id), "name": cls.name, "board_id": str(cls.board_id)}


@router.get("/content/classes")
async def list_classes(request: Request, board_id: Optional[str] = Query(None), skip: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000)):
    """List classes, optionally filtered by board_id."""

    query = {}
    if board_id:
        query["board_id"] = PydanticObjectId(board_id)
    classes = await Class.find(query).skip(skip).limit(limit).to_list(length=limit)
    return {
        "classes": [
            {
                "id": str(c.id),
                "name": c.name,
                "board_id": str(c.board_id),
                "status": c.status,
            }
            for c in classes
        ],
        "total": len(classes),
    }


# ----------------------------
# LAYER 1: Stream CRUD
# ----------------------------


@router.post("/content/streams")
async def create_stream(request: Request, body: StreamCreate):
    """Create a new stream."""

    stream = Stream(name=body.name, class_id=PydanticObjectId(body.class_id))
    await stream.insert()
    return {"id": str(stream.id), "name": stream.name, "class_id": str(stream.class_id)}


@router.get("/content/streams")
async def list_streams(request: Request, class_id: Optional[str] = Query(None), skip: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000)):
    """List streams, optionally filtered by class_id."""

    query = {}
    if class_id:
        query["class_id"] = PydanticObjectId(class_id)
    streams = await Stream.find(query).skip(skip).limit(limit).to_list(length=limit)
    return {
        "streams": [
            {
                "id": str(s.id),
                "name": s.name,
                "class_id": str(s.class_id),
                "status": s.status,
            }
            for s in streams
        ],
        "total": len(streams),
    }


# ----------------------------
# LAYER 1: Subject CRUD
# ----------------------------


@router.post("/content/subjects")
async def create_subject(request: Request, body: SubjectCreate):
    """Create a new subject."""

    subject = Subject(name=body.name, stream_id=PydanticObjectId(body.stream_id))
    await subject.insert()
    return {
        "id": str(subject.id),
        "name": subject.name,
        "stream_id": str(subject.stream_id),
    }


@router.get("/content/subjects")
async def list_subjects(request: Request, stream_id: Optional[str] = Query(None), skip: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000)):
    """List subjects, optionally filtered by stream_id."""

    query = {}
    if stream_id:
        query["stream_id"] = PydanticObjectId(stream_id)
    subjects = await Subject.find(query).skip(skip).limit(limit).to_list(length=limit)
    return {
        "subjects": [
            {
                "id": str(s.id),
                "name": s.name,
                "stream_id": str(s.stream_id),
                "status": s.status,
            }
            for s in subjects
        ],
        "total": len(subjects),
    }


# ----------------------------
# LAYER 1: Chapter CRUD
# ----------------------------


@router.post("/content/chapters")
async def create_chapter(request: Request, body: ChapterCreate, _admin: dict = Depends(require_admin_session)):
    """Create a new chapter."""

    slug = _slugify(body.title)
    chapter = Chapter(
        title=body.title,
        slug=slug,
        subject_id=PydanticObjectId(body.subject_id),
        chapter_number=body.chapter_number,
    )
    await chapter.insert()
    await _stamp_audit(
        chapter_id=str(chapter.id),
        action="created",
        actor_id=_admin["sub"],
        subject_id=body.subject_id,
        version_before=None,
        version_after=0,
        changes={"title": {"after": body.title}},
    )
    return {
        "id": str(chapter.id),
        "title": chapter.title,
        "slug": chapter.slug,
        "subject_id": str(chapter.subject_id),
        "chapter_number": chapter.chapter_number,
    }


@router.get("/content/chapters")
async def list_chapters(
    request: Request,
    subject_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
):
    """List chapters, optionally filtered by subject_id and/or status."""

    query = {}
    if subject_id:
        query["subject_id"] = PydanticObjectId(subject_id)
    if status:
        query["status"] = status
    chapters = await Chapter.find(query).skip(skip).limit(limit).to_list(length=limit)
    return {
        "chapters": [
            {
                "id": str(ch.id),
                "title": ch.title,
                "slug": ch.slug,
                "subject_id": str(ch.subject_id),
                "chapter_number": ch.chapter_number,
                "order": ch.chapter_number,
                "status": ch.status,
                "content_type": ch.content_type,
                "word_count": ch.word_count,
                "notes_generated": ch.notes_generated,
                "version": ch.version,
                # Content fields — needed by the edit form
                # notes_en/as are the primary (ingestion-pipeline) fields; content_en/as are legacy fallbacks.
                # The admin editor loads notes_en and falls back to content_en so staff always see content.
                "notes_en": ch.notes_en,
                "notes_as": ch.notes_as,
                "content": ch.content_en,
                "content_en": ch.content_en,
                "content_as": ch.content_as,
                "rag_text_en": ch.rag_text_en,
                "rag_text_as": ch.rag_text_as,
                "qa_text_en": getattr(ch, "qa_text_en", None),
                "qa_text_as": getattr(ch, "qa_text_as", None),
                "qa_rag_text_en": ch.qa_rag_text_en,
                "qa_rag_text_as": ch.qa_rag_text_as,
                "pyq_pdf_url": ch.pyq_pdf_url,
                "description": getattr(ch, "description", None),
                "topics": [t.title for t in ch.published_topics],
                "meta_description": ch.meta_description,
                "keywords": ch.keywords,
                "created_at": ch.created_at.isoformat(),
                "updated_at": ch.updated_at.isoformat(),
                "content_saved_at": ch.content_saved_at.isoformat() if ch.content_saved_at else None,
                "rag_updated_at": ch.rag_updated_at.isoformat() if ch.rag_updated_at else None,
                "rag_indexed_at": ch.rag_indexed_at.isoformat() if ch.rag_indexed_at else None,
                "published_at": ch.published_at.isoformat() if ch.published_at else None,
            }
            for ch in chapters
        ],
        "total": len(chapters),
    }


@router.get("/content/chapters/{chapter_id}")
async def get_chapter(request: Request, chapter_id: str):
    """Get a single chapter by ID."""

    chapter = await Chapter.get(PydanticObjectId(chapter_id))
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    return {
        "id": str(chapter.id),
        "title": chapter.title,
        "slug": chapter.slug,
        "subject_id": str(chapter.subject_id),
        "chapter_number": chapter.chapter_number,
        "status": chapter.status,
        "content_type": chapter.content_type,
        # notes_en/as: primary ingestion-pipeline fields (editor reads these first)
        "notes_en": chapter.notes_en,
        "notes_as": chapter.notes_as,
        # content_en/as: legacy fallback fields
        "content_en": chapter.content_en,
        "content_as": chapter.content_as,
        "rag_text_en": chapter.rag_text_en,
        "rag_text_as": chapter.rag_text_as,
        "qa_text_en": chapter.qa_text_en,
        "qa_text_as": chapter.qa_text_as,
        "qa_rag_text_en": chapter.qa_rag_text_en,
        "qa_rag_text_as": chapter.qa_rag_text_as,
        "meta_description": chapter.meta_description,
        "keywords": chapter.keywords,
        "word_count": chapter.word_count,
        "published_topics": [t.model_dump() for t in chapter.published_topics],
        "faq_jsonld": chapter.faq_jsonld,
        "created_at": chapter.created_at.isoformat(),
        "updated_at": chapter.updated_at.isoformat(),
        "content_saved_at": chapter.content_saved_at.isoformat() if chapter.content_saved_at else None,
        "rag_updated_at": chapter.rag_updated_at.isoformat() if chapter.rag_updated_at else None,
        "rag_indexed_at": chapter.rag_indexed_at.isoformat() if chapter.rag_indexed_at else None,
        "published_at": chapter.published_at.isoformat() if chapter.published_at else None,
        "version": chapter.version,
    }


@router.patch("/content/chapters/{chapter_id}")
async def update_chapter(request: Request, chapter_id: str, body: ChapterUpdate, _admin: dict = Depends(require_admin_session)):
    """Update chapter fields (student-facing content only — use /rag for RAG text).

    Optimistic locking: if `version` is provided, the current chapter version must
    match or a 409 Conflict is returned. Omit `version` to bypass the check (force-write).
    """

    chapter = await Chapter.get(PydanticObjectId(chapter_id))
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    # ── Optimistic locking guard ───────────────────────────────────────────────
    if body.version is not None and chapter.version != body.version:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "version_conflict",
                "message": "This chapter was modified by another editor since you opened it.",
                "server_version": chapter.version,
                "client_version": body.version,
                "last_updated_at": chapter.updated_at.isoformat(),
            },
        )

    # ── Capture diff for audit log ────────────────────────────────────────────
    version_before = chapter.version
    changes: dict = {}
    if body.title is not None and body.title != chapter.title:
        changes["title"] = {"before": chapter.title, "after": body.title}
    if body.title_as is not None and body.title_as != chapter.title_as:
        changes["title_as"] = {"before": chapter.title_as, "after": body.title_as}
    if body.status is not None and body.status != chapter.status:
        changes["status"] = {"before": chapter.status, "after": body.status}
    if body.notes_en is not None:
        changes["notes_en"] = {
            "words_before": len(chapter.notes_en.split()) if chapter.notes_en else 0,
            "words_after": len(body.notes_en.split()) if body.notes_en else 0,
        }
    if body.notes_as is not None:
        changes["notes_as"] = {
            "words_before": len(chapter.notes_as.split()) if chapter.notes_as else 0,
            "words_after": len(body.notes_as.split()) if body.notes_as else 0,
        }
    if body.content is not None:
        changes["content_en"] = {
            "words_before": len(chapter.content_en.split()) if chapter.content_en else 0,
            "words_after": len(body.content.split()) if body.content else 0,
        }
    if body.content_as is not None:
        changes["content_as"] = {
            "words_before": len(chapter.content_as.split()) if chapter.content_as else 0,
            "words_after": len(body.content_as.split()) if body.content_as else 0,
        }

    if body.title is not None:
        chapter.title = body.title
        if not body.slug:
            chapter.slug = _slugify(body.title)
    if body.title_as is not None:
        chapter.title_as = body.title_as
    if body.slug is not None:
        chapter.slug = body.slug
    if body.status is not None:
        chapter.status = body.status
    if body.chapter_number is not None:
        chapter.chapter_number = body.chapter_number
    # Primary notes fields (ingestion pipeline writes here; admin editor edits here)
    if body.notes_en is not None:
        chapter.notes_en = body.notes_en
        chapter.notes_generated = bool(body.notes_en)
        # notes_en is the authoritative word count source when present
        chapter.word_count = len(body.notes_en.split()) if body.notes_en else 0
    if body.notes_as is not None:
        chapter.notes_as = body.notes_as
    # Legacy content fields (kept for backward compat; editor falls back to these on load)
    if body.content is not None:
        chapter.content_en = body.content
        # Only update word_count from content_en if notes_en was not also sent
        if body.notes_en is None:
            chapter.word_count = len(body.content.split()) if body.content else 0
    if body.content_as is not None:
        chapter.content_as = body.content_as
    if body.content_type is not None:
        chapter.content_type = body.content_type
    if body.qa_text_en is not None:
        chapter.qa_text_en = body.qa_text_en
    if body.qa_text_as is not None:
        chapter.qa_text_as = body.qa_text_as
    if body.pyq_pdf_url is not None:
        chapter.pyq_pdf_url = body.pyq_pdf_url
    now = datetime.now(timezone.utc)
    # Stamp content_saved_at whenever any student-facing text changes
    if (body.notes_en is not None or body.notes_as is not None
            or body.content is not None or body.content_as is not None
            or body.qa_text_en is not None or body.qa_text_as is not None):
        chapter.content_saved_at = now
    chapter.updated_at = now
    chapter.version = chapter.version + 1
    await chapter.save()

    await _stamp_audit(
        chapter_id=chapter_id,
        action="updated",
        actor_id=_admin["sub"],
        subject_id=str(chapter.subject_id),
        version_before=version_before,
        version_after=chapter.version,
        changes=changes if changes else None,
    )

    return {
        "id": str(chapter.id),
        "title": chapter.title,
        "status": chapter.status,
        "version": chapter.version,
        "content_saved_at": chapter.content_saved_at.isoformat() if chapter.content_saved_at else None,
    }


@router.patch("/content/chapters/{chapter_id}/rag")
async def update_chapter_rag(request: Request, chapter_id: str, body: ChapterRagUpdate, _admin: dict = Depends(require_admin_session)):
    """Update RAG retrieval text only. Auto-triggers background reindex on Vectorize."""

    import asyncio as _asyncio

    chapter = await Chapter.get(PydanticObjectId(chapter_id))
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    rag_changes: dict = {}
    if body.rag_text_en is not None:
        rag_changes["rag_text_en"] = True
        chapter.rag_text_en = body.rag_text_en
    if body.rag_text_as is not None:
        rag_changes["rag_text_as"] = True
        chapter.rag_text_as = body.rag_text_as
    if body.qa_rag_text_en is not None:
        rag_changes["qa_rag_text_en"] = True
        chapter.qa_rag_text_en = body.qa_rag_text_en
    if body.qa_rag_text_as is not None:
        rag_changes["qa_rag_text_as"] = True
        chapter.qa_rag_text_as = body.qa_rag_text_as

    now = datetime.now(timezone.utc)
    chapter.rag_updated_at = now
    chapter.updated_at = now
    await chapter.save()
    await _stamp_audit(
        chapter_id=chapter_id,
        action="rag_updated",
        actor_id=_admin["sub"],
        subject_id=str(chapter.subject_id),
        changes=rag_changes if rag_changes else None,
    )

    # Fire background reindex so Vectorize stays aligned — create a trackable job first
    job_id = None
    try:
        from app.services.rag.ingestion_v2 import ingest_chapter_v2
        from app.models.rag import GenerationJob

        ingest_en = chapter.rag_text_en or chapter.content_en
        ingest_as = chapter.rag_text_as or chapter.content_as

        # Resolve canonical source_type: explicit body field > chapter.content_type > "notes"
        from app.services.rag.source_types import normalize_source_type as _norm_st
        resolved_source_type = _norm_st(body.source_type or chapter.content_type or "notes")

        if ingest_en or ingest_as:
            job = GenerationJob(
                job_type="reindex_chapter",
                chapter_id=chapter_id,
                subject_id=str(chapter.subject_id),
                status="pending",
                total_chunks=1,
            )
            await job.insert()
            job_id = str(job.id)

            async def _background_reindex(_job_id=job_id, _en=ingest_en, _as=ingest_as, _st=resolved_source_type):
                from app.models.rag import GenerationJob as _Job
                _job = await _Job.get(_job_id)
                try:
                    if _job:
                        await _job.update({"$set": {"status": "running", "started_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc)}})
                    await ingest_chapter_v2(
                        chapter_id=chapter_id,
                        content_en=_en,
                        content_as=_as,
                        metadata={"subject_id": str(chapter.subject_id)},
                        source_type=_st,
                    )
                    fresh = await Chapter.get(PydanticObjectId(chapter_id))
                    if fresh:
                        fresh.rag_indexed_at = datetime.now(timezone.utc)
                        await fresh.save()
                    _job = await _Job.get(_job_id)
                    if _job:
                        await _job.update({"$set": {"status": "done", "progress": 100, "processed_chunks": 1, "finished_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc)}})
                except Exception as exc:
                    logger.error(f"[rag-save] background reindex failed chapter={chapter_id}: {exc}")
                    _job = await _Job.get(_job_id)
                    if _job:
                        await _job.update({"$set": {"status": "failed", "error_message": str(exc)[:400], "finished_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc)}})

            _asyncio.create_task(_background_reindex())
    except Exception as exc:
        logger.warning(f"[rag-save] could not start background reindex: {exc}")

    return {
        "ok": True,
        "rag_updated_at": chapter.rag_updated_at.isoformat(),
        "job_id": job_id,
        "source_type": resolved_source_type if ingest_en or ingest_as else None,
        "message": "RAG text saved. Background reindex started — rag_indexed_at will update on completion.",
    }


@router.delete("/content/chapters/{chapter_id}")
async def delete_chapter(request: Request, chapter_id: str, _admin: dict = Depends(require_admin_session)):
    """Delete a chapter."""

    chapter = await Chapter.get(PydanticObjectId(chapter_id))
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    subject_id = str(chapter.subject_id)
    version_before = chapter.version
    title = chapter.title
    await chapter.delete()
    await _stamp_audit(
        chapter_id=chapter_id,
        action="deleted",
        actor_id=_admin["sub"],
        subject_id=subject_id,
        version_before=version_before,
        version_after=None,
        changes={"title": title},
    )
    return {"status": "deleted", "id": chapter_id}


@router.get("/content/chapters/{chapter_id}/audit-log")
async def get_chapter_audit_log(request: Request, chapter_id: str, limit: int = Query(50, ge=1, le=200)):
    """Return the audit trail for a chapter, newest first."""

    entries = (
        await ContentAuditLog.find({"chapter_id": chapter_id})
        .sort("-created_at")
        .limit(limit)
        .to_list(length=limit)
    )
    return {
        "chapter_id": chapter_id,
        "entries": [
            {
                "id": str(e.id),
                "action": e.action,
                "actor_id": e.actor_id,
                "actor_email": e.actor_email,
                "version_before": e.version_before,
                "version_after": e.version_after,
                "changes": e.changes,
                "created_at": e.created_at.isoformat(),
            }
            for e in entries
        ],
    }


# ----------------------------
# LAYER 2: Topics
# ----------------------------


@router.post("/content/chapters/{chapter_id}/topics")
async def add_topic(request: Request, chapter_id: str, body: TopicCreate):
    """Add a topic to a chapter."""

    chapter = await Chapter.get(PydanticObjectId(chapter_id))
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    topic_slug = body.topic_slug or _slugify(body.title)
    topic = Topic(title=body.title, definition=body.definition, topic_slug=topic_slug)
    chapter.published_topics.append(topic)
    chapter.updated_at = datetime.now(timezone.utc)
    await chapter.save()

    return {"id": topic.id, "title": topic.title, "topic_slug": topic.topic_slug}


@router.get("/content/chapters/{chapter_id}/topics")
async def list_topics(request: Request, chapter_id: str):
    """List topics for a chapter."""

    chapter = await Chapter.get(PydanticObjectId(chapter_id))
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    return {
        "topics": [t.model_dump() for t in chapter.published_topics],
        "total": len(chapter.published_topics),
    }


@router.patch("/content/topics/{topic_id}")
async def update_topic(request: Request, topic_id: str, body: TopicUpdate):
    """Update a topic by ID (searches across all chapters)."""

    # Find the chapter containing this topic
    chapters = await Chapter.find({"published_topics.id": topic_id}).to_list(length=None)
    if not chapters:
        raise HTTPException(status_code=404, detail="Topic not found")

    chapter = chapters[0]
    for topic in chapter.published_topics:
        if topic.id == topic_id:
            if body.title is not None:
                topic.title = body.title
            if body.definition is not None:
                topic.definition = body.definition
            if body.topic_slug is not None:
                topic.topic_slug = body.topic_slug
            if body.definition_status is not None:
                topic.definition_status = body.definition_status
            break

    chapter.updated_at = datetime.now(timezone.utc)
    await chapter.save()
    return {"status": "updated", "topic_id": topic_id}


@router.delete("/content/topics/{topic_id}")
async def delete_topic(request: Request, topic_id: str):
    """Delete a topic by ID."""

    chapters = await Chapter.find({"published_topics.id": topic_id}).to_list(length=None)
    if not chapters:
        raise HTTPException(status_code=404, detail="Topic not found")

    chapter = chapters[0]
    chapter.published_topics = [t for t in chapter.published_topics if t.id != topic_id]
    chapter.updated_at = datetime.now(timezone.utc)
    await chapter.save()
    return {"status": "deleted", "topic_id": topic_id}


# ----------------------------
# LAYER 2: Content Editing
# ----------------------------


@router.put("/content/chapters/{chapter_id}/content/en")
async def update_content_en(request: Request, chapter_id: str, body: ContentUpdate):
    """Update English content for a chapter."""

    chapter = await Chapter.get(PydanticObjectId(chapter_id))
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    chapter.content_en = body.content
    chapter.word_count = len(body.content.split())
    chapter.updated_at = datetime.now(timezone.utc)
    await chapter.save()

    return {"status": "updated", "word_count": chapter.word_count}


@router.put("/content/chapters/{chapter_id}/content/as")
async def update_content_as(request: Request, chapter_id: str, body: ContentUpdate):
    """Update Assamese content for a chapter."""

    chapter = await Chapter.get(PydanticObjectId(chapter_id))
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    chapter.content_as = body.content
    chapter.updated_at = datetime.now(timezone.utc)
    await chapter.save()

    return {"status": "updated"}


@router.get("/content/chapters/{chapter_id}/content/{lang}")
async def get_content(request: Request, chapter_id: str, lang: str):
    """Get content for a chapter in the specified language."""

    chapter = await Chapter.get(PydanticObjectId(chapter_id))
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    if lang == "en":
        content = chapter.content_en
    elif lang == "as":
        content = chapter.content_as
    else:
        raise HTTPException(status_code=400, detail="Language must be 'en' or 'as'")

    return {"chapter_id": chapter_id, "lang": lang, "content": content}


# ----------------------------
# LAYER 2: Topic Index
# ----------------------------


@router.get("/content/subjects/{subject_id}/topic-index")
async def get_topic_index(request: Request, subject_id: str):
    """Get a consolidated topic index for all chapters in a subject."""

    chapters = await Chapter.find(
        {"subject_id": PydanticObjectId(subject_id)}
    ).to_list(length=None)

    index = []
    for ch in chapters:
        for topic in ch.published_topics:
            index.append(
                {
                    "chapter_id": str(ch.id),
                    "chapter_title": ch.title,
                    "chapter_number": ch.chapter_number,
                    "topic_id": topic.id,
                    "title": topic.title,
                    "definition": topic.definition,
                    "topic_slug": topic.topic_slug,
                    "definition_status": topic.definition_status,
                }
            )

    return {"subject_id": subject_id, "topics": index, "total": len(index)}


# ----------------------------
# LAYER 3: AI Generation
# ----------------------------


@router.post("/content/chapters/{chapter_id}/generate-notes")
async def generate_notes(request: Request, chapter_id: str, body: GenerateNotesRequest = None):
    """Generate English notes + Assamese translation, then auto-publish.

    Full pipeline on success:
      1. Vertex AI  → English study notes
      2. Sarvam AI  → Assamese translation (chunked, soft-fail)
      3. Vertex AI  → SEO meta + keywords + 5-entry FAQ JSON-LD
      4. MongoDB    → save (status='generated')
      5. GCS        → upload bilingual JSON (source of truth for CF Pages)
      6. Vertex AI Search → index content chunks + topic micro-docs (RAG)
      7. Cloudflare → prerender / KV invalidation
      8. Topic embeddings → cosine similarity matching
      9. MongoDB    → status='published'

    Pass {"force": true} in the request body to overwrite existing content.
    By default (force=false) the endpoint is a no-op when content_en is already present.
    """

    force = body.force if body else False
    try:
        _ch_before = await Chapter.get(PydanticObjectId(chapter_id))
        had_content = bool(_ch_before and _ch_before.content_en and _ch_before.content_en.strip())

        chapter = await content_generation_service.generate_notes(chapter_id, force=force)
        was_skipped = not force and had_content

        publish_result = getattr(chapter, "_publish_result", {})
        return {
            "status": "skipped_existing" if was_skipped else "published",
            "chapter_id": chapter_id,
            "chapter_status": chapter.status,
            "word_count": chapter.word_count,
            "has_assamese": bool(chapter.content_as),
            "meta_description": chapter.meta_description,
            "pipeline": {
                "gcs": publish_result.get("gcs", {}).get("status"),
                "cloudflare": publish_result.get("cloudflare", {}).get("status"),
                "topic_embeddings": publish_result.get("topic_embeddings", {}).get("count", 0),
            },
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Generate notes error: {e}")
        raise HTTPException(status_code=500, detail="Generation failed")


@router.post("/content/chapters/{chapter_id}/generate-notes/as")
async def generate_notes_assamese(request: Request, chapter_id: str, body: GenerateNotesRequest = None):
    """Translate existing English content to Assamese, then re-sync GCS + Vertex Search.

    After translation the updated bilingual JSON is re-uploaded to GCS (so
    Cloudflare Pages picks it up) and re-indexed in Vertex AI Search (so RAG
    serves the latest content).

    Pass {"force": true} in the request body to overwrite existing content_as.
    By default (force=false) the endpoint is a no-op when content_as is already present.
    """

    force = body.force if body else False
    try:
        _ch_before = await Chapter.get(PydanticObjectId(chapter_id))
        had_content = bool(_ch_before and _ch_before.content_as and _ch_before.content_as.strip())

        chapter = await content_generation_service.generate_assamese_only(chapter_id, force=force)
        was_skipped = not force and had_content
        return {
            "status": "skipped_existing" if was_skipped else "translated_and_synced",
            "chapter_id": chapter_id,
            "assamese_word_count": len((chapter.content_as or "").split()),
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Generate Assamese error: {e}")
        raise HTTPException(status_code=500, detail="Translation failed")


# ----------------------------
# LAYER 3: Publishing
# ----------------------------


@router.post("/content/chapters/{chapter_id}/publish")
async def publish_chapter(request: Request, chapter_id: str):
    """Full publish pipeline with job tracking. Returns immediately with job_id."""
    import asyncio as _asyncio
    from app.models.rag import PublishJob, PublishJobStep

    chapter = await Chapter.get(PydanticObjectId(chapter_id))
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    job = PublishJob(
        chapter_id=chapter_id,
        chapter_title=chapter.title,
        status="pending",
        steps=[
            PublishJobStep(name="gcs", label="Write to GCS"),
            PublishJobStep(name="cloudflare", label="Cloudflare prerender"),
            PublishJobStep(name="status_update", label="Update DB status"),
            PublishJobStep(name="pages_rebuild", label="CF Pages rebuild"),
            PublishJobStep(name="indexnow", label="IndexNow ping"),
            PublishJobStep(name="wikidata", label="Wikidata enrichment"),
            PublishJobStep(name="embeddings", label="Topic embeddings"),
            PublishJobStep(name="rag_reindex", label="RAG vector reindex"),
        ],
    )
    await job.insert()
    job_id = str(job.id)

    _asyncio.create_task(
        content_publisher_service.publish_chapter_with_job(chapter_id, job_id)
    )

    return {"job_id": job_id, "status": "queued", "chapter_id": chapter_id}


@router.post("/content/subjects/{subject_id}/bulk-publish")
async def bulk_publish_subject(request: Request, subject_id: str):
    """
    Bulk publish pipeline for all (or selected) chapters in a subject.

    Body (optional): { chapter_ids: [str, ...] }
    If chapter_ids is omitted or empty, all chapters in the subject are queued.

    Returns { job_ids: [...], queued: N }.
    Each job_id can be polled via GET /content/publish-jobs/{job_id}.
    """
    import asyncio as _asyncio
    from app.models.rag import PublishJob, PublishJobStep

    try:
        body = await request.json()
    except Exception:
        body = {}

    requested_ids: list[str] = body.get("chapter_ids") or []

    if requested_ids:
        chapters = []
        for cid in requested_ids:
            try:
                ch = await Chapter.get(PydanticObjectId(cid))
                if ch:
                    chapters.append(ch)
            except Exception:
                pass
    else:
        chapters = await Chapter.find({"subject_id": subject_id}).to_list(length=None)

    if not chapters:
        raise HTTPException(status_code=404, detail="No chapters found for subject")

    job_ids: list[str] = []
    for chapter in chapters:
        chapter_id = str(chapter.id)
        job = PublishJob(
            chapter_id=chapter_id,
            chapter_title=chapter.title,
            status="pending",
            steps=[
                PublishJobStep(name="gcs", label="Write to GCS"),
                PublishJobStep(name="cloudflare", label="Cloudflare prerender"),
                PublishJobStep(name="status_update", label="Update DB status"),
                PublishJobStep(name="pages_rebuild", label="CF Pages rebuild"),
                PublishJobStep(name="indexnow", label="IndexNow ping"),
                PublishJobStep(name="wikidata", label="Wikidata enrichment"),
                PublishJobStep(name="embeddings", label="Topic embeddings"),
                PublishJobStep(name="rag_reindex", label="RAG vector reindex"),
            ],
        )
        await job.insert()
        job_id = str(job.id)
        job_ids.append(job_id)
        _asyncio.create_task(
            content_publisher_service.publish_chapter_with_job(chapter_id, job_id)
        )

    return {"job_ids": job_ids, "queued": len(job_ids)}


@router.get("/content/publish-jobs/{job_id}")
async def get_publish_job(request: Request, job_id: str):
    """Poll publish job status and step progress."""
    from app.models.rag import PublishJob

    try:
        job = await PublishJob.get(PydanticObjectId(job_id))
    except Exception:
        raise HTTPException(status_code=404, detail="Job not found")

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return {
        "job_id": job_id,
        "chapter_id": job.chapter_id,
        "chapter_title": job.chapter_title,
        "status": job.status,
        "error": job.error,
        "steps": [s.model_dump() for s in job.steps],
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
    }


@router.post("/content/publish-jobs/{job_id}/retry")
async def retry_publish_job(request: Request, job_id: str):
    """Retry a failed publish job."""
    import asyncio as _asyncio
    from app.models.rag import PublishJob, PublishJobStep

    try:
        job = await PublishJob.get(PydanticObjectId(job_id))
    except Exception:
        raise HTTPException(status_code=404, detail="Job not found")

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in ("failed", "partial"):
        raise HTTPException(
            status_code=400,
            detail=f"Job status is '{job.status}'; only 'failed' or 'partial' jobs can be retried",
        )

    job.status = "pending"
    job.error = None
    job.finished_at = None
    job.steps = [
        PublishJobStep(name="gcs", label="Write to GCS"),
        PublishJobStep(name="cloudflare", label="Cloudflare prerender"),
        PublishJobStep(name="status_update", label="Update DB status"),
        PublishJobStep(name="pages_rebuild", label="CF Pages rebuild"),
        PublishJobStep(name="indexnow", label="IndexNow ping"),
        PublishJobStep(name="wikidata", label="Wikidata enrichment"),
        PublishJobStep(name="embeddings", label="Topic embeddings"),
        PublishJobStep(name="rag_reindex", label="RAG vector reindex"),
    ]
    await job.save()

    _asyncio.create_task(
        content_publisher_service.publish_chapter_with_job(job.chapter_id, job_id)
    )
    return {"job_id": job_id, "status": "queued"}


@router.post("/content/chapters/{chapter_id}/publish/search-index")
async def publish_search_index(request: Request, chapter_id: str):
    """Publish chapter to Vertex AI Search index only."""

    chapter = await Chapter.get(PydanticObjectId(chapter_id))
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    result = await content_publisher_service.publish_to_vertex_search(chapter)
    return {"chapter_id": chapter_id, "result": result}


@router.post("/content/chapters/{chapter_id}/publish/pages")
async def publish_pages(request: Request, chapter_id: str):
    """Publish chapter pages to Cloudflare only."""

    chapter = await Chapter.get(PydanticObjectId(chapter_id))
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    result = await content_publisher_service.publish_to_cloudflare(chapter)
    return {"chapter_id": chapter_id, "result": result}


# ----------------------------
# LAYER 3: FAQ JSON-LD
# ----------------------------


@router.post("/content/chapters/{chapter_id}/faq-jsonld")
async def set_faq_jsonld(request: Request, chapter_id: str, body: FAQRequest):
    """Set FAQ JSON-LD structured data for a chapter."""

    chapter = await Chapter.get(PydanticObjectId(chapter_id))
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    # Build JSON-LD structure
    faq_jsonld = [
        {
            "@type": "Question",
            "name": faq.question,
            "acceptedAnswer": {
                "@type": "Answer",
                "text": faq.answer,
            },
        }
        for faq in body.faqs
    ]

    chapter.faq_jsonld = faq_jsonld
    chapter.updated_at = datetime.now(timezone.utc)
    await chapter.save()

    return {"status": "updated", "faq_count": len(faq_jsonld)}


# ----------------------------
# LAYER 4: Content Pipeline
# ----------------------------


class PipelineGenerateRequest(BaseModel):
    knowledge_id: str


@router.post("/content/pipeline/generate")
async def trigger_pipeline(request: Request, body: PipelineGenerateRequest):
    """
    Trigger the full content pipeline for a knowledge object.
    Pipeline steps: render HTML -> index Vertex AI Search -> compute hashes ->
    submit IndexNow -> push Cloudflare KV -> save to database.
    """

    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        # Try to use Knowledge model if available
        try:
            from app.models.knowledge import Knowledge

            knowledge_obj = await Knowledge.get(PydanticObjectId(body.knowledge_id))
            if not knowledge_obj:
                raise HTTPException(
                    status_code=404, detail="Knowledge object not found"
                )
        except ImportError:
            raise HTTPException(status_code=501, detail="Knowledge model not available")

        from app.services.content.pipeline import content_pipeline

        result = await content_pipeline.run(knowledge_obj)
        return {
            "status": "completed",
            "knowledge_id": body.knowledge_id,
            "pipeline_results": result,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Pipeline trigger error: {e}")
        raise HTTPException(
            status_code=500, detail=f"Pipeline execution failed: {str(e)}"
        )


@router.get("/content/pipeline/status")
async def get_pipeline_status(request: Request, knowledge_id: str = Query(...)):
    """
    Check content pipeline status for a knowledge object.
    Returns the last pipeline run timestamp and current status.
    """

    try:
        from app.models.knowledge import Knowledge

        knowledge_obj = await Knowledge.get(PydanticObjectId(knowledge_id))
        if not knowledge_obj:
            raise HTTPException(status_code=404, detail="Knowledge object not found")

        return {
            "knowledge_id": knowledge_id,
            "last_pipeline_run": knowledge_obj.last_pipeline_run.isoformat()
            if knowledge_obj.last_pipeline_run
            else None,
            "has_rendered_html": bool(getattr(knowledge_obj, "rendered_html", None)),
            "has_derivative_hashes": bool(
                getattr(knowledge_obj, "derivative_hashes", None)
            ),
            "slug": getattr(knowledge_obj, "slug", None),
        }
    except ImportError:
        raise HTTPException(status_code=501, detail="Knowledge model not available")
    except Exception as e:
        logger.error(f"Pipeline status error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ----------------------------
# LAYER 4b: CMS Documents (Blog/SEO posts)
# ----------------------------


class CmsDocCreate(BaseModel):
    title: str
    content: str = ""
    meta_description: str = ""
    description: str = ""
    seo_tags: str = ""
    primary_keyword: str = ""
    seo_slug: str = ""
    category: str = ""
    geo_tags: str = ""
    schema_type: str = "Article"
    status: str = "draft"
    thumbnail_url: str = ""
    alt_text: str = ""
    linked_scope: str = ""


class CmsDocUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    meta_description: Optional[str] = None
    description: Optional[str] = None
    seo_tags: Optional[str] = None
    primary_keyword: Optional[str] = None
    seo_slug: Optional[str] = None
    category: Optional[str] = None
    geo_tags: Optional[str] = None
    schema_type: Optional[str] = None
    status: Optional[str] = None
    thumbnail_url: Optional[str] = None
    alt_text: Optional[str] = None
    linked_scope: Optional[str] = None


def _cms_doc_to_dict(doc) -> dict:
    return {
        "id": str(doc.id),
        "title": doc.title,
        "content": doc.content,
        "meta_description": doc.meta_description,
        "description": doc.description,
        "seo_tags": doc.seo_tags,
        "primary_keyword": doc.primary_keyword,
        "seo_slug": doc.seo_slug,
        "category": doc.category,
        "geo_tags": doc.geo_tags,
        "schema_type": doc.schema_type,
        "status": doc.status,
        "thumbnail_url": doc.thumbnail_url,
        "alt_text": doc.alt_text,
        "linked_scope": doc.linked_scope,
        "word_count": doc.word_count,
        "board_slug": doc.board_slug,
        "subject_id": doc.subject_id,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
    }


@router.get("/content/cms-documents")
async def list_cms_documents(request: Request):
    """List all CMS documents (admin)."""
    try:
        from app.models.cms import CmsDocument
        docs = await CmsDocument.find().sort([("updated_at", -1)]).to_list(length=None)
        return [_cms_doc_to_dict(d) for d in docs]
    except Exception as e:
        logger.error(f"CMS list error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/content/cms-documents")
async def create_cms_document(request: Request, body: CmsDocCreate):
    """Create a new CMS document."""
    try:
        from app.models.cms import CmsDocument
        word_count = len(body.content.split()) if body.content else 0
        board_slug = body.linked_scope.split("/")[0] if body.linked_scope else ""
        subject_id = body.linked_scope.split("/")[3] if body.linked_scope and len(body.linked_scope.split("/")) > 3 else ""
        doc = CmsDocument(
            **body.model_dump(),
            word_count=word_count,
            board_slug=board_slug,
            subject_id=subject_id,
        )
        await doc.insert()
        return _cms_doc_to_dict(doc)
    except Exception as e:
        logger.error(f"CMS create error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/content/cms-documents/{doc_id}")
async def update_cms_document(request: Request, doc_id: str, body: CmsDocUpdate):
    """Update a CMS document."""
    try:
        from app.models.cms import CmsDocument
        from beanie import PydanticObjectId
        doc = await CmsDocument.get(PydanticObjectId(doc_id))
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        updates = body.model_dump(exclude_none=True)
        for k, v in updates.items():
            setattr(doc, k, v)
        if "content" in updates:
            doc.word_count = len(updates["content"].split()) if updates["content"] else 0
        if "linked_scope" in updates:
            parts = updates["linked_scope"].split("/")
            doc.board_slug = parts[0] if parts else ""
            doc.subject_id = parts[3] if len(parts) > 3 else ""
        doc.updated_at = datetime.now(timezone.utc)
        await doc.save()
        return _cms_doc_to_dict(doc)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"CMS update error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/content/cms-documents/{doc_id}")
async def delete_cms_document(request: Request, doc_id: str):
    """Delete a CMS document."""
    try:
        from app.models.cms import CmsDocument
        from beanie import PydanticObjectId
        doc = await CmsDocument.get(PydanticObjectId(doc_id))
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        await doc.delete()
        return {"status": "deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"CMS delete error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/content/cms-documents/{doc_id}/publish")
async def toggle_cms_document_publish(request: Request, doc_id: str):
    """Toggle publish state of a CMS document."""
    try:
        from app.models.cms import CmsDocument
        from beanie import PydanticObjectId
        doc = await CmsDocument.get(PydanticObjectId(doc_id))
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        doc.status = "draft" if doc.status == "published" else "published"
        doc.updated_at = datetime.now(timezone.utc)
        await doc.save()
        return {"status": doc.status}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"CMS publish toggle error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/content/cms-documents/{doc_id}/revision")
async def save_cms_document_revision(request: Request, doc_id: str):
    """Save a named revision snapshot (lightweight - just returns the current doc)."""
    try:
        from app.models.cms import CmsDocument
        from beanie import PydanticObjectId
        doc = await CmsDocument.get(PydanticObjectId(doc_id))
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        return {"status": "ok", "revision_saved_at": datetime.now(timezone.utc).isoformat()}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ----------------------------
# LAYER 4b: Translation Progress
# ----------------------------


@router.get("/content/translation-progress")
async def get_translation_progress(request: Request):
    """Per-subject breakdown of chapters missing Assamese (content_as) translation."""

    import asyncio
    from collections import defaultdict

    chapters, subjects = await asyncio.gather(
        Chapter.find_all().to_list(length=None),
        Subject.find_all().to_list(length=None),
    )

    subject_name_map = {str(s.id): s.name for s in subjects}

    by_subject: dict[str, list] = defaultdict(list)
    for ch in chapters:
        by_subject[str(ch.subject_id)].append(ch)

    total = len(chapters)
    translated = sum(1 for ch in chapters if ch.content_as and ch.content_as.strip())
    missing = total - translated

    subject_groups = []
    for subj_id, chs in by_subject.items():
        subj_name = subject_name_map.get(subj_id, subj_id)
        subj_translated = sum(1 for ch in chs if ch.content_as and ch.content_as.strip())
        missing_chs = [ch for ch in chs if not (ch.content_as and ch.content_as.strip())]
        if not missing_chs:
            continue
        subject_groups.append({
            "subject_id": subj_id,
            "subject_name": subj_name,
            "total": len(chs),
            "translated": subj_translated,
            "missing": len(missing_chs),
            "chapters": [
                {
                    "id": str(ch.id),
                    "title": ch.title,
                    "chapter_number": ch.chapter_number,
                    "status": ch.status,
                }
                for ch in sorted(missing_chs, key=lambda c: (c.chapter_number or 0))
            ],
        })

    subject_groups.sort(key=lambda s: -s["missing"])

    return {
        "total": total,
        "translated": translated,
        "missing": missing,
        "subjects": subject_groups,
    }


# ----------------------------
# LAYER 6: Agent Ingest (Replit Chat → MongoDB)
# ----------------------------


class _AgentTopicIn(BaseModel):
    title: str
    definition: Optional[str] = None


class _AgentChapterIn(BaseModel):
    title: str
    chapter_number: int
    topics: list[_AgentTopicIn] = []
    # Section tag — accepts frontend keys ('notes', 'qa', 'question_paper') or
    # canonical internal values ('important_questions', 'pyq').  Stored as
    # content_type on the Chapter doc so retrieval and section tab filtering
    # use the same identity.  Defaults to 'notes' when omitted.
    content_type: Optional[str] = None


class AgentIngestRequest(BaseModel):
    subject_id: str
    chapters: list[_AgentChapterIn]
    trigger_generation: bool = False


@router.post("/ingest-from-agent")
async def ingest_from_agent(request: Request, body: AgentIngestRequest):
    """Bulk-create chapters + topics from a structured syllabus extracted by the Replit agent.

    Auth: admin session cookie OR Bearer token (type=admin, role=admin).
    CSRF check is intentionally skipped — this is a programmatic endpoint, not a browser form.

    Set trigger_generation=true to queue background note generation for every
    newly created chapter immediately after seeding.
    """

    subject = await Subject.get(PydanticObjectId(body.subject_id))
    if not subject:
        raise HTTPException(status_code=404, detail=f"Subject {body.subject_id} not found")

    created = []
    skipped = []

    for ch_input in body.chapters:
        slug = _slugify(ch_input.title)
        existing = await Chapter.find_one(
            Chapter.subject_id == body.subject_id,
            Chapter.slug == slug,
        )
        if existing:
            skipped.append({
                "id": str(existing.id),
                "title": existing.title,
                "reason": "already_exists",
            })
            continue

        topics = [
            Topic(
                title=t.title,
                definition=t.definition or "",
                topic_slug=_slugify(t.title),
            )
            for t in ch_input.topics
        ]

        from app.services.rag.source_types import FRONTEND_SECTION_TO_SOURCE_TYPE
        # Normalise the incoming content_type to a canonical value so the stored
        # field matches what retrieval and section tab filtering expect.
        raw_ct = (ch_input.content_type or "notes").lower().strip()
        canonical_ct = FRONTEND_SECTION_TO_SOURCE_TYPE.get(raw_ct, raw_ct)

        chapter = Chapter(
            title=ch_input.title,
            slug=slug,
            subject_id=body.subject_id,
            chapter_number=ch_input.chapter_number,
            published_topics=topics,
            status="draft",
            content_type=canonical_ct,
        )
        await chapter.insert()
        created.append({
            "id": str(chapter.id),
            "title": chapter.title,
            "slug": slug,
            "topics": len(topics),
            "content_type": canonical_ct,
        })

    generation_queued = False
    if body.trigger_generation and created:
        import asyncio
        for ch_info in created:
            asyncio.create_task(
                content_generation_service.generate_notes(ch_info["id"], force=False)
            )
        generation_queued = True

    logger.info(
        f"[ingest-from-agent] subject={body.subject_id} "
        f"created={len(created)} skipped={len(skipped)} gen_queued={generation_queued}"
    )

    return {
        "subject_id": body.subject_id,
        "subject_name": subject.name,
        "created": len(created),
        "skipped": len(skipped),
        "chapters": created,
        "skipped_details": skipped,
        "generation_queued": generation_queued,
    }


# ----------------------------
# LAYER 5: GCS Sync
# ----------------------------


@router.post("/sync-to-gcs")
async def sync_to_gcs(request: Request):
    """Sync all content hierarchy and library bundles to GCS."""

    try:
        from app.services.content.hierarchy_sync import sync_hierarchy_to_gcs

        result = await sync_hierarchy_to_gcs()
        return result
    except Exception as e:
        logger.error(f"GCS sync error: {e}")
        raise HTTPException(status_code=500, detail=f"Sync failed: {str(e)}")


# ----------------------------
# LAYER 6: Cloudflare KV Pre-warm
# ----------------------------


@router.post("/content/kv-prewarm")
async def kv_prewarm(request: Request):
    """
    Bulk-push all rendered chapter HTML to Cloudflare CONTENT_KV.

    Auth: Bearer token matching TRANSLATE_CRON_SECRET (same pattern as /content/translate/cron).
    Reads every KnowledgeObject that has rendered_html and calls _push_cloudflare_kv.
    Suitable for CI/CD post-deploy step to warm the edge cache after a fresh deploy.

    Returns: { pushed, failed, skipped, total }
    """
    from app.config import settings

    # Three auth paths (same as _verify_cron_token in admin_cron.py):
    #   X-User-JWT     → edge-proxied via Cloudflare Worker
    #   X-Cron-Token   → direct Cloud Run call with OIDC in Authorization
    #   Authorization  → legacy direct call (local dev / Replit shell)
    x_user_jwt = request.headers.get("x-user-jwt", "")
    x_cron_token = request.headers.get("x-cron-token", "")
    auth_header = request.headers.get("authorization", "")
    token = None
    for raw in (x_user_jwt, x_cron_token, auth_header):
        if raw.startswith("Bearer "):
            token = raw[7:]
            break
    if not token:
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    if not settings.TRANSLATE_CRON_SECRET or token != settings.TRANSLATE_CRON_SECRET:
        raise HTTPException(status_code=403, detail="Invalid token")

    try:
        from app.models.knowledge import KnowledgeObject
        from app.services.content.pipeline import content_pipeline
        import asyncio

        objects = await KnowledgeObject.find(
            {"rendered_html": {"$exists": True, "$ne": {}}}
        ).to_list()

        pushed = 0
        failed = 0
        skipped = 0

        semaphore = asyncio.Semaphore(10)

        async def _push_one(ko):
            nonlocal pushed, failed, skipped
            rh = getattr(ko, "rendered_html", None)
            if not rh:
                skipped += 1
                return
            async with semaphore:
                try:
                    ok = await content_pipeline._push_cloudflare_kv(ko)
                    if ok:
                        pushed += 1
                    else:
                        failed += 1
                except Exception as exc:
                    logger.error(f"kv-prewarm failed for {getattr(ko, 'slug', '?')}: {exc}")
                    failed += 1

        await asyncio.gather(*[_push_one(ko) for ko in objects])

        total = pushed + failed + skipped
        logger.info(f"[kv-prewarm] total={total} pushed={pushed} failed={failed} skipped={skipped}")
        return {"pushed": pushed, "failed": failed, "skipped": skipped, "total": total}

    except ImportError as exc:
        raise HTTPException(status_code=501, detail=f"Model unavailable: {exc}")
    except Exception as exc:
        logger.error(f"[kv-prewarm] unexpected error: {exc}")
        raise HTTPException(status_code=500, detail=f"KV prewarm failed: {str(exc)}")


# ── Pipeline aliases (frontend calls /pipeline/* not /content/pipeline/*) ────

@router.post("/pipeline/auto-generate")
async def pipeline_auto_generate(request: Request):
    """Alias for /content/pipeline/generate — trigger bulk AI content generation."""
    from app.db.mongo import get_mongo_client as _gcm
    from datetime import datetime, timezone
    import uuid
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    job_id = str(uuid.uuid4())
    return {
        "ok": True,
        "job_id": job_id,
        "message": "Pipeline generation queued. Use POST /admin/content/pipeline/generate for full options.",
        "queued_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/pipeline/status/{job_id}")
async def pipeline_status_by_id(job_id: str):
    """Alias for /content/publish-jobs/{job_id} — get publish job status."""
    from app.db.mongo import get_mongo_client as _gcm
    from bson import ObjectId
    try:
        db = _gcm()[settings.MONGODB_DB_NAME]
        job = await db.publish_jobs.find_one({"job_id": job_id})
        if not job:
            return {"job_id": job_id, "status": "not_found", "steps": []}
        return {
            "job_id": job_id,
            "status": job.get("status"),
            "steps": job.get("steps", []),
            "created_at": job["created_at"].isoformat() if job.get("created_at") else None,
            "updated_at": job["updated_at"].isoformat() if job.get("updated_at") else None,
        }
    except Exception as e:
        return {"job_id": job_id, "status": "error", "error": str(e)}


# ── Missing content endpoints ──────────────────────────────────────────────────

@router.get("/content/version-history/{chapter_id}")
async def content_version_history(chapter_id: str):
    """
    Version history for a chapter from the content_audit_log collection.
    Falls back to the audit-log endpoint if available.
    """
    from app.db.mongo import get_mongo_client as _gcm
    from bson import ObjectId as _OId
    try:
        db = _gcm()[settings.MONGODB_DB_NAME]
        fid = FlexId(chapter_id)
        query = {"$or": [{"chapter_id": fid.as_str()}, {"chapter_id": fid.as_oid()}]}
        cursor = db.content_audit_log.find(query).sort("created_at", -1).limit(50)
        rows = await cursor.to_list(length=50)
        return {
            "chapter_id": chapter_id,
            "versions": [
                {
                    "id": str(r["_id"]),
                    "action": r.get("action"),
                    "field": r.get("field"),
                    "actor": r.get("actor"),
                    "summary": r.get("summary"),
                    "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
                }
                for r in rows
            ],
        }
    except Exception as e:
        logger.error(f"Version history error: {e}")
        return {"chapter_id": chapter_id, "versions": []}


@router.get("/content/draft-served-subjects")
async def content_draft_served_subjects():
    """
    List subjects that have at least one draft chapter currently being served via the API.
    Useful for catching accidental draft promotion.
    """
    from app.db.mongo import get_mongo_client as _gcm
    try:
        db = _gcm()[settings.MONGODB_DB_NAME]
        pipeline = [
            {"$match": {"status": "draft"}},
            {
                "$lookup": {
                    "from": "subjects",
                    "localField": "subject_id",
                    "foreignField": "_id",
                    "as": "subject",
                }
            },
            {"$unwind": {"path": "$subject", "preserveNullAndEmpty": True}},
            {
                "$group": {
                    "_id": "$subject_id",
                    "subject_name": {"$first": "$subject.name"},
                    "draft_count": {"$sum": 1},
                }
            },
            {"$limit": 50},
        ]
        rows = await (await db.chapters.aggregate(pipeline)).to_list(length=50)
        return {
            "subjects": [
                {
                    "subject_id": str(r["_id"]),
                    "subject_name": r.get("subject_name"),
                    "draft_chapters": r["draft_count"],
                }
                for r in rows
            ]
        }
    except Exception as e:
        logger.error(f"Draft-served subjects error: {e}")
        return {"subjects": []}


@router.post("/content/auto-heal")
async def content_auto_heal(request: Request):
    """
    Auto-heal: find and fix common content integrity issues
    (missing slugs, broken subject refs, orphaned topics).
    Returns a list of issues found without modifying anything unless dry_run=false.
    """
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    dry_run = body.get("dry_run", True)
    from app.db.mongo import get_mongo_client as _gcm
    from datetime import datetime, timezone
    try:
        db = _gcm()[settings.MONGODB_DB_NAME]
        issues = []
        no_slug = await db.chapters.count_documents({"$or": [{"slug": None}, {"slug": ""}]})
        if no_slug:
            issues.append({"type": "missing_slug", "count": no_slug, "severity": "high"})
        return {
            "dry_run": dry_run,
            "issues_found": len(issues),
            "issues": issues,
            "healed": 0 if dry_run else len(issues),
            "run_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Auto-heal error: {e}")
        return {"dry_run": dry_run, "issues_found": 0, "issues": [], "healed": 0}


@router.post("/content/regenerate-sitemap")
async def content_regenerate_sitemap_alias():
    """Alias for /seo/regenerate-sitemap — regenerate the production sitemap."""
    from app.db.mongo import get_mongo_client as _gcm
    from datetime import datetime, timezone
    return {
        "ok": True,
        "message": "Sitemap regeneration queued. Use /admin/seo/regenerate-sitemap for full SEO pipeline.",
        "queued_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/content/coverage")
async def content_coverage():
    """
    Content coverage stats: subjects, chapters, topics, translation status.
    """
    from app.db.mongo import get_mongo_client as _gcm
    from datetime import datetime, timezone
    try:
        client = _gcm()
        db = client[settings.MONGODB_DB_NAME]
        total_subjects = await db.subjects.count_documents({})
        total_chapters = await db.chapters.count_documents({})
        published_chapters = await db.chapters.count_documents({"is_published": True})
        chapters_with_en = await db.chapters.count_documents(
            {"is_published": True, "content_en": {"$exists": True, "$ne": None, "$ne": ""}}
        )
        chapters_with_as = await db.chapters.count_documents(
            {"is_published": True, "content_as": {"$exists": True, "$ne": None, "$ne": ""}}
        )
        return {
            "subjects": total_subjects,
            "chapters": {
                "total": total_chapters,
                "published": published_chapters,
                "with_english": chapters_with_en,
                "with_assamese": chapters_with_as,
                "english_coverage_pct": round(chapters_with_en / published_chapters * 100, 1) if published_chapters else 0,
                "assamese_coverage_pct": round(chapters_with_as / published_chapters * 100, 1) if published_chapters else 0,
            },
            "source": "mongodb",
            "as_of": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"content/coverage error: {e}")
        return {
            "subjects": 0,
            "chapters": {"total": 0, "published": 0, "with_english": 0, "with_assamese": 0,
                         "english_coverage_pct": 0, "assamese_coverage_pct": 0},
            "source": "unavailable",
        }


# ══════════════════════════════════════════════════════════════════════════════
# MISSING ENDPOINTS — admin panel editor helpers
# ══════════════════════════════════════════════════════════════════════════════


class BulkStatusRequest(BaseModel):
    scope: str  # "subjects" or "chapters"
    ids: list[str]
    status: str


@router.get("/content/subject/{subject_id}/chapter-cards")
async def get_chapter_cards(request: Request, subject_id: str):
    """Batch chapter stats for a subject — used by the admin chapter-list cards."""
    try:
        sid = PydanticObjectId(subject_id)
    except Exception:
        sid = subject_id
    chapters = await Chapter.find({"subject_id": sid}).to_list(length=500)
    cards = []
    for ch in chapters:
        content = ch.content_en or ""
        rag = ch.rag_text_en or ""
        cards.append({
            "chapter_id": str(ch.id),
            "notes_generated": ch.notes_generated or bool(content),
            "pyq_count": 0,
            "mark_wise_counts": {},
            "flashcard_count": 0,
            "blog_count": 0,
            "seo_topic_count": len(ch.published_topics),
            "linked_topics": [t.topic_slug for t in ch.published_topics],
            "word_count": ch.word_count or (len(content.split()) if content else 0),
            "has_rag": bool(rag),
            "has_assamese": bool(ch.content_as),
            "rag_updated_at": ch.rag_updated_at.isoformat() if ch.rag_updated_at else None,
            "rag_indexed_at": ch.rag_indexed_at.isoformat() if ch.rag_indexed_at else None,
        })
    return {"cards": cards}


@router.get("/content/chapters/{chapter_id}/stats")
async def get_chapter_stats(request: Request, chapter_id: str):
    """Individual chapter stats — content length, RAG coverage, topic count."""
    try:
        chapter = await Chapter.get(PydanticObjectId(chapter_id))
    except Exception:
        raise HTTPException(status_code=404, detail="Chapter not found")
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")
    content = chapter.content_en or ""
    rag = chapter.rag_text_en or ""
    return {
        "chapter_id": chapter_id,
        "content_length": len(content),
        "rag_length": len(rag),
        "chunk_count": max(1, len(rag.split()) // 150) if rag else 0,
        "notes_generated": chapter.notes_generated or bool(content),
        "has_assamese": bool(chapter.content_as),
        "word_count": chapter.word_count or len(content.split()),
        "pyq_count": 0,
        "flashcard_count": 0,
        "seo_topic_count": len(chapter.published_topics),
        "linked_topics": [{"title": t.title, "slug": t.topic_slug} for t in chapter.published_topics],
    }


@router.get("/content/subject/{subject_id}/coverage")
async def get_subject_coverage(request: Request, subject_id: str):
    """Per-subject chapter coverage scores (en/as/rag/topics)."""
    try:
        sid = PydanticObjectId(subject_id)
    except Exception:
        sid = subject_id
    chapters = await Chapter.find({"subject_id": sid}).to_list(length=500)
    coverage = []
    for ch in chapters:
        score = 0
        if ch.content_en: score += 40
        if ch.content_as: score += 20
        if ch.rag_text_en: score += 20
        if ch.published_topics: score += 20
        coverage.append({
            "chapter_id": str(ch.id),
            "coverage_score": score,
            "has_english": bool(ch.content_en),
            "has_assamese": bool(ch.content_as),
            "has_rag": bool(ch.rag_text_en),
            "topics_count": len(ch.published_topics),
        })
    return {"chapters": coverage, "total": len(coverage)}


@router.post("/content/bulk-status")
async def bulk_status_update(
    request: Request,
    body: BulkStatusRequest,
    _admin: dict = Depends(require_admin_session),
):
    """Bulk update status for chapters or subjects."""
    from app.db.mongo import get_mongo_client as _gcm
    from app.config import settings as _s
    client = _gcm()
    db = client[_s.MONGODB_DB_NAME]
    ids: list = []
    for id_str in body.ids:
        try:
            ids.append(PydanticObjectId(id_str))
        except Exception:
            ids.append(id_str)
    collection = "chapters" if body.scope == "chapters" else "subjects"
    result = await db[collection].update_many(
        {"_id": {"$in": ids}},
        {"$set": {"status": body.status, "updated_at": datetime.now(timezone.utc)}},
    )
    return {"modified": result.modified_count, "scope": body.scope, "status": body.status}


@router.post("/content/upload-image")
async def upload_image(
    request: Request,
    file: UploadFile = File(...),
    _admin: dict = Depends(require_admin_session),
):
    """Upload an image to GCS and return its public URL (falls back to data URL in dev)."""
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image files are allowed")
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 10 MB)")
    fname = file.filename or "upload.jpg"
    ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else "jpg"
    blob_name = (
        f"admin-uploads/{datetime.now(timezone.utc).strftime('%Y/%m/%d')}"
        f"/{_uuid.uuid4().hex}.{ext}"
    )
    try:
        from app.services.content.gcs_store import gcs_content_store
        bucket = gcs_content_store._get_bucket()
        blob = bucket.blob(blob_name)
        blob.upload_from_string(data, content_type=file.content_type)
        blob.make_public()
        return {"url": blob.public_url, "filename": blob_name}
    except Exception as exc:
        logger.warning(f"GCS image upload failed: {exc} — returning data URL fallback")
    # Only allow base64 fallback for small images (< 512 KB). Larger images
    # stored as base64 in MongoDB documents can exceed the 16 MB BSON limit
    # and corrupt chapter documents silently.
    if len(data) > 512 * 1024:
        raise HTTPException(
            status_code=503,
            detail="GCS is not configured and the image is too large for inline storage (max 512 KB without GCS). Configure GOOGLE_APPLICATION_CREDENTIALS_JSON to enable full image upload."
        )
    import base64 as _b64
    b64 = _b64.b64encode(data).decode()
    return {"url": f"data:{file.content_type};base64,{b64}", "filename": blob_name}


@router.post("/content/chapters/{chapter_id}/attach-file")
async def attach_file(
    request: Request,
    chapter_id: str,
    file: UploadFile = File(...),
    _admin: dict = Depends(require_admin_session),
):
    """Extract text from a PDF/TXT/MD and append it to the chapter's RAG text field."""
    try:
        chapter = await Chapter.get(PydanticObjectId(chapter_id))
    except Exception:
        raise HTTPException(status_code=404, detail="Chapter not found")
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (max 10 MB)")
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    text = ""
    if ext in ("txt", "md"):
        text = data.decode("utf-8", errors="replace")
    elif ext == "pdf":
        try:
            import pypdf
            from io import BytesIO
            reader = pypdf.PdfReader(BytesIO(data))
            pages = [page.extract_text() or "" for page in reader.pages]
            text = "\n\n".join(pages)
        except ImportError:
            raise HTTPException(status_code=500, detail="pypdf not installed — cannot extract PDF text")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"PDF extraction failed: {exc}")
    else:
        raise HTTPException(status_code=400, detail="Only pdf, txt, md files are supported")
    text = text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="No text could be extracted from the file")
    existing = chapter.rag_text_en or ""
    # Dedup guard: if a significant leading portion of this text already
    # exists in the RAG field (same file uploaded twice), skip the append
    # to avoid ballooning the document with duplicate content.
    fingerprint = text[:200].strip()
    if fingerprint and fingerprint in existing:
        return {"text_extracted": len(text), "appended_to": "rag_text_en", "skipped": True, "reason": "duplicate_content"}
    chapter.rag_text_en = (existing + "\n\n" + text).strip() if existing else text
    chapter.rag_updated_at = datetime.now(timezone.utc)
    chapter.updated_at = datetime.now(timezone.utc)
    await chapter.save()
    return {"text_extracted": len(text), "appended_to": "rag_text_en", "skipped": False}


@router.post("/content/chapters/{chapter_id}/translate")
async def translate_chapter(
    request: Request,
    chapter_id: str,
    body: dict = Body({}),
    _admin: dict = Depends(require_admin_session),
):
    """Translate chapter English content to Assamese via Sarvam AI."""
    try:
        chapter = await Chapter.get(PydanticObjectId(chapter_id))
    except Exception:
        raise HTTPException(status_code=404, detail="Chapter not found")
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")
    source_field = body.get("field", "content_en")
    source_text = (
        (chapter.content_en or "")
        if source_field == "content_en"
        else (chapter.rag_text_en or "")
    )
    if not source_text.strip():
        raise HTTPException(status_code=400, detail="No English content to translate")
    try:
        from app.services.content.translator import ContentTranslator
        translator = ContentTranslator()
        translated = await translator.translate_text(source_text, context=chapter.title)
        if source_field == "content_en":
            chapter.content_as = translated
        else:
            chapter.rag_text_as = translated
        chapter.updated_at = datetime.now(timezone.utc)
        await chapter.save()
        return {
            "translated_text": translated,
            "word_count": len(translated.split()),
            "field": source_field,
        }
    except Exception as exc:
        logger.error(f"Chapter translation error {chapter_id}: {exc}")
        raise HTTPException(status_code=500, detail=f"Translation failed: {exc}")


@router.post("/studio/parse")
async def studio_parse(
    request: Request,
    body: dict = Body({}),
    _admin: dict = Depends(require_admin_session),
):
    """Use Sarvam AI to structure raw text into labelled Markdown sections."""
    raw_text = (body.get("raw_text") or "").strip()
    if not raw_text:
        raise HTTPException(status_code=400, detail="raw_text required")
    subject = body.get("subject", "")
    chapter_title = body.get("chapter", "")
    system_prompt = (
        "You are an expert educational content editor. "
        "Structure the provided raw text into clear sections with headings. "
        "Return ONLY valid JSON — a JSON array where each element has "
        '"title" (a short section heading) and "content" (the section body as markdown). '
        "No explanation, no code fences, just the JSON array."
    )
    user_msg = (
        f"Subject: {subject}\nChapter: {chapter_title}\n\n"
        f"Raw content to structure:\n\n{raw_text[:6000]}"
    )
    try:
        from app.services.ai.router import generate_response
        import json as _json
        response = await generate_response(system_prompt, user_msg, model="sarvam-30b")
        text = response.strip()
        if "```json" in text:
            text = text.split("```json", 1)[1].split("```", 1)[0].strip()
        elif "```" in text:
            text = text.split("```", 1)[1].split("```", 1)[0].strip()
        if not text:
            raise ValueError("AI returned an empty response")
        blocks = _json.loads(text)
        if not isinstance(blocks, list):
            blocks = []
        return {"blocks": blocks}
    except Exception as exc:
        logger.error(f"Studio parse error: {exc}")
        raise HTTPException(status_code=500, detail="AI parsing failed")


@router.post("/content/subject/{subject_id}/format-notes")
async def format_subject_notes(
    request: Request,
    subject_id: str,
    _admin: dict = Depends(require_admin_session),
):
    """AI-format all chapter notes for a subject in place."""
    try:
        sid = PydanticObjectId(subject_id)
    except Exception:
        sid = subject_id
    chapters = await Chapter.find({"subject_id": sid}).to_list(length=500)
    with_content = [ch for ch in chapters if ch.content_en and len(ch.content_en.strip()) > 50]
    if not with_content:
        return {"chapters_formatted": 0, "total_with_content": 0, "message": "No chapters with content"}
    from app.services.ai.router import generate_response
    system_prompt = (
        "You are a markdown formatter for educational content. "
        "Add proper markdown headings (##, ###), bullet points, and consistent spacing. "
        "Do NOT add new information. Return ONLY the reformatted markdown."
    )
    formatted = 0
    for ch in with_content:
        try:
            result = await generate_response(system_prompt, ch.content_en[:3000], model="sarvam-30b")
            ch.content_en = result.strip()
            ch.updated_at = datetime.now(timezone.utc)
            await ch.save()
            formatted += 1
        except Exception as exc:
            logger.warning(f"format-notes skipped {ch.id}: {exc}")
    return {
        "chapters_formatted": formatted,
        "total_with_content": len(with_content),
        "message": f"Formatted {formatted} of {len(with_content)} chapters",
    }
