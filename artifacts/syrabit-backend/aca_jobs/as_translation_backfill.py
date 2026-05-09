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
#
# Task #45 — bumped from 0.60 → 0.85 so the backfill stops accepting
# heavily code-mixed output that lets the row count look healthy while
# real Assamese coverage stays low. The same constant is used in two
# places (the skip-decision in :func:`_doc_needs_translation` and the
# accept-decision in :func:`_process_one_collection`), which means the
# bump doubles as the "ratio drift" detector the task spec calls for:
# any previously-accepted row whose stored ``_as`` text falls below
# 0.85 now fails the skip check on the next pass and gets re-queued
# automatically — no separate drift bookkeeping required because the
# threshold IS the drift line. The matching coverage endpoint
# (`/api/health/corpus/assamese`) and CloudWatch alarm
# (`assamese-corpus-coverage-low`) measure progress against this same
# 0.85 gate via the persisted ``<field>_as_script_ratio``.
MIN_AS_SCRIPT_RATIO = 0.85

# Coverage SLO for the four largest collections — admin tile renders
# this as a target line and the CloudWatch alarm fires when any
# tracked collection's coverage drops below it for two consecutive
# nightly runs. Lives next to MIN_AS_SCRIPT_RATIO so anyone bumping
# the gate sees both numbers together.
COVERAGE_TARGET_RATIO = 0.85
COVERAGE_ALARM_FLOOR  = 0.80
ASSAMESE_BACKFILL_RUNS_COLLECTION = "assamese_backfill_runs"

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
    # Task #45 — surface accept/reject counts + reject reasons in the
    # run report so the admin tile can render *why* a collection's
    # coverage isn't moving (e.g. translator returning low-ratio output
    # vs translator timing out vs source already passes).
    reject_reasons: dict[str, int] = {}
    accepted = rejected_low_ratio = rejected_empty = rejected_exception = 0
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
                translate_exc = False
                try:
                    translated_text = await _translate_to_assamese(src)
                except Exception as exc:
                    logger.warning(
                        "[as_translation_backfill] translate raised on %s.%s: %s",
                        collection, field, exc,
                    )
                    translated_text = ""
                    translate_exc = True
                if not translated_text:
                    doc_failed = True
                    reason = "translator_exception" if translate_exc else "empty_translation"
                    reject_reasons[reason] = reject_reasons.get(reason, 0) + 1
                    if translate_exc:
                        rejected_exception += 1
                    else:
                        rejected_empty += 1
                    logger.info(
                        "[as_translation_backfill] %s — skipping %s._id=%s field=%s",
                        reason, collection, last_id, field,
                    )
                    continue
                ratio = _bengali_letter_ratio(translated_text[:1024])
                if ratio < MIN_AS_SCRIPT_RATIO:
                    doc_failed = True
                    rejected_low_ratio += 1
                    reject_reasons["script_ratio_below_threshold"] = (
                        reject_reasons.get("script_ratio_below_threshold", 0) + 1
                    )
                    logger.info(
                        "[as_translation_backfill] translation insufficient "
                        "Assamese script ratio (%.2f < %.2f) — "
                        "skipping %s._id=%s field=%s",
                        ratio, MIN_AS_SCRIPT_RATIO, collection, last_id, field,
                    )
                    continue
                accepted += 1
                update_set[f"{field}_as"] = translated_text
                update_set[f"{field}_as_src_hash"] = _hash_source(src)
                # Task #45 — persist the script ratio so the coverage
                # endpoint can `$match` per-collection without scanning
                # every doc body. Rounded to 4dp to keep the BSON cheap.
                update_set[f"{field}_as_script_ratio"] = round(ratio, 4)
                update_set[f"{field}_as_translated_at"] = _dt.datetime.utcnow().isoformat() + "Z"
                # Task #560 round-3 — per-doc driver tag so the
                # reconciliation script can do real per-document key
                # parity + hash parity, not just last_run aggregate
                # counts. The `_as_src_hash` already gives us a stable
                # output fingerprint to compare across the two drivers
                # for the same `(collection, _id, field)` key.
                update_set[f"{field}_as_translated_by"] = os.environ.get(
                    "BATCH_JOB_DRIVER", "aca",
                )

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
        # Task #45 — accept/reject breakdown for the admin tile.
        "accepted":             accepted,
        "rejected_low_ratio":   rejected_low_ratio,
        "rejected_empty":       rejected_empty,
        "rejected_exception":   rejected_exception,
        "reject_reasons":       reject_reasons,
    }
    # Task #560 — stamp the driver discriminator the shadow-mode
    # reconciliation script (`scripts/lambda_aca_shadow_reconcile.py`)
    # reads to split per-driver outcomes during the 7-day cutover
    # window. The Lambda wrapper sets `BATCH_JOB_DRIVER=lambda` before
    # calling `run_backfill`; the in-process ACA loop leaves it unset
    # so it defaults to `aca`.
    await _write_state(db, collection, {
        "running":  False,
        "last_run": {
            **summary,
            "finished_at": _dt.datetime.utcnow(),
            "driver":      os.environ.get("BATCH_JOB_DRIVER", "aca"),
        },
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
        run_started = _dt.datetime.utcnow()
        results = []
        for collection in targets:
            summary = await _process_one_collection(
                db, collection,
                max_docs=max_docs,
                batch_size=batch_size,
            )
            results.append(summary)
        # Task #45 — coverage snapshot + per-run report persisted to
        # ``db.assamese_backfill_runs`` so the admin tile can render
        # the latest accept/reject breakdown side-by-side with the
        # current per-collection coverage ratio. The Lambda handler
        # additionally pushes ``Syrabit/Corpus::AssameseCoverage`` to
        # CloudWatch so the ``assamese-corpus-coverage-low`` alarm can
        # fire on two consecutive sub-floor passes.
        coverage = {}
        try:
            coverage = await compute_assamese_coverage(db)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "[as_translation_backfill] coverage snapshot failed: %s", exc,
            )
        run_doc = {
            "started_at":  run_started,
            "finished_at": _dt.datetime.utcnow(),
            "driver":      os.environ.get("BATCH_JOB_DRIVER", "aca"),
            "min_script_ratio":     MIN_AS_SCRIPT_RATIO,
            "coverage_target":      COVERAGE_TARGET_RATIO,
            "coverage_alarm_floor": COVERAGE_ALARM_FLOOR,
            "results":     results,
            "coverage":    coverage,
        }
        try:
            await db[ASSAMESE_BACKFILL_RUNS_COLLECTION].insert_one(dict(run_doc))
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "[as_translation_backfill] run report persist failed: %s", exc,
            )
        return {"results": results, "coverage": coverage}


