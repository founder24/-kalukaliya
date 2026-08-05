"""
ahsec_gen_qa.py — Extract end-of-chapter exercises from AHSEC PDFs and
generate Q&A solutions for chapters that already have notes_en but empty
qa_rag_sections_en.

Usage:
    python3 -m scripts.ahsec_gen_qa               # all seeded chapters
    python3 -m scripts.ahsec_gen_qa --subject chemistry
    python3 -m scripts.ahsec_gen_qa --force       # overwrite existing Q&A
    python3 -m scripts.ahsec_gen_qa --dry-run     # print what would be done

The script re-uses the exercise extraction logic from ahsec_ingest.py
(split_into_chapters / _EN_EXERCISE_RE) so any improvements to that code
are picked up automatically.
"""

import argparse
import asyncio
import logging
import os
import sys
import re
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import motor.motor_asyncio
from beanie import init_beanie, PydanticObjectId

from app.models.content import Chapter, Subject
from app.services.ai.sarvam_client import SarvamClient

# Import exercise-extraction helpers from the main ingestion script
from scripts.ahsec_ingest import (
    _EN_EXERCISE_RE,
    _EN_SUMMARY_RE,
    _EN_QUESTION_NUM_RE,
    _AS_EXERCISE_RE,
    _clean_page_text,
    generate_qa,
    qa_to_rag_sections,
    reindex_chapter,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ahsec_gen_qa")


# ── PDF helpers ────────────────────────────────────────────────────────────────

async def _download_pdf(url: str) -> bytes:
    import httpx
    async with httpx.AsyncClient(timeout=120, verify=False) as client:
        r = await client.get(url, follow_redirects=True)
        r.raise_for_status()
        return r.content


def _extract_exercises_from_pdf(pdf_bytes: bytes, medium: str) -> str:
    """Extract the exercise block from a PDF.

    Strategy:
      1. Render all pages to text via PyMuPDF (+ Tesseract OCR fallback for
         image-only pages — mirrors ahsec_ingest.extract_pdf_text logic).
      2. Concatenate cleaned page text.
      3. Find the LAST occurrence of an exercise/question header in the full
         text — that is the end-of-chapter exercise block.
      4. Return up to 10 000 chars from that point.
    """
    import fitz  # PyMuPDF

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages_text = []
    for page in doc:
        text = page.get_text()
        if len(text.strip()) < 30:
            # Image-only page — try Tesseract OCR
            try:
                import pytesseract
                from PIL import Image
                import io
                mat = fitz.Matrix(1.5, 1.5)
                pix = page.get_pixmap(matrix=mat)
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                lang = "asm+eng" if medium == "as" else "eng"
                text = pytesseract.image_to_string(img, lang=lang, config="--psm 6")
            except Exception:
                text = ""
        pages_text.append(_clean_page_text(text))

    full_text = "\n\n".join(pages_text)

    exercise_re = _AS_EXERCISE_RE if medium == "as" else _EN_EXERCISE_RE

    # Search for the LAST occurrence of an exercise header
    ex_match = None
    for pat in (exercise_re, _EN_SUMMARY_RE, _EN_QUESTION_NUM_RE):
        all_matches = list(pat.finditer(full_text))
        if all_matches:
            ex_match = all_matches[-1]
            break

    if ex_match:
        exercises_text = full_text[ex_match.start():][:10000].strip()
        log.info(f"    Exercises found at char {ex_match.start()} / {len(full_text)} "
                 f"({len(exercises_text)} chars)")
        return exercises_text

    log.warning("    No exercise section found in PDF")
    return ""


# ── PDF URL lookup ─────────────────────────────────────────────────────────────

async def _get_pdf_url(chapter: Chapter) -> str | None:
    """Return the source PDF URL stored on the chapter."""
    # source_pdf_url is set by the ingestion script from v2 onwards
    return (
        getattr(chapter, "source_pdf_url", None)
        or getattr(chapter, "_injected_pdf_url", None)
    )


# ── Per-chapter processing ─────────────────────────────────────────────────────

async def process_chapter(
    chapter: Chapter,
    subject: Subject,
    sarvam: SarvamClient,
    medium: str,
    force: bool,
    dry_run: bool,
) -> bool:
    """Download PDF, extract exercises, generate Q&A, save to chapter."""
    ch_id = str(chapter.id)
    ch_title = chapter.title or f"Ch {chapter.chapter_number}"

    # Check if Q&A already exists
    existing_qa = (
        chapter.qa_rag_sections_en if medium == "en" else chapter.qa_rag_sections_as
    )
    if existing_qa and not force:
        log.info(f"  Skip '{ch_title}' — already has {len(existing_qa)} Q&A pairs")
        return False

    # Get the notes for context
    notes_text = (
        chapter.notes_en if medium == "en" else chapter.notes_as
    ) or ""

    # Get PDF URL
    pdf_url = await _get_pdf_url(chapter)
    if not pdf_url:
        # Try to look it up from the ingestion progress file
        progress_file = "/tmp/ahsec_ingest_progress.jsonl"
        if os.path.exists(progress_file):
            import json
            subject_slug = subject.slug or subject.name.lower().replace(" ", "-")
            with open(progress_file) as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                        key = rec.get("key", "")
                        if subject_slug in key.lower() and rec.get("pdf_url"):
                            pdf_url = rec["pdf_url"]
                            break
                    except Exception:
                        pass

    if not pdf_url:
        log.warning(f"  Skip '{ch_title}' — no PDF URL found")
        return False

    log.info(f"  Processing '{ch_title}' (subject: {subject.name})")
    log.info(f"    PDF: {pdf_url}")

    if dry_run:
        log.info(f"    [dry-run] Would download and extract exercises")
        return True

    # Download PDF
    try:
        pdf_bytes = await _download_pdf(pdf_url)
        log.info(f"    Downloaded {len(pdf_bytes):,} bytes")
    except Exception as e:
        log.error(f"    Download failed: {e}")
        return False

    # Extract exercises
    exercises_text = await asyncio.to_thread(_extract_exercises_from_pdf, pdf_bytes, medium)
    if not exercises_text or len(exercises_text) < 50:
        log.warning(f"    No exercises extracted — skipping Q&A generation")
        return False

    # Generate Q&A via Sarvam
    try:
        qa_pairs = await generate_qa(
            sarvam, exercises_text, notes_text, ch_title, subject.name, medium
        )
    except Exception as e:
        log.error(f"    Sarvam Q&A generation failed: {e}")
        return False

    if not qa_pairs:
        log.warning(f"    Sarvam returned 0 Q&A pairs")
        return False

    qa_sections = qa_to_rag_sections(qa_pairs)
    log.info(f"    Generated {len(qa_pairs)} Q&A pairs → {len(qa_sections)} RAG sections")

    # Save to DB
    now = datetime.now(timezone.utc)
    if medium == "en":
        chapter.qa_rag_sections_en = qa_sections
    else:
        chapter.qa_rag_sections_as = qa_sections
    chapter.qa_rag_updated_at = now
    chapter.updated_at = now
    await chapter.save()

    # Reindex for RAG
    try:
        await reindex_chapter(ch_id, scope="qa")
    except Exception as e:
        log.warning(f"    Reindex failed (non-fatal): {e}")

    return True


# ── PDF URL discovery from seed_runs collection ────────────────────────────────

async def _discover_pdf_urls(db) -> dict[str, str]:
    """Build a map of subject_id → pdf_url from the seed_runs collection."""
    url_map: dict[str, str] = {}
    try:
        cursor = db.seed_runs.find(
            {"pdf_url": {"$exists": True, "$gt": ""}},
            {"subject_id": 1, "pdf_url": 1, "chapter_id": 1},
        )
        runs = await cursor.to_list(1000)
        for r in runs:
            ch_id = str(r.get("chapter_id", ""))
            pdf = r.get("pdf_url", "")
            if ch_id and pdf:
                url_map[ch_id] = pdf
    except Exception as e:
        log.debug(f"seed_runs lookup failed: {e}")
    return url_map


async def _inject_pdf_urls_from_db(chapters: list[Chapter], db) -> None:
    """Attach pdf_url to each chapter from seed_runs if the chapter doesn't have one."""
    url_map = await _discover_pdf_urls(db)
    if not url_map:
        return
    for ch in chapters:
        if not getattr(ch, "pdf_url", None):
            ch.pdf_url = url_map.get(str(ch.id))  # type: ignore[attr-defined]


# ── Main ───────────────────────────────────────────────────────────────────────

async def main(args: argparse.Namespace) -> None:
    client = motor.motor_asyncio.AsyncIOMotorClient(os.environ["MONGODB_URL"])
    db_name = os.environ.get("MONGODB_DB", "syrabit_prod")
    db = client[db_name]

    await init_beanie(
        database=db,
        document_models=[Chapter, Subject],
    )

    medium = args.medium  # "en" or "as"
    notes_field = "notes_en" if medium == "en" else "notes_as"
    qa_field = "qa_rag_sections_en" if medium == "en" else "qa_rag_sections_as"

    # Find chapters that have notes but no Q&A (or --force)
    filter_q: dict = {notes_field: {"$exists": True, "$gt": ""}}
    if not args.force:
        filter_q[qa_field] = {"$size": 0}

    chapters = await Chapter.find(filter_q).to_list(500)
    log.info(f"Found {len(chapters)} chapters to process (medium={medium})")

    if args.subject:
        # Filter by subject name
        slug = args.subject.lower()
        filtered = []
        for ch in chapters:
            subj = await Subject.get(ch.subject_id)
            if subj and (slug in (subj.slug or "").lower() or slug in subj.name.lower()):
                ch._subject_cache = subj  # type: ignore[attr-defined]
                filtered.append(ch)
        log.info(f"  After subject filter '{args.subject}': {len(filtered)} chapters")
        chapters = filtered

    # Attach PDF URLs from seed_runs collection
    await _inject_pdf_urls_from_db(chapters, db)

    # Also try the progress JSONL file
    progress_urls: dict[str, str] = {}
    progress_file = "/tmp/ahsec_ingest_progress.jsonl"
    if os.path.exists(progress_file):
        import json
        with open(progress_file) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    if rec.get("chapter_id") and rec.get("pdf_url"):
                        progress_urls[rec["chapter_id"]] = rec["pdf_url"]
                except Exception:
                    pass
    for ch in chapters:
        if not getattr(ch, "pdf_url", None) and str(ch.id) in progress_urls:
            ch.pdf_url = progress_urls[str(ch.id)]  # type: ignore[attr-defined]

    sarvam = SarvamClient()
    processed = skipped = failed = 0

    for ch in chapters:
        # Resolve subject
        subj = getattr(ch, "_subject_cache", None) or await Subject.get(ch.subject_id)
        if not subj:
            log.warning(f"  No subject for chapter {ch.id}, skipping")
            skipped += 1
            continue

        try:
            ok = await process_chapter(ch, subj, sarvam, medium, args.force, args.dry_run)
            if ok:
                processed += 1
            else:
                skipped += 1
        except Exception as e:
            log.error(f"  Error processing chapter {ch.id}: {e}", exc_info=True)
            failed += 1

        await asyncio.sleep(2.0)  # polite rate limit between chapters

    log.info(f"\nDone: {processed} processed, {skipped} skipped, {failed} failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate Q&A from PDF exercises for seeded chapters")
    parser.add_argument("--medium", choices=["en", "as"], default="en")
    parser.add_argument("--subject", help="Filter by subject name/slug")
    parser.add_argument("--force", action="store_true", help="Overwrite existing Q&A")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be done, no writes")
    asyncio.run(main(parser.parse_args()))
