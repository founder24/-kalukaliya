"""Task #581 — free-tier cost-minimization regression tests.

Covers the founder-locked surface introduced by the task:
  * §L1  free users are hard-routed off Vertex.
  * §L4  4-step turn ladder (cheap / tight / retrieval_only / paywall).
  * §L7  per-content-type free-tier output sub-caps.
  * §L9  long-context paid-only gate, OCR free/paid split,
         voice preview dep + 30s TTS char limit.
  * §L10 free-tier-first MeterD ladder evaluator (4 steps, all
         strictly less than the legacy 60% PAUSE_BATCH step).
  * §L8  free_tier_dispatch counters expose the canonical tier names
         and a paid_escalation_pct totals row.
"""
from __future__ import annotations

from pathlib import Path


# ── §L1 — free users hard-routed off Vertex ───────────────────────────────
def test_free_users_never_get_vertex(monkeypatch):
    """Even when the runway-aware chain head is `vertex` (operator
    override), a free user MUST still land on Workers-AI."""
    import cost_caps
    monkeypatch.setenv("CHAT_PRIMARY_OVERRIDE", "vertex")
    cost_caps._reset_chat_primary_cache()
    try:
        for turn in (1, 5, 9, 12, 18):
            out = cost_caps._select_chat_model(
                user_plan="free", session_turn_count=turn, lang="en",
            )
            assert out["provider"] == "workers_ai_mistral_7b", (
                f"§L1 — free turn={turn} must NOT route to vertex; got {out}"
            )
    finally:
        monkeypatch.delenv("CHAT_PRIMARY_OVERRIDE", raising=False)
        cost_caps._reset_chat_primary_cache()


# ── §L7 — free-tier per-content-type output sub-caps ──────────────────────
def test_free_tier_output_caps_clamp_below_chat_turn():
    from cost_caps import (
        FREE_TIER_OUTPUT_CAPS, TOKEN_BUDGETS,
        effective_free_tier_output_cap,
    )
    base = TOKEN_BUDGETS["chat_turn"]["max_output_tokens"]
    for ct, cap in FREE_TIER_OUTPUT_CAPS.items():
        # Free → clamps to the per-content-type sub-cap.
        assert effective_free_tier_output_cap(ct, user_plan="free") == cap
        # Paid → keeps the full chat_turn budget.
        assert effective_free_tier_output_cap(ct, user_plan="pro") == base
    # Unknown content type → no extra clamp.
    assert effective_free_tier_output_cap("unknown", user_plan="free") == base


# ── §L9 — long-context paywall ────────────────────────────────────────────
def test_long_context_paid_only():
    from cost_caps import (
        is_long_context_paid_only, LONG_CONTEXT_FREE_MAX_INPUT_TOKENS,
    )
    assert not is_long_context_paid_only(0, "free")
    assert not is_long_context_paid_only(LONG_CONTEXT_FREE_MAX_INPUT_TOKENS, "free")
    assert is_long_context_paid_only(LONG_CONTEXT_FREE_MAX_INPUT_TOKENS + 1, "free")
    # Paid plans bypass.
    assert not is_long_context_paid_only(50_000, "pro")


# ── §L9 — OCR free/paid split ─────────────────────────────────────────────
def test_ocr_daily_caps_split_free_vs_paid():
    from auth_deps import (
        OCR_DAILY_CAP_USER_FREE,
        OCR_DAILY_CAP_USER_PAID,
        OCR_DAILY_CAP_USER,
    )
    assert OCR_DAILY_CAP_USER_FREE == 3
    assert OCR_DAILY_CAP_USER_PAID == 100
    # Back-compat alias points at the privileged cap.
    assert OCR_DAILY_CAP_USER == OCR_DAILY_CAP_USER_PAID
    assert OCR_DAILY_CAP_USER_FREE < OCR_DAILY_CAP_USER_PAID


# ── §L9 — voice preview wrapper exists + char limit set ───────────────────
def test_voice_preview_dep_and_char_limit_present():
    import auth_deps
    assert callable(auth_deps.require_paid_plan_or_voice_preview), (
        "§L9 voice-preview dep missing"
    )
    assert auth_deps.FREE_VOICE_PREVIEW_TTS_CHAR_LIMIT == 600
    voice_src = (Path(__file__).resolve().parents[1] / "routes" / "voice.py").read_text()
    # Must wire the new dep into TTS / STT / voice-pipeline.
    for route in ("/voice/tts", "/voice/stt", "/voice/voice"):
        idx = voice_src.find(f'"{route}"')
        assert idx >= 0
        window = voice_src[idx: idx + 2500]
        assert "Depends(require_paid_plan_or_voice_preview)" in window, (
            f"§L9 — {route} not wired to require_paid_plan_or_voice_preview"
        )
    # TTS route must enforce the char-limit clamp.
    assert "FREE_VOICE_PREVIEW_TTS_CHAR_LIMIT" in voice_src


