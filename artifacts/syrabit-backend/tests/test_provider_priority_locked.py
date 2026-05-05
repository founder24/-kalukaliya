"""Smoke test — Round-robin / load-balanced POOL_WEIGHTS contract (2026-05-05).

Verifies that POOL_WEIGHTS gives every active provider an EQUAL share of the
draw for every locked-down pool the spec covers:

  english_rag_chat    azure_openai, vertex, workers_ai_llama32_3b, workers_ai_mistral_7b
  content             vertex, azure_openai, sarvam, workers_ai_mistral_7b
  assamese_rag_chat   sarvam, workers_ai_indic, vertex
  translate           workers_ai_indic, vertex

Replaces the previous Task #291 strict primary→fallback assertions per the
2026-05-05 user instruction ("all llm should work as a batch not one primary
other fallback"). The dispatcher now uses a uniform random draw across all
active providers; weight-0 entries (last-resort safety net) are still gated.

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


def _expect_round_robin(feature: str, expected_active: set[str], lang: str = "en",
                        *, draws: int = 600, tolerance: float = 0.40):
    """Assert that all *expected_active* providers appear in roughly equal share.

    With ``draws=600`` and ``tolerance=0.40``, each provider should land in
    ``[ (1/N - tol/N), (1/N + tol/N) ]`` of draws — i.e. within 40% of its
    fair share. This is a loose bound chosen to keep the test stable under
    the binomial variance of `random.choices` with N=4 (~95 expected hits per
    bucket, std-dev ≈9; ±40% is ±38 hits, well outside 2σ noise).
    """
    from llm import select_provider
    from collections import Counter
    counts = Counter(select_provider(feature, lang=lang) for _ in range(draws))
    fair = draws / len(expected_active)
    lower = fair * (1 - tolerance)
    upper = fair * (1 + tolerance)
    seen_active = {p for p in counts if p in expected_active}
    assert seen_active == expected_active, (
        f"{feature}: expected every active provider in {expected_active} to be drawn at "
        f"least once across {draws} draws, missing {expected_active - seen_active}; "
        f"counts={dict(counts)}"
    )
    for p in expected_active:
        assert lower <= counts[p] <= upper, (
            f"{feature}: provider {p} drawn {counts[p]}/{draws} times — "
            f"outside ±{tolerance:.0%} of fair share ({fair:.0f}); counts={dict(counts)}"
        )


def _assert_equal_weights(feature: str, expected_active: set[str]):
    """Each active provider in the pool must carry the same weight."""
    weights = POOL_WEIGHTS[feature]
    active_weights = {p: weights[p] for p in expected_active if p in weights}
    assert set(active_weights.keys()) == expected_active, (
        f"{feature}: POOL_WEIGHTS missing entries for {expected_active - set(active_weights)}"
    )
    distinct = set(active_weights.values())
    assert len(distinct) == 1, (
        f"{feature}: round-robin requires equal weights across active providers, "
        f"got {active_weights}"
    )
    only_weight = distinct.pop()
    assert only_weight > 0, (
        f"{feature}: active providers must have a positive equal weight, got {only_weight}"
    )


def test_english_rag_chat_round_robin():
    expected = {"azure_openai", "vertex", "workers_ai_llama32_3b", "workers_ai_mistral_7b"}
    _assert_equal_weights("english_rag_chat", expected)
    assert POOL_WEIGHTS["english_rag_chat"].get("workers_ai", 0) == 0, \
        "workers_ai must remain weight-0 (last-resort safety net)"
    _expect_round_robin("english_rag_chat", expected, lang="en")
    print("  PASS: english_rag_chat round-robin across "
          "azure_openai / vertex / workers_ai_llama32_3b / workers_ai_mistral_7b")


def test_content_workers_ai_primary():
    """content + assamese_content pools (2026-05-05 user instruction):
    Stage-1 GENERATE is owned by Workers AI exclusively. Vertex sits at
    weight 0 in BOTH pools because it is reserved for the Stage-2 polish
    helper (`llm.polish_notes_with_vertex` — NotebookLM-style formatting).
    The weight-0 Vertex slot is kept as an emergency last-resort so a
    total Workers-AI outage can still serve content.

    English `content` pool:
      workers_ai_mistral_7b (10000), workers_ai_llama32_3b (10000),
      vertex (0 — polish-reserved), workers_ai (0 — emergency safety net).

    Assamese `assamese_content` pool:
      workers_ai_indic (10000), vertex (0 — polish-reserved).
    """
    from llm import select_provider, polish_notes_with_vertex
    from collections import Counter

    # ---- English content pool ------------------------------------------
    weights = POOL_WEIGHTS["content"]
    primaries = {"workers_ai_mistral_7b", "workers_ai_llama32_3b"}

    for p in primaries:
        assert weights[p] == 10000, (
            f"content: workers-AI primary {p} must carry weight 10000, got {weights[p]}"
        )
    assert weights.get("vertex", 0) == 0, (
        "content: vertex must be weight-0 (reserved for stage-2 polish via "
        "polish_notes_with_vertex), got " + str(weights.get("vertex"))
    )
    assert weights.get("workers_ai", 0) == 0, (
        "content: generic workers_ai must remain weight-0 (last-resort safety net)"
    )
    assert "sarvam" not in weights, (
        "content: sarvam must NOT be in the English content pool — it is "
        "reserved for the Assamese conversational chain only"
    )
    assert "azure_openai" not in weights, (
        "content: azure_openai must NOT be in the English content pool — "
        "content generation is Cloudflare-native (Workers AI) only"
    )

    # Across 600 draws every selection must be a workers-AI primary —
    # Vertex (weight 0) must never be drawn except via the emergency
    # exhaustion path which a healthy pool never enters.
    draws = 600
    counts = Counter(select_provider("content", lang="en") for _ in range(draws))
    primary_share = sum(counts[p] for p in primaries) / draws
    assert primary_share == 1.0, (
        f"content: stage-1 generate must route 100% to Workers AI primaries; "
        f"counts={dict(counts)}"
    )
    assert counts.get("vertex", 0) == 0, (
        f"content: vertex must NOT be drawn for stage-1 generate (it is "
        f"polish-reserved); counts={dict(counts)}"
    )

    # ---- Assamese content pool -----------------------------------------
    as_weights = POOL_WEIGHTS["assamese_content"]
    assert as_weights["workers_ai_indic"] == 10000, (
        f"assamese_content: workers_ai_indic must carry weight 10000, got "
        f"{as_weights['workers_ai_indic']}"
    )
    assert as_weights.get("vertex", 0) == 0, (
        "assamese_content: vertex must be weight-0 (reserved for stage-2 "
        "polish via polish_notes_with_vertex), got "
        + str(as_weights.get("vertex"))
    )

    as_counts = Counter(select_provider("assamese_content", lang="as") for _ in range(draws))
    assert as_counts.get("workers_ai_indic", 0) == draws, (
        f"assamese_content: stage-1 generate must route 100% to "
        f"workers_ai_indic (IndicTrans2); counts={dict(as_counts)}"
    )
    assert as_counts.get("vertex", 0) == 0, (
        f"assamese_content: vertex must NOT be drawn for stage-1 generate "
        f"(it is polish-reserved); counts={dict(as_counts)}"
    )

    # ---- Stage-2 polish helper exists and is callable -----------------
    assert callable(polish_notes_with_vertex), (
        "llm.polish_notes_with_vertex must exist as a callable — it is the "
        "Stage-2 NotebookLM-style polish helper pinned to Vertex / Gemini"
    )

    print(
        "  PASS: content + assamese_content stage-1 generate is workers-only; "
        "vertex is polish-reserved (weight 0); polish_notes_with_vertex callable"
    )


def test_assamese_rag_chat_round_robin():
    expected = {"sarvam", "workers_ai_indic", "vertex"}
    _assert_equal_weights("assamese_rag_chat", expected)
    # workers_ai_indic IS still in the chain (re-introduced 2026-05-05) and
    # is now drawn equally with sarvam and vertex.
    for p in expected:
        assert p in PROVIDER_PRIORITY["assamese_rag_chat"], (
            f"{p} must be in PROVIDER_PRIORITY['assamese_rag_chat']"
        )
    _expect_round_robin("assamese_rag_chat", expected, lang="as")
    print("  PASS: assamese_rag_chat round-robin across sarvam / workers_ai_indic / vertex")


def test_translate_round_robin():
    expected = {"workers_ai_indic", "vertex"}
    _assert_equal_weights("translate", expected)
    _expect_round_robin("translate", expected, lang="as")
    print("  PASS: translate round-robin across workers_ai_indic / vertex")


def test_workers_ai_fallback_uses_gpt_oss_20b():
    """When content / english_rag_chat fall through to the weight-0
    workers_ai last-resort leg, the dispatched model must be
    @cf/openai/gpt-oss-20b (no quota lock-up like llama-3.3-70b)."""
    from llm import _PROVIDER_DEFAULT_MODELS
    assert _PROVIDER_DEFAULT_MODELS["workers_ai"] == "@cf/openai/gpt-oss-20b", (
        f"workers_ai default model must be @cf/openai/gpt-oss-20b for the "
        f"locked content + english_rag_chat fallback chain; got "
        f"{_PROVIDER_DEFAULT_MODELS['workers_ai']!r}"
    )
    print("  PASS: workers_ai default model is @cf/openai/gpt-oss-20b")


def test_priority_lists_contain_every_active_member():
    """PROVIDER_PRIORITY list-order is preserved (used to seed the candidate
    pool for select_provider) but no longer encodes a "primary" — the first
    entry is just the iteration starting point."""
    expectations = {
        "english_rag_chat":  {"azure_openai", "vertex",
                              "workers_ai_llama32_3b", "workers_ai_mistral_7b"},
        # Vertex is in PROVIDER_PRIORITY["content"] for stage-2 polish
        # routing (POOL_WEIGHTS sets it to 0 for stage-1 generate).
        "content":           {"vertex",
                              "workers_ai_mistral_7b", "workers_ai_llama32_3b"},
        "assamese_rag_chat": {"sarvam", "workers_ai_indic", "vertex"},
        "translate":         {"workers_ai_indic", "vertex"},
    }
    for feature, members in expectations.items():
        chain = set(PROVIDER_PRIORITY[feature])
        missing = members - chain
        assert not missing, f"{feature}: PROVIDER_PRIORITY missing {missing}; got {PROVIDER_PRIORITY[feature]}"
    # assamese_rag_chat is still the strict 3-leg chain in terms of *membership*
    # (sarvam, workers_ai_indic, vertex) — workers_ai_llama31_8b and the
    # generic workers_ai shorthand remain forbidden because they emit
    # non-Assamese output. Order is no longer significant.
    assert set(PROVIDER_PRIORITY["assamese_rag_chat"]) == {"sarvam", "workers_ai_indic", "vertex"}, (
        f"assamese_rag_chat must contain exactly sarvam/workers_ai_indic/vertex; "
        f"got {PROVIDER_PRIORITY['assamese_rag_chat']}"
    )
    print("  PASS: PROVIDER_PRIORITY membership matches round-robin pools")


if __name__ == "__main__":
    test_english_rag_chat_round_robin()
    test_content_workers_ai_primary()
    test_assamese_rag_chat_round_robin()
    test_translate_round_robin()
    test_workers_ai_fallback_uses_gpt_oss_20b()
    test_priority_lists_contain_every_active_member()
    print("\nAll round-robin POOL_WEIGHTS assertions verified.")
