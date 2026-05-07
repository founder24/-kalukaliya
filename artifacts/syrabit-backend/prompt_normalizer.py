"""prompt_normalizer — Task #571 deterministic prompt canonicalization.

A pure function that lowercases, strips punctuation, collapses whitespace,
and applies a curated synonym map so cache lookups for semantically
identical (but cosmetically different) prompts converge on a single key.

Scope is **exact-string canonicalization only** — there is no embedding
lookup, no fuzzy match, no token-level rewrite. Every synonym mapping is
listed explicitly in `_SYNONYM_RULES`, and the unit tests in
`tests/test_prompt_normalizer.py` pin every entry. Adding a new mapping
requires a corresponding test.

The normalizer is consumed by `ai_input_cache.normalize_messages` which
runs the function over every `content` string in a chat message list
before the cache key is computed. The original (un-normalized) messages
are still sent to the LLM on cache miss — the normalizer affects the
key only, never the upstream payload.

Public API (frozen by `tests/test_prompt_normalizer.py`):

    normalize(text: str) -> str
    diff_summary(original: str, normalized: str) -> dict

`diff_summary` reports whether each transform fired so
`ai_input_cache.get_response` can tag a miss with `normalization_mismatch`
when the cached side and the live side normalized differently.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Dict

# ── Curated synonym rules ─────────────────────────────────────────────
# Pattern → canonical form. Pattern is matched as a whole-word phrase
# at the start of the (already lowercased + punct-stripped) text.
# Every entry MUST have a unit test in `tests/test_prompt_normalizer.py`.
#
# Format: list of (regex_pattern, replacement). Order is meaningful —
# longer / more-specific rewrites come first so a later short rule does
# not steal a longer match.
_SYNONYM_RULES: list[tuple[re.Pattern, str]] = [
    # Definition-style requests collapse to the single canonical form
    # `define X` so "what is X", "explain X simply", "tell me about X",
    # "give me a definition of X" all hit the same cache slot.
    (re.compile(r"^(?:can you )?(?:please )?give me (?:a |the )?definition of (.+)$"), r"define \1"),
    (re.compile(r"^(?:can you )?(?:please )?(?:tell|show) me (?:about|what is) (.+)$"), r"define \1"),
    (re.compile(r"^(?:can you )?(?:please )?explain (.+?) (?:simply|briefly|in simple words?)$"), r"define \1"),
    (re.compile(r"^(?:can you )?(?:please )?explain (.+)$"),                                       r"define \1"),
    (re.compile(r"^what (?:is|are|was|were) (.+)$"),                                              r"define \1"),
    (re.compile(r"^(?:can you )?(?:please )?define (.+)$"),                                       r"define \1"),
    # MCQ / flashcard generation phrasings collapse to a stable verb.
    (re.compile(r"^(?:please )?generate (?:some )?(?:mcqs?|multiple choice questions?) (?:on|about|for) (.+)$"),
        r"generate mcqs for \1"),
    (re.compile(r"^(?:please )?make (?:me )?(?:some )?(?:mcqs?|multiple choice questions?) (?:on|about|for) (.+)$"),
        r"generate mcqs for \1"),
    (re.compile(r"^(?:please )?generate (?:some )?flashcards? (?:on|about|for) (.+)$"),
        r"generate flashcards for \1"),
    (re.compile(r"^(?:please )?make (?:me )?(?:some )?flashcards? (?:on|about|for) (.+)$"),
        r"generate flashcards for \1"),
]

# Punctuation we strip during normalization. We keep `-`, `_`, `/` because
# they appear inside chemistry / formula names where stripping them would
# materially change the meaning (e.g. `H2/SO4` → `H2 SO4`).
_PUNCT_RE = re.compile(r"[\.\?\!\,\;\:\"'`\(\)\[\]\{\}\*~]+")
_WS_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Return the canonical key form of `text`. Pure function.

    Steps (in order):
      1. NFKC unicode normalization (collapses width / compatibility variants).
      2. Lowercase.
      3. Strip outer whitespace.
      4. Drop the punctuation set above (kept: `-`, `_`, `/`).
      5. Collapse all whitespace runs to a single space.
      6. Apply curated synonym rules (first-match wins).
    """
    if not text:
        return ""
    s = unicodedata.normalize("NFKC", str(text))
    s = s.lower().strip()
    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    for pattern, replacement in _SYNONYM_RULES:
        new_s, n = pattern.subn(replacement, s)
        if n:
            s = new_s
            break
    return s


def diff_summary(original: str, normalized: str) -> Dict[str, bool]:
    """Report which transforms fired between `original` and `normalized`.

    Used by `ai_input_cache.get_response` to attribute a miss with the
    correct top-level reason (e.g. `normalization_mismatch` when the
    raw text differs from the normalized form a previously-cached
    write would have used).
    """
    raw = (original or "").strip()
    return {
        "case_changed":         raw.lower() != raw,
        "punct_stripped":       bool(_PUNCT_RE.search(raw)),
        "whitespace_collapsed": bool(_WS_RE.search(raw)) and "  " in raw,
        "synonym_applied":      normalize(raw) != raw.lower().strip().replace("  ", " "),
        "normalized_differs":   normalize(raw) != raw,
    }


__all__ = ["normalize", "diff_summary"]
