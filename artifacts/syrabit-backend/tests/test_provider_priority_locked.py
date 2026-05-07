"""Smoke test — POOL_WEIGHTS / PROVIDER_PRIORITY contract (Task #554).

Task #554 — English chat is now a strict 2-position chain that the
``cost_caps._select_chat_primary()`` selector returns dynamically:

  * Default order      :  vertex            → workers_ai_llama32_3b
  * Credit-runway flip :  workers_ai_llama32_3b → vertex
    (when projected GCP credit runway ≤ 90 days; cached for 60 s on
    a monotonic clock so the hot dispatch path never re-reads env / redis
    per turn).

Other pools are unchanged:

  * assamese_rag_chat — sarvam (primary 10000) → workers_ai_indic
    (weight 0; reachable only via call_with_provider_fallback's
    exclusion-redraw after Sarvam exhausts).
  * content / assamese_content — Workers AI exclusively.
  * content_format — Vertex (primary) → Workers-AI Llama-3.3-70b.
  * translate — workers_ai_indic only.

Run::

    python -m pytest tests/test_provider_priority_locked.py -v
"""
from __future__ import annotations

import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging

# Suppress noisy import-time logging from config without leaking the
# global ``logging.disable`` state into other test modules. Task #402:
# leaving ``logging.disable(logging.CRITICAL)`` set at module scope made
# tests/test_vertex_startup_probe.py fail when run after this file.
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


_CHAT_CHAIN_PROVIDERS = {"vertex", "workers_ai_llama32_3b"}


def _clear_runway_env(monkeypatch):
    """Drop both runway-driving env vars so the default chain wins."""
    monkeypatch.delenv("CHAT_CREDIT_RUNWAY_DAYS", raising=False)
    monkeypatch.delenv("GCP_CREDITS_REMAINING_USD", raising=False)
    monkeypatch.delenv("CHAT_PRIMARY_OVERRIDE", raising=False)
    _reset_chat_primary_cache()


# ───────────────────────────────────────────────────────────────────────────
# english_rag_chat — Task #554 2-position chain
# ───────────────────────────────────────────────────────────────────────────


def test_english_rag_chat_chain_is_exactly_two_positions(monkeypatch):
    """Acceptance C — _select_chat_primary returns exactly 2 entries
    (vertex + workers_ai_llama32_3b in some order). No third leg, no
    azure_openai, no other workers_ai variant."""
    _clear_runway_env(monkeypatch)
    chain = _select_chat_primary()
    assert isinstance(chain, list), f"chain must be a list, got {type(chain)}"
    assert len(chain) == 2, (
        f"english chat chain must be exactly 2 positions (Task #554); got {chain}"
    )
    assert set(chain) == _CHAT_CHAIN_PROVIDERS, (
        f"english chat chain must be exactly {{vertex, workers_ai_llama32_3b}}; "
        f"got {chain}"
    )
    assert "azure_openai" not in chain, (
        "Task #554 negative — azure_openai is RETIRED from the english chat chain"
    )


def test_english_rag_chat_default_chain_starts_with_vertex(monkeypatch):
    """Healthy GCP credits → Vertex Gemini 2.5 Flash is the head."""
    _clear_runway_env(monkeypatch)
    chain = _select_chat_primary()
    assert chain[0] == "vertex", (
        f"default head must be vertex (drains GCP startup credits); got {chain}"
    )
    assert chain == list(_CHAT_CHAIN_DEFAULT)


def test_english_rag_chat_credit_flip_swaps_head(monkeypatch):
    """Acceptance D — when projected runway ≤ 90 days the chain flips
    so workers_ai_llama32_3b becomes the head and Vertex stays as the
    paid fallback (V4 §12 — no silent removal)."""
    _clear_runway_env(monkeypatch)
    monkeypatch.setenv("CHAT_CREDIT_RUNWAY_DAYS", "89")
    _reset_chat_primary_cache()
    chain = _select_chat_primary()
    assert chain == list(_CHAT_CHAIN_FLIPPED), (
        f"runway≤90d must flip the chain to {_CHAT_CHAIN_FLIPPED}; got {chain}"
    )
    assert chain[0] == "workers_ai_llama32_3b"
    assert chain[1] == "vertex"


