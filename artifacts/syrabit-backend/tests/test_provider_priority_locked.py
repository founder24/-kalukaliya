"""Smoke test — POOL_WEIGHTS contract (2026-05-05 user instruction).

Mixed semantics across pools:

  CHAT pools — STRICT PRIMARY / FALLBACK (per 2026-05-05 user instruction
  "for english azure openai will be primary backed by worker ai, for
  assamese sarvam will be primary backed by worker ai indic"). Vertex
  REMOVED from both chat pools entirely.
    english_rag_chat   azure_openai (primary 10000) → workers_ai_llama32_3b /
                       workers_ai_mistral_7b / workers_ai (all weight 0,
                       reachable only via call_with_provider_fallback's
                       exclusion-redraw after Azure exhausts)
    assamese_rag_chat  sarvam (primary 10000) → workers_ai_indic (weight 0,
                       reachable only after Sarvam exhausts)

  CONTENT / TRANSLATE pools — round-robin equal-weight draw remains in
  effect for the generate stage:
    content             workers_ai_mistral_7b, workers_ai_llama32_3b
                        (vertex weight 0 — polish-reserved)
    translate           workers_ai_indic, vertex

Run::

    python -m pytest tests/test_provider_priority_locked.py -v
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging

# Suppress noisy import-time logging from config without leaking the
# global ``logging.disable`` state into other test modules. Task #402:
# leaving ``logging.disable(logging.CRITICAL)`` set at module scope made
# tests/test_vertex_startup_probe.py fail when run after this file (the
# probe asserts on captured ERROR records, which a non-NOTSET disable
# level silently swallows). Save the previous level, suppress only
# across the import that needs it, then restore.
_PREV_LOGGING_DISABLE_LEVEL = logging.root.manager.disable
logging.disable(logging.CRITICAL)
try:
    from config import POOL_WEIGHTS, PROVIDER_PRIORITY
finally:
    logging.disable(_PREV_LOGGING_DISABLE_LEVEL)


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


def test_english_rag_chat_azure_primary_workers_fallback():
    """english_rag_chat — strict primary/fallback (2026-05-05 user instruction).
    Azure OpenAI is the SOLE primary (weight 10000); Workers AI variants sit
    at weight 0 as pure fallbacks that call_with_provider_fallback only
    reaches through its exclusion-redraw loop after Azure exhausts. Vertex
    must NOT be present in this pool — it was removed entirely from chat."""
    from llm import select_provider
    from collections import Counter

    weights = POOL_WEIGHTS["english_rag_chat"]
    assert weights.get("azure_openai") == 10000, (
        f"english_rag_chat: azure_openai must carry weight 10000 (sole primary), "
        f"got {weights.get('azure_openai')!r}"
    )
    assert "vertex" not in weights, (
        "english_rag_chat: vertex must NOT be in the chat pool (2026-05-05 — "
        "Vertex removed from both chat chains entirely)"
    )
    # Workers AI fallbacks must be present (in pool) so the dispatcher can
    # reach them via exclusion-redraw, but they MUST be weight 0 — non-zero
    # would steal traffic from the Azure primary.
    for fallback in ("workers_ai_llama32_3b", "workers_ai_mistral_7b", "workers_ai"):
        assert fallback in weights, (
            f"english_rag_chat: {fallback} must be in the pool (as a fallback "
            f"reachable via call_with_provider_fallback exclusion-redraw)"
        )
        assert weights[fallback] == 0, (
            f"english_rag_chat: {fallback} must be weight 0 (pure fallback — "
            f"non-zero would steal traffic from the Azure primary), got {weights[fallback]}"
        )

    # Healthy-path draw: 200 selections must all return azure_openai because
    # it is the sole non-zero-weight entry in the pool.
    counts = Counter(select_provider("english_rag_chat", lang="en") for _ in range(200))
    assert counts.get("azure_openai", 0) == 200, (
        f"english_rag_chat: healthy-path draw must route 100% to azure_openai; "
        f"counts={dict(counts)}"
    )
    print("  PASS: english_rag_chat azure_openai-primary, workers_ai-fallback (vertex removed)")


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


def test_assamese_rag_chat_sarvam_primary_indic_fallback():
    """assamese_rag_chat — strict primary/fallback (2026-05-05 user instruction).
    Sarvam is the SOLE primary (weight 10000); Workers AI IndicTrans2 sits
    at weight 0 as the pure fallback that call_with_provider_fallback only
    reaches through its exclusion-redraw loop after Sarvam exhausts. Vertex
    REMOVED from the Assamese chat chain entirely. Strict 2-leg exhaustion
    must surface 503 (no silent downgrade to wrong-language providers)."""
    from llm import select_provider
    from collections import Counter

    weights = POOL_WEIGHTS["assamese_rag_chat"]
    assert weights.get("sarvam") == 10000, (
        f"assamese_rag_chat: sarvam must carry weight 10000 (sole primary), "
        f"got {weights.get('sarvam')!r}"
    )
    assert "vertex" not in weights, (
        "assamese_rag_chat: vertex must NOT be in the chat pool (2026-05-05 — "
        "Vertex removed from both chat chains entirely)"
    )
    assert "workers_ai_indic" in weights, (
        "assamese_rag_chat: workers_ai_indic must be in the pool (as the "
        "fallback reachable via call_with_provider_fallback exclusion-redraw)"
    )
    assert weights["workers_ai_indic"] == 0, (
        f"assamese_rag_chat: workers_ai_indic must be weight 0 (pure fallback — "
        f"non-zero would steal traffic from the Sarvam primary), "
        f"got {weights['workers_ai_indic']}"
    )
    # Wrong-language providers must NOT appear in the Assamese chat pool.
    for forbidden in ("workers_ai_llama31_8b", "workers_ai", "azure_openai"):
        assert forbidden not in weights, (
            f"assamese_rag_chat: {forbidden} must NOT be in the Assamese chat pool — "
            f"it emits non-Assamese output for Assamese prompts"
        )

    # PROVIDER_PRIORITY chain must contain exactly sarvam + workers_ai_indic
    # (vertex removed, no wrong-language tail).
    chain = list(PROVIDER_PRIORITY["assamese_rag_chat"])
    assert chain == ["sarvam", "workers_ai_indic"], (
        f"assamese_rag_chat: PROVIDER_PRIORITY must be exactly "
        f"['sarvam', 'workers_ai_indic'] (vertex removed); got {chain}"
    )

    # Healthy-path draw: every selection must return sarvam because it is
    # the sole non-zero-weight entry in the pool.
    counts = Counter(select_provider("assamese_rag_chat", lang="as") for _ in range(200))
    assert counts.get("sarvam", 0) == 200, (
        f"assamese_rag_chat: healthy-path draw must route 100% to sarvam; "
        f"counts={dict(counts)}"
    )
    print("  PASS: assamese_rag_chat sarvam-primary, workers_ai_indic-fallback (vertex removed)")


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
    """PROVIDER_PRIORITY list-order seeds the candidate pool for
    select_provider and (via call_with_provider_fallback's exclusion-redraw
    loop) defines the failover order for chat pools that have a single
    primary at non-zero weight."""
    expectations = {
        # english_rag_chat (2026-05-05): vertex REMOVED. The chain is
        # azure_openai (primary) → workers_ai variants (fallbacks).
        "english_rag_chat":  {"azure_openai",
                              "workers_ai_llama32_3b", "workers_ai_mistral_7b"},
        # Vertex is in PROVIDER_PRIORITY["content"] for stage-2 polish
        # routing (POOL_WEIGHTS sets it to 0 for stage-1 generate).
        "content":           {"vertex",
                              "workers_ai_mistral_7b", "workers_ai_llama32_3b"},
        # assamese_rag_chat (2026-05-05): vertex REMOVED. Strict 2-leg
        # chain — sarvam (primary) → workers_ai_indic (fallback).
        "assamese_rag_chat": {"sarvam", "workers_ai_indic"},
        "translate":         {"workers_ai_indic", "vertex"},
    }
    for feature, members in expectations.items():
        chain = set(PROVIDER_PRIORITY[feature])
        missing = members - chain
        assert not missing, f"{feature}: PROVIDER_PRIORITY missing {missing}; got {PROVIDER_PRIORITY[feature]}"
    # assamese_rag_chat: strict 2-leg chain (sarvam, workers_ai_indic).
    # Vertex removed entirely from chat. workers_ai_llama31_8b and the
    # generic workers_ai shorthand remain forbidden because they emit
    # non-Assamese output.
    assert set(PROVIDER_PRIORITY["assamese_rag_chat"]) == {"sarvam", "workers_ai_indic"}, (
        f"assamese_rag_chat must contain exactly sarvam/workers_ai_indic; "
        f"got {PROVIDER_PRIORITY['assamese_rag_chat']}"
    )
    # english_rag_chat: vertex must NOT be in the chain.
    assert "vertex" not in PROVIDER_PRIORITY["english_rag_chat"], (
        f"english_rag_chat must NOT contain vertex (2026-05-05 — Vertex "
        f"removed from chat); got {PROVIDER_PRIORITY['english_rag_chat']}"
    )
    print("  PASS: PROVIDER_PRIORITY membership matches chat primary/fallback contract")


if __name__ == "__main__":
    test_english_rag_chat_azure_primary_workers_fallback()
    test_content_workers_ai_primary()
    test_assamese_rag_chat_sarvam_primary_indic_fallback()
    test_translate_round_robin()
    test_workers_ai_fallback_uses_gpt_oss_20b()
    test_priority_lists_contain_every_active_member()
    print("\nAll POOL_WEIGHTS / PROVIDER_PRIORITY assertions verified.")
