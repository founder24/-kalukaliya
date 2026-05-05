"""Task #382 — Memory brain write/query round trip.

Mocks Voyage and the Mongo collection to verify that:
  * ``write_memory`` embeds with Voyage and persists into the
    ``memory_brain`` collection with the canonical document shape.
  * ``query_memory`` calls Atlas ``$vectorSearch`` with the user-id
    filter and returns ranked matches.
  * The chunk-path embed providers (Cohere / workers_embed) are NEVER
    consulted on the memory-brain path — only Voyage.
"""
from __future__ import annotations

import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _install_voyage_stub(monkeypatch, vec):
    import providers.voyage_ai as voyage
    calls: list[dict] = []

    async def _embed(texts, *, input_type="search_document", model=None):
        calls.append({"texts": list(texts), "input_type": input_type})
        return [list(vec) for _ in texts]

    monkeypatch.setattr(voyage, "embed", _embed, raising=True)
    monkeypatch.setattr(voyage, "ENABLED", True, raising=True)
    return calls


def _install_mock_db(monkeypatch, *, search_indexes=None):
    """Patch deps.db with a fake Mongo client we can introspect.

    ``search_indexes`` controls what ``$listSearchIndexes`` returns so
    the vector-index leg of ``health_check`` can be exercised. ``None``
    (default) returns a single READY index matching ``INDEX_NAME``;
    pass ``[]`` to simulate a missing index.
    """
    import deps
    import providers.memory_brain as _mb

    inserted: list[dict] = []
    pipelines: list[list] = []

    fake_collection = MagicMock(name="memory_brain")
    insert_result = MagicMock(inserted_id="mem-id-1")

    async def _insert_one(doc):
        inserted.append(doc)
        return insert_result

    if search_indexes is None:
        search_indexes = [{
            "name":      _mb.INDEX_NAME,
            "status":    "READY",
            "queryable": True,
        }]

    def _aggregate(pipeline):
        pipelines.append(pipeline)
        cur = MagicMock()
        # Route $listSearchIndexes vs $vectorSearch to the right payload.
        first_stage = pipeline[0] if pipeline else {}
        if "$listSearchIndexes" in first_stage:
            payload = list(search_indexes)
        else:
            payload = [
                {
                    "_id": "mem-id-1",
                    "user_id": "user-42",
                    "kind": "note",
                    "text": "Photosynthesis converts CO2 + H2O → glucose + O2",
                    "metadata": {"subject": "biology"},
                    "created_at": None,
                    "score": 0.82,
                }
            ]
        async def _to_list(length=None):
            return payload
        cur.to_list = _to_list
        return cur

    fake_collection.insert_one = _insert_one
    fake_collection.aggregate = _aggregate
    fake_collection.estimated_document_count = AsyncMock(return_value=7)

    fake_db = MagicMock()
    fake_db.__getitem__ = lambda self, name: fake_collection
    monkeypatch.setattr(deps, "db", fake_db, raising=False)
    return inserted, pipelines, fake_collection


@pytest.mark.asyncio
async def test_write_memory_embeds_with_voyage_and_inserts(monkeypatch):
    voyage_calls = _install_voyage_stub(monkeypatch, [0.5] * 1024)
    inserted, _pipelines, _coll = _install_mock_db(monkeypatch)

    import providers.memory_brain as mb
    mem_id = await mb.write_memory(
        user_id="user-42",
        text="The mitochondrion is the powerhouse of the cell.",
        kind="fact",
        metadata={"subject": "biology"},
    )

    assert mem_id == "mem-id-1"
    assert len(voyage_calls) == 1
    assert voyage_calls[0]["input_type"] == "document"
    assert len(inserted) == 1
    doc = inserted[0]
    assert doc["user_id"] == "user-42"
    assert doc["kind"] == "fact"
    assert doc["embedding_model"] == "voyage-3.5"
    assert doc["embedding_source"] == "voyage"
    assert doc["embedding_dim"] == 1024
    assert len(doc["embedding"]) == 1024
    assert doc["metadata"] == {"subject": "biology"}


