"""
Repair Corrupted Chapter Titles — Diagnostic Report
=====================================================
Identifies chapters whose title was clobbered by the old PDF-URL fallback bug
in upsert_chapter.  The old code did find_one({subject_id, source_pdf_url})
which returned the *first* chapter ever stored from that PDF.  Because all
chapters in a textbook share the same source PDF URL, every later chapter
grabbed the first chapter's record and the ingest loop overwrote its
chapter_number — leaving the wrong title in place forever.

SYMPTOMS
--------
• Multiple chapters in the same subject have identical titles
  (e.g. three "Physics" chapters all titled "Units and Measurements").
• chapter_number values are out of sequence or contain gaps.

HOW TO REPAIR
-------------
The upsert_chapter function has been fixed so that:

  Step 2  now narrows the PDF-URL lookup by chapter_number (primary) or
          title similarity (secondary) within the PDF's sibling set, and
          corrects the stored title+slug on the spot when they differ.

  Step 3  (chapter_number fallback for pre-fix rows) also corrects a stored
          title and regenerates its slug when the incoming title differs.

A single --force re-run of the ingestion script is therefore sufficient to
heal all corrupted rows automatically.  This script only reports the current
damage so you know which subjects to target and how many rows need repairing.

Usage (run from apps/backend/):
    python3 -m scripts.repair_chapter_titles [--subject SLUG]

Options:
    --subject SLUG    Limit scan to one subject slug (e.g. "physics").
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("repair_chapter_titles")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _content_score(ch) -> int:
    """Higher = more content — used to identify the likely original chapter."""
    score = 0
    score += len(ch.notes_en or "")
    score += len(ch.notes_as or "")
    score += len(ch.rag_sections_en or []) * 100
    score += len(ch.rag_sections_as or []) * 100
    score += len(ch.content_en or "")
    return score


# ── Main run ───────────────────────────────────────────────────────────────────

async def run(subject_filter: str | None = None) -> None:
    import motor.motor_asyncio
    from beanie import init_beanie
    from app.core.config import get_settings
    from app.models.content import (
        Board, Class, Stream, Subject, Chapter,
        ContentAuditLog, TopicEmbedding, QuestionPaper,
    )
    from app.models.rag import RagDocument, Chunk, ContentNode

    settings = get_settings()
    mongo_url = settings.MONGODB_URL
    db_name = mongo_url.split("/")[-1].split("?")[0] or "syrabit"
    client = motor.motor_asyncio.AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    await init_beanie(
        database=db,
        document_models=[
            Board, Class, Stream, Subject, Chapter,
            ContentAuditLog, TopicEmbedding, QuestionPaper,
            RagDocument, Chunk, ContentNode,
        ],
    )
    log.info(f"Connected to MongoDB database '{db_name}'")

    # ── Build filter ──────────────────────────────────────────────────────────
    query: dict = {}
    if subject_filter:
        subj = await Subject.find_one({"slug": subject_filter})
        if not subj:
            log.error(f"Subject slug '{subject_filter}' not found in DB")
            return
        query["subject_id"] = subj.id
        log.info(f"Filtering to subject: {subj.name} (id={subj.id})")

    # ── Load all chapters ─────────────────────────────────────────────────────
    all_chapters = await Chapter.find(query).to_list(length=50000)
    log.info(f"Loaded {len(all_chapters)} chapters total")

    # ── Group by (subject_id, normalised_title) to find duplicates ────────────
    # Chapters from separate subjects can legitimately share a title (e.g. every
    # subject has an "Introduction" chapter).  We only flag groups within the
    # *same subject* — that is the invariant the PDF-URL bug violated.
    groups: dict[tuple, list] = {}
    for ch in all_chapters:
        key = (str(ch.subject_id), (ch.title or "").strip().lower())
        groups.setdefault(key, []).append(ch)

    dup_groups = {k: v for k, v in groups.items() if len(v) > 1}

    if not dup_groups:
        log.info("✓ No duplicate titles found within any subject — no corruption detected.")
        return

    log.info(
        f"\n{'='*60}\n"
        f"Found {len(dup_groups)} subject(s) with duplicate chapter titles "
        f"(read-only report — no DB changes made):\n"
    )

    total_suspected_corrupted = 0

    for (subject_id, title_key), dupes in sorted(
        dup_groups.items(), key=lambda x: x[0]
    ):
        # Retrieve subject name for readable output
        subj_name: str = subject_id
        try:
            s = await Subject.get(dupes[0].subject_id)
            if s:
                subj_name = f"{s.name} (slug={s.slug})"
        except Exception:
            pass

        # Sort: highest content score first (most likely the original chapter)
        dupes.sort(key=_content_score, reverse=True)
        original = dupes[0]
        suspected_corrupted = dupes[1:]

        total_suspected_corrupted += len(suspected_corrupted)

        log.info(
            f"[DUP] Subject: {subj_name}\n"
            f"  Shared title    : '{original.title}'\n"
            f"  Likely original : #{original.chapter_number}  id={original.id} "
            f"  notes_en={len(original.notes_en or '')} chars\n"
            + "\n".join(
                f"  Suspected bad   : #{c.chapter_number}  id={c.id} "
                f"  notes_en={len(c.notes_en or '')} chars  "
                f"pdf={c.source_pdf_url or 'n/a'}"
                for c in suspected_corrupted
            )
        )

    log.info(f"\n{'='*60}")
    log.info(
        f"Total: {total_suspected_corrupted} suspected corrupted chapter(s) across "
        f"{len(dup_groups)} duplicate group(s).\n"
    )
    log.info(
        "To repair, re-run ingestion with --force.  The fixed upsert_chapter\n"
        "now narrows PDF-URL lookups by chapter_number and corrects stored titles\n"
        "in place — no manual data editing required.\n\n"
        "  cd apps/backend\n"
        "  python3 -m scripts.ahsec_ingest --medium en --force\n"
    )


# ── Entry point ────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Report chapter titles corrupted by the old PDF-URL ingestion bug. "
            "Does not modify the database — re-run ingestion with --force to repair."
        )
    )
    p.add_argument(
        "--subject", type=str, default=None,
        help="Limit to one subject slug (e.g. 'physics')",
    )
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(run(subject_filter=args.subject))