def test_english_rag_chat_credit_flip_at_threshold(monkeypatch):
    """Boundary — runway == 90.0 days must already trigger the flip
    (selector uses ``runway <= 90`` so the transition is inclusive)."""
    _clear_runway_env(monkeypatch)
    monkeypatch.setenv("CHAT_CREDIT_RUNWAY_DAYS", "90")
    _reset_chat_primary_cache()
    chain = _select_chat_primary()
    assert chain[0] == "workers_ai_llama32_3b", (
        f"runway == 90d (boundary) must flip; got head={chain[0]!r}"
    )


def test_english_rag_chat_healthy_runway_uses_default(monkeypatch):
    """Runway 91+ days → default chain (vertex first)."""
    _clear_runway_env(monkeypatch)
    monkeypatch.setenv("CHAT_CREDIT_RUNWAY_DAYS", "180")
    _reset_chat_primary_cache()
    chain = _select_chat_primary()
    assert chain[0] == "vertex", f"runway 180d must keep vertex head; got {chain}"


def test_english_rag_chat_override_vertex(monkeypatch):
    """CHAT_PRIMARY_OVERRIDE=vertex pins the default chain regardless of
    the credit-runway signal."""
    _clear_runway_env(monkeypatch)
    monkeypatch.setenv("CHAT_CREDIT_RUNWAY_DAYS", "5")  # would normally flip
    monkeypatch.setenv("CHAT_PRIMARY_OVERRIDE", "vertex")
    _reset_chat_primary_cache()
    chain = _select_chat_primary()
    assert chain == list(_CHAT_CHAIN_DEFAULT), (
        f"CHAT_PRIMARY_OVERRIDE=vertex must pin the default chain; got {chain}"
    )


def test_english_rag_chat_override_workers(monkeypatch):
    """CHAT_PRIMARY_OVERRIDE=workers_ai_llama32_3b pins the flipped chain."""
    _clear_runway_env(monkeypatch)
    monkeypatch.setenv("CHAT_PRIMARY_OVERRIDE", "workers_ai_llama32_3b")
    _reset_chat_primary_cache()
    chain = _select_chat_primary()
    assert chain == list(_CHAT_CHAIN_FLIPPED), (
        f"CHAT_PRIMARY_OVERRIDE=workers_ai_llama32_3b must pin the flipped chain; "
        f"got {chain}"
    )


def test_english_rag_chat_pool_membership_negative():
    """Negative — no retired provider may appear in PROVIDER_PRIORITY or
    POOL_WEIGHTS for english_rag_chat."""
    chain = list(PROVIDER_PRIORITY["english_rag_chat"])
    weights = POOL_WEIGHTS["english_rag_chat"]
    assert "azure_openai" not in chain, (
        "Task #554 — azure_openai must NOT appear in PROVIDER_PRIORITY['english_rag_chat']"
    )
    assert "azure_openai" not in weights, (
        "Task #554 — azure_openai must NOT appear in POOL_WEIGHTS['english_rag_chat']"
    )
    # Chain must contain both Task #554 chain members.
    for required in _CHAT_CHAIN_PROVIDERS:
        assert required in chain, (
            f"english_rag_chat: PROVIDER_PRIORITY must include {required!r}; got {chain}"
        )


def test_english_rag_chat_weights_have_chain_members():
    """Both chain providers must carry a positive weight so a healthy-
    path draw resolves; the relative order is decided by the runtime
    selector (cost_caps._select_chat_primary)."""
    weights = POOL_WEIGHTS["english_rag_chat"]
    for required in _CHAT_CHAIN_PROVIDERS:
        assert weights.get(required, 0) > 0, (
            f"english_rag_chat: {required!r} must carry a positive weight; got {weights}"
        )


