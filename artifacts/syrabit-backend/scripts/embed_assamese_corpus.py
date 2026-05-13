"""scripts/embed_assamese_corpus.py — Task #291.

Idempotent, resumable embedder that populates Pinecone namespace ``"as"``
of the ``syrabit-ahsec`` index with multilingual-e5-large vectors for the
**entire** Assamese corpus, not just chapters.

Collections embedded (one vector per item, prefixed IDs to avoid collisions):

  ┌────────────┬──────────────────────────┬───────────────────┐
  │ source     │ Mongo collection / field │ ID prefix         │
  ├────────────┼──────────────────────────┼───────────────────┤
  │ chapter    │ chapters.content_as      │ ch:               │
  │ topic      │ topics.definition_as     │ tp:               │
  │ notes      │ chapters.notes_as        │ nt:               │
  │ mcqs       │ chapters.mcqs_as         │ mcq:              │
  │ pyqs       │ chapters.pyqs_as         │ pyq:              │
  │ important  │ chapters.important_qs_as │ iq:               │
  └────────────┴──────────────────────────┴───────────────────┘

Idempotency: each vector ID is deterministic (``<prefix><content_id>``);
metadata carries a SHA-256 ``content_hash``. On re-runs we call
``retriever.get_by_ids(ids, namespace="as")`` and skip every ID whose stored
hash matches the current content. This is namespace-correct — fetching from
the default namespace would otherwise miss every Assamese vector and force
a full re-embed each run.

Metadata schema (SEO + GEO + AEO):
  • content_id, content_type, subject_id, subject, class, chapter, topic
  • content_hash               — idempotency guard
  • lang = "as", hreflang = "as-IN"
  • canonical_url              — board/class/subject deep link with ?lang=as
  • alt_url_en                 — English sibling URL (hreflang pair)
  • geo_region = "IN-AS", geo_placename = "Assam, India"
  • jsonld_type = "LearningResource"
  • aeo_question_variants      — Assamese AEO question templates

Usage::

    PINECONE_API_KEY=… MONGO_URL=… \\
        python -m scripts.embed_assamese_corpus

Safe to run repeatedly — unchanged content is skipped server-side.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sys
from typing import Any, Iterable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger("scripts.embed_assamese_corpus")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

NAMESPACE = "as"
LANG = "as"
HREFLANG = "as-IN"
EMBED_MODEL = "multilingual-e5-large"
BATCH_SIZE = 32
BASE_URL = "https://syrabit.ai"


def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _aeo_variants(title: str) -> list[str]:
    t = (title or "").strip()
    if not t:
        return []
    return [
        f"{t} কি?",
        f"{t} ব্যাখ্যা কৰক।",
        f"{t} ৰ গুৰুত্ব কি?",
        f"{t} সম্পৰ্কে বিতং তথ্য।",
    ]


def _canonical_url(ch: dict, *, lang_qs: str = "as") -> str:
    """Build the canonical URL for a chapter using the same
    ``board/class/subject/slug`` shape ``seo_engine.py`` emits in its
    canonical/sitemap output (lines ~4020/4261/4295 of seo_engine.py).
    Re-implemented locally instead of imported because seo_engine has
    heavy startup cost (Vertex/Anthropic clients) we don't want in batch
    embedders, but the URL shape is kept in lock-step with seo_engine.

    Task #295 — for the Assamese variant (``lang_qs='as'``) we now emit
    a path-based ``/as/<board>/<class>/<subject>/<slug_as_or_slug>``
    URL instead of the legacy ``?lang=as`` query string, matching the
    new SPA route + sitemap emission. When ``chapters.slug_as`` is
    populated the chapter segment is the translated Assamese slug; if
    not yet backfilled, the English slug is reused under ``/as/`` so
    the URL still resolves via the backend fallback resolver.
    """
    from urllib.parse import quote
    en_slug = ch.get("slug") or ""
    if lang_qs == "as":
        chapter_seg = (ch.get("slug_as") or "").strip() or en_slug
        parts = [ch.get("board_slug"), ch.get("class_slug"),
                 ch.get("subject_slug"), chapter_seg]
        parts = [p for p in parts if p]
        if not parts:
            return ""
        encoded = "/".join(quote(str(p), safe="-") for p in parts)
        return f"{BASE_URL}/as/{encoded}"
    parts = [ch.get("board_slug"), ch.get("class_slug"),
             ch.get("subject_slug"), en_slug]
    parts = [p for p in parts if p]
    if not parts:
        return ""
    base = f"{BASE_URL}/{'/'.join(parts)}"
    return f"{base}?lang={lang_qs}" if lang_qs else base


async def _existing_hashes(retriever, ids: list[str]) -> dict[str, str]:
    """Namespace-aware idempotency lookup (Task #291)."""
    if not ids:
        return {}
    try:
        existing = await retriever.get_by_ids(ids, namespace=NAMESPACE)
    except TypeError:
        existing = await retriever.get_by_ids(ids)
    out: dict[str, str] = {}
    for v in existing or []:
        meta = v.get("metadata") or {}
        h = meta.get("content_hash")
        if h:
            out[v.get("id", "")] = h
    return out


def _build_meta(*, content_type: str, content_id: str, content_hash: str,
                title: str, ch: dict, subject: str, class_name: str,
                topic: str = "") -> dict[str, Any]:
    return {
        "content_id":     content_id,
        "content_type":   content_type,
        "subject_id":     ch.get("subject_id") or "",
        "subject":        subject,
        "class":          class_name,
        "chapter_id":     ch.get("id") or "",
        "chapter":        (ch.get("title") or "")[:300],
        "topic":          topic[:300],
        "title":          title[:300],
        "content_hash":   content_hash,
        "lang":           LANG,
        "hreflang":       HREFLANG,
        "canonical_url":  _canonical_url(ch, lang_qs="as"),
        "alt_url_en":     _canonical_url(ch, lang_qs=""),
        "geo_region":     "IN-AS",
        "geo_placename":  "Assam, India",
        "jsonld_type":    "LearningResource",
        "embedding_model": EMBED_MODEL,
        "aeo_question_variants": _aeo_variants(title or ch.get("title", "")),
    }


async def _resolve_subject_class(db, subject_id: str, _cache: dict) -> tuple[str, str]:
    if not subject_id:
        return "", ""
    if subject_id in _cache:
        return _cache[subject_id]
    try:
        subj = await db.subjects.find_one({"id": subject_id},
                                          {"_id": 0, "name": 1, "class_name": 1, "class": 1})
    except Exception:
        subj = None
    name = (subj or {}).get("name") or ""
    klass = (subj or {}).get("class_name") or (subj or {}).get("class") or ""
    _cache[subject_id] = (name, klass)
    return name, klass


def _augment_with_aeo(text: str, title: str) -> str:
    """Task #291 — append AEO question variants to the embedded text so
    they participate in vector similarity (not just metadata). Without this,
    a user asking "what is X?" would only match if the corpus happened to
    phrase the topic identically; with it, the variant phrasings ("explain
    X", "X কি?", etc.) become part of the chunk's semantic surface."""
    variants = _aeo_variants(title or "")
    if not variants:
        return text
    return text + "\n\n" + " ".join(variants)


def _coerce_text(v: Any) -> str:
    """Some chapter fields (e.g. ``important_questions_as``) are persisted
    as lists of dicts/strings rather than free-form strings. Flatten those
    safely into a single embedding-friendly text blob."""
    if not v:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, list):
        parts: list[str] = []
        for item in v:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                q = item.get("question") or item.get("q") or item.get("text") or ""
                a = item.get("answer") or item.get("a") or ""
                if q and a:
                    parts.append(f"{q}\n{a}")
                elif q:
                    parts.append(str(q))
                elif a:
                    parts.append(str(a))
                else:
                    parts.append(" ".join(str(x) for x in item.values() if x))
        return "\n\n".join(p for p in parts if p).strip()
    if isinstance(v, dict):
        return _coerce_text(list(v.values()))
    return str(v).strip()


