"""backfill_assamese_content.py — Task #465 CLI driver.

Walks every SSR-feeding collection (``subjects``, ``chapters``,
``seo_pages``, ``pyq_html_pages``) and translates the configured English
fields into their Assamese siblings. Mirrors the resumable pattern of
``backfill_workers_embeddings.py``.

Prerequisites
-------------
    MONGO_URL                          — Mongo connection string.
    CF_AI_GATEWAY_ACCOUNT_ID,
    CLOUDFLARE_API_TOKEN               — Workers-AI IndicTrans2 (primary).
    GOOGLE_APPLICATION_CREDENTIALS_JSON — Vertex polish step (V4 §4).

Usage
-----
    # Run a capped pass across every collection
    python scripts/backfill_assamese_content.py --max-docs 100

    # Single collection, custom batch
    python scripts/backfill_assamese_content.py --collection chapters --max-docs 50

    # Show progress without doing any work
    python scripts/backfill_assamese_content.py --status

Exit codes
----------
    0 — success (or nothing to do)
    1 — fatal error (cannot connect to MongoDB)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill_assamese_content")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def _get_db():
    mongo_url = (os.environ.get("MONGO_URL") or "").strip().strip('"').strip("'")
    if not mongo_url:
        raise RuntimeError("MONGO_URL env var is required")
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=8000)
    await client.admin.command("ping")
    db_name = (mongo_url.rstrip("/").split("/")[-1].split("?")[0]) or "syrabit"
    return client[db_name]


async def _amain(args) -> int:
    try:
        db = await _get_db()
    except Exception as exc:
        logger.error("MongoDB connection failed: %s", exc)
        return 1

    from aca_jobs import as_translation_backfill as _bf

    if args.status:
        progress = await _bf.get_progress(db)
        print(json.dumps(progress, default=str, indent=2))
        return 0

    targets = [args.collection] if args.collection else None

    if args.max_docs is not None:
        result = await _bf.run_backfill(
            db,
            collections=targets,
            max_docs=args.max_docs,
            batch_size=args.batch_size,
        )
        print(json.dumps(result, default=str, indent=2))
        if result.get("error"):
            logger.error("Backfill error: %s", result.get("error"))
            return 1
        if result.get("skipped") == "already_running":
            logger.warning("Another backfill run holds the lock — exiting.")
            return 0
        return 0

    # No budget => loop per-collection until each one drains.
    while True:
        result = await _bf.run_backfill(
            db,
            collections=targets,
            max_docs=args.pass_size,
            batch_size=args.batch_size,
        )
        if result.get("skipped") == "already_running":
            logger.warning("Another backfill run holds the lock — exiting.")
            return 0
        if result.get("error"):
            logger.error("Backfill error: %s", result)
            return 1
        per_pass_remaining = sum(
            int(r.get("remaining", 0)) for r in result.get("results", [])
        )
        per_pass_processed = sum(
            int(r.get("processed", 0)) for r in result.get("results", [])
        )
        logger.info(
            "Loop pass: processed=%d remaining_estimate=%d",
            per_pass_processed, per_pass_remaining,
        )
        # ``remaining`` from `_count_remaining()` only counts docs whose
        # ``<field>_as`` is null/empty — it does NOT see docs that need
        # *re*-translation due to source-hash drift or low-Assamese-script
        # ratio. Treating ``remaining == 0`` as completion would exit
        # early and leave stale-hash docs untranslated. The only safe
        # termination signal is ``processed == 0`` (the cursor returned
        # nothing on every collection in this pass — i.e. each collection
        # has reached end-of-stream and reset).
        if per_pass_processed == 0:
            break

    logger.info("Backfill complete.")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--status", action="store_true",
                   help="Print current progress and exit (no work).")
    p.add_argument("--collection", default=None,
                   help="Restrict to a single collection (subjects, chapters, "
                        "seo_pages, pyq_html_pages).")
    p.add_argument("--max-docs", type=int, default=None,
                   help="Cap a single pass at this many docs per collection.")
    p.add_argument("--pass-size", type=int, default=200,
                   help="Docs per resume cycle when looping (default 200).")
    p.add_argument("--batch-size", type=int, default=5,
                   help="Docs per Mongo cursor page (default 5).")
    args = p.parse_args()
    sys.exit(asyncio.run(_amain(args)))


if __name__ == "__main__":
    main()
