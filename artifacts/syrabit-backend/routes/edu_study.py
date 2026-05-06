"""Syrabit.ai — Educational study superpowers (Phase 3).

Adds the following surfaces on top of the existing Edu Browser stack:

  Quiz generator        POST /api/edu/quiz/generate
  Notebook (notes CRUD) GET/POST /api/edu/notes
                        PATCH/DELETE /api/edu/notes/{note_id}
                        GET /api/edu/notes/export?format=md|csv
  Flashcards (SR)       POST /api/edu/flashcards/build
                        GET  /api/edu/flashcards/due
                        POST /api/edu/flashcards/review
                        GET  /api/edu/flashcards/streak
  Study settings        GET/POST /api/edu/study/settings
  Guardian PIN          POST /api/edu/guardian/pin/set
                        POST /api/edu/guardian/pin/verify
  Voice (STT proxy)     POST /api/edu/stt        (multipart audio → text)
  Voice (status)        GET  /api/edu/voice/status

Storage strategy:
  Authenticated users  → PostgreSQL (asyncpg pool from deps.pg_pool).
  Anonymous users      → identified by `x-anon-id` header (matches the
                         IP-credit model elsewhere). Same PG tables, just
                         a different `actor` discriminator.

The PG schema is created lazily on first use (idempotent CREATE TABLE
IF NOT EXISTS). Keeping it out of `deps.py` avoids touching the global
startup path.
"""
from __future__ import annotations

import asyncio
import io
import csv
import json
import uuid
import hmac
import hashlib
import logging
import random
import re
from datetime import datetime, timezone, timedelta, date
from typing import Optional, List, Any

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from auth_deps import get_current_user, get_current_user_optional, check_rate_limit, get_user_credits
from db_dualwrite import mirror_edu_flashcards_write, mirror_edu_notes_write
from llm import call_llm_api, _call_vertex_chat
from providers.azure_openai import call_chat as _az_quiz_chat
from guardrails.prompt_safety import validate_llm_output
import deps
from deps import sarvam_client
import vertex_chat as _vchat
from db_ops import supa_get_conversation, atomic_deduct_credit

logger = logging.getLogger(__name__)
router = APIRouter()

# ───────────────────────── Schema bootstrap ─────────────────────────

