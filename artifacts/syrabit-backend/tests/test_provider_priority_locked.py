"""Smoke test — POOL_WEIGHTS / PROVIDER_PRIORITY contract (Task #2).

Task #2 — 2026 blueprint:

  * English chat is a strict 3-position chain that the
    ``cost_caps._select_chat_primary()`` selector returns dynamically:

      Default order      :  vertex → vertex_flash_lite → workers_ai_llama32_3b
      Credit-runway flip :  workers_ai_llama32_3b → vertex_flash_lite → vertex
      (when projected GCP credit runway ≤ 90 days; cached for 60 s on
      a monotonic clock so the hot dispatch path never re-reads env /
      redis per turn).

  * Assamese chat is a strict 3-position chain:
      sarvam → vertex_assamese → retrieval_only

Other pools are unchanged:

  * content / assamese_content — Workers AI exclusively.
  * content_format — Vertex (primary) → Workers-AI Llama-3.3-70b.
  * translate — workers_ai_indic only.
  * tts — elevenlabs primary, deepgram named fallback, workers_ai tail.

Run::

    python -m pytest tests/test_provider_priority_locked.py -v
"""
from __future__ import annotations

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging

_PREV_LOGGING_DISABLE_LEVEL = logging.root.manager.disable
logging.disable(logging.CRITICAL)
try:
    from config import POOL_WEIGHTS, PROVIDER_PRIORITY
    from cost_caps import (
        _CHAT_CHAIN_DEFAULT,
        _CHAT_CHAIN_FLIPPED,
        _select_chat_primary,
        _reset_chat_primary_cache,
    )
finally:
    logging.disable(_PREV_LOGGING_DISABLE_LEVEL)


_CHAT_CHAIN_PROVIDERS = {"vertex", "vertex_flash_lite", "workers_ai_llama32_3b"}


def _clear_runway_env(monkeypatch):
    monkeypatch.delenv("CHAT_CREDIT_RUNWAY_DAYS", raising=False)
    monkeypatch.delenv("GCP_CREDITS_REMAINING_USD", raising=False)
    monkeypatch.delenv("CHAT_PRIMARY_OVERRIDE", raising=False)
    _reset_chat_primary_cache()


# ───────────────────────────────────────────────────────────────────────────
# english_rag_chat — Task #2 3-position chain
# ───────────────────────────────────────────────────────────────────────────


def test_english_rag_chat_chain_is_exactly_three_positions(monkeypatch):
    """Task #2 — _select_chat_primary returns exactly 3 entries."""
    _clear_runway_env(monkeypatch)
    chain = _select_chat_primary()
    assert isinstance(chain, list)
    assert len(chain) == 3, (
        f"english chat chain must be exactly 3 positions (Task #2); got {chain}"
    )
    assert set(chain) == _CHAT_CHAIN_PROVIDERS, (
        f"english chat chain must be exactly {_CHAT_CHAIN_PROVIDERS}; got {chain}"
    )
    assert "azure_openai" not in chain


def test_english_rag_chat_default_chain_starts_with_vertex(monkeypatch):
    _clear_runway_env(monkeypatch)
    chain = _select_chat_primary()
    assert chain[0] == "vertex"
    assert chain[1] == "vertex_flash_lite"
    assert chain[2] == "workers_ai_llama32_3b"
    assert chain == list(_CHAT_CHAIN_DEFAULT)


def test_english_rag_chat_credit_flip_swaps_head(monkeypatch):
    _clear_runway_env(monkeypatch)
    monkeypatch.setenv("CHAT_CREDIT_RUNWAY_DAYS", "89")
    _reset_chat_primary_cache()
    chain = _select_chat_primary()
    assert chain == list(_CHAT_CHAIN_FLIPPED)
    assert chain[0] == "workers_ai_llama32_3b"
    assert chain[-1] == "vertex"


def test_english_rag_chat_credit_flip_at_threshold(monkeypatch):
    _clear_runway_env(monkeypatch)
    monkeypatch.setenv("CHAT_CREDIT_RUNWAY_DAYS", "90")
    _reset_chat_primary_cache()
    chain = _select_chat_primary()
    assert chain[0] == "workers_ai_llama32_3b"


def test_english_rag_chat_healthy_runway_uses_default(monkeypatch):
    _clear_runway_env(monkeypatch)
    monkeypatch.setenv("CHAT_CREDIT_RUNWAY_DAYS", "180")
    _reset_chat_primary_cache()
    chain = _select_chat_primary()
    assert chain[0] == "vertex"


def test_english_rag_chat_override_vertex(monkeypatch):
    _clear_runway_env(monkeypatch)
    monkeypatch.setenv("CHAT_CREDIT_RUNWAY_DAYS", "5")
    monkeypatch.setenv("CHAT_PRIMARY_OVERRIDE", "vertex")
    _reset_chat_primary_cache()
    chain = _select_chat_primary()
    assert chain == list(_CHAT_CHAIN_DEFAULT)


def test_english_rag_chat_override_workers(monkeypatch):
    _clear_runway_env(monkeypatch)
    monkeypatch.setenv("CHAT_PRIMARY_OVERRIDE", "workers_ai_llama32_3b")
    _reset_chat_primary_cache()
    chain = _select_chat_primary()
    assert chain == list(_CHAT_CHAIN_FLIPPED)


def test_english_rag_chat_pool_membership_negative():
    chain = list(PROVIDER_PRIORITY["english_rag_chat"])
    weights = POOL_WEIGHTS["english_rag_chat"]
    assert "azure_openai" not in chain
    assert "azure_openai" not in weights
    for required in _CHAT_CHAIN_PROVIDERS:
        assert required in chain


