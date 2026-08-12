"""
clean_polluted_notes.py
-----------------------
One-shot script to fix notes_en (and notes_as) that contain model
chain-of-thought planning preamble ("Topic Selection:", "Drafting Content
for each topic", "Word Count Check:", etc.) stored from a window when
enable_thinking=True was used in the non-streaming Sarvam path.

Strategy:
  - For each chapter where notes_en exists, apply _clean_notes_output.
  - If the cleaned version is materially different (stripped ≥50 chars),
    save the chapter.
  - Dry-run by default; pass --apply to write changes.

Usage:
    cd apps/backend
    python3 -m scripts.clean_polluted_notes          # dry-run
    python3 -m scripts.clean_polluted_notes --apply  # write to DB
    python3 -m scripts.clean_polluted_notes --apply --subject "Chemistry"
"""
import asyncio
import logging
import sys
import os
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

log = logging.getLogger("clean_notes")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ── Planning preamble detector ────────────────────────────────────────────────
# Matches the actual patterns found in polluted chapters (from analysis of 8 real cases):
#   *   **Topic Selection:** ...      (bullet + bold planning label)
#   *   **Initial Plan:** ...         (bullet + bold)
#   1.  **Deconstruct the Request:**  (numbered + bold)
#   ## Constraints Analysis:          (## planning heading)
#   ## Drafting the Notes ...:        (## planning heading)
#   ## Topic Planning:                (## planning heading)
#   ## Key Themes and Elements...:    (## planning heading)
_PLANNING_SIGNALS = re.compile(
    r"(?:"
    r"^\*\s*\*\*(?:Topic\s+Selection|Initial\s+Plan|Deconstruct|Refining|"
    r"Final\s+Review|Word\s+Count|Drafting|Mental\s+Sandbox)\b"
    r"|^1\.\s+\*\*(?:Deconstruct|Topic\s+Selection|Initial\s+Plan)\b"
    r"|^##\s+(?:Constraints?\s+Analysis|Drafting|Initial\s+Plan|Deconstruct"
    r"|Topic\s+Selection|Word\s+Count|Refining|Final\s+Review"
    r"|Mental\s+Sandbox|Quick\s+Word|Content\s+Plan|Plan:|Key\s+Themes"
    r"|Topic\s+Planning|Outline|Structuring\s+the)"
    r")",
    re.MULTILINE | re.IGNORECASE,
)


def _is_polluted(text: str) -> bool:
    """Return True if the text starts with or contains planning preamble."""
    if not text:
        return False
    # Check within first 1500 chars for speed
    return bool(_PLANNING_SIGNALS.search(text[:1500]))


# Import the cleaner from the ingestion script
def _clean(text: str) -> str:
    from scripts.ahsec_ingest import _clean_notes_output  # type: ignore
    return _clean_notes_output(text)


async def main():
    apply = "--apply" in sys.argv
    subject_filter = None
    for i, arg in enumerate(sys.argv):
        if arg == "--subject" and i + 1 < len(sys.argv):
            subject_filter = sys.argv[i + 1].lower()

    from app.db.mongo import init_mongo
    from app.models.content import Chapter, Subject

    await init_mongo()
    log.info("MongoDB connected")

    # Build subject filter
    query: dict = {"status": {"$ne": "deleted"}}
    if subject_filter:
        subjs = await Subject.find({"name": {"$regex": subject_filter, "$options": "i"}}).to_list(None)
        if not subjs:
            log.warning(f"No subjects matching '{subject_filter}'")
            return
        query["subject_id"] = {"$in": [s.id for s in subjs]}
        log.info(f"Filtering to {len(subjs)} subject(s) matching '{subject_filter}'")

    chapters = await Chapter.find(query).to_list(None)
    log.info(f"Loaded {len(chapters)} chapters")

    fixed = skipped = already_clean = 0

    for ch in chapters:
        changed = False

        for field, lang in [("notes_en", "EN"), ("notes_as", "AS")]:
            raw = getattr(ch, field, None)
            if not raw or not _is_polluted(raw):
                continue

            cleaned = _clean(raw)
            stripped_chars = len(raw) - len(cleaned)

            if stripped_chars < 50:
                # Difference too small — might be a false positive, skip
                already_clean += 1
                continue

            log.info(
                f"{'[DRY-RUN] ' if not apply else ''}Fixing {lang} notes for "
                f"'{ch.title}' (ch#{ch.chapter_number}) — stripped {stripped_chars} chars"
            )

            if apply:
                setattr(ch, field, cleaned)
                # Keep legacy content_* in sync
                if field == "notes_en":
                    ch.content_en = cleaned
                elif field == "notes_as":
                    ch.content_as = cleaned
                changed = True
                fixed += 1
            else:
                # Show first 200 chars of what would be stripped
                prefix = raw[: len(raw) - len(cleaned)]
                log.info(f"  Would strip: {prefix[:200]!r}…")
                fixed += 1

        if changed and apply:
            await ch.save()

    log.info(
        f"\nDone. polluted_and_fixed={fixed}  already_clean={already_clean}  "
        f"skipped={skipped}"
    )
    if not apply:
        log.info("Run with --apply to write changes to the database.")


if __name__ == "__main__":
    asyncio.run(main())
