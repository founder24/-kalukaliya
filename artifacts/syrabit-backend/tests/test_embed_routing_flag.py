"""Task #382 — Embed routing under EMBED_PROVIDER_PRIMARY flag.

Asserts that:
  * ``call_embed_with_dispatch`` routes to ``providers.workers_embed``
    when the pool's primary slot is ``workers_ai_custom`` and the
    worker is configured.
  * Voyage / Cohere / Vertex / Pinecone Inference are NOT called on
    that path (they were the legacy providers Task #382 disabled).
"""
from __future__ import annotations

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Make the worker provider self-enable so call_embed_with_dispatch's
# is_enabled() guard passes inside the test process.
os.environ.setdefault("WORKERS_EMBED_URL", "https://embed.test.local")
os.environ.setdefault("WORKERS_EMBED_SECRET", "test-secret")


def _install_workers_embed_stub(monkeypatch, vector):
    """Replace providers.workers_embed with a stub that records calls."""
    import providers.workers_embed as we

    calls: list[dict] = []

    async def _embed(texts, *, input_type="search_document"):
        calls.append({"texts": list(texts), "input_type": input_type})
        return [list(vector) for _ in texts]

    monkeypatch.setattr(we, "embed", _embed, raising=True)
    monkeypatch.setattr(we, "is_enabled", lambda: True, raising=True)
    return calls


def _block_legacy_providers(monkeypatch):
    """Force-import the legacy embed modules with raising stubs so any
    accidental call surfaces as a test failure rather than a silent
    network attempt."""
    for mod_name in ("providers.cohere", "providers.voyage_ai", "providers.pinecone_ai"):
        stub = types.ModuleType(mod_name)
        async def _raise(*a, **k):  # noqa: ANN001, ANN002
            raise AssertionError(f"{mod_name} should NOT be called under EMBED_PROVIDER_PRIMARY=workers_ai_custom")
        stub.embed = _raise
        stub.embed_query = _raise
        stub.embed_one = _raise
        stub.embed_passages = _raise
        stub.ENABLED = False
        # Use monkeypatch.setitem ONLY — direct sys.modules assignment
        # would leak across tests and break suites that depend on the
        # real providers (e.g. test_vertex_startup_probe).
        monkeypatch.setitem(sys.modules, mod_name, stub)


def _force_pool(monkeypatch):
    """Pin the embed pools to Task #382 layout (workers_ai_custom only)."""
    import config
    monkeypatch.setattr(
        config, "PROVIDER_PRIORITY",
        {**config.PROVIDER_PRIORITY,
         "embed": ["workers_ai_custom"],
         "embed_en": ["workers_ai_custom"],
         "embed_indic": ["workers_ai_custom"]},
        raising=True,
    )
    monkeypatch.setattr(
        config, "POOL_WEIGHTS",
        {**config.POOL_WEIGHTS,
         "embed": {"workers_ai_custom": 10000},
         "embed_en": {"workers_ai_custom": 10000},
         "embed_indic": {"workers_ai_custom": 10000}},
        raising=True,
    )


@pytest.mark.asyncio
async def test_embed_dispatch_routes_to_workers_ai_custom(monkeypatch):
    _force_pool(monkeypatch)
    _block_legacy_providers(monkeypatch)
    calls = _install_workers_embed_stub(monkeypatch, [0.42] * 1024)

    # Disable the embed cache so we exercise the dispatch path, not a hit.
    import embed_cache
    monkeypatch.setattr(embed_cache, "get_cached_embedding", lambda *a, **k: None, raising=True)
    monkeypatch.setattr(embed_cache, "set_cached_embedding", lambda *a, **k: True, raising=True)

    import llm
    vec = await llm.call_embed_with_dispatch(
        "Photosynthesis explained",
        task_type="RETRIEVAL_QUERY",
        lang="en",
    )
    assert len(vec) == 1024
    assert vec[0] == 0.42
    assert len(calls) == 1
    # Query path → search_query input_type passthrough.
    assert calls[0]["input_type"] == "search_query"


@pytest.mark.asyncio
async def test_embed_dispatch_indic_lang_also_routes_to_workers_custom(monkeypatch):
    _force_pool(monkeypatch)
    _block_legacy_providers(monkeypatch)
    calls = _install_workers_embed_stub(monkeypatch, [0.1] * 1024)

    import embed_cache
    monkeypatch.setattr(embed_cache, "get_cached_embedding", lambda *a, **k: None, raising=True)
    monkeypatch.setattr(embed_cache, "set_cached_embedding", lambda *a, **k: True, raising=True)

    import llm
    # Bengali script triggers the embed_indic sub-pool.
    vec = await llm.call_embed_with_dispatch(
        "ফটোসিন্থেসিস কী",
        task_type="RETRIEVAL_DOCUMENT",
        lang="bn",
    )
    assert len(vec) == 1024
    assert len(calls) == 1
    assert calls[0]["input_type"] == "search_document"