def test_english_rag_chat_weights_have_chain_members():
    weights = POOL_WEIGHTS["english_rag_chat"]
    for required in _CHAT_CHAIN_PROVIDERS:
        assert weights.get(required, 0) > 0


# ───────────────────────────────────────────────────────────────────────────
# Other locked pools
# ───────────────────────────────────────────────────────────────────────────


def test_content_workers_ai_primary():
    from llm import select_provider
    from collections import Counter

    weights = POOL_WEIGHTS["content"]
    primaries = {"workers_ai_mistral_7b", "workers_ai_llama32_3b"}
    for p in primaries:
        assert weights[p] == 10000
    assert "vertex" not in weights
    assert weights.get("workers_ai", 0) == 0

    draws = 600
    counts = Counter(select_provider("content", lang="en") for _ in range(draws))
    primary_share = sum(counts[p] for p in primaries) / draws
    assert primary_share == 1.0

    as_weights = POOL_WEIGHTS["assamese_content"]
    assert as_weights["workers_ai_indic"] == 10000
    assert "vertex" not in as_weights

    polish_weights = POOL_WEIGHTS["content_format"]
    assert set(polish_weights.keys()) == {"vertex", "workers_ai_llama33_70b"}
    assert polish_weights["vertex"] == 10000


def test_assamese_rag_chat_three_leg_chain():
    """Task #2 — Assamese chain: sarvam → vertex_assamese → retrieval_only."""
    weights = POOL_WEIGHTS["assamese_rag_chat"]
    assert weights.get("sarvam") == 10000
    assert weights.get("vertex_assamese") == 10000
    assert weights.get("retrieval_only") == 0
    for forbidden in ("workers_ai_llama31_8b", "workers_ai", "azure_openai"):
        assert forbidden not in weights

    chain = list(PROVIDER_PRIORITY["assamese_rag_chat"])
    assert chain == ["sarvam", "vertex_assamese", "retrieval_only"]


def test_translate_workers_ai_indic_primary():
    from llm import select_provider
    from collections import Counter

    weights = POOL_WEIGHTS["translate"]
    assert "vertex" not in weights
    assert weights.get("workers_ai_indic", 0) > 0

    counts = Counter(select_provider("translate", lang="as") for _ in range(200))
    assert counts.get("workers_ai_indic", 0) >= 180


def test_workers_ai_fallback_uses_gpt_oss_20b():
    from llm import _PROVIDER_DEFAULT_MODELS
    assert _PROVIDER_DEFAULT_MODELS["workers_ai"] == "@cf/openai/gpt-oss-20b"


def test_no_azure_openai_anywhere_in_provider_priority():
    for feature, chain in PROVIDER_PRIORITY.items():
        assert "azure_openai" not in list(chain)
    for feature, weights in POOL_WEIGHTS.items():
        assert "azure_openai" not in weights


def test_tts_elevenlabs_primary_task_2():
    """Task #2 — TTS chain: elevenlabs(primary) → deepgram → workers_ai."""
    chain = list(PROVIDER_PRIORITY["tts"])
    assert chain[0] == "elevenlabs"
    assert chain[1] == "deepgram"
    assert chain[-1] == "workers_ai"
    weights = POOL_WEIGHTS["tts"]
    assert weights.get("elevenlabs", 0) > weights.get("deepgram", 0)
    assert weights.get("workers_ai", 0) == 0


# ───────────────────────────────────────────────────────────────────────────
# Cache + chain regression
# ───────────────────────────────────────────────────────────────────────────


@pytest.fixture
def _no_runway_env(monkeypatch):
    _clear_runway_env(monkeypatch)
    yield


def test_select_chat_primary_cache_is_60s_monotonic(monkeypatch, _no_runway_env):
    import cost_caps as _cc

    monkeypatch.setenv("CHAT_PRIMARY_OVERRIDE", "vertex")
    _cc._reset_chat_primary_cache()
    first = _cc._select_chat_primary()
    assert first[0] == "vertex"

    monkeypatch.setenv("CHAT_PRIMARY_OVERRIDE", "workers_ai_llama32_3b")
    cached = _cc._select_chat_primary()
    assert cached == first

    assert _cc._CHAT_PRIMARY_CACHE_TTL_S == 60.0

    _cc._reset_chat_primary_cache()
    after_reset = _cc._select_chat_primary()
    assert after_reset[0] == "workers_ai_llama32_3b"


@pytest.mark.asyncio
async def test_call_llm_for_rag_strict_chain_raises_503_on_exhaustion(monkeypatch):
    from fastapi import HTTPException
    import llm

    async def _exhaust(*args, **kwargs):
        raise RuntimeError("simulated chain exhaustion")

    monkeypatch.setattr(llm, "call_with_provider_fallback", _exhaust)

    with pytest.raises(HTTPException) as exc_info:
        await llm.call_llm_for_rag([{"role": "user", "content": "hi"}])
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_call_llm_api_chat_english_chain_raises_503_on_exhaustion(monkeypatch):
    from fastapi import HTTPException
    import llm

    async def _exhaust(*args, **kwargs):
        raise RuntimeError("simulated chain exhaustion")

    monkeypatch.setattr(llm, "call_with_provider_fallback", _exhaust)

    with pytest.raises(HTTPException) as exc_info:
        await llm.call_llm_api_chat(
            [{"role": "user", "content": "hi"}],
            lang="en",
        )
    assert exc_info.value.status_code == 503


if __name__ == "__main__":
    import pytest as _pt
    _pt.main([__file__, "-v"])
