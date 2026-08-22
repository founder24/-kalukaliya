"""
ahsec_gen_qa.py — Generate Q&A for AHSEC chapters that already have notes_en
but empty qa_rag_sections_en.

Q&A is generated directly from the chapter notes (no PDF download required).
Sarvam creates exam-style questions and model answers from the notes content.

Usage:
    python3 -m scripts.ahsec_gen_qa               # all chapters missing Q&A
    python3 -m scripts.ahsec_gen_qa --medium as   # Assamese medium
    python3 -m scripts.ahsec_gen_qa --subject chemistry
    python3 -m scripts.ahsec_gen_qa --force       # overwrite existing Q&A
    python3 -m scripts.ahsec_gen_qa --dry-run     # print what would be done
"""

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.ahsec_ingest import (
    generate_qa_from_notes,
    qa_to_rag_sections,
    reindex_chapter,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ahsec_gen_qa")


async def main(args: argparse.Namespace) -> None:
    from app.db.mongo import init_mongo
    from app.config import settings
    from app.models.content import Chapter, Subject
    from app.services.ai.workers_ai_client import workers_ai_client

    # Bootstrap DB + secrets
    mongo_url = os.environ.get("MONGODB_URL") or os.environ.get("MONGODB_URI")
    if mongo_url and not settings.MONGODB_URI:
        settings.MONGODB_URI = mongo_url  # type: ignore[attr-defined]

    await init_mongo()
    log.info(f"MongoDB connected — db={settings.MONGODB_DB_NAME!r}")

    if not settings.EDGE_SHARED_SECRET and not args.dry_run:
        log.error("EDGE_SHARED_SECRET not set — cannot authenticate Workers AI generation")
        sys.exit(1)

    medium = args.medium  # "en" or "as"
    notes_field = "notes_en" if medium == "en" else "notes_as"
    qa_field = "qa_rag_sections_en" if medium == "en" else "qa_rag_sections_as"

    # Find chapters that have notes but no Q&A (unless --force)
    filter_q: dict = {notes_field: {"$exists": True, "$gt": ""}}
    if not args.force:
        filter_q[qa_field] = {"$size": 0}

    chapters = await Chapter.find(filter_q).to_list(500)
    log.info(f"Found {len(chapters)} chapters to process (medium={medium})")

    # Optional subject filter
    if args.subject:
        slug = args.subject.lower()
        filtered = []
        for ch in chapters:
            subj = await Subject.get(ch.subject_id)
            if subj and (slug in (subj.slug or "").lower() or slug in subj.name.lower()):
                ch._subject_cache = subj  # type: ignore[attr-defined]
                filtered.append(ch)
        log.info(f"  After subject filter '{args.subject}': {len(filtered)} chapters")
        chapters = filtered

    processed = skipped = failed = 0

    for ch in chapters:
        ch_title = ch.title or f"Chapter {ch.chapter_number}"
        notes_text = (ch.notes_en if medium == "en" else ch.notes_as) or ""

        if not notes_text or len(notes_text) < 100:
            log.warning(f"  Skip '{ch_title}' — no notes content")
            skipped += 1
            continue

        # Resolve subject name
        subj = getattr(ch, "_subject_cache", None) or await Subject.get(ch.subject_id)
        if not subj:
            log.warning(f"  Skip '{ch_title}' — subject not found")
            skipped += 1
            continue

        log.info(f"  {subj.name} / '{ch_title}' ({len(notes_text)} note chars)")

        if args.dry_run:
            log.info(f"    [dry-run] Would generate Q&A from notes")
            processed += 1
            continue

        try:
            qa_pairs = await generate_qa_from_notes(
                workers_ai_client, notes_text, ch_title, subj.name, medium
            )
        except Exception as e:
            log.error(f"    Workers AI generation failed: {e}")
            failed += 1
            await asyncio.sleep(3.0)
            continue

        if not qa_pairs:
            log.warning(f"    Workers AI returned 0 Q&A pairs — skipping")
            skipped += 1
            await asyncio.sleep(2.0)
            continue

        log.info(f"    Generated {len(qa_pairs)} Q&A pairs")

        qa_sections = qa_to_rag_sections(qa_pairs)
        now = datetime.now(timezone.utc)
        if medium == "en":
            ch.qa_rag_sections_en = qa_sections
        else:
            ch.qa_rag_sections_as = qa_sections
        ch.qa_rag_updated_at = now
        ch.updated_at = now
        await ch.save()

        try:
            await reindex_chapter(str(ch.id), scope="qa")
        except Exception as e:
            log.warning(f"    Reindex failed (non-fatal): {e}")

        processed += 1
        await asyncio.sleep(2.0)  # polite rate-limit pause

    log.info(f"\nDone: {processed} processed, {skipped} skipped, {failed} failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate Q&A from notes for AHSEC chapters missing Q&A"
    )
    parser.add_argument("--medium", choices=["en", "as"], default="en")
    parser.add_argument("--subject", help="Filter by subject name/slug")
    parser.add_argument("--force", action="store_true", help="Overwrite existing Q&A")
    parser.add_argument("--dry-run", action="store_true", help="No writes")
    asyncio.run(main(parser.parse_args()))
