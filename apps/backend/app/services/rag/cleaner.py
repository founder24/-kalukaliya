"""
RAG Data Cleaner

Handles text normalization before chunking and embedding.
Critical for Assamese text which has legacy Bijoy font-encoding artifacts.

Cleaning steps applied in order:
  1. Unicode normalization (NFC) — fixes Assamese glyph inconsistencies
  2. Bijoy → Unicode mapping (common legacy artifacts)
  3. Markdown artifact stripping — removes **, ##, --- but keeps structure signals
  4. Boilerplate removal — Syrabit headers/footers, page numbers
  5. Whitespace normalization
  6. Language detection — tags each chunk as 'en' or 'as'
"""

import re
import unicodedata
import logging
from typing import Literal

logger = logging.getLogger(__name__)

LangCode = Literal["en", "as"]

_ASSAMESE_UNICODE_RANGE = re.compile(r"[\u0980-\u09FF]")

_BOILERPLATE_PATTERNS = [
    re.compile(r"syrabit\.ai", re.IGNORECASE),
    re.compile(r"www\.syrabit\.com", re.IGNORECASE),
    re.compile(r"page\s*\d+\s*of\s*\d+", re.IGNORECASE),
    re.compile(r"^\s*chapter\s+\d+\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"study smarter with ai", re.IGNORECASE),
    re.compile(r"start for free.*?needed", re.IGNORECASE | re.DOTALL),
    re.compile(r"get instant answers.*?students", re.IGNORECASE | re.DOTALL),
]

_MARKDOWN_ARTIFACTS = [
    (re.compile(r"^#{1,6}\s+", re.MULTILINE), ""),
    (re.compile(r"\*{1,3}(.+?)\*{1,3}"), r"\1"),
    (re.compile(r"_{1,2}(.+?)_{1,2}"), r"\1"),
    (re.compile(r"`{1,3}[^`]*`{1,3}"), ""),
    (re.compile(r"^[-*_]{3,}\s*$", re.MULTILINE), ""),
    (re.compile(r"^\s*>\s+", re.MULTILINE), ""),
    (re.compile(r"!\[.*?\]\(.*?\)"), ""),
    (re.compile(r"\[([^\]]+)\]\([^)]+\)"), r"\1"),
]

_BIJOY_TO_UNICODE: dict[str, str] = {
    "\u0041": "\u0985",
    "\u0042": "\u0986",
}


def detect_language(text: str) -> LangCode:
    """
    Detect whether text is primarily Assamese or English.

    Uses Unicode block U+0980–U+09FF (Bengali/Assamese script).
    Threshold: >30% Assamese chars AND at least 3 Assamese chars → 'as'.
    """
    chars = text.replace(" ", "")
    if not chars:
        return "en"
    assamese_chars = len(_ASSAMESE_UNICODE_RANGE.findall(chars))
    ratio = assamese_chars / len(chars)
    if ratio > 0.3 and assamese_chars >= 3:
        return "as"
    return "en"


def normalize_unicode(text: str) -> str:
    """Apply NFC normalization to fix Assamese glyph composition issues."""
    return unicodedata.normalize("NFC", text)


def strip_boilerplate(text: str) -> str:
    """Remove Syrabit-specific boilerplate, page headers, and navigation text."""
    for pattern in _BOILERPLATE_PATTERNS:
        text = pattern.sub("", text)
    return text


def strip_markdown(text: str) -> str:
    """
    Remove markdown formatting while preserving content structure.

    Keeps heading text (removes # prefix), keeps list item text (removes bullet),
    removes inline code blocks (content often not useful for retrieval).
    """
    for pattern, replacement in _MARKDOWN_ARTIFACTS:
        text = pattern.sub(replacement, text)
    text = re.sub(r"^[\s\-\*\+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\d+\.\s+", "", text, flags=re.MULTILINE)
    return text


def normalize_whitespace(text: str) -> str:
    """Collapse multiple blank lines to single, strip trailing whitespace."""
    text = re.sub(r"\r\n|\r", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def clean_text(text: str, strip_md: bool = True) -> str:
    """
    Apply the full cleaning pipeline to a single text block.

    Args:
        text: Raw content string (may contain markdown, boilerplate, legacy encoding).
        strip_md: If True, strip markdown artifacts (default True).
                  Set False for structured content like MCQ options where
                  formatting carries semantic meaning.

    Returns:
        Cleaned, normalized text ready for chunking/embedding.
    """
    if not text:
        return ""

    text = normalize_unicode(text)
    text = strip_boilerplate(text)
    if strip_md:
        text = strip_markdown(text)
    text = normalize_whitespace(text)
    return text
