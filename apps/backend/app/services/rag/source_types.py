"""
Canonical source-type definitions for the Syrabit RAG pipeline.

This is the SINGLE source of truth for source type strings used across:
  - Content editor (frontend section names)
  - RAG chunker (chunking strategy selector)
  - MongoDB rag_chunks / chunks collections (source_type field, snake_case)
  - Cloudflare Vectorize (sourceType metadata field, camelCase)
  - Retrieval filters (both Atlas vector search and Vectorize queries)

Rule: snake_case in MongoDB / Python code, camelCase in Vectorize metadata only.
Never use an ad-hoc string — always import from here.
"""

from typing import Literal

# ── Canonical internal source type enum ──────────────────────────────────────
# These are the values stored in MongoDB source_type and used by the chunker.
SourceType = Literal[
    "notes",
    "definition",
    "important_questions",
    "mcqs",
    "pyq",
    "chapter_question",
]

VALID_SOURCE_TYPES: frozenset[str] = frozenset(
    ["notes", "definition", "important_questions", "mcqs", "pyq", "chapter_question"]
)

# Default source type when none is specified.
# "notes" is the safe default — all chapters have notes-style text.
DEFAULT_SOURCE_TYPE: str = "notes"

# ── Frontend section → internal source type mapping ──────────────────────────
# The frontend uses short section names in the content card ("notes", "qa", "pyq").
# These map to the internal source_type values used by the chunker and retrieval.
#
# Frontend section  →  Internal source_type  →  Chunker strategy
# ─────────────────────────────────────────────────────────────────
#   "notes"         →  "notes"               →  semantic (H2/H3 split)
#   "qa"            →  "important_questions" →  qa_pair
#   "pyq"           →  "pyq"                 →  qa_pair
FRONTEND_SECTION_TO_SOURCE_TYPE: dict[str, str] = {
    "notes": "notes",
    "qa": "important_questions",
    "pyq": "pyq",
    # Legacy aliases
    "question_paper": "pyq",
    "important_questions": "important_questions",
    "definition": "definition",
    "mcqs": "mcqs",
}

# ── Internal source type → frontend section mapping (reverse) ─────────────────
SOURCE_TYPE_TO_FRONTEND_SECTION: dict[str, str] = {
    "notes": "notes",
    "definition": "notes",        # definition pages displayed in notes tab
    "important_questions": "qa",
    "chapter_question": "qa",
    "mcqs": "qa",
    "pyq": "pyq",
}

# ── MongoDB field names (snake_case) ─────────────────────────────────────────
# Used in Atlas $vectorSearch pre-filter and MongoDB queries.
ATLAS_FILTER_FIELDS = frozenset(
    ["language", "source_type", "subject_id", "chapter_id", "board", "class_level"]
)

# ── Vectorize metadata field names (camelCase) ───────────────────────────────
# Used ONLY when writing to or querying Cloudflare Vectorize.
# These MUST match the metadata indexes created via wrangler:
#   wrangler vectorize create-metadata-index syrabit-rag --property-name=subjectId --type=string
VECTORIZE_INDEXED_FIELDS = frozenset(
    ["subjectId", "chapterId", "topicId", "medium", "sourceType", "chunkType"]
)


def normalize_source_type(raw: str | None) -> str:
    """
    Normalize any source type string (from frontend, DB, or editor) to
    a canonical internal source_type value.

    Examples:
        "qa"               → "important_questions"
        "question_paper"   → "pyq"
        "book_pdf"         → "notes"   (unknown types fall back to notes)
        None               → "notes"
    """
    if not raw:
        return DEFAULT_SOURCE_TYPE
    mapped = FRONTEND_SECTION_TO_SOURCE_TYPE.get(raw.lower().strip())
    if mapped:
        return mapped
    if raw in VALID_SOURCE_TYPES:
        return raw
    # Unknown type — fall back to notes with a warning marker in the value
    return DEFAULT_SOURCE_TYPE


def to_vectorize_source_type(internal_source_type: str) -> str:
    """
    Convert internal snake_case source_type to camelCase for Vectorize metadata.

    Only the Vectorize write path (_vectorize_metadata in ingestion_v2.py)
    should call this. All other code uses snake_case.
    """
    _MAP = {
        "notes": "notes",
        "definition": "definition",
        "important_questions": "importantQuestions",
        "chapter_question": "chapterQuestion",
        "mcqs": "mcqs",
        "pyq": "pyq",
    }
    return _MAP.get(internal_source_type, internal_source_type)


def snake_to_vectorize_filter(filters: dict) -> dict:
    """
    Convert a snake_case filter dict (used in Python/MongoDB) to
    the camelCase keys required by Cloudflare Vectorize metadata queries.

    Input:  { "subject_id": "s13", "source_type": "notes", "chapter_id": "ch01" }
    Output: { "subjectId": "s13",  "sourceType": "notes",  "chapterId": "ch01"  }

    Only fields indexed in Vectorize are included in the output.
    Non-indexed fields are silently dropped.
    """
    _KEY_MAP = {
        "subject_id": "subjectId",
        "chapter_id": "chapterId",
        "topic_id": "topicId",
        "medium": "medium",
        "source_type": "sourceType",
        "chunk_type": "chunkType",
    }
    result: dict = {}
    for snake_key, value in filters.items():
        camel_key = _KEY_MAP.get(snake_key)
        if camel_key and value:
            result[camel_key] = value
    return result
