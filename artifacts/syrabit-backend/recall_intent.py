"""Task #362 §1 — Recall-intent classifier (Tier 1, regex/keyword fast path).

Detects when a user prompt likely refers to something said earlier in the
conversation, e.g. "earlier you told me X", "go back to that", "what
did I ask about?". Used by the chat dispatcher to gate the optional
summary-vector Pinecone lookup so most turns pay zero extra latency.

This is the **Tier 1** detector only:

* O(1) per turn (~µs cost).
* Curated phrase list (substring match, case-insensitive).
* Explicit ``@recall`` user prefix override.
* Anaphoric / temporal token list — only counted when paired with a
  phrase miss + token presence so a bare "it" doesn't constantly fire.

The Tier-2 LLM classifier (1-token yes/no via Workers-AI fast-mode) is
deferred until Tier 1 produces calibration data — see task spec.

Calibration loop (operator-side, lives in the runbook): sample 200
turns/week, hand-label, measure precision/recall, edit
``RECALL_PHRASES`` / ``ANAPHORIC_TOKENS`` constants below. Bias toward
firing — false positives cost ~50 ms (one summary-vector lookup); false
negatives cost a missed recall.

Targets: ≥85% recall on labelled positives, ≤15% false-positive rate.
"""
from __future__ import annotations

import re
from typing import Optional

# ── Tier-1 phrase list (substring, case-insensitive) ────────────────────────
# Editable via runbook — every change should land alongside a calibration
# data point. Phrases are deliberately literal: anchoring on the exact
# words real users type beats fancy regexes here.
RECALL_PHRASES: tuple = (
    "earlier you said",
    "earlier you told",
    "earlier you mentioned",
    "you said earlier",
    "you told me earlier",
    "you mentioned earlier",
    "you mentioned before",
    "you said before",
    "you told me before",
    "you just said",
    "you just told",
    "go back to",
    "going back to",
    "back to what",
    "back to that",
    "what did i ask",
    "what was my question",
    "what was i asking",
    "previously you",
    "previously,",
    "last time you",
    "last time we",
    "remember when",
    "remember what",
    "as you said",
    "as you mentioned",
    "the thing about",
    "the bit about",
    "the part about",
    "the topic we",
    "earlier in our chat",
    "earlier in this chat",
    "earlier in the conversation",
)

# Explicit user opt-in prefix — always fires.
RECALL_USER_PREFIX = "@recall"

# Anaphoric / temporal tokens — only counted when one is present *and*
# Tier-1 phrase match missed. These alone don't trigger; they raise the
# probability enough that Tier 2 (when wired) would run. For Tier 1 we
# pair-match: an anaphoric token plus a back-reference cue ("that", "it",
# "those") at the *start* of the prompt is the strongest Tier-1-only
# signal, so we treat that as a hit.
ANAPHORIC_TOKENS: frozenset = frozenset({
    "that", "those", "this", "these", "it", "they", "them",
    "previously", "earlier", "before", "above",
})

# Compiled once for hot-path use.
_RECALL_PHRASE_RE = re.compile(
    "|".join(re.escape(p) for p in RECALL_PHRASES),
    re.IGNORECASE,
)
# A leading anaphoric token like "Those..." or "That one you mentioned"
# is a Tier-1-detectable recall cue. We require the anaphor at the start
# (≤ first 3 words) to keep precision high.
_LEADING_ANAPHOR_RE = re.compile(
    r"^\s*\W*(?:" + "|".join(re.escape(t) for t in sorted(ANAPHORIC_TOKENS)) + r")\b",
    re.IGNORECASE,
)
_USER_PREFIX_RE = re.compile(r"^\s*@recall\b", re.IGNORECASE)


def is_recall_intent(prompt: Optional[str]) -> bool:
    """Tier-1 verdict: True if the prompt likely needs full-conversation recall.

    Args:
        prompt: The user message (any string-or-None input is safe).

    Returns:
        True when any of: ``@recall`` prefix, a phrase from
        ``RECALL_PHRASES``, or a leading anaphoric token paired with a
        sentence longer than 4 words (filters out bare "that?" /
        "what?" replies that are not actually recall requests).
    """
    if not prompt:
        return False
    text = str(prompt).strip()
    if not text:
        return False
    if _USER_PREFIX_RE.search(text):
        return True
    if _RECALL_PHRASE_RE.search(text):
        return True
    # Leading anaphor — only counts when the prompt is a real question
    # (≥ 5 words) so a bare "that" / "those?" doesn't trigger a Pinecone
    # lookup. Bias toward precision here; phrase-list catches the easy
    # cases and Tier 2 will catch the rest.
    if _LEADING_ANAPHOR_RE.search(text):
        word_count = len(re.findall(r"\w+", text))
        if word_count >= 5:
            return True
    return False


def explain(prompt: Optional[str]) -> dict:
    """Diagnostic helper — returns which Tier-1 rule fired (for tests / logs)."""
    if not prompt:
        return {"hit": False, "rule": None}
    text = str(prompt).strip()
    if not text:
        return {"hit": False, "rule": None}
    if _USER_PREFIX_RE.search(text):
        return {"hit": True, "rule": "user_prefix"}
    m = _RECALL_PHRASE_RE.search(text)
    if m:
        return {"hit": True, "rule": "phrase", "match": m.group(0)}
    if _LEADING_ANAPHOR_RE.search(text):
        word_count = len(re.findall(r"\w+", text))
        if word_count >= 5:
            return {"hit": True, "rule": "leading_anaphor", "word_count": word_count}
        return {"hit": False, "rule": "leading_anaphor_too_short", "word_count": word_count}
    return {"hit": False, "rule": None}


__all__ = [
    "ANAPHORIC_TOKENS",
    "RECALL_PHRASES",
    "RECALL_USER_PREFIX",
    "explain",
    "is_recall_intent",
]
