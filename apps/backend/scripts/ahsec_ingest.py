"""
AHSEC Textbook Content Ingestion Pipeline
==========================================
Crawls AHSEC HS 1st-year (Class 11) and 2nd-year (Class 12) textbook pages,
downloads English and Assamese medium PDFs, extracts chapter text via PyMuPDF,
generates concise notes + Q&A solutions using Sarvam AI, and populates each
chapter's notes, RAG sections and Q&A RAG sections in MongoDB.

Usage (run from apps/backend/):
    python3 -m scripts.ahsec_ingest [options]

Options:
    --class11           Class 11 only
    --class12           Class 12 only
    --medium en         English medium only
    --medium as         Assamese medium only
    --subject SLUG      Single subject slug (e.g. chemistry, physics)
    --dry-run           Parse + extract but skip all DB writes and Sarvam calls
    --force             Re-process chapters that already have notes content
    --limit N           Stop after N chapters (useful for pilots)
    --delay S           Seconds between Sarvam calls (default 1.5)
    --pilot             Shorthand: Chemistry XI EN + Biology XI AS only

Progress is logged to /tmp/ahsec_ingest_progress.jsonl for resume support.
On re-run without --force, already-completed chapters are skipped.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import ssl
import sys
import tempfile
import time
import unicodedata
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/tmp/ahsec_ingest.log"),
    ],
)
log = logging.getLogger("ahsec_ingest")

# ── CLI args ───────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="AHSEC textbook ingestion pipeline")
    p.add_argument("--class11",  action="store_true")
    p.add_argument("--class12",  action="store_true")
    p.add_argument("--medium",   choices=["en", "as"])
    p.add_argument("--subject",  type=str, default=None)
    p.add_argument("--dry-run",  action="store_true")
    p.add_argument("--force",    action="store_true")
    p.add_argument("--limit",    type=int, default=None)
    p.add_argument("--delay",    type=float, default=1.5)
    p.add_argument("--pilot",    action="store_true")
    return p.parse_args()


# ── Progress log ───────────────────────────────────────────────────────────────

PROGRESS_FILE = Path("/tmp/ahsec_ingest_progress.jsonl")


def _load_done_keys() -> set[str]:
    """Return a set of '<pdf_url>|<chapter_num>' strings already finished."""
    done: set[str] = set()
    if not PROGRESS_FILE.exists():
        return done
    for line in PROGRESS_FILE.read_text().splitlines():
        try:
            rec = json.loads(line)
            if rec.get("status") == "done":
                done.add(rec["key"])
        except Exception:
            pass
    return done


def _log_progress(key: str, status: str, detail: str = "") -> None:
    with PROGRESS_FILE.open("a") as f:
        f.write(json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "key": key,
            "status": status,
            "detail": detail,
        }) + "\n")


# ── AHSEC PDF Catalogue ────────────────────────────────────────────────────────

def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _fetch_html(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, context=_ssl_ctx(), timeout=20) as r:
        return r.read().decode("utf-8", errors="replace")


def _slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[\s_-]+", "-", text).strip("-")


# Non-academic vocational subjects to skip
_SKIP_KEYWORDS = {
    "it-ites", "beauty", "wellness", "apparel", "home furnishing",
    "health care", "healthcare", "automotive", "agriculture", "floriculturist",
    "electronics", "retail", "travel", "tourism", "private security", "dairy",
    "power", "food processing", "media entertainment", "telecom",
    "physical education", "bihu", "financial literacy",
}

# MIL / regional-language textbooks to skip (keep only EN + AS)
_SKIP_MIL_KEYWORDS = {
    "bangla", "bengali", "hindi", "garo", "manipuri", "bodo", "sanskrit",
    "arabic", "vitan", "aroh", "sahitya", "saurav", "chayanika", "chijak",
    "anouba", "sujunai", "thunlai", "uchchatar", "advance bengali",
    "prithibir itihas",
}


def build_catalogue(class11: bool = True, class12: bool = True) -> list[dict]:
    """
    Fetch both AHSEC textbook pages and return a list of entries:
      {subject_name, class_level, medium, pdf_url, part_num, subject_slug}

    Filters: English (E) and Assamese (A) medium only; academic subjects only.
    """
    pages = []
    if class11:
        pages.append(("11", "https://ahsec.assam.gov.in/index.php/hs-1st-year-textbooks/"))
    if class12:
        pages.append(("12", "https://ahsec.assam.gov.in/index.php/hs-2nd-year-textbooks/"))

    entries = []
    for class_level, url in pages:
        log.info(f"Fetching catalogue page for Class {class_level}…")
        try:
            html = _fetch_html(url)
        except Exception as e:
            log.error(f"Failed to fetch Class {class_level} catalogue: {e}")
            continue

        # Extract all anchor links
        anchors = re.findall(
            r'<a[^>]+href=["\']([^"\']+\.pdf[^"\']*)["\'][^>]*>(.*?)</a>',
            html, re.DOTALL | re.IGNORECASE
        )
        for pdf_url, raw_text in anchors:
            text = re.sub(r"<[^>]+>", "", raw_text).strip()
            text = re.sub(r"\s+", " ", text)
            text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")

            if not text or len(text) < 3:
                continue

            # Determine medium
            if re.search(r"\(E\)", text):
                medium = "en"
            elif re.search(r"\(A\)", text):
                medium = "as"
            else:
                continue  # skip non-EN / non-AS

            # Skip vocational + MIL
            text_lower = text.lower()
            if any(kw in text_lower for kw in _SKIP_KEYWORDS):
                log.debug(f"  Skip vocational: {text}")
                continue
            if any(kw in text_lower for kw in _SKIP_MIL_KEYWORDS):
                log.debug(f"  Skip MIL: {text}")
                continue

            # Normalise subject name (strip medium marker)
            subj_name = re.sub(r"\s*\(E\)\s*|\s*\(A\)\s*", "", text).strip()
            subj_name = re.sub(r"\s*\|\|.*", "", subj_name).strip()

            # Extract part number
            part_match = re.search(r"Part[- ]+(I{1,3}|[12])\b", subj_name, re.IGNORECASE)
            part_num = 1
            if part_match:
                pstr = part_match.group(1).upper()
                part_num = {"I": 1, "II": 2, "III": 3}.get(pstr, int(pstr) if pstr.isdigit() else 1)

            # Canonical subject name (strip Part I/II for grouping)
            canonical = re.sub(r"\s*Part[- ]+(I{1,3}|[12])\b", "", subj_name, flags=re.IGNORECASE).strip()

            # Deduplicate: prefer newer URLs (2025 > 2023) when the same
            # subject+class+medium already appears.
            existing = next(
                (e for e in entries
                 if e["subject_slug"] == _slug(canonical)
                 and e["class_level"] == class_level
                 and e["medium"] == medium
                 and e["part_num"] == part_num),
                None
            )
            if existing:
                # Prefer the 2025 URL over the 2023 one
                if "2025" in pdf_url and "2025" not in existing["pdf_url"]:
                    existing["pdf_url"] = pdf_url
                continue

            entries.append({
                "subject_name":  canonical,
                "subject_slug":  _slug(canonical),
                "class_level":   class_level,
                "medium":        medium,
                "pdf_url":       pdf_url,
                "part_num":      part_num,
                "book_label":    subj_name,   # full label incl. Part I/II
            })

    log.info(f"Catalogue built: {len(entries)} PDF entries")
    return entries


# ── PDF Text Extraction ────────────────────────────────────────────────────────

def _download_pdf(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, context=_ssl_ctx(), timeout=60) as r:
        return r.read()


def _ocr_page(page, lang: str = "asm+eng") -> str:
    """Render a PyMuPDF page to an image and OCR it with Tesseract."""
    import pytesseract
    from PIL import Image
    import io

    # Render at 2× zoom for better OCR quality (300 dpi equivalent)
    matrix = __import__("fitz").Matrix(2.0, 2.0)
    pix = page.get_pixmap(matrix=matrix, colorspace=__import__("fitz").csRGB)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    return pytesseract.image_to_string(img, lang=lang, config="--psm 3")


def extract_pdf_text(url: str, medium: str = "en") -> list[dict]:
    """
    Download a PDF and extract text per page using PyMuPDF (fitz).
    For Assamese PDFs: if a page's embedded text is garbled (non-Unicode
    Assamese font), fall back to Tesseract OCR with lang='asm+eng'.
    Returns [{page_num, text}].  Pages with < 20 chars are skipped.
    """
    import fitz  # PyMuPDF

    data = _download_pdf(url)
    pages = []
    ocr_count = 0

    with fitz.open(stream=data, filetype="pdf") as doc:
        for i, page in enumerate(doc):
            text = page.get_text("text")
            text = re.sub(r"\n{3,}", "\n\n", text).strip()

            # For Assamese medium: if the embedded text looks garbled
            # (very few actual Assamese Unicode chars), fall back to OCR.
            if medium == "as" and len(text) > 30 and not _is_readable_assamese(text):
                try:
                    text = _ocr_page(page, lang="asm+eng")
                    text = re.sub(r"\n{3,}", "\n\n", text).strip()
                    ocr_count += 1
                except Exception as e:
                    log.debug(f"    OCR failed p{i+1}: {e}")

            if len(text) >= 20:
                pages.append({"page_num": i + 1, "text": text})

    if ocr_count:
        log.info(f"  OCR used on {ocr_count}/{len(pages)} pages (Assamese non-Unicode font)")
    return pages


# ── Chapter Boundary Detection ─────────────────────────────────────────────────

# TOC entry pattern: "Unit N" or "Chapter N" on its own line (English)
_TOC_ENTRY_RE = re.compile(
    r"^(?:Unit|UNIT|Chapter|CHAPTER)\s+(\d+)\s*\n([^\n]{5,120})\n\s*(\d+)\s*$",
    re.MULTILINE,
)
# Simpler fallback: "Unit N" followed (within 3 lines) by a page number (English)
_TOC_UNIT_RE = re.compile(
    r"^(?:Unit|UNIT|Chapter|CHAPTER)\s+(\d+)\s*\n(.{5,120})",
    re.MULTILINE,
)
# Assamese chapter heading: "অধ্যায় - N" or "অধ্যায় N" (OCR output)
_TOC_AS_RE = re.compile(
    r"^অধ্যায়\s*[-–—]?\s*([০-৯\d]+)\s*\n(.{5,120})",
    re.MULTILINE,
)
_AS_DIGIT_MAP = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")

# Exercise / questions section markers (English)
_EN_EXERCISE_RE = re.compile(
    r"^(?:EXERCISES?|Summary|SUMMARY|QUESTIONS?|PROBLEMS?|REVIEW\s+QUESTIONS|"
    r"ACTIVITIES|PRACTICE\s+PROBLEMS?|SELF[\s-]?ASSESSMENT)\b",
    re.MULTILINE | re.IGNORECASE,
)
# Assamese exercises (Unicode)
_AS_EXERCISE_RE = re.compile(
    r"^(?:অনুশীলনী|প্ৰশ্নোত্তৰ|চমু প্ৰশ্ন|দীঘলীয়া প্ৰশ্ন|অতি চমু|"
    r"বহু বিকল্প|অতিৰিক্ত প্ৰশ্ন|মূল্যায়ন)\b",
    re.MULTILINE,
)


def _is_readable_assamese(text: str) -> bool:
    """Return True if text contains a meaningful proportion of Assamese Unicode glyphs."""
    if not text:
        return False
    assamese_count = sum(1 for c in text if 0x0980 <= ord(c) <= 0x09FF)
    return assamese_count / max(len(text), 1) > 0.05


def _parse_toc(pages_text: str) -> list[dict]:
    """
    Parse a table of contents to extract {chapter_num, title, start_page}.
    Handles both English (Unit N / Chapter N) and Assamese (অধ্যায় N) patterns.
    Returns a list sorted by start_page.
    """
    entries: list[dict] = []

    # ── English: strict three-line pattern ───────────────────────────────────
    for m in _TOC_ENTRY_RE.finditer(pages_text):
        num   = int(m.group(1))
        title = m.group(2).strip()
        page  = int(m.group(3))
        if not title or page < 1 or page > 999:
            continue
        entries.append({"chapter_num": num, "title": title, "start_page": page})

    if entries:
        return sorted(entries, key=lambda x: x["start_page"])

    # ── English: looser — Unit/Chapter N + title + nearby page number ────────
    for m in _TOC_UNIT_RE.finditer(pages_text):
        num   = int(m.group(1))
        title = m.group(2).strip()
        snippet = pages_text[m.end():m.end() + 150]
        pnum_m = re.search(r"^\s*(\d{1,4})\s*$", snippet, re.MULTILINE)
        if pnum_m:
            page = int(pnum_m.group(1))
            if 1 <= page <= 999 and title:
                entries.append({"chapter_num": num, "title": title, "start_page": page})

    if entries:
        return sorted(entries, key=lambda x: x["start_page"])

    # ── Assamese: "অধ্যায় - N\nTitle" (OCR output) ──────────────────────────
    for m in _TOC_AS_RE.finditer(pages_text):
        raw_num = m.group(1).translate(_AS_DIGIT_MAP)
        try:
            num = int(raw_num)
        except ValueError:
            continue
        title = m.group(2).strip()
        # Find the next standalone number as the start page
        snippet = pages_text[m.end():m.end() + 200]
        pnum_m = re.search(r"^\s*([০-৯\d]{1,4})\s*$", snippet, re.MULTILINE)
        if pnum_m:
            page_raw = pnum_m.group(1).translate(_AS_DIGIT_MAP)
            try:
                page = int(page_raw)
            except ValueError:
                page = 0
            if 1 <= page <= 999 and title:
                entries.append({"chapter_num": num, "title": title, "start_page": page})

    return sorted(entries, key=lambda x: x["start_page"])


def split_into_chapters(pages: list[dict], medium: str) -> list[dict]:
    """
    Split PDF pages into chapters using the table of contents.
    Strategy:
      1. Find TOC in the first 20 pages
      2. Parse chapter list with start page numbers
      3. Extract text page-range for each chapter
      4. Detect exercises block within each chapter

    Falls back to treating the whole PDF as a single chunk if no TOC found.
    """
    exercise_re = _AS_EXERCISE_RE if medium == "as" else _EN_EXERCISE_RE

    # Build page index: page_num (1-based) → text
    page_map = {p["page_num"]: p["text"] for p in pages}
    max_page = max(page_map.keys()) if page_map else 0

    # Try to find and parse the TOC (search first 20 pages)
    toc_text = "\n".join(page_map.get(i, "") for i in range(1, min(21, max_page + 1)))
    toc_entries = _parse_toc(toc_text)

    # For Assamese PDFs: TOC chapter numbers are often garbled by OCR (stylised fonts).
    # Fall back to body-text chapter heading detection if the TOC is missing or corrupt.
    if not toc_entries or (medium == "as" and _toc_is_degenerate(toc_entries)):
        if medium == "as":
            return _split_assamese_by_body(pages, exercise_re)
        # English fallback: whole book as one blob
        full_text = "\n\n".join(p["text"] for p in sorted(pages, key=lambda x: x["page_num"]))
        full_text = _clean_page_text(full_text)
        return [{
            "chapter_num": 1,
            "title": "Full Book",
            "body_text": full_text[:12000],
            "exercises_text": "",
        }]

    log.info(f"    TOC found: {len(toc_entries)} chapters/units")

    # ── Resolve actual PDF page numbers via title search ──────────────────────
    # The TOC page numbers refer to the *book's printed pagination*, which is
    # offset from the PDF page index by a prelim section (cover, foreword, TOC).
    # We find each chapter's real starting PDF page by searching for its title.
    resolved = _resolve_chapter_pages(pages, toc_entries, max_page)
    if not resolved:
        log.warning("    Could not resolve chapter pages via title search — skipping")
        return []

    log.info(f"    Resolved {len(resolved)} chapter start pages via title search")

    chapters = []
    for idx, entry in enumerate(resolved):
        ch_num   = entry["chapter_num"]
        title    = entry["title"]
        pg_start = entry["pdf_start"]
        pg_end   = resolved[idx + 1]["pdf_start"] - 1 if idx + 1 < len(resolved) else max_page

        # Gather text for these pages
        chapter_pages_text = "\n\n".join(
            _clean_page_text(page_map[p])
            for p in range(pg_start, pg_end + 1)
            if p in page_map and len(page_map[p].strip()) > 30
        )

        if len(chapter_pages_text) < 150:
            log.debug(f"    Ch {ch_num} '{title}': too short ({len(chapter_pages_text)} chars), skipping")
            continue

        # Separate exercises block from body
        body_text = chapter_pages_text
        exercises_text = ""
        ex_match = exercise_re.search(chapter_pages_text)
        if ex_match:
            body_text = chapter_pages_text[:ex_match.start()]
            exercises_text = chapter_pages_text[ex_match.start():]

        body_text = body_text[:12000].strip()
        exercises_text = exercises_text[:6000].strip()

        chapters.append({
            "chapter_num":    ch_num,
            "title":          title[:200],
            "body_text":      body_text,
            "exercises_text": exercises_text,
        })

    return chapters


def _resolve_chapter_pages(
    pages: list[dict],
    toc_entries: list[dict],
    max_page: int,
) -> list[dict]:
    """
    Map each TOC entry to its actual PDF page number by searching for the
    chapter title in the page text.  The TOC page numbers are *book* page
    numbers which differ from PDF page numbers by the prelim offset.

    Returns [{chapter_num, title, pdf_start}] sorted by pdf_start.
    """
    # Build a searchable list of (pdf_page_num, text)
    page_list = sorted(pages, key=lambda x: x["page_num"])

    # Detect the prelim boundary: first page that looks like TOC content.
    # We'll skip pages that appear to be the TOC itself when searching.
    def _is_toc_or_prelim(text: str) -> bool:
        lower = text.lower()
        # A page is TOC-like if it says "contents" or lists 3+ standalone numbers
        # (page number lines) in a short stretch — characteristic of a TOC page.
        if "contents" in lower:
            return True
        standalone_nums = re.findall(r"^\s*\d{1,3}\s*$", text, re.MULTILINE)
        return len(standalone_nums) >= 3

    resolved: list[dict] = []
    search_from = 1

    for entry in toc_entries:
        title       = entry["title"]
        ch_num      = entry["chapter_num"]
        title_lower = title.lower().strip()
        key         = title_lower[:60]  # first 60 chars — enough to be unique

        found_page = None
        for p in page_list:
            if p["page_num"] < search_from:
                continue
            # Skip TOC / prelim pages (the title appears in the TOC too)
            if _is_toc_or_prelim(p["text"]):
                continue
            if key in p["text"].lower():
                found_page = p["page_num"]
                break

        if found_page is None:
            log.debug(f"    Ch {ch_num} '{title}': title not found in body — skipping")
            continue

        resolved.append({"chapter_num": ch_num, "title": title, "pdf_start": found_page})
        search_from = found_page + 1  # next chapter must be on a later page

    return resolved


def _toc_is_degenerate(entries: list[dict]) -> bool:
    """Return True if TOC looks corrupt — all entries share the same start_page."""
    if not entries:
        return True
    pages = [e["start_page"] for e in entries]
    return len(set(pages)) == 1


# Body-text chapter heading for Assamese (OCR output): "অধ্যায় - N" or "অধ্যায়-N"
_AS_BODY_CHAPTER_RE = re.compile(
    r"অধ্যায়\s*[-–—]?\s*([০-৯\d]+)\s*\n([^\n]{5,150})",
    re.MULTILINE,
)


def _split_assamese_by_body(pages: list[dict], exercise_re) -> list[dict]:
    """
    Split Assamese PDF into chapters using body-text "অধ্যায় N" headings.
    Called when the TOC approach fails (garbled OCR chapter numbers in TOC).
    """
    sorted_pages = sorted(pages, key=lambda x: x["page_num"])
    full_text = "\n\n".join(_clean_page_text(p["text"]) for p in sorted_pages)

    matches = list(_AS_BODY_CHAPTER_RE.finditer(full_text))
    if not matches:
        log.warning("  Assamese: no অধ্যায় headings found in body text — treating as single chunk")
        return [{
            "chapter_num": 1,
            "title": "Full Book",
            "body_text": full_text[:12000],
            "exercises_text": "",
        }]

    log.info(f"    Assamese body-text split: {len(matches)} chapters found")
    chapters = []
    seen: set[int] = set()
    for idx, m in enumerate(matches):
        raw_num = m.group(1).translate(_AS_DIGIT_MAP)
        try:
            ch_num = int(raw_num)
        except ValueError:
            ch_num = idx + 1

        if ch_num in seen:
            continue  # duplicate heading (running header) — skip
        seen.add(ch_num)

        title = m.group(2).strip()
        start = m.start()
        end   = matches[idx + 1].start() if idx + 1 < len(matches) else len(full_text)
        chapter_text = full_text[start:end]

        body_text = chapter_text
        exercises_text = ""
        ex_m = exercise_re.search(chapter_text)
        if ex_m:
            body_text = chapter_text[:ex_m.start()]
            exercises_text = chapter_text[ex_m.start():]

        body_text = body_text[:12000].strip()
        exercises_text = exercises_text[:6000].strip()

        if len(body_text) < 150:
            continue

        chapters.append({
            "chapter_num":    ch_num,
            "title":          title[:200],
            "body_text":      body_text,
            "exercises_text": exercises_text,
        })

    return chapters


def _clean_page_text(text: str) -> str:
    """Remove common PDF artifacts: .indd markers, repeated digit borders, lone page numbers.
    Intentionally does NOT remove ALL-CAPS section headings — they are real chapter content.
    """
    # Remove InDesign source file artifacts
    text = re.sub(r"\.indd\s+\d+", "", text)
    # Remove decorative repeated-digit borders (e.g. 123456789012345678...)
    text = re.sub(r"(\d{5,}\n?){2,}", "", text)
    # Remove standalone lone page numbers (1-3 digit lines surrounded by blank lines or EOF)
    text = re.sub(r"(?:^|\n)\s*\d{1,3}\s*(?=\n|$)", "", text)
    # Remove known single-word book-title running headers (chemistry / biology / physics / maths)
    text = re.sub(
        r"^\s*(?:chemistry|biology|physics|mathematics|maths|CHEMISTRY|BIOLOGY|PHYSICS|MATHEMATICS)\s*$",
        "",
        text,
        flags=re.MULTILINE | re.IGNORECASE,
    )
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _roman_to_int(s: str) -> int:
    vals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    s = s.upper()
    result = 0
    for i, c in enumerate(s):
        if c not in vals:
            raise ValueError(f"Bad Roman numeral: {s!r}")
        if i + 1 < len(s) and vals[s[i + 1]] > vals[c]:
            result -= vals[c]
        else:
            result += vals[c]
    return result


# ── AI Content Generation ──────────────────────────────────────────────────────

_NOTES_SYSTEM_EN = """\
You are an expert AHSEC (Assam Higher Secondary Education Council) notes writer.
Write concise, well-structured study notes covering only the CORE CONCEPTS from
the chapter content provided. Format with Markdown:
- Use ## for each major topic heading (match the headings present in the source text).
- Under each heading write 3-6 tight bullet points or a short paragraph.
- Include important definitions, formulas, laws, and key facts.
- Avoid unnecessary padding; every sentence must carry information.
- Target 400-700 words total.
- Do NOT include exercises, questions, or answers in the notes.
"""

_NOTES_SYSTEM_AS = """\
তুমি এজন দক্ষ AHSEC (অসম উচ্চতৰ মাধ্যমিক শিক্ষা পৰিষদ) টোকা লেখক।
নিম্নলিখিত অধ্যায়ৰ বিষয়বস্তুৰ পৰা কেৱল মূল ধাৰণাসমূহ সামৰি সংক্ষিপ্ত, সুসংগঠিত অসমীয়া ভাষাত অধ্যয়ন টোকা লিখা।
বিন্যাস:
- প্ৰতিটো মূল বিষয়ৰ বাবে ## শিৰোনাম ব্যৱহাৰ কৰা।
- প্ৰতিটো শিৰোনামৰ তলত ৩-৬টা সংক্ষিপ্ত বিন্দু বা এটা চমু অনুচ্ছেদ লিখা।
- গুৰুত্বপূৰ্ণ সংজ্ঞা, সূত্ৰ, নিয়ম আৰু মূল তথ্য অন্তৰ্ভুক্ত কৰা।
- মুঠ ৪০০-৭০০ শব্দ লক্ষ্য কৰা।
- অনুশীলনী বা প্ৰশ্নোত্তৰ অন্তৰ্ভুক্ত নকৰিবা।
"""

_QA_SYSTEM_EN = """\
You are an AHSEC subject expert. Given the exercise section of a textbook chapter,
extract every question or problem and provide a complete solution.
- For numerical / calculation problems: show the step-by-step working and the final answer.
- For short-answer questions: write a concise but complete factual answer.
- For long-answer / descriptive questions: write a structured answer in 3-6 sentences.
Format strictly as a JSON array (no markdown fences, no extra keys):
[
  {"question": "<question text>", "answer": "<complete solution>"},
  ...
]
If the exercises section contains no answerable questions, return [].
"""

_QA_SYSTEM_AS = """\
তুমি এজন AHSEC বিষয় বিশেষজ্ঞ। তলত দিয়া অনুশীলনী প্ৰশ্নসমূহৰ সম্পূৰ্ণ সমাধান অসমীয়া ভাষাত লিখা।
বিন্যাস (JSON array হিচাপে):
[
  {"question": "<প্ৰশ্ন>", "answer": "<সম্পূৰ্ণ সমাধান>"},
  ...
]
উত্তৰ তথ্যভিত্তিক আৰু শিক্ষামূলক ৰাখা। প্ৰশ্ন নাথাকিলে [] ৰিটাৰ্ন কৰা।
"""


async def generate_notes(
    sarvam,
    body_text: str,
    chapter_title: str,
    subject_name: str,
    medium: str,
) -> str:
    """Call Sarvam to generate concise summary notes for a chapter.
    Retries up to 2 more times if the response is suspiciously short (< 300 chars).
    """
    is_as = medium == "as"
    system = _NOTES_SYSTEM_AS if is_as else _NOTES_SYSTEM_EN

    # Send first 10 000 chars; if that gives a short reply retry with first 5000
    # (sometimes shorter input prompts a fuller response from the model).
    for attempt, body_slice in enumerate([10000, 5000, 3000]):
        user_msg = (
            f"Subject: {subject_name}\nChapter: {chapter_title}\n\n"
            f"--- CHAPTER CONTENT ---\n{body_text[:body_slice]}"
        )
        result = await sarvam.generate(
            system_prompt=system,
            user_message=user_msg,
            is_assamese=is_as,
        )
        result = result.strip()
        if len(result) >= 300:
            return result
        if attempt < 2:
            log.warning(
                f"    Notes too short ({len(result)} chars), retrying with smaller slice…"
            )
        await asyncio.sleep(2.0)

    return result  # return whatever we got on the last attempt


async def generate_qa(
    sarvam,
    exercises_text: str,
    chapter_notes: str,
    chapter_title: str,
    subject_name: str,
    medium: str,
) -> list[dict]:
    """Call Sarvam to generate Q&A solutions for a chapter's exercises."""
    if not exercises_text or len(exercises_text) < 50:
        return []

    is_as = medium == "as"
    system = _QA_SYSTEM_AS if is_as else _QA_SYSTEM_EN
    user_msg = (
        f"Subject: {subject_name}\nChapter: {chapter_title}\n\n"
        f"--- CHAPTER NOTES (context) ---\n{chapter_notes[:3000]}\n\n"
        f"--- EXERCISES ---\n{exercises_text[:4000]}"
    )
    raw = await sarvam.generate(
        system_prompt=system,
        user_message=user_msg,
        is_assamese=is_as,
    )

    # Try to parse JSON from the response
    raw = raw.strip()
    # Strip markdown code fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE)
    raw = raw.strip()

    try:
        pairs = json.loads(raw)
        if isinstance(pairs, list):
            return [
                {"question": str(p.get("question", "")), "answer": str(p.get("answer", ""))}
                for p in pairs
                if p.get("question") and p.get("answer")
            ]
    except Exception:
        # Fall back: try to extract JSON array from anywhere in the response
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if m:
            try:
                pairs = json.loads(m.group(0))
                if isinstance(pairs, list):
                    return [
                        {"question": str(p.get("question", "")), "answer": str(p.get("answer", ""))}
                        for p in pairs if p.get("question")
                    ]
            except Exception:
                pass
    return []