_SCHEMA_READY = False

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS edu_notes (
    id            TEXT PRIMARY KEY,
    actor_kind    TEXT NOT NULL,           -- 'user' | 'anon'
    actor         TEXT NOT NULL,
    text          TEXT NOT NULL,
    source_url    TEXT,
    source_title  TEXT,
    chapter_ref   TEXT,
    tags          TEXT[] NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS edu_notes_actor_idx ON edu_notes (actor_kind, actor, created_at DESC);
-- Task #612: stamp the moment an offline note was adopted into a user account
-- so the UI can render a "synced from this device" badge for the first session.
ALTER TABLE edu_notes ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ;
-- Task #641: NotebookLM-style AI-generated notes. `generated` flag + structured
-- JSON body (title, summary, outline, key_terms, qa) + citation anchors.
-- `source_kind` ∈ ('conversation','chapter','highlights'), `source_ref` is the
-- conversation id / chapter id / comma-joined note ids the AI was grounded on.
ALTER TABLE edu_notes ADD COLUMN IF NOT EXISTS generated BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE edu_notes ADD COLUMN IF NOT EXISTS structured JSONB;
ALTER TABLE edu_notes ADD COLUMN IF NOT EXISTS citations JSONB;
ALTER TABLE edu_notes ADD COLUMN IF NOT EXISTS source_kind TEXT;
ALTER TABLE edu_notes ADD COLUMN IF NOT EXISTS source_ref TEXT;

CREATE TABLE IF NOT EXISTS edu_flashcards (
    id            TEXT PRIMARY KEY,
    actor_kind    TEXT NOT NULL,
    actor         TEXT NOT NULL,
    note_id       TEXT,
    front         TEXT NOT NULL,
    back          TEXT NOT NULL,
    ef            REAL NOT NULL DEFAULT 2.5,
    interval_days INTEGER NOT NULL DEFAULT 0,
    repetitions   INTEGER NOT NULL DEFAULT 0,
    due_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_reviewed TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS edu_flashcards_due_idx ON edu_flashcards (actor_kind, actor, due_at);
ALTER TABLE edu_flashcards ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS edu_study_settings (
    actor_kind        TEXT NOT NULL,
    actor             TEXT NOT NULL,
    strict_mode       BOOLEAN NOT NULL DEFAULT FALSE,
    guardian_pin_hash TEXT,
    streak_count      INTEGER NOT NULL DEFAULT 0,
    streak_last_day   DATE,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (actor_kind, actor)
);
"""


async def _ensure_schema():
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    if not deps.pg_pool:
        raise HTTPException(status_code=503, detail="storage_unavailable")
    async with deps.pg_pool.acquire() as conn:
        await conn.execute(_SCHEMA_SQL)
    _SCHEMA_READY = True


# ───────────────────────── Helpers ─────────────────────────

def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _actor(request: Request, user) -> tuple[str, str]:
    if user and user.get("id"):
        return "user", user["id"]
    anon = request.headers.get("x-anon-id", "").strip()[:80]
    if anon:
        return "anon", anon
    ip = _client_ip(request)
    return "anon", "ip:" + hashlib.sha256(ip.encode()).hexdigest()[:24]


def _strip_md(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[`*_#>~]+", "", s or "")).strip()


def _note_row_to_dict(row) -> dict:
    # Task #641: `structured`/`citations` are stored as JSONB and surface as
    # parsed objects to asyncpg; older rows (or rows from before the column
    # was added) come back as None — we expose the column only when set so
    # the frontend can branch on `note.generated`.
    structured = row["structured"] if "structured" in row.keys() else None
    citations = row["citations"] if "citations" in row.keys() else None
    if isinstance(structured, str):
        try: structured = json.loads(structured)
        except Exception: structured = None
    if isinstance(citations, str):
        try: citations = json.loads(citations)
        except Exception: citations = None
    return {
        "id": row["id"],
        "text": row["text"],
        "source_url": row["source_url"] or "",
        "source_title": row["source_title"] or "",
        "chapter_ref": row["chapter_ref"] or "",
        "tags": list(row["tags"] or []),
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
        "claimed_at": (
            row["claimed_at"].isoformat()
            if "claimed_at" in row.keys() and row["claimed_at"]
            else None
        ),
        "generated": bool(row["generated"]) if "generated" in row.keys() else False,
        "structured": structured,
        "citations": citations or [],
        "source_kind": row["source_kind"] if "source_kind" in row.keys() else None,
        "source_ref": row["source_ref"] if "source_ref" in row.keys() else None,
    }


def _note_row_for_mongo(row) -> dict:
    """Convert an asyncpg ``edu_notes`` Record into a Mongo document.

    ADR-0001 §50 — greenfield collection. Composite key is
    ``{actor_kind, actor, id}``; we keep ``id`` as a plain field (not
    Mongo's reserved ``_id``) to match the users/conversations rollout
    pattern. JSONB columns ``structured`` / ``citations`` may surface as
    ``str`` *or* native dict/list depending on asyncpg's codec setup
    (see ``_note_row_to_dict`` for the read-side equivalent); normalize
    to native types so future Phase 3 read-shadow diffs aren't tripped
    by string-vs-dict noise.
    """
    d = dict(row)
    for k in ("structured", "citations"):
        v = d.get(k)
        if isinstance(v, str):
            try:
                d[k] = json.loads(v)
            except Exception:
                d[k] = None
    d["tags"] = list(d.get("tags") or [])
    return d


# ───────────────────────── Quiz generator ─────────────────────────

class QuizGenReq(BaseModel):
    context: str = Field("", max_length=12000)
    topic: str = Field("", max_length=300)
    chapter_ref: str = Field("", max_length=300)
    subject_name: str = Field("", max_length=200)
    count: int = Field(7, ge=3, le=10)
    response_lang: str = Field("en", max_length=8)


_QUIZ_SYS = """You are an expert exam-question writer for Indian school students.
Return a STRICT JSON object (no prose, no markdown fences) of the form:
{
  "questions": [
    {
      "q": "Question text (single-best-answer MCQ that probes a CORE concept)",
      "choices": ["A choice", "Another", "Third", "Fourth"],
      "answer": 0,
      "explanation": "1-2 sentence reason for the correct choice."
    }
  ]
}
Rules:
- Exactly 4 choices per question.
- "answer" is the 0-based index of the correct choice.
- Each question MUST test the student's CORE CONCEPTUAL UNDERSTANDING of
  the chapter — not surface-level recall. Prefer "why does X happen",
  "which principle explains Y", "predict the outcome of Z", "identify the
  best example of W", "compare/contrast", "apply to a new scenario".
  AVOID trivia, dates-only, name-dropping, or copy-pasted definitions.
- Distractors must be PLAUSIBLE — common misconceptions, near-misses, or
  partially-correct ideas — so a student who only memorised the surface
  facts cannot eliminate them by elimination alone.
- Cover the WHOLE chapter. Spread questions across the major sub-topics
  proportionally; do not bunch all questions on a single section.
- Diversify Bloom's levels — mix understand / apply / analyse questions.
- Avoid duplicate questions and avoid questions that are merely
  rephrasings of each other.
- Avoid trick questions or questions that hinge on ambiguous wording;
  aim for board-exam style clarity.
- Never quote PII, never include personal opinions, never reference Syrabit."""


# How many questions to keep in the per-chapter pool. Generated ONCE at
# chapter-creation time (or lazily on the first miss) and then sampled +
# shuffled for every student request, so each student sees a distinct
# selection of questions in a distinct order with distinct choice
# orderings — without ever paying another LLM round-trip. Sized to ≈3-4
# quizzes worth of unique questions (the on-screen quiz is 7 by
# default), giving thousands of distinct (question-set × ordering)
# combinations from a single generation.
_QUIZ_POOL_SIZE = 24

# Larger token budget for the one-shot pool generation. 24 questions of
# ~150 tokens each + JSON structural overhead ≈ 4-5k; pad to 6k for
# safety. Per-request cache hits do not pay this — only the very first
# (admin pre-gen or first-student) write call.
_QUIZ_POOL_MAX_TOKENS = 6000


def _coerce_quiz_payload(raw: str) -> dict:
    """Tolerant JSON parse: strip code fences, trim before/after braces."""
    txt = (raw or "").strip()
    if "```" in txt:
        m = re.search(r"```(?:json)?\s*([\s\S]+?)```", txt)
        if m:
            txt = m.group(1).strip()
    if "{" in txt and "}" in txt:
        txt = txt[txt.index("{"):txt.rindex("}") + 1]
    try:
        return json.loads(txt)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"quiz_parse_error: {e}")


QUIZ_DAILY_CAP = 200          # Task #615: per-actor LLM call budget per UTC day.

# Task #739: daily caps reset at UTC midnight (DB-friendly), but the
# user base is in IST. Show both so a 9 PM IST student isn't confused
# by "midnight UTC" (which is 5:30 AM IST the next morning).
DAILY_RESET_LABEL = "midnight UTC (5:30 AM IST)"
QUIZ_DAY_WINDOW_SEC = 86400


def _quiz_daily_key(kind: str, actor: str) -> str:
    """Stable rl2 key for the per-actor daily quiz cap. Kept in one place
    so the admin read/reset endpoints derive the exact same string."""
    return f"edu_quiz_day:{kind}:{actor}"


# ─── Permanent per-chapter quiz cache ──────────────────────────────────
# Chapter quizzes are now generated ONCE and reused forever instead of
# re-rolling a fresh batch on every "Quiz me" click. The intent (per the
# user request "make quiz at chapter creation time only, one permanent,
# no regeneration"):
#   * Admin creates a chapter        → background task generates the
#                                       quiz and stores it.
#   * Student clicks "Quiz me"       → cache hit returns the pinned
#                                       quiz instantly, no LLM call,
#                                       no rate-limit charge.
#   * Cache miss for a legacy chapter → fall back to the existing
#                                        on-the-fly LLM path AND store
#                                        the result so subsequent clicks
#                                        also hit the cache.
#
# Storage: MongoDB ``chapter_quizzes`` collection (Mongo, like the rest
# of the syllabus / chapter content, so the same admin tooling and
# backup story applies). Cache key is the pair
# ``(chapter_ref, response_lang)`` because the questions themselves are
# translated per-language. Index is created lazily on first use so we
# don't touch the global startup path.

_QUIZ_CACHE_INDEXES_READY = False


def _normalize_chapter_ref(ref: str) -> str:
    """Cache lookups must be order- and case-stable across the various
    spots that produce a chapter_ref (frontend ChapterPage builds
    ``board/class/subject/chapter`` without a leading slash; the
    backend ``_build_chapter_url`` returns it WITH a leading slash;
    admin tooling may have trailing slashes from copy-pasted URLs).
    Strip slashes and lowercase so all callers map to the same key."""
    return (ref or "").strip().strip("/").lower()


async def _ensure_quiz_cache_indexes() -> None:
    global _QUIZ_CACHE_INDEXES_READY
    if _QUIZ_CACHE_INDEXES_READY:
        return
    if not getattr(deps, "db", None):
        return
    try:
        await deps.db.chapter_quizzes.create_index(
            [("chapter_ref", 1), ("response_lang", 1)], unique=True
        )
        await deps.db.chapter_quizzes.create_index("chapter_id")
        _QUIZ_CACHE_INDEXES_READY = True
    except Exception as e:
        # Best-effort — a missing index just means slower lookups, not
        # a broken feature. Don't fail the request over it.
        logger.warning(f"[edu_quiz] cache index create failed: {e}")


async def _lookup_cached_quiz(chapter_ref: str, response_lang: str) -> Optional[dict]:
    """Return the persisted quiz dict for this chapter+language, or
    ``None`` if there is no cached entry yet. Always safe to call —
    swallows transport errors so a flaky Mongo never blocks the LLM
    fallback path."""
    if not chapter_ref:
        return None
    if not getattr(deps, "db", None):
        return None
    await _ensure_quiz_cache_indexes()
    try:
        doc = await deps.db.chapter_quizzes.find_one(
            {
                "chapter_ref": _normalize_chapter_ref(chapter_ref),
                "response_lang": (response_lang or "en").lower()[:8],
            },
            # Pull `chapter_id` too so the lazy-upgrade path in
            # quiz_generate can re-fetch the chapter doc without a
            # second round-trip through `_resolve_quiz_cache_chapter_ref`.
            {"_id": 0, "questions": 1, "count": 1, "chapter_id": 1},
        )
    except Exception as e:
        logger.warning(f"[edu_quiz] cache lookup failed for {chapter_ref!r}: {e}")
        return None
    if not doc or not doc.get("questions"):
        return None
    return doc


# Per-process dedupe for the lazy "legacy 7-question row → 24-question
# pool" upgrade. Multiple students hitting a legacy chapter at once
# would otherwise queue N parallel pool-regenerations of the same
# chapter and waste LLM spend on duplicate work. Membership is removed
# in a `finally` so a crashed upgrade can be retried by the next click.
_QUIZ_POOL_UPGRADE_INFLIGHT: set[str] = set()


async def _resolve_chapter_id_from_ref(chapter_ref: str) -> str:
    """Inverse of ``_resolve_quiz_cache_chapter_ref``: given the
    ``board/class/subject/chapter`` slug path the frontend sends,
    walk the four parent collections and return the chapter id, or
    "" if any segment is missing or unmatched. Used as a fallback
    by the legacy-pool upgrade when a cache row was written by the
    lazy-backfill code path (which historically did not stamp
    chapter_id on the row). Non-fatal — returns "" on any error."""
    if not getattr(deps, "db", None):
        return ""
    try:
        parts = _normalize_chapter_ref(chapter_ref).split("/")
        if len(parts) < 4:
            return ""
        board_slug, cls_slug, subj_slug, ch_slug = parts[:4]
        board = await deps.db.boards.find_one(
            {"slug": board_slug}, {"_id": 0, "id": 1}
        )
        if not board:
            return ""
        cls = await deps.db.classes.find_one(
            {"slug": cls_slug, "board_id": board["id"]}, {"_id": 0, "id": 1},
        )
        if not cls:
            return ""
        subj = await deps.db.subjects.find_one(
            {"slug": subj_slug, "class_id": cls["id"]}, {"_id": 0, "id": 1},
        )
        if not subj:
            return ""
        ch = await deps.db.chapters.find_one(
            {"slug": ch_slug, "subject_id": subj["id"]}, {"_id": 0, "id": 1},
        )
        return (ch or {}).get("id", "") or ""
    except Exception as e:
        logger.warning(
            f"[edu_quiz] _resolve_chapter_id_from_ref crashed for "
            f"{chapter_ref!r}: {e}"
        )
        return ""


async def _maybe_upgrade_legacy_quiz_pool(
    chapter_id: str, chapter_ref: str, response_lang: str,
) -> None:
    """Background task: if a cache hit returned a small pool (a row
    written before `_QUIZ_POOL_SIZE` was raised to 24), regenerate
    the FULL conceptual pool and overwrite the cache. Idempotent and
    deduped. Best-effort — failure just leaves the legacy row in
    place and the next click attempts again."""
    key = f"{_normalize_chapter_ref(chapter_ref)}::{(response_lang or 'en').lower()[:8]}"
    if key in _QUIZ_POOL_UPGRADE_INFLIGHT:
        return
    _QUIZ_POOL_UPGRADE_INFLIGHT.add(key)
    try:
        if not getattr(deps, "db", None):
            return
        # Cache rows written by the older lazy-backfill code path do
        # NOT carry chapter_id (it was only stamped on rows written
        # by the admin pre-gen hook). Resolve it from the chapter_ref
        # slug so those rows can still auto-upgrade.
        if not chapter_id:
            chapter_id = await _resolve_chapter_id_from_ref(chapter_ref)
        if not chapter_id:
            logger.info(
                f"[edu_quiz] legacy pool upgrade skipped for "
                f"{chapter_ref!r} — could not resolve chapter_id"
            )
            return
        chapter_doc = await deps.db.chapters.find_one({"id": chapter_id})
        if not chapter_doc:
            logger.info(
                f"[edu_quiz] legacy pool upgrade skipped — chapter {chapter_id} "
                f"no longer exists"
            )
            return
        upgraded = await pregenerate_chapter_quiz(
            chapter_doc, count=_QUIZ_POOL_SIZE, response_lang=response_lang,
        )
        if upgraded:
            logger.info(
                f"[edu_quiz] upgraded legacy small-pool cache to "
                f"{_QUIZ_POOL_SIZE}-question pool for chapter_id={chapter_id} "
                f"chapter_ref={chapter_ref!r} lang={response_lang}"
            )
    except Exception as e:
        logger.warning(
            f"[edu_quiz] legacy pool upgrade crashed for {chapter_ref!r}: {e}"
        )
    finally:
        _QUIZ_POOL_UPGRADE_INFLIGHT.discard(key)


async def _save_quiz_cache(
    chapter_ref: str,
    response_lang: str,
    questions: list[dict],
    *,
    chapter_id: str = "",
) -> None:
    """Pin a freshly-generated quiz so the next click serves it
    without an LLM round-trip. Upsert (instead of insert) so a race
    between two simultaneous cache misses can't 500 the second
    request — the loser just overwrites with an equivalent payload."""
    norm = _normalize_chapter_ref(chapter_ref)
    if not norm or not questions:
        return
    if not getattr(deps, "db", None):
        return
    await _ensure_quiz_cache_indexes()
    payload = {
        "chapter_ref": norm,
        "response_lang": (response_lang or "en").lower()[:8],
        "chapter_id": chapter_id or "",
        "questions": questions,
        "count": len(questions),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        await deps.db.chapter_quizzes.update_one(
            {"chapter_ref": norm,
             "response_lang": payload["response_lang"]},
            {"$set": payload},
            upsert=True,
        )
    except Exception as e:
        logger.warning(f"[edu_quiz] cache save failed for {chapter_ref!r}: {e}")


def _sample_and_shuffle(pool: list[dict], count: int) -> list[dict]:
    """Pick ``count`` random questions from the cached pool, shuffle
    their order, AND shuffle the four choices inside each question
    (re-mapping the ``answer`` index so it still points at the correct
    choice in its new position).

    Why three layers of shuffling:
      1. Subset sampling — different students see different questions
         out of the chapter's 24-question pool.
      2. Question-order shuffle — even two students who happen to draw
         the same subset see them in different orders.
      3. Choice-order shuffle — students can't share "the answer to
         Q3 is C" with each other.

    All three layers use the standard library RNG (Mersenne Twister) —
    we don't need cryptographic randomness, just enough variety that
    no two students see the same paper. ``count`` is clamped to the
    pool size so we never raise ``ValueError`` on a too-small pool."""
    if not pool:
        return []
    n = max(1, min(count or 1, len(pool)))
    sampled = random.sample(pool, n)
    out: list[dict] = []
    for q in sampled:
        choices = list(q.get("choices") or [])
        if len(choices) != 4:
            # Defensive — never happens for cleaned questions but
            # guards against forward-incompat cache rows.
            out.append(q)
            continue
        try:
            old_ans = int(q.get("answer", 0))
        except Exception:
            old_ans = 0
        if old_ans < 0 or old_ans > 3:
            old_ans = 0
        perm = list(range(4))
        random.shuffle(perm)
        new_choices = [choices[i] for i in perm]
        new_answer = perm.index(old_ans)
        out.append({
            **q,
            "choices": new_choices,
            "answer": new_answer,
        })
    return out


async def _generate_and_clean_quiz(
    *,
    context: str,
    topic: str,
    chapter_ref: str,
    subject_name: str,
    count: int,
    response_lang: str,
    max_tokens: int = 2000,
    prefer_vertex: bool = False,
) -> list[dict]:
    """Pure LLM-call + parse + clean + safety-validate path. Extracted
    from ``quiz_generate`` so the admin background pre-generation hook
    can reuse the exact same generation contract without duplicating
    the prompt template, JSON tolerant-parse, choice validation,
    answer-index clamp, or guardrails pass.

    ``max_tokens`` lets the pool-generation caller raise the LLM
    output budget (24 questions need ≈3-5k tokens; the legacy
    7-question request fits well inside the 2000-token default).

    ``prefer_vertex=True`` swaps the provider order to **Vertex
    Gemini PRIMARY → Azure GPT-4.1-mini FALLBACK**. The 2026-05-05
    user instruction made Vertex / Gemini the formatting-stage owner
    (see ``llm.polish_notes_with_vertex``); the same Vertex slot now
    owns quiz pre-generation kicked off from the polish stage so the
    "Quiz Me" cache is hot the moment the chapter polishes through.
    Default ``False`` preserves the historical Azure-primary order
    used by lazy on-click generation in ``quiz_generate``.

    Returns the cleaned list of question dicts. Raises ``HTTPException``
    on any failure so the route handler can propagate the same status
    codes the original inline code did."""
    ctx_text = (context or "").strip()
    if not ctx_text and not topic:
        raise HTTPException(status_code=400, detail="context or topic required")
    if len(ctx_text) > 12000:
        ctx_text = ctx_text[:12000]
    user_msg_parts = [
        f"Subject: {subject_name}" if subject_name else "",
        f"Chapter: {chapter_ref}" if chapter_ref else "",
        f"Topic focus: {topic}" if topic else "",
        f"Generate exactly {count} MCQs.",
    ]
    if response_lang and response_lang.lower().startswith("as"):
        user_msg_parts.append("Write the questions, choices and explanations in Assamese (as-IN).")
    if ctx_text:
        user_msg_parts.append("\n--- SOURCE TEXT ---\n" + ctx_text)
    messages = [
        {"role": "system", "content": _QUIZ_SYS},
        {"role": "user",   "content": "\n".join([p for p in user_msg_parts if p])},
    ]
    if prefer_vertex:
        # Vertex / Gemini PRIMARY (formatting-stage owner per 2026-05-05
        # instruction) → Azure GPT-4.1-mini FALLBACK.
        try:
            raw = await _call_vertex_chat(messages, "gemini-2.5-flash", max_tokens)
        except Exception as e:
            logger.warning(
                f"[edu_quiz] Vertex Gemini quiz LLM call failed (prefer_vertex=True): "
                f"{e} — falling back to Azure GPT-4.1-mini"
            )
            try:
                raw = await _az_quiz_chat(messages, max_tokens=max_tokens)
            except Exception as e2:
                logger.warning(f"[edu_quiz] Azure quiz fallback also failed: {e2}")
                raise HTTPException(status_code=502, detail="quiz_llm_failed")
    else:
        try:
            raw = await _az_quiz_chat(messages, max_tokens=max_tokens)
        except Exception as e:
            logger.warning(f"[edu_quiz] Azure quiz LLM call failed: {e} — trying Vertex Gemini fallback")
            try:
                raw = await _call_vertex_chat(messages, "gemini-2.5-flash", max_tokens)
            except Exception as e2:
                logger.warning(f"[edu_quiz] Vertex Gemini quiz fallback also failed: {e2}")
                raise HTTPException(status_code=502, detail="quiz_llm_failed")
    payload = _coerce_quiz_payload(raw)
    questions = payload.get("questions") or []
    cleaned: list[dict] = []
    for q in questions[:count]:
        if not isinstance(q, dict):
            continue
        choices = q.get("choices") or []
        if not (isinstance(choices, list) and len(choices) == 4):
            continue
        try:
            ans = int(q.get("answer", 0))
        except Exception:
            ans = 0
        if ans < 0 or ans > 3:
            ans = 0
        cleaned.append({
            "id": str(uuid.uuid4()),
            "q": str(q.get("q", "")).strip()[:500],
            "choices": [str(c).strip()[:240] for c in choices],
            "answer": ans,
            "explanation": str(q.get("explanation", "")).strip()[:600],
        })
    if not cleaned:
        raise HTTPException(status_code=502, detail="quiz_no_questions")
    flat = " ".join(c["q"] + " " + c["explanation"] for c in cleaned)
    ok, _why = validate_llm_output(flat)
    if not ok:
        raise HTTPException(status_code=502, detail="quiz_safety_block")
    return cleaned


async def _resolve_quiz_cache_chapter_ref(chapter_doc: dict) -> str:
    """Resolve the canonical cache key for a chapter — the SAME
    `board/class/subject/chapter` slug path the frontend's ChapterPage
    builds at runtime when calling /edu/quiz/generate.

    IMPORTANT: this intentionally drops the stream segment even when
    the subject lives under a stream (e.g. AHSEC Class 12 → Science).
    ``ChapterPage.jsx`` constructs ``${board}/${classSlug}/${subjectSlug}/${chapterSlug}``
    without a stream level, and ``App.jsx`` further redirects any
    stream-bearing chapter URL down to the no-stream shape, so the
    student request never carries the stream slug. Reusing the
    stream-aware ``_build_chapter_url`` here would key the cache under
    a path the frontend never asks for and the pre-generated quiz
    would silently never be hit. Returns "" if any required parent
    slug is missing — caller logs and skips."""
    try:
        ch_slug = (chapter_doc.get("slug") or "").strip()
        subj_id = (chapter_doc.get("subject_id") or "").strip()
        if not (ch_slug and subj_id):
            return ""
        subj = await deps.db.subjects.find_one(
            {"id": subj_id},
            {"_id": 0, "slug": 1, "class_id": 1, "board_id": 1, "stream_id": 1},
        )
        if not subj:
            return ""
        subj_slug = (subj.get("slug") or "").strip()
        cls_id = (subj.get("class_id") or "").strip()
        board_id = (subj.get("board_id") or "").strip()
        stream_id = (subj.get("stream_id") or "").strip()
        # Stream-only subjects don't denormalise class_id/board_id, so
        # walk one extra hop via streams → class → board to fill them
        # in. Mirrors the same fallback in `_build_chapter_url`.
        if stream_id and not (cls_id and board_id):
            stream = await deps.db.streams.find_one(
                {"id": stream_id}, {"_id": 0, "class_id": 1}
            )
            if stream:
                cls_id = cls_id or stream.get("class_id") or ""
        if cls_id and not board_id:
            cls = await deps.db.classes.find_one(
                {"id": cls_id}, {"_id": 0, "board_id": 1}
            )
            if cls:
                board_id = cls.get("board_id") or ""
        if not (cls_id and board_id and subj_slug):
            return ""
        cls = await deps.db.classes.find_one({"id": cls_id}, {"_id": 0, "slug": 1})
        board = await deps.db.boards.find_one({"id": board_id}, {"_id": 0, "slug": 1})
        if not (cls and board):
            return ""
        cls_slug = (cls.get("slug") or "").strip()
        board_slug = (board.get("slug") or "").strip()
        if not (cls_slug and board_slug):
            return ""
        return _normalize_chapter_ref(
            f"{board_slug}/{cls_slug}/{subj_slug}/{ch_slug}"
        )
    except Exception as e:
        logger.warning(f"[edu_quiz] _resolve_quiz_cache_chapter_ref crashed: {e}")
        return ""


async def pregenerate_chapter_quiz(
    chapter_doc: dict,
    *,
    count: int = _QUIZ_POOL_SIZE,
    response_lang: str = "en",
    prefer_vertex: bool = False,
) -> bool:
    """Public hook called from ``admin_create_chapter`` (and any future
    bulk-import / migration script) to materialise the permanent quiz
    cache entries for a chapter at the moment it is created. Best-effort:
    returns True if ALL three language copies succeed, False if any fail
    (logged individually). Never raises — the chapter creation flow must
    not be blocked by a flaky LLM, and the lazy fallback in
    ``quiz_generate`` will recover the miss the first time a student
    opens the quiz anyway.

    Generates THREE independent copies (English, Assamese, Hindi) via
    Azure GPT-4.1-mini and stores each under its own lang key in MongoDB.
    This matches the "store multiple copies per chapter" spec and ensures
    every language tab loads instantly from cache without any LLM round-trip.

    Resolves the chapter_ref slug-path the SAME way the frontend
    constructs it (board/class/subject/chapter, no stream segment) via
    ``_resolve_quiz_cache_chapter_ref`` so the cache key the admin
    pre-gen writes is the SAME key ``quiz_generate`` will look up on
    a student click. Skips quietly if the chapter has no body content
    (no source text → not enough material to write a meaningful MCQ set)."""
    try:
        title = (chapter_doc.get("title") or "").strip()
        content = (chapter_doc.get("content") or "").strip()
        chapter_id = chapter_doc.get("id") or ""
        if not (title and content and len(content) > 200):
            logger.info(
                f"[edu_quiz] pregenerate skipped for {chapter_id} — no body content"
            )
            return False
        chapter_ref = await _resolve_quiz_cache_chapter_ref(chapter_doc)
        if not chapter_ref:
            logger.info(
                f"[edu_quiz] pregenerate skipped for {chapter_id} — could not "
                f"resolve parent slugs (board/class/subject/chapter)"
            )
            return False
        # Resolve the parent subject's display name for the prompt
        # (improves question quality and matches what the frontend
        # sends from ChapterPage.jsx).
        subject_name = ""
        try:
            subj = await deps.db.subjects.find_one(
                {"id": chapter_doc.get("subject_id") or ""},
                {"_id": 0, "name": 1, "title": 1},
            )
            if subj:
                subject_name = (subj.get("name") or subj.get("title") or "")[:200]
        except Exception:
            pass
        # Generate three language copies (en, as, hi) via Azure GPT-4.1-mini.
        # Each copy is stored independently; all three must succeed for True return.
        _PREGENERATE_LANGS = ["en", "as", "hi"]
        all_ok = True
        for lang in _PREGENERATE_LANGS:
            try:
                cleaned = await _generate_and_clean_quiz(
                    context=content,
                    topic=title,
                    chapter_ref=chapter_ref,
                    subject_name=subject_name,
                    count=count,
                    response_lang=lang,
                    # Pool generation needs a much larger output budget than
                    # the legacy 7-question request — see _QUIZ_POOL_MAX_TOKENS.
                    max_tokens=(_QUIZ_POOL_MAX_TOKENS if count >= 12 else 2000),
                    # Honour the caller's provider preference: when the
                    # polish/format stage triggers pre-generation
                    # (`prefer_vertex=True`) the Vertex Gemini slot is
                    # primary so a single Gemini session formats the notes
                    # AND writes the quiz pool; lazy on-click pre-gen from
                    # `quiz_generate` keeps the historical Azure-primary
                    # default.
                    prefer_vertex=prefer_vertex,
                )
                await _save_quiz_cache(
                    chapter_ref, lang, cleaned, chapter_id=chapter_id
                )
                logger.info(
                    f"[edu_quiz] pregenerated and cached pool of {len(cleaned)} questions "
                    f"for chapter_id={chapter_id} chapter_ref={chapter_ref!r} "
                    f"lang={lang} (target pool size={count})"
                )
            except HTTPException as e:
                logger.warning(
                    f"[edu_quiz] pregenerate lang={lang} failed for "
                    f"{chapter_id!r}: {e.detail}"
                )
                all_ok = False
            except Exception as e:
                logger.warning(
                    f"[edu_quiz] pregenerate lang={lang} crashed for {chapter_id!r}: {e}"
                )
                all_ok = False
        return all_ok
    except Exception as e:
        logger.warning(
            f"[edu_quiz] pregenerate crashed for {chapter_doc.get('id')!r}: {e}"
        )
        return False


@router.post("/edu/quiz/generate")
async def quiz_generate(req: QuizGenReq, request: Request,
                        user=Depends(get_current_user_optional)):
    # Permanent-per-chapter cache short-circuit. Keyed by chapter_ref
    # (the slug path the frontend already sends) + response_lang. Cache
    # hits skip BOTH the per-IP burst limit AND the per-actor daily cap
    # because they cost zero LLM tokens — the student isn't really
    # "generating" a quiz, just opening the one that already exists.
    if req.chapter_ref:
        cached = await _lookup_cached_quiz(req.chapter_ref, req.response_lang)
        if cached:
            # Each student gets a DIFFERENT random subset of the
            # chapter's question pool, in a randomised order, with the
            # four choices of every question shuffled. This is the
            # "3-4 quizzes per chapter, shuffle and provide to user"
            # behaviour: one stored pool of ~24 conceptual questions
            # at chapter creation, but every click serves a fresh
            # shuffled paper drawn from it.
            pool = cached["questions"]
            qs = _sample_and_shuffle(pool, req.count)
            # Lazy upgrade: rows written before _QUIZ_POOL_SIZE was
            # raised to 24 only have ~7 questions, which limits the
            # shuffle variety to "same 7 in a different order". Kick
            # off a background regeneration of the full pool so the
            # NEXT student to open this chapter gets the new pool.
            # The current student still gets the (smaller) shuffled
            # paper immediately — no extra latency on this request.
            if len(pool) < 12:
                try:
                    asyncio.create_task(_maybe_upgrade_legacy_quiz_pool(
                        # May be empty for legacy lazy-backfilled rows —
                        # the upgrade helper will resolve via slug as a
                        # fallback before giving up.
                        chapter_id=cached.get("chapter_id") or "",
                        chapter_ref=req.chapter_ref,
                        response_lang=req.response_lang,
                    ))
                except RuntimeError:
                    # No running loop in some test contexts — safe to ignore.
                    pass
            return {
                "ok": True,
                "questions": qs,
                "count": len(qs),
                "cached": True,
                "pool_size": len(pool),
            }

    ip = _client_ip(request)
    if not check_rate_limit(f"edu_quiz:{ip}", max_requests=15, window_seconds=300):
        raise HTTPException(status_code=429, detail="Too many quiz requests; try again later.")
    # Task #615: even within the per-IP burst limit, cap the absolute number
    # of LLM-backed quiz generations any single actor (signed-in user OR
    # device anon-id, falling back to a salted IP hash) can request per UTC
    # day. This bounds worst-case spend for grinders behind the burst limit.
    kind, actor = _actor(request, user)
    if not check_rate_limit(_quiz_daily_key(kind, actor),
                            max_requests=QUIZ_DAILY_CAP,
                            window_seconds=QUIZ_DAY_WINDOW_SEC):
        raise HTTPException(
            status_code=429,
            detail={
                "error": "quiz_daily_cap",
                "limit": QUIZ_DAILY_CAP,
                "scope": "day",
                "resets_at": DAILY_RESET_LABEL,
                "message": (
                    f"Daily quiz limit reached ({QUIZ_DAILY_CAP}/day). "
                    f"Try again after {DAILY_RESET_LABEL}."
                ),
            },
            headers={
                "Retry-After": "3600",
                "X-RateLimit-Limit": str(QUIZ_DAILY_CAP),
                "X-RateLimit-Scope": "day",
            },
        )
    # Cache miss path: when the request carries a chapter_ref (the
    # normal student flow), generate the FULL pool (~24 conceptual
    # questions) so every subsequent student gets an instant shuffled
    # subset. When chapter_ref is empty (ad-hoc quiz from arbitrary
    # context — e.g. selected text), keep the original lightweight
    # behaviour of generating exactly the requested count.
    pool_target = _QUIZ_POOL_SIZE if req.chapter_ref else req.count
    cleaned = await _generate_and_clean_quiz(
        context=req.context,
        topic=req.topic,
        chapter_ref=req.chapter_ref,
        subject_name=req.subject_name,
        count=pool_target,
        response_lang=req.response_lang,
        max_tokens=(_QUIZ_POOL_MAX_TOKENS if pool_target >= 12 else 2000),
    )
    # Lazy backfill — pin the freshly-generated POOL to the cache so
    # the next click on this same chapter is a cache hit (and so the
    # student community as a whole only ever pays the LLM cost once
    # per chapter+language combo).
    if req.chapter_ref:
        await _save_quiz_cache(req.chapter_ref, req.response_lang, cleaned)
        # Sample + shuffle the just-generated pool for THIS request
        # too, so the first student gets the same variety experience
        # as every cache-hit student that follows.
        qs = _sample_and_shuffle(cleaned, req.count)
        return {
            "ok": True,
            "questions": qs,
            "count": len(qs),
            "cached": False,
            "pool_size": len(cleaned),
        }
    # No chapter_ref → ad-hoc one-off quiz, no caching, return as-is.
    return {"ok": True, "questions": cleaned, "count": len(cleaned), "cached": False}


# ───────────────────────── Notebook (notes) ─────────────────────────

class NoteCreateReq(BaseModel):
    text: str = Field(..., min_length=1, max_length=8000)
    source_url: str = Field("", max_length=2048)
    source_title: str = Field("", max_length=400)
    chapter_ref: str = Field("", max_length=400)
    tags: List[str] = Field(default_factory=list)


class NotePatchReq(BaseModel):
    text: Optional[str] = Field(None, max_length=8000)
    tags: Optional[List[str]] = None


def _norm_tags(tags) -> list[str]:
    if not tags:
        return []
    out: list[str] = []
    for t in tags:
        s = str(t).strip().lower()
        if not s:
            continue
        s = re.sub(r"[^a-z0-9\-_]+", "-", s)[:32]
        if s and s not in out:
            out.append(s)
        if len(out) >= 12:
            break
    return out


@router.post("/edu/notes")
async def create_note(req: NoteCreateReq, request: Request,
                      user=Depends(get_current_user_optional)):
    await _ensure_schema()
    kind, actor = _actor(request, user)
    if not check_rate_limit(f"edu_note:{actor}", max_requests=120, window_seconds=300):
        raise HTTPException(status_code=429, detail="Save rate limit exceeded.")
    nid = str(uuid.uuid4())
    tags = _norm_tags(req.tags)
    async with deps.pg_pool.acquire() as conn:
        cnt = await conn.fetchval(
            "SELECT COUNT(*) FROM edu_notes WHERE actor_kind=$1 AND actor=$2",
            kind, actor,
        )
        if cnt is not None and cnt >= 2000:
            raise HTTPException(status_code=400, detail="notebook_full")
        row = await conn.fetchrow(
            """INSERT INTO edu_notes (id, actor_kind, actor, text, source_url,
                  source_title, chapter_ref, tags)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
               RETURNING *""",
            nid, kind, actor, req.text.strip(), req.source_url.strip(),
            req.source_title.strip(), req.chapter_ref.strip(), tags,
        )
    # ADR-0001 Phase 2 (V4 §13): mirror to Mongo (best-effort; PG is SoT).
    _mongo_doc = _note_row_for_mongo(row)
    await mirror_edu_notes_write(
        "insert",
        lambda: deps.db.edu_notes.insert_one(_mongo_doc),
    )
    return {"ok": True, "note": _note_row_to_dict(row)}


@router.get("/edu/notes")
async def list_notes(request: Request, q: str = "", tag: str = "",
                     limit: int = 100, offset: int = 0,
                     user=Depends(get_current_user_optional)):
    await _ensure_schema()
    kind, actor = _actor(request, user)
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    sql = ["SELECT * FROM edu_notes WHERE actor_kind=$1 AND actor=$2"]
    args: list[Any] = [kind, actor]
    if q:
        args.append(f"%{q.strip()[:200]}%")
        sql.append(f"AND (text ILIKE ${len(args)} OR source_title ILIKE ${len(args)})")
    if tag:
        args.append(_norm_tags([tag]))
        sql.append(f"AND tags && ${len(args)}::text[]")
    sql.append("ORDER BY created_at DESC")
    args.extend([limit, offset])
    sql.append(f"LIMIT ${len(args) - 1} OFFSET ${len(args)}")
    async with deps.pg_pool.acquire() as conn:
        rows = await conn.fetch(" ".join(sql), *args)
    return {"ok": True, "notes": [_note_row_to_dict(r) for r in rows], "count": len(rows)}


@router.patch("/edu/notes/{note_id}")
async def patch_note(note_id: str, req: NotePatchReq, request: Request,
                     user=Depends(get_current_user_optional)):
    await _ensure_schema()
    kind, actor = _actor(request, user)
    sets, args = [], []
    if req.text is not None:
        args.append(req.text.strip())
        sets.append(f"text=${len(args)}")
    if req.tags is not None:
        args.append(_norm_tags(req.tags))
        sets.append(f"tags=${len(args)}")
    if not sets:
        raise HTTPException(status_code=400, detail="no_fields")
    sets.append("updated_at=NOW()")
    args.extend([note_id, kind, actor])
    async with deps.pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            f"UPDATE edu_notes SET {', '.join(sets)} WHERE id=${len(args)-2} "
            f"AND actor_kind=${len(args)-1} AND actor=${len(args)} RETURNING *",
            *args,
        )
    if not row:
        raise HTTPException(status_code=404, detail="not_found")
    # ADR-0001 Phase 2: mirror full row to Mongo via replace_one(upsert=True).
    # Greenfield collection — pre-Phase-2 PG rows have no Mongo twin yet, so
    # ``upsert=True`` lets the first patch *create* the Mongo doc instead of
    # silently no-op'ing on a missing match.
    _mongo_doc = _note_row_for_mongo(row)
    _nid = note_id
    _kind = kind
    _actor = actor
    await mirror_edu_notes_write(
        "update",
        lambda: deps.db.edu_notes.replace_one(
            {"id": _nid, "actor_kind": _kind, "actor": _actor},
            _mongo_doc,
            upsert=True,
        ),
    )
    return {"ok": True, "note": _note_row_to_dict(row)}


@router.delete("/edu/notes/{note_id}")
async def delete_note(note_id: str, request: Request,
                      user=Depends(get_current_user_optional)):
    await _ensure_schema()
    kind, actor = _actor(request, user)
    async with deps.pg_pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM edu_notes WHERE id=$1 AND actor_kind=$2 AND actor=$3",
            note_id, kind, actor,
        )
    # ADR-0001 Phase 2: mirror delete to Mongo (best-effort; PG is SoT).
    _nid = note_id
    _kind = kind
    _actor = actor
    await mirror_edu_notes_write(
        "delete",
        lambda: deps.db.edu_notes.delete_one(
            {"id": _nid, "actor_kind": _kind, "actor": _actor},
        ),
    )
    return {"ok": True, "deleted": result.endswith(" 1")}


@router.get("/edu/notes/export")
async def export_notes(request: Request, format: str = "md",
                       user=Depends(get_current_user_optional)):
    await _ensure_schema()
    kind, actor = _actor(request, user)
    async with deps.pg_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM edu_notes WHERE actor_kind=$1 AND actor=$2 "
            "ORDER BY created_at DESC",
            kind, actor,
        )
    fmt = (format or "md").lower()
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        # Task #641: include the structured-note + citations columns so AI
        # notes export with full fidelity (downstream tools / spreadsheets
        # can parse the JSON without losing data).
        w.writerow(["created_at", "text", "source_title", "source_url",
                    "chapter_ref", "tags", "generated", "source_kind",
                    "structured_json", "citations_json"])
        for r in rows:
            keys = r.keys()
            generated = bool(r["generated"]) if "generated" in keys else False
            structured_v = r["structured"] if "structured" in keys else None
            if structured_v is not None and not isinstance(structured_v, str):
                structured_v = json.dumps(structured_v)
            citations_v = r["citations"] if "citations" in keys else None
            if citations_v is not None and not isinstance(citations_v, str):
                citations_v = json.dumps(citations_v)
            sk_v = r["source_kind"] if "source_kind" in keys else ""
            w.writerow([
                r["created_at"].isoformat(), r["text"], r["source_title"] or "",
                r["source_url"] or "", r["chapter_ref"] or "",
                ",".join(r["tags"] or []),
                "1" if generated else "0",
                sk_v or "",
                structured_v or "",
                citations_v or "",
            ])
        return StreamingResponse(
            iter([buf.getvalue()]), media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=syrabit-notebook.csv"},
        )
    # Markdown (default)
    lines = ["# Syrabit Notebook",
             f"_Exported {datetime.now(timezone.utc).isoformat()}_", ""]
    for r in rows:
        ttl = r["source_title"] or r["chapter_ref"] or "Note"
        lines.append(f"## {ttl}")
        lines.append(f"_Saved {r['created_at'].isoformat()}_")
        if r["tags"]:
            lines.append("Tags: " + ", ".join(f"`{t}`" for t in r["tags"]))
        if r["source_url"]:
            lines.append(f"Source: <{r['source_url']}>")
        lines.append("")
        # Task #641: render the structured body for AI-generated notes so
        # the export matches the on-screen layout (summary + outline + key
        # terms + Q&A + citation list). Manual notes still get the legacy
        # blockquote rendering.
        structured = None
        if "generated" in r.keys() and r["generated"] and "structured" in r.keys():
            structured = r["structured"]
            if isinstance(structured, str):
                try: structured = json.loads(structured)
                except Exception: structured = None
        if structured:
            if structured.get("summary"):
                lines.append(structured["summary"])
                lines.append("")
            for sec in structured.get("outline") or []:
                lines.append(f"### {sec.get('heading', 'Section')}")
                for p in sec.get("points") or []:
                    lines.append(f"- {p}")
                if sec.get("citations"):
                    lines.append(f"  _Sources: {', '.join(sec['citations'])}_")
                lines.append("")
            kts = structured.get("key_terms") or []
            if kts:
                lines.append("### Key terms")
                for kt in kts:
                    cit = f" _[{', '.join(kt.get('citations') or [])}]_" if kt.get("citations") else ""
                    lines.append(f"- **{kt.get('term', '')}** — {kt.get('definition', '')}{cit}")
                lines.append("")
            qas = structured.get("qa") or []
            if qas:
                lines.append("### Q&A")
                for qa in qas:
                    cit = f" _[{', '.join(qa.get('citations') or [])}]_" if qa.get("citations") else ""
                    lines.append(f"- **Q:** {qa.get('q', '')}")
                    lines.append(f"  **A:** {qa.get('a', '')}{cit}")
                lines.append("")
            cits_raw = r["citations"] if "citations" in r.keys() else None
            if isinstance(cits_raw, str):
                try: cits_raw = json.loads(cits_raw)
                except Exception: cits_raw = None
            if cits_raw:
                lines.append("**Sources**")
                for c in cits_raw:
                    cid = c.get("id", "")
                    lab = c.get("label", "")
                    url = c.get("url", "")
                    if url and url.startswith("/"):
                        url = f"https://syrabit.ai{url}"
                    if url:
                        lines.append(f"- [{cid}] [{lab}]({url})")
                    else:
                        lines.append(f"- [{cid}] {lab}")
                lines.append("")
        else:
            lines.append("> " + (r["text"] or "").replace("\n", "\n> "))
            lines.append("")
    body = "\n".join(lines)
    return StreamingResponse(
        iter([body]), media_type="text/markdown",
        headers={"Content-Disposition": "attachment; filename=syrabit-notebook.md"},
    )


# ───────────────────────── Notes Generation (Task #641) ─────────────────────────
# NotebookLM-style structured notes grounded in the user's own sources
# (a chat conversation, a published chapter, or saved highlights).

_NOTES_GEN_SYS = """You are an expert study-note writer for Indian school and college students,
specialising in Assam Board examinations (SEBA for Class 9–10, AHSEC for
Class 11–12). You receive SOURCE PASSAGES labelled [S1], [S2], … and produce a
visually rich, board-exam-ready study note GROUNDED ONLY in those passages.

━━━ REQUIRED JSON OUTPUT (no prose, no markdown fences) ━━━
{
  "title": "Concise note title (≤ 80 chars)",
  "summary": "2-4 sentence plain-language summary.",
  "outline": [
    {
      "heading": "Section heading",
      "points": ["Bullet 1.", "Bullet 2."],
      "citations": ["S1", "S3"]
    }
  ],
  "key_terms": [
    {"term": "Term", "definition": "Short definition.", "citations": ["S2"]}
  ],
  "qa": [
    {"q": "Likely exam question", "a": "Concise answer.", "citations": ["S1"]}
  ]
}

━━━ OPTIONAL VISUAL FIELDS (omit each field entirely if not applicable) ━━━

"tables": [
  {
    "caption": "Table title (e.g. Journal Entry for Apr 1)",
    "headers": ["Date", "Particulars", "L.F.", "Debit (₹)", "Credit (₹)"],
    "rows": [
      ["Apr 1", "Cash A/c  Dr.", "1", "50,000", ""],
      ["", "  To Capital A/c", "2", "", "50,000"]
    ]
  }
]
Emit tables when the content naturally calls for one:
  • Commerce: journal entries, ledger accounts, trial balance, Trading/P&L,
    Balance Sheet, ratio comparison tables.
  • Science/Geography: element/compound properties, organism classification,
    measurement data, comparative features.
  • Any subject: "Distinguish between X and Y" — 2-column comparison table.
Every row MUST have exactly the same number of cells as headers (pad with "" if empty).
Maximum 6 tables, 10 rows each.

"diagrams": [
  {
    "label": "Diagram title",
    "type": "cell" | "flowchart" | "timeline" | "other",
    "content": "Structured ASCII or labelled-list description (≤ 600 chars)\nUse arrows (→ ↓) and indentation to show structure.\nLabel every named part that appears in the sources."
  }
]
Emit diagrams for: cell/tissue structure, body systems, plant/animal anatomy,
process flowcharts, historical timelines, geographical features. Maximum 4 diagrams.

"mindmap": {
  "root": "Central concept",
  "branches": [
    {"label": "Branch topic", "children": ["Leaf detail 1", "Leaf detail 2"]}
  ]
}
Emit ONLY when the topic has a clear hierarchical/radial structure
(e.g. classification hierarchies, concept maps with multiple sub-categories).
Omit entirely (do NOT emit null or {}) for linear or narrative topics.
Maximum 8 branches, 6 children each.

"mnemonics": [
  {
    "for": "What the mnemonic helps remember",
    "mnemonic": "The phrase or acronym itself",
    "explanation": "How each word/letter maps to the content"
  }
]
Emit ONLY when a strong, logically coherent mnemonic naturally fits — for
sequences, ordered lists, classification steps, kingdom/phylum orders, etc.
Never force a mnemonic. Omit the array entirely if none fits well. Max 4.

━━━ ASSAM BOARD STYLE RULES ━━━
• Q&A language: "Write short notes on…", "Distinguish between…",
  "State and explain…", "Give two differences between…",
  "What do you mean by…", "Mention any two…"
• Match marks patterns: 1-mark = one-line; 2-mark = 2–3 sentences;
  4/5-mark = structured paragraph with examples or numbered points.
• Use Assam-specific examples where supported by sources: Brahmaputra,
  North-East economy, Assam industries, AHSEC/SEBA exam question patterns.

━━━ GROUNDING RULES ━━━
• Use ONLY information present in the SOURCE PASSAGES. Do NOT invent facts,
  definitions, dates, or examples not in the sources.
• outline, key_terms, and qa items MUST cite at least one anchor id.
  tables, diagrams, mindmap, mnemonics do NOT require citations but must be
  grounded in the sources.
• 3–7 outline sections, 4–10 key terms, 4–8 Q&A pairs (fewer if thin sources).
• Bullet points ≤ 200 chars. No first-person. Never mention "Syrabit"."""


def _coerce_notes_payload(raw: str) -> dict:
    """Tolerant JSON parse for the notes generator. Mirrors _coerce_quiz_payload."""
    txt = (raw or "").strip()
    if "```" in txt:
        m = re.search(r"```(?:json)?\s*([\s\S]+?)```", txt)
        if m:
            txt = m.group(1).strip()
    if "{" in txt and "}" in txt:
        txt = txt[txt.index("{"):txt.rindex("}") + 1]
    try:
        return json.loads(txt)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"notes_parse_error: {e}")


def _validate_notes_payload(payload: dict, valid_anchors: set[str]) -> dict:
    """Reject responses missing citations or referencing unknown anchors. Returns
    a normalised payload with clamped string lengths and de-duplicated citations.
    Raises HTTPException(502) on hard violations so the user sees a clear error."""
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="notes_invalid_shape")

    title = str(payload.get("title") or "").strip()[:200]
    summary = str(payload.get("summary") or "").strip()[:1200]
    outline_in = payload.get("outline") or []
    key_terms_in = payload.get("key_terms") or []
    qa_in = payload.get("qa") or []

    def _clean_citations(raw):
        if not isinstance(raw, list):
            return []
        out, seen = [], set()
        for c in raw:
            cid = str(c).strip().upper()
            if not cid or cid in seen:
                continue
            if cid not in valid_anchors:
                continue
            seen.add(cid)
            out.append(cid)
        return out

    outline = []
    for sec in outline_in[:8]:
        if not isinstance(sec, dict):
            continue
        cits = _clean_citations(sec.get("citations"))
        if not cits:
            continue
        pts = []
        for p in (sec.get("points") or [])[:10]:
            t = str(p).strip()[:300]
            if t:
                pts.append(t)
        if not pts:
            continue
        outline.append({
            "heading": str(sec.get("heading") or "").strip()[:160] or "Section",
            "points": pts,
            "citations": cits,
        })

    key_terms = []
    for kt in key_terms_in[:14]:
        if not isinstance(kt, dict):
            continue
        cits = _clean_citations(kt.get("citations"))
        term = str(kt.get("term") or "").strip()[:120]
        defn = str(kt.get("definition") or "").strip()[:400]
        if not (term and defn and cits):
            continue
        key_terms.append({"term": term, "definition": defn, "citations": cits})

    qa = []
    for item in qa_in[:10]:
        if not isinstance(item, dict):
            continue
        cits = _clean_citations(item.get("citations"))
        q = str(item.get("q") or "").strip()[:400]
        a = str(item.get("a") or "").strip()[:800]
        if not (q and a and cits):
            continue
        qa.append({"q": q, "a": a, "citations": cits})

    # ── Optional: tables ─────────────────────────────────────────────────────
    tables: list = []
    for tbl in (payload.get("tables") or [])[:6]:
        if not isinstance(tbl, dict):
            continue
        caption = str(tbl.get("caption") or "").strip()[:200]
        headers = [str(h).strip()[:100] for h in (tbl.get("headers") or [])
                   if str(h).strip()]
        if len(headers) < 2:
            continue
        n_cols = len(headers)
        rows: list = []
        for row in (tbl.get("rows") or [])[:10]:
            if not isinstance(row, list):
                continue
            cells = [str(c).strip()[:200] for c in row]
            # Pad short rows; truncate overlong ones
            while len(cells) < n_cols:
                cells.append("")
            rows.append(cells[:n_cols])
        if rows:
            tables.append({"caption": caption, "headers": headers, "rows": rows})

    # ── Optional: diagrams ───────────────────────────────────────────────────
    diagrams: list = []
    _valid_diagram_types = {"cell", "flowchart", "timeline", "other"}
    for diag in (payload.get("diagrams") or [])[:4]:
        if not isinstance(diag, dict):
            continue
        label = str(diag.get("label") or "").strip()[:160]
        dtype = str(diag.get("type") or "other").strip().lower()
        if dtype not in _valid_diagram_types:
            dtype = "other"
        content = str(diag.get("content") or "").strip()[:800]
        if label and content:
            diagrams.append({"label": label, "type": dtype, "content": content})

    # ── Optional: mindmap ────────────────────────────────────────────────────
    mindmap: dict | None = None
    mm_raw = payload.get("mindmap")
    if isinstance(mm_raw, dict) and mm_raw:
        mm_root = str(mm_raw.get("root") or "").strip()[:120]
        branches: list = []
        for br in (mm_raw.get("branches") or [])[:8]:
            if not isinstance(br, dict):
                continue
            blabel = str(br.get("label") or "").strip()[:120]
            children = [str(c).strip()[:120] for c in (br.get("children") or [])
                        if str(c).strip()][:6]
            if blabel:
                branches.append({"label": blabel, "children": children})
        if mm_root and branches:
            mindmap = {"root": mm_root, "branches": branches}

    # ── Optional: mnemonics ──────────────────────────────────────────────────
    mnemonics: list = []
    for mn in (payload.get("mnemonics") or [])[:4]:
        if not isinstance(mn, dict):
            continue
        for_text = str(mn.get("for") or "").strip()[:200]
        mnemonic_str = str(mn.get("mnemonic") or "").strip()[:300]
        explanation = str(mn.get("explanation") or "").strip()[:500]
        if for_text and mnemonic_str:
            mnemonics.append({
                "for": for_text,
                "mnemonic": mnemonic_str,
                "explanation": explanation,
            })

    # ── Hard requirements ────────────────────────────────────────────────────
    if not title:
        raise HTTPException(status_code=502, detail="notes_missing_title")
    if not summary:
        raise HTTPException(status_code=502, detail="notes_missing_summary")
    if not outline and not qa:
        # No grounded body at all — almost always means the model ignored
        # citations or hallucinated anchors. Better to fail loudly than save
        # an empty note.
        raise HTTPException(status_code=502, detail="notes_no_cited_content")

    result: dict = {
        "title": title,
        "summary": summary,
        "outline": outline,
        "key_terms": key_terms,
        "qa": qa,
    }
    # Only include optional fields in the payload when they have content so
    # the frontend can safely branch on truthiness.
    if tables:
        result["tables"] = tables
    if diagrams:
        result["diagrams"] = diagrams
    if mindmap:
        result["mindmap"] = mindmap
    if mnemonics:
        result["mnemonics"] = mnemonics
    return result


# ───── Source assemblers ─────

# Cap on raw source characters fed to the LLM. Gemini Flash handles ≫ 12k but
# longer payloads cost more and rarely improve note quality on focused topics.
_NOTES_SOURCE_CHAR_CAP = 14000
_NOTES_PER_ANCHOR_CHAR_CAP = 2400


def _truncate(text: str, n: int) -> str:
    s = (text or "").strip()
    return s if len(s) <= n else s[:n].rstrip() + "…"


def _safe_citation_url(url: str) -> str:
    """Defense-in-depth: only persist citation URLs that are app-internal
    (start with single '/') or http(s). Strips javascript:/data:/vbscript:
    schemes that could fire on click in the rendered note."""
    s = (url or "").strip()
    if not s:
        return ""
    if s.startswith("/") and not s.startswith("//"):
        return s
    if re.match(r"^https?://", s, re.IGNORECASE):
        return s
    return ""


def _slugify_heading(s: str) -> str:
    """Stable URL-fragment slug for a chapter section heading. Used to
    build deep-link anchors like /…/chapter#sec-photosynthesis-equation."""
    s = (s or "").lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return s[:80] or "section"


def _split_chapter_sections(content: str, max_sections: int = 8) -> list[tuple[str, str]]:
    """Split chapter markdown into (heading, body) tuples by ## headings.
    Falls back to a single section if no headings are present."""
    if not content:
        return []
    parts: list[tuple[str, str]] = []
    cur_head = "Overview"
    cur_buf: list[str] = []
    for line in content.splitlines():
        m = re.match(r"^\s{0,3}#{1,3}\s+(.+?)\s*$", line)
        if m:
            if cur_buf:
                parts.append((cur_head, "\n".join(cur_buf).strip()))
                cur_buf = []
            cur_head = m.group(1).strip()[:160]
            continue
        cur_buf.append(line)
    if cur_buf:
        parts.append((cur_head, "\n".join(cur_buf).strip()))
    parts = [(h, b) for h, b in parts if b and len(b) > 40]
    return parts[:max_sections] or [("Overview", content.strip())]


async def _build_chapter_url(chapter: dict) -> str:
    """Best-effort full URL like /:board/:class/:stream?/:subject/:chapter.
    Returns "" if any hop is missing — frontend will then render a label-only
    citation chip."""
    try:
        ch_slug = chapter.get("slug") or ""
        subj_id = chapter.get("subject_id") or ""
        if not (ch_slug and subj_id):
            return ""
        subj = await deps.db.subjects.find_one(
            {"id": subj_id},
            {"_id": 0, "slug": 1, "stream_id": 1, "class_id": 1, "board_id": 1},
        )
        if not subj:
            return ""
        subj_slug = subj.get("slug") or ""
        cls_id = subj.get("class_id") or ""
        board_id = subj.get("board_id") or ""
        stream_id = subj.get("stream_id") or ""
        if stream_id and not (cls_id and board_id):
            stream = await deps.db.streams.find_one({"id": stream_id}, {"_id": 0, "class_id": 1})
            if stream:
                cls_id = cls_id or stream.get("class_id") or ""
        if cls_id and not board_id:
            cls = await deps.db.classes.find_one({"id": cls_id}, {"_id": 0, "board_id": 1})
            if cls:
                board_id = cls.get("board_id") or ""
        if not (cls_id and board_id and subj_slug):
            return ""
        cls = await deps.db.classes.find_one({"id": cls_id}, {"_id": 0, "slug": 1})
        board = await deps.db.boards.find_one({"id": board_id}, {"_id": 0, "slug": 1})
        if not (cls and board):
            return ""
        cls_slug = cls.get("slug") or ""
        board_slug = board.get("slug") or ""
        if not (cls_slug and board_slug):
            return ""
        if stream_id:
            stream = await deps.db.streams.find_one({"id": stream_id}, {"_id": 0, "slug": 1})
            stream_slug = (stream or {}).get("slug") or ""
            if stream_slug:
                return f"/{board_slug}/{cls_slug}/{stream_slug}/{subj_slug}/{ch_slug}"
        return f"/{board_slug}/{cls_slug}/{subj_slug}/{ch_slug}"
    except Exception as e:
        logger.warning(f"[notes-gen] _build_chapter_url failed: {e}")
        return ""


async def _assemble_from_conversation(conv_id: str, user_id: Optional[str]) -> tuple[list, str]:
    """Pull a conversation's user/assistant turns AND any RAG chunks attached
    to assistant turns. Each anchor carries:
      - a stable per-message URL fragment (#m{index})
      - origin metadata (message_index, role, kind) so future deep-link
        navigation knows exactly which message/chunk to scroll to."""
    if not user_id:
        raise HTTPException(status_code=401, detail="signin_required_for_conversation_source")
    conv = await supa_get_conversation(conv_id, user_id)
    if not conv:
        raise HTTPException(status_code=404, detail="conversation_not_found")
    msgs = conv.get("messages") or []
    if isinstance(msgs, str):
        try: msgs = json.loads(msgs)
        except Exception: msgs = []
    title = (conv.get("title") or "Chat conversation").strip()[:120]
    anchors: list[dict] = []
    total = 0
    base_url = f"/chat?id={conv_id}"
    for i, m in enumerate(msgs):
        role = (m.get("role") or "").lower()
        if role not in ("user", "assistant"):
            continue
        content = (m.get("content") or "").strip()
        if not content:
            continue
        if total >= _NOTES_SOURCE_CHAR_CAP:
            break
        body = _truncate(content, _NOTES_PER_ANCHOR_CHAR_CAP)
        total += len(body)
        label = ("Question" if role == "user" else "AI answer") + f" #{(i // 2) + 1}"
        anchors.append({
            "id": f"S{len(anchors) + 1}",
            "kind": "message",
            "label": label,
            "url": _safe_citation_url(f"{base_url}#m{i}"),
            "text": body,
            "role": role,
            "ref": {"conv_id": conv_id, "message_index": i},
        })
        # Pull RAG passages that grounded this assistant turn so the model
        # can cite original source material — not just our prior answer.
        if role == "assistant" and total < _NOTES_SOURCE_CHAR_CAP:
            snippets: list[str] = []
            snip = (m.get("rag_chunk_snippet") or "").strip()
            if snip:
                snippets.append(snip)
            srcs = m.get("sources") or []
            if isinstance(srcs, list):
                for s in srcs[:6]:
                    if isinstance(s, dict):
                        t = (s.get("snippet") or s.get("text") or s.get("content") or "").strip()
                        if t:
                            snippets.append(t)
                    elif isinstance(s, str) and s.strip():
                        snippets.append(s.strip())
            for k, sn in enumerate(snippets[:4]):
                if total >= _NOTES_SOURCE_CHAR_CAP:
                    break
                clip = _truncate(sn, _NOTES_PER_ANCHOR_CHAR_CAP)
                total += len(clip)
                src_obj = srcs[k] if isinstance(srcs, list) and k < len(srcs) and isinstance(srcs[k], dict) else {}
                src_url = _safe_citation_url(src_obj.get("url") or f"{base_url}#m{i}")
                src_label = (src_obj.get("title") or src_obj.get("source") or
                             f"Source for AI answer #{(i // 2) + 1}").strip()[:160]
                anchors.append({
                    "id": f"S{len(anchors) + 1}",
                    "kind": "rag_chunk",
                    "label": src_label,
                    "url": src_url,
                    "text": clip,
                    "ref": {"conv_id": conv_id, "message_index": i, "chunk_index": k},
                })
        if len(anchors) >= 28:
            break
    if not anchors:
        raise HTTPException(status_code=400, detail="conversation_empty")
    return anchors, title


async def _assemble_from_chapter(chapter_id: str) -> tuple[list, str]:
    if not deps.db:
        raise HTTPException(status_code=503, detail="storage_unavailable")
    ch = await deps.db.chapters.find_one(
        {"id": chapter_id},
        {"_id": 0, "id": 1, "title": 1, "content": 1, "slug": 1,
         "subject_id": 1, "description": 1},
    )
    if not ch:
        raise HTTPException(status_code=404, detail="chapter_not_found")
    chapter_url = await _build_chapter_url(ch)
    sections = _split_chapter_sections(ch.get("content") or ch.get("description") or "")
    if not sections:
        raise HTTPException(status_code=400, detail="chapter_empty")
    anchors: list[dict] = []
    total = 0
    for sec_idx, (heading, body) in enumerate(sections):
        if total >= _NOTES_SOURCE_CHAR_CAP:
            break
        clip = _truncate(body, _NOTES_PER_ANCHOR_CHAR_CAP)
        total += len(clip)
        # Stable per-section anchor: …/chapter#sec-photosynthesis
        sec_slug = _slugify_heading(heading)
        sec_url = _safe_citation_url(f"{chapter_url}#sec-{sec_slug}") if chapter_url else ""
        anchors.append({
            "id": f"S{len(anchors) + 1}",
            "kind": "chapter_section",
            "label": f"{ch.get('title', 'Chapter')} — {heading}",
            "url": sec_url,
            "text": clip,
            "ref": {
                "chapter_id": chapter_id,
                "section_index": sec_idx,
                "section_slug": sec_slug,
            },
        })
    return anchors, str(ch.get("title") or "Chapter").strip()[:120]


async def _assemble_from_subject(subject_id: str) -> tuple[list, str]:
    """Pull top sections from each chapter of a subject. Useful for "make
    me notes for the whole Photosynthesis subject"-style requests."""
    if not deps.db:
        raise HTTPException(status_code=503, detail="storage_unavailable")
    subj = await deps.db.subjects.find_one({"id": subject_id}, {"_id": 0, "name": 1})
    if not subj:
        raise HTTPException(status_code=404, detail="subject_not_found")
    chapters_cur = deps.db.chapters.find(
        {"subject_id": subject_id},
        {"_id": 0, "id": 1, "title": 1, "content": 1, "slug": 1,
         "subject_id": 1, "description": 1, "order": 1},
    ).sort("order", 1)
    chapters = await chapters_cur.to_list(length=20)
    if not chapters:
        raise HTTPException(status_code=400, detail="subject_has_no_chapters")
    anchors: list[dict] = []
    total = 0
    for ch in chapters:
        chapter_url = await _build_chapter_url(ch)
        sections = _split_chapter_sections(ch.get("content") or ch.get("description") or "",
                                           max_sections=3)
        for sec_idx, (heading, body) in enumerate(sections):
            if total >= _NOTES_SOURCE_CHAR_CAP:
                break
            clip = _truncate(body, _NOTES_PER_ANCHOR_CHAR_CAP)
            total += len(clip)
            sec_slug = _slugify_heading(heading)
            sec_url = _safe_citation_url(f"{chapter_url}#sec-{sec_slug}") if chapter_url else ""
            anchors.append({
                "id": f"S{len(anchors) + 1}",
                "kind": "chapter_section",
                "label": f"{ch.get('title', 'Chapter')} — {heading}",
                "url": sec_url,
                "text": clip,
                "ref": {
                    "subject_id": subject_id,
                    "chapter_id": ch.get("id"),
                    "section_index": sec_idx,
                    "section_slug": sec_slug,
                },
            })
        if total >= _NOTES_SOURCE_CHAR_CAP:
            break
    if not anchors:
        raise HTTPException(status_code=400, detail="subject_chapters_empty")
    return anchors, str(subj.get("name") or "Subject").strip()[:120]


async def _assemble_from_highlights(note_ids: list[str], kind: str, actor: str) -> tuple[list, str]:
    if not note_ids:
        raise HTTPException(status_code=400, detail="no_note_ids")
    note_ids = [str(nid)[:80] for nid in note_ids[:30] if nid]
    async with deps.pg_pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, text, source_url, source_title, chapter_ref
               FROM edu_notes
               WHERE actor_kind=$1 AND actor=$2 AND id = ANY($3::text[])
               ORDER BY created_at ASC""",
            kind, actor, note_ids,
        )
    if not rows:
        raise HTTPException(status_code=404, detail="highlights_not_found")
    anchors: list[dict] = []
    total = 0
    for r in rows:
        body = (r["text"] or "").strip()
        if not body:
            continue
        if total >= _NOTES_SOURCE_CHAR_CAP:
            break
        clip = _truncate(body, _NOTES_PER_ANCHOR_CHAR_CAP)
        total += len(clip)
        label = (r["source_title"] or r["chapter_ref"] or "Highlight").strip()[:160] or "Highlight"
        anchors.append({
            "id": f"S{len(anchors) + 1}",
            "kind": "highlight",
            "label": label,
            "url": _safe_citation_url(r["source_url"] or ""),
            "text": clip,
        })
    if not anchors:
        raise HTTPException(status_code=400, detail="highlights_empty")
    return anchors, "Saved highlights"


# ───── Gemini-only caller (no silent fallback) ─────

async def _call_gemini_strict(messages: list, max_tokens: int = 2200) -> str:
    """Call Gemini ONLY (Vertex first, then API key fallback). Raises
    HTTPException(502/503) on failure so the user gets a clear error rather
    than a silent fallback to Cerebras/Groq."""
    errors: list[str] = []

    # 1) Vertex Gemini Flash (preferred for BYOK / billed paths).
    if _vchat.is_configured():
        try:
            buf: list[str] = []
            async for tok in _vchat.stream_chat(messages, max_tokens=max_tokens, temperature=0.2):
                buf.append(tok)
                if sum(len(b) for b in buf) > 30000:
                    break
            txt = "".join(buf).strip()
            if txt:
                return txt
            errors.append("vertex_empty_response")
        except Exception as e:
            errors.append(f"vertex:{type(e).__name__}:{str(e)[:140]}")
            logger.warning(f"[notes-gen] Vertex Gemini failed: {e}")

    # 2) Vertex non-streaming fallback (separate from the streaming primary
    # above): tries the same Vertex SA path via :generateContent in case the
    # stream returned empty / failed mid-stream. The legacy direct GEMINI_API_KEY
    # loop was removed in the 2026-05-03 vertex-only Gemini migration.
    try:
        txt = await _call_vertex_chat(messages, "gemini-2.5-flash", max_tokens)
        if (txt or "").strip():
            return txt
        errors.append("vertex_nonstream_empty_response")
    except Exception as e:
        errors.append(f"vertex_nonstream:{type(e).__name__}:{str(e)[:140]}")
        logger.warning(f"[notes-gen] Vertex non-stream fallback failed: {e}")

    if not errors:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "gemini_unavailable",
                "message": "Notes generation requires Google Gemini, which is not configured on this server.",
            },
        )
    raise HTTPException(
        status_code=502,
        detail={
            "error": "gemini_failed",
            "message": "Google Gemini is currently unavailable. Please try again in a few minutes.",
            "attempts": errors[-3:],
        },
    )


# ───── Generation route ─────

class NotesGenReq(BaseModel):
    source_kind: str = Field(..., description="conversation | chapter | subject | highlights")
    source_id: str = Field("", max_length=120, description="conv_id, chapter_id, or subject_id")
    note_ids: List[str] = Field(default_factory=list, description="for source_kind='highlights'")
    response_lang: str = Field("en", max_length=8)
    custom_focus: str = Field("", max_length=300, description="optional topical focus hint")


NOTES_GEN_DAILY_CAP = 20         # per-actor LLM call budget per UTC day
NOTES_GEN_BURST_CAP = 6          # per-actor 5-min burst
NOTES_GEN_DAY_WINDOW = 86400
NOTES_GEN_CREDIT_COST = 2        # AI notes are heavier than a chat turn


def _notes_gen_daily_key(kind: str, actor: str) -> str:
    return f"edu_notes_gen_day:{kind}:{actor}"


@router.post("/edu/notes/generate")
async def generate_notes(req: NotesGenReq, request: Request,
                         user=Depends(get_current_user)):
    """NotebookLM-style grounded notes generator. Calls Gemini with the user's
    own sources (conversation / chapter / subject / highlights), validates
    citations, and persists the result through the existing notes table.

    Costs `NOTES_GEN_CREDIT_COST` credits from the user's daily allowance.
    Sign-in is required so credits and ownership can be enforced."""
    await _ensure_schema()
    kind, actor = _actor(request, user)

    # Burst limit (per 5 min) — protects from accidental double-clicks.
    if not check_rate_limit(f"edu_notes_gen:{actor}",
                            max_requests=NOTES_GEN_BURST_CAP,
                            window_seconds=300):
        raise HTTPException(status_code=429, detail="Too many generations; try again in a few minutes.")
    # Per-UTC-day cap (cost control on top of credits).
    if not check_rate_limit(_notes_gen_daily_key(kind, actor),
                            max_requests=NOTES_GEN_DAILY_CAP,
                            window_seconds=NOTES_GEN_DAY_WINDOW):
        raise HTTPException(
            status_code=429,
            detail={
                "error": "notes_gen_daily_cap",
                "limit": NOTES_GEN_DAILY_CAP,
                "scope": "day",
                "resets_at": DAILY_RESET_LABEL,
                "message": (
                    f"Daily AI-notes limit reached ({NOTES_GEN_DAILY_CAP}/day). "
                    f"Try again after {DAILY_RESET_LABEL}."
                ),
            },
            headers={"Retry-After": "3600",
                     "X-RateLimit-Limit": str(NOTES_GEN_DAILY_CAP),
                     "X-RateLimit-Scope": "day"},
        )

    # Credit pre-check (mirrors /ai/chat/stream): refuse if remaining < cost.
    credits = await get_user_credits(user)
    remaining = int(credits.get("remaining") or 0)
    if remaining < NOTES_GEN_CREDIT_COST:
        raise HTTPException(
            status_code=402,
            detail={
                "error": "insufficient_credits",
                "cost": NOTES_GEN_CREDIT_COST,
                "remaining": remaining,
                "message": (
                    f"This action costs {NOTES_GEN_CREDIT_COST} credits but you have "
                    f"{remaining} remaining today. Resets at {DAILY_RESET_LABEL}."
                ),
            },
        )

    sk = (req.source_kind or "").strip().lower()
    if sk == "conversation":
        if not req.source_id:
            raise HTTPException(status_code=400, detail="source_id_required")
        anchors, source_title = await _assemble_from_conversation(
            req.source_id, user["id"] if user else None,
        )
        source_ref = req.source_id
    elif sk == "chapter":
        if not req.source_id:
            raise HTTPException(status_code=400, detail="source_id_required")
        anchors, source_title = await _assemble_from_chapter(req.source_id)
        source_ref = req.source_id
    elif sk == "subject":
        if not req.source_id:
            raise HTTPException(status_code=400, detail="source_id_required")
        anchors, source_title = await _assemble_from_subject(req.source_id)
        source_ref = req.source_id
    elif sk == "highlights":
        anchors, source_title = await _assemble_from_highlights(req.note_ids, kind, actor)
        source_ref = ",".join((req.note_ids or [])[:30])
    else:
        raise HTTPException(status_code=400, detail="invalid_source_kind")

    # Atomic credit deduction *after* sources resolve but *before* the LLM
    # call. Mirrors the chat stream pattern: deduct first, refund on a
    # downstream failure so users aren't charged for a failed generation.
    used_now = int(credits.get("used") or 0)
    limit_now = int(credits.get("limit") or 0)
    deducted = 0
    for _ in range(NOTES_GEN_CREDIT_COST):
        ok = await atomic_deduct_credit(user["id"], used_now, limit_now)
        if not ok:
            break
        used_now += 1
        deducted += 1
    if deducted < NOTES_GEN_CREDIT_COST:
        # Race lost — refund anything we managed to take.
        if deducted > 0:
            try:
                async with deps.pg_pool.acquire() as conn:
                    await conn.execute(
                        """UPDATE users SET credits_used_today = GREATEST(0, credits_used_today - $1),
                                            credits_used = GREATEST(0, credits_used - $1)
                           WHERE id=$2""",
                        deducted, user["id"],
                    )
                # ADR-0001 Phase 2: mirror refund to Mongo (best-effort).
                # Pipeline-update floor matches PG GREATEST(0, ...) so
                # a duplicate refund cannot drive Mongo counters negative.
                from db_dualwrite import (
                    mirror_user_write as _muw,
                    clamped_decrement_pipeline as _clamp,
                )
                _refund_n = deducted
                _uid = user["id"]
                await _muw(
                    "refund_credit_partial",
                    lambda: deps.db.users.update_one(
                        {"id": _uid},
                        _clamp({"credits_used_today": _refund_n,
                                "credits_used": _refund_n}),
                    ),
                )
            except Exception as e:
                logger.warning(f"[notes-gen] credit refund failed: {e}")
        raise HTTPException(
            status_code=402,
            detail={"error": "insufficient_credits",
                    "message": "Daily credit limit reached during generation."},
        )

    async def _refund_credits():
        """Best-effort refund used when generation fails after deduction."""
        try:
            async with deps.pg_pool.acquire() as conn:
                await conn.execute(
                    """UPDATE users SET credits_used_today = GREATEST(0, credits_used_today - $1),
                                        credits_used = GREATEST(0, credits_used - $1)
                       WHERE id=$2""",
                    NOTES_GEN_CREDIT_COST, user["id"],
                )
            # ADR-0001 Phase 2: mirror refund to Mongo (best-effort).
            # Pipeline-update floor matches PG GREATEST(0, ...) so
            # a duplicate refund cannot drive Mongo counters negative.
            from db_dualwrite import (
                mirror_user_write as _muw,
                clamped_decrement_pipeline as _clamp,
            )
            _uid = user["id"]
            _n = NOTES_GEN_CREDIT_COST
            await _muw(
                "refund_credit_full",
                lambda: deps.db.users.update_one(
                    {"id": _uid},
                    _clamp({"credits_used_today": _n, "credits_used": _n}),
                ),
            )
        except Exception as e:
            logger.warning(f"[notes-gen] credit refund failed: {e}")

    valid_anchor_ids = {a["id"] for a in anchors}

    # From this point on, ANY non-success exit must refund credits. Track
    # success and run `_refund_credits()` in a `finally` block to guarantee
    # coverage across LLM errors, safety blocks, notebook_full, DB errors,
    # and unexpected exceptions alike.
    success = False
    try:
        # Build the user message with each anchor labelled and bounded.
        parts: list[str] = []
        if req.custom_focus:
            parts.append(f"Topical focus: {req.custom_focus.strip()[:300]}")
        if req.response_lang and req.response_lang.lower().startswith("as"):
            parts.append("Write the title, summary, headings, definitions, and Q&A in Assamese (as-IN).")
        parts.append(f"Source set: {source_title}")
        parts.append("")
        parts.append("--- SOURCE PASSAGES ---")
        for a in anchors:
            parts.append(f"[{a['id']}] {a['label']}")
            parts.append(a["text"])
            parts.append("")
        parts.append("--- END SOURCES ---")
        parts.append("")
        parts.append("Now produce the JSON note as instructed. Cite anchors only from the list above.")

        messages = [
            {"role": "system", "content": _NOTES_GEN_SYS},
            {"role": "user",   "content": "\n".join(parts)},
        ]

        raw = await _call_gemini_strict(messages, max_tokens=4096)
        payload = _coerce_notes_payload(raw)
        structured = _validate_notes_payload(payload, valid_anchor_ids)

        # Light safety pass on the flattened generated text.
        flat_parts = [structured["title"], structured["summary"]]
        for sec in structured["outline"]:
            flat_parts.append(sec["heading"])
            flat_parts.extend(sec["points"])
        for kt in structured["key_terms"]:
            flat_parts.append(kt["term"]); flat_parts.append(kt["definition"])
        for q in structured["qa"]:
            flat_parts.append(q["q"]); flat_parts.append(q["a"])
        for mn in structured.get("mnemonics") or []:
            flat_parts.append(mn.get("mnemonic") or "")
            flat_parts.append(mn.get("explanation") or "")
        ok, _why = validate_llm_output(" ".join(flat_parts))
        if not ok:
            raise HTTPException(status_code=502, detail="notes_safety_block")

        # Build a plain-text representation so the existing list/search/text
        # pipeline keeps working unchanged for generated notes.
        plain_lines = [structured["title"], "", structured["summary"], ""]
        for sec in structured["outline"]:
            plain_lines.append(sec["heading"])
            for p in sec["points"]:
                plain_lines.append(f"• {p}")
            plain_lines.append("")
        if structured["key_terms"]:
            plain_lines.append("Key terms")
            for kt in structured["key_terms"]:
                plain_lines.append(f"• {kt['term']}: {kt['definition']}")
            plain_lines.append("")
        if structured["qa"]:
            plain_lines.append("Q&A")
            for qa in structured["qa"]:
                plain_lines.append(f"Q: {qa['q']}")
                plain_lines.append(f"A: {qa['a']}")
            plain_lines.append("")
        for mn in structured.get("mnemonics") or []:
            plain_lines.append(f"Mnemonic ({mn.get('for', '')}): {mn.get('mnemonic', '')}")
        for diag in structured.get("diagrams") or []:
            plain_lines.append(f"Diagram: {diag.get('label', '')}")
        plain_text = "\n".join(plain_lines).strip()

        # Citations table: id → {label, url, kind} so the frontend can render
        # clickable chips without re-querying the source.
        citations_out = [
            {"id": a["id"], "kind": a["kind"], "label": a["label"], "url": a["url"]}
            for a in anchors
        ]

        nid = str(uuid.uuid4())
        auto_tag = f"ai-notes" if sk != "conversation" else "ai-notes"
        sk_tag = f"src-{sk}"
        tags = _norm_tags([auto_tag, sk_tag])
        chapter_ref = source_title if sk in ("chapter", "highlights") else ""
        src_url = ""
        src_title = structured["title"]
        if sk == "conversation":
            src_url = f"/chat?id={req.source_id}"
            src_title = f"Notes from chat: {source_title}"
        elif sk == "chapter":
            # Use the first anchor's url (the chapter URL) when we successfully
            # built it; falling back to empty keeps the existing renderer happy.
            src_url = anchors[0].get("url") or ""

        async with deps.pg_pool.acquire() as conn:
            cnt = await conn.fetchval(
                "SELECT COUNT(*) FROM edu_notes WHERE actor_kind=$1 AND actor=$2",
                kind, actor,
            )
            if cnt is not None and cnt >= 2000:
                raise HTTPException(status_code=400, detail="notebook_full")
            row = await conn.fetchrow(
                """INSERT INTO edu_notes (
                      id, actor_kind, actor, text, source_url, source_title,
                      chapter_ref, tags, generated, structured, citations,
                      source_kind, source_ref)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,TRUE,$9::jsonb,$10::jsonb,$11,$12)
                   RETURNING *""",
                nid, kind, actor, plain_text, src_url, src_title,
                chapter_ref, tags,
                json.dumps(structured), json.dumps(citations_out),
                sk, source_ref[:300],
            )
        # ADR-0001 Phase 2: mirror autogen note to Mongo (best-effort).
        _mongo_doc = _note_row_for_mongo(row)
        await mirror_edu_notes_write(
            "insert_autogen",
            lambda: deps.db.edu_notes.insert_one(_mongo_doc),
        )
        success = True
        return {"ok": True, "note": _note_row_to_dict(row)}
    finally:
        if not success:
            await _refund_credits()


# ───────────────────────── Flashcards (SM-2 lite) ─────────────────────────

class CardBuildReq(BaseModel):
    note_ids: Optional[List[str]] = None  # None → build for ALL un-carded notes


def _flashcard_row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "note_id": row["note_id"] or "",
        "front": row["front"],
        "back": row["back"],
        "ef": float(row["ef"]),
        "interval_days": int(row["interval_days"]),
        "repetitions": int(row["repetitions"]),
        "due_at": row["due_at"].isoformat(),
        "last_reviewed": row["last_reviewed"].isoformat() if row["last_reviewed"] else None,
        "claimed_at": row["claimed_at"].isoformat() if row["claimed_at"] else None,
    }


def _flashcard_doc_for_mongo(
    *, card_id: str, kind: str, actor: str, note_id: str,
    front: str, back: str, now_utc: datetime,
) -> dict:
    """Construct a Mongo doc for an edu_flashcards INSERT.

    ADR-0001 §50 — greenfield collection. Composite key is
    ``{actor_kind, actor, id}``; ``id`` is kept as a plain field (not
    Mongo's reserved ``_id``) to match the users / conversations /
    edu_notes rollout pattern.

    The PG INSERT uses ``DEFAULT 2.5`` / ``0`` / ``NOW()`` for the SM-2
    state columns; we replicate those defaults client-side rather than
    round-tripping a ``RETURNING *`` because:
      1. The build endpoint fans out up to ~2.4 k inserts per request
         and ``execute`` (no return) is materially faster than
         ``fetchrow``.
      2. Sub-millisecond clock skew between PG ``NOW()`` and
         ``datetime.now(timezone.utc)`` is irrelevant for SR scheduling
         (intervals are measured in days).
      3. Phase 3 read-shadow will detect any drift > 0.1 % anyway.
    """
    return {
        "id": card_id,
        "actor_kind": kind,
        "actor": actor,
        "note_id": note_id,
        "front": front,
        "back": back,
        "ef": 2.5,
        "interval_days": 0,
        "repetitions": 0,
        "due_at": now_utc,
        "last_reviewed": None,
        "claimed_at": None,
        "created_at": now_utc,
    }


def _flashcard_row_for_mongo(row) -> dict:
    """Convert an asyncpg edu_flashcards Record (post-review UPDATE
    with ``RETURNING *``) into a Mongo doc.

    All edu_flashcards columns are scalar types — no JSONB normalisation
    needed. We still defensively copy timestamps as native datetimes
    (asyncpg already does this) so future Phase-3 read-shadow diffs
    aren't tripped by string-vs-datetime noise.
    """
    return {
        "id": row["id"],
        "actor_kind": row["actor_kind"],
        "actor": row["actor"],
        "note_id": row["note_id"],
        "front": row["front"],
        "back": row["back"],
        "ef": float(row["ef"]),
        "interval_days": int(row["interval_days"]),
        "repetitions": int(row["repetitions"]),
        "due_at": row["due_at"],
        "last_reviewed": row["last_reviewed"],
        "claimed_at": row["claimed_at"] if "claimed_at" in row.keys() else None,
        "created_at": row["created_at"],
    }


def _split_front_back(text: str) -> tuple[str, str]:
    """Heuristic: turn a note into (front, back). Definition lines like
    `X — Y` or `X: Y` give a Q/A split; otherwise we make a cloze-style
    "What is the key idea?" front."""
    s = (text or "").strip()
    m = re.match(r"^(.{3,80}?)[\s]*[—:\-–][\s]+(.{2,400})$", s)
    if m:
        return m.group(1).strip()[:200], m.group(2).strip()[:400]
    # Sentence-based fallback: first sentence becomes back, prompt fixed.
    first = re.split(r"(?<=[.!?])\s+", s)[0]
    return ("Recall this idea:", first[:400] or s[:400])


@router.post("/edu/flashcards/build")
async def build_flashcards(req: CardBuildReq, request: Request,
                           user=Depends(get_current_user_optional)):
    await _ensure_schema()
    kind, actor = _actor(request, user)
    # ADR-0001 Phase 2: collect Mongo docs for every PG INSERT and bulk-
    # mirror them in one ``insert_many`` after the PG block exits. This
    # avoids a serial mirror round-trip per card (build can fan-out
    # ≤2.4 k cards per request).
    _mongo_docs: list[dict] = []
    _now_utc = datetime.now(timezone.utc)
    async with deps.pg_pool.acquire() as conn:
        if req.note_ids:
            rows = await conn.fetch(
                "SELECT * FROM edu_notes WHERE actor_kind=$1 AND actor=$2 "
                "AND id = ANY($3::text[])",
                kind, actor, req.note_ids,
            )
        else:
            rows = await conn.fetch(
                "SELECT n.* FROM edu_notes n LEFT JOIN edu_flashcards f "
                "ON f.note_id = n.id WHERE n.actor_kind=$1 AND n.actor=$2 "
                "AND f.id IS NULL ORDER BY n.created_at DESC LIMIT 200",
                kind, actor,
            )
        created = 0
        for n in rows:
            is_generated = bool(n["generated"]) if "generated" in n.keys() else False
            note_id = n["id"]

            if is_generated:
                # AI-generated notes: emit one card per Q&A pair + one per mnemonic.
                # The flat n["text"] is a concatenation of the structured fields used
                # for full-text search — it is not suitable for a single flashcard.
                structured_raw = n["structured"] if "structured" in n.keys() else None
                if isinstance(structured_raw, str):
                    try:
                        structured_raw = json.loads(structured_raw)
                    except Exception as exc:
                        logger.warning(
                            f"[edu_study] note {note_id!r} has corrupted structured JSON"
                            f" — flashcards skipped ({type(exc).__name__}: {exc})"
                        )
                        structured_raw = None
                structured_raw = structured_raw or {}

                # Q&A cards
                for qa_item in (structured_raw.get("qa") or [])[:8]:
                    q = str(qa_item.get("q") or "").strip()[:400]
                    a = str(qa_item.get("a") or "").strip()[:800]
                    if q and a:
                        _cid = str(uuid.uuid4())
                        await conn.execute(
                            """INSERT INTO edu_flashcards (id, actor_kind, actor, note_id,
                                   front, back, due_at)
                               VALUES ($1,$2,$3,$4,$5,$6,NOW())""",
                            _cid, kind, actor, note_id, q, a,
                        )
                        _mongo_docs.append(_flashcard_doc_for_mongo(
                            card_id=_cid, kind=kind, actor=actor,
                            note_id=note_id, front=q, back=a, now_utc=_now_utc,
                        ))
                        created += 1

                # Mnemonic cards — front = "Mnemonic for <topic>?", back = phrase + explanation
                for mn in (structured_raw.get("mnemonics") or [])[:4]:
                    for_text = str(mn.get("for") or "").strip()[:200]
                    mnemonic_str = str(mn.get("mnemonic") or "").strip()[:300]
                    explanation = str(mn.get("explanation") or "").strip()[:500]
                    if for_text and mnemonic_str:
                        front_mn = f"Mnemonic for: {for_text}"[:300]
                        back_mn = mnemonic_str
                        if explanation:
                            back_mn += f"\n\n{explanation}"
                        back_mn = back_mn[:800]
                        _cid = str(uuid.uuid4())
                        await conn.execute(
                            """INSERT INTO edu_flashcards (id, actor_kind, actor, note_id,
                                   front, back, due_at)
                               VALUES ($1,$2,$3,$4,$5,$6,NOW())""",
                            _cid, kind, actor, note_id, front_mn, back_mn,
                        )
                        _mongo_docs.append(_flashcard_doc_for_mongo(
                            card_id=_cid, kind=kind, actor=actor,
                            note_id=note_id, front=front_mn, back=back_mn,
                            now_utc=_now_utc,
                        ))
                        created += 1
            else:
                # Manual highlight notes: split the raw text heuristically.
                front, back = _split_front_back(n["text"])
                _cid = str(uuid.uuid4())
                await conn.execute(
                    """INSERT INTO edu_flashcards (id, actor_kind, actor, note_id,
                           front, back, due_at)
                       VALUES ($1,$2,$3,$4,$5,$6,NOW())""",
                    _cid, kind, actor, note_id, front, back,
                )
                _mongo_docs.append(_flashcard_doc_for_mongo(
                    card_id=_cid, kind=kind, actor=actor, note_id=note_id,
                    front=front, back=back, now_utc=_now_utc,
                ))
                created += 1
    # ADR-0001 Phase 2: bulk-mirror every freshly-inserted card to Mongo
    # in one round-trip (best-effort; PG is SoT). Fired AFTER the PG
    # block exits so the connection is released first. Skip when the
    # build produced zero cards (no notes / all corrupt JSON).
    if _mongo_docs:
        _docs = _mongo_docs
        await mirror_edu_flashcards_write(
            "build_bulk",
            lambda: deps.db.edu_flashcards.insert_many(_docs, ordered=False),
        )
    return {"ok": True, "created": created}


@router.get("/edu/flashcards/due")
async def due_flashcards(request: Request, limit: int = 30,
                         user=Depends(get_current_user_optional)):
    await _ensure_schema()
    kind, actor = _actor(request, user)
    limit = max(1, min(limit, 100))
    async with deps.pg_pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT * FROM edu_flashcards
               WHERE actor_kind=$1 AND actor=$2 AND due_at <= NOW()
               ORDER BY due_at ASC LIMIT $3""",
            kind, actor, limit,
        )
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM edu_flashcards WHERE actor_kind=$1 AND actor=$2",
            kind, actor,
        )
    return {
        "ok": True,
        "cards": [_flashcard_row_to_dict(r) for r in rows],
        "total": int(total or 0),
    }


class CardReviewReq(BaseModel):
    card_id: str
    quality: int = Field(..., ge=0, le=5)  # SM-2 grade


def _sm2_step(ef: float, reps: int, interval: int, q: int) -> tuple[float, int, int]:
    """Apply one SM-2 step. q∈[0,5]: <3 = forgot."""
    if q < 3:
        return max(1.3, ef - 0.2), 0, 1
    new_ef = max(1.3, ef + 0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))
    new_reps = reps + 1
    if new_reps == 1:
        new_int = 1
    elif new_reps == 2:
        new_int = 6
    else:
        new_int = max(1, round(interval * new_ef))
    return new_ef, new_reps, new_int


