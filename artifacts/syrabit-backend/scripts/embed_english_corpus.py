"""scripts/embed_english_corpus.py — Task #291.

Sibling of ``embed_assamese_corpus.py`` for the English corpus. Populates
Pinecone namespace ``"en"`` with multilingual-e5-large vectors for every
published chapter, topic, and chapter-attached notes/MCQs/PYQs/important
questions. Same metadata schema, same namespace-aware idempotency.

Usage::

    PINECONE_API_KEY=… MONGO_URL=… \\
        python -m scripts.embed_english_corpus
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sys
from typing import Any, Iterable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger("scripts.embed_english_corpus")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

NAMESPACE = "en"
LANG = "en"
HREFLANG = "en-IN"
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
        f"What is {t}?",
        f"Explain {t}.",
        f"Why is {t} important?",
        f"Detailed notes on {t}.",
    ]


def _canonical_url(ch: dict, *, lang_qs: str = "") -> str:
    """Mirrors seo_engine.py's canonical URL shape
    (``board/class/subject/slug``) — kept identical so RAG hits resolve to
    the same URL the public chapter pages render."""
    parts = [ch.get("board_slug"), ch.get("class_slug"),
             ch.get("subject_slug"), ch.get("slug")]
    parts = [p for p in parts if p]
    if not parts:
        return ""
    base = f"{BASE_URL}/{'/'.join(parts)}"
    return f"{base}?lang={lang_qs}" if lang_qs else base


async def _existing_hashes(retriever, ids: list[str]) -> dict[str, str]:
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
        "canonical_url":  _canonical_url(ch),
        "alt_url_as":     _canonical_url(ch, lang_qs="as"),
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
    alternate phrasings participate in vector similarity, not metadata
    alone."""
    variants = _aeo_variants(title or "")
    if not variants:
        return text
    return text + "\n\n" + " ".join(variants)


def _coerce_text(v: Any) -> str:
    """Some chapter fields (e.g. ``important_questions``) are persisted as
    lists of dicts/strings rather than free-form strings. Flatten those
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
    cid = ch.get("id") or ""
    title = (ch.get("title") or "").strip()

    body = _coerce_text(ch.get("content"))
    if body:
        yield ("chapter", "ch:", body, title)

    notes = _coerce_text(ch.get("notes"))
    if notes:
        yield ("notes", "nt:", notes, f"{title} — Notes")

    mcqs = _coerce_text(ch.get("mcqs"))
    if mcqs:
        yield ("mcqs", "mcq:", mcqs, f"{title} — MCQs")

    pyqs = _coerce_text(ch.get("pyqs"))
    if pyqs:
        yield ("pyqs", "pyq:", pyqs, f"{title} — Previous-year questions")

    iq = _coerce_text(ch.get("important_qs")) or _coerce_text(ch.get("important_questions"))
    if iq:
        yield ("important_questions", "iq:", iq, f"{title} — Important questions")


async def _embed_batch(pinecone_ai, items: list[dict]) -> list[list[float]] | None:
    try:
        # Task #291 — augment each chunk with its AEO question variants so
        # alternate phrasings ("what is X", "explain X") become part of the
        # vector's semantic surface, making AEO variants actually retrievable
        # via similarity search rather than being metadata-only.
        passages = [
            _augment_with_aeo(it["text"], it.get("title", ""))[:8192]
            for it in items
        ]
        return await pinecone_ai.embed(
            passages, input_type="passage", model=EMBED_MODEL,
        )
    except Exception as exc:
        logger.error("[T291][en] embed batch failed: %s", exc)
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

    chapter_proj = {
        "_id": 0, "id": 1, "title": 1, "subject_id": 1, "slug": 1,
        "subject_slug": 1, "class_slug": 1, "board_slug": 1,
        "content": 1, "notes": 1, "mcqs": 1, "pyqs": 1,
        "important_qs": 1, "important_questions": 1,
    }
    chapters: list[dict] = await db.chapters.find(
        {"status": "published"}, chapter_proj,
    ).to_list(length=20000)
    logger.info("[T291][en] loaded %d published chapters", len(chapters))

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

    topics: list[dict] = await db.topics.find(
        {"status": "published"},
        {"_id": 0, "id": 1, "title": 1, "definition": 1,
         "chapter_id": 1, "subject_id": 1, "topic_slug": 1, "slug": 1},
    ).to_list(length=50000)
    logger.info("[T291][en] loaded %d published topics", len(topics))

    ch_by_id = {c.get("id"): c for c in chapters}
    for tp in topics:
        defn = (tp.get("definition") or "").strip()
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

    logger.info("[T291][en] %d total candidate items across all corpora", len(pending))
    if not pending:
        return 0

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

        result = await retriever.upsert(payload, namespace=NAMESPACE)
        counts["upserted"] += int(result.get("upserted", 0))
        logger.info("[T291][en] batch %d–%d → upserted=%d skipped=%d",
                    i, i + len(batch), result.get("upserted", 0),
                    len(batch) - len(to_embed))

    logger.info("[T291][en] DONE — upserted=%d skipped(unchanged)=%d failed=%d",
                counts["upserted"], counts["skipped"], counts["failed"])
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
