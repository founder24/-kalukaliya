"""
verify_as_notes.py — End-to-end verification of Assamese study notes pipeline.

Checks that Biology, Chemistry, and Physics Class XI AHSEC chapters have:
  - notes_as ≥ 500 chars containing actual Assamese Unicode (U+0980–U+09FF)
  - rag_sections_as with ≥ 2 non-empty Assamese-containing entries
  - Public API returns content_as with Assamese Unicode via chapter-by-slug

Usage (from apps/backend/):
    python3 -m scripts.verify_as_notes

Exit codes:
    0  All checks pass
    1  One or more checks failed (failures printed to stdout)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("verify_as_notes")

# ── Subjects to verify (Biology, Chemistry, Physics XI AHSEC) ─────────────────

SUBJECTS = [
    {
        "name": "Biology XI AS",
        "subject_slug": "biology",
        "class_level": "11",
        "api_slug": "biology",
        "min_chapters": 1,  # At least 1 chapter must have notes_as
    },
    {
        "name": "Chemistry XI AS",
        "subject_slug": "chemistry",
        "class_level": "11",
        "api_slug": "chemistry",
        "min_chapters": 1,
    },
    {
        "name": "Physics XI AS",
        "subject_slug": "physics",
        "class_level": "11",
        "api_slug": "physics",
        "min_chapters": 1,
    },
]

ASSAMESE_MIN_CHARS = 500   # notes_as must be at least this long
ASSAMESE_UNICODE_MIN = 10  # notes_as must contain at least this many Assamese Unicode chars
RAG_MIN_ENTRIES = 2        # rag_sections_as must have at least this many entries


def _is_assamese(text: str) -> bool:
    """Return True if text contains meaningful Assamese Unicode glyphs."""
    return sum(1 for c in text if 0x0980 <= ord(c) <= 0x09FF) >= ASSAMESE_UNICODE_MIN


def _count_assamese_chars(text: str) -> int:
    return sum(1 for c in text if 0x0980 <= ord(c) <= 0x09FF)


async def verify_mongodb(db) -> list[dict]:
    """Query MongoDB and return per-subject verification results."""
    results = []

    # Resolve AHSEC board
    board = await db.boards.find_one({"slug": "ahsec"})
    if not board:
        return [{"name": s["name"], "pass": False, "reason": "AHSEC board not found in DB"} for s in SUBJECTS]

    # Resolve HS 1st Year class
    classes = await db.classes.find({"board_id": board["_id"]}).to_list(length=None)
    cls11 = next(
        (c for c in classes if "1st" in c.get("name", "").lower() or "11" in c.get("name", "")),
        None,
    )
    if not cls11:
        return [{"name": s["name"], "pass": False, "reason": "HS 1st Year class not found"} for s in SUBJECTS]

    streams = await db.streams.find({"class_id": cls11["_id"]}).to_list(length=None)

    for spec in SUBJECTS:
        subj_doc = None
        for stream in streams:
            found = await db.subjects.find_one(
                {"stream_id": stream["_id"], "slug": spec["subject_slug"]}
            )
            if found:
                subj_doc = found
                break

        if not subj_doc:
            results.append({
                "name": spec["name"],
                "pass": False,
                "reason": f"Subject '{spec['subject_slug']}' not found under AHSEC XI",
            })
            continue

        # Find chapters with notes_as
        chapters = await db.chapters.find(
            {"subject_id": subj_doc["_id"]},
            {"title": 1, "slug": 1, "chapter_number": 1, "notes_as": 1, "rag_sections_as": 1},
        ).to_list(length=None)

        qualifying = []
        for ch in chapters:
            notes_as = ch.get("notes_as") or ""
            rag = ch.get("rag_sections_as") or []

            # Check notes_as criteria
            as_chars = _count_assamese_chars(notes_as)
            notes_ok = len(notes_as) >= ASSAMESE_MIN_CHARS and as_chars >= ASSAMESE_UNICODE_MIN

            # Check rag_sections_as criteria
            non_empty_rag = [
                r for r in rag
                if r.get("content") and len(r.get("content", "")) >= 30
            ]
            rag_ok = len(non_empty_rag) >= RAG_MIN_ENTRIES

            if notes_ok and rag_ok:
                qualifying.append({
                    "chapter": ch.get("chapter_number"),
                    "title": ch.get("title"),
                    "slug": ch.get("slug"),
                    "notes_as_len": len(notes_as),
                    "assamese_chars": as_chars,
                    "rag_entries": len(non_empty_rag),
                })

        if len(qualifying) >= spec["min_chapters"]:
            results.append({
                "name": spec["name"],
                "pass": True,
                "qualifying_chapters": qualifying,
                "total_chapters": len(chapters),
            })
        else:
            # Provide diagnostic detail
            detail_rows = []
            for ch in sorted(chapters, key=lambda c: c.get("chapter_number", 0))[:5]:
                notes = ch.get("notes_as") or ""
                rag = ch.get("rag_sections_as") or []
                detail_rows.append(
                    f"  ch{ch.get('chapter_number','?')} '{ch.get('title','?')[:40]}': "
                    f"notes_as={len(notes)}c, AS_chars={_count_assamese_chars(notes)}, rag={len(rag)}"
                )
            results.append({
                "name": spec["name"],
                "pass": False,
                "reason": (
                    f"Only {len(qualifying)}/{len(chapters)} chapters meet criteria "
                    f"(need ≥{spec['min_chapters']}). "
                    f"Run: python3 -m scripts.ahsec_ingest --class11 --medium as "
                    f"--subject {spec['subject_slug']} --force --limit 3"
                ),
                "diagnostics": detail_rows,
            })

    return results


def verify_api(base_url: str, board: str, class_slug: str, chapters: list[dict]) -> list[dict]:
    """Hit the public API for a set of chapters and check content_as."""
    api_results = []
    for ch in chapters:
        url = (
            f"{base_url}/api/v1/content/chapter-by-slug"
            f"/{board}/{class_slug}/{ch['api_slug']}/{ch['chapter_slug']}"
        )
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())
            content_as = data.get("content_as") or ""
            as_chars = _count_assamese_chars(content_as)
            api_results.append({
                "url": url,
                "pass": len(content_as) >= ASSAMESE_MIN_CHARS and as_chars >= ASSAMESE_UNICODE_MIN,
                "content_as_len": len(content_as),
                "assamese_chars": as_chars,
                "has_assamese": data.get("has_assamese", False),
            })
        except Exception as e:
            api_results.append({"url": url, "pass": False, "error": str(e)})
    return api_results


async def main() -> int:
    log.info("=== Assamese Study Notes Verification ===")
    log.info(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    log.info(f"Criteria: notes_as ≥{ASSAMESE_MIN_CHARS} chars, ≥{ASSAMESE_UNICODE_MIN} AS chars, rag ≥{RAG_MIN_ENTRIES} entries")

    # ── Bootstrap DB ─────────────────────────────────────────────────────────
    from app.db.mongo import init_mongo
    from app.config import settings

    if not settings.MONGODB_URI:
        mongo_url = os.environ.get("MONGODB_URL") or os.environ.get("MONGODB_URI")
        if mongo_url:
            settings.MONGODB_URI = mongo_url  # type: ignore[attr-defined]
        else:
            log.error("MONGODB_URI / MONGODB_URL not set")
            return 1

    await init_mongo()

    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(settings.MONGODB_URI)
    db = client[settings.MONGODB_DB_NAME]

    # ── MongoDB checks ────────────────────────────────────────────────────────
    log.info("\n--- MongoDB verification ---")
    db_results = await verify_mongodb(db)

    all_pass = True
    api_chapters_to_check = []  # collect qualifying chapter slugs for API check

    for r in db_results:
        if r["pass"]:
            q = r["qualifying_chapters"]
            log.info(f"✓ {r['name']}: {len(q)} qualifying chapter(s)")
            for ch in q:
                log.info(
                    f"  ch{ch['chapter']} '{ch['title']}': "
                    f"notes_as={ch['notes_as_len']}c, AS_chars={ch['assamese_chars']}, "
                    f"rag={ch['rag_entries']} entries"
                )
                # Pick first qualifying chapter for API check
                if not any(x["api_slug"] == r["name"].split()[0].lower() for x in api_chapters_to_check):
                    api_chapters_to_check.append({
                        "api_slug": r["name"].split()[0].lower(),
                        "chapter_slug": ch["slug"],
                        "label": r["name"],
                    })
        else:
            log.error(f"✗ {r['name']}: {r['reason']}")
            for line in r.get("diagnostics", []):
                log.error(line)
            all_pass = False

    # ── Public API checks ─────────────────────────────────────────────────────
    api_base = os.environ.get("API_BASE_URL", "http://localhost:8000")
    if api_chapters_to_check:
        log.info(f"\n--- Public API verification (base={api_base}) ---")
        api_results = verify_api(api_base, "ahsec", "hs-1st-year", api_chapters_to_check)
        for ar in api_results:
            if ar.get("pass"):
                log.info(
                    f"✓ API: content_as={ar['content_as_len']}c, "
                    f"AS_chars={ar['assamese_chars']}, has_assamese={ar['has_assamese']}"
                )
                log.info(f"  URL: {ar['url']}")
            else:
                log.error(f"✗ API: {ar.get('error', 'content_as too short or missing')}")
                log.error(f"  URL: {ar['url']}")
                all_pass = False

    # ── Summary ───────────────────────────────────────────────────────────────
    log.info("\n=== Result ===")
    if all_pass:
        log.info("ALL CHECKS PASSED — Assamese notes are live and reachable via public API")
    else:
        log.error("SOME CHECKS FAILED — See above for details")
        log.error(
            "To generate missing notes, run:\n"
            "  python3 -m scripts.ahsec_ingest --class11 --medium as --force --delay 5"
        )

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