@pytest.mark.asyncio
async def test_query_memory_filters_by_user_and_returns_matches(monkeypatch):
    _install_voyage_stub(monkeypatch, [0.1] * 1024)
    _inserted, pipelines, _coll = _install_mock_db(monkeypatch)

    import providers.memory_brain as mb
    matches = await mb.query_memory(
        user_id="user-42",
        query="What is photosynthesis?",
        top_k=3,
    )

    assert len(matches) == 1
    assert matches[0]["text"].startswith("Photosynthesis")
    assert matches[0]["score"] == pytest.approx(0.82)
    # First aggregation stage must be the $vectorSearch with our filter.
    assert pipelines, "aggregate() should have been invoked"
    vs = pipelines[0][0]["$vectorSearch"]
    assert vs["filter"] == {"user_id": "user-42"}
    assert vs["limit"] == 3
    assert len(vs["queryVector"]) == 1024


@pytest.mark.asyncio
async def test_health_check_marks_ok_only_when_vector_index_is_queryable(monkeypatch):
    """Voyage + collection healthy alone is not enough — the Atlas
    vector index must exist and be queryable for the memory brain to
    actually serve queries. ``health_check`` must reflect that."""
    _install_voyage_stub(monkeypatch, [0.0] * 1024)

    # Stub voyage_ai.health_check so the voyage leg passes regardless
    # of network reachability inside the test process.
    import providers.voyage_ai as voyage
    async def _voy_ok():
        return {"ok": True, "model": "voyage-3.5"}
    monkeypatch.setattr(voyage, "health_check", _voy_ok, raising=False)

    # Case A: index present + queryable → ok=True.
    _install_mock_db(monkeypatch)  # default = single READY index
    import providers.memory_brain as mb
    info_ok = await mb.health_check()
    assert info_ok["ok"] is True
    assert info_ok["vector_index"]["exists"] is True
    assert info_ok["vector_index"]["ok"] is True
    assert info_ok["vector_index"]["queryable"] is True

    # Case B: index missing → ok=False with a clear reason.
    _install_mock_db(monkeypatch, search_indexes=[])
    info_missing = await mb.health_check()
    assert info_missing["ok"] is False
    assert info_missing["vector_index"]["exists"] is False
    assert "not found" in info_missing["vector_index"]["reason"]

    # Case C: index present but not queryable (still building) → ok=False.
    _install_mock_db(monkeypatch, search_indexes=[
        {"name": mb.INDEX_NAME, "status": "BUILDING", "queryable": False},
    ])
    info_building = await mb.health_check()
    assert info_building["ok"] is False
    assert info_building["vector_index"]["exists"] is True
    assert info_building["vector_index"]["queryable"] is False


@pytest.mark.asyncio
async def test_memory_path_does_not_invoke_chunk_embed_providers(monkeypatch):
    """A memory-brain write must not touch workers_embed or cohere — those
    providers serve the chunk path only after Task #382."""
    _install_voyage_stub(monkeypatch, [0.0] * 1024)
    _install_mock_db(monkeypatch)

    # Booby-trap the chunk-path embed providers.
    for mod_name in ("providers.workers_embed", "providers.cohere"):
        stub = types.ModuleType(mod_name)
        async def _raise(*a, **k):  # noqa: ANN001, ANN002
            raise AssertionError(f"{mod_name} must not be called from the memory-brain path")
        stub.embed = _raise
        stub.embed_query = _raise
        stub.embed_documents = _raise
        stub.ENABLED = False
        stub.is_enabled = lambda: False
        monkeypatch.setitem(sys.modules, mod_name, stub)

    import providers.memory_brain as mb
    await mb.write_memory("user-1", "tiny note")
    # The booby traps would have raised AssertionError if hit.
