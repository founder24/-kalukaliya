"""
KnowledgeObject - Canonical content model for educational resources.
Supports multi-board, multi-language, multi-page-type content with
derivative tracking and search indexing.
"""

from beanie import Document
from pydantic import BaseModel, Field
from typing import Optional, Literal
from datetime import datetime, timezone


class ContentBlock(BaseModel):
    """Individual content block within a knowledge object."""

    block_type: Literal["text", "heading", "list", "code", "image", "table"] = "text"
    content: str = ""
    language: str = "en"
    order: int = 0


class ContentMetadata(BaseModel):
    """Metadata about the content for SEO and filtering."""

    board: str = ""  # e.g., "SEBA", "CBSE", "AHSEC"
    class_level: str = ""  # e.g., "10", "12"
    subject: str = ""  # e.g., "science", "mathematics"
    chapter: str = ""  # e.g., "chemical-reactions"
    chapter_number: Optional[int] = None
    topic: Optional[str] = None
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    language: str = "en"
    estimated_read_time_minutes: int = 5
    keywords: list[str] = Field(default_factory=list)


class DerivativeHashes(BaseModel):
    """Hashes of generated derivatives for change detection."""

    notes_html: Optional[str] = None
    mcqs_html: Optional[str] = None
    summary_html: Optional[str] = None
    definitions_html: Optional[str] = None
    important_questions_html: Optional[str] = None
    search_index: Optional[str] = None


class GeneratedContent(BaseModel):
    """Generated derivative content (MCQs, summaries, etc.)."""

    mcqs: list[dict] = Field(default_factory=list)
    summary: str = ""
    definitions: list[dict] = Field(default_factory=list)
    important_questions: list[dict] = Field(default_factory=list)


class KnowledgeObject(Document):
    """
    Canonical knowledge object representing an educational chapter/topic.
    Each object can generate multiple page types (notes, mcqs, summary, etc.).
    """

    # Identity
    slug: str = Field(..., description="Unique URL-friendly identifier")
    title: str = ""
    description: str = ""

    # Core content
    body_markdown: str = ""
    content_blocks: list[ContentBlock] = Field(default_factory=list)

    # Classification
    metadata: ContentMetadata = Field(default_factory=ContentMetadata)

    # Generated derivatives
    generated: GeneratedContent = Field(default_factory=GeneratedContent)
    derivative_hashes: DerivativeHashes = Field(default_factory=DerivativeHashes)

    # Rendered HTML cache
    rendered_html: dict[str, str] = Field(
        default_factory=dict,
        description="Cached rendered HTML keyed by page_type",
    )

    # Publishing state
    status: Literal["draft", "published", "archived"] = "draft"
    published_at: Optional[datetime] = None
    last_pipeline_run: Optional[datetime] = None

    # Analytics (excluded from public API responses)
    page_views: int = 0
    search_impressions: int = 0

    # Timestamps
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    class Settings:
        name = "knowledge_objects"
        indexes = [
            # Compound index for URL resolution
            [
                ("metadata.board", 1),
                ("metadata.class_level", 1),
                ("metadata.subject", 1),
                ("metadata.chapter", 1),
            ],
            # Unique slug
            [("slug", 1)],
            # Status + updated for admin listing
            [("status", 1), ("updated_at", -1)],
            # Text search on title, description, body
            [("title", "text"), ("description", "text"), ("body_markdown", "text")],
        ]
