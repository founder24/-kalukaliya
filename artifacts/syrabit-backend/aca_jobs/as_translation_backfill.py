"""aca_jobs.as_translation_backfill — Task #465 backfill driver.

Walks every SSR-feeding document in `subjects`, `chapters`, `seo_pages`,
and `pyq_html_pages`, translates the configured English fields into
Assamese, and writes the result to the matching ``<field>_as`` sibling
so `seo_engine._localized()` returns real Assamese instead of falling
through to the English copy.

Translation path
----------------
Per V4 §4 (Assamese path) and the post-#15 amendment that scoped
Sarvam to ``assamese_rag_chat`` only, the surviving translate chain is:

    Workers-AI IndicTrans2 (primary)  →  Vertex / Gemini polish

Implemented in :func:`routes.ai_chat._assamese_translate_gemini_main_sarvam_polish`,
which we call directly so this job inherits the existing redis cache,
script validation (rejects Devanagari output that CF sometimes returns
for ``asm_Beng``), and the documented failure semantics — no silent
fallbacks, per V4 §12.

Resumability
------------
State lives in the ``as_translation_state`` Mongo collection, one doc
per collection (``_id`` = collection name). A killed run picks up at
``last_processed_id``. A doc whose ``_as`` fields already contain
≥ ``MIN_AS_SCRIPT_RATIO`` Assamese characters is treated as already-
translated and skipped, so a re-run is a cheap idempotent sweep.

A content hash (``<field>_as_src_hash``) is also written alongside each
``<field>_as`` field so a future English edit can be detected (next
backfill pass re-translates whenever the hash drifts).
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import hashlib
import logging
import os
from typing import Any, Iterable, Optional

logger = logging.getLogger("aca_jobs.as_translation_backfill")

STATE_COLLECTION = "as_translation_state"

# Per-collection field map: which English fields need a sibling `_as`.
# Conservative — only covers the fields the SSR `_localized()` helper in
# `seo_engine.py` actually reads. Extending this map is a one-line
# change; the rest of the driver is generic.
FIELD_MAP: dict[str, list[str]] = {
    "subjects":       ["name", "description"],
    "chapters":       ["title", "description", "content"],
    "seo_pages":      ["title", "topic_title", "meta_description", "content_html"],
    "pyq_html_pages": ["title", "meta_description", "content_html"],
}

# Empty / very-short fields aren't worth a translate round-trip.
MIN_SOURCE_CHARS = 8

# Threshold for "already translated": the destination field must be
# non-empty and ≥ this fraction of letters must be in the Bengali block
# (Assamese script, U+0980–U+09FF). Anything below means the field is
# either empty or still leaking English prose, so re-translate.
MIN_AS_SCRIPT_RATIO = 0.60

# Per-pass tunables (env-overridable so ops can throttle without a
# deploy if Sarvam / Workers-AI rate-limits start firing).
DEFAULT_PER_CALL_LIMIT = int(
    os.environ.get("AS_BACKFILL_PER_CALL_LIMIT", "200") or "200"
)
DEFAULT_BATCH_SIZE = int(os.environ.get("AS_BACKFILL_BATCH_SIZE", "5") or "5")
INTER_DOC_SLEEP_S = float(
    os.environ.get("AS_BACKFILL_INTER_DOC_SLEEP_S", "0.25") or "0.25"
)
TRANSLATE_TIMEOUT_S = float(
    os.environ.get("AS_BACKFILL_TRANSLATE_TIMEOUT_S", "45") or "45"
)
# Long Assamese pages get split into chunks before translate. IndicTrans2
# happily handles a couple of KB but the polish step starts truncating
# beyond ~4 KB, so split well below that.
MAX_CHUNK_CHARS = int(os.environ.get("AS_BACKFILL_MAX_CHUNK_CHARS", "1500") or "1500")

_run_lock = asyncio.Lock()


# ── Script-detection helpers ────────────────────────────────────────────────

def _bengali_letter_ratio(text: str) -> float:
    """Fraction of letters in *text* that fall inside the Bengali Unicode
    block (Assamese script). Returns 0.0 for empty / whitespace input."""
    if not text:
        return 0.0
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
    return bengali / total


def _hash_source(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def _doc_needs_translation(doc: dict, fields: list[str]) -> list[tuple[str, str]]:
    """Return ``[(field, source_text), …]`` for every field whose ``_as``
    sibling is missing / empty / not-actually-Assamese, or whose source
    English text has changed since the last translation."""
    pending: list[tuple[str, str]] = []
    for field in fields:
        src = (doc.get(field) or "")
        if not isinstance(src, str):
            continue
        src = src.strip()
        if len(src) < MIN_SOURCE_CHARS:
            continue
        dst = (doc.get(f"{field}_as") or "")
        if not isinstance(dst, str):
            dst = ""
        # Already-translated check: destination must contain enough
        # Assamese script. We sample only the first 1 KB so very long
        # mostly-Assamese pages aren't dragged below the threshold by a
        # trailing English citation block.
        if dst and _bengali_letter_ratio(dst[:1024]) >= MIN_AS_SCRIPT_RATIO:
            # Re-translate iff the source English text changed since we
            # last wrote the destination.
            stored_hash = doc.get(f"{field}_as_src_hash") or ""
            if stored_hash == _hash_source(src):
                continue
        pending.append((field, src))
    return pending


# ── Translate primitive (delegates to the V4 §4 Assamese chain) ─────────────

def _split_for_translate(text: str, max_chunk: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split *text* into ≤``max_chunk`` segments along paragraph then
    sentence boundaries so the polish step doesn't truncate."""
    if len(text) <= max_chunk:
        return [text]
    chunks: list[str] = []
    buf = ""
    for line in text.split("\n"):
        if len(line) > max_chunk:
            if buf:
                chunks.append(buf)
                buf = ""
            for j in range(0, len(line), max_chunk):
                chunks.append(line[j:j + max_chunk])
            continue
        if len(buf) + len(line) + 1 > max_chunk and buf:
            chunks.append(buf)
            buf = line
        else:
            buf = (buf + "\n" + line) if buf else line
    if buf:
        chunks.append(buf)
    return chunks


