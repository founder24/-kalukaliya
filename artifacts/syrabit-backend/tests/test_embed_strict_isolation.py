"""Task #382 — Strict provider isolation under EMBED_PROVIDER_PRIMARY=workers_ai_custom.

Code review (round 2) flagged that even with workers_ai_custom as the
primary, the dispatcher could still fall through to legacy providers
via the exclusion-redraw loop on worker failure, and the chunk
embedder silently fell back to Cohere/Pinecone Inference. The new
contract is:

  * call_embed_with_dispatch short-circuits to workers_embed and
    raises on failure — no silent fallback to legacy providers.
  * chunk_embedder._embed_batch returns None slots on worker failure
    so embed_chunks_bulk marks chunks as failed (retry next run)
    rather than calling Cohere/Pinecone Inference.
  * Both behaviors only relax when the operator explicitly flips
    EMBED_PROVIDER_PRIMARY to a legacy provider name.

These tests booby-trap the legacy providers so any silent fallback
surfaces as a test failure rather than a network call.
"""
from __future__ import annotations

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("WORKERS_EMBED_URL", "https://embed.test.local")
os.environ.setdefault("WORKERS_EMBED_SECRET", "test-secret")


def _booby_trap_legacy(monkeypatch):
    """Replace every legacy embed provider with a stub that raises on
    any call. If the dispatcher silently falls through under the
    workers_ai_custom flag, one of these will fire and fail the test.
    """
    for mod_name in (
        "providers.cohere",
        "providers.voyage_ai",
        "providers.pinecone_ai",
        "providers.azure_openai",
        "vertex_services",
    ):
        stub = types.ModuleType(mod_name)
        async def _raise(*a, _mn=mod_name, **k):  # noqa: ANN001, ANN002
            raise AssertionError(
                f"{_mn} must NOT be called under "
                "EMBED_PROVIDER_PRIMARY=workers_ai_custom"
            )
        stub.embed = _raise
        stub.embed_query = _raise
        stub.embed_text = _raise
        stub.embed_one = _raise
        stub.embed_passages = _raise
        stub.call_embed = _raise
        stub.ENABLED = False
        monkeypatch.setitem(sys.modules, mod_name, stub)
    # Also booby-trap providers.cloudflare_ai (the bge-m3 fallback that
    # the workers_ai pool entry points at). The dispatcher's old
    # exclusion-redraw loop would have walked there after the worker
    # failed; the new short-circuit must skip it entirely.
    cf_stub = types.ModuleType("providers.cloudflare_ai")
    async def _cf_raise(*a, **k):  # noqa: ANN001, ANN002
        raise AssertionError(
            "providers.cloudflare_ai (bge-m3 fallback) must NOT be "
            "called under EMBED_PROVIDER_PRIMARY=workers_ai_custom"
        )
    cf_stub.embed = _cf_raise
    monkeypatch.setitem(sys.modules, "providers.cloudflare_ai", cf_stub)


def _disable_embed_cache(monkeypatch):
    import embed_cache
    monkeypatch.setattr(embed_cache, "get_cached_embedding", lambda *a, **k: None, raising=True)
    monkeypatch.setattr(embed_cache, "set_cached_embedding", lambda *a, **k: True, raising=True)


@pytest.mark.asyncio
async def test_dispatch_raises_on_worker_failure_no_legacy_fallback(monkeypatch):
    """When EMBED_PROVIDER_PRIMARY=workers_ai_custom and the worker
    fails, call_embed_with_dispatch must raise — NOT fall through to
    Cohere/Voyage/Vertex/Pinecone Inference/bge-m3."""
    import config
    monkeypatch.setattr(config, "EMBED_PROVIDER_PRIMARY", "workers_ai_custom", raising=True)

    import providers.workers_embed as we
    monkeypatch.setattr(we, "is_enabled", lambda: True, raising=True)
    async def _embed_fail(texts, *, input_type="search_document"):
        raise RuntimeError("worker 503")
    monkeypatch.setattr(we, "embed", _embed_fail, raising=True)

    _booby_trap_legacy(monkeypatch)
    _disable_embed_cache(monkeypatch)

    import llm
    with pytest.raises(RuntimeError, match="workers_ai_custom failed"):
        await llm.call_embed_with_dispatch(
            "What is photosynthesis?",
            task_type="RETRIEVAL_QUERY",
            lang="en",
        )