@router.post("/edu/flashcards/review")
async def review_flashcard(req: CardReviewReq, request: Request,
                           user=Depends(get_current_user_optional)):
    await _ensure_schema()
    kind, actor = _actor(request, user)
    async with deps.pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM edu_flashcards WHERE id=$1 AND actor_kind=$2 AND actor=$3",
            req.card_id, kind, actor,
        )
        if not row:
            raise HTTPException(status_code=404, detail="card_not_found")
        ef, reps, interval = _sm2_step(
            float(row["ef"]), int(row["repetitions"]),
            int(row["interval_days"]), req.quality,
        )
        due_at = datetime.now(timezone.utc) + timedelta(days=interval)
        updated = await conn.fetchrow(
            """UPDATE edu_flashcards SET ef=$1, repetitions=$2, interval_days=$3,
                  due_at=$4, last_reviewed=NOW()
               WHERE id=$5 RETURNING *""",
            ef, reps, interval, due_at, req.card_id,
        )
    # ADR-0001 Phase 2: mirror the SM-2-updated card to Mongo via
    # replace_one(upsert=True). Greenfield-safe — pre-Phase-2 PG cards
    # have no Mongo twin yet, so an update_one $set would silently
    # no-op; upsert lets the first review materialise the doc. Filter
    # is scoped to {id, actor_kind, actor} per architect-hardening
    # pattern from edu_notes.
    _mongo_doc = _flashcard_row_for_mongo(updated)
    _cid = req.card_id
    _kind = kind
    _actor = actor
    await mirror_edu_flashcards_write(
        "review",
        lambda: deps.db.edu_flashcards.replace_one(
            {"id": _cid, "actor_kind": _kind, "actor": _actor},
            _mongo_doc,
            upsert=True,
        ),
    )
    async with deps.pg_pool.acquire() as conn:
        # Streak update
        today = date.today()
        s = await conn.fetchrow(
            "SELECT * FROM edu_study_settings WHERE actor_kind=$1 AND actor=$2",
            kind, actor,
        )
        if s is None:
            await conn.execute(
                """INSERT INTO edu_study_settings (actor_kind, actor, streak_count,
                      streak_last_day) VALUES ($1,$2,1,$3)""",
                kind, actor, today,
            )
            streak = 1
        else:
            last = s["streak_last_day"]
            if last == today:
                streak = int(s["streak_count"] or 0)
            elif last and (today - last).days == 1:
                streak = int(s["streak_count"] or 0) + 1
                await conn.execute(
                    "UPDATE edu_study_settings SET streak_count=$1, streak_last_day=$2 "
                    "WHERE actor_kind=$3 AND actor=$4",
                    streak, today, kind, actor,
                )
            else:
                streak = 1
                await conn.execute(
                    "UPDATE edu_study_settings SET streak_count=1, streak_last_day=$1 "
                    "WHERE actor_kind=$2 AND actor=$3",
                    today, kind, actor,
                )
    # Task #401 — for *successful* recalls (SM-2 quality 4 / 5) of a
    # logged-in user, persist the card's front/back into the memory_brain
    # as a `fact` memory so the next chat turn can ground on what the
    # student has confirmably learned. Only fires for `actor_kind == "user"`
    # to avoid spamming the brain with anonymous device IDs that have no
    # cross-session continuity, and only on quality >= 4 to skip cards
    # the student is still struggling with. Fully best-effort:
    # `write_flashcard_recall_memory` swallows all provider errors.
    if kind == "user" and actor and req.quality is not None and req.quality >= 4:
        try:
            from memory_brain_chat import (
                write_flashcard_recall_memory as _mb_write_flashcard,
            )
            asyncio.create_task(_mb_write_flashcard(
                actor,
                front=updated["front"] or "",
                back=updated["back"] or "",
                quality=int(req.quality),
                note_id=updated["note_id"] or None,
                card_id=str(req.card_id),
                interval_days=int(updated["interval_days"]),
                repetitions=int(updated["repetitions"]),
            ))
        except Exception as _mb_err:
            logger.debug("memory_brain flashcard write skipped (non-fatal): %s", _mb_err)
    return {"ok": True, "card": _flashcard_row_to_dict(updated), "streak": streak}