# ── §L10 — free-tier-first MeterD ladder ──────────────────────────────────
def test_free_tier_dispatch_state_ladder_steps():
    from cost_caps import (
        free_tier_dispatch_state,
        DEGRADATION_PCT_FREE_TIGHTEN_1,
        DEGRADATION_PCT_FREE_TIGHTEN_2,
        DEGRADATION_PCT_FREE_TIGHTEN_3,
        DEGRADATION_PCT_FREE_TIGHTEN_4,
        DEGRADATION_PCT_PAUSE_BATCH,
    )
    # Strict ordering + all below the legacy PAUSE_BATCH step.
    ladder = [
        DEGRADATION_PCT_FREE_TIGHTEN_1,
        DEGRADATION_PCT_FREE_TIGHTEN_2,
        DEGRADATION_PCT_FREE_TIGHTEN_3,
        DEGRADATION_PCT_FREE_TIGHTEN_4,
    ]
    assert ladder == sorted(ladder)
    assert all(0.0 < t < 1.0 for t in ladder)
    assert ladder[-1] < DEGRADATION_PCT_PAUSE_BATCH

    # Level transitions.
    assert free_tier_dispatch_state(0.0)["level"] == 0
    assert free_tier_dispatch_state(DEGRADATION_PCT_FREE_TIGHTEN_1)["level"] == 1
    assert free_tier_dispatch_state(DEGRADATION_PCT_FREE_TIGHTEN_2)["level"] == 2
    assert free_tier_dispatch_state(DEGRADATION_PCT_FREE_TIGHTEN_3)["level"] == 3
    assert free_tier_dispatch_state(DEGRADATION_PCT_FREE_TIGHTEN_4)["level"] == 4

    # Effects.
    s1 = free_tier_dispatch_state(DEGRADATION_PCT_FREE_TIGHTEN_1)
    assert s1["free_output_multiplier"] == 0.5
    assert not s1["free_chat_paywalled"]
    s4 = free_tier_dispatch_state(DEGRADATION_PCT_FREE_TIGHTEN_4)
    assert s4["free_chat_paywalled"]
    assert s4["free_turns_11_20_paywalled"]
    assert s4["free_turns_21_30_paywalled"]


def test_select_chat_model_collapses_buckets_under_meter_d_pressure():
    """At §L10 stage-2+, free turns 21-30 collapse to paywall instead
    of retrieval_only; at stage-3+, turns 11-20 also collapse; at
    stage-4 ALL free chat collapses."""
    from cost_caps import (
        _select_chat_model,
        DEGRADATION_PCT_FREE_TIGHTEN_2,
        DEGRADATION_PCT_FREE_TIGHTEN_3,
        DEGRADATION_PCT_FREE_TIGHTEN_4,
    )
    # Stage-2 (>=55%): turns 21-30 collapse.
    s2 = _select_chat_model(
        user_plan="free", session_turn_count=25, lang="en",
        monthly_spend_fraction=DEGRADATION_PCT_FREE_TIGHTEN_2,
    )
    assert s2["tier"] == "paywall"
    # Stage-3 (>=70%): turns 11-20 also collapse; turns 1-10 still cheap.
    s3 = _select_chat_model(
        user_plan="free", session_turn_count=15, lang="en",
        monthly_spend_fraction=DEGRADATION_PCT_FREE_TIGHTEN_3,
    )
    assert s3["tier"] == "paywall"
    s3_cheap = _select_chat_model(
        user_plan="free", session_turn_count=5, lang="en",
        monthly_spend_fraction=DEGRADATION_PCT_FREE_TIGHTEN_3,
    )
    assert s3_cheap["tier"] == "cheap"
    # Stage-4 (>=85%): all free chat collapses.
    s4 = _select_chat_model(
        user_plan="free", session_turn_count=5, lang="en",
        monthly_spend_fraction=DEGRADATION_PCT_FREE_TIGHTEN_4,
    )
    assert s4["tier"] == "paywall"


# ── §L8 — observability counter shape ─────────────────────────────────────
def test_free_tier_dispatch_counter_snapshot_shape():
    import free_tier_dispatch as ftd
    ftd.record(ftd.TIER_CACHE_HIT, lang="en")
    ftd.record(ftd.TIER_CHEAP, lang="en")
    ftd.record(ftd.TIER_PAID_ESCALATE, lang="en")
    snap = ftd.snapshot(lang="en")
    assert snap["lang"] == "en"
    assert set(snap["counts"].keys()) == set(ftd.ALL_TIERS)
    assert snap["counts"][ftd.TIER_CACHE_HIT] >= 1
    assert "paid_escalation_pct" in snap["totals"]
    # Unknown tier names are dropped silently (no raise, no record).
    before = sum(snap["counts"].values())
    ftd.record("not_a_tier", lang="en")
    after_snap = ftd.snapshot(lang="en")
    after = sum(after_snap["counts"].values())
    assert after == before


# ── §L5 / §L6 — module surface only (deep wiring is opt-in by caller) ────
def test_retrieval_first_module_surface():
    import retrieval_first
    assert hasattr(retrieval_first, "try_resolve")
    assert retrieval_first.DEFAULT_CONFIDENCE_THRESHOLD == 0.85


def test_assamese_dispatch_classifier():
    from assamese_dispatch import needs_reasoning, is_simple_definition
    assert needs_reasoning("Explain why photosynthesis matters in detail")
    assert needs_reasoning("কিয় গ্ৰহণ ঘটে?")
    assert not needs_reasoning("Define photosynthesis")
    assert is_simple_definition("Define photosynthesis")
    assert not is_simple_definition(
        "Compare and contrast photosynthesis with cellular respiration "
        "across all the differences you can find" * 5
    )
