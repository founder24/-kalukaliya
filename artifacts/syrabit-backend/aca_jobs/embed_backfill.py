"""aca_jobs.embed_backfill — Task #411 re-embed every legacy chunk through
the new Workers-AI custom embed worker so retrieval stops mixing old
Cohere/Voyage vectors with new Gemma+Qwen3 ones.

Background
----------
Task #382 / #400 cut the live embed path over to the custom Workers-AI
worker (Gemma-300M + Qwen3-0.6B fused to 1024 dims) and tagged every
*new* chunk with ``embedding_source=workers_ai_custom``. Every chunk
that was indexed before the cutover still carries the legacy tag
(``cohere`` / ``voyage`` / etc.) and a vector produced by a different
model. Cosine similarity between an old vector and a new query vector
is no longer apples-to-apples, so retrieval drifts silently for any
book that was indexed pre-cutover.

This job walks every such chunk in MongoDB, re-embeds it through the
new worker, re-upserts the vector into Pinecone, and stamps the chunk
with the new source tag. It is:

* **Resumable** — progress (last processed ``_id`` + counters) is
  stored in the ``embed_backfill_state`` collection. A killed run
  picks up exactly where the previous one left off on the next call.
* **Rate-limited** — respects the worker's 32-text per-batch cap and
  the 600 RPM ceiling (sleeps ``60 / EMBED_BACKFILL_MAX_RPM`` seconds
  between batches; default 0.1s = 600 RPM).
* **Bounded per call** — ``max_chunks`` lets the admin trigger a
  capped slice (e.g. one shift's worth) so the caller can monitor
  progress without committing to a multi-hour run.

Selection rule
--------------
A chunk is "legacy" iff ``embedding_source != "workers_ai_custom"``
(missing field counts as legacy). After a successful upsert + Mongo
write, the chunk's ``embedding_source`` is set to
``"workers_ai_custom"`` so the next pass naturally skips it.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import os
from typing import Any, Optional

logger = logging.getLogger("aca_jobs.embed_backfill")

# ── Tunables ─────────────────────────────────────────────────────────────────
# Worker-side hard cap is 32 inputs per request (see providers.workers_embed
# WORKERS_EMBED_MAX_BATCH). Match it so each batch is exactly one HTTP call.
BATCH_SIZE = int(os.environ.get("EMBED_BACKFILL_BATCH_SIZE", "32") or "32")
# 600 RPM = 10 req/s. Sleep gap between batches (each batch == one /embed call).
MAX_RPM = int(os.environ.get("EMBED_BACKFILL_MAX_RPM", "600") or "600")
# Per-call processing budget when invoked from the admin endpoint. None ⇒ run
# until exhausted (used by the CLI).
DEFAULT_PER_CALL_LIMIT = int(
    os.environ.get("EMBED_BACKFILL_PER_CALL_LIMIT", "5000") or "5000"
)
# Periodic loop cadence when started at boot via opt-in env flag.
LOOP_INTERVAL_S = int(os.environ.get("EMBED_BACKFILL_INTERVAL_S", "900") or "900")
AUTOSTART = (os.environ.get("EMBED_BACKFILL_AUTOSTART") or "").strip().lower() in {
    "1", "true", "yes",
}

STATE_COLLECTION = "embed_backfill_state"
STATE_DOC_ID = "global"
TARGET_SOURCE_TAG = "workers_ai_custom"

# Concurrency guard so the admin endpoint can't kick off a second run
# on top of an in-flight one.
_run_lock = asyncio.Lock()


def _sleep_between_batches() -> float:
    """Return the per-batch sleep so we never exceed ``MAX_RPM``."""
    if MAX_RPM <= 0:
        return 0.1
    return max(60.0 / float(MAX_RPM), 0.0)


def _legacy_filter(after_id: Any | None) -> dict:
    """Mongo filter that selects chunks still on the old embed stack."""
    cond: dict = {"embedding_source": {"$ne": TARGET_SOURCE_TAG}}
    if after_id is not None:
        cond["_id"] = {"$gt": after_id}
    return cond


async def _load_state(db: Any) -> dict:
    try:
        doc = await db[STATE_COLLECTION].find_one({"_id": STATE_DOC_ID})
    except Exception as exc:
        logger.warning("[embed_backfill] state read failed: %s", exc)
        doc = None
    return doc or {}


async def _write_state(db: Any, patch: dict) -> None:
    patch = dict(patch)
    patch["updated_at"] = _dt.datetime.utcnow()
    try:
        await db[STATE_COLLECTION].update_one(
            {"_id": STATE_DOC_ID},
            {"$set": patch, "$setOnInsert": {"_id": STATE_DOC_ID}},
            upsert=True,
        )
    except Exception as exc:
        logger.warning("[embed_backfill] state write failed: %s", exc)


async def _count_remaining(db: Any) -> int:
    try:
        return int(await db.chunks.count_documents(_legacy_filter(None)))
    except Exception as exc:
        logger.debug("[embed_backfill] count_remaining failed: %s", exc)
        return 0


async def _count_total(db: Any) -> int:
    try:
        return int(await db.chunks.count_documents({}))
    except Exception as exc:
        logger.debug("[embed_backfill] count_total failed: %s", exc)
        return 0


async def _embed_texts(texts: list[str]) -> list[Optional[list[float]]]:
    """Call the new Workers-AI custom embed worker. Never raises."""
    from providers import workers_embed as _we
    if not _we.is_enabled():
        logger.warning(
            "[embed_backfill] workers_embed disabled — set "
            "WORKERS_EMBED_URL + WORKERS_EMBED_SECRET"
        )
        return [None] * len(texts)
    try:
        vecs = await asyncio.wait_for(
            _we.embed(texts, input_type="search_document"),
            timeout=30.0,
        )
        if vecs and len(vecs) == len(texts):
            return list(vecs)
        logger.warning(
            "[embed_backfill] worker returned %d vectors for %d texts",
            len(vecs) if vecs else 0, len(texts),
        )
    except Exception as exc:
        logger.warning("[embed_backfill] embed call failed: %s", exc)
    return [None] * len(texts)


def _embed_text_for(chunk: dict) -> Optional[str]:
    """Build the text payload that gets sent to the embed worker."""
    content = (chunk.get("content") or "").strip()
    if not content:
        return None
    topic_prefix = chunk.get("topic_name") or chunk.get("chapter_title") or ""
    text = f"{topic_prefix}\n\n{content}" if topic_prefix else content
    content_as = (chunk.get("content_as") or "").strip()
    if content_as:
        text = f"{text}\n\n{content_as[:400]}"
    return text[:2048]


async def get_progress(db: Any) -> dict:
    """Return the admin-facing backfill progress payload."""
    state = await _load_state(db)
    remaining = await _count_remaining(db)
    total = await _count_total(db)
    done = max(total - remaining, 0)
    pct = round((done / total) * 100.0, 2) if total else 0.0
    return {
        "target_source":   TARGET_SOURCE_TAG,
        "total_chunks":    total,
        "remaining":       remaining,
        "re_embedded":     done,
        "percent":         pct,
        "running":         bool(state.get("running")),
        "last_processed_id": state.get("last_processed_id"),
        "started_at":      state.get("started_at"),
        "updated_at":      state.get("updated_at"),
        "completed_at":    state.get("completed_at"),
        "last_run":        state.get("last_run"),
        "batch_size":      BATCH_SIZE,
        "max_rpm":         MAX_RPM,
    }


async def run_backfill(
    db: Any,
    *,
    max_chunks: Optional[int] = DEFAULT_PER_CALL_LIMIT,
    batch_size: int = BATCH_SIZE,
) -> dict:
    """Process up to ``max_chunks`` legacy chunks. Resumes from saved state.

    Returns a summary dict (also written to state.last_run for the admin UI).
    """
    if _run_lock.locked():
        return {"skipped": "already_running"}

    async with _run_lock:
        # Cap the per-batch size to the worker's hard limit.
        batch_size = max(1, min(int(batch_size or BATCH_SIZE), 32))

        state = await _load_state(db)
        last_id = state.get("last_processed_id")
        # If the previous run reached the end, restart from the top. Any
        # chunks newly written since then will be picked up on this pass.
        if state.get("completed_at") and not state.get("running"):
            last_id = None

        started = _dt.datetime.utcnow()
        total_estimate = await _count_remaining(db)
        await _write_state(db, {
            "running":          True,
            "started_at":       started,
            "completed_at":     None,
            "last_total_estimate": total_estimate,
        })

        processed = succeeded = failed = skipped = 0
        gap = _sleep_between_batches()
        budget = max_chunks if max_chunks is not None else 10**12

        try:
            from pymongo import UpdateOne
        except Exception as exc:  # pragma: no cover — backend always has pymongo
            logger.error("[embed_backfill] pymongo import failed: %s", exc)
            await _write_state(db, {"running": False})
            return {"error": "pymongo_unavailable"}

        # Lazy Pinecone retriever — re-used across batches. Fail-closed:
        # if the retriever cannot be constructed or is unconfigured, abort
        # the run before any chunk is mis-stamped as migrated. The whole
        # point of this job is to land vectors in Pinecone, so a missing
        # Pinecone is a hard error, not a soft fall-through.
        pinecone_retriever = None
        try:
            from retrievers.pinecone_vector import PineconeVectorRetriever
            pinecone_retriever = PineconeVectorRetriever()
        except Exception as exc:
            logger.error("[embed_backfill] Pinecone retriever import failed: %s", exc)
            pinecone_retriever = None
        if pinecone_retriever is None or not pinecone_retriever.is_configured():
            await _write_state(db, {"running": False})
            err = {
                "error":     "pinecone_unavailable",
                "processed": 0,
                "succeeded": 0,
                "failed":    0,
                "skipped":   0,
                "remaining": await _count_remaining(db),
            }
            logger.error(
                "[embed_backfill] aborting — Pinecone retriever is not "
                "configured; refusing to stamp embedding_source markers."
            )
            return err

        while processed < budget:
            take = min(batch_size, budget - processed)
            cursor = (
                db.chunks
                .find(
                    _legacy_filter(last_id),
                    {
                        "_id": 1, "id": 1, "chapter_id": 1, "subject_id": 1,
                        "board_id": 1, "chapter_title": 1, "topic_name": 1,
                        "content": 1, "content_as": 1,
                    },
                )
                .sort("_id", 1)
                .limit(take)
            )
            try:
                batch = await cursor.to_list(length=take)
            except Exception as exc:
                logger.warning("[embed_backfill] cursor read failed: %s", exc)
                break

            if not batch:
                # Nothing left — mark complete and exit.
                await _write_state(db, {
                    "running":      False,
                    "completed_at": _dt.datetime.utcnow(),
                })
                break

            texts: list[Optional[str]] = [_embed_text_for(c) for c in batch]
            embed_idxs = [i for i, t in enumerate(texts) if t]
            embed_texts = [texts[i] for i in embed_idxs]
            vecs: list[Optional[list[float]]] = []
            if embed_texts:
                vecs = await _embed_texts(embed_texts)
            # Re-align vectors back to the full batch.
            full_vecs: list[Optional[list[float]]] = [None] * len(batch)
            for slot, vec in zip(embed_idxs, vecs):
                full_vecs[slot] = vec

            # Build the Pinecone payload for everything that embedded
            # successfully. The Mongo marker write is deferred until
            # Pinecone *confirms* the upsert — see below.
            pinecone_vectors: list[dict] = []
            pending_chunks: list[dict] = []
            for slot_i, (chunk, vec) in enumerate(zip(batch, full_vecs)):
                processed += 1
                last_id = chunk["_id"]
                if vec is None:
                    if texts[slot_i] is None:
                        skipped += 1
                    else:
                        failed += 1
                    continue
                pinecone_vectors.append({
                    "id":     str(chunk["_id"]),
                    "values": vec,
                    "metadata": {
                        "chapter_id":      chunk.get("chapter_id", ""),
                        "subject_id":      chunk.get("subject_id", ""),
                        "board_id":        chunk.get("board_id", ""),
                        "chapter_title":   chunk.get("chapter_title", ""),
                        "topic_name":      chunk.get("topic_name", ""),
                        "embedding_model":
                            "workers_ai_custom@gemma+qwen3-meanpool-1024",
                        # Task #411 acceptance: every re-upserted vector
                        # in Pinecone must carry the new source tag so
                        # post-backfill audits can prove "all chunks in
                        # Pinecone have embedding_source=workers_ai_custom".
                        "embedding_source": TARGET_SOURCE_TAG,
                    },
                })
                pending_chunks.append(chunk)

            # Push to Pinecone first. ONLY when Pinecone confirms a clean
            # upsert (no errors AND upserted == len(payload)) do we stamp
            # the Mongo marker. PineconeVectorRetriever.upsert catches
            # network/HTTP errors and returns ``{"errors": [...]}`` rather
            # than raising, so checking the return value (not just
            # try/except) is what makes Pinecone success authoritative.
            mongo_ops: list = []
            if pinecone_vectors:
                pinecone_ok = False
                try:
                    res = await pinecone_retriever.upsert(pinecone_vectors)
                    errs = res.get("errors") or []
                    upserted_count = int(res.get("upserted", 0))
                    if errs or upserted_count != len(pinecone_vectors):
                        logger.warning(
                            "[embed_backfill] Pinecone upsert incomplete "
                            "(upserted=%d/%d errors=%s) — keeping chunks "
                            "selected for retry on next pass.",
                            upserted_count, len(pinecone_vectors), errs[:3],
                        )
                    else:
                        pinecone_ok = True
                except Exception as exc:
                    logger.warning(
                        "[embed_backfill] Pinecone upsert raised: %s — "
                        "keeping chunks selected for retry.", exc,
                    )

                if not pinecone_ok:
                    failed += len(pinecone_vectors)
                else:
                    succeeded += len(pinecone_vectors)
                    for chunk in pending_chunks:
                        mongo_ops.append(UpdateOne(
                            {"_id": chunk["_id"]},
                            {"$set": {
                                "embedding_source": TARGET_SOURCE_TAG,
                                "embedding_model":
                                    "workers_ai_custom@gemma+qwen3-meanpool-1024",
                                "embedding_dim":    1024,
                                "vector_store":     "pinecone",
                                "embedded_at":      _dt.datetime.utcnow(),
                            }},
                            upsert=False,
                        ))

            if mongo_ops:
                try:
                    await db.chunks.bulk_write(mongo_ops, ordered=False)
                except Exception as exc:
                    # The vectors *are* in Pinecone but we couldn't stamp
                    # the marker. Log loudly — the next pass will re-embed
                    # and re-upsert (idempotent on Pinecone vector ids).
                    logger.warning(
                        "[embed_backfill] bulk_write failed after Pinecone "
                        "upsert succeeded: %s — chunks will be re-processed "
                        "on the next pass.", exc,
                    )
                    failed += len(mongo_ops)
                    succeeded -= len(mongo_ops)

            await _write_state(db, {
                "running":            True,
                "last_processed_id":  last_id,
                "last_run_processed": processed,
                "last_run_succeeded": succeeded,
                "last_run_failed":    failed,
                "last_run_skipped":   skipped,
            })

            logger.info(
                "[embed_backfill] processed=%d succeeded=%d failed=%d "
                "skipped=%d (budget=%s)",
                processed, succeeded, failed, skipped,
                budget if max_chunks is not None else "∞",
            )

            if gap > 0:
                await asyncio.sleep(gap)

        duration = (_dt.datetime.utcnow() - started).total_seconds()
        summary = {
            "processed":    processed,
            "succeeded":    succeeded,
            "failed":       failed,
            "skipped":      skipped,
            "duration_s":   round(duration, 2),
            "budget":       max_chunks,
            "remaining":    await _count_remaining(db),
        }
        await _write_state(db, {
            "running":  False,
            "last_run": {**summary, "finished_at": _dt.datetime.utcnow()},
        })
        logger.info("[embed_backfill] pass complete: %s", summary)
        return summary


async def run_loop(db_handle) -> None:
    """Forever loop. Wakes every ``LOOP_INTERVAL_S`` seconds and processes
    the next ``DEFAULT_PER_CALL_LIMIT`` legacy chunks. Never raises."""
    logger.info(
        "embed_backfill loop started (interval=%ds, batch=%d, max_rpm=%d)",
        LOOP_INTERVAL_S, BATCH_SIZE, MAX_RPM,
    )
    await asyncio.sleep(min(LOOP_INTERVAL_S, 30))
    while True:
        try:
            await run_backfill(db_handle)
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning("embed_backfill iteration crashed: %s", str(exc)[:200])
        await asyncio.sleep(LOOP_INTERVAL_S)


def start(db_handle) -> Optional[asyncio.Task]:
    """Kick off the periodic loop. Opt-in via ``EMBED_BACKFILL_AUTOSTART=1``.

    Returns the task handle (or None when disabled / no DB)."""
    if db_handle is None:
        logger.info("embed_backfill not started (db unavailable)")
        return None
    if not AUTOSTART:
        logger.info(
            "embed_backfill loop dormant — set EMBED_BACKFILL_AUTOSTART=1 "
            "to run continuously, or trigger via the admin endpoint."
        )
        return None
    return asyncio.create_task(run_loop(db_handle))