@pytest.mark.asyncio
async def test_dispatch_raises_when_worker_returns_empty(monkeypatch):
    """An empty vector list must raise too — not be silently treated
    as a legacy-provider opportunity."""
    import config
    monkeypatch.setattr(config, "EMBED_PROVIDER_PRIMARY", "workers_ai_custom", raising=True)

    import providers.workers_embed as we
    monkeypatch.setattr(we, "is_enabled", lambda: True, raising=True)
    async def _embed_empty(texts, *, input_type="search_document"):
        return []
    monkeypatch.setattr(we, "embed", _embed_empty, raising=True)

    _booby_trap_legacy(monkeypatch)
    _disable_embed_cache(monkeypatch)

    import llm
    with pytest.raises(RuntimeError, match="returned no vectors"):
        await llm.call_embed_with_dispatch("x", task_type="RETRIEVAL_QUERY", lang="en")


@pytest.mark.asyncio
async def test_dispatch_raises_when_worker_secret_missing(monkeypatch):
    """If WORKERS_EMBED_URL/SECRET are missing under the new default
    flag, the dispatcher must surface a clear configuration error
    instead of silently degrading to a legacy provider."""
    import config
    monkeypatch.setattr(config, "EMBED_PROVIDER_PRIMARY", "workers_ai_custom", raising=True)

    import providers.workers_embed as we
    monkeypatch.setattr(we, "is_enabled", lambda: False, raising=True)

    _booby_trap_legacy(monkeypatch)
    _disable_embed_cache(monkeypatch)

    import llm
    with pytest.raises(RuntimeError, match="WORKERS_EMBED_URL"):
        await llm.call_embed_with_dispatch("x", task_type="RETRIEVAL_QUERY", lang="en")


@pytest.mark.asyncio
async def test_chunk_embedder_does_not_silently_fall_back_to_cohere(monkeypatch):
    """When the worker returns None slots under the workers_ai_custom
    flag, chunk_embedder must surface those Nones (the bulk caller
    marks the chunks as failed) — it must NOT call Cohere or Pinecone
    Inference behind the operator's back."""
    import config
    monkeypatch.setattr(config, "EMBED_PROVIDER_PRIMARY", "workers_ai_custom", raising=True)

    import providers.workers_embed as we
    monkeypatch.setattr(we, "is_enabled", lambda: True, raising=True)
    async def _empty(texts, *, input_type="search_document"):
        return []  # triggers the None-slots return inside _workers_custom_embed_batch
    monkeypatch.setattr(we, "embed", _empty, raising=True)

    _booby_trap_legacy(monkeypatch)

    from providers import chunk_embedder
    out = await chunk_embedder._embed_batch(["chunk text 1", "chunk text 2"])
    # Strict isolation contract: empty worker response → None slots,
    # not a silent Cohere/Pinecone retry.
    assert out == [None, None]


@pytest.mark.asyncio
async def test_chunk_embedder_rollback_path_uses_legacy_chain(monkeypatch):
    """Sanity check the rollback contract: when the operator flips
    EMBED_PROVIDER_PRIMARY to a legacy name, the legacy multi-provider
    chain MUST be reachable (otherwise the rollback story is broken).
    """
    import config
    monkeypatch.setattr(config, "EMBED_PROVIDER_PRIMARY", "cohere", raising=True)

    # Cohere stub that succeeds — proves the legacy path is reachable
    # and exercised under the rollback flag.
    cohere_calls: list = []
    cohere_stub = types.ModuleType("providers.cohere")
    async def _cohere_embed(texts, *, input_type="search_document"):
        cohere_calls.append({"texts": list(texts), "input_type": input_type})
        return [[0.1] * 1024 for _ in texts]
    cohere_stub.embed = _cohere_embed
    cohere_stub.ENABLED = True
    monkeypatch.setitem(sys.modules, "providers.cohere", cohere_stub)

    # Worker booby-trapped — must NOT be called on the rollback path.
    we_stub = types.ModuleType("providers.workers_embed")
    async def _worker_called(*a, **k):  # noqa: ANN001, ANN002
        raise AssertionError("workers_embed must NOT be called on rollback path")
    we_stub.embed = _worker_called
    we_stub.is_enabled = lambda: True
    monkeypatch.setitem(sys.modules, "providers.workers_embed", we_stub)

    from providers import chunk_embedder
    out = await chunk_embedder._embed_batch(["a", "b"])
    assert len(out) == 2 and all(len(v) == 1024 for v in out)
    assert len(cohere_calls) == 1
    assert cohere_calls[0]["texts"] == ["a", "b"]
