"""
One-shot script to remove duplicate Chapter documents left by ingestion restarts.

Every time the ingestion script was restarted mid-run it created new rows for the
same chapter title (e.g. "Full Book") inside the same subject instead of reusing
the existing one.  This script:

  1. Groups chapters per subject by their normalised title.
  2. For each group with > 1 member, keeps the chapter with the most content
     (notes_en + qa_rag_sections_en populated, then raw text length as tiebreaker).
  3. Deletes the losers.

Usage (run from apps/backend/):
    # Preview what would be deleted:
    python3 -m scripts.dedup_chapters

    # Actually delete:
    python3 -m scripts.dedup_chapters --execute
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("dedup_chapters")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Deduplicate chapters by (subject_id, title)")
    p.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete duplicates (default is dry-run / preview only)",
    )
    return p.parse_args()


def _content_score(ch) -> tuple[int, int, int]:
    """Return a (has_notes_en, has_qa, total_text_len) tuple for ranking.

    Higher is richer — when comparing duplicates, pick the max.
    """
    has_notes_en   = 1 if ch.notes_en and len(ch.notes_en) > 100 else 0
    has_qa         = 1 if ch.qa_rag_sections_en else 0
    total_len      = (
        len(ch.notes_en or "")
        + len(ch.notes_as or "")
        + len(ch.qa_rag_text_en or "")
        + len(ch.rag_text_en or "")
    )
    return (has_notes_en, has_qa, total_len)


async def run(execute: bool) -> None:
    from motor.motor_asyncio import AsyncIOMotorClient
    from beanie import init_beanie
    from app.models.content import Chapter, Subject

    mongodb_url = os.environ.get("MONGODB_URL", "")
    if not mongodb_url:
        log.error("MONGODB_URL environment variable is not set")
        sys.exit(1)

    client = AsyncIOMotorClient(mongodb_url)
    # Detect DB name from URL (path component after last '/'), default syrabit_prod
    db_name = mongodb_url.rstrip("/").rsplit("/", 1)[-1].split("?")[0] or "syrabit_prod"
    log.info(f"Connecting to database: {db_name}")
    db = client[db_name]

    await init_beanie(database=db, document_models=[Chapter, Subject])

    if not execute:
        log.info("=== DRY-RUN MODE — pass --execute to actually delete ===")

    # ── 1. Load all chapters ──────────────────────────────────────────────────
    log.info("Loading all chapters…")
    all_chapters = await Chapter.find_all().to_list()
    log.info(f"  Total chapters in DB: {len(all_chapters)}")

    # ── 2. Group by (subject_id, normalised_title) ────────────────────────────
    groups: dict[tuple[str, str], list] = defaultdict(list)
    for ch in all_chapters:
        key = (str(ch.subject_id), ch.title.strip().lower())
        groups[key].append(ch)

    # ── 3. Find duplicate groups ──────────────────────────────────────────────
    dup_groups = {k: v for k, v in groups.items() if len(v) > 1}

    if not dup_groups:
        log.info("No duplicate chapters found — nothing to do.")
        return

    total_excess = sum(len(v) - 1 for v in dup_groups.values())
    log.info(
        f"Found {len(dup_groups)} duplicate groups, {total_excess} excess records to remove"
    )

    # Resolve subject names for readable logging
    subject_ids = {g[0] for g in dup_groups.keys()}
    subjects: dict[str, str] = {}
    for sid in subject_ids:
        # subject_id may be ObjectId or legacy string — fetch by _id
        try:
            from beanie import PydanticObjectId
            s = await Subject.get(PydanticObjectId(sid))
        except Exception:
            s = await Subject.find_one({"_id": sid})
        if s:
            subjects[sid] = s.name

    # ── 4. For each group: keep richest, delete the rest ─────────────────────
    deleted_total = 0
    for (subject_id, title_key), members in sorted(dup_groups.items(), key=lambda x: x[0]):
        subject_name = subjects.get(subject_id, f"<subject {subject_id}>")

        # Sort descending by content score; first element is the winner
        ranked = sorted(members, key=_content_score, reverse=True)
        winner  = ranked[0]
        losers  = ranked[1:]

        w_score = _content_score(winner)
        log.info(
            f"\n  Subject: {subject_name!r} ({subject_id})"
            f"\n  Title:   {winner.title!r}  ({len(members)} copies)"
            f"\n  KEEP:    {winner.id}  score={w_score}"
        )
        for loser in losers:
            l_score = _content_score(loser)
            log.info(f"  DELETE:  {loser.id}  score={l_score}  ch#={loser.chapter_number}")

        if execute:
            for loser in losers:
                await loser.delete()
                deleted_total += 1
            log.info(f"  → Deleted {len(losers)} duplicate(s)")
        else:
            log.info(f"  → [dry-run] Would delete {len(losers)} duplicate(s)")

    # ── 5. Summary ────────────────────────────────────────────────────────────
    if execute:
        log.info(f"\n✅ Done — deleted {deleted_total} duplicate chapter(s)")
    else:
        log.info(
            f"\n[dry-run] Would delete {total_excess} duplicate chapter(s). "
            f"Re-run with --execute to apply."
        )


def main() -> None:
    args = _parse_args()
    asyncio.run(run(execute=args.execute))


if __name__ == "__main__":
    main()
