"""
Bulk RAG seed — English notes chunks + topic embeddings
========================================================
Iterates every AHSEC chapter that has English notes content, pushes the text
through the v2 Vectorize ingestion pipeline (chunking → CF Workers AI embedding
→ CF Vectorize upsert), and refreshes the MongoDB topic_embeddings collection
so the chat topic matcher can route questions correctly.

Assamese content is intentionally skipped (AS is coming soon).

Usage (run from apps/backend/):
    python3 -m scripts.bulk_seed_rag_en [options]

Options:
    --subject SLUG      Only process chapters for this subject (e.g. chemistry)
    --force             Re-index chapters that already have notes_rag_indexed_at set
    --dry-run           Run chunking/embedding but skip all writes (Vectorize + Mongo)
    --limit N           Stop after N chapters (smoke-test)
    --parallelism N     Concurrent chapter ingestions (default 3, max 10)
    --delay S           Seconds between batches (default 0.5)
    --skip-topics       Skip topic embedding refresh (only do notes chunks)
    --skip-chunks       Skip Vectorize chunk ingestion (only do topic embeddings)

Exit codes:
    0 — all chapters processed (errors logged but treated as non-fatal)
    1 — fatal startup error (no DB, no embedding provider)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(Path(__file__).parent / ".bulk_seed_rag_en.log"),
    ],
)
log = logging.getLogger("bulk_seed_rag_en")


# ── CLI ────────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Bulk seed English RAG chunks + topic embeddings")
    p.add_argument("--subject",      type=str, default=None, help="Filter by subject slug or name")
    p.add_argument("--force",        action="store_true",    help="Re-index already-indexed chapters")
    p.add_argument("--dry-run",      action="store_true",    help="Embed but skip all writes")
    p.add_argument("--limit",        type=int, default=None, help="Stop after N chapters")
    p.add_argument("--parallelism",  type=int, default=3,    help="Concurrent ingestions (1-10)")
    p.add_argument("--delay",        type=float, default=0.5,help="Seconds between semaphore batches")
    p.add_argument("--skip-topics",  action="store_true",    help="Skip topic embedding refresh")
    p.add_argument("--skip-chunks",  action="store_true",    help="Skip Vectorize chunk ingestion")
    return p.parse_args()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _flatten_sections(sections: list[dict]) -> str:
    """Convert [{title, content}] → plain text for Vectorize ingestion."""
    parts: list[str] = []
    for s in (sections or []):
        t = (s.get("title") or "").strip()
        c = (s.get("content") or "").strip()
        if t:
            parts.append(f"## {t}")
        if c:
            parts.append(c)
    return "\n\n".join(parts)


def _en_text(chapter) -> str | None:
    """Return the best available English text for RAG ingestion, or None."""
    # Priority: structured sections > retrieval text > user-facing notes > legacy content
    if chapter.rag_sections_en:
        t = _flatten_sections(chapter.rag_sections_en)
        if t.strip():
            return t
    if chapter.rag_text_en and chapter.rag_text_en.strip():
        return chapter.rag_text_en
    if chapter.notes_en and chapter.notes_en.strip():
        return chapter.notes_en
    if chapter.content_en and chapter.content_en.strip():
        return chapter.content_en
    return None


# ── Per-chapter processing ─────────────────────────────────────────────────────

async def _seed_one(
    chapter,
    *,
    args: argparse.Namespace,
    cp_service,                 # content_publisher_service instance
    semaphore: asyncio.Semaphore,
    counter: list[int],         # [done, skipped, errors]
    total: int,
) -> dict:
    """Seed one chapter: Vectorize chunks (EN only) + topic embeddings."""
    from app.services.rag.ingestion_v2 import ingest_chapter_v2

    chapter_id = str(chapter.id)
    title = chapter.title or chapter_id

    async with semaphore:
        # ── Decide whether to skip ──────────────────────────────────────────
        already_indexed = bool(chapter.notes_rag_indexed_at)
        en_text = _en_text(chapter)
        has_topics = bool(chapter.published_topics)

        if not en_text and not has_topics:
            log.info(f"  [{counter[0]+counter[1]+1}/{total}] SKIP (no EN content, no topics): {title!r}")
            counter[1] += 1
            return {"status": "skipped", "reason": "no_content"}

        if already_indexed and not args.force:
            log.info(f"  [{counter[0]+counter[1]+1}/{total}] SKIP (already indexed, use --force): {title!r}")
            counter[1] += 1
            return {"status": "skipped", "reason": "already_indexed"}

        result: dict = {"chapter_id": chapter_id, "title": title}

        # ── Step 1: Vectorize chunks ────────────────────────────────────────
        if not args.skip_chunks and en_text:
            try:
                meta = {
                    "subject_id": str(chapter.subject_id),
                    "chapter_id": chapter_id,
                    "chapter_slug": chapter.slug or "",
                }
                ingest_result = await ingest_chapter_v2(
                    chapter_id=chapter_id,
                    content_en=en_text,
                    content_as=None,        # AS is coming soon — skip
                    metadata={**meta, "source_type": "notes"},
                    source_type="notes",
                    dry_run=args.dry_run,
                )
                en = ingest_result.get("en", {})
                result["chunks"] = en.get("chunks_total", 0)
                result["vectorized"] = en.get("vectorize_upserted", 0)
                result["chunk_errors"] = en.get("errors", [])

                if not args.dry_run:
                    now = datetime.now(timezone.utc)
                    chapter.notes_rag_indexed_at = now
                    chapter.rag_indexed_at = now
                    await chapter.save()

                log.info(
                    f"  [{counter[0]+1}/{total}] CHUNKS OK: {title!r} "
                    f"→ {result['chunks']} chunks, {result['vectorized']} vectorized"
                    + (" [DRY RUN]" if args.dry_run else "")
                )
            except Exception as exc:
                result["chunk_error"] = str(exc)
                log.error(f"  [{counter[0]+1}/{total}] CHUNKS FAILED: {title!r} — {exc}")

        # ── Step 2: Topic embeddings ────────────────────────────────────────
        if not args.skip_topics and has_topics:
            try:
                hierarchy = await cp_service._resolve_hierarchy(chapter)
                embed_result = await cp_service._generate_topic_embeddings(chapter, hierarchy)
                result["topics"] = embed_result.get("count", 0)
                result["topic_errors"] = embed_result.get("error_count", 0)
                log.info(
                    f"    ↳ topics: {result['topics']} embedded"
                    + (f", {result['topic_errors']} errors" if result.get("topic_errors") else "")
                    + (" [DRY RUN]" if args.dry_run else "")
                )
            except Exception as exc:
                result["topic_error"] = str(exc)
                log.warning(f"    ↳ topic embedding failed: {exc}")

        counter[0] += 1
        if args.delay > 0:
            await asyncio.sleep(args.delay)
        return result


# ── Main ───────────────────────────────────────────────────────────────────────

async def main() -> None:
    args = _parse_args()

    # ── Bootstrap DB ──────────────────────────────────────────────────────────
    from app.db.mongo import init_mongo
    from app.config import settings

    if not settings.MONGODB_URI:
        mongo_url = os.environ.get("MONGODB_URL") or os.environ.get("MONGODB_URI")
        if mongo_url:
            settings.MONGODB_URI = mongo_url  # type: ignore[attr-defined]
        else:
            log.error("MONGODB_URI / MONGODB_URL not set — aborting")
            sys.exit(1)

    await init_mongo()
    log.info(f"MongoDB connected — db={settings.MONGODB_DB_NAME!r}")

    # ── Pre-flight: check embedding provider ──────────────────────────────────
    if not args.dry_run:
        cf_account = (
            os.environ.get("CF_ACCOUNT_ID")
            or os.environ.get("CLOUDFLARE_ACCOUNT_ID")
            or getattr(settings, "CF_ACCOUNT_ID", None)
            or getattr(settings, "CLOUDFLARE_ACCOUNT_ID", None)
        )
        if not cf_account:
            log.error(
                "PRE-FLIGHT FAILED: CF_ACCOUNT_ID / CLOUDFLARE_ACCOUNT_ID is not set. "
                "Cannot call CF Workers AI embedding API. "
                "Set the env var and retry."
            )
            sys.exit(1)
        log.info(f"CF account: {cf_account}")

    # ── Load chapters ─────────────────────────────────────────────────────────
    from app.models.content import Chapter, Subject

    query: dict = {}
    if args.subject:
        # Match subject by slug or name (case-insensitive)
        want = args.subject.lower().replace("-", " ")
        subjects = await Subject.find().to_list()
        matching_ids = [
            str(s.id)
            for s in subjects
            if s.name.lower().replace("-", " ") == want
            or (s.slug or "").lower().replace("-", " ") == want
            or want in s.name.lower()
        ]
        if not matching_ids:
            log.error(f"No subjects found matching {args.subject!r}")
            sys.exit(1)
        log.info(f"Subject filter: {args.subject!r} → {len(matching_ids)} subject ID(s)")
        query["subject_id"] = {"$in": matching_ids}

    all_chapters = (
        await Chapter.find(query)
        .sort([("subject_id", 1), ("chapter_number", 1)])
        .to_list()
    )

    # Only include chapters that have something to index
    eligible = [
        ch for ch in all_chapters
        if _en_text(ch) or ch.published_topics
    ]

    if args.limit:
        eligible = eligible[: args.limit]

    log.info(
        f"Chapters: {len(all_chapters)} total, {len(eligible)} eligible "
        f"({'dry-run' if args.dry_run else 'live'}, "
        f"parallelism={min(args.parallelism, 10)}, "
        f"force={'yes' if args.force else 'no'})"
    )

    if not eligible:
        log.info("Nothing to seed — done.")
        return

    # ── Seed ──────────────────────────────────────────────────────────────────
    from app.services.content_publisher import content_publisher_service as cp_service

    semaphore = asyncio.Semaphore(max(1, min(args.parallelism, 10)))
    counter = [0, 0, 0]  # [done, skipped, errors]

    tasks = [
        _seed_one(
            ch,
            args=args,
            cp_service=cp_service,
            semaphore=semaphore,
            counter=counter,
            total=len(eligible),
        )
        for ch in eligible
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # ── Summary ───────────────────────────────────────────────────────────────
    total_chunks   = sum(r.get("chunks",   0) for r in results if isinstance(r, dict))
    total_vec      = sum(r.get("vectorized", 0) for r in results if isinstance(r, dict))
    total_topics   = sum(r.get("topics",   0) for r in results if isinstance(r, dict))
    error_count    = sum(1 for r in results if isinstance(r, Exception))
    skipped        = sum(1 for r in results if isinstance(r, dict) and r.get("status") == "skipped")

    log.info("")
    log.info("=" * 60)
    log.info("BULK RAG SEED COMPLETE")
    log.info(f"  Processed : {counter[0]}")
    log.info(f"  Skipped   : {skipped}")
    log.info(f"  Errors    : {error_count}")
    log.info(f"  Chunks    : {total_chunks} ingested, {total_vec} vectorized")
    log.info(f"  Topics    : {total_topics} embeddings written")
    if args.dry_run:
        log.info("  [DRY RUN — no writes made]")
    log.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
