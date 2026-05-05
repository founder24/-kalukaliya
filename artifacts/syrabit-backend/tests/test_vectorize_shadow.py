"""Task #383 — tests for the Vectorize shadow retriever wrapper."""
from __future__ import annotations

import asyncio

import pytest

from retrievers.base import Retriever


class _StubRetriever(Retriever):
    def __init__(self, name: str = "stub", configured: bool = True,
                 results=None, dimensions: int = 8) -> None:
        self.name = name
        self.dimensions = dimensions
        self._configured = configured
        self._results = results or []
        self.upserts: list = []
        self.queries: list = []
        self.deletes: list = []

    def is_configured(self) -> bool:
        return self._configured

    async def query(self, vector, top_k=10, metadata_filter=None,
                    return_values=False, return_metadata=True):
        self.queries.append({"vector": vector, "top_k": top_k})
        return list(self._results)

    async def upsert(self, vectors):
        self.upserts.append(list(vectors))
        return {"upserted": len(vectors)}

    async def delete(self, ids):
        self.deletes.append(list(ids))
        return len(ids)

    async def get_by_ids(self, ids):
        return []

    async def index_info(self):
        return {"name": self.name}

    async def index_config(self):
        return {"dimensions": self.dimensions}


@pytest.fixture(autouse=True)
def _reset_state():
    from vectorize_shadow import reset_for_tests
    reset_for_tests()
    yield
    reset_for_tests()


@pytest.mark.asyncio
async def test_shadow_mirrors_upsert_when_enabled():
    from vectorize_shadow import ShadowRetriever, snapshot
    primary = _StubRetriever(name="pinecone",
                             results=[{"id": "a"}, {"id": "b"}])
    shadow = _StubRetriever(name="vectorize",
                            results=[{"id": "a"}, {"id": "c"}])
    wrapped = ShadowRetriever(primary, shadow, enabled=True,
                              shadow_sample_rate=1.0)
    out = await wrapped.upsert([{"id": "a", "values": [0.0] * 8, "metadata": {}}])
    # Give the asyncio.create_task a chance to run.
    await asyncio.sleep(0.01)
    assert out == {"upserted": 1}
    assert len(shadow.upserts) == 1, "shadow should have mirrored the upsert"
    snap = snapshot()
    assert snap["writes_mirrored"] == 1


@pytest.mark.asyncio
async def test_shadow_skips_when_disabled():
    from vectorize_shadow import ShadowRetriever
    primary = _StubRetriever(name="pinecone")
    shadow = _StubRetriever(name="vectorize")
    wrapped = ShadowRetriever(primary, shadow, enabled=False)
    await wrapped.upsert([{"id": "a", "values": [0.0] * 8, "metadata": {}}])
    await asyncio.sleep(0.01)
    assert shadow.upserts == []


@pytest.mark.asyncio
async def test_shadow_query_records_recall():
    from vectorize_shadow import ShadowRetriever, snapshot
    primary = _StubRetriever(name="pinecone",
                             results=[{"id": "a"}, {"id": "b"}, {"id": "c"}])
    shadow = _StubRetriever(name="vectorize",
                            results=[{"id": "a"}, {"id": "b"}, {"id": "z"}])
    wrapped = ShadowRetriever(primary, shadow, enabled=True,
                              shadow_sample_rate=1.0)
    results = await wrapped.query([0.1] * 8, top_k=3)
    assert [r["id"] for r in results] == ["a", "b", "c"]
    await asyncio.sleep(0.02)
    snap = snapshot()
    assert snap["queries_mirrored"] == 1
    # Overlap of 2/3 = 0.6667.
    assert 0.6 < snap["avg_recall_overlap"] < 0.7


@pytest.mark.asyncio
async def test_shadow_query_failure_does_not_break_primary():
    from vectorize_shadow import ShadowRetriever, snapshot

    class _BrokenShadow(_StubRetriever):
        async def query(self, *a, **k):
            raise RuntimeError("vectorize down")

    primary = _StubRetriever(name="pinecone",
                             results=[{"id": "a"}])
    shadow = _BrokenShadow(name="vectorize")
    wrapped = ShadowRetriever(primary, shadow, enabled=True,
                              shadow_sample_rate=1.0)
    results = await wrapped.query([0.1] * 8, top_k=1)
    assert results == [{"id": "a"}]  # primary still authoritative
    await asyncio.sleep(0.02)
    assert snapshot()["queries_failed"] >= 1


def test_maybe_wrap_returns_primary_when_flag_off(monkeypatch):
    monkeypatch.setattr("config.VECTORIZE_SHADOW_ON", False)
    from vectorize_shadow import maybe_wrap_with_shadow
    primary = _StubRetriever(name="pinecone")
    assert maybe_wrap_with_shadow(primary) is primary


def test_maybe_wrap_skips_self_shadow(monkeypatch):
    monkeypatch.setattr("config.VECTORIZE_SHADOW_ON", True)
    from vectorize_shadow import maybe_wrap_with_shadow
    primary = _StubRetriever(name="vectorize")
    assert maybe_wrap_with_shadow(primary) is primary
