"""
Task #525 — Regression test: index-time and query-time syllabus embeddings
must use the same provider/model.

Task #491 fixed a latent bug in `syllabus_embedder.py` where indexed vectors
went through `vertex_services.embed_text` (workers_ai_custom) but the
`classify()` path tried Pinecone Inference first, producing cross-embedding
-space retrieval. This test asserts that:

* `embed_chapter` records the workers_ai_custom model name on the upserted
  vector metadata (`embedding_model` field).
* `classify` calls `vertex_services.embed_text` and never reaches the
  Pinecone embed helper (`providers.pinecone_ai.embed_one`).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

import syllabus_embedder as se


class _FakeRetriever:
    name = "fake"
    dimensions = 1024

    def __init__(self) -> None:
        self.upserted: list[dict[str, Any]] = []
        self.query_calls: list[dict[str, Any]] = []

    def is_configured(self) -> bool:
        return True

    async def upsert(self, vectors):
        self.upserted.extend(vectors)
        return {"upserted": len(vectors)}

    async def delete(self, ids):
        return len(ids)

    async def get_by_ids(self, ids):
        return []

    async def query(self, vector, top_k=10, metadata_filter=None,
                    return_values=False, return_metadata=True):
        self.query_calls.append({
            "vector": vector, "top_k": top_k,
            "metadata_filter": metadata_filter,
        })
        return [{
            "id": "v1",
            "score": 0.99,
            "metadata": {
                "chapter_id": "ch1",
                "subject_id": "sub1",
                "subject_name": "Physics",
                "chapter_title": "Kinematics",
                "chapter_number": 1,
                "level": "chapter",
                "topic": "",
                "board": "AHSEC",
                "class_name": "11",
                "stream": "Science",
            },
        }]


class _FakeChapters:
    async def find_one(self, *_a, **_kw):
        return None


class _FakeSubjects:
    async def find_one(self, *_a, **_kw):
        return {
            "id": "sub1",
            "title": "Physics",
            "boardName": "AHSEC",
            "className": "11",
            "streamName": "Science",
        }


class _FakeDb:
    subjects = _FakeSubjects()
    chapters = _FakeChapters()


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_index_and_query_share_workers_ai_custom_model(monkeypatch):
    """embed_chapter tags vectors with workers_ai_custom; classify never
    reaches providers.pinecone_ai.embed_one."""

    embed_calls: list[dict[str, Any]] = []

    async def _fake_vt_embed(text, task_type="RETRIEVAL_DOCUMENT", **_kw):
        embed_calls.append({"text": text, "task_type": task_type})
        return [0.1] * 1024

    pinecone_embed_calls: list[Any] = []

    async def _fake_pc_embed_one(text, *, input_type="query"):
        pinecone_embed_calls.append({"text": text, "input_type": input_type})
        return [0.2] * 1024

    import vertex_services
    from providers import pinecone_ai

    monkeypatch.setattr(vertex_services, "embed_text", _fake_vt_embed,
                        raising=True)
    monkeypatch.setattr(pinecone_ai, "embed_one", _fake_pc_embed_one,
                        raising=True)

    fake_retriever = _FakeRetriever()

    embedder = se.SyllabusEmbedder(_FakeDb())

    async def _fake_get_retriever():
        return fake_retriever

    monkeypatch.setattr(embedder, "_get_retriever", _fake_get_retriever,
                        raising=True)

    # Disable the optional query-embed cache so we exercise the live path.
    try:
        import cache as _cache_mod
        if hasattr(_cache_mod, "_query_embed_cache"):
            monkeypatch.setattr(_cache_mod, "_query_embed_cache", None,
                                raising=True)
    except ImportError:
        pass

    loop = asyncio.new_event_loop()
    try:
        # ── Index path ─────────────────────────────────────────────────
        inserted = loop.run_until_complete(embedder.embed_chapter(
            chapter_id="ch1",
            subject_id="sub1",
            title="Kinematics",
            description="Motion in one dimension",
            topics=["Velocity", "Acceleration"],
            content="",
        ))

        assert inserted >= 1, "expected at least the chapter vector to upsert"
        assert fake_retriever.upserted, "no vectors were upserted"

        # Every upserted vector must declare workers_ai_custom as the
        # embedding model — this is the parity contract.
        for v in fake_retriever.upserted:
            model = v["metadata"].get("embedding_model", "")
            assert model.startswith("workers_ai_custom"), (
                f"index-time vector tagged with non-canonical model "
                f"{model!r}; expected workers_ai_custom/* (Task #491)"
            )

        # Index path went through vertex_services.embed_text, not pinecone.
        assert embed_calls, "vertex_services.embed_text was never called at index time"
        index_call_count = len(embed_calls)

        # ── Query path ─────────────────────────────────────────────────
        match = loop.run_until_complete(embedder.classify("what is velocity"))

        assert match is not None, "classify should return a match for the stub"
        assert len(embed_calls) > index_call_count, (
            "classify did not invoke vertex_services.embed_text — query "
            "path is not sharing the workers_ai_custom embedder"
        )

        last_call = embed_calls[-1]
        assert last_call["task_type"] == "RETRIEVAL_QUERY", (
            "classify should embed with task_type=RETRIEVAL_QUERY"
        )

        # The Pinecone embed helper must NEVER be touched by either path.
        assert not pinecone_embed_calls, (
            "providers.pinecone_ai.embed_one was called — this is the "
            "exact cross-embedding-space regression Task #491 fixed and "
            "Task #525 locks down"
        )
    finally:
        loop.close()
