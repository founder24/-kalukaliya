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
    # Call the Sarvam API directly (same pattern as the passing smoke test)
    # to avoid any circuit-breaker or generate() abstraction issues.
    from app.services.ai.sarvam_client import sarvam_client
    import httpx as _httpx
    from datetime import datetime as _dt, timezone as _tz

    TRANSLATE_SYSTEM = (
        "You are a professional translator specialising in Assamese educational content. "
        "Translate the following English text to Assamese. "
        "Output ONLY the Assamese translation. "
        "Preserve markdown headings, bold, bullet points and LaTeX math exactly."
    )
    CHUNK_WORDS = 400

    async def _translate_chunk(text: str) -> str:
        """Call Sarvam directly and return the content field."""
        resp = await sarvam_client._client.post(
            f"{sarvam_client.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {sarvam_client.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": sarvam_client.model,
                "messages": [
                    {"role": "system", "content": TRANSLATE_SYSTEM},
                    {"role": "user",   "content": text},
                ],
                "temperature": 0.1,
                "enable_thinking": False,
                "max_tokens": 2048,
                "stream": False,
            },
        )
        resp.raise_for_status()
        msg = resp.json()["choices"][0]["message"]
        content = (msg.get("content") or "").strip()
        if not content:
            # Model sometimes puts answer in reasoning_content; extract cleanly
            from app.services.ai.sarvam_client import _extract_assamese_translation
            rc = (msg.get("reasoning_content") or "").strip()
            content = _extract_assamese_translation(rc) if rc else ""
        return content

    sem = asyncio.Semaphore(CONCURRENCY)
    completed = failed = skipped = 0
    failed_ids: list[str] = []

    async def translate_one(ch, idx: int):
        nonlocal completed, failed, skipped
        async with sem:
            chapter_id = str(ch.id)
            title      = ch.title or chapter_id
            try:
                words  = (ch.content_en or "").split()
                chunks = [
                    " ".join(words[i: i + CHUNK_WORDS])
                    for i in range(0, len(words), CHUNK_WORDS)
                ]
                log.info(f"[{idx}/{total}] {title!r} — {len(chunks)} chunk(s)")

                parts = []
                for ci, chunk in enumerate(chunks, 1):
                    translated = await _translate_chunk(chunk)
                    if translated.strip():
                        parts.append(translated.strip())
                        log.info(f"  chunk {ci}/{len(chunks)} → {len(translated.split())} words")
                    else:
                        log.warning(f"  chunk {ci}/{len(chunks)} returned empty translation")

                if parts:
                    content_as = "\n\n".join(parts)
                    await ch.update({"$set": {
                        "content_as":  content_as,
                        "updated_at":  _dt.now(_tz.utc),
                    }})
                    completed += 1
                    log.info(f"[{idx}/{total}] ✓ {title!r} — {len(content_as.split())} words saved")
                else:
                    skipped += 1
                    log.warning(f"[{idx}/{total}] ↷ {title!r} — all chunks empty")
            except Exception as exc:
                failed += 1
                failed_ids.append(chapter_id)
                log.error(f"[{idx}/{total}] ✗ {title!r}: {exc}")

            # Flush to MongoDB every 10 chapters
            done_so_far = completed + failed + skipped
            if done_so_far % 10 == 0:
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