# ── Coverage helpers (Task #45) ─────────────────────────────────────────────

# Fields the coverage gate keys off, per collection. Picked as the
# *primary* user-facing field per collection — `_doc_needs_translation`
# requires *every* mapped field to pass, but for the dashboard we want
# a single ratio per collection and the primary field is the one
# students actually read in the Assamese SSR view.
COVERAGE_PRIMARY_FIELD: dict[str, str] = {
    "subjects":       "name",
    "chapters":       "title",
    "seo_pages":      "title",
    "pyq_html_pages": "title",
}

# Materialized content types that ship Assamese variants through the
# Redis-backed ``ai_input_cache`` rather than a Mongo source +
# ``<field>_as`` sibling. They are listed in the coverage payload so the
# admin tile can render them next to the four Mongo-backed collections,
# but their ratio is sourced from the deterministic-cache surface
# (``/api/health/cache``) — there is no English-source-with-_as_sibling
# shape for them and so the 0.85 script-ratio gate does not apply.
# When the underlying Mongo collection is missing (the common case in
# this stack) the row degrades to ``total_docs=0`` with
# ``status="ai_input_cache_only"`` so the dashboard can render an
# explanatory pill instead of a misleading 0.0 ratio that would page
# on-call.
COVERAGE_AI_CACHE_CONTENT_TYPES: tuple[str, ...] = (
    "mcq",
    "flashcards",
    "definitions",
)


# How many "ratio missing" docs to compute on-the-fly per coverage call.
# Bounded so the admin endpoint stays fast even on the first call after
# the Task #45 deploy when historical translations have no persisted
# `_as_script_ratio` yet.
COVERAGE_INLINE_BACKFILL_LIMIT = int(
    os.environ.get("AS_COVERAGE_INLINE_BACKFILL_LIMIT", "2000") or "2000"
)


