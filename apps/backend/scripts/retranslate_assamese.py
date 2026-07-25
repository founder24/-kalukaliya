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
    from beanie import init_beanie
    from motor.motor_asyncio import AsyncIOMotorClient
    from app.config import settings

    mongo_url = settings.MONGODB_URL or os.environ.get("MONGODB_URL")
    db_name   = getattr(settings, "MONGODB_DB_NAME", None) or os.environ.get("MONGODB_DB_NAME", "syrabit_prod")

    client = AsyncIOMotorClient(mongo_url)
    db     = client[db_name]

    # Import all document models that Beanie needs
    from app.models.content   import Chapter, Subject, Topic
    from app.models.seed_run  import SeedRun
    from app.models.user      import User

    await init_beanie(
        database=db,
        document_models=[Chapter, Subject, Topic, SeedRun, User],
    )
    log.info(f"MongoDB connected — db={db_name!r}")

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
    client.close()


async def _flush(run_id, completed, failed, skipped, failed_ids, finished: bool):
    from app.models.seed_run import SeedRun
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
