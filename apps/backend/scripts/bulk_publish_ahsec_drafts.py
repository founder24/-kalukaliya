"""
Bulk-publish AHSEC draft chapters that have content and topics ready.

Selects chapters where:
  - status == "draft"
  - published_topics is non-empty  (topics are ready)
  - content_en is non-empty        (notes have been generated)
  - Board slug matches --board flag (default: "ahsec")

Runs the full ContentPublisherService.publish_chapter() pipeline per chapter.
GCP/Cloudflare steps skip automatically when credentials are absent — the
one step that always runs is flipping status → "published" in MongoDB.

Usage:
    cd apps/backend

    # Preview what would be published (no writes)
    python -m scripts.bulk_publish_ahsec_drafts --dry-run

    # Publish all eligible AHSEC drafts
    python -m scripts.bulk_publish_ahsec_drafts

    # Publish a different board
    python -m scripts.bulk_publish_ahsec_drafts --board seba

    # Limit to N chapters (useful for staged rollout)
    python -m scripts.bulk_publish_ahsec_drafts --limit 20

    # Adjust delay between chapters (seconds, default 0.5)
    python -m scripts.bulk_publish_ahsec_drafts --delay 1.0
"""

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


async def main(board_slug: str, dry_run: bool, limit: int | None, delay: float):
    from pymongo import AsyncMongoClient
    from beanie import init_beanie

    from app.config import settings
    from app.models.content import Board, Class, Stream, Subject, Chapter, TopicEmbedding
    from app.models.user import User
    from app.models.chat import Chat
    from app.models.feedback import ChatFeedback
    from app.models.knowledge import KnowledgeObject
    from app.services.content_publisher import ContentPublisherService

    if not settings.MONGODB_URI:
        logger.error("MONGODB_URI is not set — cannot connect to MongoDB")
        sys.exit(1)

    client = AsyncMongoClient(settings.MONGODB_URI)
    await init_beanie(
        database=client[settings.MONGODB_DB_NAME],
        document_models=[
            User,
            Chat,
            ChatFeedback,
            KnowledgeObject,
            Board,
            Class,
            Stream,
            Subject,
            Chapter,
            TopicEmbedding,
        ],
    )

    logger.info(f"Connected to MongoDB database: {settings.MONGODB_DB_NAME}")

    # ── Resolve board → classes → streams → subjects ─────────────────────────
    board = await Board.find_one(Board.slug == board_slug)
    if not board:
        all_boards = await Board.find_all().to_list()
        slugs = [b.slug for b in all_boards]
        logger.error(
            f"Board '{board_slug}' not found. Available board slugs: {slugs}"
        )
        sys.exit(1)

    logger.info(f"Board: {board.name} (slug={board.slug}, id={board.id})")

    classes = await Class.find(Class.board_id == board.id).to_list()
    if not classes:
        logger.error(f"No classes found for board '{board_slug}'")
        sys.exit(1)

    class_ids = [c.id for c in classes]
    streams = await Stream.find({"class_id": {"$in": class_ids}}).to_list()
    stream_ids = [s.id for s in streams]

    subjects = await Subject.find({"stream_id": {"$in": stream_ids}}).to_list()
    subject_ids = [s.id for s in subjects]

    logger.info(
        f"Hierarchy: {len(classes)} class(es), {len(streams)} stream(s), "
        f"{len(subjects)} subject(s)"
    )

    # ── Find eligible draft chapters ─────────────────────────────────────────
    all_drafts = await Chapter.find(
        {
            "subject_id": {"$in": subject_ids},
            "status": "draft",
        }
    ).to_list()

    eligible = [
        ch for ch in all_drafts
        if ch.published_topics and ch.content_en and ch.content_en.strip()
    ]

    skipped_no_topics = sum(
        1 for ch in all_drafts if not ch.published_topics
    )
    skipped_no_content = sum(
        1 for ch in all_drafts
        if ch.published_topics and (not ch.content_en or not ch.content_en.strip())
    )

    logger.info(
        f"\nDraft chapters found: {len(all_drafts)}\n"
        f"  ✓ Eligible (have topics + content): {len(eligible)}\n"
        f"  ✗ Skipped (no topics yet):          {skipped_no_topics}\n"
        f"  ✗ Skipped (no content_en yet):      {skipped_no_content}\n"
    )

    if not eligible:
        logger.info("Nothing to publish.")
        client.close()
        return

    if limit:
        eligible = eligible[:limit]
        logger.info(f"--limit applied: processing {len(eligible)} chapter(s)")

    if dry_run:
        logger.info("DRY RUN — no changes will be made.\n")
        for i, ch in enumerate(eligible, 1):
            topic_count = len(ch.published_topics)
            word_count = ch.word_count or len((ch.content_en or "").split())
            logger.info(
                f"  [{i:3d}/{len(eligible)}] {ch.title!r:55s} "
                f"({topic_count} topics, ~{word_count} words)"
            )
        logger.info(
            f"\nDry run complete. Run without --dry-run to publish {len(eligible)} chapter(s)."
        )
        client.close()
        return

    # ── Publish ───────────────────────────────────────────────────────────────
    publisher = ContentPublisherService()
    published = 0
    errors = 0
    started_at = datetime.now(timezone.utc)

    logger.info(f"Starting bulk publish of {len(eligible)} chapter(s)...\n")

    for i, chapter in enumerate(eligible, 1):
        prefix = f"[{i:3d}/{len(eligible)}]"
        logger.info(f"{prefix} Publishing: {chapter.title!r} (id={chapter.id})")
        try:
            result = await publisher.publish_chapter(str(chapter.id))

            gcs_s    = result.get("gcs", {}).get("status", "?")
            vtx_s    = result.get("vertex_search", {}).get("status", "?")
            cf_s     = result.get("cloudflare", {}).get("status", "?")
            inow_s   = result.get("indexnow", {}).get("status", result.get("indexnow", "?"))
            emb_s    = result.get("topic_embeddings", {}).get("status", "?")
            emb_cnt  = result.get("topic_embeddings", {}).get("count", 0)
            wiki_cnt = len(result.get("wikidata", {}))

            logger.info(
                f"{prefix}   ✓ published  "
                f"gcs={gcs_s}  vtx={vtx_s}  cf={cf_s}  "
                f"indexnow={inow_s}  embeddings={emb_s}({emb_cnt})  "
                f"wikidata={wiki_cnt} uris"
            )
            published += 1

        except Exception as exc:
            logger.error(f"{prefix}   ✗ FAILED: {exc}")
            errors += 1

        if i < len(eligible):
            await asyncio.sleep(delay)

    elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()
    logger.info(
        f"\n{'─' * 60}\n"
        f"Bulk publish complete in {elapsed:.1f}s\n"
        f"  Published : {published}\n"
        f"  Errors    : {errors}\n"
        f"  Total     : {len(eligible)}\n"
    )

    client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Bulk-publish AHSEC draft chapters that have content and topics ready."
    )
    parser.add_argument(
        "--board",
        default="ahsec",
        help="Board slug to target (default: ahsec)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview eligible chapters without publishing",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Publish at most N chapters (staged rollout)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.5,
        metavar="SECS",
        help="Seconds to wait between chapters (default: 0.5)",
    )
    args = parser.parse_args()

    asyncio.run(
        main(
            board_slug=args.board,
            dry_run=args.dry_run,
            limit=args.limit,
            delay=args.delay,
        )
    )
