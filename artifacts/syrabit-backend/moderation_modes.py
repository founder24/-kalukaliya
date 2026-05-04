"""Task #362 §4 — Per-user moderation modes + friendlier veto messages.

Three modes:

* ``safe``      — stricter than default; suitable for school accounts /
                   parent-managed profiles. Blocks at low severity for
                   hate / sexual / self_harm / violence.
* ``default``   — the existing #347/#360 behaviour. Block at moderate
                   severity. This row exists explicitly so changing the
                   mode is a documented config flip, never a stealth
                   behaviour change.
* ``challenge`` — looser, opt-in only. Suitable for adult researchers /
                   advanced exam prep where stricter modes over-block
                   legitimate medical, legal, or historical content.
                   Self-harm stays at the safe-mode floor regardless.

**Hard floors** that no mode can cross — codified as constants, not
config — are enforced in :func:`is_hard_floor_category` and exercised by
unit tests:

* ``csam``                — CSAM signals
* ``self_harm_intent``    — credible self-harm-with-intent
* ``exam_paper_leakage``  — exam paper / model-paper leakage signals
* ``injection``           — prompt-injection (always blocked, all modes)

Friendlier veto messages add a *rephrase hint* and a *category context*
line (when the moderation provider supplies a category) on top of the
base block message.
"""
from __future__ import annotations

from typing import Optional

# Mode → category → severity threshold (Azure Content Safety 0–7 scale;
# block when provider score is >= the value here). The categories
# `csam`, `self_harm_intent`, `exam_paper_leakage`, `injection` are
# absent on purpose — they are *hard floors* and live in
# ``_HARD_FLOOR_CATEGORIES`` below.
_MODE_THRESHOLDS: dict = {
    "safe": {
        "hate": 2, "sexual": 2, "self_harm": 2, "violence": 2,
    },
    "default": {
        "hate": 4, "sexual": 4, "self_harm": 4, "violence": 4,
    },
    "challenge": {
        "hate": 6, "sexual": 6, "violence": 6,
        # Self-harm stays at safe-mode floor regardless — non-negotiable.
        "self_harm": 2,
    },
}

# Categories that block in every mode, regardless of severity score.
# Adding a category here is a security decision, not a tuning decision.
_HARD_FLOOR_CATEGORIES = frozenset({
    "csam",
    "self_harm_intent",
    "exam_paper_leakage",
    "injection",
})

# Friendly per-category rephrase hints — appended after the base block
# message. Falls back to the generic hint when a category is not mapped.
_REPHRASE_HINTS: dict = {
    "hate":     "Try rephrasing your question without language that targets a group of people.",
    "sexual":   "I focus on academic topics — try asking about the syllabus or a study concept instead.",
    "violence": "I can help with the academic side of this topic (e.g. history, biology) — try framing it that way.",
    "self_harm": "If you or someone you know is in distress, please reach out — iCall (9152987821) or Vandrevala Foundation (1860-2662-345). I'm here for academic help whenever you're ready.",
    "self_harm_intent": "Please reach out — iCall (9152987821) or Vandrevala Foundation (1860-2662-345). You are not alone.",
    "csam":               "This kind of content is never allowed.",
    "exam_paper_leakage": "I won't share or summarise live exam papers, but I can absolutely help you study for one — try asking about a concept or past-year problem.",
    "injection":          "Try rephrasing your question without instructions that override my guidelines — just ask the academic question directly!",
    "cheating":           "Ask me to *explain* a concept, work through a practice problem, or clarify a topic — I'm happy to walk you through it.",
}

_GENERIC_REPHRASE_HINT = (
    "Try rephrasing your question to focus on the academic concept "
    "you'd like to understand."
)


def normalize_mode(mode: Optional[str]) -> str:
    """Coerce arbitrary input to one of the three valid modes."""
    if isinstance(mode, str):
        m = mode.strip().lower()
        if m in ("safe", "default", "challenge"):
            return m
    return "default"


def is_hard_floor_category(category: Optional[str]) -> bool:
    """Return True for categories that block in *every* mode."""
    if not category:
        return False
    return category.strip().lower() in _HARD_FLOOR_CATEGORIES


def should_block(category: str, severity: int, mode: str = "default") -> bool:
    """Return True when *category*/*severity* is blocked under *mode*.

    Hard-floor categories always block regardless of severity or mode.
    Severity is the provider-supplied integer 0–7 (Azure Content Safety
    convention; Llama Guard 'unsafe' verdicts should map to severity ≥ 4).
    """
    if is_hard_floor_category(category):
        return True
    mode = normalize_mode(mode)
    thresholds = _MODE_THRESHOLDS.get(mode, _MODE_THRESHOLDS["default"])
    cat = (category or "").strip().lower()
    threshold = thresholds.get(cat)
    if threshold is None:
        # Unknown category: be conservative — block at the default
        # severity floor so an unrecognised category from a future
        # provider revision does not silently leak through challenge mode.
        threshold = _MODE_THRESHOLDS["default"].get(cat, 4)
    try:
        return int(severity) >= int(threshold)
    except (TypeError, ValueError):
        return False


def friendly_message(
    base_message: str,
    *,
    category: Optional[str] = None,
    mode: str = "default",
) -> str:
    """Build a friendlier user-visible veto message.

    Pattern: ``"<base_message>\\n\\n<rephrase hint>\\n\\n<category context>"``,
    with each component included only when meaningful. The category line
    is only added when a known category was supplied (avoids exposing
    raw provider tag names to users on unknown categories).
    """
    parts: list[str] = []
    base = (base_message or "").strip()
    if base:
        parts.append(base)
    cat_norm = (category or "").strip().lower()
    hint = _REPHRASE_HINTS.get(cat_norm) or _GENERIC_REPHRASE_HINT
    if hint and hint not in base:
        parts.append(hint)
    if cat_norm in _REPHRASE_HINTS and cat_norm not in ("self_harm", "self_harm_intent", "csam"):
        # Category context line for the policy-veto categories. We
        # deliberately suppress this for distress / CSAM categories so
        # the response reads as supportive, not bureaucratic.
        mode_norm = normalize_mode(mode)
        parts.append(f"(Moderation: {cat_norm.replace('_', ' ')} — mode={mode_norm})")
    return "\n\n".join(parts)


__all__ = [
    "friendly_message",
    "is_hard_floor_category",
    "normalize_mode",
    "should_block",
]
