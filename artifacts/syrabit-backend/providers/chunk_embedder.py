"""
providers.chunk_embedder — Batch embedding pipeline for the chunks collection.

Embeds all chunks that are missing an `embedding` field using Cohere
`embed-multilingual-v3.0` via Cloudflare AI Gateway (BYOK).
1024-dim, multilingual — handles Assamese, Bengali, Hindi, English content.

After running, the Atlas `vector_index` on `chunks.embedding` becomes
queryable via `$vectorSearch`.

Completion markers
------------------
* PINECONE_WRITE=false (Atlas-only legacy): writes ``embedding`` + metadata
  to MongoDB.  Next run skips via ``{"embedding": {"$exists": false}}``.
* PINECONE_WRITE=true (Pinecone-primary, Task #203/208 default): does NOT
  write ``embedding`` to MongoDB.  DOES write
  ``{"vector_store": "pinecone", "embedded_at": …}`` after a successful
  Pinecone upsert.  Next run skips via that marker, so chunks are never
  re-embedded on repeated runs.  Set ``PINECONE_SKIP_MONGO_EMBED=false`` to
  force the old path (e.g. to warm up Atlas fallback).

Usage (from admin endpoint):
    from providers.chunk_embedder import embed_chunks_bulk
    result = await embed_chunks_bulk(db, batch_size=64, force_all=False)

Also provides:
    embed_chapter_content   — embed a single chapter's full content text
    translate_and_embed_as  — translate chapter to Assamese then embed bilingual
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

logger = logging.getLogger("providers.chunk_embedder")

_EMBED_MODEL = "embed-multilingual-v3.0"
_EMBED_DIM   = 1024
_BATCH_SIZE  = 48   # Cohere allows up to 96 inputs; keep below for safety

# Task #382 — when the primary is the custom Workers-AI worker we tag
# inserted chunks with that source name (so admin/diagnostics can tell
# at-a-glance which embed provider produced a given vector batch) and
# fall back to the historical "cohere" tag for the legacy path.
_EMBED_MODEL_WORKERS = "workers_ai_custom@gemma+qwen3-meanpool-1024"


def _embed_source_for_primary() -> tuple[str, str]:
    """Return (model_string, source_tag) for the active primary."""
    if _embed_provider_primary() == "workers_ai_custom":
        return (_EMBED_MODEL_WORKERS, "workers_ai_custom")
    return (_EMBED_MODEL, "cohere")


def _embed_provider_primary() -> str:
    """Return the configured primary embed provider for the chunk path.

    Reads ``EMBED_PROVIDER_PRIMARY`` lazily so test monkeypatches and
    runtime overrides take effect without re-importing this module.
    """
    try:
        from config import EMBED_PROVIDER_PRIMARY as _epp
        return (_epp or "").strip().lower() or "workers_ai_custom"
    except Exception:
        return "workers_ai_custom"


async def _workers_custom_embed_batch(texts: list[str]) -> list[Optional[list[float]]]:
    """Embed a batch of texts via the Task #382 custom Workers-AI worker.

    Returns one vector per input text, or ``None`` slots when the worker
    cannot serve a particular call. Never raises.
    """
    try:
        from providers import workers_embed as _we
        if not _we.is_enabled():
            logger.warning(
                "[chunk_embedder] workers_ai_custom embed disabled — "
                "WORKERS_EMBED_URL/SECRET not set"
            )
            return [None] * len(texts)
        vecs = await asyncio.wait_for(
            _we.embed(texts, input_type="search_document"),
            timeout=30.0,
        )
        if vecs and len(vecs) == len(texts):
            return vecs
        logger.warning(
            "[chunk_embedder] workers_ai_custom returned %d vectors for %d texts",
            len(vecs) if vecs else 0, len(texts),
        )
    except Exception as exc:
        logger.warning("[chunk_embedder] workers_ai_custom embed failed: %s", exc)
    return [None] * len(texts)


async def _legacy_cohere_embed_batch(texts: list[str]) -> list[Optional[list[float]]]:
    """Legacy embed path — Cohere → Pinecone Inference fallback.

    Kept for the rollback case (``EMBED_PROVIDER_PRIMARY != workers_ai_custom``)
    so flipping the flag back restores the prior behaviour without a
    code change. Never raises.
    """
    try:
        from providers.cohere import embed as _cohere_embed, ENABLED as _cohere_on
        if _cohere_on:
            vecs = await asyncio.wait_for(
                _cohere_embed(texts, input_type="search_document"),
                timeout=25.0,
            )
            if vecs and len(vecs) == len(texts):
                return vecs
    except Exception as exc:
        logger.warning("[chunk_embedder] Cohere embed batch failed: %s", exc)

    try:
        from providers.pinecone_ai import embed as pc_embed, ENABLED as _pc_on
        if _pc_on:
            logger.info("[chunk_embedder] Falling back to Pinecone for this batch")
            vecs = await asyncio.wait_for(
                pc_embed(texts, input_type="passage"),
                timeout=20.0,
            )
            return vecs
    except Exception as exc:
        logger.warning("[chunk_embedder] Pinecone fallback also failed: %s", exc)

    return [None] * len(texts)


async def _embed_batch(texts: list[str]) -> list[Optional[list[float]]]:
    """Dispatch a chunk batch to the configured primary embed provider.

    STRICT provider isolation (Task #382): when
    ``EMBED_PROVIDER_PRIMARY=workers_ai_custom`` (the default), this
    function is a single-source pipeline — it calls ONLY the custom
    Workers-AI worker. A worker failure surfaces as None slots so
    ``embed_chunks_bulk`` can mark those chunks as failed and retry
    them on the next run; it does NOT silently fall back to Cohere /
    Voyage / Pinecone Inference. The legacy multi-provider path is
    only reachable when the operator flips
    ``EMBED_PROVIDER_PRIMARY`` to a legacy provider name (cohere /
    voyage_ai / vertex / azure_openai / workers_ai), which is the
    explicit rollback contract. Voyage in particular is reserved for
    the memory_brain collection (``providers.memory_brain``) and is
    never reached from this chunk path under either flag value.
    """
    primary = _embed_provider_primary()
    if primary == "workers_ai_custom":
        return await _workers_custom_embed_batch(texts)
    # Rollback path — Cohere → Pinecone Inference, original behaviour.
    return await _legacy_cohere_embed_batch(texts)


# Back-compat alias — keep the historical symbol importable for any
# downstream caller (and in-tree tests) that still references the
# Cohere-batch helper directly. The alias forwards to the dispatcher
# so the new flag is honoured even via the old symbol name.
async def _cohere_embed_batch(texts: list[str]) -> list[Optional[list[float]]]:
    return await _embed_batch(texts)


async def embed_chunks_bulk(
    db: Any,
    *,
    batch_size: int = _BATCH_SIZE,
    force_all: bool = False,
    subject_id: Optional[str] = None,
    limit: Optional[int] = None,
) -> dict:
    """Embed all chunks missing an `embedding` field using Pinecone.

    Args:
        db:         Motor database instance.
        batch_size: How many chunks to embed per Pinecone API call.
        force_all:  If True, re-embed ALL chunks even if they already have embeddings.
        subject_id: Scope to a single subject (optional).
        limit:      Max total chunks to process (optional, for test runs).

    Returns:
        Dict with stats: total, embedded, skipped, failed, duration_s.
    """
    t0 = time.perf_counter()

    # Select chunks that haven't been embedded via EITHER path:
    #   • "embedding" missing → not yet in Atlas Vector Search
    #   • "vector_store" absent/not "pinecone" → not yet in Pinecone
    # Using $and so both exclusion conditions must be satisfied (i.e., a
    # chunk already upserted to Pinecone with PINECONE_SKIP_MONGO_EMBED=true
    # will have vector_store="pinecone" and be excluded from future runs).
    query: dict = {}
    if not force_all:
        query = {
            "$and": [
                {"embedding": {"$exists": False}},
                {"vector_store": {"$ne": "pinecone"}},
            ]
        }
    if subject_id:
        query["subject_id"] = subject_id

    cursor = db.chunks.find(
        query,
        {"_id": 1, "id": 1, "chapter_id": 1, "subject_id": 1, "board_id": 1,
         "chapter_title": 1, "topic_name": 1, "content": 1, "content_as": 1},
    )
    if limit:
        cursor = cursor.limit(limit)

    chunks = await cursor.to_list(length=limit or 10_000)
    total = len(chunks)
    logger.info("[chunk_embedder] Starting bulk embed: %d chunks (force=%s)", total, force_all)

    embedded = failed = skipped = 0

    for batch_start in range(0, total, batch_size):
        batch = chunks[batch_start: batch_start + batch_size]

        texts = []
        for ch in batch:
            content = (ch.get("content") or "").strip()
            if not content:
                skipped += 1
                texts.append(None)
                continue
            # Bilingual: append Assamese content if available
            content_as = (ch.get("content_as") or "").strip()
            topic_prefix = ch.get("topic_name", "") or ch.get("chapter_title", "")
            embed_text = f"{topic_prefix}\n\n{content}"
            if content_as:
                embed_text += f"\n\n{content_as[:400]}"
            texts.append(embed_text[:2048])

        # Embed non-None texts
        to_embed = [(i, t) for i, t in enumerate(texts) if t is not None]
        if not to_embed:
            continue

        idxs, embed_texts = zip(*to_embed)
        vecs = await _embed_batch(list(embed_texts))

        # Update MongoDB (source of truth — always written)
        from motor.motor_asyncio import AsyncIOMotorDatabase
        from pymongo import UpdateOne
        import os as _os

        _pinecone_write = _os.environ.get("PINECONE_WRITE", "").strip().lower() in ("1", "true", "yes")
        # When PINECONE_WRITE=true, Pinecone is the primary vector store
        # (Task #203/208). Pinecone-primary mode defaults to NOT writing the
        # large embedding array to MongoDB — it wastes ~8 KB per chunk and
        # inflates collection reads. Set PINECONE_SKIP_MONGO_EMBED=false to
        # re-enable the MongoDB embedding write (e.g. for Atlas fallback warm-up).
        _skip_mongo_embed_env = _os.environ.get("PINECONE_SKIP_MONGO_EMBED", "").strip().lower()
        if _pinecone_write:
            # Pinecone-primary: skip mongo embed UNLESS explicitly set to false.
            _skip_mongo_embed = _skip_mongo_embed_env not in ("0", "false", "no")
        else:
            # Atlas-only path: write embedding to MongoDB (legacy default).
            _skip_mongo_embed = _skip_mongo_embed_env in ("1", "true", "yes")

        ops = []
        pinecone_vectors: list = []
        # Track which chunk _ids were successfully queued for Pinecone upsert
        # so we can write the completion marker after the upsert succeeds.
        pinecone_chunk_ids: list = []
        _embed_model_tag, _embed_source_tag = _embed_source_for_primary()
        for i, vec in zip(idxs, vecs):
            if vec is None:
                failed += 1
                continue
            chunk = batch[i]
            filter_q = {"_id": chunk["_id"]}
            if not _skip_mongo_embed:
                ops.append(UpdateOne(
                    filter_q,
                    {"$set": {
                        "embedding":        vec,
                        "embedding_model":  _embed_model_tag,
                        "embedding_dim":    _EMBED_DIM,
                        "embedding_source": _embed_source_tag,
                    }},
                    upsert=False,
                ))
            # Queue for Pinecone upsert if PINECONE_WRITE is enabled
            if _pinecone_write:
                pinecone_vectors.append({
                    "id": str(chunk["_id"]),
                    "values": vec,
                    "metadata": {
                        "chapter_id":      chunk.get("chapter_id", ""),
                        "subject_id":      chunk.get("subject_id", ""),
                        "board_id":        chunk.get("board_id", ""),
                        "chapter_title":   chunk.get("chapter_title", ""),
                        "topic_name":      chunk.get("topic_name", ""),
                        "embedding_model": _embed_model_tag,
                    },
                })
                pinecone_chunk_ids.append(chunk["_id"])
            embedded += 1

        if ops:
            try:
                await db.chunks.bulk_write(ops, ordered=False)
            except Exception as exc:
                logger.warning("[chunk_embedder] Bulk write error: %s", exc)
                failed += len(ops)
                embedded -= len(ops)

        # Upsert to Pinecone when PINECONE_WRITE=true
        if pinecone_vectors:
            try:
                from retrievers.pinecone_vector import PineconeVectorRetriever
                _pc = PineconeVectorRetriever()
                if _pc.is_configured():
                    pc_result = await _pc.upsert(pinecone_vectors)
                    logger.info(
                        "[chunk_embedder] Pinecone upsert: %d vectors → %s",
                        len(pinecone_vectors), pc_result,
                    )
                    # When skipping MongoDB embedding write, mark chunks with a
                    # completion marker so subsequent runs skip them instead of
                    # re-embedding the same documents on every invocation.
                    if _skip_mongo_embed and pinecone_chunk_ids:
                        import datetime as _dt
                        _now = _dt.datetime.utcnow()
                        _marker_ops = [
                            UpdateOne(
                                {"_id": cid},
                                {"$set": {"vector_store": "pinecone", "embedded_at": _now}},
                                upsert=False,
                            )
                            for cid in pinecone_chunk_ids
                        ]
                        try:
                            await db.chunks.bulk_write(_marker_ops, ordered=False)
                        except Exception as _me:
                            logger.warning("[chunk_embedder] Completion marker write failed: %s", _me)
            except Exception as exc:
                logger.warning("[chunk_embedder] Pinecone upsert failed (non-fatal): %s", exc)

        logger.info(
            "[chunk_embedder] Progress %d/%d — embedded=%d failed=%d skipped=%d",
            batch_start + len(batch), total, embedded, failed, skipped,
        )
        # Throttle to avoid Pinecone rate limits
        await asyncio.sleep(0.1)

    duration = round(time.perf_counter() - t0, 2)
    # Report the model/source actually used for this run so admin
    # diagnostics don't mis-label a workers_ai_custom run as Cohere.
    _active_model_tag, _active_source_tag = _embed_source_for_primary()
    result = {
        "total":            total,
        "embedded":         embedded,
        "skipped":          skipped,
        "failed":           failed,
        "duration_s":       duration,
        "model":            _active_model_tag,
        "embedding_source": _active_source_tag,
        "primary":          _embed_provider_primary(),
    }
    logger.info("[chunk_embedder] Bulk embed complete: %s", result)
    return result


async def embed_chapter_content(
    db: Any,
    chapter_id: str,
    *,
    force: bool = False,
) -> dict:
    """Embed all chunks for a single chapter.

    Useful after notes/QA generation to immediately make the chapter
    searchable via Atlas Vector Search.
    """
    result = await embed_chunks_bulk(
        db,
        batch_size=_BATCH_SIZE,
        force_all=force,
        subject_id=None,
        limit=None,
    )
    return result


async def translate_chapters_to_assamese(
    db: Any,
    *,
    limit: int = 50,
    skip_existing: bool = True,
) -> dict:
    """Translate English chapter content to Assamese (content_as field).

    Uses Sarvam translate:v1 as primary, no fallback (admin pipeline only).
    After translation, also re-embeds the chapter chunks bilingually.

    Args:
        db:            Motor database.
        limit:         Max chapters to process per run.
        skip_existing: Skip chapters that already have content_as.

    Returns:
        Stats dict: total, translated, failed, skipped, duration_s.
    """
    import deps

    t0 = time.perf_counter()
    query: dict = {"status": "published", "content": {"$exists": True, "$ne": ""}}
    if skip_existing:
        query["content_as"] = {"$exists": False}

    chapters = await db.chapters.find(
        query,
        {"_id": 0, "id": 1, "title": 1, "content": 1, "subject_id": 1},
    ).limit(limit).to_list(length=limit)

    total = len(chapters)
    translated_count = failed = skipped = 0

    # Task #492 — translate via the unified weighted dispatch (Workers-AI
    # IndicTrans2 primary). The Sarvam-specific HTTP loop was removed
    # along with `sarvam_translate_client`.
    from llm import call_translate_with_dispatch

    logger.info("[chunk_embedder] Translating %d chapters to Assamese", total)

    for ch in chapters:
        content = (ch.get("content") or "").strip()
        if not content or len(content) < 50:
            skipped += 1
            continue

        # Chunk into 1800-char pieces (matches translate dispatch limits).
        parts = []
        for i in range(0, len(content), 1800):
            parts.append(content[i:i + 1800])

        translated_parts = []
        ok = True
        for part in parts:
            try:
                translated_text = await asyncio.wait_for(
                    call_translate_with_dispatch(part, "en-IN", "as-IN", lang="as"),
                    timeout=8.0,
                )
                if translated_text:
                    translated_parts.append(translated_text.strip())
                else:
                    logger.warning("[chunk_embedder] Empty translation for chapter %s", ch["id"])
                    ok = False
                    break
            except Exception as exc:
                logger.warning("[chunk_embedder] Translation failed for chapter %s: %s", ch["id"], exc)
                ok = False
                break

        if not ok or not translated_parts:
            failed += 1
            continue

        content_as = "\n".join(translated_parts)
        await db.chapters.update_one(
            {"id": ch["id"]},
            {"$set": {"content_as": content_as, "content_as_lang": "as-IN",
                      "content_as_model": "workers_ai_indictrans2"}},
        )

        # Re-embed the chapter's chunks bilingually
        try:
            await db.chunks.update_many(
                {"chapter_id": ch["id"]},
                {"$unset": {"embedding": ""}},
            )
        except Exception:
            pass

        translated_count += 1
        logger.info("[chunk_embedder] Translated '%s' (%d chars → %d chars as)", ch["title"][:40], len(content), len(content_as))
        await asyncio.sleep(0.2)

    # Re-embed all modified chunks
    embed_result = {}
    if translated_count > 0:
        embed_result = await embed_chunks_bulk(db, force_all=False)

    duration = round(time.perf_counter() - t0, 2)
    return {
        "total":        total,
        "translated":   translated_count,
        "failed":       failed,
        "skipped":      skipped,
        "duration_s":   duration,
        "embed_result": embed_result,
    }
