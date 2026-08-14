"""
ahsec_retry_blank_notes.py
==========================
Find AHSEC chapters that have blank/missing notes (< 100 chars) and unlock them
in the progress file so that a subsequent fill-gaps run will re-process them.

Usage (run from apps/backend/):
    python3 -m scripts.ahsec_retry_blank_notes [--medium en|as] [--dry-run]

What it does:
  1. Queries MongoDB for chapters where notes_en (or notes_as) is empty/short.
  2. For each blank chapter, identifies its source PDF URL from the chapter record.
  3. Removes any "done" entries for those PDF URLs from the progress JSONL file,
     so the next fill-gaps run (without --force) will re-generate notes for them.
  4. Prints a summary and suggested follow-up command.

This script is safe to run multiple times.  It never writes to MongoDB —
it only patches the local progress file.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import logging
import os
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("retry_blank_notes")

PROGRESS_FILE      = Path(__file__).parent / ".ahsec_ingest_progress.jsonl"
PROGRESS_LOCK_FILE = Path(__file__).parent / ".ahsec_ingest_progress.lock"
NOTES_MIN_CHARS    = 100  # chapters shorter than this are considered "blank"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Unlock blank AHSEC chapters for re-ingestion")
    p.add_argument("--medium", choices=["en", "as"], default=None,
                   help="Only check English or Assamese notes (default: both)")
    p.add_argument("--dry-run", action="store_true",
                   help="Report blank chapters without modifying the progress file")
    return p.parse_args()


async def find_blank_chapters(medium_filter: str | None) -> dict[str, list[dict]]:
    """
    Query MongoDB for chapters with blank notes_en and/or notes_as.

    Returns a dict keyed by source_pdf_url → list of chapter info dicts.
    Chapters without a source_pdf_url are reported but cannot be unlocked
    automatically (re-run ahsec_ingest --force --subject <name> manually).
    """
    from app.models.content import Chapter, Subject

    mediums = []
    if medium_filter in (None, "en"):
        mediums.append("en")
    if medium_filter in (None, "as"):
        mediums.append("as")

    # Load all subjects for name lookups
    subjects = await Subject.find_all().to_list(length=500)
    subj_map = {str(s.id): s.name for s in subjects}

    blank_by_pdf: dict[str, list[dict]] = defaultdict(list)
    no_pdf_url: list[dict] = []

    for medium in mediums:
        notes_field = "notes_en" if medium == "en" else "notes_as"

        # Find chapters where the notes field is None or very short
        all_chapters = await Chapter.find_all().to_list(length=5000)
        blank = [
            ch for ch in all_chapters
            if len((getattr(ch, notes_field) or "").strip()) < NOTES_MIN_CHARS
        ]

        log.info(f"Medium={medium}: {len(blank)} chapters with blank {notes_field} "
                 f"(out of {len(all_chapters)} total)")

        for ch in blank:
            info = {
                "chapter_id": str(ch.id),
                "chapter_number": ch.chapter_number,
                "title": ch.title,
                "subject_name": subj_map.get(str(ch.subject_id), str(ch.subject_id)),
                "medium": medium,
                "notes_len": len((getattr(ch, notes_field) or "").strip()),
                "source_pdf_url": ch.source_pdf_url or "",
            }
            if ch.source_pdf_url:
                blank_by_pdf[ch.source_pdf_url].append(info)
            else:
                no_pdf_url.append(info)

    if no_pdf_url:
        log.warning(
            f"\n⚠  {len(no_pdf_url)} blank chapters have no source_pdf_url stored — "
            f"they were likely never ingested.  Re-run ahsec_ingest with --force "
            f"to process them:\n"
        )
        for info in no_pdf_url:
            log.warning(
                f"  * [{info['medium'].upper()}] {info['subject_name']} "
                f"Ch.{info['chapter_number']} '{info['title']}'  "
                f"(id={info['chapter_id']})"
            )

    return blank_by_pdf


def _load_progress_lines() -> list[str]:
    if not PROGRESS_FILE.exists():
        return []
    return PROGRESS_FILE.read_text().splitlines()


def unlock_chapters_in_progress_file(
    blank_by_pdf: dict[str, list[dict]],
    dry_run: bool,
) -> int:
    """
    Remove "done" entries from the progress JSONL for each affected PDF URL.

    Removing entries for the whole PDF URL (rather than per-chapter) is safe
    because the next fill-gaps run uses the notes-presence check (notes_en > 100)
    to skip chapters that already have good content — only blank ones get re-processed.

    Returns the count of lines removed.
    """
    affected_urls = set(blank_by_pdf.keys())
    lines = _load_progress_lines()
    kept_lines: list[str] = []
    removed = 0

    for line in lines:
        try:
            rec = json.loads(line)
        except Exception:
            kept_lines.append(line)
            continue
        pdf_url = rec.get("pdf_url", "") or rec.get("key", "").split("|")[0]
        if rec.get("status") == "done" and pdf_url in affected_urls:
            removed += 1
            log.debug(f"  Unlocking: {rec.get('key', '?')} ({pdf_url[:60]}…)")
        else:
            kept_lines.append(line)

    if dry_run:
        log.info(f"[dry-run] Would remove {removed} 'done' entries from progress file")
        return removed

    if removed:
        new_content = "\n".join(kept_lines)
        if new_content and not new_content.endswith("\n"):
            new_content += "\n"
        # Acquire the same exclusive lock used by ahsec_ingest.py so we never
        # race with a concurrent ingest run that is also appending to the file.
        lf = PROGRESS_LOCK_FILE.open("a+")
        try:
            fcntl.flock(lf, fcntl.LOCK_EX)
            PROGRESS_FILE.write_text(new_content)
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)
            lf.close()
        log.info(f"Removed {removed} 'done' entries from {PROGRESS_FILE}")
    else:
        log.info("No 'done' entries found for blank chapters in progress file "
                 "(they were never marked done — fill-gaps will pick them up automatically)")

    return removed


async def main() -> None:
    args = _parse_args()

    # ── Cross-script mutex ────────────────────────────────────────────────────
    # This script only patches the progress file — it doesn't ingest.
    # Still acquire the mutex so it doesn't run while an ingest is active.
    if not args.dry_run:
        from scripts.script_lock import acquire_script_lock
        _lock_fh = acquire_script_lock("ahsec_retry_blank_notes")
        if _lock_fh is None:
            sys.exit(0)

    # ── Bootstrap MongoDB ─────────────────────────────────────────────────────
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

    # ── Find blank chapters ───────────────────────────────────────────────────
    blank_by_pdf = await find_blank_chapters(args.medium)
    total_blank = sum(len(v) for v in blank_by_pdf.values())

    if not blank_by_pdf:
        log.info("✓ No blank chapters found — all notes look good!")
        return

    log.info(f"\n{'='*60}")
    log.info(f"Blank chapters: {total_blank} across {len(blank_by_pdf)} PDFs")
    log.info(f"{'='*60}")

    # Print per-PDF summary
    for pdf_url, chapters in sorted(blank_by_pdf.items()):
        log.info(f"\nPDF: {pdf_url}")
        for ch in sorted(chapters, key=lambda x: (x["medium"], x["chapter_number"])):
            title = ch['title']
            subj  = ch['subject_name']
            log.info(
                f"  [{ch['medium'].upper()}] {subj} "
                f"Ch.{ch['chapter_number']} '{title}'  "
                f"({ch['notes_len']} chars)"
            )

    # ── Unlock in progress file ───────────────────────────────────────────────
    log.info(f"\n{'='*60}")
    removed = unlock_chapters_in_progress_file(blank_by_pdf, dry_run=args.dry_run)

    # ── Print follow-up command ───────────────────────────────────────────────
    medium_flag = f"--medium {args.medium}" if args.medium else ""
    log.info(f"\n{'='*60}")
    if args.dry_run:
        log.info("DRY-RUN complete.  Re-run without --dry-run to apply changes.")
        log.info("Then run:")
    else:
        log.info(f"Unlocked {total_blank} blank chapters.")
        log.info("Now run fill-gaps to re-generate their notes:")
    log.info(f"  cd apps/backend && bash scripts/ahsec_fill_gaps.sh")
    log.info(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
