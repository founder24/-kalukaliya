"""
migrate_chunks_to_pinecone.py — One-shot migration of MongoDB chunks to Pinecone.

Reads all chunks that have a non-null `embedding` field from the MongoDB
`chunks` collection, batches them in groups of 100, and upserts to the
Pinecone `syrabit-ahsec` index.

Prerequisites
-------------
1. Set env vars: MONGO_URL, PINECONE_KEY (or PINECONE_API_KEY), PINECONE_INDEX
2. Run `embed_chunks_bulk` first to ensure all chunks have embeddings.
3. The Pinecone index must exist (call ensure_pinecone_index() or run with
   --ensure-index).

Usage
-----
    # Dry run (no writes to Pinecone)
    python scripts/migrate_chunks_to_pinecone.py --dry-run

    # Real migration (all embedded chunks)
    python scripts/migrate_chunks_to_pinecone.py

    # Scope to a single subject
    python scripts/migrate_chunks_to_pinecone.py --subject-id sub_physics_ahsec12

    # Limit to first N chunks (for smoke testing)
    python scripts/migrate_chunks_to_pinecone.py --limit 200 --dry-run

    # Force re-upsert even if already in Pinecone (default: upsert all)
    python scripts/migrate_chunks_to_pinecone.py --batch-size 50

Exit codes
----------
    0 — success (even partial — check logs for per-batch failures)
    1 — fatal error (cannot connect to MongoDB or Pinecone)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("migrate_chunks_to_pinecone")

_BATCH_SIZE = 100


async def _get_mongo_db():
    """Connect to MongoDB and return the database instance."""
    mongo_url = os.environ.get("MONGO_URL", "").strip()
    if not mongo_url:
        raise RuntimeError("MONGO_URL env var is required")
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=8000)
        await client.admin.command("ping")
        db_name = (mongo_url.rstrip("/").split("/")[-1].split("?")[0]) or "syrabit"
        return client[db_name]
    except Exception as exc:
        raise RuntimeError(f"MongoDB connection failed: {exc}") from exc


async def migrate(
    *,
    dry_run: bool = False,
    batch_size: int = _BATCH_SIZE,
    subject_id: Optional[str] = None,
    limit: Optional[int] = None,
    ensure_index: bool = False,
) -> dict:
    t0 = time.perf_counter()

    # ── Connect to MongoDB ──────────────────────────────────────────────────
    logger.info("Connecting to MongoDB …")
    try:
        db = await _get_mongo_db()
    except RuntimeError as exc:
        logger.error("%s", exc)
        sys.exit(1)

    # ── Ensure Pinecone index exists ────────────────────────────────────────
    if ensure_index:
        logger.info("Ensuring Pinecone index …")
        try:
            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from retrievers.pinecone_vector import ensure_pinecone_index
            result = await ensure_pinecone_index()
            logger.info("Pinecone index: %s", result)
            if not result.get("ok"):
                logger.error("Could not ensure Pinecone index: %s", result)
                sys.exit(1)
        except Exception as exc:
            logger.error("ensure_pinecone_index failed: %s", exc)
            sys.exit(1)

    # ── Load PineconeVectorRetriever ─────────────────────────────────────────
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from retrievers.pinecone_vector import PineconeVectorRetriever
        retriever = PineconeVectorRetriever()
        if not retriever.is_configured():
            logger.error(
                "PINECONE_KEY (or PINECONE_API_KEY) is not set — cannot proceed"
            )
            sys.exit(1)
    except Exception as exc:
        logger.error("Failed to load PineconeVectorRetriever: %s", exc)
        sys.exit(1)

    # ── Query MongoDB for chunks with embeddings ──────────────────────────────
    query: dict = {"embedding": {"$exists": True, "$ne": None}}
    if subject_id:
        query["subject_id"] = subject_id
        logger.info("Scoped to subject_id=%s", subject_id)

    projection = {
        "_id": 1,
        "chapter_id": 1,
        "subject_id": 1,
        "board_id": 1,
        "chapter_title": 1,
        "topic_name": 1,
        "embedding": 1,
        "embedding_model": 1,
    }

    cursor = db.chunks.find(query, projection)
    if limit:
        cursor = cursor.limit(limit)
        logger.info("Limiting to first %d chunks", limit)

    # Count for progress display (best-effort; does not affect correctness)
    try:
        total_estimate = await db.chunks.count_documents(query)
        if limit:
            total_estimate = min(total_estimate, limit)
        logger.info("Found ~%d chunks with embeddings to migrate", total_estimate)
    except Exception:
        total_estimate = 0
        logger.info("Fetching chunks from MongoDB (count unavailable) …")

    if total_estimate == 0 and limit is None:
        # Do a quick check — count might fail on some Atlas configs
        pass

    # ── Cursor-based streaming: batch without loading all docs into memory ────
    # This avoids any fixed cap on corpus size. Each batch is fetched from the
    # cursor incrementally so multi-million chunk collections are handled safely.
    upserted = 0
    failed = 0
    processed = 0
    batch_num = 0
    batch: list = []

    async def _flush_batch(batch: list, batch_num: int) -> tuple[int, int]:
        """Upsert one batch to Pinecone. Returns (upserted, failed) counts."""
        vectors = []
        local_failed = 0
        for ch in batch:
            emb = ch.get("embedding")
            if not emb or not isinstance(emb, list):
                logger.warning("Chunk %s has invalid embedding — skipping", ch.get("_id"))
                local_failed += 1
                continue
            vectors.append({
                "id": str(ch["_id"]),
                "values": emb,
                "metadata": {
                    "chapter_id":      ch.get("chapter_id", ""),
                    "subject_id":      ch.get("subject_id", ""),
                    "board_id":        ch.get("board_id", ""),
                    "chapter_title":   ch.get("chapter_title", ""),
                    "topic_name":      ch.get("topic_name", ""),
                    "embedding_model": ch.get("embedding_model", "embed-multilingual-v3.0"),
                },
            })

        if not vectors:
            return 0, local_failed

        if dry_run:
            logger.info(
                "  [DRY RUN] Batch %d — would upsert %d vectors", batch_num, len(vectors)
            )
            return len(vectors), local_failed

        try:
            # embed-model: legacy-migration-script-not-in-prod-chain
            result = await retriever.upsert(vectors)
            n = result.get("upserted", 0)
            errs = result.get("errors", [])
            if errs:
                logger.warning("  Batch %d partial failure: %s", batch_num, errs)
                local_failed += len(vectors) - n
            else:
                logger.info("  Batch %d OK — upserted %d", batch_num, n)
            return n, local_failed
        except Exception as exc:
            logger.error("  Batch %d FAILED: %s", batch_num, exc)
            return 0, local_failed + len(vectors)

    async for doc in cursor:
        batch.append(doc)
        processed += 1

        if len(batch) >= batch_size:
            batch_num += 1
            logger.info(
                "Batch %d — processing %d vectors [%d processed …] …",
                batch_num, len(batch), processed,
            )
            n_up, n_fail = await _flush_batch(batch, batch_num)
            upserted += n_up
            failed += n_fail
            batch = []
            await asyncio.sleep(0.2)  # throttle

    # Final partial batch
    if batch:
        batch_num += 1
        logger.info(
            "Batch %d (final) — processing %d vectors [%d processed] …",
            batch_num, len(batch), processed,
        )
        n_up, n_fail = await _flush_batch(batch, batch_num)
        upserted += n_up
        failed += n_fail

    total = processed

    duration = round(time.perf_counter() - t0, 2)
    summary = {
        "total":      total,
        "upserted":   upserted,
        "failed":     failed,
        "duration_s": duration,
        "dry_run":    dry_run,
    }
    logger.info("Migration complete: %s", summary)

    if failed > 0:
        logger.warning("%d chunks failed to upsert — check logs above", failed)
    else:
        logger.info("All chunks migrated successfully")

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate MongoDB chunk embeddings to Pinecone syrabit-ahsec index"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print progress without writing to Pinecone",
    )
    parser.add_argument(
        "--batch-size", type=int, default=_BATCH_SIZE,
        help=f"Vectors per Pinecone upsert request (default: {_BATCH_SIZE})",
    )
    parser.add_argument(
        "--subject-id", type=str, default=None,
        help="Scope migration to a single subject_id",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Max total chunks to process (for smoke tests)",
    )
    parser.add_argument(
        "--ensure-index", action="store_true",
        help="Create the Pinecone index if it doesn't exist before migrating",
    )
    args = parser.parse_args()

    asyncio.run(migrate(
        dry_run=args.dry_run,
        batch_size=args.batch_size,
        subject_id=args.subject_id,
        limit=args.limit,
        ensure_index=args.ensure_index,
    ))


if __name__ == "__main__":
    main()
