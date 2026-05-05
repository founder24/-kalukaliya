"""
backfill_workers_embeddings.py — Task #411 CLI driver for the
legacy → workers_ai_custom re-embedding job.

Walks every chunk where ``embedding_source != "workers_ai_custom"``,
re-embeds it through the new Workers-AI custom embed worker, and
re-upserts the vector into Pinecone with the new source tag. State
(last processed ``_id``, counters) is persisted in the
``embed_backfill_state`` Mongo collection so the run is resumable —
killing the script and re-running it picks up where the previous
invocation left off.

Prerequisites
-------------
    MONGO_URL, WORKERS_EMBED_URL, WORKERS_EMBED_SECRET,
    PINECONE_KEY (or PINECONE_API_KEY)

Usage
-----
    # Run until every legacy chunk is re-embedded
    python scripts/backfill_workers_embeddings.py

    # Process at most 1,000 chunks then exit (resume by re-running)
    python scripts/backfill_workers_embeddings.py --max-chunks 1000

    # Show current progress without doing any work
    python scripts/backfill_workers_embeddings.py --status

Exit codes
----------
    0 — success (or nothing to do)
    1 — fatal error (cannot connect to MongoDB)
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
logger = logging.getLogger("backfill_workers_embeddings")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def _get_db():
    mongo_url = os.environ.get("MONGO_URL", "").strip()
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

    from aca_jobs import embed_backfill as _bf

    if args.status:
        progress = await _bf.get_progress(db)
        logger.info("Backfill progress: %s", progress)
        return 0

    if args.max_chunks is not None:
        # Single pass with the supplied budget.
        summary = await _bf.run_backfill(
            db, max_chunks=args.max_chunks, batch_size=args.batch_size,
        )
        logger.info("Pass complete: %s", summary)
        return 0

    # No budget => loop until exhausted.
    total_processed = 0
    total_succeeded = 0
    total_failed = 0
    while True:
        summary = await _bf.run_backfill(
            db, max_chunks=args.pass_size, batch_size=args.batch_size,
        )
        if summary.get("skipped") == "already_running":
            logger.warning("Another backfill run holds the lock — exiting.")
            return 0
        total_processed += int(summary.get("processed", 0))
        total_succeeded += int(summary.get("succeeded", 0))
        total_failed += int(summary.get("failed", 0))
        remaining = int(summary.get("remaining", 0))
        logger.info(
            "Cumulative: processed=%d succeeded=%d failed=%d remaining=%d",
            total_processed, total_succeeded, total_failed, remaining,
        )
        if int(summary.get("processed", 0)) == 0 or remaining == 0:
            break

    logger.info("Backfill complete.")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--status", action="store_true",
                   help="Print current progress and exit (no work).")
    p.add_argument("--max-chunks", type=int, default=None,
                   help="Cap a single pass at this many chunks then exit.")
    p.add_argument("--pass-size", type=int, default=5000,
                   help="Chunks per resume cycle when looping (default 5000).")
    p.add_argument("--batch-size", type=int, default=32,
                   help="Texts per /embed call (max 32, the worker's hard cap).")
    args = p.parse_args()
    sys.exit(asyncio.run(_amain(args)))


if __name__ == "__main__":
    main()
