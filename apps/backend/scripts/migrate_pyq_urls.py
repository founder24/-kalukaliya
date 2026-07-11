"""
Migration script: Rewrite legacy CF API PYQ URLs to the public R2 base URL.

Any chapter where `pyq_pdf_url` starts with
    https://api.cloudflare.com/client/v4/accounts/.../r2/buckets/.../objects/...
gets its URL rewritten to
    {CF_R2_PUBLIC_URL}/{object_key}

The script is idempotent — chapters that already use the public URL are skipped.

Usage (from the repo root or apps/backend/):
    cd apps/backend
    CF_R2_PUBLIC_URL=https://assets.syrabit.ai python -m scripts.migrate_pyq_urls

    # dry-run (no writes):
    DRY_RUN=1 python -m scripts.migrate_pyq_urls

Environment variables:
    MONGODB_URI      — required (or set MONGODB_URL)
    CF_R2_PUBLIC_URL — required; the public base URL, e.g. https://assets.syrabit.ai
    DRY_RUN          — optional; set to 1 / true to preview changes without writing
"""

import asyncio
import logging
import os
import re
import sys
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Pattern:  ...accounts/<id>/r2/buckets/<bucket>/objects/<key>
# Capture group 1 = everything after /objects/
_LEGACY_URL_RE = re.compile(
    r"^https://api\.cloudflare\.com/client/v4/accounts/[^/]+/r2/buckets/[^/]+/objects/(.+)$"
)


async def main() -> None:
    from pymongo import AsyncMongoClient
    from beanie import init_beanie
    from app.config import settings
    from app.models.content import (
        Board, Chapter, Class, Stream, Subject, TopicEmbedding,
    )
    from app.models.user import User

    # ── Resolve configuration ────────────────────────────────────────────────
    dry_run_raw = os.environ.get("DRY_RUN", "").strip().lower()
    dry_run = dry_run_raw in ("1", "true", "yes")

    public_url = settings.CF_R2_PUBLIC_URL or os.environ.get("CF_R2_PUBLIC_URL", "")
    if not public_url:
        logger.error(
            "CF_R2_PUBLIC_URL is not set. "
            "Pass it as an env var, e.g.  CF_R2_PUBLIC_URL=https://assets.syrabit.ai"
        )
        sys.exit(1)
    public_url = public_url.rstrip("/")

    if not settings.MONGODB_URI:
        logger.error("MONGODB_URI is not set (try MONGODB_URL for Replit compatibility)")
        sys.exit(1)

    if dry_run:
        logger.info("DRY RUN — no changes will be written to the database")

    # ── Connect ──────────────────────────────────────────────────────────────
    client = AsyncMongoClient(settings.MONGODB_URI)
    await init_beanie(
        database=client[settings.MONGODB_DB_NAME],
        document_models=[User, Board, Class, Stream, Subject, Chapter, TopicEmbedding],
    )
    logger.info("Connected to MongoDB: %s", settings.MONGODB_DB_NAME)

    # ── Scan chapters ────────────────────────────────────────────────────────
    # Fetch only chapters that have a pyq_pdf_url set; filter in Python so we
    # don't need a regex index on the collection.
    chapters = await Chapter.find(
        {"pyq_pdf_url": {"$exists": True, "$ne": None, "$ne": ""}}
    ).to_list(length=10_000)

    logger.info("Chapters with pyq_pdf_url: %d", len(chapters))

    total = 0
    updated = 0
    skipped_already_public = 0
    skipped_unrecognised = 0

    for ch in chapters:
        total += 1
        old_url = ch.pyq_pdf_url or ""

        m = _LEGACY_URL_RE.match(old_url)
        if not m:
            if old_url.startswith(public_url) or not old_url.startswith("https://api.cloudflare.com"):
                skipped_already_public += 1
            else:
                skipped_unrecognised += 1
                logger.warning(
                    "Chapter %s has an unrecognised pyq_pdf_url pattern — skipping: %s",
                    ch.id, old_url[:120],
                )
            continue

        object_key = m.group(1)
        new_url = f"{public_url}/{object_key}"

        logger.info(
            "Chapter %s  %s\n  OLD: %s\n  NEW: %s",
            ch.id, ch.title[:60], old_url, new_url,
        )

        if not dry_run:
            ch.pyq_pdf_url = new_url
            ch.updated_at = datetime.now(timezone.utc)
            await ch.save()

        updated += 1

    # ── Summary ──────────────────────────────────────────────────────────────
    logger.info(
        "\n"
        "=== Migration complete%s ===\n"
        "  Total chapters with pyq_pdf_url : %d\n"
        "  Rewritten to public R2 URL      : %d\n"
        "  Already on public URL (skipped) : %d\n"
        "  Unrecognised pattern (skipped)  : %d",
        " (DRY RUN)" if dry_run else "",
        total,
        updated,
        skipped_already_public,
        skipped_unrecognised,
    )

    if dry_run and updated > 0:
        logger.info(
            "Re-run without DRY_RUN=1 to apply the %d rewrite(s) above.", updated
        )


if __name__ == "__main__":
    asyncio.run(main())