@router.get("/edu/flashcards/streak")
async def flashcards_streak(request: Request,
                            user=Depends(get_current_user_optional)):
    await _ensure_schema()
    kind, actor = _actor(request, user)
    async with deps.pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT streak_count, streak_last_day FROM edu_study_settings "
            "WHERE actor_kind=$1 AND actor=$2",
            kind, actor,
        )
    if not row:
        return {"ok": True, "streak": 0, "last_day": None}
    return {"ok": True, "streak": int(row["streak_count"] or 0),
            "last_day": row["streak_last_day"].isoformat() if row["streak_last_day"] else None}


# ───────────────────────── Settings + guardian PIN ─────────────────────────

class SettingsReq(BaseModel):
    strict_mode: Optional[bool] = None


def _hash_pin(pin: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), salt.encode(), 50_000).hex()


@router.get("/edu/study/settings")
async def get_study_settings(request: Request,
                             user=Depends(get_current_user_optional)):
    await _ensure_schema()
    kind, actor = _actor(request, user)
    async with deps.pg_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM edu_study_settings WHERE actor_kind=$1 AND actor=$2",
            kind, actor,
        )
    return {
        "ok": True,
        "strict_mode": bool(row and row["strict_mode"]),
        "guardian_locked": bool(row and row["guardian_pin_hash"]),
        "streak": int(row["streak_count"]) if row else 0,
    }


