"""
test_retrievers — exercise the retriever interface, the Vectorize
adapter (with a stubbed `vectorize_client`), and the factory's
selection precedence.

Task #490 removed `retrievers/vertex.py` (`VertexVectorSearchRetriever`)
along with the Vertex chat / multilingual-embed surface; only Vectorize,
MongoVectorRetriever, and PineconeVectorRetriever remain.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from retrievers import (
    DEFAULT_RETRIEVER,
    get_retriever,
    get_retriever_by_name,
    invalidate_retriever_cache,
    list_available_retrievers,
)
from retrievers.vectorize import VectorizeRetriever
from retrievers import factory as _factory



@pytest.fixture
def anyio_backend():
    return "asyncio"

# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_cache():
    invalidate_retriever_cache()
    yield
    invalidate_retriever_cache()


@pytest.fixture
def patched_vectorize(monkeypatch):
    """Stub every `vectorize_client.*` call so VectorizeRetriever can be
    exercised without network/account credentials."""
    import vectorize_client as vc

    state: dict[str, Any] = {
        "configured": True,
        "upsert_calls": [],
        "delete_calls": [],
        "query_calls": [],
        "get_by_ids_calls": [],
    }

    monkeypatch.setattr(vc, "VECTORIZE_DIMENSIONS", 1024, raising=True)
    monkeypatch.setattr(vc, "VECTORIZE_INDEX_NAME", "test-index", raising=True)
    monkeypatch.setattr(vc, "is_configured", lambda: state["configured"])

    async def _upsert(vectors):
        state["upsert_calls"].append(list(vectors))
        return {"upserted": len(vectors)}

    async def _query(vector, top_k=10, metadata_filter=None,
                     return_values=False, return_metadata=True):
        state["query_calls"].append({
            "top_k": top_k, "metadata_filter": metadata_filter,
            "return_values": return_values, "return_metadata": return_metadata,
        })
        return [{"id": "v1", "score": 0.9, "metadata": {"chapter_id": "c1"}}]

    async def _delete(ids):
        state["delete_calls"].append(list(ids))
        return len(ids)

    async def _get_by_ids(ids):
        state["get_by_ids_calls"].append(list(ids))
        return [{"id": i, "metadata": {}} for i in ids]

    async def _index_info():
        return {"vector_count": 42}

    async def _index_config():
        return {"dimensions": 1024, "metric": "cosine"}

    monkeypatch.setattr(vc, "upsert_vectors", _upsert)
    monkeypatch.setattr(vc, "query_vectors", _query)
    monkeypatch.setattr(vc, "delete_vectors", _delete)
    monkeypatch.setattr(vc, "get_vectors_by_ids", _get_by_ids)
    monkeypatch.setattr(vc, "get_index_info", _index_info)
    monkeypatch.setattr(vc, "get_index_config", _index_config)
    return state


# ── Factory ─────────────────────────────────────────────────────────────────

def test_default_and_listing():
    assert DEFAULT_RETRIEVER == "vectorize"
    names = list_available_retrievers()
    assert "vectorize" in names
    assert "vertex" not in names  # Task #490 — vertex retriever deleted


def test_get_by_name_returns_correct_class():
    assert isinstance(get_retriever_by_name("vectorize"), VectorizeRetriever)


def test_get_by_name_vertex_raises_after_task_490():
    with pytest.raises(ValueError):
        get_retriever_by_name("vertex")


def test_get_by_name_memoises():
    a = get_retriever_by_name("vectorize")
    b = get_retriever_by_name("VECTORIZE")  # case insensitive
    assert a is b


def test_get_by_name_unknown_raises():
    with pytest.raises(ValueError):
        get_retriever_by_name("not_a_real_backend")


def test_env_override(monkeypatch):
    monkeypatch.setenv("RAG_RETRIEVER", "mongodb_vector")
    assert _factory.get_active_retriever_name() == "mongodb_vector"
    monkeypatch.setenv("RAG_RETRIEVER", "garbage")
    assert _factory.get_active_retriever_name() == DEFAULT_RETRIEVER


def test_env_default(monkeypatch):
    monkeypatch.delenv("RAG_RETRIEVER", raising=False)
    assert _factory.get_active_retriever_name() == DEFAULT_RETRIEVER


@pytest.mark.anyio
async def test_get_retriever_falls_back_to_env(monkeypatch):
    # No DB override available → factory should yield the env-default
    # without raising. Force the DB-read code path to return None.
    async def _no_override():
        return None
    monkeypatch.setattr(_factory, "_read_db_override", _no_override)
    monkeypatch.delenv("RAG_RETRIEVER", raising=False)
    r = await get_retriever()
    assert isinstance(r, VectorizeRetriever)


# ── Vectorize adapter (delegation correctness) ──────────────────────────────

@pytest.mark.anyio
async def test_vectorize_adapter_delegates(patched_vectorize):
    r = VectorizeRetriever()
    assert r.name == "vectorize"
    assert r.dimensions == 1024
    assert r.is_configured() is True

    out = await r.query([0.1] * 1024, top_k=3, metadata_filter={"subject_id": "s1"})
    assert out and out[0]["id"] == "v1"
    call = patched_vectorize["query_calls"][-1]
    assert call["top_k"] == 3
    assert call["metadata_filter"] == {"subject_id": "s1"}

    res = await r.upsert([{"id": "x", "values": [0.0] * 1024, "metadata": {}}])
    assert res == {"upserted": 1}

    n = await r.delete(["a", "b"])
    assert n == 2

    got = await r.get_by_ids(["a", "b"])
    assert {g["id"] for g in got} == {"a", "b"}

    info = await r.index_info()
    cfg = await r.index_config()
    assert info["vector_count"] == 42
    assert cfg["dimensions"] == 1024
    assert cfg["name"] == "test-index"


@pytest.mark.anyio
async def test_vectorize_unconfigured_short_circuits_via_is_configured(patched_vectorize):
    patched_vectorize["configured"] = False
    r = VectorizeRetriever()
    assert r.is_configured() is False


# ── Vertex adapter — REMOVED Task #490 ──────────────────────────────────────
# `retrievers/vertex.py` and the entire VertexVectorSearchRetriever surface
# were deleted alongside the Vertex chat / multilingual-embed paths. The
# admin retriever-toggle test below stays — it now uses the Pinecone
# entry to exercise the "unknown backend" rejection path.


@pytest.mark.anyio
async def test_admin_toggle_rejects_unknown_name():
    from routes import admin_retriever as ar
    from fastapi import HTTPException
    payload = ar.RetrieverSwitchPayload(active="not_a_real_backend")
    with pytest.raises(HTTPException) as exc:
        await ar.update_retriever_config(payload, _admin={"id": "admin"})
    assert exc.value.status_code == 400


@pytest.mark.anyio
async def test_admin_toggle_rejects_vertex_after_task_490():
    """`PUT /admin/retriever/config` must refuse `vertex` because the
    backend was deleted in Task #490."""
    from routes import admin_retriever as ar
    from fastapi import HTTPException
    payload = ar.RetrieverSwitchPayload(active="vertex")
    with pytest.raises(HTTPException) as exc:
        await ar.update_retriever_config(payload, _admin={"id": "admin"})
    assert exc.value.status_code == 400
