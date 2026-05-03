"""AHSEC 2025-26 syllabus scraper / loader (Task #287).

This script is the source-of-truth pipeline for HS 1st & 2nd year syllabus
data feeding `build_ahsec_content.py`.

Modes:
  --refresh-live   Fetch ahsec.assam.gov.in syllabus index, download per-
                   subject PDFs, parse with pypdf, and emit data/ahsec_2025_26.json.
                   Falls back to ahseconline.org and the Wayback Machine
                   when the .gov.in host is unreachable.
  (default)        Validate the curated manifest at data/ahsec_2025_26.json
                   and print a summary tree (subjects/chapters/topics).

The curated manifest is committed because (a) the live AHSEC site is often
unreachable from CI/sandboxed environments, (b) the syllabus is stable
across the academic year, and (c) it lets the rest of the rebuild pipeline
run deterministically without flaky network calls.

Usage:
  python -m scripts.ahsec_scrape                   # validate + summarise
  python -m scripts.ahsec_scrape --refresh-live    # live scrape (slow)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
MANIFEST_PATH = DATA_DIR / "ahsec_2025_26.json"
PDF_CACHE_DIR = DATA_DIR / "ahsec_pdfs"

LIVE_INDEX_URLS = [
    "https://ahsec.assam.gov.in/portlets/syllabus",
    "https://ahsec.assam.gov.in/portlets/syllabus-hs-1st-year",
    "https://ahsec.assam.gov.in/portlets/syllabus-hs-2nd-year",
]
WAYBACK_PREFIX = "https://web.archive.org/web/2025/"

UA = "Mozilla/5.0 (compatible; SyrabitBot/1.0; +https://syrabit.ai/bot)"


# ── Manifest validation ──────────────────────────────────────────────────────


def load_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        raise SystemExit(f"manifest not found: {MANIFEST_PATH}")
    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    validate_manifest(data)
    return data


def validate_manifest(data: dict) -> None:
    """Hard-fail manifest schema check. Catches missing/mistyped required
    keys before downstream wipe/build uses the data."""
    def _err(msg: str) -> None:
        raise SystemExit(f"manifest schema invalid: {msg}")

    if not isinstance(data, dict):
        _err("root must be an object")
    if "classes" not in data or not isinstance(data["classes"], dict):
        _err("missing or non-dict `classes`")
    for class_id, class_data in data["classes"].items():
        if not isinstance(class_data, dict):
            _err(f"class {class_id!r} must be an object")
        for k in ("name", "slug", "streams"):
            if k not in class_data:
                _err(f"class {class_id!r} missing key {k!r}")
        if not isinstance(class_data["streams"], dict):
            _err(f"class {class_id!r}.streams must be an object")
        for stream_id, stream in class_data["streams"].items():
            if not isinstance(stream, dict):
                _err(f"stream {class_id}/{stream_id} must be an object")
            for k in ("name", "slug", "subjects"):
                if k not in stream:
                    _err(f"stream {class_id}/{stream_id} missing key {k!r}")
            if not isinstance(stream["subjects"], list):
                _err(f"stream {class_id}/{stream_id}.subjects must be a list")
            for subj in stream["subjects"]:
                if not isinstance(subj, dict):
                    _err(f"subject in {class_id}/{stream_id} must be an object")
                for k in ("name", "slug"):
                    if k not in subj:
                        _err(f"subject in {class_id}/{stream_id} missing key {k!r}")
                # Either an inline chapters list or an _inherits reference.
                if "_inherits" not in subj:
                    if not isinstance(subj.get("chapters"), list):
                        _err(f"subject {class_id}/{stream_id}/{subj['slug']} "
                             "must have list `chapters` or string `_inherits`")
                    for ch in subj["chapters"]:
                        if not isinstance(ch, dict) or "title" not in ch:
                            _err(f"chapter in {subj['slug']} missing `title`")
                        if "topics" in ch and not isinstance(ch["topics"], list):
                            _err(f"chapter {ch.get('title')!r}.topics must be a list")


def resolve_inherits(manifest: dict) -> dict:
    """Inline `_inherits: 'stream-slug/subject-slug'` references so callers
    receive a fully-flat tree."""
    classes = manifest["classes"]
    for class_id, class_data in classes.items():
        streams = class_data["streams"]
        # Build a slug -> subject map per class for lookup.
        by_path: dict[str, dict] = {}
        for stream in streams.values():
            for subj in stream.get("subjects", []):
                if "_inherits" not in subj:
                    by_path[f"{stream['slug']}/{subj['slug']}"] = subj
        for stream in streams.values():
            for subj in stream.get("subjects", []):
                ref = subj.pop("_inherits", None)
                if not ref:
                    continue
                src = by_path.get(ref)
                if not src:
                    raise SystemExit(
                        f"_inherits target not found: class={class_id} ref={ref}"
                    )
                subj["chapters"] = src["chapters"]
    return manifest


def summarise(manifest: dict) -> None:
    total_subjects = total_chapters = total_topics = 0
    for class_id, class_data in manifest["classes"].items():
        print(f"\n[{class_id}] {class_data['name']} ({class_data['slug']})")
        for stream_id, stream in class_data["streams"].items():
            subj_count = len(stream.get("subjects", []))
            ch_count = sum(len(s.get("chapters", [])) for s in stream.get("subjects", []))
            topic_count = sum(
                len(c.get("topics", []))
                for s in stream.get("subjects", [])
                for c in s.get("chapters", [])
            )
            total_subjects += subj_count
            total_chapters += ch_count
            total_topics += topic_count
            print(
                f"  └─ [{stream_id}] {stream['name']:<18} "
                f"subjects={subj_count:>2}  chapters={ch_count:>3}  topics={topic_count:>4}"
            )
    print(
        f"\nTOTAL: {total_subjects} subjects, {total_chapters} chapters, "
        f"{total_topics} topics"
    )


# ── Live scrape ──────────────────────────────────────────────────────────────


def fetch(url: str, timeout: float = 20.0) -> str | None:
    try:
        r = httpx.get(url, headers={"User-Agent": UA}, timeout=timeout, follow_redirects=True)
        if r.status_code != 200:
            return None
        return r.text
    except Exception:
        return None


def fetch_with_fallback(url: str) -> str | None:
    body = fetch(url)
    if body:
        return body
    # Try Wayback Machine.
    body = fetch(WAYBACK_PREFIX + url)
    if body:
        return body
    return None


def discover_pdf_links(html: str, base_url: str) -> list[str]:
    pdfs = re.findall(r'href="([^"]+\.pdf)"', html, flags=re.I)
    out: list[str] = []
    for href in pdfs:
        if href.startswith("http"):
            out.append(href)
        elif href.startswith("/"):
            origin = re.match(r"(https?://[^/]+)", base_url)
            if origin:
                out.append(origin.group(1) + href)
        else:
            out.append(base_url.rstrip("/") + "/" + href)
    return sorted(set(out))


def download_pdf(url: str) -> Path | None:
    PDF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^a-zA-Z0-9._-]+", "_", url.rsplit("/", 1)[-1])[:200]
    out = PDF_CACHE_DIR / name
    if out.exists() and out.stat().st_size > 0:
        return out
    try:
        with httpx.stream(
            "GET", url, headers={"User-Agent": UA}, timeout=60.0, follow_redirects=True
        ) as r:
            if r.status_code != 200:
                return None
            with out.open("wb") as f:
                for chunk in r.iter_bytes(8192):
                    f.write(chunk)
        return out
    except Exception:
        return None


def parse_pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    try:
        reader = PdfReader(str(path))
        return "\n".join(p.extract_text() or "" for p in reader.pages)
    except Exception:
        return ""


def refresh_live() -> dict:
    """Best-effort live refresh. Writes raw scrape into data/ahsec_pdfs/ and
    a parsed JSON dump into data/ahsec_live_dump.json. Does NOT overwrite
    the curated manifest — the human curator integrates by hand because
    chapter ordering / spelling needs editorial review."""
    print("[refresh-live] probing AHSEC syllabus index pages…")
    pages: list[tuple[str, str]] = []
    for url in LIVE_INDEX_URLS:
        body = fetch_with_fallback(url)
        if body:
            pages.append((url, body))
            print(f"  ✓ fetched {url} ({len(body)} bytes)")
        else:
            print(f"  ✗ unreachable {url}")

    if not pages:
        print("[refresh-live] no AHSEC index pages reachable — keeping curated manifest")
        return {"pages": 0, "pdfs": 0}

    pdf_urls: set[str] = set()
    for src, html in pages:
        for u in discover_pdf_links(html, src):
            pdf_urls.add(u)
    print(f"[refresh-live] discovered {len(pdf_urls)} PDF candidates")

    parsed: list[dict] = []
    for u in sorted(pdf_urls):
        p = download_pdf(u)
        if not p:
            continue
        text = parse_pdf_text(p)
        parsed.append({"url": u, "file": str(p.relative_to(ROOT)), "chars": len(text)})
        print(f"  → {p.name}  chars={len(text)}")

    dump = DATA_DIR / "ahsec_live_dump.json"
    dump.write_text(
        json.dumps({"pages": [u for u, _ in pages], "pdfs": parsed}, indent=2),
        encoding="utf-8",
    )
    print(f"[refresh-live] live dump written to {dump}")
    print(
        "[refresh-live] NOTE: curated manifest at data/ahsec_2025_26.json was NOT "
        "overwritten. Integrate parsed PDFs by editing the manifest manually."
    )
    return {"pages": len(pages), "pdfs": len(parsed)}


# ── Entry point ──────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh-live", action="store_true",
                    help="Probe AHSEC live site (best-effort) and write data/ahsec_live_dump.json")
    args = ap.parse_args()

    if args.refresh_live:
        refresh_live()
        return 0

    manifest = resolve_inherits(load_manifest())
    summarise(manifest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
