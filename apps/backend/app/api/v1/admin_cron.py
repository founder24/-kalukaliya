"""
Admin Cron Endpoints — triggered by CI/cron jobs, NOT browser sessions.

Auth: Bearer token (TRANSLATE_CRON_SECRET) — no session cookie required.
These routes must NOT be mixed with session-protected admin routes.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Admin Cron"])

# How often (in chapters processed) to flush progress to MongoDB
_MONGO_FLUSH_EVERY = 5


def _verify_cron_token(request: Request) -> None:
    """Validate Bearer token against TRANSLATE_CRON_SECRET."""
    from app.config import settings

    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")

    token = auth_header[7:]
    expected = settings.TRANSLATE_CRON_SECRET
    if not expected or token != expected:
        raise HTTPException(status_code=403, detail="Invalid cron token")


@router.post("/cron/expire-subscriptions")
async def cron_expire_subscriptions(request: Request):
    """Downgrade users whose paid subscription period has lapsed.

    Finds every non-free user where current_period_end is in the past
    (or unset and the account is older than 30 days) and sets them back
    to free.  Safe to run multiple times — already-free users are skipped.

    Auth: Bearer {TRANSLATE_CRON_SECRET}

    Returns:
        { "expired": N, "skipped": N, "errors": [...] }
    """
    _verify_cron_token(request)

    from app.db.mongo import get_mongo_client
    from app.config import settings

    now = datetime.now(timezone.utc)
    client = get_mongo_client()
    db = client[settings.MONGODB_DB_NAME]

    # Find paid users whose period has ended
    cursor = db.users.find(
        {
            "subscription_tier": {"$nin": ["free", None]},
            "$or": [
                # Period explicitly set and expired
                {"current_period_end": {"$lt": now}},
                # Legacy: paid but period never recorded — treat as expired if
                # cancel_at_period_end is True (manually cancelled via webhook)
                {
                    "current_period_end": {"$exists": False},
                    "cancel_at_period_end": True,
                },
            ],
        },
        {"_id": 1, "email": 1, "subscription_tier": 1, "current_period_end": 1},
    )

    expired = 0
    skipped = 0
    errors = []

    async for doc in cursor:
        user_id = doc["_id"]
        try:
            result = await db.users.update_one(
                {"_id": user_id},
                {
                    "$set": {
                        "subscription_tier": "free",
                        "subscription_status": "cancelled",
                        "cancel_at_period_end": False,
                    }
                },
            )
            if result.modified_count:
                expired += 1
                logger.info(
                    f"Subscription expired → free: {doc.get('email', user_id)} "
                    f"(period_end={doc.get('current_period_end')})"
                )
            else:
                skipped += 1
        except Exception as e:
            errors.append({"user_id": str(user_id), "error": str(e)})
            logger.error(f"Failed to expire subscription for {user_id}: {e}")

    logger.info(
        f"expire-subscriptions complete: expired={expired}, skipped={skipped}, errors={len(errors)}"
    )
    return {"expired": expired, "skipped": skipped, "errors": errors}


@router.post("/cron/translate")
async def cron_translate(request: Request):
    """
    Cron/CI-triggered bulk translation.
    Auth: Bearer {TRANSLATE_CRON_SECRET}
    Body (optional JSON): { board?, subject?, limit? }
    """
    _verify_cron_token(request)

    from app.services.content.translator import ContentTranslator

    body = {}
    try:
        body = await request.json()
    except Exception:
        pass

    translator = ContentTranslator()

    request.app.state.translation_status = {
        "running": True,
        "total": 0,
        "completed": 0,
        "failed": 0,
        "current_slug": "",
        "errors": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    result = await translator.bulk_translate(
        request.app.state,
        board=body.get("board"),
        subject=body.get("subject"),
        limit=body.get("limit", 100),
        skip_existing=True,
    )
    return result


# ── Seed Notes ────────────────────────────────────────────────────────────────

@router.post("/cron/seed-notes")
async def cron_seed_notes(request: Request):
    """Bulk-generate English notes for all chapters that have topics but no content.

    Launches a background job immediately and returns.  Poll
    GET /cron/seed-notes/status for live progress.

    Auth: Bearer {TRANSLATE_CRON_SECRET}

    Body (all optional):
        {
          "subject_id":    "<mongo_id>",   # restrict to one subject
          "board":         "AHSEC",        # restrict by board name
          "chapter_ids":   ["<id>", ...],  # re-run specific chapters (retry)
          "limit":         50,             # max chapters to process (default: all)
          "concurrency":   2,              # parallel Sarvam calls (default: 2)
          "force":         false           # overwrite existing content
        }

    Returns immediately:
        { "job": "started", "run_id": "<id>", "total_queued": N }
    """
    _verify_cron_token(request)

    body: dict = {}
    try:
        body = await request.json()
    except Exception:
        pass

    # Check if a job is already running (in-process guard first, fast path)
    existing = getattr(request.app.state, "seed_notes_status", {})
    if existing.get("running"):
        raise HTTPException(
            status_code=409,
            detail="A seed-notes job is already running. "
                   "Poll GET /cron/seed-notes/status for progress.",
        )

    from app.models.content import Chapter
    from beanie import PydanticObjectId

    subject_id_raw: Optional[str] = body.get("subject_id")
    board_filter: Optional[str]   = body.get("board")
    chapter_ids_raw: list         = body.get("chapter_ids", [])
    limit: int                    = int(body.get("limit", 9999))
    concurrency: int              = max(1, min(int(body.get("concurrency", 2)), 5))
    force: bool                   = bool(body.get("force", False))

    # ── Build the candidate list ───────────────────────────────────────────────
    if chapter_ids_raw:
        # Explicit retry list — process these regardless of content state
        chapters = []
        for cid in chapter_ids_raw[:limit]:
            try:
                ch = await Chapter.get(PydanticObjectId(cid))
                if ch:
                    chapters.append(ch)
            except Exception:
                pass
    else:
        # All chapters that lack content_en (or force=True means all with topics)
        filt = {}
        if subject_id_raw:
            try:
                filt["subject_id"] = PydanticObjectId(subject_id_raw)
            except Exception:
                pass
        if not force:
            filt["$or"] = [
                {"content_en": {"$exists": False}},
                {"content_en": None},
                {"content_en": ""},
            ]

        chapters = await Chapter.find(filt).to_list(length=limit)

        # Board filter requires a join — do it in Python after fetching
        if board_filter:
            from app.models.content import Subject, Stream, Class, Board  # noqa
            chapters = await _filter_chapters_by_board(chapters, board_filter)

    total = len(chapters)
    if total == 0:
        return {"job": "nothing_to_do", "total_queued": 0,
                "message": "All chapters already have content (pass force=true to regenerate)"}

    # ── Create a persistent run document in MongoDB ────────────────────────────
    from app.models.seed_run import SeedRun

    run = SeedRun(
        status="running",
        total=total,
        concurrency=concurrency,
        force=force,
    )
    try:
        await run.insert()
        run_id = str(run.id)
    except Exception as e:
        logger.warning(f"Failed to insert seed_run document: {e} — continuing without DB persistence")
        run_id = "unavailable"
        run = None

    # ── Initialise in-process status ──────────────────────────────────────────
    request.app.state.seed_notes_status = {
        "running":       True,
        "run_id":        run_id,
        "total":         total,
        "completed":     0,
        "failed":        0,
        "skipped":       0,
        "topics_seeded": 0,
        "current":       "",
        "failed_ids":    [],
        "errors":        [],
        "started_at":    run.started_at.isoformat() if run else datetime.now(timezone.utc).isoformat(),
        "finished_at":   None,
        "concurrency":   concurrency,
        "force":         force,
    }

    # ── Launch background task ─────────────────────────────────────────────────
    asyncio.create_task(
        _seed_notes_background(
            app_state=request.app.state,
            chapters=chapters,
            concurrency=concurrency,
            force=force,
            run_id=run_id,
        )
    )

    return {"job": "started", "run_id": run_id, "total_queued": total, "concurrency": concurrency}


@router.get("/cron/seed-notes/status")
async def cron_seed_notes_status(request: Request):
    """Return live progress of the running (or last completed) seed-notes job.

    Falls back to the latest MongoDB run document when the in-process state
    is missing (e.g. after a server restart).

    Auth: Bearer {TRANSLATE_CRON_SECRET}
    """
    _verify_cron_token(request)

    status = getattr(request.app.state, "seed_notes_status", None)
    if status is not None:
        # Calculate ETA from in-process state
        result = dict(status)
        done = status["completed"] + status["failed"] + status["skipped"]
        if status["running"] and done > 0:
            started = datetime.fromisoformat(status["started_at"])
            elapsed = (datetime.now(timezone.utc) - started).total_seconds()
            rate = done / elapsed  # chapters/sec
            remaining = status["total"] - done
            result["eta_seconds"] = round(remaining / rate) if rate > 0 else None
            result["elapsed_seconds"] = round(elapsed)
        return result

    # ── Fallback: read latest run from MongoDB ─────────────────────────────────
    try:
        from app.models.seed_run import SeedRun
        latest = await SeedRun.find_one(
            sort=[("started_at", -1)]
        )
        if latest is None:
            return {"running": False, "message": "No seed-notes job has been started yet."}

        return _seed_run_to_dict(latest)
    except Exception as e:
        logger.warning(f"Failed to read seed_run from MongoDB: {e}")
        return {"running": False, "message": "No seed-notes job has been started yet."}


@router.post("/cron/seed-assamese")
async def cron_seed_assamese(request: Request):
    """Bulk-translate content_en → content_as for chapters missing Assamese (or force all).

    Launches a background job immediately. Poll GET /cron/seed-assamese/status.
    Auth: Bearer {TRANSLATE_CRON_SECRET}

    Body (all optional):
        {
          "limit":       500,   # max chapters (default: all)
          "concurrency": 2,     # parallel Sarvam calls (default: 2)
          "force":       false  # re-translate even if content_as already exists
        }
    """
    _verify_cron_token(request)

    body: dict = {}
    try:
        body = await request.json()
    except Exception:
        pass

    existing = getattr(request.app.state, "seed_assamese_status", {})
    if existing.get("running"):
        raise HTTPException(
            status_code=409,
            detail="A seed-assamese job is already running. "
                   "Poll GET /cron/seed-assamese/status for progress.",
        )
    existing_notes = getattr(request.app.state, "seed_notes_status", {})
    if existing_notes.get("running"):
        raise HTTPException(
            status_code=409,
            detail="A seed-notes job is running — wait for it to finish first.",
        )

    from app.models.content import Chapter

    limit: int       = int(body.get("limit", 9999))
    concurrency: int = max(1, min(int(body.get("concurrency", 2)), 5))
    force: bool      = bool(body.get("force", False))

    filt: dict = {"content_en": {"$exists": True, "$nin": [None, ""]}}
    if not force:
        filt["$or"] = [
            {"content_as": {"$exists": False}},
            {"content_as": None},
            {"content_as": ""},
        ]
    chapters = await Chapter.find(filt).to_list(length=limit)

    total = len(chapters)
    if total == 0:
        return {
            "job": "nothing_to_do",
            "total_queued": 0,
            "message": "All chapters already have Assamese content (pass force=true to retranslate)",
        }

    from app.models.seed_run import SeedRun

    run = SeedRun(status="running", run_type="assamese", total=total,
                  concurrency=concurrency, force=force)
    try:
        await run.insert()
        run_id = str(run.id)
    except Exception as e:
        logger.warning(f"Failed to insert seed_run (assamese): {e}")
        run_id = "unavailable"
        run = None

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


@router.get("/cron/seed-assamese/status")
async def cron_seed_assamese_status(request: Request):
    """Live progress of the running (or last completed) seed-assamese job.
    Auth: Bearer {TRANSLATE_CRON_SECRET}
    """
    _verify_cron_token(request)

    status = getattr(request.app.state, "seed_assamese_status", None)
    if status is not None:
        result = dict(status)
        done = status["completed"] + status["failed"] + status["skipped"]
        if status["running"] and done > 0:
            started = datetime.fromisoformat(status["started_at"])
            elapsed = (datetime.now(timezone.utc) - started).total_seconds()
            rate = done / elapsed
            remaining = status["total"] - done
            result["eta_seconds"]     = round(remaining / rate) if rate > 0 else None
            result["elapsed_seconds"] = round(elapsed)
        return result

    try:
        from app.models.seed_run import SeedRun
        latest = await SeedRun.find_one(
            SeedRun.run_type == "assamese",
            sort=[("started_at", -1)],
        )
        if latest is None:
            return {"running": False, "message": "No seed-assamese job has been started yet."}
        from app.api.v1.admin_cron import _seed_run_to_dict
        return _seed_run_to_dict(latest)
    except Exception as e:
        logger.warning(f"Failed to read seed_assamese run from MongoDB: {e}")
        return {"running": False, "message": "No seed-assamese job has been started yet."}


@router.get("/cron/seed-notes/history")
async def cron_seed_notes_history(request: Request):
    """Return the last 10 seed-notes runs for admin review.

    Auth: Bearer {TRANSLATE_CRON_SECRET}
    """
    _verify_cron_token(request)

    try:
        from app.models.seed_run import SeedRun
        runs = await SeedRun.find(
            sort=[("started_at", -1)]
        ).to_list(length=10)
        return {"runs": [_seed_run_to_dict(r) for r in runs]}
    except Exception as e:
        logger.warning(f"Failed to read seed_run history from MongoDB: {e}")
        return {"runs": [], "error": str(e)}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _seed_run_to_dict(run) -> dict:
    """Serialise a SeedRun document to a plain dict."""
    return {
        "run_id":        str(run.id),
        "run_type":      getattr(run, "run_type", "notes"),
        "status":        run.status,
        "running":       run.status == "running",
        "started_at":    run.started_at.isoformat(),
        "finished_at":   run.finished_at.isoformat() if run.finished_at else None,
        "total":         run.total,
        "completed":     run.completed,
        "failed":        run.failed,
        "skipped":       run.skipped,
        "topics_seeded": run.topics_seeded,
        "failed_ids":    run.failed_ids,
        "errors":        run.errors,
        "concurrency":   run.concurrency,
        "force":         run.force,
        "current":       run.current,
    }


async def _flush_run_to_mongo(run_id: str, app_state) -> None:
    """Upsert current in-process status into the MongoDB seed_run document."""
    if run_id == "unavailable":
        return
    try:
        from app.models.seed_run import SeedRun
        from beanie import PydanticObjectId
        status = app_state.seed_notes_status
        await SeedRun.find_one(SeedRun.id == PydanticObjectId(run_id)).update(
            {"$set": {
                "status":        "running" if status.get("running") else (
                    "error" if status.get("failed", 0) == status.get("total", 0) else "completed"
                ),
                "completed":     status.get("completed", 0),
                "failed":        status.get("failed", 0),
                "skipped":       status.get("skipped", 0),
                "topics_seeded": status.get("topics_seeded", 0),
                "failed_ids":    status.get("failed_ids", []),
                "errors":        status.get("errors", []),
                "current":       status.get("current", ""),
                "finished_at":   (
                    datetime.fromisoformat(status["finished_at"])
                    if status.get("finished_at") else None
                ),
            }}
        )
    except Exception as e:
        logger.warning(f"Failed to flush seed_run progress to MongoDB: {e}")


async def _filter_chapters_by_board(chapters, board_name: str):
    """Filter chapter list to those belonging to the given board name."""
    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        pipeline = [
            {"$lookup": {"from": "streams",  "localField": "stream_id",   "foreignField": "_id", "as": "stream"}},
            {"$unwind": "$stream"},
            {"$lookup": {"from": "classes",  "localField": "stream.class_id", "foreignField": "_id", "as": "cls"}},
            {"$unwind": "$cls"},
            {"$lookup": {"from": "boards",   "localField": "cls.board_id",    "foreignField": "_id", "as": "board"}},
            {"$unwind": "$board"},
            {"$match":  {"board.name": board_name}},
            {"$project": {"_id": 1}},
        ]
        subject_ids = {
            str(doc["_id"])
            async for doc in db["subjects"].aggregate(pipeline)
        }
        return [ch for ch in chapters if str(ch.subject_id) in subject_ids]
    except Exception as e:
        logger.warning(f"Board filter failed ({e}), returning all chapters")
        return chapters


async def _seed_assamese_background(app_state, chapters, concurrency: int, force: bool, run_id: str):
    """Background worker: translate content_en → content_as for each chapter."""
    from app.services.content_generation import content_generation_service

    sem = asyncio.Semaphore(concurrency)
    processed_count = 0
    flush_lock = asyncio.Lock()
    state_key = "seed_assamese_status"

    async def _process_one(ch):
        nonlocal processed_count
        async with sem:
            chapter_id = str(ch.id)
            title = ch.title or chapter_id
            app_state.seed_assamese_status["current"] = title
            try:
                result = await content_generation_service.generate_assamese_only(
                    chapter_id, force=force
                )
                # generate_assamese_only returns the chapter unchanged if skipped
                if result and result.content_as and result.content_as.strip():
                    app_state.seed_assamese_status["completed"] += 1
                    logger.info(f"Seed-assamese ✓ {title!r}")
                else:
                    app_state.seed_assamese_status["skipped"] += 1
                    logger.info(f"Seed-assamese ↷ skipped {title!r} (already has AS or no EN)")
            except Exception as exc:
                logger.error(f"Seed-assamese ✗ {title!r}: {exc}")
                app_state.seed_assamese_status["failed"] += 1
                app_state.seed_assamese_status["failed_ids"].append(chapter_id)
                app_state.seed_assamese_status["errors"].append(
                    {"chapter_id": chapter_id, "title": title, "error": str(exc)[:200]}
                )
            finally:
                async with flush_lock:
                    processed_count += 1
                    if processed_count % _MONGO_FLUSH_EVERY == 0:
                        await _flush_assamese_run_to_mongo(run_id, app_state)

    await asyncio.gather(*[_process_one(ch) for ch in chapters])

    app_state.seed_assamese_status["running"]     = False
    app_state.seed_assamese_status["current"]     = ""
    app_state.seed_assamese_status["finished_at"] = datetime.now(timezone.utc).isoformat()

    await _flush_assamese_run_to_mongo(run_id, app_state)

    total   = app_state.seed_assamese_status["total"]
    done    = app_state.seed_assamese_status["completed"]
    failed  = app_state.seed_assamese_status["failed"]
    skipped = app_state.seed_assamese_status["skipped"]
    logger.info(
        f"Seed-assamese job finished: {done}/{total} translated, "
        f"{failed} failed, {skipped} skipped"
    )


async def _flush_assamese_run_to_mongo(run_id: str, app_state) -> None:
    """Upsert current in-process seed-assamese status into the MongoDB seed_run document."""
    if run_id == "unavailable":
        return
    try:
        from app.models.seed_run import SeedRun
        from beanie import PydanticObjectId
        status = app_state.seed_assamese_status
        await SeedRun.find_one(SeedRun.id == PydanticObjectId(run_id)).update(
            {"$set": {
                "status":     "running" if status.get("running") else (
                    "error" if status.get("failed", 0) == status.get("total", 1) else "completed"
                ),
                "completed":  status.get("completed", 0),
                "failed":     status.get("failed", 0),
                "skipped":    status.get("skipped", 0),
                "failed_ids": status.get("failed_ids", []),
                "errors":     status.get("errors", []),
                "current":    status.get("current", ""),
                "finished_at": (
                    datetime.fromisoformat(status["finished_at"])
                    if status.get("finished_at") else None
                ),
            }}
        )
    except Exception as e:
        logger.warning(f"Failed to flush seed_assamese run to MongoDB: {e}")


async def _seed_notes_background(app_state, chapters, concurrency: int, force: bool, run_id: str):
    """Background worker: generate notes for each chapter with Sarvam concurrency guard."""
    from app.services.content_generation import content_generation_service
    from app.models.content import Subject

    sem = asyncio.Semaphore(concurrency)
    processed_count = 0
    flush_lock = asyncio.Lock()

    async def _process_one(ch):
        nonlocal processed_count
        async with sem:
            chapter_id = str(ch.id)
            title = ch.title or chapter_id
            app_state.seed_notes_status["current"] = title
            try:
                # 1. Ensure topics exist (generates them via AI if missing)
                if not ch.published_topics:
                    # Try to get subject name for context
                    subject_name = ""
                    try:
                        subj = await Subject.get(ch.subject_id)
                        subject_name = subj.name if subj else ""
                    except Exception:
                        pass

                    ch = await content_generation_service.ensure_topics(ch, subject_name=subject_name)
                    if ch.published_topics:
                        app_state.seed_notes_status["topics_seeded"] += 1
                    else:
                        # Still no topics — skip, can't generate
                        logger.warning(f"Seed-notes: no topics for {title!r}, skipping")
                        app_state.seed_notes_status["skipped"] += 1
                        return

                # 2. Generate notes (skips automatically if content_en present and not force)
                await content_generation_service.generate_notes(chapter_id, force=force)
                app_state.seed_notes_status["completed"] += 1
                logger.info(f"Seed-notes ✓ {title!r}")

            except Exception as exc:
                logger.error(f"Seed-notes ✗ {title!r}: {exc}")
                app_state.seed_notes_status["failed"] += 1
                app_state.seed_notes_status["failed_ids"].append(chapter_id)
                app_state.seed_notes_status["errors"].append(
                    {"chapter_id": chapter_id, "title": title, "error": str(exc)[:200]}
                )
            finally:
                # Periodically flush progress to MongoDB so it survives restarts
                async with flush_lock:
                    processed_count += 1
                    if processed_count % _MONGO_FLUSH_EVERY == 0:
                        await _flush_run_to_mongo(run_id, app_state)

    await asyncio.gather(*[_process_one(ch) for ch in chapters])

    app_state.seed_notes_status["running"]     = False
    app_state.seed_notes_status["current"]     = ""
    app_state.seed_notes_status["finished_at"] = datetime.now(timezone.utc).isoformat()

    # Final flush — mark the run as completed in MongoDB
    await _flush_run_to_mongo(run_id, app_state)

    total    = app_state.seed_notes_status["total"]
    done     = app_state.seed_notes_status["completed"]
    failed   = app_state.seed_notes_status["failed"]
    skipped  = app_state.seed_notes_status["skipped"]
    logger.info(
        f"Seed-notes job finished: {done}/{total} generated, "
        f"{failed} failed, {skipped} skipped"
    )


# ── Bulk Mirror RAG ────────────────────────────────────────────────────────────

def _strip_markdown_to_plain(md: str) -> str:
    """Strip markdown syntax to produce clean plain text for RAG chunks."""
    import re
    text = re.sub(r'^#{1,6}\s+', '', md, flags=re.MULTILINE)   # headings
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)                # bold
    text = re.sub(r'\*(.+?)\*', r'\1', text)                    # italic
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'_(.+?)_', r'\1', text)
    text = re.sub(r'~~(.+?)~~', r'\1', text)                    # strikethrough
    text = re.sub(r'`{1,3}([^`]*)`{1,3}', r'\1', text)          # code
    text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.MULTILINE) # bullets
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE) # numbered lists
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)        # links
    text = re.sub(r'!\[[^\]]*\]\([^)]+\)', '', text)             # images
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _split_notes_into_rag_sections(notes: str) -> list[dict]:
    """Split markdown notes by H2/H3 (then H1 as fallback) into [{title, content}]."""
    import re

    def _build(lines: list[str], heading_re: str) -> list[dict]:
        out, cur = [], None
        for line in lines:
            m = re.match(heading_re, line)
            if m:
                if cur is not None:
                    body = _strip_markdown_to_plain('\n'.join(cur['lines']))
                    if len(body) > 10:
                        out.append({'title': cur['title'], 'content': body})
                cur = {'title': m.group(1).strip(), 'lines': []}
            elif cur is not None:
                cur['lines'].append(line)
        if cur is not None:
            body = _strip_markdown_to_plain('\n'.join(cur['lines']))
            if len(body) > 10:
                out.append({'title': cur['title'], 'content': body})
        return [s for s in out if len(s['content']) > 10]

    lines = notes.split('\n')
    sections = _build(lines, r'^#{2,3}\s+(.+)')   # H2 / H3
    if not sections:
        sections = _build(lines, r'^#\s+(.+)')    # H1 fallback
    return sections


@router.post("/cron/bulk-mirror-rag")
async def cron_bulk_mirror_rag(request: Request):
    """Auto-generate rag_sections_en from notes_en for all chapters.

    Splits notes_en by H2/H3 markdown headings into {title, content} sections.
    Markdown syntax is stripped so chunks are clean plain text.

    Auth: Bearer {TRANSLATE_CRON_SECRET}

    Query params:
        force       (bool)   — overwrite chapters that already have sections (default: false)
        limit       (int)    — max chapters to process (default: all)
        subject_id  (str)    — restrict to one subject

    Returns:
        { "processed": N, "skipped": N, "no_headings": N, "errors": [...] }
    """
    _verify_cron_token(request)

    from app.models.content import Chapter
    from app.db.mongo import get_motor_collection
    from beanie import PydanticObjectId

    force      = request.query_params.get("force", "false").lower() == "true"
    limit      = int(request.query_params.get("limit", "0")) or None
    subject_id = request.query_params.get("subject_id") or None

    # Build filter — chapters that have notes_en but (optionally) no RAG sections yet
    filt: dict = {
        "notes_en": {"$exists": True, "$ne": "", "$ne": None, "$type": "string"},
    }
    if not force:
        filt["$or"] = [
            {"rag_sections_en": {"$exists": False}},
            {"rag_sections_en": None},
            {"rag_sections_en": []},
        ]
    if subject_id:
        try:
            filt["subject_id"] = PydanticObjectId(subject_id)
        except Exception:
            filt["subject_id"] = subject_id

    candidates = await Chapter.find(filt).to_list(length=limit or 9999)

    processed = 0
    skipped   = 0
    no_headings = 0
    errors: list[str] = []

    coll = await get_motor_collection("chapters")

    for ch in candidates:
        notes = (ch.notes_en or "").strip()
        if not notes or len(notes) < 50:
            skipped += 1
            continue

        sections = _split_notes_into_rag_sections(notes)
        if not sections:
            no_headings += 1
            logger.info(
                f"bulk-mirror-rag: no headings in chapter {ch.id} "
                f"({getattr(ch, 'title', '?')!r}) — skipped"
            )
            continue

        try:
            await coll.update_one(
                {"_id": ch.id},
                {"$set": {"rag_sections_en": sections}},
            )
            processed += 1
            logger.info(
                f"bulk-mirror-rag: wrote {len(sections)} sections "
                f"→ chapter {ch.id} ({getattr(ch, 'title', '?')!r})"
            )
        except Exception as exc:
            errors.append(f"{ch.id}: {exc}")
            logger.exception(f"bulk-mirror-rag: failed for chapter {ch.id}")

    logger.info(
        f"bulk-mirror-rag done: {processed} processed, "
        f"{skipped} skipped, {no_headings} no-headings, {len(errors)} errors"
    )
    return {
        "processed":   processed,
        "skipped":     skipped,
        "no_headings": no_headings,
        "errors":      errors,
    }
