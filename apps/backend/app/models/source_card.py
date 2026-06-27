"""
SourceCard — typed result of chat retrieval, emitted as SSE before LLM starts.

Field names in to_sse_dict() match exactly what ChatPage.jsx already parses
from the SSE stream, so the frontend requires zero changes to consume them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SourceCard:
    # Subject
    subject_name: Optional[str] = None
    subject_id: Optional[str] = None
    subject_slug: Optional[str] = None
    subject_icon: Optional[str] = None
    subject_gradient: Optional[str] = None

    # Chapter / topic
    chapter_name: Optional[str] = None
    chapter_slug: Optional[str] = None
    topic_name: Optional[str] = None

    # Curriculum context
    class_level: Optional[str] = None    # e.g. "Class 11"
    board_name: Optional[str] = None     # e.g. "AHSEC"
    board_slug: Optional[str] = None

    # Retrieval quality
    match_score: float = 0.0             # cosine similarity or chunk score
    source_type: str = "llm_only"        # "rag_chapter" | "rag_vectorize" | "rag_atlas" | "rag_inmem" | "web" | "llm_only"
    rag_path: str = "none"               # "mongodb" | "fast" | "vectorize" | "legacy_atlas" | "legacy_inmem" | "web" | "none"
    confidence_tier: str = "none"        # "high" | "mid" | "low" | "none" | "generic"
    rag_chunks: int = 0

    def to_sse_dict(self) -> dict:
        """
        Emit SSE payload. Keys match the field names ChatPage.jsx reads
        from the SSE stream meta object (rag_subject_name, rag_chapter_name, etc.).
        """
        # Derive class_slug from class_level: "Class 12" → "class-12"
        class_slug = (
            self.class_level.lower().replace(" ", "-") if self.class_level else None
        )
        return {
            "event": "source_card",
            # Subject
            "rag_subject_id": self.subject_id,
            "rag_subject_name": self.subject_name,
            "rag_subject_slug": self.subject_slug,
            "rag_subject_icon": self.subject_icon,
            "rag_subject_gradient": self.subject_gradient,
            # Chapter / topic
            "rag_chapter_name": self.chapter_name,
            "rag_chapter_slug": self.chapter_slug,
            "rag_topic_name": self.topic_name,
            # Board / class — use rag_ prefix so MessageBubble reads them correctly
            "rag_board_name": self.board_name,
            "rag_board_slug": self.board_slug,
            "rag_class_name": self.class_level,
            "rag_class_slug": class_slug,
            # Meta
            "rag_source": self.rag_path,
            "rag_chunks": self.rag_chunks,
            "match_score": round(self.match_score, 4),
            "source_type": self.source_type,
            "confidence_tier": self.confidence_tier,
        }
