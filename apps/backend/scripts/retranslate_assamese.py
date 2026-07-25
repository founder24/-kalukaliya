"""
Retranslate all chapters with content_en → content_as using Sarvam AI.
Runs with force=True so it overwrites bad Bengali / truncated translations.

Usage:
    cd apps/backend
    python3 scripts/retranslate_assamese.py [--concurrency 3] [--limit 9999]
"""
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/tmp/retranslate_assamese.log"),
    ],
)
log = logging.getLogger("retranslate_assamese")

CONCURRENCY = int(sys.argv[sys.argv.index("--concurrency") + 1]) if "--concurrency" in sys.argv else 3
LIMIT       = int(sys.argv[sys.argv.index("--limit") + 1])       if "--limit"       in sys.argv else 9999
FORCE       = True   # always force — this script exists to fix bad translations


async def main():
    # ── Bootstrap Beanie / MongoDB ──────────────────────────────────────────
    # Use the same init_mongo() path the app uses so Beanie + pymongo version
    # compat is handled correctly (AsyncMongoClient, not motor).
    from app.db.mongo import init_mongo, close_mongo
    from app.config import settings

    # Ensure MONGODB_URI is available (it may come from MONGODB_URL env var)
    if not settings.MONGODB_URI:
        mongo_url = os.environ.get("MONGODB_URL") or os.environ.get("MONGODB_URI")
        if mongo_url:
            settings.MONGODB_URI = mongo_url  # type: ignore[attr-defined]
        else:
            log.error("No MONGODB_URI / MONGODB_URL in environment. Aborting.")
            return

    db_name = settings.MONGODB_DB_NAME or "syrabit_prod"
    await init_mongo()
    log.info(f"MongoDB connected — db={db_name!r}")

    # ── Load SARVAM_API_KEY from GCP Secret Manager ─────────────────────────
    try:
        from app.core.secret_manager import load_secrets_into_settings
        from app.services.ai.sarvam_client import sarvam_client
        sm_results = await load_secrets_into_settings()
        if settings.SARVAM_API_KEY:
            sarvam_client.api_key = settings.SARVAM_API_KEY
            log.info(f"Sarvam key loaded (prefix={settings.SARVAM_API_KEY[:8]}...)")
        else:
            log.error("SARVAM_API_KEY still empty after Secret Manager fetch — aborting.")
            return
    except Exception as e:
        log.error(f"Failed to load secrets: {e}")
        return

    # Import models AFTER init_mongo so Beanie is already initialised
    from app.models.content  import Chapter   # noqa: E402
    from app.models.seed_run import SeedRun   # noqa: E402

    # ── Find candidate chapters ─────────────────────────────────────────────
    # All chapters with English content (regardless of whether they have AS)
    filt = {"content_en": {"$exists": True, "$nin": [None, ""]}}
    chapters = await Chapter.find(filt).to_list(length=LIMIT)
    total = len(chapters)
    log.info(f"Candidates: {total} chapters with content_en (force={FORCE})")

    if total == 0:
        log.info("Nothing to do.")
        return

    # ── Create a SeedRun record ─────────────────────────────────────────────
    run = SeedRun(
        status="running",
        run_type="assamese",
        total=total,
        concurrency=CONCURRENCY,
        force=FORCE,
    )
    await run.insert()
    run_id = str(run.id)
    log.info(f"SeedRun created — run_id={run_id}")

    # ── Translation worker ──────────────────────────────────────────────────
    from app.services.content_generation import content_generation_service

    sem = asyncio.Semaphore(CONCURRENCY)
    completed = failed = skipped = 0
    failed_ids: list[str] = []

    async def translate_one(ch, idx: int):
        nonlocal completed, failed, skipped
        async with sem:
            chapter_id = str(ch.id)
            title      = ch.title or chapter_id
            try:
                result = await content_generation_service.generate_assamese_only(
                    chapter_id, force=FORCE
                )
                if result and result.content_as and result.content_as.strip():
                    completed += 1
                    log.info(f"[{idx}/{total}] ✓ {title!r} — {len(result.content_as)} chars")
                else:
                    skipped += 1
                    log.warning(f"[{idx}/{total}] ↷ {title!r} — returned empty AS content")
            except Exception as exc:
                failed += 1
                failed_ids.append(chapter_id)
                log.error(f"[{idx}/{total}] ✗ {title!r}: {exc}")

            # Flush to MongoDB every 10 chapters
            if (completed + failed + skipped) % 10 == 0:
                await _flush(run_id, completed, failed, skipped, failed_ids, finished=False)

    await asyncio.gather(*[translate_one(ch, i + 1) for i, ch in enumerate(chapters)])

    # ── Final flush ─────────────────────────────────────────────────────────
    await _flush(run_id, completed, failed, skipped, failed_ids, finished=True)

    log.info(
        f"\n{'─'*60}\n"
        f"DONE  — {completed}/{total} translated,  "
        f"{skipped} skipped,  {failed} failed\n"
        f"Log:  /tmp/retranslate_assamese.log\n"
        f"{'─'*60}"
    )
    await close_mongo()


async def _flush(run_id, completed, failed, skipped, failed_ids, finished: bool):
    from app.models.seed_run import SeedRun   # noqa: F811
    from beanie import PydanticObjectId
    try:
        now = datetime.now(timezone.utc)
        total_done = completed + failed + skipped
        status_str = (
            "completed" if finished and failed == 0
            else "error"  if finished and failed > 0 and completed == 0
            else "partial" if finished
            else "running"
        )
        await SeedRun.find_one(SeedRun.id == PydanticObjectId(run_id)).update({"$set": {
            "status":      status_str,
            "completed":   completed,
            "failed":      failed,
            "skipped":     skipped,
            "failed_ids":  failed_ids,
            "finished_at": now if finished else None,
            "current":     "",
        }})
    except Exception as e:
        log.warning(f"Flush failed: {e}")


if __name__ == "__main__":
    asyncio.run(main())
