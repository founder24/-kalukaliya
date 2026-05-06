"""probe_assamese_ssr_coverage.py — Task #465 acceptance probe.

Fetches a sample of Assamese-mode SSR URLs and asserts that the visible
article body contains < ``MAX_LATIN_RATIO`` Latin letters. Run this
after a backfill pass to confirm the Assamese variant is actually
showing translated content rather than the silent English fallback.

Sample URLs are auto-discovered from Mongo (one ``seo_pages`` row per
distinct ``page_type``, plus a chapter, subject, and PYQ page). The
probe issues a real HTTP request against ``BASE_URL`` (defaults to the
production origin), waits for the SSR HTML, strips tags, and compares
the Bengali- vs Latin-letter ratio over the article body.

Exit codes
----------
    0 — every probed URL met the threshold
    2 — at least one URL failed (printed in the JSON report)
    1 — fatal error (Mongo / network)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from typing import Any

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("probe_assamese_ssr_coverage")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_BASE_URL = (
    os.environ.get("ASSAMESE_PROBE_BASE_URL")
    or os.environ.get("BASE_URL")
    or "https://syrabit.ai"
).rstrip("/")

# Acceptance threshold from the task plan: "< 5% Latin characters in the
# article body".
MAX_LATIN_RATIO = float(os.environ.get("ASSAMESE_PROBE_MAX_LATIN_RATIO", "0.05"))

# Pull article-body content out of the SSR HTML. We look for the most
# common containers used by `seo_engine.py` (article + main + body
# fallback) and fall back to the full page body if none match.
_BODY_RE = re.compile(
    r"<article[^>]*>(.*?)</article>|<main[^>]*>(.*?)</main>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_html(html: str) -> str:
    m = _BODY_RE.search(html)
    if m:
        body = next((g for g in m.groups() if g), "")
    else:
        body = html
    text = _TAG_RE.sub(" ", body)
    return _WS_RE.sub(" ", text).strip()


def _latin_ratio(text: str) -> float:
    bengali = 0
    latin = 0
    for ch in text:
        if "\u0980" <= ch <= "\u09FF":
            bengali += 1
        elif ("a" <= ch.lower() <= "z"):
            latin += 1
    total = bengali + latin
    if total == 0:
        return 0.0
    return latin / total


async def _get_db():
    mongo_url = (os.environ.get("MONGO_URL") or "").strip().strip('"').strip("'")
    if not mongo_url:
        raise RuntimeError("MONGO_URL env var is required")
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=8000)
    await client.admin.command("ping")
    db_name = (mongo_url.rstrip("/").split("/")[-1].split("?")[0]) or "syrabit"
    return client[db_name]


async def _sample_urls(db: Any, *, per_kind: int = 1) -> list[dict]:
    """Pick a representative set of Assamese-mode SSR URLs to probe."""
    samples: list[dict] = []

    # SEO pages: one per distinct page_type so we exercise notes, MCQ,
    # PYQ, summary, and similar templates.
    try:
        pipeline = [
            {"$match": {"status": "published", "topic_slug": {"$exists": True, "$ne": ""}}},
            {"$group": {"_id": "$page_type", "doc": {"$first": "$$ROOT"}}},
            {"$limit": 10},
        ]
        async for doc in db.seo_pages.aggregate(pipeline):
            page = doc.get("doc") or {}
            board = page.get("board_slug") or ""
            cls = page.get("class_slug") or ""
            subj = page.get("subject_slug") or ""
            topic = page.get("topic_slug") or ""
            ptype = page.get("page_type") or ""
            if not (board and cls and subj and topic and ptype):
                continue
            samples.append({
                "kind": f"seo_pages:{ptype}",
                "url":  f"/as/{board}/{cls}/{subj}/{topic}/{ptype}",
            })
    except Exception as exc:
        logger.warning("seo_pages sampling failed: %s", exc)

    # Chapters (one).
    try:
        async for ch in db.chapters.find(
            {"slug": {"$exists": True, "$ne": ""}},
            {"_id": 0, "slug": 1, "subject_id": 1},
        ).limit(per_kind):
            subj = await db.subjects.find_one(
                {"id": ch.get("subject_id", "")},
                {"_id": 0, "slug": 1, "board_slug": 1, "class_slug": 1},
            ) or {}
            board = subj.get("board_slug") or ""
            cls = subj.get("class_slug") or ""
            ssub = subj.get("slug") or ""
            chs = ch.get("slug") or ""
            if board and cls and ssub and chs:
                samples.append({
                    "kind": "chapters",
                    "url":  f"/as/{board}/{cls}/{ssub}/{chs}",
                })
    except Exception as exc:
        logger.warning("chapters sampling failed: %s", exc)

    # PYQ HTML pages (one).
    try:
        async for pq in db.pyq_html_pages.find(
            {"slug": {"$exists": True, "$ne": ""}},
            {"_id": 0, "slug": 1, "board_slug": 1, "class_slug": 1, "subject_slug": 1},
        ).limit(per_kind):
            board = pq.get("board_slug") or ""
            cls = pq.get("class_slug") or ""
            subj = pq.get("subject_slug") or ""
            slug = pq.get("slug") or ""
            if board and cls and subj and slug:
                samples.append({
                    "kind": "pyq_html_pages",
                    "url":  f"/as/{board}/{cls}/{subj}/pyq/{slug}",
                })
    except Exception as exc:
        logger.warning("pyq_html_pages sampling failed: %s", exc)

    return samples


async def _probe_one(client: httpx.AsyncClient, base: str, sample: dict) -> dict:
    url = base + sample["url"]
    try:
        resp = await client.get(url, timeout=20.0, follow_redirects=True)
        resp.raise_for_status()
    except Exception as exc:
        return {**sample, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
    text = _strip_html(resp.text)
    sample_len = len(text)
    ratio = _latin_ratio(text)
    ok = sample_len >= 200 and ratio <= MAX_LATIN_RATIO
    return {
        **sample,
        "ok":           ok,
        "latin_ratio":  round(ratio, 4),
        "body_chars":   sample_len,
        "threshold":    MAX_LATIN_RATIO,
        "status_code":  resp.status_code,
    }


async def _amain(args) -> int:
    try:
        db = await _get_db()
    except Exception as exc:
        logger.error("MongoDB connection failed: %s", exc)
        return 1

    base = (args.base_url or DEFAULT_BASE_URL).rstrip("/")
    samples = await _sample_urls(db, per_kind=args.per_kind)
    if not samples:
        logger.error("No probeable URLs discovered — is the corpus empty?")
        return 1

    results: list[dict] = []
    async with httpx.AsyncClient(headers={"User-Agent": "syrabit-as-coverage-probe/1.0"}) as client:
        for sample in samples:
            results.append(await _probe_one(client, base, sample))

    failed = [r for r in results if not r.get("ok")]
    report = {
        "base_url":     base,
        "threshold":    MAX_LATIN_RATIO,
        "probed":       len(results),
        "failed_count": len(failed),
        "results":      results,
    }
    print(json.dumps(report, indent=2))
    return 0 if not failed else 2


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("--base-url", default=None,
                   help=f"Origin to probe (default: {DEFAULT_BASE_URL}).")
    p.add_argument("--per-kind", type=int, default=1,
                   help="Sample size per non-seo_pages collection (default 1).")
    args = p.parse_args()
    sys.exit(asyncio.run(_amain(args)))


if __name__ == "__main__":
    main()