@router.post("/edu/study/settings")
async def set_study_settings(req: SettingsReq, request: Request,
                             pin: str = "", user=Depends(get_current_user_optional)):
    await _ensure_schema()
    kind, actor = _actor(request, user)
    async with deps.pg_pool.acquire() as conn:
        cur = await conn.fetchrow(
            "SELECT guardian_pin_hash FROM edu_study_settings WHERE actor_kind=$1 AND actor=$2",
            kind, actor,
        )
        # If a PIN exists and we are turning Strict Mode OFF, require it.
        if cur and cur["guardian_pin_hash"] and req.strict_mode is False:
            if not pin:
                raise HTTPException(status_code=403, detail="pin_required")
            salt = f"{kind}:{actor}"
            if not hmac.compare_digest(_hash_pin(pin, salt), cur["guardian_pin_hash"]):
                raise HTTPException(status_code=403, detail="bad_pin")
        await conn.execute(
            """INSERT INTO edu_study_settings (actor_kind, actor, strict_mode)
               VALUES ($1,$2,COALESCE($3, FALSE))
               ON CONFLICT (actor_kind, actor)
               DO UPDATE SET strict_mode=COALESCE($3, edu_study_settings.strict_mode),
                             updated_at=NOW()""",
            kind, actor, req.strict_mode,
        )
    return {"ok": True}


