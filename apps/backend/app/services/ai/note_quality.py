"""Validation shared by curriculum note-generation pipelines."""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable


MAX_PREAMBLE_DIFF_LINES = 8
MAX_PREAMBLE_DIFF_LINE_CHARS = 240


class ModelPreambleError(RuntimeError):
    """Raised when a model adds assistant-style introduction text to notes."""


_MODEL_PREAMBLE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "notes_introduction",
        re.compile(
            r"^\s*(?:here|below|the following)\s+(?:are|is)\b"
            r".{0,180}\b(?:study\s+)?notes?\b",
            flags=re.I | re.S,
        ),
    ),
    (
        "assistant_acknowledgement",
        re.compile(
            r"^\s*(?:sure|certainly|of course|absolutely)[!,.]?\s+"
            r"(?:here|below|i(?:'ll| will)\b)",
            flags=re.I,
        ),
    ),
    (
        "ai_disclaimer",
        re.compile(r"^\s*as an ai(?:\s+language)?\s+model\b", flags=re.I),
    ),
    (
        "first_person_offer",
        re.compile(
            r"^\s*i\s+(?:will|'ll)\s+(?:provide|present|give|create)\b",
            flags=re.I,
        ),
    ),
    (
        "notes_summary_introduction",
        re.compile(
            r"^\s*(?:these|the following)\s+(?:study\s+)?notes?\s+"
            r"(?:provide|cover|include|summarize)\b",
            flags=re.I,
        ),
    ),
)


def _bounded_diff_preview(
    before: str,
    after: str,
    *,
    max_lines: int = MAX_PREAMBLE_DIFF_LINES,
    max_line_chars: int = MAX_PREAMBLE_DIFF_LINE_CHARS,
) -> tuple[str, bool]:
    diff = list(
        difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile="model-output",
            tofile="normalized-output",
            lineterm="",
            n=1,
        )
    )
    preview_lines = [line[:max_line_chars] for line in diff[:max_lines]]
    return "\n".join(preview_lines), len(diff) > max_lines


def validate_generated_notes(
    record_id: str,
    raw_notes: str,
    *,
    normalizer: Callable[[str], str] | None = None,
    record_type: str = "chapter",
) -> None:
    """Reject known model introductions before generated notes are persisted.

    ``normalizer`` is only used to provide a bounded diagnostic diff. The
    validator never returns cleaned content: callers must not silently repair
    a generation that violated the notes contract.
    """
    candidate = raw_notes.lstrip()[:1000]
    for pattern_name, pattern in _MODEL_PREAMBLE_PATTERNS:
        if not pattern.search(candidate):
            continue

        normalized = normalizer(raw_notes) if normalizer else raw_notes
        preview, truncated = _bounded_diff_preview(raw_notes, normalized)
        diff_summary = preview or "(normalizer produced no diff)"
        if truncated:
            diff_summary += "\n... diff truncated ..."
        raise ModelPreambleError(
            f"Generated notes rejected for {record_type} {record_id}: "
            f"model preamble '{pattern_name}' detected; "
            f"raw_chars={len(raw_notes)} normalized_chars={len(normalized)}; "
            f"bounded_diff:\n{diff_summary}"
        )