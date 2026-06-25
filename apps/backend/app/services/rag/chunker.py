"""
RAG Source-Type-Aware Chunker

Different content types need different chunking strategies:

  notes            → semantic split at H2/H3 headings (400 tokens, 50 overlap)
  definition       → one definition per chunk (150 tokens, no overlap)
  important_questions → Q+A pair per chunk (300 tokens, no overlap)
  mcqs             → stem + options + answer per chunk (200 tokens, no overlap)
  pyq              → question + answer/solution per chunk (350 tokens, no overlap)

All strategies produce dicts with 'text', 'token_count', and 'chunk_index'.
Token count is estimated at 4 chars/token (fast approximation, no tokenizer dep).
"""

import re
import logging
from typing import Literal

logger = logging.getLogger(__name__)

SourceType = Literal[
    "notes", "definition", "important_questions", "mcqs", "pyq", "chapter_question"
]

CHUNK_CONFIG: dict[str, dict] = {
    "notes": {
        "strategy": "semantic",
        "max_tokens": 400,
        "overlap_tokens": 50,
    },
    "definition": {
        "strategy": "sentence",
        "max_tokens": 150,
        "overlap_tokens": 0,
    },
    "important_questions": {
        "strategy": "qa_pair",
        "max_tokens": 300,
        "overlap_tokens": 0,
    },
    "chapter_question": {
        "strategy": "qa_pair",
        "max_tokens": 300,
        "overlap_tokens": 0,
    },
    "mcqs": {
        "strategy": "qa_pair",
        "max_tokens": 200,
        "overlap_tokens": 0,
    },
    "pyq": {
        "strategy": "qa_pair",
        "max_tokens": 350,
        "overlap_tokens": 0,
    },
}

_DEFAULT_CONFIG = {"strategy": "semantic", "max_tokens": 400, "overlap_tokens": 50}

_HEADING_RE = re.compile(r"^#{1,3}\s+.+$", re.MULTILINE)
_QA_SPLIT_RE = re.compile(
    r"(?=(?:^|\n)(?:Q\.?\s*\d*[:.]?|Question\s*\d*[:.]?|\*\*Q|##\s*Q))",
    re.IGNORECASE,
)
_SENTENCE_END_RE = re.compile(r"(?<=[.!?।])\s+")


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: 4 chars ≈ 1 token (no tokenizer dependency)."""
    return max(1, len(text) // 4)


def _split_by_tokens(
    text: str, max_tokens: int, overlap_tokens: int = 0
) -> list[str]:
    """
    Split text into chunks of at most max_tokens with optional token overlap.
    Splits on sentence boundaries where possible.
    """
    sentences = _SENTENCE_END_RE.split(text)
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        s_tokens = _estimate_tokens(sentence)

        if current_tokens + s_tokens > max_tokens and current:
            chunk_text = " ".join(current).strip()
            if chunk_text:
                chunks.append(chunk_text)
            if overlap_tokens > 0:
                overlap: list[str] = []
                overlap_count = 0
                for s in reversed(current):
                    t = _estimate_tokens(s)
                    if overlap_count + t > overlap_tokens:
                        break
                    overlap.insert(0, s)
                    overlap_count += t
                current = overlap
                current_tokens = overlap_count
            else:
                current = []
                current_tokens = 0

        current.append(sentence)
        current_tokens += s_tokens

    if current:
        chunk_text = " ".join(current).strip()
        if chunk_text:
            chunks.append(chunk_text)

    return chunks or [text]


def _chunk_semantic(text: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    """
    Split at H1/H2/H3 headings first, then by token budget within each section.
    Preserves section context: heading text is prepended to each sub-chunk.
    """
    sections: list[tuple[str, str]] = []
    parts = _HEADING_RE.split(text)
    headings = _HEADING_RE.findall(text)

    first_text = parts[0].strip() if parts else ""
    if first_text:
        sections.append(("", first_text))

    for i, heading in enumerate(headings):
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        sections.append((heading.lstrip("#").strip(), body))

    chunks: list[str] = []
    for heading, body in sections:
        if not body:
            if heading:
                chunks.append(heading)
            continue
        sub = _split_by_tokens(body, max_tokens=max_tokens, overlap_tokens=overlap_tokens)
        for s in sub:
            text_with_ctx = f"{heading}\n{s}".strip() if heading else s
            chunks.append(text_with_ctx)

    return chunks or [text]


def _chunk_sentence(text: str, max_tokens: int) -> list[str]:
    """
    Split on sentence boundaries. Each definition stands alone.
    No overlap — definitions should not bleed into each other.
    """
    sentences = [s.strip() for s in _SENTENCE_END_RE.split(text) if s.strip()]
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for sentence in sentences:
        t = _estimate_tokens(sentence)
        if current_tokens + t > max_tokens and current:
            chunks.append(" ".join(current))
            current = []
            current_tokens = 0
        current.append(sentence)
        current_tokens += t

    if current:
        chunks.append(" ".join(current))

    return chunks or [text]


def _chunk_qa_pairs(text: str, max_tokens: int) -> list[str]:
    """
    Split on Q/A pair boundaries.

    Detects patterns like:
      Q. What is...
      **Q:** What is...
      Question 1: ...
      1. What is... (numbered questions)

    Each Q+A block becomes one chunk. If a pair exceeds max_tokens,
    it is truncated to fit within the budget.
    """
    pairs = _QA_SPLIT_RE.split(text)
    numbered_re = re.compile(r"^\d+[\.\)]\s+\S", re.MULTILINE)

    if len(pairs) <= 1:
        numbered_parts = numbered_re.split(text)
        if len(numbered_parts) > 1:
            pairs = numbered_parts

    chunks: list[str] = []
    for pair in pairs:
        pair = pair.strip()
        if not pair:
            continue
        if _estimate_tokens(pair) <= max_tokens:
            chunks.append(pair)
        else:
            sub = _split_by_tokens(pair, max_tokens=max_tokens, overlap_tokens=0)
            chunks.extend(sub)

    return chunks or [text]


def chunk_content(
    text: str,
    source_type: str = "notes",
) -> list[dict]:
    """
    Chunk cleaned content according to its source type.

    Args:
        text: Cleaned text (run through cleaner.clean_text() first).
        source_type: One of notes/definition/important_questions/mcqs/pyq/chapter_question.

    Returns:
        List of dicts with:
          - text: chunk content
          - token_count: estimated token count
          - chunk_index: 0-based position within the source document
    """
    if not text or not text.strip():
        return []

    config = CHUNK_CONFIG.get(source_type, _DEFAULT_CONFIG)
    strategy = config["strategy"]
    max_tokens = config["max_tokens"]
    overlap = config.get("overlap_tokens", 0)

    if strategy == "semantic":
        raw_chunks = _chunk_semantic(text, max_tokens=max_tokens, overlap_tokens=overlap)
    elif strategy == "sentence":
        raw_chunks = _chunk_sentence(text, max_tokens=max_tokens)
    elif strategy == "qa_pair":
        raw_chunks = _chunk_qa_pairs(text, max_tokens=max_tokens)
    else:
        raw_chunks = _split_by_tokens(text, max_tokens=max_tokens, overlap_tokens=overlap)

    return [
        {
            "text": chunk.strip(),
            "token_count": _estimate_tokens(chunk),
            "chunk_index": i,
        }
        for i, chunk in enumerate(raw_chunks)
        if chunk.strip()
    ]