# ── Notes → RAG Sections ───────────────────────────────────────────────────────

def notes_to_rag_sections(notes_md: str) -> list[dict]:
    """
    Split Markdown notes on ## headings → [{title, content}] for rag_sections.
    Strips Markdown symbols so chunks are clean plain text for the vector store.
    """
    lines = notes_md.split("\n")
    sections: list[dict] = []
    cur_title: Optional[str] = None
    cur_lines: list[str] = []

    def _flush():
        nonlocal cur_title, cur_lines
        if cur_title is not None:
            content = _strip_md("\n".join(cur_lines)).strip()
            if content and len(content) > 20:
                sections.append({"title": cur_title, "content": content})
        cur_title = None
        cur_lines = []

    for line in lines:
        m = re.match(r"^#{1,3}\s+(.+)", line)
        if m:
            _flush()
            cur_title = m.group(1).strip()
        else:
            if cur_title is not None:
                cur_lines.append(line)

    _flush()
    return sections


def _strip_md(text: str) -> str:
    """Remove common Markdown syntax, leaving clean plain text."""
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"_(.+?)_", r"\1", text)
    text = re.sub(r"`{1,3}([^`]*)`{1,3}", r"\1", text)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*\d+\.\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def qa_to_rag_sections(qa_pairs: list[dict]) -> list[dict]:
    """Convert [{question, answer}] to qa_rag_sections format."""
    return [
        {
            "section": "",
            "question": p["question"],
            "answer": p["answer"],
            "solution": "",
        }
        for p in qa_pairs
        if p.get("question") and p.get("answer")
    ]