# ───────────────────────────────────────────────────────────────────────────
# Other locked pools (unchanged by Task #554, retained for regression cover)
# ───────────────────────────────────────────────────────────────────────────


def test_content_workers_ai_primary():
    """content + assamese_content pools — Workers AI exclusive (Task #490)."""
    from llm import select_provider
    from collections import Counter

    weights = POOL_WEIGHTS["content"]
    primaries = {"workers_ai_mistral_7b", "workers_ai_llama32_3b"}
    for p in primaries:
        assert weights[p] == 10000, f"content: {p} must carry weight 10000"
    assert "vertex" not in weights
    assert "azure_openai" not in weights
    assert weights.get("workers_ai", 0) == 0

    draws = 600
    counts = Counter(select_provider("content", lang="en") for _ in range(draws))
    primary_share = sum(counts[p] for p in primaries) / draws
    assert primary_share == 1.0

    as_weights = POOL_WEIGHTS["assamese_content"]
    assert as_weights["workers_ai_indic"] == 10000
    assert "vertex" not in as_weights

    # content_format polish pool — Vertex primary, Llama-3.3-70b fallback.
    polish_weights = POOL_WEIGHTS["content_format"]
    assert set(polish_weights.keys()) == {"vertex", "workers_ai_llama33_70b"}
    assert polish_weights["vertex"] == 10000
    assert 0 < polish_weights["workers_ai_llama33_70b"] < polish_weights["vertex"]


def test_assamese_rag_chat_sarvam_primary_indic_fallback():
    """assamese_rag_chat — Sarvam primary, IndicTrans2 fallback. Vertex
    REMOVED from the Assamese chat chain entirely (2026-05-05)."""
    from llm import select_provider
    from collections import Counter

    weights = POOL_WEIGHTS["assamese_rag_chat"]
    assert weights.get("sarvam") == 10000
    assert "vertex" not in weights
    assert weights["workers_ai_indic"] == 0
    for forbidden in ("workers_ai_llama31_8b", "workers_ai", "azure_openai"):
        assert forbidden not in weights, (
            f"assamese_rag_chat: {forbidden} must NOT appear in the chat pool"
        )

    chain = list(PROVIDER_PRIORITY["assamese_rag_chat"])
    assert chain == ["sarvam", "workers_ai_indic"]

    counts = Counter(select_provider("assamese_rag_chat", lang="as") for _ in range(200))
    assert counts.get("sarvam", 0) == 200


def test_translate_workers_ai_indic_primary():
    """translate pool — IndicTrans2 must remain dominant."""
    from llm import select_provider
    from collections import Counter

    weights = POOL_WEIGHTS["translate"]
    assert "vertex" not in weights
    assert weights.get("workers_ai_indic", 0) > 0

    counts = Counter(select_provider("translate", lang="as") for _ in range(200))
    assert counts.get("workers_ai_indic", 0) >= 180, (
        f"translate: workers_ai_indic must dominate; counts={dict(counts)}"
    )


def test_workers_ai_fallback_uses_gpt_oss_20b():
    """Generic workers_ai default model must remain @cf/openai/gpt-oss-20b
    (no quota lock-up like llama-3.3-70b)."""
    from llm import _PROVIDER_DEFAULT_MODELS
    assert _PROVIDER_DEFAULT_MODELS["workers_ai"] == "@cf/openai/gpt-oss-20b"


def test_no_azure_openai_anywhere_in_provider_priority():
    """Negative — azure_openai must not appear in ANY PROVIDER_PRIORITY
    pool. Task #554 retired the entire Azure OpenAI surface (chat /
    embed / Whisper STT / text-embedding-3-large). Azure Speech /
    Translator survive on their own keys via providers.azure_speech."""
    for feature, chain in PROVIDER_PRIORITY.items():
        assert "azure_openai" not in list(chain), (
            f"PROVIDER_PRIORITY[{feature!r}] still references retired "
            f"azure_openai (Task #554); got {chain}"
        )
    for feature, weights in POOL_WEIGHTS.items():
        assert "azure_openai" not in weights, (
            f"POOL_WEIGHTS[{feature!r}] still references retired "
            f"azure_openai (Task #554); got {weights}"
        )


