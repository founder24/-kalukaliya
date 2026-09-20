"""Lightweight, deterministic response quality checks for chat output."""

import re


_ASSAMESE_PATTERN = re.compile(r"[\u0980-\u09FF]")


def score_response_quality(response: str, expected_language: str) -> dict:
    """Return a privacy-safe quality score and flags without storing content."""
    text = (response or "").strip()
    compact = re.sub(r"\s+", "", text)
    flags: list[str] = []
    score = 1.0

    if len(compact) < 20:
        flags.append("too_short")
        score -= 0.35

    script_ratio = (
        len(_ASSAMESE_PATTERN.findall(text)) / len(compact) if compact else 0.0
    )
    if expected_language == "en" and script_ratio > 0.25:
        flags.append("language_mismatch")
        score -= 0.5
    elif expected_language == "as" and len(compact) >= 40 and script_ratio < 0.1:
        flags.append("language_mismatch")
        score -= 0.5

    return {
        "score": round(max(0.0, min(1.0, score)), 2),
        "passed": not flags,
        "flags": flags,
    }