async def _translate_to_assamese(text: str) -> str:
    """Run *text* through the V4 §4 Assamese translate chain.

    Returns the translated string, or "" if every tier failed (caller
    is expected to fail loud per V4 §12 — we do NOT silently store the
    English original under the ``_as`` sibling).
    """
    src = (text or "").strip()
    if not src:
        return ""
    # Lazy import — keeps `aca_jobs` importable during alembic-style
    # migration tooling that doesn't want to load the chat router.
    from routes.ai_chat import _assamese_translate_gemini_main_sarvam_polish

    chunks = _split_for_translate(src)
    out_parts: list[str] = []
    for chunk in chunks:
        try:
            translated = await asyncio.wait_for(
                _assamese_translate_gemini_main_sarvam_polish(
                    chunk, target_lang_code="as-IN",
                ),
                timeout=TRANSLATE_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "[as_translation_backfill] translate timeout after %ss "
                "(chunk_len=%d)", TRANSLATE_TIMEOUT_S, len(chunk),
            )
            return ""
        translated = (translated or "").strip()
        if not translated:
            return ""
        out_parts.append(translated)
    return "\n".join(out_parts).strip()


# ── State helpers ───────────────────────────────────────────────────────────

async def _load_state(db: Any, collection: str) -> dict:
    try:
        doc = await db[STATE_COLLECTION].find_one({"_id": collection})
    except Exception as exc:
        logger.warning("[as_translation_backfill] state read failed: %s", exc)
        doc = None
    return doc or {}


async def _write_state(db: Any, collection: str, patch: dict) -> None:
    patch = dict(patch)
    patch["updated_at"] = _dt.datetime.utcnow()
    try:
        await db[STATE_COLLECTION].update_one(
            {"_id": collection},
            {"$set": patch, "$setOnInsert": {"_id": collection}},
            upsert=True,
        )
    except Exception as exc:
        logger.warning("[as_translation_backfill] state write failed: %s", exc)


async def _count_remaining(db: Any, collection: str) -> int:
    """Cheap upper-bound estimate: docs that don't yet carry every
    expected ``_as`` sibling. Exact counting would require scanning the
    body of each doc to check the script ratio, which is too expensive
    to do repeatedly. The driver itself does the precise check per doc."""
    fields = FIELD_MAP.get(collection, [])
    if not fields:
        return 0
    or_clauses = []
    for f in fields:
        or_clauses.append({f"{f}_as": {"$in": [None, ""]}})
    try:
        return int(await db[collection].count_documents({"$or": or_clauses}))
    except Exception as exc:
        logger.debug(
            "[as_translation_backfill] count_remaining(%s) failed: %s",
            collection, exc,
        )
        return 0


async def get_progress(db: Any) -> dict:
    """Admin-facing progress payload across every backfilled collection."""
    out = {
        "collections": {},
        "fetched_at":  _dt.datetime.utcnow().isoformat() + "Z",
    }
    for collection in FIELD_MAP:
        state = await _load_state(db, collection)
        remaining = await _count_remaining(db, collection)
        try:
            total = int(await db[collection].count_documents({}))
        except Exception:
            total = 0
        done = max(total - remaining, 0)
        pct = round((done / total) * 100.0, 2) if total else 0.0
        out["collections"][collection] = {
            "fields":           FIELD_MAP[collection],
            "total":            total,
            "remaining":        remaining,
            "translated":       done,
            "percent":          pct,
            "running":          bool(state.get("running")),
            "last_processed_id": state.get("last_processed_id"),
            "started_at":       state.get("started_at"),
            "updated_at":       state.get("updated_at"),
            "completed_at":     state.get("completed_at"),
            "last_run":         state.get("last_run"),
        }
    return out


# ── Main backfill loop ──────────────────────────────────────────────────────

def _id_filter(after_id: Any | None) -> dict:
    return {"_id": {"$gt": after_id}} if after_id is not None else {}


