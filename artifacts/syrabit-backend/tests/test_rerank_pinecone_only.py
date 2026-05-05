"""Task #382 — Rerank dispatch must short-circuit to Pinecone when
``RERANK_PROVIDER=pinecone_only`` (the new default after Task #382).

The legacy weighted draw could in principle reach cohere / azure /
workers_ai entries; the short-circuit ensures none of those are
touched on the rerank path.
"""
from __future__ import annotations

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _install_pinecone_stub(monkeypatch, scores):
    import providers.pinecone_ai as pc
    calls: list[dict] = []

    async def _rerank(query, documents, *, top_n=None, model=None):
        calls.append({"query": query, "documents": list(documents), "top_n": top_n})
        return list(scores)

    monkeypatch.setattr(pc, "rerank", _rerank, raising=True)
    return calls


def _block_other_rerank(monkeypatch):
    # Cohere doesn't actually expose a rerank endpoint via the gateway,
    # but make sure the dispatcher isn't even tempted: stub the module
    # so any access raises immediately.
    for mod_name in ("providers.cohere", "providers.azure_openai"):
        stub = types.ModuleType(mod_name)
        def _raise(*a, **k):  # noqa: ANN001, ANN002
            raise AssertionError(f"{mod_name} should not be called under RERANK_PROVIDER=pinecone_only")
        stub.rerank = _raise
        stub.embed = _raise
        stub.ENABLED = False
        monkeypatch.setitem(sys.modules, mod_name, stub)


@pytest.mark.asyncio
async def test_rerank_short_circuits_to_pinecone_only(monkeypatch):
    import config
    monkeypatch.setattr(config, "RERANK_PROVIDER", "pinecone_only", raising=True)
    _block_other_rerank(monkeypatch)
    calls = _install_pinecone_stub(monkeypatch, scores=[0.1, 0.9, 0.5])

    import llm
    docs = [{"text": "a"}, {"text": "b"}, {"text": "c"}]
    out = await llm.call_rerank_with_dispatch("query", docs, lang="en")
    # Sorted by score desc → b, c, a
    assert [d["text"] for d in out] == ["b", "c", "a"]
    assert len(calls) == 1
    assert calls[0]["documents"] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_rerank_short_circuit_returns_unranked_on_failure(monkeypatch):
    import config
    monkeypatch.setattr(config, "RERANK_PROVIDER", "pinecone_only", raising=True)
    import providers.pinecone_ai as pc

    async def _boom(*a, **k):
        raise RuntimeError("pinecone down")

    monkeypatch.setattr(pc, "rerank", _boom, raising=True)

    import llm
    docs = ["a", "b", "c"]
    out = await llm.call_rerank_with_dispatch("query", docs, lang="en")
    # Failure under pinecone_only short-circuit → original list unchanged.
    assert out == docs


@pytest.mark.asyncio
async def test_rerank_short_circuit_no_weighted_fallback_to_workers_ai(monkeypatch):
    """Under pinecone_only, the dispatcher MUST NOT enter the weighted
    fallback loop that historically tried workers_ai/cohere/azure on
    pinecone failure."""
    import config
    monkeypatch.setattr(config, "RERANK_PROVIDER", "pinecone_only", raising=True)

    import providers.pinecone_ai as pc

    async def _empty(*a, **k):
        raise RuntimeError("intentional fail")

    monkeypatch.setattr(pc, "rerank", _empty, raising=True)

    # Booby-trap select_provider so a fall-through to the weighted loop
    # would surface as an explicit assertion error.
    import llm
    def _trap(*a, **k):
        raise AssertionError("weighted rerank loop should not be entered under pinecone_only")
    monkeypatch.setattr(llm, "select_provider", _trap, raising=True)

    docs = ["x", "y"]
    out = await llm.call_rerank_with_dispatch("q", docs, lang="en")
    assert out == docs
