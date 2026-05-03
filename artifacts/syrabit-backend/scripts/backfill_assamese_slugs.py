"""scripts/backfill_assamese_slugs.py — Task #295.

Generates and stores an Assamese URL slug (``chapters.slug_as``) for every
published chapter that already has an Assamese body (``content_as``). The
slug becomes the chapter segment of the dedicated ``/as/<board>/<class>/
<subject>/<slug_as>`` SPA route, replacing the previous ``?lang=as`` query
string variant in hreflang and sitemap output (Google treats path-based
language variants as much stronger SEO signals).

Pipeline per chapter:
  1. Skip if ``slug_as`` already populated (idempotent).
  2. Translate the English chapter ``title`` → Assamese using Sarvam
     translate:v1 (the same provider that produced ``content_as`` in
     scripts/translate workflows, so terminology stays consistent).
  3. Slugify with ``utils.slugify_title`` which preserves the Bengali
     unicode block (U+0980–U+09FF), strips punctuation, collapses
     whitespace into hyphens, and falls back to an English-derived
     slug if the translation comes back empty / latin-only.
  4. Persist with ``$set: {slug_as, slug_as_updated_at}``.

Usage::

    MONGO_URL=… SARVAM_API_KEY=… \\
        python -m scripts.backfill_assamese_slugs [--limit 200] [--force]

Flags:
  --limit N   Process at most N chapters this run (default: 500).
  --force     Re-translate even when slug_as is already set.
  --dry-run   Print proposed slug_as values without writing to Mongo.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient

logger = logging.getLogger("backfill_assamese_slugs")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")


async def _translate_title(http: Any, title: str) -> str:
    """Translate one chapter title using Sarvam translate:v1.

    Returns the translated string, or "" on any failure (caller falls
    back to slugifying the English title under /as/).
    """
    if not title or len(title.strip()) < 2:
        return ""
    try:
        resp = await asyncio.wait_for(
            http.post("/translate", json={
                "input": title.strip()[:500],
                "source_language_code": "en-IN",
                "target_language_code": "as-IN",
                "speaker_gender": "Female",
                "mode": "formal",
                "model": "sarvam-translate:v1",
                "enable_preprocessing": False,
            }),
            timeout=10.0,
        )
        if resp.status_code != 200:
            logger.warning("sarvam translate HTTP %d for title=%r", resp.status_code, title[:60])
            return ""
        return ((resp.json() or {}).get("translated_text") or "").strip()
    except Exception as exc:
        logger.warning("sarvam translate error for title=%r: %s", title[:60], exc)
        return ""


async def _run(limit: int, force: bool, dry_run: bool) -> int:
    mongo_url = os.environ.get("MONGO_URL") or os.environ.get("MONGODB_URI")
    if not mongo_url:
        logger.error("MONGO_URL / MONGODB_URI not set — aborting")
        return 2
    sarvam_key = os.environ.get("SARVAM_API_KEY")
    if not sarvam_key:
        logger.error("SARVAM_API_KEY not set — aborting")
        return 2

    # Make script importable from anywhere by ensuring backend root on path.
    here = os.path.dirname(os.path.abspath(__file__))
    backend_root = os.path.abspath(os.path.join(here, ".."))
    if backend_root not in sys.path:
        sys.path.insert(0, backend_root)
    from utils import slugify_title  # noqa: E402

    import httpx  # noqa: E402

    client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=8000)
    db_name = os.environ.get("DB_NAME") or client.get_default_database().name
    db = client[db_name]

    query: dict = {
        "status": "published",
        "content_as": {"$exists": True, "$ne": ""},
        "title": {"$exists": True, "$ne": ""},
    }
    if not force:
        query["$or"] = [{"slug_as": {"$exists": False}}, {"slug_as": ""}]

    chapters = await db.chapters.find(
        query, {"_id": 0, "id": 1, "title": 1, "slug": 1, "slug_as": 1},
    ).limit(limit).to_list(length=limit)

    total = len(chapters)
    logger.info("found %d chapters needing slug_as (limit=%d, force=%s, dry_run=%s)",
                total, limit, force, dry_run)
    if not total:
        return 0

    sarvam = httpx.AsyncClient(
        base_url="https://api.sarvam.ai",
        headers={"api-subscription-key": sarvam_key,
                 "content-type": "application/json"},
        timeout=15.0,
    )

    translated_n = fallback_n = failed_n = 0
    t0 = time.perf_counter()
    try:
        for ch in chapters:
            title = ch.get("title", "")
            translated = await _translate_title(sarvam, title)
            if translated:
                slug_as = slugify_title(translated)
                source = "translation"
                if not slug_as:
                    # Translation came back but slug came out empty (all
                    # punctuation / unsupported chars). Fall back below.
                    translated = ""
            if translated:
                translated_n += 1
            else:
                # Fall back to slugifying the English title under /as/ —
                # the URL will at least render a meaningful path until
                # a manual edit improves it.
                slug_as = slugify_title(title) or (ch.get("slug") or "")
                source = "fallback-en"
                fallback_n += 1

            if not slug_as:
                failed_n += 1
                logger.warning("could not derive slug_as for chapter %s (%r)",
                               ch.get("id"), title[:60])
                continue

            logger.info("[%s] %s  →  %s", source, title[:60], slug_as)
            if dry_run:
                continue
            await db.chapters.update_one(
                {"id": ch["id"]},
                {"$set": {"slug_as": slug_as,
                          "slug_as_updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                              time.gmtime())}},
            )
    finally:
        await sarvam.aclose()
        client.close()

    elapsed = time.perf_counter() - t0
    logger.info("done — translated=%d fallback=%d failed=%d total=%d in %.1fs",
                translated_n, fallback_n, failed_n, total, elapsed)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--force", action="store_true",
                        help="Re-translate even when slug_as is already set.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print proposed slug_as values without writing.")
    args = parser.parse_args()
    rc = asyncio.run(_run(limit=args.limit, force=args.force, dry_run=args.dry_run))
    sys.exit(rc)


if __name__ == "__main__":
    main()
