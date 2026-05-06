"""
Task #490 — Option D embed failover.

When primary embed (Workers-AI Gemma + Qwen3 → Pinecone
`cached_gemma_today`) is unavailable, the system MUST NOT silently
fall back to a Vertex multilingual embed in a second Pinecone
namespace. Instead it enters degraded "cache-only" mode:
  - serve cached vectors when present
  - enqueue a deferred-embed job to AWS SQS for fresh content
  - never write a Vertex embedding into Pinecone

These tests pin that contract by asserting the obvious negatives
(no Vertex embed module, no `fallback_vertex_pending_reembed`
namespace literal in the codebase, no `RAG_EMBEDDING_PROVIDER=fallback`
toggle wiring).
"""

from __future__ import annotations

import importlib
import pathlib
import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_vertex_embed_provider_module_is_gone():
    """`providers/vertex_embed.py` was deleted in Task #490."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("providers.vertex_embed")


def test_vertex_retriever_module_is_gone():
    """`retrievers/vertex.py` was deleted in Task #490."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("retrievers.vertex")


def test_no_fallback_vertex_pending_reembed_namespace_in_backend():
    """The Option-A fallback namespace must not exist anywhere in the
    backend — Option D replaces it with a cache-only degraded path."""
    bad = "fallback" + "_vertex_pending_" + "reembed"  # split so this test file is not its own match
    self_path = pathlib.Path(__file__).resolve()
    hits: list[str] = []
    for p in BACKEND_ROOT.rglob("*.py"):
        if p.resolve() == self_path:
            continue
        try:
            if bad in p.read_text(encoding="utf-8", errors="ignore"):
                hits.append(str(p.relative_to(REPO_ROOT)))
        except OSError:
            continue
    assert hits == [], f"Option D forbids the second Pinecone namespace; found in: {hits}"


def test_content_format_pool_priority_is_vertex_only():
    """`content_format` pool must select Vertex Gemini 2.5 Flash with the
    documented weight 10000 — this is the only place Vertex is allowed."""
    import config
    priority = config.PROVIDER_PRIORITY.get("content_format")
    assert priority == ["vertex"], (
        "content_format must be Vertex-only (Task #490); "
        f"got {priority!r}"
    )
    pool = config.POOL_WEIGHTS.get("content_format") or {}
    assert pool.get("vertex") == 10000, (
        "content_format vertex weight must be 10000 to lock it as the only "
        f"primary (Task #490 V4 §15 amendment); got {pool!r}"
    )


def test_vertex_is_absent_from_chat_pools():
    """No Vertex entries in any chat / embed / vector_search / vision pool."""
    import config
    forbidden_pools = (
        "english_chat",
        "assamese_chat",
        "long_context",
        "casual_chat",
        "embed_doc",
        "embed_query",
        "vector_search",
        "vision",
        "translate",
        "safety",
        "tts",
        "stt",
        "voice",
    )
    offenders: list[str] = []
    for pool in forbidden_pools:
        priority = config.PROVIDER_PRIORITY.get(pool, [])
        if "vertex" in priority:
            offenders.append(pool)
    assert offenders == [], (
        "Task #490: Vertex must only appear in `content_format`. "
        f"Found in: {offenders}"
    )
