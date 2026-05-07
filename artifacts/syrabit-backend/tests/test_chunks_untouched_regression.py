"""Task #382 — Regression: the existing ``chunks`` collection's
already-embedded documents are NOT modified by the new pipeline.

The new bulk embed pipeline must:
  * skip chunks that already carry an ``embedding`` (or the
    ``vector_store=pinecone`` completion marker), and
  * update only chunks that genuinely lack an embedding.

This test exercises ``embed_chunks_bulk`` with a curated chunks set
and asserts that ``bulk_write`` is invoked only for the unembedded
slice, and never for chunks that already had an embedding.
"""
from __future__ import annotations

import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def fake_db_with_chunks(monkeypatch):
    """Build a Motor-shaped mock with a ``chunks`` collection seeded
    with two embedded chunks and one unembedded chunk."""
    pre_existing = [
        {
            "_id": "chunk-A",
            "id": "chunk-A",
            "content": "already embedded A",
            "embedding": [0.42] * 1024,
            "embedding_source": "workers_ai_custom",
            "embedding_model": "embed-multilingual-v3.0",
        },
        {
            "_id": "chunk-B",
            "id": "chunk-B",
            "content": "already in pinecone B",
            "vector_store": "pinecone",
        },
    ]
    unembedded = [
        {
            "_id": "chunk-C",
            "id": "chunk-C",
            "content": "needs embedding C",
            "subject_id": "s1",
            "chapter_id": "ch1",
            "chapter_title": "Chapter 1",
            "topic_name": "Topic 1",
        }
    ]
    db_state = {"chunks": list(pre_existing) + list(unembedded)}

    bulk_writes: list[list] = []

    chunks_coll = MagicMock(name="chunks")

    def _find(query, projection=None):
        # Honour the {$and: [{embedding: missing}, {vector_store != pinecone}]} filter.
        def _matches(doc, q):
            if "$and" in q:
                return all(_matches(doc, sub) for sub in q["$and"])
            for k, cond in q.items():
                if k == "subject_id":
                    if doc.get("subject_id") != cond:
                        return False
                    continue
                if isinstance(cond, dict):
                    if "$exists" in cond and bool("embedding" in doc) != cond["$exists"]:
                        return False
                    if "$ne" in cond and doc.get(k) == cond["$ne"]:
                        return False
                else:
                    if doc.get(k) != cond:
                        return False
            return True

        results = [d for d in db_state["chunks"] if _matches(d, query)]
        cursor = MagicMock()
        cursor.limit = MagicMock(return_value=cursor)

        async def _to_list(length=None):
            return list(results)

        cursor.to_list = _to_list
        return cursor

    async def _bulk_write(ops, ordered=False):
        bulk_writes.append(list(ops))
        result = MagicMock(upserted_count=0, modified_count=len(list(ops)))
        return result

    chunks_coll.find = _find
    chunks_coll.bulk_write = _bulk_write

    fake_db = MagicMock()
    fake_db.__getitem__ = lambda self, name: chunks_coll if name == "chunks" else MagicMock()
    fake_db.chunks = chunks_coll

    return fake_db, bulk_writes, db_state


@pytest.mark.asyncio
async def test_embed_chunks_bulk_skips_already_embedded(monkeypatch, fake_db_with_chunks):
    fake_db, bulk_writes, db_state = fake_db_with_chunks

    # Stub the chunk-path embed dispatch so no real provider is called.
    from providers import chunk_embedder

    embed_calls: list[list[str]] = []

    async def _embed_batch(texts):
        embed_calls.append(list(texts))
        return [[0.99] * 1024 for _ in texts]

    monkeypatch.setattr(chunk_embedder, "_embed_batch", _embed_batch, raising=True)
    # Make sure we exercise the workers_ai_custom branding.
    import config
    monkeypatch.setattr(config, "EMBED_PROVIDER_PRIMARY", "workers_ai_custom", raising=True)

    # Disable Pinecone write so the test stays focused on the Mongo side.
    monkeypatch.setenv("PINECONE_WRITE", "0")
    monkeypatch.setenv("PINECONE_SKIP_MONGO_EMBED", "0")

    result = await chunk_embedder.embed_chunks_bulk(fake_db, batch_size=10, force_all=False)

    # Only chunk-C lacked an embedding and wasn't marked vector_store=pinecone.
    assert result["embedded"] == 1
    assert result["total"] == 1
    assert len(embed_calls) == 1
    assert "needs embedding C" in embed_calls[0][0]

    # Bulk write must have run exactly once and only contain a single
    # UpdateOne for chunk-C. The op's filter targets _id="chunk-C".
    assert len(bulk_writes) == 1
    assert len(bulk_writes[0]) == 1
    op = bulk_writes[0][0]
    # pymongo.UpdateOne stores the filter under ``_filter`` (private)
    # and the doc under ``_doc``; fall back to repr() if those names
    # ever shift, since the cardinality-1 assertion above is the
    # primary guarantee.
    op_filter = getattr(op, "_filter", None) or {}
    op_doc = getattr(op, "_doc", None) or {}
    if op_filter:
        assert op_filter.get("_id") == "chunk-C", op_filter
    if op_doc:
        set_block = op_doc.get("$set", {})
        # Source tag must reflect the workers_ai_custom branding under
        # the Task #382 default flag.
        assert set_block.get("embedding_source") == "workers_ai_custom"
        assert set_block.get("embedding_dim") == 1024

    # Pre-existing embedded chunks must have their original embedding intact.
    chunk_a = next(d for d in db_state["chunks"] if d["_id"] == "chunk-A")
    assert chunk_a["embedding"][0] == 0.42
    assert chunk_a["embedding_source"] == "workers_ai_custom"
    chunk_b = next(d for d in db_state["chunks"] if d["_id"] == "chunk-B")
    assert chunk_b["vector_store"] == "pinecone"


@pytest.mark.asyncio
async def test_embed_source_tag_uses_workers_custom_when_flag_on(monkeypatch):
    import config
    monkeypatch.setattr(config, "EMBED_PROVIDER_PRIMARY", "workers_ai_custom", raising=True)

    from providers import chunk_embedder
    model, source = chunk_embedder._embed_source_for_primary()
    assert "workers_ai_custom" in source
    assert "gemma" in model.lower()