# ───────────────────────────────────────────────────────────────────────────
# Task #554 — strict-chain (no silent fallback) + 60 s cache regression
# ───────────────────────────────────────────────────────────────────────────


@pytest.fixture
def _no_runway_env(monkeypatch):
    _clear_runway_env(monkeypatch)
    yield


def test_select_chat_primary_cache_is_60s_monotonic(monkeypatch, _no_runway_env):
    """The selector must cache its result for ~60 s on a monotonic clock
    so the hot dispatch path never re-reads env / redis per turn. We
    flip CHAT_PRIMARY_OVERRIDE between the two heads and assert the
    selector returns the SAME (cached) chain until the cache is
    explicitly reset."""
    import cost_caps as _cc

    monkeypatch.setenv("CHAT_PRIMARY_OVERRIDE", "vertex")
    _cc._reset_chat_primary_cache()
    first = _cc._select_chat_primary()
    assert first[0] == "vertex"

    # Flip the override behind the cache — the selector must still
    # return the cached value, proving the 60 s cache short-circuits
    # env reads on hot calls.
    monkeypatch.setenv("CHAT_PRIMARY_OVERRIDE", "workers_ai_llama32_3b")
    cached = _cc._select_chat_primary()
    assert cached == first, (
        "selector must return cached chain on hot calls; got "
        f"{cached!r} != {first!r}"
    )

    # Confirm the cache TTL is exactly 60 s — anything looser would let
    # the runway-flip drift across a ladder of turns.
    assert _cc._CHAT_PRIMARY_CACHE_TTL_S == 60.0

    # Reset clears the cache so the new override takes effect.
    _cc._reset_chat_primary_cache()
    after_reset = _cc._select_chat_primary()
    assert after_reset[0] == "workers_ai_llama32_3b"


@pytest.mark.asyncio
async def test_call_llm_for_rag_strict_chain_raises_503_on_exhaustion(monkeypatch):
    """Task #554 (no-silent-fallback) — when the english_rag_chat 2-leg
    chain exhausts, ``call_llm_for_rag`` must raise an HTTP 503 instead
    of silently downgrading onto a non-chain Workers-AI provider."""
    from fastapi import HTTPException
    import llm

    async def _exhaust(*args, **kwargs):
        raise RuntimeError("simulated chain exhaustion")

    monkeypatch.setattr(llm, "call_with_provider_fallback", _exhaust)

    with pytest.raises(HTTPException) as exc_info:
        await llm.call_llm_for_rag([{"role": "user", "content": "hi"}])
    assert exc_info.value.status_code == 503, (
        "english chain exhaustion must surface 503 (Task #554), got "
        f"{exc_info.value.status_code}"
    )


@pytest.mark.asyncio
async def test_call_llm_api_chat_english_chain_raises_503_on_exhaustion(monkeypatch):
    """Same invariant on the chat hot path — ``call_llm_api_chat`` with
    ``feature='english_rag_chat'`` must surface 503 on chain exhaustion
    rather than fall through to ``_LLM_PROVIDERS_WORKERS_ONLY``."""
    from fastapi import HTTPException
    import llm

    async def _exhaust(*args, **kwargs):
        raise RuntimeError("simulated chain exhaustion")

    monkeypatch.setattr(llm, "call_with_provider_fallback", _exhaust)

    # lang="en" → feature derives to "english_rag_chat" inside
    # call_llm_api_chat — that's the strict-chain leg under test.
    with pytest.raises(HTTPException) as exc_info:
        await llm.call_llm_api_chat(
            [{"role": "user", "content": "hi"}],
            lang="en",
        )
    assert exc_info.value.status_code == 503


if __name__ == "__main__":
    import pytest as _pt
    _pt.main([__file__, "-v"])