class PinSetReq(BaseModel):
    new_pin: str = Field(..., min_length=4, max_length=12)
    current_pin: str = Field("", max_length=12)


@router.post("/edu/guardian/pin/set")
async def guardian_pin_set(req: PinSetReq, request: Request,
                           user=Depends(get_current_user_optional)):
    await _ensure_schema()
    if not req.new_pin.isdigit():
        raise HTTPException(status_code=400, detail="numeric_pin_required")
    kind, actor = _actor(request, user)
    salt = f"{kind}:{actor}"
    async with deps.pg_pool.acquire() as conn:
        cur = await conn.fetchrow(
            "SELECT guardian_pin_hash FROM edu_study_settings "
            "WHERE actor_kind=$1 AND actor=$2",
            kind, actor,
        )
        if cur and cur["guardian_pin_hash"]:
            if not req.current_pin:
                raise HTTPException(status_code=403, detail="current_pin_required")
            if not hmac.compare_digest(_hash_pin(req.current_pin, salt),
                                       cur["guardian_pin_hash"]):
                raise HTTPException(status_code=403, detail="bad_pin")
        new_hash = _hash_pin(req.new_pin, salt)
        await conn.execute(
            """INSERT INTO edu_study_settings (actor_kind, actor, guardian_pin_hash)
               VALUES ($1,$2,$3)
               ON CONFLICT (actor_kind, actor)
               DO UPDATE SET guardian_pin_hash=$3, updated_at=NOW()""",
            kind, actor, new_hash,
        )
    return {"ok": True}


