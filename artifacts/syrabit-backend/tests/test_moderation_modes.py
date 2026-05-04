"""Task #362 §4 — moderation modes + friendlier veto message tests."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from moderation_modes import (
    friendly_message,
    is_hard_floor_category,
    normalize_mode,
    should_block,
)


# ── normalize_mode ──────────────────────────────────────────────────────────
def test_normalize_mode_accepts_valid():
    for m in ("safe", "default", "challenge"):
        assert normalize_mode(m) == m


def test_normalize_mode_rejects_unknown():
    assert normalize_mode("yolo") == "default"
    assert normalize_mode("") == "default"
    assert normalize_mode(None) == "default"
    assert normalize_mode("  SAFE  ") == "safe"


# ── hard-floor enforcement (the security-critical guarantee) ────────────────
def test_hard_floor_blocks_every_mode_regardless_of_severity():
    for category in ("csam", "self_harm_intent", "exam_paper_leakage", "injection"):
        for mode in ("safe", "default", "challenge"):
            assert should_block(category, severity=0, mode=mode) is True, (
                f"hard-floor category {category!r} must block in mode={mode!r} "
                f"even at severity=0"
            )


def test_is_hard_floor_category():
    assert is_hard_floor_category("csam") is True
    assert is_hard_floor_category("CSAM") is True
    assert is_hard_floor_category("self_harm_intent") is True
    assert is_hard_floor_category("hate") is False
    assert is_hard_floor_category("") is False
    assert is_hard_floor_category(None) is False


# ── mode threshold mapping ──────────────────────────────────────────────────
def test_safe_mode_blocks_strictly():
    # Severity 2 is the safe-mode block threshold for hate/sexual/violence.
    assert should_block("hate", 2, "safe") is True
    assert should_block("sexual", 2, "safe") is True
    assert should_block("violence", 2, "safe") is True
    # Below threshold should pass.
    assert should_block("hate", 1, "safe") is False


def test_default_mode_matches_azure_recommended():
    # Default = severity 4 (Azure recommended).
    assert should_block("hate", 4, "default") is True
    assert should_block("hate", 3, "default") is False
    assert should_block("hate", 2, "default") is False


def test_challenge_mode_relaxes_three_categories_only():
    # Challenge raises hate/sexual/violence to 6.
    assert should_block("hate", 5, "challenge") is False
    assert should_block("hate", 6, "challenge") is True
    assert should_block("sexual", 5, "challenge") is False
    assert should_block("violence", 5, "challenge") is False


def test_challenge_mode_keeps_self_harm_at_safe_floor():
    # Self-harm stays at severity 2 regardless of mode — non-negotiable.
    assert should_block("self_harm", 2, "challenge") is True
    assert should_block("self_harm", 2, "default") is False  # default is 4
    assert should_block("self_harm", 4, "default") is True


def test_unknown_category_falls_back_to_default_threshold():
    # Unknown category should not silently pass through challenge mode.
    assert should_block("future_unmapped_category", 4, "challenge") is True


# ── friendlier veto messages ────────────────────────────────────────────────
def test_friendly_message_appends_rephrase_hint():
    out = friendly_message("Sorry, I can't answer that.", category="hate", mode="default")
    assert "Sorry, I can't answer that." in out
    assert "rephrasing" in out.lower() or "rephrase" in out.lower()
    assert "hate" in out.lower()  # category context line
    assert "default" in out


def test_friendly_message_no_category_context_for_distress():
    out = friendly_message("Blocked.", category="self_harm", mode="safe")
    # Distress categories should not get a bureaucratic moderation tag line.
    assert "Moderation:" not in out
    # But should include support-line hint.
    assert "iCall" in out or "Vandrevala" in out


def test_friendly_message_unknown_category_uses_generic_hint():
    out = friendly_message("Blocked.", category="totally_unknown", mode="default")
    assert "Try rephrasing" in out
    assert "Moderation:" not in out  # no leak of unknown raw tag name


def test_friendly_message_handles_missing_inputs():
    assert friendly_message("", category=None, mode="default") != ""
    out = friendly_message("Base.", category=None, mode="default")
    assert "Base." in out
