"""
ahsec_clear_prelim_polluted.py
------------------------------
One-shot cleanup script: finds chapters whose notes begin with publication-page
text (foreword, acknowledgements, educational philosophy, etc.) and clears them
so a subsequent ``ahsec_ingest --force`` (or ``ahsec_fill_gaps.sh``) will
regenerate clean notes.

Background
~~~~~~~~~~
When _detect_prelim_boundary() in ahsec_ingest.py misses a preliminary page
the AI faithfully summarises the foreword/acknowledgement content and produces
notes that open with headings like "## Foreword and Educational Philosophy".
This script detects those chapters and resets them to an empty state.

Strategy
~~~~~~~~
- Scan every chapter that has notes_en or notes_as content.
- Check the first 600 chars of the notes field against _NOTES_PRELIM_START_RE.
- Also check for plain-text prelim headings that appear without ## prefix
  (the AI occasionally omits the ## on the very first heading).
- In dry-run mode (default) print affected chapters; pass --apply to clear them.

Fields cleared on match (set to empty string / empty list)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
  notes_en / content_en / rag_sections_en
  notes_as / content_as / rag_sections_as
  notes_generated → False (so fill-gaps will pick the chapter up)

Usage
~~~~~
    cd apps/backend
    python3 -m scripts.ahsec_clear_prelim_polluted            # dry-run
    python3 -m scripts.ahsec_clear_prelim_polluted --apply    # write to DB
    python3 -m scripts.ahsec_clear_prelim_polluted --apply --subject Chemistry
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

log = logging.getLogger("clear_prelim")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ── Detection patterns ─────────────────────────────────────────────────────────

# Matches notes that open with a ## heading that names a prelim section.
_NOTES_PRELIM_HASH_RE = re.compile(
    r"^#{1,3}\s+(?:Foreword|Preface|Acknowledgements?|Acknowledgments?|"
    r"About\s+(?:the|this)\s+(?:Textbook|Book)|"
    r"Textbook\s+(?:Publication|Development|Overview|Information|Committee)|"
    r"Educational\s+Philosophy|Philosophy\s+of\s+Education|"
    r"National\s+Curriculum\s+Framework|"
    r"Content\s+Rationali[sz]ation|Purpose\s+and\s+Approach|"
    r"Introduction\s+to\s+(?:the\s+)?(?:Textbook|Book)|"
    r"Learning\s+Outcomes?\s+(?:and|of|for)|"
    r"To\s+the\s+(?:Teacher|Student|Reader|Learner)|"
    r"Organisation\s+of\s+(?:the\s+)?(?:Book|Textbook)|"
    r"Features\s+of\s+(?:the\s+)?(?:Book|Textbook))",
    re.IGNORECASE | re.MULTILINE,
)

# Also catches plain-text prelim headings (no ##) at the very start.
# These appear when the AI drops the ## on the first heading despite instructions.
_NOTES_PRELIM_PLAIN_RE = re.compile(
    r"^(?:Foreword|Preface|Acknowledgements?|Acknowledgments?|"
    r"About\s+(?:the|this)\s+(?:Textbook|Book)|"
    r"Textbook\s+Publication\s+Details|Educational\s+Philosophy|"
    r"National\s+Curriculum\s+Framework|Content\s+Rationali[sz]ation)",
    re.IGNORECASE,
)


def _is_prelim_polluted(text: str) -> bool:
    """Return True if the notes text begins with a prelim/publication-page heading."""
    if not text or len(text.strip()) < 10:
        return False
    head = text.strip()[:600]
    if _NOTES_PRELIM_HASH_RE.match(head):
        return True
    if _NOTES_PRELIM_PLAIN_RE.match(head):
        return True
    return False


# ── Main ───────────────────────────────────────────────────────────────────────

async def main() -> None:
    apply = "--apply" in sys.argv
    subject_filter: str | None = None
    for i, arg in enumerate(sys.argv):
        if arg == "--subject" and i + 1 < len(sys.argv):
            subject_filter = sys.argv[i + 1].lower()

    from app.db.mongo import init_mongo
    from app.models.content import Chapter, Subject

    await init_mongo()
    log.info("MongoDB connected")

    # ── Build query ───────────────────────────────────────────────────────────
    query: dict = {"status": {"$ne": "deleted"}}
    if subject_filter:
        subjs = await Subject.find(
            {"name": {"$regex": subject_filter, "$options": "i"}}
        ).to_list(None)
        if not subjs:
            log.warning(f"No subjects matching '{subject_filter}'")
            return
        query["subject_id"] = {"$in": [s.id for s in subjs]}
        log.info(f"Filtering to {len(subjs)} subject(s) matching '{subject_filter}'")

    chapters = await Chapter.find(query).to_list(None)
    log.info(f"Loaded {len(chapters)} chapters")

    cleared = skipped = 0

    for ch in chapters:
        en_polluted = _is_prelim_polluted(ch.notes_en or "")
        as_polluted = _is_prelim_polluted(ch.notes_as or "")

        if not en_polluted and not as_polluted:
            skipped += 1
            continue

        lang_tags = []
        if en_polluted:
            lang_tags.append("EN")
        if as_polluted:
            lang_tags.append("AS")

        prefix_en = (ch.notes_en or "")[:120].replace("\n", " ") if en_polluted else ""
        prefix_as = (ch.notes_as or "")[:120].replace("\n", " ") if as_polluted else ""

        log.info(
            f"{'[DRY-RUN] ' if not apply else ''}CLEARING {'/'.join(lang_tags)} notes "
            f"for '{ch.title}' (ch#{ch.chapter_number})"
        )
        if prefix_en:
            log.info(f"  EN prefix: {prefix_en!r}")
        if prefix_as:
            log.info(f"  AS prefix: {prefix_as!r}")

        if apply:
            if en_polluted:
                ch.notes_en = ""
                ch.content_en = ""
                ch.rag_sections_en = []
            if as_polluted:
                ch.notes_as = ""
                ch.content_as = ""
                ch.rag_sections_as = []
            # Mark as not-generated so fill-gaps / --force picks it up
            ch.notes_generated = False
            await ch.save()

        cleared += 1

    log.info(
        f"\nDone. cleared={cleared}  skipped={skipped}"
    )
    if not apply:
        log.info(
            "Run with --apply to clear the above chapters in the database.\n"
            "After clearing, re-ingest with:\n"
            "  cd apps/backend && bash scripts/ahsec_fill_gaps.sh\n"
            "or:\n"
            "  python3 -m scripts.ahsec_ingest --class11 --class12 --medium en --force"
        )


if __name__ == "__main__":
    asyncio.run(main())
