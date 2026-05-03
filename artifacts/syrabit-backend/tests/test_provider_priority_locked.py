"""Smoke test — Task #291: locked PROVIDER_PRIORITY chains.

Verifies that POOL_WEIGHTS yields strict primary→fallback selection for
the four pools the spec locks down:

  english_rag_chat    azure_openai → vertex → workers_ai
  content             vertex       → azure_openai → workers_ai
  assamese_rag_chat   sarvam       → vertex                       (no further downgrade)
  translate           workers_ai_indic → vertex

Run::

    python -m pytest tests/test_provider_priority_locked.py -v
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
logging.disable(logging.CRITICAL)

from config import POOL_WEIGHTS, PROVIDER_PRIORITY


def _expect_primary(feature: str, primary: str, lang: str = "en", *, draws: int = 200):
    from llm import select_provider
    hits = sum(1 for _ in range(draws)
               if select_provider(feature, lang=lang) == primary)
    ratio = hits / draws
    assert ratio >= 0.90, (
        f"{feature}: expected {primary} primary >=90% of {draws} draws, got {ratio:.0%}"
    )


def test_english_rag_chat_locked_to_azure_primary():
    weights = POOL_WEIGHTS["english_rag_chat"]
    assert weights["azure_openai"] >= 100 * weights.get("vertex", 1), \
        "english_rag_chat: azure must dominate vertex by >=100x"
    assert weights.get("workers_ai", 0) == 0, "workers_ai must be last-resort (weight 0)"
    _expect_primary("english_rag_chat", "azure_openai", lang="en")
    print("  PASS: english_rag_chat locked to azure_openai → vertex → workers_ai")


def test_content_locked_to_vertex_primary():
    weights = POOL_WEIGHTS["content"]
    assert weights["vertex"] >= 100 * weights.get("azure_openai", 1), \
        "content: vertex must dominate azure by >=100x"
    assert weights.get("workers_ai", 0) == 0
    _expect_primary("content", "vertex", lang="en")
    print("  PASS: content locked to vertex → azure_openai → workers_ai")


def test_assamese_rag_chat_locked_to_sarvam_primary():
    weights = POOL_WEIGHTS["assamese_rag_chat"]
    assert weights["sarvam"] >= 100 * weights.get("vertex", 1), \
        "assamese_rag_chat: sarvam must dominate vertex by >=100x"
    # Task #291 — workers_ai_indic must NOT be present in the chat pool.
    # IndicTrans2 is a translation model; using it as a chat fallback would
    # silently downgrade reasoning to a translator, producing nonsense
    # answers. The chain is strictly sarvam → vertex with no further leg.
    assert "workers_ai_indic" not in weights, (
        "workers_ai_indic must NOT be in assamese_rag_chat POOL_WEIGHTS — "
        "it is a translation model, not a chat model"
    )
    assert "workers_ai_indic" not in PROVIDER_PRIORITY["assamese_rag_chat"], (
        "workers_ai_indic must NOT be in PROVIDER_PRIORITY['assamese_rag_chat']"
    )
    _expect_primary("assamese_rag_chat", "sarvam", lang="as")
    print("  PASS: assamese_rag_chat locked to sarvam → vertex (no downgrade)")


def test_translate_locked_to_indictrans2_primary():
    weights = POOL_WEIGHTS["translate"]
    assert weights["workers_ai_indic"] >= 100 * weights.get("vertex", 1), \
        "translate: workers_ai_indic must dominate vertex by >=100x"
    _expect_primary("translate", "workers_ai_indic", lang="as")
    print("  PASS: translate locked to workers_ai_indic → vertex")


def test_workers_ai_fallback_uses_gpt_oss_20b():
    """Task #291 — when content / english_rag_chat fall through to the
    weight-0 workers_ai last-resort leg, the dispatched model must be
    @cf/openai/gpt-oss-20b (no quota lock-up like llama-3.3-70b)."""
    from llm import _PROVIDER_DEFAULT_MODELS
    assert _PROVIDER_DEFAULT_MODELS["workers_ai"] == "@cf/openai/gpt-oss-20b", (
        f"workers_ai default model must be @cf/openai/gpt-oss-20b for the "
        f"locked content + english_rag_chat fallback chain; got "
        f"{_PROVIDER_DEFAULT_MODELS['workers_ai']!r}"
    )
    print("  PASS: workers_ai default model is @cf/openai/gpt-oss-20b")


def test_priority_lists_match_locked_chain_order():
    """First entry of each PROVIDER_PRIORITY list must match its primary."""
    expectations = {
        "english_rag_chat": "azure_openai",
        "content":          "vertex",
        "assamese_rag_chat": "sarvam",
        "translate":        "workers_ai_indic",
    }
    for feature, primary in expectations.items():
        order = PROVIDER_PRIORITY[feature]
        assert order[0] == primary, (
            f"{feature}: PROVIDER_PRIORITY[0] must be {primary}, got {order}"
        )
    # assamese_rag_chat must contain ONLY [sarvam, vertex] — no third leg.
    assert PROVIDER_PRIORITY["assamese_rag_chat"] == ["sarvam", "vertex"], (
        f"assamese_rag_chat must be exactly ['sarvam', 'vertex']; got "
        f"{PROVIDER_PRIORITY['assamese_rag_chat']}"
    )
    print("  PASS: PROVIDER_PRIORITY ordering matches locked chains")


if __name__ == "__main__":
    test_english_rag_chat_locked_to_azure_primary()
    test_content_locked_to_vertex_primary()
    test_assamese_rag_chat_locked_to_sarvam_primary()
    test_translate_locked_to_indictrans2_primary()
    test_workers_ai_fallback_uses_gpt_oss_20b()
    test_priority_lists_match_locked_chain_order()
    print("\nAll Task #291 provider-chain locks verified.")