async def _coverage_for_collection(db: Any, collection: str) -> dict:
    """Compute per-collection Assamese coverage against the 0.85 gate.

    Counts:
      total_docs      = docs whose primary English field has ≥
                        ``MIN_SOURCE_CHARS`` characters (the only docs
                        the backfill ever considers).
      translated_docs = subset of ``total_docs`` whose ``<field>_as_script_ratio``
                        is ≥ ``MIN_AS_SCRIPT_RATIO``.
      ratio           = translated_docs / total_docs, 0.0 when total = 0.

    Task #45 — first-call undercount fix
    ------------------------------------
    Historical rows that were translated under the old 0.60 gate carry
    a ``<field>_as`` value but no ``<field>_as_script_ratio`` (the
    ratio field was introduced by this task). Without a fallback the
    admin tile would under-report on day one and only climb as the
    nightly job re-evaluated each row. To make coverage reflect the
    actual on-disk corpus from the first call, we opportunistically
    compute the ratio for up to ``COVERAGE_INLINE_BACKFILL_LIMIT``
    docs per call that have a non-empty ``_as`` value but no
    persisted ratio, persist the result, and fold the qualifying
    rows into the translated count for this call.
    """
    field = COVERAGE_PRIMARY_FIELD.get(collection)
    if not field:
        return {
            "collection": collection,
            "field":      None,
            "total_docs": 0,
            "translated_docs": 0,
            "ratio":      0.0,
            "inline_backfilled": 0,
            "inline_backfill_pending": 0,
        }
    # Mirror `_doc_needs_translation`'s eligibility: only docs whose
    # primary English field is a string of ≥ MIN_SOURCE_CHARS characters
    # are ever translated, so the coverage denominator must use the
    # same gate. Without `$strLenCP` the count includes empty / very
    # short rows that the backfill correctly skips, dragging the
    # ratio down and tripping false alarms.
    total_filter = {
        field: {"$type": "string"},
        "$expr": {
            "$gte": [{"$strLenCP": f"${field}"}, MIN_SOURCE_CHARS],
        },
    }
    translated_filter = {
        **total_filter,
        f"{field}_as_script_ratio": {"$gte": MIN_AS_SCRIPT_RATIO},
    }
    try:
        total = int(await db[collection].count_documents(total_filter))
    except Exception as exc:
        logger.debug(
            "[as_translation_backfill] coverage total count(%s) failed: %s",
            collection, exc,
        )
        total = 0
    try:
        translated = int(
            await db[collection].count_documents(translated_filter)
        )
    except Exception as exc:
        logger.debug(
            "[as_translation_backfill] coverage translated count(%s) failed: %s",
            collection, exc,
        )
        translated = 0

    # Opportunistic inline ratio backfill for historical rows.
    inline_backfilled = 0
    inline_pending = 0
    missing_filter = {
        **total_filter,
        f"{field}_as": {"$type": "string", "$ne": ""},
        f"{field}_as_script_ratio": {"$exists": False},
    }
    try:
        inline_pending = int(
            await db[collection].count_documents(missing_filter)
        )
    except Exception:
        inline_pending = 0
    if inline_pending and COVERAGE_INLINE_BACKFILL_LIMIT > 0:
        try:
            from pymongo import UpdateOne  # type: ignore
        except Exception:  # pragma: no cover
            UpdateOne = None  # type: ignore
        try:
            cursor = (
                db[collection]
                .find(missing_filter, projection={"_id": 1, f"{field}_as": 1})
                .limit(COVERAGE_INLINE_BACKFILL_LIMIT)
            )
            ops = []
            inline_translated = 0
            scanned = 0
            async for doc in cursor:
                scanned += 1
                as_text = doc.get(f"{field}_as") or ""
                if not isinstance(as_text, str):
                    continue
                ratio_val = round(_bengali_letter_ratio(as_text[:1024]), 4)
                if UpdateOne is not None:
                    ops.append(UpdateOne(
                        {"_id": doc["_id"]},
                        {"$set": {f"{field}_as_script_ratio": ratio_val}},
                    ))
                if ratio_val >= MIN_AS_SCRIPT_RATIO:
                    inline_translated += 1
            if ops:
                try:
                    await db[collection].bulk_write(ops, ordered=False)
                except Exception as exc:
                    logger.debug(
                        "[as_translation_backfill] inline ratio bulk_write(%s) failed: %s",
                        collection, exc,
                    )
            inline_backfilled = scanned
            translated += inline_translated
            inline_pending = max(inline_pending - scanned, 0)
        except Exception as exc:
            logger.debug(
                "[as_translation_backfill] inline coverage backfill(%s) failed: %s",
                collection, exc,
            )

    ratio = round((translated / total), 4) if total else 0.0
    return {
        "collection":              collection,
        "field":                   field,
        "total_docs":              total,
        "translated_docs":         translated,
        "ratio":                   ratio,
        "inline_backfilled":       inline_backfilled,
        "inline_backfill_pending": inline_pending,
    }