def _iter_corpus_items(ch: dict) -> Iterable[tuple[str, str, str, str]]:
    """Yield (content_type, id_prefix, content_text, item_title) per chapter
    for every populated Assamese field."""
    cid = ch.get("id") or ""
    title = (ch.get("title") or "").strip()

    body = _coerce_text(ch.get("content_as"))
    if body:
        yield ("chapter", "ch:", body, title)

    notes = _coerce_text(ch.get("notes_as"))
    if notes:
        yield ("notes", "nt:", notes, f"{title} — টোকা")

    mcqs = _coerce_text(ch.get("mcqs_as"))
    if mcqs:
        yield ("mcqs", "mcq:", mcqs, f"{title} — MCQ")

    pyqs = _coerce_text(ch.get("pyqs_as"))
    if pyqs:
        yield ("pyqs", "pyq:", pyqs, f"{title} — পূৰ্বৱৰ্ষৰ প্ৰশ্ন")

    iq = _coerce_text(ch.get("important_qs_as")) or _coerce_text(ch.get("important_questions_as"))
    if iq:
        yield ("important_questions", "iq:", iq, f"{title} — গুৰুত্বপূৰ্ণ প্ৰশ্ন")


async def _embed_batch(pinecone_ai, items: list[dict]) -> list[list[float]] | None:
    try:
        # Task #291 — augment each chunk with its AEO question variants so
        # alternate phrasings ("X কি?", "X ব্যাখ্যা কৰক") become part of the
        # vector's semantic surface, not just metadata. This is what makes
        # AEO variants actually retrievable via similarity search.
        passages = [
            _augment_with_aeo(it["text"], it.get("title", ""))[:8192]
            for it in items
        ]
        return await pinecone_ai.embed(
            passages, input_type="passage", model=EMBED_MODEL,
        )
    except Exception as exc:
        logger.error("[T291][as] embed batch failed: %s", exc)
        return None