class PinVerifyReq(BaseModel):
    pin: str = Field(..., min_length=4, max_length=12)


@router.post("/edu/guardian/pin/verify")
async def guardian_pin_verify(req: PinVerifyReq, request: Request,
                              user=Depends(get_current_user_optional)):
    await _ensure_schema()
    kind, actor = _actor(request, user)
    # Task #594: rate-limit PIN verify per actor so an attacker can't
    # brute-force a 4-digit PIN (10⁴ space) in seconds. 8 attempts per
    # 5 minutes is well above any legitimate guardian flow but caps a
    # brute-force run at a few attempts per window.
    if not check_rate_limit(f"edu_pin_verify:{kind}:{actor}",
                            max_requests=8, window_seconds=300):
        raise HTTPException(status_code=429, detail="pin_verify_rate_limited")
    salt = f"{kind}:{actor}"
    async with deps.pg_pool.acquire() as conn:
        cur = await conn.fetchrow(
            "SELECT guardian_pin_hash FROM edu_study_settings "
            "WHERE actor_kind=$1 AND actor=$2",
            kind, actor,
        )
    if not cur or not cur["guardian_pin_hash"]:
        return {"ok": True, "valid": True, "set": False}
    valid = hmac.compare_digest(_hash_pin(req.pin, salt), cur["guardian_pin_hash"])
    return {"ok": True, "valid": valid, "set": True}


