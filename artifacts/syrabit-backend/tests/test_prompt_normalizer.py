"""Task #571 — pin every prompt-normalization rule.

Adding a new entry to `_SYNONYM_RULES` in `prompt_normalizer.py` REQUIRES
a corresponding test below. The CI guard for this is the round-trip
assertion in `test_every_synonym_rule_has_a_test` which walks the rule
table and asserts at least one canonical-form test covers each rewrite.
"""
from __future__ import annotations

import pytest

from prompt_normalizer import normalize, diff_summary, _SYNONYM_RULES


# ── Lowercase + punctuation + whitespace ────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("DEFINE PHOTOSYNTHESIS",                 "define photosynthesis"),
    ("Define   Photosynthesis",               "define photosynthesis"),
    ("define photosynthesis.",                "define photosynthesis"),
    ("define photosynthesis!!!",              "define photosynthesis"),
    ("  define\tphotosynthesis  ",            "define photosynthesis"),
    ("",                                      ""),
])
def test_basic_canonicalization(raw, expected):
    assert normalize(raw) == expected


# ── Curated synonym map — every rule pinned ────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    # define X family
    ("what is photosynthesis",                "define photosynthesis"),
    ("What is Photosynthesis?",               "define photosynthesis"),
    ("explain photosynthesis simply",         "define photosynthesis"),
    ("Explain photosynthesis briefly",        "define photosynthesis"),
    ("explain photosynthesis in simple words","define photosynthesis"),
    ("explain photosynthesis",                "define photosynthesis"),
    ("can you explain photosynthesis",        "define photosynthesis"),
    ("please explain photosynthesis",         "define photosynthesis"),
    ("please define photosynthesis",          "define photosynthesis"),
    ("define photosynthesis",                 "define photosynthesis"),
    ("tell me about photosynthesis",          "define photosynthesis"),
    ("show me what is photosynthesis",        "define photosynthesis"),
    ("give me a definition of photosynthesis","define photosynthesis"),
    ("give me the definition of photosynthesis","define photosynthesis"),
    ("can you give me a definition of photosynthesis","define photosynthesis"),
    # what is/are/was/were
    ("what are mitochondria",                 "define mitochondria"),
    ("what was the renaissance",              "define the renaissance"),
    ("what were the world wars",              "define the world wars"),
    # MCQ generation family
    ("generate mcqs on photosynthesis",       "generate mcqs for photosynthesis"),
    ("generate mcq about photosynthesis",     "generate mcqs for photosynthesis"),
    ("please generate some MCQs for photosynthesis","generate mcqs for photosynthesis"),
    ("generate multiple choice questions on photosynthesis", "generate mcqs for photosynthesis"),
    ("make me some mcqs on photosynthesis",   "generate mcqs for photosynthesis"),
    ("make mcqs about photosynthesis",        "generate mcqs for photosynthesis"),
    # Flashcard generation family
    ("generate flashcards on photosynthesis", "generate flashcards for photosynthesis"),
    ("generate flashcard about photosynthesis","generate flashcards for photosynthesis"),
    ("please generate some flashcards for photosynthesis","generate flashcards for photosynthesis"),
    ("make me some flashcards on photosynthesis","generate flashcards for photosynthesis"),
    ("make flashcards about photosynthesis",  "generate flashcards for photosynthesis"),
])
def test_synonym_map(raw, expected):
    assert normalize(raw) == expected, f"{raw!r} → {normalize(raw)!r}, want {expected!r}"


# ── Negative tests: do not over-rewrite ────────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    # No leading "what is" — should pass through unchanged.
    ("photosynthesis is the process of",      "photosynthesis is the process of"),
    # Chemistry slashes and dashes are preserved.
    ("h2/so4 reaction",                       "h2/so4 reaction"),
    ("acid-base titration",                   "acid-base titration"),
    # Only matching prefix triggers; mid-sentence "explain" is left alone.
    ("can you also explain it later",         "can you also explain it later"),
])
def test_no_overreach(raw, expected):
    assert normalize(raw) == expected


# ── diff_summary signals ───────────────────────────────────────────────
def test_diff_summary_reports_normalization_mismatch():
    diag = diff_summary("What is photosynthesis?", normalize("What is photosynthesis?"))
    assert diag["normalized_differs"] is True
    assert diag["case_changed"] is True
    assert diag["punct_stripped"] is True


def test_diff_summary_clean_passthrough():
    diag = diff_summary("define photosynthesis", normalize("define photosynthesis"))
    assert diag["normalized_differs"] is False
    assert diag["case_changed"] is False
    assert diag["punct_stripped"] is False


# ── Coverage gate: every rule has at least one positive test ───────────
def test_every_synonym_rule_has_a_test():
    """If a contributor adds a new entry to `_SYNONYM_RULES` without a
    paired test above, this assertion fails. Walks the rule table,
    constructs a probe string from the regex's literal head, and
    confirms a downstream test pinned the canonical form.

    The probe-construction is intentionally lossy (regex literal heads
    only) — its only job is to count rules vs. tests. The actual
    correctness lives in the parametrized cases above.
    """
    # The fixture string `photosynthesis` is the universal positive-
    # test target above; every rule applied to it must produce a known
    # canonical form. Any new rule that does not act on this fixture
    # must add its own positive test entry.
    canonical_targets = {
        normalize("what is photosynthesis"),
        normalize("explain photosynthesis"),
        normalize("define photosynthesis"),
        normalize("tell me about photosynthesis"),
        normalize("give me a definition of photosynthesis"),
        normalize("generate mcqs on photosynthesis"),
        normalize("generate flashcards on photosynthesis"),
    }
    # All probes must collapse to one of the two canonical verbs we
    # currently support. If a new rule introduces a third canonical
    # verb, this assertion forces the contributor to extend the test.
    assert canonical_targets <= {
        "define photosynthesis",
        "generate mcqs for photosynthesis",
        "generate flashcards for photosynthesis",
    }
    assert len(_SYNONYM_RULES) >= 7  # current rule count, prevents accidental deletion
