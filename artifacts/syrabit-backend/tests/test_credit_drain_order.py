"""Task #513 §H — credit-drain order assertion.

The four-cloud lock (CF 40 / Azure 30 / AWS 20 / GCP 10) requires that
the **specialist credit pools burn first** so the cheap-but-quota-bound
providers are exhausted before the expensive paid hot path. This test
freezes the per-feature provider order in `config.PROVIDER_PRIORITY`
to the founder-locked sequence so a future PR cannot quietly demote
Sarvam (Indic) or Workers-AI (English fallback) to "after Azure" — a
change which would silently triple monthly billing because every chat
turn would burn a paid Azure call before touching the free credit
pool.

Pools covered:
  * `english_rag_chat`  — Azure primary; Workers-AI fallbacks AFTER
                          (Azure is the locked English-chat primary
                          per V4 §4 / `replit.md` Architecture
                          decisions). The drain assertion here is
                          that every Workers-AI variant in the pool
                          appears AFTER `azure_openai`, not before.
  * `assamese_rag_chat` — Sarvam (specialist) BEFORE Workers-AI Indic
                          fallback. The Indic specialist's startup
                          credit pool ($500) MUST be drained before
                          we burn the Cloudflare free-tier slot.
  * `content_format`    — Vertex BEFORE Workers-AI llama33_70b. Vertex
                          startup credits ($2 000) drain before the
                          free-tier formatter fallback.
  * `embed`             — workers_ai_custom (free Cloudflare worker)
                          BEFORE the Azure / generic fallback. Embed
                          is the highest-volume call type so the free
                          tier MUST be drained first regardless of
                          credit pool size.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture(scope="module")
def cfg():
    return importlib.import_module("config")


def _assert_index_before(pool: list, earlier: str, later: str) -> None:
    assert earlier in pool, f"missing provider {earlier} in pool {pool}"
    assert later in pool, f"missing provider {later} in pool {pool}"
    assert pool.index(earlier) < pool.index(later), (
        f"credit-drain order violated: {earlier} must appear BEFORE "
        f"{later} in pool {pool}"
    )


def test_english_chat_workers_ai_drains_after_azure(cfg):
    pool = cfg.PROVIDER_PRIORITY["english_rag_chat"]
    # Azure is the locked English chat primary; every Workers-AI
    # variant in the pool MUST be a strict fallback (later index).
    _assert_index_before(pool, "azure_openai", "workers_ai_mistral_7b")
    _assert_index_before(pool, "azure_openai", "workers_ai_llama32_3b")
    _assert_index_before(pool, "azure_openai", "workers_ai")


def test_assamese_chat_sarvam_drains_before_workers_ai(cfg):
    pool = cfg.PROVIDER_PRIORITY["assamese_rag_chat"]
    _assert_index_before(pool, "sarvam", "workers_ai_indic")


def test_content_format_vertex_drains_before_workers_ai(cfg):
    pool = cfg.PROVIDER_PRIORITY["content_format"]
    _assert_index_before(pool, "vertex", "workers_ai_llama33_70b")


def test_embed_workers_custom_drains_first(cfg):
    pool = cfg.PROVIDER_PRIORITY["embed"]
    _assert_index_before(pool, "workers_ai_custom", "azure_openai")
    _assert_index_before(pool, "workers_ai_custom", "workers_ai")


def test_no_retired_providers_present(cfg):
    """Task #491 retired Cerebras, Cohere, Voyage-AI from runtime
    paths. A regression that re-introduces them in any pool would
    quietly re-burn a credit pool we no longer have a provider
    integration for."""
    retired = {"cerebras", "cohere", "voyage_ai", "bedrock"}
    for pool_name, pool in cfg.PROVIDER_PRIORITY.items():
        present = retired & set(pool)
        assert not present, (
            f"retired provider(s) {present} must not appear in pool "
            f"{pool_name!r} — see Task #491 / Task #347 decommissions"
        )
