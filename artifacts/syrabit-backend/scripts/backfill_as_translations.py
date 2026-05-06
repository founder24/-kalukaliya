"""scripts/backfill_as_translations.py — Task #465 CLI driver.

Translates English fields on the SSR-feeding collections (``subjects``,
``chapters``, ``seo_pages``, ``pyq_html_pages``) into Assamese sibling
fields (``name_as``, ``content_as``, ``meta_description_as``, …) by
delegating to the centralized V4 §4 Assamese chain in
``routes.ai_chat._assamese_translate_gemini_main_sarvam_polish``
(Workers-AI IndicTrans2 primary → Vertex/Gemini polish, with redis
caching and script-validation). State is persisted in the
``as_translation_state`` Mongo collection so the run is resumable.

Prerequisites
-------------
    MONGO_URL                                    — required
    CF_AI_GATEWAY_ACCOUNT_ID + CLOUDFLARE_API_TOKEN — IndicTrans2 path
    GEMINI_API_KEY                               — polish step

Usage
-----
    # Run until every targeted doc has been translated.
    python scripts/backfill_as_translations.py

    # Cap a single pass to 50 docs per collection then exit.
    python scripts/backfill_as_translations.py --max-docs 50

    # Restrict to one collection.
    python scripts/backfill_as_translations.py --collection seo_pages

    # Print current progress without doing any work.
    python scripts/backfill_as_translations.py --status

Exit codes
----------
    0 — success (or nothing to do)
    1 — fatal error (cannot connect to MongoDB)
    2 — bad arguments
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill_as_translations")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def _get_db():
    mongo_url = os.environ.get("MONGO_URL", "").strip()
    if not mongo_url:
        raise RuntimeError("MONGO_URL env var is required")
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=8000)
    await client.admin.command("ping")
    db_name = (
        os.environ.get("DB_NAME")
        or (mongo_url.rstrip("/").split("/")[-1].split("?")[0])
        or "syrabit"
    )
    return client[db_name]


async def _amain(args) -> int:
    try:
        db = await _get_db()
    except Exception as exc:
        logger.error("MongoDB connection failed: %s", exc)
        return 1

    from aca_jobs import as_translation_backfill as _bf

    if args.collection and args.collection not in _bf.FIELD_MAP:
        logger.error(
            "Unknown collection %r. Choose one of: %s",
            args.collection, ", ".join(sorted(_bf.FIELD_MAP)),
        )
        return 2

    if args.status:
        progress = await _bf.get_progress(db)
        logger.info("Backfill progress: %s", progress)
        return 0

    targets = [args.collection] if args.collection else None

    if args.max_docs is not None:
        out = await _bf.run_backfill(
            db,
            collections=targets,
            max_docs=args.max_docs,
            batch_size=args.batch_size,
        )
        logger.info("Pass complete: %s", out)
        return 0

    # No budget — loop the configured pass size until every targeted
    # collection has zero remaining docs.
    while True:
        out = await _bf.run_backfill(
            db,
            collections=targets,
            max_docs=args.pass_size,
            batch_size=args.batch_size,
        )
        if out.get("skipped") == "already_running":
            logger.warning("Another backfill run holds the lock — exiting.")
            return 0
        results = out.get("results") or []
        any_progress = any(int(r.get("processed", 0)) > 0 for r in results)
        any_remaining = any(int(r.get("remaining", 0)) > 0 for r in results)
        logger.info(
            "Cumulative summary: %s",
            [{k: r.get(k) for k in ("collection", "processed",
                                    "translated", "failed", "skipped",
                                    "remaining")}
             for r in results],
        )
        if not any_progress or not any_remaining:
            break

    logger.info("Backfill complete.")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--status", action="store_true",
                   help="Print current progress and exit (no work).")
    p.add_argument("--collection", default=None,
                   help="Restrict the run to one collection "
                        "(subjects, chapters, seo_pages, pyq_html_pages).")
    p.add_argument("--max-docs", type=int, default=None,
                   help="Cap a single pass at this many docs per "
                        "collection then exit.")
    p.add_argument("--pass-size", type=int, default=200,
                   help="Docs per resume cycle when looping (default 200).")
    p.add_argument("--batch-size", type=int, default=5,
                   help="Docs per Mongo batch / bulk_write (default 5).")
    args = p.parse_args()
    sys.exit(asyncio.run(_amain(args)))


if __name__ == "__main__":
    main()
