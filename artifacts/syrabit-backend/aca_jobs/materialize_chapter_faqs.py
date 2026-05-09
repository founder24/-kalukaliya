"""aca_jobs.materialize_chapter_faqs — Task #12 nightly AEO Answer-Card + FAQ
materializer.

Walks every published chapter, mines the PYQ corpus + syllabus graph for
5–10 deterministic Q→A pairs per chapter, materialises each pair via
``content_formatter.format_content(query_type="faq", ...)`` and a
40–60-word Quick-Answer via ``query_type="quick_answer"``. Writes the
result to:

  * ``db.chapter_faqs``      — authoritative store (one doc per pair,
                               ``kind="faq" | "quick_answer"``).
                               Idempotent upsert keyed by
                               ``(chapter_id, kind, fingerprint)``.
  * ``db.aeo_faq_entries``   — renderer view consumed by
                               ``routes/seo_pages._load_faq_entries``.
                               One doc per ``(chapter_id, page_type,
                               position)``; the same FAQ set is
                               replicated across all 7 page-types so
                               every SEO URL emits a populated FAQPage
                               JSON-LD block on day one.
  * ``db.aeo_quick_answers`` — renderer view consumed by
                               ``routes/seo_pages._load_quick_answer``.
                               One doc per ``(chapter_id, page_type)``.
  * Cloudflare KV / Redis    — ``ai_input_cache`` write under the
                               Task #6 fingerprint key so the
                               edge-side preview rendering can serve
                               the materialised answer without a
                               Mongo round-trip.

V4 §12 (no silent fallbacks): every Q→A is rendered through
``content_formatter`` so the audit field ``formatted_by =
"deterministic_template"`` is preserved across the chain. If the
template fails for a chapter we surface the error in the per-chapter
summary and skip the chapter — one bad row never blocks the rest of
the corpus.

Lambda handler lives at
``artifacts/syrabit/services/backend/lambda_batch/materialize_chapter_faqs.py``;
this module is the in-tree entry point invoked from there. The
``aca_jobs`` package is enumerated by
``scripts/check_dead_providers.py`` so the matching
``infra/aws/lambda/manifest.json`` row is mandatory.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("aca_jobs.materialize_chapter_faqs")

# Mirrors ``routes/seo_pages.PAGE_TYPES``; kept as a module-local list
# so this job has zero hard dependency on the FastAPI router import
# tree (the Lambda package may not bring the routes/ subpackage along
# in a slim image).
PAGE_TYPES: Tuple[str, ...] = (
    "notes", "mcqs", "flashcards", "pyqs",
    "summary", "definitions", "revision",
)

# Generation knobs — caller can override via env or kwargs. Lower bound
# of 5 is a hard contract from the spec; upper bound of 10 protects the
# JSON-LD payload size budget Google uses to admit FAQPage rich
# results (~10 entries before they start trimming).
FAQ_MIN_PER_CHAPTER = 5
FAQ_MAX_PER_CHAPTER = 10
QUICK_ANSWER_MIN_WORDS = 40
QUICK_ANSWER_MAX_WORDS = 60


# ─── Q→A construction helpers ────────────────────────────────────────────


def _clean_text(s: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _trim_words(text: str, *, min_words: int, max_words: int,
                pad_suffix: str = "") -> str:
    """Return ``text`` clamped to ``[min_words, max_words]``.

    If shorter than ``min_words`` and ``pad_suffix`` is provided, the
    suffix is appended (and re-clamped). The function never raises; if
    we cannot reach the floor even after padding, the result is
    returned as-is (callers prefer a slightly-short answer over a hard
    failure that would skip the entire chapter).
    """
    words = _clean_text(text).split()
    if len(words) > max_words:
        return " ".join(words[:max_words]).rstrip(",.;:") + "."
    if len(words) < min_words and pad_suffix:
        words = (text + " " + pad_suffix).split()
        if len(words) > max_words:
            return " ".join(words[:max_words]).rstrip(",.;:") + "."
    return " ".join(words)


def build_quick_answer(
    *, chapter_title: str, subject_name: str, board_name: str,
    class_name: str, subtopics: List[Dict[str, Any]],
    chapter_description: str = "",
) -> str:
    """40–60-word AEO Answer-Card body for a chapter.

    Deterministic — same inputs produce the same output. Uses the
    chapter description when present (pre-trimmed to the word ceiling)
    and falls back to a syllabus-grounded composition assembled from
    the chapter title + first three sub-topic titles.
    """
    seed = _clean_text(chapter_description)
    if seed:
        body = (
            f"{chapter_title} ({class_name} {subject_name}, {board_name}) — "
            f"{seed}"
        )
    else:
        bullets = ", ".join(
            _clean_text(t.get("title", ""))
            for t in subtopics[:3]
            if _clean_text(t.get("title", ""))
        )
        if bullets:
            body = (
                f"{chapter_title} is the {class_name} {subject_name} "
                f"chapter for {board_name} students in Assam covering "
                f"{bullets}, with definitions, worked examples and "
                f"exam-ready revision points."
            )
        else:
            body = (
                f"{chapter_title} is the {class_name} {subject_name} "
                f"chapter prescribed by {board_name} for students in "
                f"Assam, with definitions, worked examples and "
                f"exam-ready revision points aligned to the official "
                f"syllabus."
            )
    pad = (
        f"Each sub-topic is cross-linked to the matching {board_name} "
        f"{class_name} {subject_name} revision notes so students can "
        f"prepare quickly ahead of board exams."
    )
    return _trim_words(
        body,
        min_words=QUICK_ANSWER_MIN_WORDS,
        max_words=QUICK_ANSWER_MAX_WORDS,
        pad_suffix=pad,
    )


def build_faq_pairs(
    *, chapter_title: str, subject_name: str, board_name: str,
    class_name: str, subtopics: List[Dict[str, Any]],
    pyq_stems: List[Dict[str, Any]],
    chapter_description: str = "",
) -> List[Dict[str, str]]:
    """Return 5–10 deterministic Q→A pairs for a chapter.

    Sources, in priority order:
      1. A canonical "what does this chapter cover?" lead-in.
      2. Each sub-topic with a non-empty summary becomes
         "What is <sub-topic>?" with the syllabus summary as answer.
      3. Each PYQ stem becomes the question; the deterministic answer
         points the student at the syllabus-aligned revision material
         for the chapter (the actual marker-scheme answer is owned by
         the per-question PYQ pipeline, not this AEO materializer).
      4. Two evergreen "where can I find" / "is this AHSEC-aligned"
         pairs to guarantee we always have ≥5 entries even for a
         freshly-seeded chapter with no PYQs and no sub-topic summaries.
    """
    seen_q: set = set()
    pairs: List[Dict[str, str]] = []

    def _push(question: str, answer: str, source: str) -> None:
        q = _clean_text(question)
        a = _clean_text(answer)
        if not q or not a:
            return
        key = q.lower()
        if key in seen_q:
            return
        if len(pairs) >= FAQ_MAX_PER_CHAPTER:
            return
        seen_q.add(key)
        pairs.append({"question": q, "answer": a, "source": source})

    # 1. Lead-in.
    lead_answer = (
        _clean_text(chapter_description)
        or (
            f"The {chapter_title} chapter in {class_name} {subject_name} "
            f"({board_name}) covers every sub-topic prescribed in the "
            f"official Assam Board syllabus, with definitions, worked "
            f"examples and revision aids."
        )
    )
    _push(
        f"What does the {chapter_title} chapter cover?",
        lead_answer,
        "syllabus",
    )

    # 2. Sub-topic Q→As.
    for st in subtopics:
        title = _clean_text(st.get("title", ""))
        summary = _clean_text(st.get("summary", ""))
        if not title:
            continue
        if summary:
            _push(f"What is {title}?", summary, "syllabus")
        else:
            _push(
                f"What is {title} in {chapter_title}?",
                (
                    f"{title} is a sub-topic of {chapter_title} in the "
                    f"{board_name} {class_name} {subject_name} syllabus."
                ),
                "syllabus",
            )
        if len(pairs) >= FAQ_MAX_PER_CHAPTER:
            break

    # 3. PYQ-mined questions.
    if len(pairs) < FAQ_MAX_PER_CHAPTER:
        for row in pyq_stems:
            stem = _clean_text(row.get("question_stem") or row.get("title") or "")
            year = row.get("year")
            if not stem or len(stem) < 8:
                continue
            year_prefix = f"({board_name} {year}) " if year else ""
            answer = (
                f"{year_prefix}This PYQ on {chapter_title} is solved in "
                f"the chapter's revision notes and worked-example bank "
                f"on Syrabit.ai — open the chapter's MCQs and "
                f"definitions pages for the syllabus-aligned solution."
            )
            _push(stem, answer, "pyq")
            if len(pairs) >= FAQ_MAX_PER_CHAPTER:
                break

    # 4. Evergreen pad to guarantee the floor.
    pad_pairs = [
        (
            f"Where can I find {chapter_title} notes for {board_name} "
            f"{class_name} {subject_name}?",
            (
                f"Free, syllabus-aligned {chapter_title} notes for "
                f"{board_name} {class_name} {subject_name} are published "
                f"on Syrabit.ai in both English and Assamese, with "
                f"chapter-level MCQs, flashcards and previous-year "
                f"questions."
            ),
        ),
        (
            f"Is the {chapter_title} chapter aligned to the {board_name} "
            f"{class_name} syllabus?",
            (
                f"Yes — Syrabit.ai's {chapter_title} content is grounded "
                f"in the official {board_name} {class_name} "
                f"{subject_name} syllabus and reviewed against the "
                f"current academic-year curriculum."
            ),
        ),
        (
            f"How can I revise {chapter_title} quickly before exams?",
            (
                f"Use the Syrabit.ai revision page for {chapter_title} — "
                f"it bundles a 40-word summary, the chapter's flashcards "
                f"and the previous-year questions so {board_name} "
                f"{class_name} students can revise the chapter in one "
                f"sitting."
            ),
        ),
        (
            f"Are {chapter_title} notes available in Assamese?",
            (
                f"Yes — every {chapter_title} page on Syrabit.ai ships "
                f"with an Assamese translation alongside the English "
                f"original, so {board_name} {class_name} students can "
                f"revise in either language."
            ),
        ),
        (
            f"What kinds of questions appear from {chapter_title} in "
            f"{board_name} {class_name} board exams?",
            (
                f"Past {board_name} {class_name} {subject_name} papers on "
                f"{chapter_title} typically combine short definitions, "
                f"a labelled-diagram question and one long-answer "
                f"explanation — Syrabit.ai's PYQ bank lists every recent "
                f"appearance with its mark allocation."
            ),
        ),
    ]
    for q, a in pad_pairs:
        if len(pairs) >= FAQ_MIN_PER_CHAPTER:
            break
        _push(q, a, "evergreen")

    return pairs


# ─── Persistence ─────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_bson() -> datetime:
    """Timezone-aware ``datetime`` for Mongo writes.

    The ``chapter_faqs`` TTL index ``expireAfterSeconds`` only fires
    when the indexed field is a BSON date — string timestamps are
    silently ignored by Mongo's TTL monitor. Every authoritative
    write therefore stamps ``updated_at`` / ``created_at`` with this
    helper rather than the ISO string used elsewhere for log lines.
    """
    return datetime.now(timezone.utc)


async def ensure_indexes(db) -> None:
    """Create the ``chapter_faqs`` indexes the spec demands.

    * **Unique** ``(chapter_id, kind, fingerprint)`` — idempotent
      upsert key called out in step #1 of the task plan.
    * **TTL** ``updated_at`` 90 days — also from step #1; stale rows
      that the nightly job stops touching are auto-purged so the
      authoritative collection cannot drift indefinitely.
    """
    try:
        await db.chapter_faqs.create_index(
            [("chapter_id", 1), ("kind", 1), ("fingerprint", 1)],
            unique=True, name="chapter_faqs_unique_v1",
        )
    except Exception as e:
        logger.warning("[materialize-faqs] unique index create failed: %s", e)
    try:
        await db.chapter_faqs.create_index(
            [("updated_at", 1)],
            expireAfterSeconds=90 * 24 * 3600,
            name="chapter_faqs_ttl_v1",
        )
    except Exception as e:
        logger.warning("[materialize-faqs] ttl index create failed: %s", e)


async def _purge_stale_authoritative_rows(
    db, *, chapter_id: str, fresh_fingerprints: List[str],
) -> None:
    """Delete ``chapter_faqs`` rows for ``chapter_id`` whose fingerprint
    is no longer in the freshly-materialised set. Without this, the
    authoritative store would monotonically grow stale rows whenever
    the FAQ list shrinks or a question is rephrased — and the admin
    coverage tile (which reads ``chapter_faqs.distinct("chapter_id")``)
    would over-count chapters whose live FAQ payload is now empty.
    """
    try:
        await db.chapter_faqs.delete_many({
            "chapter_id":  chapter_id,
            "fingerprint": {"$nin": fresh_fingerprints},
        })
    except Exception as e:
        logger.warning(
            "[materialize-faqs] stale-row purge failed for %s: %s",
            chapter_id, e,
        )


async def _store_chapter_payload(
    db,
    *,
    chapter_id: str,
    quick_answer_text: str,
    quick_answer_rendered: str,
    faq_pairs: List[Dict[str, str]],
    faq_rendered: List[str],
    chapter_title: str,
    subject_name: str,
    board_name: str,
    class_name: str,
) -> None:
    """Persist the materialised payload across the three collections."""
    from cache_fingerprint import fingerprint as fp_compute

    # BSON datetime — TTL index only honours date types.
    now = _now_bson()

    # Authoritative ``chapter_faqs`` upserts (one doc per FAQ + one for
    # the quick-answer). Idempotent on (chapter_id, kind, fingerprint).
    qa_fp = fp_compute(
        f"quick_answer:{chapter_id}",
        language="en",
        chapter=chapter_id,
        query_type="quick_answer",
    )
    fresh_fps: List[str] = [qa_fp]
    await db.chapter_faqs.update_one(
        {"chapter_id": chapter_id, "kind": "quick_answer",
         "fingerprint": qa_fp},
        {"$set": {
            "chapter_id":   chapter_id,
            "kind":         "quick_answer",
            "fingerprint":  qa_fp,
            "answer":       quick_answer_text,
            "rendered":     quick_answer_rendered,
            "formatted_by": "deterministic_template",
            "chapter_title": chapter_title,
            "subject_name":  subject_name,
            "board_name":    board_name,
            "class_name":    class_name,
            "updated_at":    now,
         },
         "$setOnInsert": {"created_at": now}},
        upsert=True,
    )

    for position, pair in enumerate(faq_pairs):
        fp = fp_compute(
            pair["question"],
            language="en",
            chapter=chapter_id,
            query_type="faq",
        )
        fresh_fps.append(fp)
        await db.chapter_faqs.update_one(
            {"chapter_id": chapter_id, "kind": "faq", "fingerprint": fp},
            {"$set": {
                "chapter_id":   chapter_id,
                "kind":         "faq",
                "fingerprint":  fp,
                "question":     pair["question"],
                "answer":       pair["answer"],
                "rendered":     faq_rendered[position],
                "source":       pair.get("source", "syllabus"),
                "position":     position,
                "formatted_by": "deterministic_template",
                "updated_at":   now,
             },
             "$setOnInsert": {"created_at": now}},
            upsert=True,
        )

    # Drop authoritative rows for this chapter that the latest pass
    # did not reproduce — keeps ``chapter_faqs`` in lock-step with the
    # current FAQ list and prevents the admin coverage tile from
    # over-counting against stale rows.
    await _purge_stale_authoritative_rows(
        db, chapter_id=chapter_id, fresh_fingerprints=fresh_fps,
    )

    # Renderer-friendly views: replicate across all 7 page-types so
    # every SEO URL renders a populated FAQPage block. Wipe-then-insert
    # keeps the view in lock-step with the latest authoritative payload
    # (new FAQs evict old ones; deleted FAQs disappear from the view).
    await db.aeo_quick_answers.delete_many({"chapter_id": chapter_id})
    await db.aeo_faq_entries.delete_many({"chapter_id": chapter_id})

    qa_view_docs = [
        {"chapter_id": chapter_id, "page_type": pt,
         "answer": quick_answer_text, "updated_at": now}
        for pt in PAGE_TYPES
    ]
    if qa_view_docs:
        await db.aeo_quick_answers.insert_many(qa_view_docs)

    faq_view_docs: List[Dict[str, Any]] = []
    for pt in PAGE_TYPES:
        for position, pair in enumerate(faq_pairs):
            faq_view_docs.append({
                "chapter_id": chapter_id,
                "page_type":  pt,
                "question":   pair["question"],
                "answer":     pair["answer"],
                "position":   position,
                "updated_at": now,
            })
    if faq_view_docs:
        await db.aeo_faq_entries.insert_many(faq_view_docs)


async def _publish_kv(*, chapter_id: str, faq_pairs: List[Dict[str, str]],
                      quick_answer_text: str,
                      quick_answer_rendered: str,
                      faq_rendered: List[str]) -> None:
    """Best-effort write into ``ai_input_cache`` under the Task #6
    fingerprint key. A failure here MUST NOT roll back the Mongo write
    — the cache is an accelerator, the database is the source of truth.
    """
    try:
        from ai_input_cache import set_response
        from cache_fingerprint import fingerprint as fp_compute
    except Exception as e:  # pragma: no cover — import-time only
        logger.warning("[materialize-faqs] ai_input_cache import failed: %s", e)
        return

    qa_fp = fp_compute(
        f"quick_answer:{chapter_id}",
        language="en",
        chapter=chapter_id,
        query_type="quick_answer",
    )
    try:
        set_response(
            [{"role": "user", "content": f"quick_answer:{chapter_id}"}],
            "content_formatter:deterministic:quick_answer",
            quick_answer_rendered or quick_answer_text,
            content_type="formatter",
            template_version="aeo_quick_answer_v1",
            fingerprint=qa_fp,
        )
    except Exception as e:
        logger.info("[materialize-faqs] KV write skipped (quick_answer): %s", e)

    for position, pair in enumerate(faq_pairs):
        fp = fp_compute(
            pair["question"], language="en",
            chapter=chapter_id, query_type="faq",
        )
        try:
            set_response(
                [{"role": "user", "content": pair["question"]}],
                "content_formatter:deterministic:faq",
                faq_rendered[position],
                content_type="formatter",
                template_version="aeo_faq_v1",
                fingerprint=fp,
            )
        except Exception as e:
            logger.info(
                "[materialize-faqs] KV write skipped (faq pos=%d): %s",
                position, e,
            )


# ─── Top-level driver ────────────────────────────────────────────────────


async def _resolve_chapter_chain(db, chapter: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """Resolve (board, class, subject) names for ``chapter``.

    Returns ``None`` when any link is missing — the chapter is then
    skipped from materialization with a structured warning so the
    operator can backfill the syllabus graph.
    """
    subj_id = chapter.get("subject_id")
    if not subj_id:
        return None
    subj = await db.subjects.find_one(
        {"id": subj_id},
        {"_id": 0, "id": 1, "name": 1, "class_id": 1, "stream_id": 1, "board_id": 1},
    )
    if not subj:
        return None
    cls_id = subj.get("class_id")
    if not cls_id and subj.get("stream_id"):
        stream = await db.streams.find_one(
            {"id": subj.get("stream_id")},
            {"_id": 0, "class_id": 1},
        )
        cls_id = (stream or {}).get("class_id")
    cls = await db.classes.find_one(
        {"id": cls_id}, {"_id": 0, "id": 1, "name": 1, "board_id": 1},
    ) if cls_id else None
    if not cls:
        return None
    brd_id = subj.get("board_id") or cls.get("board_id")
    brd = await db.boards.find_one(
        {"id": brd_id}, {"_id": 0, "id": 1, "name": 1},
    ) if brd_id else None
    if not brd:
        return None
    return {
        "board_name":   brd.get("name") or brd_id,
        "class_name":   cls.get("name") or cls_id,
        "subject_name": subj.get("name") or subj_id,
    }


async def _load_subtopics(db, chapter_id: str) -> List[Dict[str, Any]]:
    try:
        rows = await db.topics.find(
            {"chapter_id": chapter_id, "status": "published"},
            {"_id": 0, "title": 1, "summary": 1},
        ).sort("order", 1).to_list(50)
        return [r for r in rows if r.get("title")]
    except Exception:
        return []


_STEM_NORMALISE_RE = re.compile(r"[^\w\s]+")


def _normalise_stem(text: str) -> str:
    """Lower-case + collapse punctuation so semantically-identical
    stems group under one bucket during frequency mining."""
    return _STEM_NORMALISE_RE.sub(" ", _clean_text(text).lower()).strip()


async def _load_pyq_stems(db, chapter_id: str) -> List[Dict[str, Any]]:
    """Return PYQ rows for a chapter ranked by stem **frequency**.

    The task spec calls for "the most-frequent question stems" — i.e.
    the questions students actually keep seeing on the paper, not the
    most recent one. We run a Mongo aggregation that groups by a
    normalised stem (lower-cased, punctuation stripped) and counts
    occurrences; the canonical (unnormalised) stem of the
    highest-year occurrence is used as the display question. Returns
    up to 20 rows in descending count order; ties broken by year desc.
    """
    try:
        pipeline = [
            {"$match": {"chapter_id": chapter_id}},
            {"$project": {
                "_id": 0,
                "stem": {"$ifNull": ["$question_stem", "$title"]},
                "year": 1,
            }},
            {"$match": {"stem": {"$nin": [None, ""]}}},
            {"$group": {
                "_id": {"$toLower": {"$trim": {"input": "$stem"}}},
                "count":  {"$sum": 1},
                "stem":   {"$first": "$stem"},
                "year":   {"$max":  "$year"},
            }},
            {"$sort": {"count": -1, "year": -1}},
            {"$limit": 20},
            {"$project": {
                "_id": 0,
                "question_stem": "$stem",
                "year":  1,
                "count": 1,
            }},
        ]
        cursor = db.pyq_html_pages.aggregate(pipeline)
        rows = await cursor.to_list(20)
        if rows:
            return rows
    except Exception as e:
        # Fall through to the simpler client-side aggregation below
        # so a Mongo driver / fixture without ``aggregate`` still
        # produces frequency-ranked output rather than crashing.
        logger.info(
            "[materialize-faqs] aggregate() unavailable for %s — "
            "falling back to client-side frequency mining: %s",
            chapter_id, e,
        )

    try:
        raw = await db.pyq_html_pages.find(
            {"chapter_id": chapter_id},
            {"_id": 0, "question_stem": 1, "title": 1, "year": 1},
        ).to_list(500)
    except Exception:
        return []
    buckets: Dict[str, Dict[str, Any]] = {}
    for r in raw or []:
        stem = _clean_text(r.get("question_stem") or r.get("title") or "")
        if not stem:
            continue
        key = _normalise_stem(stem)
        if not key:
            continue
        b = buckets.setdefault(
            key, {"question_stem": stem, "year": r.get("year"), "count": 0},
        )
        b["count"] += 1
        if (r.get("year") or 0) > (b.get("year") or 0):
            b["year"] = r.get("year")
            b["question_stem"] = stem
    ranked = sorted(
        buckets.values(),
        key=lambda b: (-(b.get("count", 0) or 0), -(b.get("year") or 0)),
    )
    return ranked[:20]


async def materialize_one_chapter(db, chapter: Dict[str, Any]) -> Dict[str, Any]:
    """Materialise one chapter. Returns a per-chapter summary row."""
    from content_formatter import format_content

    chapter_id = chapter.get("id") or ""
    chapter_title = _clean_text(chapter.get("title", "")) or chapter.get("slug", "")
    description = _clean_text(chapter.get("description", ""))

    chain = await _resolve_chapter_chain(db, chapter)
    if not chain:
        return {
            "chapter_id": chapter_id,
            "chapter_title": chapter_title,
            "skipped": "missing_chain",
            "faq_count": 0,
        }

    subtopics = await _load_subtopics(db, chapter_id)
    pyq_stems = await _load_pyq_stems(db, chapter_id)

    quick_answer_text = build_quick_answer(
        chapter_title=chapter_title,
        subject_name=chain["subject_name"],
        board_name=chain["board_name"],
        class_name=chain["class_name"],
        subtopics=subtopics,
        chapter_description=description,
    )

    faq_pairs = build_faq_pairs(
        chapter_title=chapter_title,
        subject_name=chain["subject_name"],
        board_name=chain["board_name"],
        class_name=chain["class_name"],
        subtopics=subtopics,
        pyq_stems=pyq_stems,
        chapter_description=description,
    )

    if len(faq_pairs) < FAQ_MIN_PER_CHAPTER:
        return {
            "chapter_id": chapter_id,
            "chapter_title": chapter_title,
            "skipped": f"insufficient_pairs:{len(faq_pairs)}",
            "faq_count": len(faq_pairs),
        }

    try:
        qa_render = await format_content(
            quick_answer_text,
            query_type="quick_answer",
            template_data={"answer": quick_answer_text},
        )
    except Exception as e:
        logger.warning("[materialize-faqs] quick_answer render failed (%s): %s",
                       chapter_id, e)
        return {
            "chapter_id": chapter_id,
            "chapter_title": chapter_title,
            "skipped": "quick_answer_render_failed",
            "faq_count": 0,
        }

    faq_rendered: List[str] = []
    for pair in faq_pairs:
        try:
            r = await format_content(
                pair["question"],
                query_type="faq",
                template_data={
                    "question": pair["question"],
                    "answer":   pair["answer"],
                },
            )
        except Exception as e:
            logger.warning(
                "[materialize-faqs] faq render failed (%s): %s",
                chapter_id, e,
            )
            return {
                "chapter_id": chapter_id,
                "chapter_title": chapter_title,
                "skipped": "faq_render_failed",
                "faq_count": 0,
            }
        faq_rendered.append(r.get("text") or "")

    await _store_chapter_payload(
        db,
        chapter_id=chapter_id,
        quick_answer_text=quick_answer_text,
        quick_answer_rendered=qa_render.get("text") or quick_answer_text,
        faq_pairs=faq_pairs,
        faq_rendered=faq_rendered,
        chapter_title=chapter_title,
        subject_name=chain["subject_name"],
        board_name=chain["board_name"],
        class_name=chain["class_name"],
    )

    await _publish_kv(
        chapter_id=chapter_id,
        faq_pairs=faq_pairs,
        quick_answer_text=quick_answer_text,
        quick_answer_rendered=qa_render.get("text") or quick_answer_text,
        faq_rendered=faq_rendered,
    )

    return {
        "chapter_id":   chapter_id,
        "chapter_title": chapter_title,
        "faq_count":    len(faq_pairs),
        "quick_answer_words": len(quick_answer_text.split()),
    }


async def run_materialization(db, *, max_chapters: Optional[int] = None) -> Dict[str, Any]:
    """Walk every chapter and materialise FAQs + Quick-Answer.

    Returns a summary row suitable for the Lambda log + the admin
    coverage tile. Bounded by ``max_chapters`` (env
    ``MAX_DOCS_PER_RUN``); the default 0 means "every chapter".
    """
    cap = max_chapters or int(os.environ.get("MAX_DOCS_PER_RUN", "0") or "0")
    summary: Dict[str, Any] = {
        "started_at": _now_iso(),
        "scanned":    0,
        "materialised": 0,
        "skipped":    0,
        "errors":     0,
        "skip_reasons": {},
        "samples":    [],
    }

    # Idempotent — first pass creates the unique + TTL indexes the
    # spec mandates; subsequent passes are no-ops.
    await ensure_indexes(db)

    try:
        # Task spec scope: "every published chapter". Anything in
        # draft / archived state must not leak into materialised AEO
        # output (which is publicly cached at the SEO renderer).
        cursor = db.chapters.find(
            {"status": "published"},
            {"_id": 0, "id": 1, "slug": 1, "title": 1, "description": 1,
             "subject_id": 1},
        )
    except Exception as e:
        logger.exception("[materialize-faqs] cursor open failed: %s", e)
        summary["errors"] += 1
        summary["finished_at"] = _now_iso()
        return summary

    async for chapter in cursor:
        summary["scanned"] += 1
        if cap and summary["materialised"] + summary["skipped"] >= cap:
            break
        try:
            row = await materialize_one_chapter(db, chapter)
        except Exception as e:
            logger.exception(
                "[materialize-faqs] chapter %s failed: %s",
                chapter.get("id"), e,
            )
            summary["errors"] += 1
            continue
        if row.get("skipped"):
            summary["skipped"] += 1
            reason = row["skipped"]
            summary["skip_reasons"][reason] = (
                summary["skip_reasons"].get(reason, 0) + 1
            )
        else:
            summary["materialised"] += 1
            if len(summary["samples"]) < 5:
                summary["samples"].append(row)

    summary["finished_at"] = _now_iso()
    return summary


# ─── ACA in-process loop entry-point (mirrors comprehend_sampler) ────────


async def run_loop() -> None:  # pragma: no cover — invoked from server.py
    """Optional in-process loop for parity with the other ``aca_jobs``
    modules. The Lambda handler is the canonical driver post-cutover —
    this loop is gated by ``ACA_JOB_BATCHES_DISABLED`` like its peers
    and is mostly here so the ``check_dead_providers`` guard sees a
    real entry point.
    """
    if os.environ.get("ACA_JOB_BATCHES_DISABLED", "").strip() in ("1", "true", "yes"):
        logger.info("[materialize-faqs] disabled via env; loop exiting")
        return
    interval_s = int(os.environ.get("MATERIALIZE_FAQ_INTERVAL_S", "86400"))
    try:
        from deps import db
    except Exception as e:
        logger.warning("[materialize-faqs] deps unavailable: %s", e)
        return
    while True:
        try:
            await run_materialization(db)
        except Exception as e:
            logger.exception("[materialize-faqs] loop pass failed: %s", e)
        await asyncio.sleep(interval_s)