async def _coverage_for_ai_cache_content_type(db: Any, content_type: str) -> dict:
    """Best-effort coverage row for a materialized content type.

    These types (mcq / flashcards / definitions) ride on the Redis-backed
    ``ai_input_cache`` so there is no Mongo source field + ``_as``
    sibling to gate on. If a same-named Mongo collection exists in this
    deployment we report a simple `<docs with non-empty `*_as` text>` /
    `<total>` count so the admin tile has a number to render; otherwise
    the row degrades to ``status="ai_input_cache_only"`` so the UI can
    point operators at ``/api/health/cache`` for the deterministic-cache
    hit ratio that actually governs Assamese delivery for these types.
    """
    try:
        names = await db.list_collection_names()
    except Exception:
        names = []
    if content_type not in names:
        return {
            "collection":      content_type,
            "field":           None,
            "total_docs":      0,
            "translated_docs": 0,
            "ratio":           0.0,
            "status":          "ai_input_cache_only",
            "note":            (
                "No Mongo collection — this content type lives in the "
                "Redis-backed ai_input_cache. Track via /api/health/cache."
            ),
        }
    try:
        total = int(await db[content_type].count_documents({}))
    except Exception:
        total = 0
    translated = 0
    if total:
        # Heuristic: any string field on the doc whose name ends in
        # `_as` and is non-empty counts as a translated leg.
        try:
            translated = int(await db[content_type].count_documents({
                "$or": [
                    {"text_as":    {"$type": "string", "$ne": ""}},
                    {"content_as": {"$type": "string", "$ne": ""}},
                    {"answer_as":  {"$type": "string", "$ne": ""}},
                ],
            }))
        except Exception:
            translated = 0
    ratio = round((translated / total), 4) if total else 0.0
    return {
        "collection":      content_type,
        "field":           "<any *_as>",
        "total_docs":      total,
        "translated_docs": translated,
        "ratio":           ratio,
        "status":          "mongo_collection_present",
    }


async def compute_assamese_coverage(db: Any) -> dict:
    """Return per-collection Assamese coverage + the overall ratio.

    The four backfill-owned collections in ``COVERAGE_PRIMARY_FIELD``
    are the ones the 0.85 script-ratio gate measures and the only
    rows folded into ``overall_ratio`` (the gate doesn't apply to
    materialized content types). The three
    ``COVERAGE_AI_CACHE_CONTENT_TYPES`` rows are appended for the
    admin tile so ops can see them next to the gated four; their
    real observability surface is ``/api/health/cache``.
    """
    rows: list[dict] = []
    for collection in COVERAGE_PRIMARY_FIELD:
        rows.append(await _coverage_for_collection(db, collection))
    gated_total = sum(r["total_docs"] for r in rows)
    gated_translated = sum(r["translated_docs"] for r in rows)
    overall = round((gated_translated / gated_total), 4) if gated_total else 0.0

    for content_type in COVERAGE_AI_CACHE_CONTENT_TYPES:
        rows.append(await _coverage_for_ai_cache_content_type(db, content_type))

    return {
        "collections":          rows,
        "overall_ratio":        overall,
        "gated_collections":    list(COVERAGE_PRIMARY_FIELD.keys()),
        "ai_cache_collections": list(COVERAGE_AI_CACHE_CONTENT_TYPES),
        "target_ratio":         COVERAGE_TARGET_RATIO,
        "alarm_floor":          COVERAGE_ALARM_FLOOR,
        "min_script_ratio":     MIN_AS_SCRIPT_RATIO,
        "computed_at":          _dt.datetime.utcnow().isoformat() + "Z",
    }


async def latest_run_report(db: Any) -> Optional[dict]:
    """Return the most recent ``assamese_backfill_runs`` doc (or None)."""
    try:
        doc = await db[ASSAMESE_BACKFILL_RUNS_COLLECTION].find_one(
            {}, sort=[("started_at", -1)],
        )
    except Exception as exc:
        logger.warning(
            "[as_translation_backfill] latest_run_report read failed: %s", exc,
        )
        return None
    if not doc:
        return None
    doc.pop("_id", None)
    return doc
