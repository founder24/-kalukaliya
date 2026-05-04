"""Task #362 §1 Tier-1 recall-intent classifier tests."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from recall_intent import explain, is_recall_intent


# ── Empty / invalid input ───────────────────────────────────────────────────
def test_empty_inputs_return_false():
    for v in (None, "", "   ", "\n\t"):
        assert is_recall_intent(v) is False


# ── User opt-in prefix ──────────────────────────────────────────────────────
def test_user_prefix_always_fires():
    assert is_recall_intent("@recall what was my last question?") is True
    assert is_recall_intent("  @RECALL the bit about photosynthesis") is True
    assert explain("@recall foo")["rule"] == "user_prefix"


# ── Curated phrase list ─────────────────────────────────────────────────────
def test_phrase_list_hits():
    positives = [
        "Earlier you said something about Newton's third law — repeat it?",
        "go back to the part about ATP synthesis",
        "What did I ask about earlier?",
        "Previously, you mentioned a formula — what was it?",
        "Remember when we discussed mitosis?",
        "as you mentioned, the bond is covalent — explain again",
        "the thing about parallel circuits",
        "Earlier in our chat you gave me a mnemonic, what was it?",
    ]
    for p in positives:
        assert is_recall_intent(p) is True, f"phrase miss on: {p!r}"


def test_phrase_match_is_case_insensitive():
    assert is_recall_intent("EARLIER YOU SAID something") is True
    assert is_recall_intent("Earlier You Said something") is True


# ── Leading anaphor (high-precision pair-match) ─────────────────────────────
def test_leading_anaphor_with_real_question_fires():
    assert is_recall_intent("That equation you wrote — can you derive it?") is True
    assert is_recall_intent("Those four reactions, can we redo them?") is True


def test_leading_anaphor_too_short_does_not_fire():
    # Bare "that?" / "what?" must not trigger a Pinecone lookup.
    assert is_recall_intent("That?") is False
    assert is_recall_intent("Those?") is False
    assert is_recall_intent("It works.") is False
    info = explain("That?")
    assert info["hit"] is False
    assert info["rule"] == "leading_anaphor_too_short"


# ── Negatives (the bias is toward firing, so negatives matter) ──────────────
def test_normal_questions_do_not_fire():
    negatives = [
        "What is photosynthesis?",
        "Solve x^2 + 5x + 6 = 0.",
        "Explain Newton's first law.",
        "How does a transistor work?",
        "Translate 'hello' to Assamese.",
        "Tell me about the French Revolution.",
        "Hi!",
    ]
    for p in negatives:
        assert is_recall_intent(p) is False, f"false positive on: {p!r}"


def test_explain_returns_match_text_for_phrase_rule():
    info = explain("Earlier you said something")
    assert info["hit"] is True
    assert info["rule"] == "phrase"
    assert "earlier you said" in info["match"].lower()