async def _process_one_collection(
    db: Any,
    collection: str,
    *,
    max_docs: int,
    batch_size: int,
) -> dict:
    fields = FIELD_MAP[collection]
    state = await _load_state(db, collection)
    last_id = state.get("last_processed_id")
    if state.get("completed_at") and not state.get("running"):
        last_id = None  # Restart sweep — picks up newly written docs.

    started = _dt.datetime.utcnow()
    await _write_state(db, collection, {
        "running":      True,
        "started_at":   started,
        "completed_at": None,
    })

    processed = translated = failed = skipped = 0
    try:
        from pymongo import UpdateOne
    except Exception as exc:  # pragma: no cover
        logger.error("[as_translation_backfill] pymongo import failed: %s", exc)
        await _write_state(db, collection, {"running": False})
        return {"error": "pymongo_unavailable", "collection": collection}

    while processed < max_docs:
        take = min(batch_size, max_docs - processed)
        cursor = (
            db[collection]
            .find(_id_filter(last_id))
            .sort("_id", 1)
            .limit(take)
        )
        try:
            batch = await cursor.to_list(length=take)
        except Exception as exc:
            logger.warning(
                "[as_translation_backfill] cursor read on %s failed: %s",
                collection, exc,
            )
            break
        if not batch:
            await _write_state(db, collection, {
                "running":      False,
                "completed_at": _dt.datetime.utcnow(),
            })
            break

        ops: list = []
        for doc in batch:
            processed += 1
            last_id = doc["_id"]
            pending = _doc_needs_translation(doc, fields)
            if not pending:
                skipped += 1
                continue

            update_set: dict[str, Any] = {}
            doc_failed = False
            for field, src in pending:
                try:
                    translated_text = await _translate_to_assamese(src)
                except Exception as exc:
                    logger.warning(
                        "[as_translation_backfill] translate raised on %s.%s: %s",
                        collection, field, exc,
                    )
                    translated_text = ""
                if not translated_text:
                    doc_failed = True
                    logger.info(
                        "[as_translation_backfill] empty translation — "
                        "skipping %s._id=%s field=%s",
                        collection, last_id, field,
                    )
                    continue
                if _bengali_letter_ratio(translated_text[:1024]) < MIN_AS_SCRIPT_RATIO:
                    doc_failed = True
                    logger.info(
                        "[as_translation_backfill] translation insufficient "
                        "Assamese script ratio — skipping %s._id=%s field=%s",
                        collection, last_id, field,
                    )
                    continue
                update_set[f"{field}_as"] = translated_text
                update_set[f"{field}_as_src_hash"] = _hash_source(src)
                update_set[f"{field}_as_translated_at"] = _dt.datetime.utcnow().isoformat() + "Z"

            if update_set:
                ops.append(UpdateOne(
                    {"_id": doc["_id"]},
                    {"$set": update_set},
                    upsert=False,
                ))
                if doc_failed:
                    failed += 1
                else:
                    translated += 1
            else:
                failed += 1

            if INTER_DOC_SLEEP_S > 0:
                await asyncio.sleep(INTER_DOC_SLEEP_S)

        if ops:
            try:
                await db[collection].bulk_write(ops, ordered=False)
            except Exception as exc:
                logger.warning(
                    "[as_translation_backfill] bulk_write on %s failed: %s",
                    collection, exc,
                )

        await _write_state(db, collection, {
            "running":            True,
            "last_processed_id":  last_id,
            "last_run_processed": processed,
            "last_run_translated": translated,
            "last_run_failed":     failed,
            "last_run_skipped":    skipped,
        })

    duration = (_dt.datetime.utcnow() - started).total_seconds()
    summary = {
        "collection":  collection,
        "processed":   processed,
        "translated":  translated,
        "failed":      failed,
        "skipped":     skipped,
        "duration_s":  round(duration, 2),
        "remaining":   await _count_remaining(db, collection),
    }
    await _write_state(db, collection, {
        "running":  False,
        "last_run": {**summary, "finished_at": _dt.datetime.utcnow()},
    })
    logger.info("[as_translation_backfill] %s pass complete: %s", collection, summary)
    return summary


async def run_backfill(
    db: Any,
    *,
    collections: Optional[Iterable[str]] = None,
    max_docs: int = DEFAULT_PER_CALL_LIMIT,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict:
    """Run one pass of the backfill across the requested *collections*
    (defaults to every key in :data:`FIELD_MAP`).

    Returns ``{"results": [<per-collection summary>, …]}`` (or
    ``{"skipped": "already_running"}`` if another pass is in flight).
    """
    if _run_lock.locked():
        return {"skipped": "already_running"}
    async with _run_lock:
        targets = list(collections) if collections else list(FIELD_MAP.keys())
        bad = [c for c in targets if c not in FIELD_MAP]
        if bad:
            return {"error": "unknown_collection", "unknown": bad}
        max_docs = max(1, int(max_docs))
        batch_size = max(1, int(batch_size))
        results = []
        for collection in targets:
            summary = await _process_one_collection(
                db, collection,
                max_docs=max_docs,
                batch_size=batch_size,
            )
            results.append(summary)
        return {"results": results}
