"""
providers.chunk_embedder — Batch embedding pipeline for the chunks collection.

Embeds all chunks that are missing an `embedding` field using the
custom Cloudflare Workers-AI worker (Gemma-300M + Qwen3-0.6B
mean-pool, 1024-dim — see ``providers.workers_embed``).
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

# Task #513 §B + §K.3 — surfaced for the cost_caps wiring regression
# test; the embed batch builder below honours `_BATCH_SIZE = 48` which
# is the §K.3 "embed micro-batch" setting (single Workers-AI request
# per up-to-48 chunks instead of one request per chunk).
from cost_caps import TOKEN_BUDGETS as _COST_TOKEN_BUDGETS

logger = logging.getLogger("providers.chunk_embedder")

_EMBED_DIM   = 1024
_BATCH_SIZE  = 32  # COST-CAP-OVERRIDE: Task #513 §K.3 — embed batch size locked at 32 (was 48). Fewer items per CF Workers AI request keeps per-call latency bounded so the AsyncBatcher window can flush before clients time out, while still amortising the round-trip overhead. Bumping requires Sentry-annotated changelog.
# Task #513 §B — per-chunk text length cap derived from the locked
# `embed` budget (1 500 input tokens × ~4 chars/token ≈ 6 000 chars).
# A single runaway 100 KB chunk would otherwise blow past the
# Workers-AI worker's per-request payload limit and force a retry.
_EMBED_CHARS_CAP = max(1024, int(_COST_TOKEN_BUDGETS.get("embed", {}).get("max_input_tokens", 1500)) * 4)

_EMBED_MODEL_WORKERS = "workers_ai_custom@gemma+qwen3-meanpool-1024"


def _embed_source_for_primary() -> tuple[str, str]:
    """Return (model_string, source_tag) for the active primary.

    Task #491 retired the legacy embed providers; the chunk path is single-source
    workers_ai_custom. The function is kept for diagnostics callers.
    """
    return (_EMBED_MODEL_WORKERS, "workers_ai_custom")


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


async def _bedrock_indic_embed_batch(texts: list[str]) -> list[Optional[list[float]]]:
    """Embed a batch of Indic-language chunk texts via the Task #27
    Bedrock-Cohere route. One InvokeModel call per text (the provider
    module's ``embed_one`` does not yet batch — Bedrock Cohere v3
    accepts batches of up to 96 inputs but our wrapper currently
    targets the single-text query path). Returns ``None`` slots on
    failure so the caller can mark the chunk as failed and retry it
    on the next run; there is NO silent fallback to Workers-AI here
    (that would re-introduce the cache-key mixing the dispatcher
    already guards against — provider-correctness > availability for
    the indexing path)."""
    from providers import cohere_bedrock_embed as _cb
    out: list[Optional[list[float]]] = []
    for t in texts:
        try:
            v = await asyncio.wait_for(
                _cb.embed_one(t, task_type="RETRIEVAL_DOCUMENT"),
                timeout=30.0,
            )
            out.append(v)
        except _cb.BedrockEmbedAccessDenied as exc:
            logger.warning(
                "[chunk_embedder] bedrock indic embed IAM/access denied — "
                "leaving slot None for retry: %s", exc,
            )
            out.append(None)
        except Exception as exc:
            logger.warning("[chunk_embedder] bedrock indic embed failed: %s", exc)
            out.append(None)
    return out


def _is_indic_text(content_as: str, content: str, lang: str | None = None) -> bool:
    """Task #27 — language gate for the bulk indexing path.

    Order of precedence:
      1. If the chunk carries an explicit ``lang`` (e.g. ``"as"``,
         ``"as-IN"``, ``"hi"``), defer to the shared
         ``llm._is_indic_lang`` classifier so the indexing route
         contract matches the chat-path dispatcher exactly.
      2. Otherwise fall back to a length heuristic on the bilingual
         ``content_as`` field — Indic-dominant when the Assamese
         variant is present AND at least 30 % of the English content
         length AND ≥ 40 chars (avoids false positives on stub fields).
    """
    if lang:
        try:
            from llm import _is_indic_lang as _shared_is_indic
            return bool(_shared_is_indic(lang))
        except Exception:
            pass
    return bool(content_as) and len(content_as) >= max(40, int(0.30 * max(1, len(content))))


async def _embed_batch(texts: list[str]) -> list[Optional[list[float]]]:
    """Dispatch a chunk batch to the workers_ai_custom embed worker.

    STRICT provider isolation (Task #382, hardened by Task #491): the
    English / unknown-language chunk path is single-source
    workers_ai_custom. A worker failure surfaces as None slots so
    ``embed_chunks_bulk`` can mark those chunks as failed and retry
    them on the next run; there is NO silent fallback to retired
    legacy embed providers.

    Task #27 — language-gated split lives in ``_embed_batch_split`` so
    Indic chunks route to Bedrock-Cohere at indexing time too. This
    helper is kept for the legacy single-language code path and tests
    that monkey-patch it directly.
    """
    return await _workers_custom_embed_batch(texts)


async def _embed_batch_split(
    items: list[tuple[int, str, bool]],
) -> tuple[list[Optional[list[float]]], list[str]]:
    """Task #27 — language-gated indexing split.

    ``items`` is a list of ``(slot_idx, text, is_indic)`` tuples. We
    partition by the ``is_indic`` flag, embed each leg through its
    canonical provider (Bedrock-Cohere for Indic, Workers-AI custom
    for English / unknown), and reassemble vectors back into the
    caller's slot order along with a parallel list of per-slot
    ``embed_provider`` tags so the Pinecone metadata write reflects
    the provider that ACTUALLY produced each vector.

    A pre-call check against MeterD's Indic sub-cap (``$5/mo`` inside
    the global ``$100`` cap) routes Indic texts back to Workers-AI
    when the sub-cap has tripped — this matches the runtime
    dispatcher's behaviour in ``llm.call_embed_with_dispatch`` and
    prevents the bulk indexer from busting the sub-cap in a single
    nightly run.
    """
    n = len(items)
    vecs: list[Optional[list[float]]] = [None] * n
    tags: list[str] = ["workers_ai_custom"] * n

    # Pre-call sub-cap pause check.
    _indic_paused = False
    try:
        from credit_burn_meter_runtime import is_indic_embed_paused as _ip
        _indic_paused = bool(_ip())
    except Exception:
        _indic_paused = False

    indic_idx: list[int] = []
    indic_texts: list[str] = []
    eng_idx: list[int] = []
    eng_texts: list[str] = []
    for slot, text, is_indic in items:
        if is_indic and not _indic_paused:
            indic_idx.append(slot)
            indic_texts.append(text)
        else:
            eng_idx.append(slot)
            eng_texts.append(text)

    if eng_texts:
        eng_vecs = await _embed_batch(eng_texts)
        for slot, v in zip(eng_idx, eng_vecs):
            vecs[slot] = v
            tags[slot] = "workers_ai_custom"

    if indic_texts:
        ind_vecs = await _bedrock_indic_embed_batch(indic_texts)
        # MeterD ingest for Bedrock spend (1 token ≈ 4 chars heuristic;
        # reconciled by the daily Cost Explorer ingestion — Task #513 §J).
        try:
            from cost_caps import BEDROCK_COHERE_EMBED_USD_PER_1K_TOKENS as _RATE
            from credit_burn_meter_runtime import ingest_meter_d_usd_indic as _meter_indic
            _approx = sum(max(1, len(t) // 4) for t in indic_texts)
            _meter_indic((_approx / 1000.0) * float(_RATE))
        except Exception as _meter_exc:
            logger.debug("[chunk_embedder] meter D indic ingest failed: %s", _meter_exc)
        for slot, v in zip(indic_idx, ind_vecs):
            vecs[slot] = v
            tags[slot] = "cohere_multilingual_v3_bedrock"

    return vecs, tags


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
         "chapter_title": 1, "topic_name": 1, "content": 1, "content_as": 1,
         # Task #27 — pull `lang` so the language gate can defer to the
         # shared `_is_indic_lang` classifier when present.
         "lang": 1, "language": 1},
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
        is_indic_flags: list[bool] = []
        for ch in batch:
            content = (ch.get("content") or "").strip()
            if not content:
                skipped += 1
                texts.append(None)
                is_indic_flags.append(False)
                continue
            # Bilingual: append Assamese content if available
            content_as = (ch.get("content_as") or "").strip()
            topic_prefix = ch.get("topic_name", "") or ch.get("chapter_title", "")
            embed_text = f"{topic_prefix}\n\n{content}"
            if content_as:
                embed_text += f"\n\n{content_as[:400]}"
            # Task #27 — flag Indic-dominant chunks so the language-gated
            # split can route them to Bedrock-Cohere v3. Defers to the
            # shared `_is_indic_lang` classifier when the chunk carries
            # an explicit `lang` / `language` field.
            _chunk_lang = (ch.get("lang") or ch.get("language") or "").strip() or None
            is_indic_flags.append(
                _is_indic_text(content_as, content, lang=_chunk_lang)
            )
            # Task #513 §B — clamp at the §B-locked `embed` budget
            # (`_EMBED_CHARS_CAP`, derived from cost_caps.TOKEN_BUDGETS),
            # not the legacy hard-coded 2048 ceiling. The two are equal
            # today (1500 tok × 4 chars = 6000), but slaving the
            # truncation to TOKEN_BUDGETS guarantees future budget
            # changes propagate without a manual edit here.
            texts.append(embed_text[:_EMBED_CHARS_CAP])

        # Embed non-None texts via the language-gated split (Task #27).
        to_embed = [(i, t) for i, t in enumerate(texts) if t is not None]
        if not to_embed:
            continue

        idxs, embed_texts = zip(*to_embed)
        # Build (slot, text, is_indic) tuples preserving the original
        # batch order so the per-slot provider tag below lines up with
        # the chunk metadata we write to Pinecone.
        split_items = [
            (k, embed_texts[k], is_indic_flags[idxs[k]])
            for k in range(len(embed_texts))
        ]
        vecs, vec_provider_tags = await _embed_batch_split(split_items)

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
        for k, (i, vec) in enumerate(zip(idxs, vecs)):
            if vec is None:
                failed += 1
                continue
            chunk = batch[i]
            # Task #27 — per-slot provider tag from the language-gated
            # split. Falls back to the chunk-path default if the split
            # somehow didn't tag this slot (defensive).
            _slot_provider_tag = (
                vec_provider_tags[k] if k < len(vec_provider_tags) else _embed_source_tag
            )
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
                        # Task #27 — provider-tag every Pinecone vector so
                        # retrieval can filter by `embed_provider` and
                        # never mix Workers-AI vectors with Bedrock-Cohere
                        # vectors in a single result set. Default mirrors
                        # `chunk_embedder`'s embed source (Workers-AI
                        # custom worker); the language-gated dispatcher
                        # in `llm.call_embed_with_dispatch` writes the
                        # `cohere_multilingual_v3_bedrock` tag for the
                        # Indic route. Bulk indexing now also routes
                        # Indic-dominant chunks via Bedrock-Cohere
                        # (`_embed_batch_split`), so this tag reflects
                        # the provider that ACTUALLY produced the
                        # vector for this chunk, not the chunk-path
                        # default.
                        "embed_provider":  _slot_provider_tag,
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
                    # embed-model: @cf/google/embeddinggemma-300m
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
    # diagnostics don't mis-label a workers_ai_custom run as a legacy provider.
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
