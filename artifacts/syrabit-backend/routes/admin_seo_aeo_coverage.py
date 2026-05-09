"""Task #12 — admin tile for AEO Answer-Card + FAQ coverage.

Single endpoint:

  GET /api/admin/seo/aeo-coverage

Returns the per-board materialisation snapshot the admin dashboard
renders into the ``/admin/seo/aeo-coverage`` tile:

  {
    "totals": {"chapters": 3120, "with_faq": 2980, "with_quick_answer": 2980,
               "faq_coverage_pct": 95.5, "quick_answer_coverage_pct": 95.5},
    "by_board": [
      {"board": "AHSEC", "chapters": 1820, "with_faq": 1810,
       "with_quick_answer": 1810, "faq_coverage_pct": 99.5,
       "quick_answer_coverage_pct": 99.5},
      ...
    ],
    "generated_at": "2026-05-09T01:23:45+00:00"
  }

Reads ``db.chapter_faqs`` (authoritative store written by
``aca_jobs/materialize_chapter_faqs.py``) — the renderer views
``aeo_faq_entries`` / ``aeo_quick_answers`` are derivative and would
double-count when a chapter has multiple page-types replicated.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends

from auth_deps import get_admin_user
from deps import db

logger = logging.getLogger(__name__)
router = APIRouter()


def _pct(numer: int, denom: int) -> float:
    if not denom:
        return 0.0
    return round(100.0 * float(numer) / float(denom), 2)


async def _board_name_for_subject(subject_id: str,
                                  *, subj_cache: Dict[str, Dict],
                                  cls_cache: Dict[str, Dict],
                                  brd_cache: Dict[str, Dict],
                                  stream_cache: Dict[str, Dict]) -> str:
    """Resolve the board-name for a subject id by walking
    subject → (class | stream → class) → board. Cached dictionaries
    eliminate the per-chapter Mongo round-trip."""
    subj = subj_cache.get(subject_id)
    if not subj:
        return "Unknown"
    cls_id = subj.get("class_id") or ""
    if not cls_id and subj.get("stream_id"):
        stream = stream_cache.get(subj["stream_id"])
        cls_id = (stream or {}).get("class_id", "")
    cls = cls_cache.get(cls_id) if cls_id else None
    brd_id = subj.get("board_id") or (cls or {}).get("board_id") or ""
    brd = brd_cache.get(brd_id) if brd_id else None
    return (brd or {}).get("name") or "Unknown"


@router.get("/api/admin/seo/aeo-coverage")
async def admin_aeo_coverage(admin: dict = Depends(get_admin_user)):
    """Return per-board AEO Answer-Card + FAQ coverage for the admin tile."""
    # Pull every chapter id once.
    try:
        # Coverage % is meaningful only against the same denominator
        # the materializer walks (published chapters); counting drafts
        # would push the per-board ratio artificially low.
        chapters = await db.chapters.find(
            {"status": "published"},
            {"_id": 0, "id": 1, "subject_id": 1, "title": 1},
        ).to_list(20_000)
    except Exception as e:
        logger.warning("admin_aeo_coverage: chapters cursor failed: %s", e)
        chapters = []

    subj_ids = list({c.get("subject_id") for c in chapters
                     if c.get("subject_id")})
    subj_rows = await db.subjects.find(
        {"id": {"$in": subj_ids}},
        {"_id": 0, "id": 1, "class_id": 1, "stream_id": 1, "board_id": 1},
    ).to_list(5_000) if subj_ids else []
    subj_cache = {s["id"]: s for s in subj_rows}

    stream_ids = list({s.get("stream_id") for s in subj_rows if s.get("stream_id")})
    stream_rows = await db.streams.find(
        {"id": {"$in": stream_ids}}, {"_id": 0, "id": 1, "class_id": 1},
    ).to_list(2_000) if stream_ids else []
    stream_cache = {s["id"]: s for s in stream_rows}

    cls_ids = {s.get("class_id") for s in subj_rows if s.get("class_id")}
    cls_ids |= {s.get("class_id") for s in stream_rows if s.get("class_id")}
    cls_rows = await db.classes.find(
        {"id": {"$in": list(cls_ids)}},
        {"_id": 0, "id": 1, "board_id": 1},
    ).to_list(2_000) if cls_ids else []
    cls_cache = {c["id"]: c for c in cls_rows}

    brd_ids = {c.get("board_id") for c in cls_rows if c.get("board_id")}
    brd_ids |= {s.get("board_id") for s in subj_rows if s.get("board_id")}
    brd_rows = await db.boards.find(
        {"id": {"$in": list(brd_ids)}}, {"_id": 0, "id": 1, "name": 1},
    ).to_list(500) if brd_ids else []
    brd_cache = {b["id"]: b for b in brd_rows}

    # Pull the materialised chapter ids in one shot per kind.
    try:
        faq_rows = await db.chapter_faqs.distinct(
            "chapter_id", {"kind": "faq"},
        )
    except Exception:
        faq_rows = []
    try:
        qa_rows = await db.chapter_faqs.distinct(
            "chapter_id", {"kind": "quick_answer"},
        )
    except Exception:
        qa_rows = []
    faq_set = set(faq_rows or [])
    qa_set = set(qa_rows or [])

    # Group counts by board.
    per_board: Dict[str, Dict[str, int]] = {}
    totals = {"chapters": 0, "with_faq": 0, "with_quick_answer": 0}

    for ch in chapters:
        cid = ch.get("id") or ""
        board = await _board_name_for_subject(
            ch.get("subject_id") or "",
            subj_cache=subj_cache, cls_cache=cls_cache,
            brd_cache=brd_cache, stream_cache=stream_cache,
        )
        row = per_board.setdefault(
            board, {"chapters": 0, "with_faq": 0, "with_quick_answer": 0},
        )
        row["chapters"] += 1
        totals["chapters"] += 1
        if cid in faq_set:
            row["with_faq"] += 1
            totals["with_faq"] += 1
        if cid in qa_set:
            row["with_quick_answer"] += 1
            totals["with_quick_answer"] += 1

    by_board: List[Dict[str, Any]] = []
    for board, row in sorted(per_board.items()):
        by_board.append({
            "board":       board,
            "chapters":    row["chapters"],
            "with_faq":    row["with_faq"],
            "with_quick_answer": row["with_quick_answer"],
            "faq_coverage_pct":          _pct(row["with_faq"], row["chapters"]),
            "quick_answer_coverage_pct": _pct(
                row["with_quick_answer"], row["chapters"]),
        })

    return {
        "totals": {
            **totals,
            "faq_coverage_pct":          _pct(
                totals["with_faq"], totals["chapters"]),
            "quick_answer_coverage_pct": _pct(
                totals["with_quick_answer"], totals["chapters"]),
        },
        "by_board":     by_board,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
