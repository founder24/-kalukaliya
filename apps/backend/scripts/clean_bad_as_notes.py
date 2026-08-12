"""
clean_bad_as_notes.py — detect and clear Assamese notes/content that contains
model reasoning text leaked from the translation pipeline.

Patterns that indicate bad content:
  • English reasoning sentences: "This is a direct and accurate translation.",
    "Putting it together:", "Straightforward.", "Let's try another phrasing."
  • Translation-glossary lines: "English term -> অসমীয়া (Romanized)"
  • Very low Assamese Unicode density for a field claiming to be Assamese

Run:
    cd apps/backend
    python3 -m scripts.clean_bad_as_notes [--dry-run] [--field notes_as|content_as|both]

Effect:
  • Sets the affected field to None / "" in MongoDB.
  • The Assamese ingestion (--medium as --force) will then regenerate clean notes.
"""

import asyncio
import os
import re
import sys
import argparse
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── Detection patterns ────────────────────────────────────────────────────────

_REASONING_PATTERNS = re.compile(
    r'(?:'
    r'This (?:is|sounds|seems|looks) (?:a direct|correct|fine|natural|a bit)|'
    r'Putting it together[:\s]|'
    r'Let\'?s (?:try|stick with|use) |'
    r'Straightforward\.|'
    r'Direct (?:and accurate )?translation|'
    r'is (?:fine|correct)\.|'
    r'sounds a bit clunky'
    r')',
    re.IGNORECASE,
)

_GLOSS_PATTERN = re.compile(
    r'"?[A-Za-z][A-Za-z0-9 \-\'\"()]+?"?\s*->\s*.+\([A-Za-z][A-Za-z\- ]+\)',
    re.MULTILINE,
)

# Fraction of Assamese Unicode (Bengali block 0x0980-0x09FF) required
_MIN_ASSAMESE_DENSITY = 0.05

# Minimum content length — very short content isn't worth cleaning
_MIN_CONTENT_LEN = 50


def _has_reasoning_leak(text: str) -> bool:
    """Return True if the text contains model reasoning artifacts."""
    if not text or len(text) < _MIN_CONTENT_LEN:
        return False
    if _REASONING_PATTERNS.search(text):
        return True
    if _GLOSS_PATTERN.search(text):
        return True
    return False


def _assamese_density(text: str) -> float:
    if not text:
        return 0.0
    count = sum(1 for c in text if 0x0980 <= ord(c) <= 0x09FF)
    return count / max(len(text), 1)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Clear bad Assamese notes from MongoDB")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be cleared without modifying the DB")
    parser.add_argument("--field", choices=["notes_as", "content_as", "both"],
                        default="both", help="Which field(s) to inspect and clear")
    args = parser.parse_args()

    # ── Bootstrap ─────────────────────────────────────────────────────────────
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from app.db.mongo import init_mongo
    from app.config import settings

    mongo_url = os.environ.get("MONGODB_URL") or os.environ.get("MONGODB_URI")
    if mongo_url and not settings.MONGODB_URI:
        settings.MONGODB_URI = mongo_url  # type: ignore[attr-defined]
    if not settings.MONGODB_URI:
        log.error("MONGODB_URI / MONGODB_URL not set — aborting")
        sys.exit(1)

    await init_mongo()
    log.info(f"MongoDB connected — db={settings.MONGODB_DB_NAME!r}")

    from app.models.content import Chapter

    fields_to_check = (
        ["notes_as", "content_as"] if args.field == "both"
        else [args.field]
    )

    total_found = 0
    total_cleared = 0

    for field in fields_to_check:
        log.info(f"\n── Scanning '{field}' ──────────────────────────────────────────────")

        # Fetch chapters that have non-empty content in this field
        query = {field: {"$nin": [None, ""]}}
        chapters = await Chapter.find(query).to_list(length=5000)
        log.info(f"  Found {len(chapters)} chapters with {field} set")

        bad = []
        for ch in chapters:
            text = getattr(ch, field, None) or ""
            if not text or len(text) < _MIN_CONTENT_LEN:
                continue
            if _has_reasoning_leak(text):
                density = _assamese_density(text)
                bad.append((ch, field, text, density))

        log.info(f"  {len(bad)} chapters have reasoning-leak artifacts in {field}")
        total_found += len(bad)

        for ch, fld, text, density in bad:
            preview = text[:200].replace('\n', ' ')
            log.info(
                f"  [BAD] {ch.title!r} | slug={ch.slug!r} | "
                f"as_density={density:.2%} | preview: {preview!r}"
            )
            if not args.dry_run:
                await ch.update({"$set": {fld: None}})
                log.info(f"    → Cleared {fld}")
                total_cleared += 1
            else:
                log.info(f"    → [dry-run] Would clear {fld}")

    log.info(f"\n══ Summary ══════════════════════════════════════════════════════")
    log.info(f"  Chapters with bad AS content found:   {total_found}")
    if args.dry_run:
        log.info(f"  Would clear: {total_found}  (dry-run — no changes made)")
        log.info(f"\nRe-run without --dry-run to apply, then:")
    else:
        log.info(f"  Chapters cleared: {total_cleared}")
        log.info(f"\nNext step:")
    log.info(f"  Run Assamese ingestion with --force to regenerate clean notes:")
    log.info(f"    cd apps/backend && python3 -m scripts.ahsec_ingest "
             f"--class11 --class12 --medium as --force --delay 5")


if __name__ == "__main__":
    asyncio.run(main())