async def main() -> int:
    from deps import db
    from providers import pinecone_ai
    from retrievers.pinecone_vector import PineconeVectorRetriever, ensure_pinecone_index

    if not pinecone_ai.ENABLED:
        logger.error("Pinecone AI not configured — set PINECONE_API_KEY")
        return 2

    await ensure_pinecone_index()
    retriever = PineconeVectorRetriever()
    if not retriever.is_configured():
        logger.error("PineconeVectorRetriever not configured")
        return 2

    subject_cache: dict[str, tuple[str, str]] = {}
    counts = {"upserted": 0, "skipped": 0, "failed": 0}

    # ── Chapter-level fields (chapters + chapter-attached notes/mcqs/pyqs/iq) ─
    chapter_proj = {
        "_id": 0, "id": 1, "title": 1, "subject_id": 1, "slug": 1,
        # Task #295 — pull slug_as so _canonical_url emits the
        # path-based /as/<board>/<class>/<subject>/<slug_as> URL when
        # the backfill script has translated the chapter's slug. Without
        # this projection the field would always be missing in `ch` and
        # canonical URLs would silently fall back to the English slug.
        "slug_as": 1,
        "subject_slug": 1, "class_slug": 1, "board_slug": 1,
        "content_as": 1, "notes_as": 1, "mcqs_as": 1, "pyqs_as": 1,
        "important_qs_as": 1, "important_questions_as": 1,
    }
    chapters: list[dict] = await db.chapters.find(
        {"status": "published"}, chapter_proj,
    ).to_list(length=20000)
    logger.info("[T291][as] loaded %d published chapters", len(chapters))

    # Flatten all (chapter, content_type, item) candidates first so batching
    # spans collections instead of being serialized one collection at a time.
    pending: list[dict] = []
    for ch in chapters:
        subject, klass = await _resolve_subject_class(db, ch.get("subject_id", ""), subject_cache)
        for content_type, prefix, text, item_title in _iter_corpus_items(ch):
            cid = ch.get("id") or ""
            if not cid:
                continue
            pending.append({
                "id":           f"{prefix}{cid}",
                "content_id":   cid,
                "content_type": content_type,
                "title":        item_title,
                "ch":           ch,
                "subject":      subject,
                "class_name":   klass,
                "text":         text,
                "hash":         _sha256(text),
            })

    # ── Topic-level definitions ──────────────────────────────────────────────
    topics: list[dict] = await db.topics.find(
        {"status": "published"},
        {"_id": 0, "id": 1, "title": 1, "definition_as": 1,
         "chapter_id": 1, "subject_id": 1, "topic_slug": 1, "slug": 1},
    ).to_list(length=50000)
    logger.info("[T291][as] loaded %d published topics", len(topics))

    # Build a chapter-id → chapter doc map for canonical URL fields on topics
    ch_by_id = {c.get("id"): c for c in chapters}
    for tp in topics:
        defn = (tp.get("definition_as") or "").strip()
        tid = tp.get("id") or ""
        if not (defn and tid):
            continue
        ch = ch_by_id.get(tp.get("chapter_id", "")) or {"subject_id": tp.get("subject_id", "")}
        subject, klass = await _resolve_subject_class(db, ch.get("subject_id", ""), subject_cache)
        item_title = (tp.get("title") or "").strip() or "Topic"
        pending.append({
            "id":           f"tp:{tid}",
            "content_id":   tid,
            "content_type": "topic",
            "title":        item_title,
            "ch":           ch,
            "subject":      subject,
            "class_name":   klass,
            "text":         defn,
            "hash":         _sha256(defn),
            "topic":        item_title,
        })

    logger.info("[T291][as] %d total candidate items across all corpora", len(pending))
    if not pending:
        return 0

    # ── Batched, namespace-aware idempotent upsert loop ──────────────────────
    for i in range(0, len(pending), BATCH_SIZE):
        batch = pending[i : i + BATCH_SIZE]
        ids = [b["id"] for b in batch]
        existing = await _existing_hashes(retriever, ids)

        to_embed = [b for b in batch if existing.get(b["id"]) != b["hash"]]
        counts["skipped"] += len(batch) - len(to_embed)
        if not to_embed:
            continue

        vectors = await _embed_batch(pinecone_ai, to_embed)
        if vectors is None:
            counts["failed"] += len(to_embed)
            continue

        payload: list[dict[str, Any]] = []
        for b, vec in zip(to_embed, vectors):
            payload.append({
                "id":     b["id"],
                "values": vec,
                "metadata": _build_meta(
                    content_type=b["content_type"], content_id=b["content_id"],
                    content_hash=b["hash"], title=b["title"], ch=b["ch"],
                    subject=b["subject"], class_name=b["class_name"],
                    topic=b.get("topic", ""),
                ),
            })

        # embed-model: legacy-corpus-rebuild-script-not-in-prod-chain
        result = await retriever.upsert(payload, namespace=NAMESPACE)
        counts["upserted"] += int(result.get("upserted", 0))
        logger.info("[T291][as] batch %d–%d → upserted=%d skipped=%d",
                    i, i + len(batch), result.get("upserted", 0),
                    len(batch) - len(to_embed))

    logger.info("[T291][as] DONE — upserted=%d skipped(unchanged)=%d failed=%d",
                counts["upserted"], counts["skipped"], counts["failed"])

    # ── Coverage guard: warn when chapters lack Assamese alternates ──────
    # After a successful run we expect (almost) every published English
    # chapter to have a sibling Assamese variant. Surface gaps so SEO can
    # request translations / hide hreflang stubs.
    try:
        en_total = await db.chapters.count_documents({"status": "published"})
        as_total = await db.chapters.count_documents(
            {"status": "published", "content_as": {"$exists": True, "$ne": ""}}
        )
        missing = en_total - as_total
        if missing > 0:
            logger.warning(
                "[T291][as] coverage gap — %d/%d published chapters have no "
                "content_as. hreflang='as-IN' alternates for those URLs will "
                "404-equivalently when crawled. Translate the missing chapters "
                "or suppress their hreflang.",
                missing, en_total,
            )
        else:
            logger.info("[T291][as] coverage OK — %d/%d chapters have Assamese alternates",
                        as_total, en_total)
    except Exception as exc:
        logger.debug("[T291][as] coverage guard skipped: %s", exc)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