# ── Topics extraction from notes ───────────────────────────────────────────────

def extract_topics_from_notes(notes_md: str) -> list[dict]:
    """Extract ## headings from notes as topic list for topic_embeddings."""
    import uuid as _uuid
    topics = []
    for m in re.finditer(r"^#{1,3}\s+(.+)", notes_md, re.MULTILINE):
        title = m.group(1).strip()
        if not title or len(title) < 3:
            continue
        slug = re.sub(r"[\s_-]+", "-", re.sub(r"[^\w\s-]", "", title.lower())).strip("-")
        topics.append({
            "id":    str(_uuid.uuid4()),
            "title": title,
            "topic_slug": slug,
            "definition_status": "pending",
        })
    return topics[:20]  # cap at 20 topics per chapter


# ── DB helpers ─────────────────────────────────────────────────────────────────

async def _find_or_create_stream(board_id, class_id, stream_name: str = "General"):
    """Find or create a Stream under a given Class."""
    from app.models.content import Stream
    from beanie import PydanticObjectId

    existing = await Stream.find_one({
        "class_id": class_id,
        "name": stream_name,
    })
    if existing:
        return existing

    stream = Stream(
        name=stream_name,
        class_id=class_id,
        status="active",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    await stream.insert()
    log.info(f"  Created stream '{stream_name}' under class {class_id}")
    return stream


async def upsert_subject(
    subject_name: str,
    subject_slug: str,
    class_level: str,
    board_slug: str = "ahsec",
) -> Optional[object]:
    """Find an existing subject by slug+class, or create a new one."""
    from app.models.content import Board, Class, Stream, Subject
    from beanie import PydanticObjectId

    board = await Board.find_one({"slug": board_slug})
    if not board:
        log.error(f"Board '{board_slug}' not found in DB — run setup first")
        return None

    # Find the class (Class 11 or Class 12)
    cls_name = f"Class {class_level}"
    cls = await Class.find_one({"board_id": board.id, "name": cls_name})
    if not cls:
        cls = await Class.find_one({"board_id": board.id, "name": {"$regex": class_level}})
    if not cls:
        log.warning(f"Class '{cls_name}' not found under board '{board_slug}' — creating")
        cls = Class(
            name=cls_name,
            board_id=board.id,
            status="active",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        await cls.insert()

    # Find/create a general stream
    stream = await _find_or_create_stream(board.id, cls.id)

    # Check if subject already exists by slug (case-insensitive)
    subj = await Subject.find_one({
        "slug": subject_slug,
        "stream_id": stream.id,
    })
    if subj:
        return subj

    # Also check by name similarity
    all_subjs = await Subject.find({"stream_id": stream.id}).to_list(length=500)
    for s in all_subjs:
        if s.slug == subject_slug or _slug(s.name) == subject_slug:
            return s

    # Create new subject
    subj = Subject(
        name=subject_name,
        slug=subject_slug,
        stream_id=stream.id,
        status="draft",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    await subj.insert()
    log.info(f"  Created subject '{subject_name}' ({subject_slug}) class={class_level}")
    return subj


async def upsert_chapter(
    subject_id,
    chapter_num: int,
    title: str,
    medium: str,
) -> object:
    """Find or create a chapter by subject_id + chapter_number."""
    from app.models.content import Chapter
    from beanie import PydanticObjectId

    # Try to find by chapter_number first
    chapter = await Chapter.find_one({
        "subject_id": subject_id,
        "chapter_number": chapter_num,
    })
    if chapter:
        return chapter, False  # (chapter, created)

    # Determine slug
    slug = re.sub(r"[\s_-]+", "-", re.sub(r"[^\w\s-]", "", title.lower())).strip("-")
    # Ensure slug uniqueness within subject
    existing_slugs = {
        ch.slug for ch in await Chapter.find({"subject_id": subject_id}).to_list(length=500)
    }
    base_slug = slug
    counter = 2
    while slug in existing_slugs:
        slug = f"{base_slug}-{counter}"
        counter += 1

    chapter = Chapter(
        title=title,
        subject_id=subject_id,
        slug=slug,
        chapter_number=chapter_num,
        content_type="notes",
        status="draft",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    await chapter.insert()
    log.info(f"    Created chapter '{title}' (#{chapter_num})")
    return chapter, True  # (chapter, created)


async def save_chapter_content(
    chapter,
    notes_text: str,
    rag_sections: list[dict],
    qa_sections: list[dict],
    topics: list[dict],
    medium: str,
    force: bool = False,
    dry_run: bool = False,
) -> bool:
    """
    Write notes, rag_sections, qa_rag_sections, and published_topics to a chapter.
    Returns True if content was written, False if skipped.
    """
    now = datetime.now(timezone.utc)

    if medium == "en":
        # Skip if already has English notes and not forcing
        if not force and chapter.notes_en and len(chapter.notes_en) > 100:
            log.info(f"    ↳ Skip (already has EN notes, use --force to overwrite)")
            return False
    else:
        if not force and chapter.notes_as and len(chapter.notes_as) > 100:
            log.info(f"    ↳ Skip (already has AS notes, use --force to overwrite)")
            return False

    if dry_run:
        log.info(f"    ↳ [dry-run] Would write {len(rag_sections)} RAG sections, "
                 f"{len(qa_sections)} QA sections, {len(topics)} topics")
        return True

    from app.models.content import Topic as _Topic
    import uuid as _uuid

    valid_topics = [
        _Topic(
            id=t.get("id") or str(_uuid.uuid4()),
            title=t["title"],
            topic_slug=t.get("topic_slug") or _slug(t["title"]),
            definition=t.get("definition"),
            definition_status="pending",
        )
        for t in topics
        if t.get("title")
    ]

    if medium == "en":
        chapter.notes_en = notes_text
        chapter.content_en = notes_text  # keep legacy field in sync
        chapter.rag_sections_en = rag_sections
        chapter.qa_rag_sections_en = qa_sections
    else:
        chapter.notes_as = notes_text
        chapter.content_as = notes_text
        chapter.rag_sections_as = rag_sections
        chapter.qa_rag_sections_as = qa_sections

    # Merge topics (don't overwrite topics from the other medium)
    existing_titles = {t.title for t in (chapter.published_topics or [])}
    for t in valid_topics:
        if t.title not in existing_titles:
            chapter.published_topics = list(chapter.published_topics or []) + [t]
            existing_titles.add(t.title)

    _wc_src = chapter.notes_en or chapter.content_en or ""
    chapter.word_count = len(_wc_src.split()) if _wc_src.strip() else 0
    chapter.notes_generated = True
    chapter.content_saved_at = now
    chapter.notes_rag_updated_at = now
    if qa_sections:
        chapter.qa_rag_updated_at = now
    chapter.updated_at = now

    await chapter.save()
    log.info(
        f"    ↳ Saved {medium.upper()} notes ({len(notes_text)} chars), "
        f"{len(rag_sections)} RAG sections, {len(qa_sections)} QA sections, "
        f"{len(valid_topics)} topics"
    )
    return True


async def reindex_chapter(chapter_id_str: str, scope: str = "notes") -> None:
    """Trigger a reindex for notes scope (pushes to Vectorize + refreshes topic embeddings)."""
    from app.services.rag.ingestion_v2 import ingest_chapter_v2
    from app.models.content import Chapter
    from beanie import PydanticObjectId

    try:
        chapter = await Chapter.get(PydanticObjectId(chapter_id_str))
        if not chapter:
            return
        now = datetime.now(timezone.utc)

        meta = {
            "subject_id": str(chapter.subject_id),
            "chapter_id": chapter_id_str,
            "chapter_slug": chapter.slug or "",
        }

        def _flatten(sections):
            parts = []
            for s in (sections or []):
                t = s.get("title", "").strip()
                c = s.get("content", "").strip()
                if t: parts.append(f"## {t}")
                if c: parts.append(c)
            return "\n\n".join(parts)

        if scope in ("notes", "all"):
            en_text = _flatten(chapter.rag_sections_en) or chapter.rag_text_en or None
            as_text = _flatten(chapter.rag_sections_as) or chapter.rag_text_as or None
            if en_text or as_text:
                await ingest_chapter_v2(
                    chapter_id=chapter_id_str,
                    content_en=en_text,
                    content_as=as_text,
                    metadata={**meta, "source_type": "notes"},
                    source_type="notes",
                )
                chapter = await Chapter.get(PydanticObjectId(chapter_id_str))
                if chapter:
                    chapter.notes_rag_indexed_at = now
                    chapter.rag_indexed_at = now
                    await chapter.save()

        if scope in ("qa", "all"):
            def _flatten_qa(sections):
                parts = []
                for s in (sections or []):
                    q = s.get("question", "").strip()
                    a = s.get("answer", "").strip()
                    if q: parts.append(f"Q: {q}")
                    if a: parts.append(f"A: {a}")
                    if q or a: parts.append("")
                return "\n".join(parts).strip()

            en_qa = _flatten_qa(chapter.qa_rag_sections_en) or None
            as_qa = _flatten_qa(chapter.qa_rag_sections_as) or None
            if en_qa or as_qa:
                await ingest_chapter_v2(
                    chapter_id=chapter_id_str,
                    content_en=en_qa,
                    content_as=as_qa,
                    metadata={**meta, "source_type": "important_questions"},
                    source_type="important_questions",
                )
                chapter = await Chapter.get(PydanticObjectId(chapter_id_str))
                if chapter:
                    chapter.qa_rag_indexed_at = now
                    await chapter.save()

        # Refresh topic embeddings
        if chapter and chapter.published_topics:
            try:
                from app.services.content_publisher import content_publisher as _cp
                hierarchy = await _cp._resolve_hierarchy(chapter)
                await _cp._generate_topic_embeddings(chapter, hierarchy)
            except Exception as e:
                log.warning(f"    Topic embedding refresh failed: {e}")

        log.info(f"    ↳ Reindexed chapter {chapter_id_str} (scope={scope})")
    except Exception as e:
        log.warning(f"    Reindex failed for {chapter_id_str}: {e}")


# ── Main pipeline ──────────────────────────────────────────────────────────────

async def process_pdf_entry(
    entry: dict,
    sarvam,
    *,
    force: bool,
    dry_run: bool,
    delay: float,
    done_keys: set[str],
) -> dict:
    """
    Process a single catalogue entry (one PDF book).
    Returns a summary dict with counts.
    """
    subject_name  = entry["subject_name"]
    subject_slug  = entry["subject_slug"]
    class_level   = entry["class_level"]
    medium        = entry["medium"]
    pdf_url       = entry["pdf_url"]
    part_num      = entry["part_num"]
    book_label    = entry["book_label"]

    log.info(f"\n{'='*60}")
    log.info(f"Subject: {subject_name} | Class {class_level} | {medium.upper()} | Part {part_num}")
    log.info(f"PDF: {pdf_url}")

    # ── Step 1: Upsert subject ────────────────────────────────────────────────
    subj = await upsert_subject(subject_name, subject_slug, class_level)
    if not subj:
        log.error(f"  Could not find/create subject '{subject_name}' — skipping")
        return {"skipped": 1}

    # ── Step 2: Download + extract PDF text ───────────────────────────────────
    log.info(f"  Downloading PDF…")
    try:
        pages = extract_pdf_text(pdf_url, medium=medium)
        log.info(f"  Extracted {len(pages)} pages")
    except Exception as e:
        log.error(f"  PDF extraction failed: {e}")
        _log_progress(f"{pdf_url}|ALL", "error", str(e))
        return {"errors": 1}

    if not pages:
        log.warning("  No pages extracted — skipping")
        return {"skipped": 1}

    # ── Step 3: Split into chapters ───────────────────────────────────────────
    chapters = split_into_chapters(pages, medium)
    log.info(f"  Detected {len(chapters)} chapters")

    if not chapters:
        log.warning("  No chapters detected — skipping PDF")
        return {"skipped": 1}

    # For multi-part books: offset chapter numbers so Part II picks up after Part I
    ch_offset = 0
    if part_num > 1:
        # Count chapters already existing for this subject
        from app.models.content import Chapter as _Ch
        existing_count = len(await _Ch.find({"subject_id": subj.id}).to_list(length=500))
        ch_offset = existing_count

    stats = {"done": 0, "skipped": 0, "errors": 0}

    for ch_info in chapters:
        raw_num    = ch_info["chapter_num"]
        ch_num     = raw_num + ch_offset
        ch_title   = ch_info["title"]
        body_text  = ch_info["body_text"]
        ex_text    = ch_info["exercises_text"]
        progress_key = f"{pdf_url}|ch{raw_num}"

        if not force and progress_key in done_keys:
            log.info(f"  Ch {ch_num}: '{ch_title}' — already done, skipping")
            stats["skipped"] += 1
            continue

        log.info(f"  Ch {ch_num}: '{ch_title}' ({len(body_text)} body chars, {len(ex_text)} ex chars)")

        # ── Upsert chapter ────────────────────────────────────────────────────
        chapter, created = await upsert_chapter(subj.id, ch_num, ch_title, medium)

        # ── Generate notes ─────────────────────────────────────────────────────
        notes_text = ""
        if not dry_run:
            try:
                notes_text = await generate_notes(sarvam, body_text, ch_title, subject_name, medium)
                await asyncio.sleep(delay)
            except Exception as e:
                log.error(f"    Notes generation failed: {e}")
                _log_progress(progress_key, "error", str(e))
                stats["errors"] += 1
                continue
        else:
            notes_text = f"## {ch_title}\n\n[dry-run placeholder notes for {subject_name}]\n"

        if not notes_text or len(notes_text) < 50:
            log.warning(f"    Empty notes returned — skipping chapter")
            stats["skipped"] += 1
            continue

        # ── Generate Q&A ──────────────────────────────────────────────────────
        qa_pairs: list[dict] = []
        if not dry_run and ex_text:
            try:
                qa_pairs = await generate_qa(sarvam, ex_text, notes_text, ch_title, subject_name, medium)
                await asyncio.sleep(delay)
                log.info(f"    Generated {len(qa_pairs)} Q&A pairs")
            except Exception as e:
                log.warning(f"    QA generation failed (non-fatal): {e}")

        # ── Build sections ────────────────────────────────────────────────────
        rag_sections = notes_to_rag_sections(notes_text)
        qa_sections  = qa_to_rag_sections(qa_pairs)
        topics       = extract_topics_from_notes(notes_text)

        # ── Save to DB ────────────────────────────────────────────────────────
        written = await save_chapter_content(
            chapter, notes_text, rag_sections, qa_sections, topics,
            medium, force=force, dry_run=dry_run,
        )

        # ── Reindex (vectorize + topic embeddings) ────────────────────────────
        if written and not dry_run:
            scope = "all" if qa_sections else "notes"
            await reindex_chapter(str(chapter.id), scope=scope)

        if written:
            _log_progress(progress_key, "done")
            done_keys.add(progress_key)
            stats["done"] += 1
        else:
            stats["skipped"] += 1

        # Small pause between chapters to avoid rate limits
        await asyncio.sleep(0.5)

    return stats


async def main() -> None:
    args = _parse_args()

    # ── Bootstrap ─────────────────────────────────────────────────────────────
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

    # ── Load Sarvam key ───────────────────────────────────────────────────────
    from app.services.ai.sarvam_client import sarvam_client
    if not settings.SARVAM_API_KEY:
        try:
            from app.core.secret_manager import load_secrets_into_settings
            await load_secrets_into_settings()
        except Exception as e:
            log.warning(f"Secret Manager fetch failed: {e}")

    if settings.SARVAM_API_KEY:
        log.info(f"Sarvam key loaded (prefix={settings.SARVAM_API_KEY[:8]}…)")
    else:
        if not args.dry_run:
            log.error("SARVAM_API_KEY not available — run with --dry-run or set key")
            sys.exit(1)
        log.warning("SARVAM_API_KEY missing — proceeding in dry-run mode only")
        args.dry_run = True

    # ── Build catalogue ───────────────────────────────────────────────────────
    want_c11 = args.class11 or (not args.class11 and not args.class12)
    want_c12 = args.class12 or (not args.class11 and not args.class12)

    if args.pilot:
        # Hard-coded pilot entries for testing the pipeline
        catalogue = [
            {
                "subject_name": "Chemistry",
                "subject_slug": "chemistry",
                "class_level": "11",
                "medium": "en",
                "pdf_url": "https://ahsec.assam.gov.in/wp-content/uploads/2025/06/Chemistry-Part-I-Freebook.pdf",
                "part_num": 1,
                "book_label": "Chemistry Part I (E)",
            },
            {
                "subject_name": "Biology",
                "subject_slug": "biology",
                "class_level": "11",
                "medium": "as",
                "pdf_url": "https://ahsec.assam.gov.in/wp-content/uploads/2025/06/BIOLOGY_1ST-YR_2023.pdf",
                "part_num": 1,
                "book_label": "BIOLOGY (A)",
            },
        ]
        log.info(f"Pilot mode: {len(catalogue)} entries")
    else:
        catalogue = build_catalogue(class11=want_c11, class12=want_c12)

    # Apply filters
    if args.medium:
        catalogue = [e for e in catalogue if e["medium"] == args.medium]
    if args.subject:
        catalogue = [e for e in catalogue if e["subject_slug"] == args.subject or args.subject in e["subject_slug"]]

    log.info(f"Processing {len(catalogue)} entries "
             f"({'dry-run' if args.dry_run else 'live'}, delay={args.delay}s)")

    done_keys = _load_done_keys()
    log.info(f"Resuming — {len(done_keys)} chapters already done")

    # ── Process each PDF ──────────────────────────────────────────────────────
    total = {"done": 0, "skipped": 0, "errors": 0}
    chapters_processed = 0

    for entry in catalogue:
        result = await process_pdf_entry(
            entry,
            sarvam_client,
            force=args.force,
            dry_run=args.dry_run,
            delay=args.delay,
            done_keys=done_keys,
        )
        for k in total:
            total[k] += result.get(k, 0)
        chapters_processed += result.get("done", 0) + result.get("skipped", 0)

        if args.limit and chapters_processed >= args.limit:
            log.info(f"Reached --limit {args.limit} — stopping")
            break

    log.info(f"\n{'='*60}")
    log.info(f"Pipeline complete: {total['done']} written, {total['skipped']} skipped, {total['errors']} errors")
    log.info(f"Progress log: {PROGRESS_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