# ───────────────────────── Anon → user sync ─────────────────────────

@router.post("/edu/sync/claim")
async def claim_anon_data(request: Request, user=Depends(get_current_user)):
    """Reassign notes / flashcards / settings created under the caller's
    anonymous device id (`x-anon-id`) to their now signed-in user id.

    Idempotent: rerunning after a successful claim returns zero counts
    because the anon rows have already been moved to the user actor.
    """
    await _ensure_schema()
    anon = request.headers.get("x-anon-id", "").strip()[:80]
    user_id = user.get("id") if isinstance(user, dict) else None
    if not anon or not user_id or anon == user_id:
        return {"ok": True, "notes": 0, "flashcards": 0,
                "settings_merged": False}
    notes_count = 0
    cards_count = 0
    settings_merged = False
    pin_dropped = False
    async with deps.pg_pool.acquire() as conn:
        async with conn.transaction():
            notes_res = await conn.execute(
                "UPDATE edu_notes SET actor_kind='user', actor=$1, "
                "claimed_at=NOW() "
                "WHERE actor_kind='anon' AND actor=$2",
                user_id, anon,
            )
            cards_res = await conn.execute(
                "UPDATE edu_flashcards SET actor_kind='user', actor=$1, "
                "claimed_at=NOW() "
                "WHERE actor_kind='anon' AND actor=$2",
                user_id, anon,
            )
            anon_settings = await conn.fetchrow(
                "SELECT * FROM edu_study_settings "
                "WHERE actor_kind='anon' AND actor=$1",
                anon,
            )
            anon_had_pin = bool(anon_settings and anon_settings["guardian_pin_hash"])
            if anon_settings:
                user_settings = await conn.fetchrow(
                    "SELECT * FROM edu_study_settings "
                    "WHERE actor_kind='user' AND actor=$1",
                    user_id,
                )
                anon_strict = bool(anon_settings["strict_mode"])
                anon_streak = int(anon_settings["streak_count"] or 0)
                anon_last = anon_settings["streak_last_day"]
                if not user_settings:
                    # Adopt the anon strict-mode + streak. The guardian
                    # PIN hash is salted with the anon actor id so it
                    # would no longer verify after re-salting; drop it
                    # rather than carry an unusable hash forward.
                    await conn.execute(
                        """INSERT INTO edu_study_settings (actor_kind, actor,
                              strict_mode, guardian_pin_hash, streak_count,
                              streak_last_day)
                           VALUES ('user',$1,$2,NULL,$3,$4)""",
                        user_id, anon_strict, anon_streak, anon_last,
                    )
                    settings_merged = True
                else:
                    # Merge into existing user settings so signed-out
                    # changes are not silently lost.
                    user_strict = bool(user_settings["strict_mode"])
                    user_streak = int(user_settings["streak_count"] or 0)
                    user_last = user_settings["streak_last_day"]
                    # Strict Mode: take the safer (stricter) setting.
                    merged_strict = user_strict or anon_strict
                    # Streak: keep the longer chain; tie-break with the
                    # more recent activity day so the streak's "last
                    # day" stays accurate for the daily-rollover logic.
                    if anon_streak > user_streak:
                        merged_streak, merged_last = anon_streak, anon_last
                    elif anon_streak < user_streak:
                        merged_streak, merged_last = user_streak, user_last
                    else:
                        merged_streak = user_streak
                        if user_last and anon_last:
                            merged_last = max(user_last, anon_last)
                        else:
                            merged_last = user_last or anon_last
                    changed = (
                        merged_strict != user_strict
                        or merged_streak != user_streak
                        or merged_last != user_last
                    )
                    if changed:
                        await conn.execute(
                            """UPDATE edu_study_settings
                               SET strict_mode=$1, streak_count=$2,
                                   streak_last_day=$3, updated_at=NOW()
                               WHERE actor_kind='user' AND actor=$4""",
                            merged_strict, merged_streak, merged_last, user_id,
                        )
                        settings_merged = True
                await conn.execute(
                    "DELETE FROM edu_study_settings "
                    "WHERE actor_kind='anon' AND actor=$1",
                    anon,
                )
            # Task #611: signal to the client that the parental PIN
            # could not be migrated. The PIN hash is salted with the
            # actor id, so a hash created under the anon device id
            # can never be verified once the actor flips to the user.
            # Surface this so the UI can prompt the parent to set a
            # new PIN, instead of silently leaving Strict Mode without
            # a usable lock.
            if anon_had_pin:
                final = await conn.fetchrow(
                    "SELECT strict_mode, guardian_pin_hash FROM edu_study_settings "
                    "WHERE actor_kind='user' AND actor=$1",
                    user_id,
                )
                if final and bool(final["strict_mode"]) and not final["guardian_pin_hash"]:
                    pin_dropped = True
    try:
        notes_count = int(notes_res.split()[-1])
    except Exception:
        notes_count = 0
    try:
        cards_count = int(cards_res.split()[-1])
    except Exception:
        cards_count = 0
    # ADR-0001 Phase 2: mirror the bulk anon→user reassignment to Mongo
    # (best-effort; PG is SoT). This fires AFTER the PG transaction has
    # committed (we are outside both ``async with`` blocks here), so a
    # rollback on the PG side cannot leave a phantom Mongo write. Skip
    # when PG moved zero rows to keep the counter honest about real work.
    _anon = anon
    _uid = user_id
    _claimed_at = datetime.now(timezone.utc)
    if notes_count > 0:
        await mirror_edu_notes_write(
            "claim_bulk",
            lambda: deps.db.edu_notes.update_many(
                {"actor_kind": "anon", "actor": _anon},
                {"$set": {
                    "actor_kind": "user",
                    "actor": _uid,
                    "claimed_at": _claimed_at,
                }},
            ),
        )
    # ADR-0001 Phase 2 (edu_flashcards): same post-transaction mirror
    # pattern. Gated on cards_count > 0 to keep the counter honest.
    if cards_count > 0:
        await mirror_edu_flashcards_write(
            "claim_bulk",
            lambda: deps.db.edu_flashcards.update_many(
                {"actor_kind": "anon", "actor": _anon},
                {"$set": {
                    "actor_kind": "user",
                    "actor": _uid,
                    "claimed_at": _claimed_at,
                }},
            ),
        )
    return {"ok": True, "notes": notes_count, "flashcards": cards_count,
            "settings_merged": settings_merged, "pin_dropped": pin_dropped}


# ───────────────────────── Voice (STT + status) ─────────────────────────

# Task #337 — Personalize-backed home rail with deterministic fallback.
# The endpoint mirrors the runbook §3.8 contract: try Personalize first
# (when ``AWS_PERSONALIZE_CAMPAIGN_ARN`` + the admin toggle are both set
# and live), and otherwise apply the popularity × recency × subject_affinity
# deterministic ranker shipped with providers.aws_native. Either path
# returns a list of subject/chapter ids the React home page renders.

@router.get("/edu/recommendations")
async def edu_recommendations(
    limit: int = 12,
    user=Depends(get_current_user_optional),
):
    import os as _os
    user_id = (user or {}).get("id") or "anon"
    campaign_arn = _os.environ.get("AWS_PERSONALIZE_CAMPAIGN_ARN", "").strip()

    # 1) Live Personalize path.
    try:
        from providers import aws_native as _awsn
        if campaign_arn and _awsn.is_enabled("personalize") and _awsn.is_configured():
            ids = await asyncio.to_thread(
                _awsn.get_recommendations,
                campaign_arn=campaign_arn,
                user_id=user_id,
                num_results=limit,
            )
            if ids:
                return {"source": "personalize", "items": ids}
    except Exception as _exc:
        logger.warning("Personalize recommendations failed, falling through: %s", _exc)

    # 2) Deterministic fallback — popularity × recency × subject_affinity.
    try:
        _db = deps.db
        cursor = _db["chapters"].find(
            {}, {"id": 1, "popularity_score": 1, "recency_score": 1, "subject_affinity": 1},
        ).limit(500)
        candidates = []
        async for doc in cursor:
            candidates.append({
                "id": doc.get("id"),
                "popularity_score": doc.get("popularity_score", 0.0),
                "recency_score":    doc.get("recency_score", 0.0),
                "subject_affinity": doc.get("subject_affinity", 0.0),
            })
    except Exception:
        candidates = []

    from providers import aws_native as _awsn
    ids = _awsn.deterministic_recommendations(candidates, num_results=limit)
    return {"source": "deterministic", "items": ids}


@router.get("/edu/voice/status")
async def voice_status():
    return {
        "ok": True,
        "tts_enabled": sarvam_client is not None,
        "stt_enabled": sarvam_client is not None,
        "languages": ["en-IN", "as-IN", "hi-IN", "bn-IN"],
        "browser_stt_recommended": True,
    }


_GOOGLE_STT_INDIC_LANGS = frozenset({"hi", "hi-in", "bn", "bn-in", "as", "as-in"})


@router.post("/edu/stt")
async def edu_stt(audio: UploadFile = File(...), language: str = Form("en-IN"),
                  request: Request = None, user=Depends(get_current_user_optional)):
    """Server-side fallback STT.

    Priority:
    - Indic (hi, bn, as): Google Cloud Chirp_2 STT (Task #247) → Sarvam Saaras → Workers AI Whisper
    - English/other: Sarvam Saaras → Workers AI Whisper
    Browser SpeechRecognition is preferred on the client; this endpoint is the server-side fallback.
    """
    import time as _t_stt
    _stt_t0 = _t_stt.perf_counter()

    if request is not None:
        ip = _client_ip(request)
        if not check_rate_limit(f"edu_stt:{ip}", max_requests=30, window_seconds=60):
            raise HTTPException(status_code=429, detail="STT rate limit exceeded.")

    body = await audio.read()
    if not body or len(body) > 4 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="audio_size_invalid")

    lang_key = (language or "en-IN").lower().strip()

    if lang_key in _GOOGLE_STT_INDIC_LANGS:
        try:
            from providers import google_stt as _gstt
            if _gstt.is_configured():
                transcript = await _gstt.transcribe_indic(
                    body,
                    language_code=lang_key,
                    audio_encoding=audio.content_type or "LINEAR16",
                )
                if transcript:
                    _gstt_ms = int((_t_stt.perf_counter() - _stt_t0) * 1000)
                    logger.info("[edu_stt] Google Chirp_2 lang=%s chars=%d ms=%d", lang_key, len(transcript), _gstt_ms)
                    return {"ok": True, "text": transcript, "language": language, "provider": "google_chirp2"}
        except Exception as _gstt_err:
            logger.warning("[edu_stt] Google Chirp_2 failed for %s: %s", lang_key, str(_gstt_err)[:150])

    if sarvam_client is None:
        raise HTTPException(status_code=503, detail="stt_unavailable")

    files = {"file": (audio.filename or "speech.wav",
                      body, audio.content_type or "audio/wav")}
    data = {"language_code": language or "en-IN", "model": "saaras:v2"}
    primary_err: Exception | None = None
    resp = None
    try:
        resp = await sarvam_client.post("/speech-to-text", files=files, data=data)
    except Exception as e:
        logger.warning(f"[edu_stt] sarvam call failed: {e}")
        primary_err = e
    if resp is not None and resp.status_code >= 400:
        logger.warning(f"[edu_stt] provider {resp.status_code}: {resp.text[:300]}")
        try:
            resp.raise_for_status()
        except Exception as e:
            primary_err = e

    if primary_err is not None:
        # Task #636 — Workers AI Whisper fallback for retryable failures.
        try:
            from providers import workers_ai as _wai
            if _wai.is_enabled("stt") and _wai.should_fallback(primary_err):
                import base64 as _b64
                audio_b64 = _b64.b64encode(body).decode("ascii")
                _primary_ms = int((_t_stt.perf_counter() - _stt_t0) * 1000)
                ok, val, _ = await _wai.attempt_fallback(
                    "stt", primary_err, _primary_ms,
                    lambda: _wai.call_stt(audio_b64),
                )
                if ok and isinstance(val, str):
                    return {"ok": True, "text": val, "language": language,
                            "provider": "workers-ai"}
        except Exception as _wai_err:  # noqa: BLE001
            logger.warning(f"[workers-ai] stt fallback skipped: {type(_wai_err).__name__}: {str(_wai_err)[:150]}")
        raise HTTPException(status_code=502,
                            detail="stt_provider_failed" if resp is None else "stt_provider_error")

    payload = resp.json()
    text = payload.get("transcript") or payload.get("text") or ""
    return {"ok": True, "text": text, "language": payload.get("language_code", language), "provider": "sarvam"}


__all__ = ["router"]
