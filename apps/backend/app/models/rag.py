"""
RAG data models — MongoDB collections for the ingestion + retrieval pipeline.

Collections:
  rag_documents    — uploaded file metadata (PDF, syllabus, PYQ, etc.)
  chunks           — text chunks with a pointer to the CF Vectorize vector ID
  content_nodes    — editable structured content per topic/medium (notes, questions)
  page_assets      — frontend page JSON paths on Cloudflare (browse-only)
  generation_jobs  — background ingestion/generation job tracking
"""

from datetime import datetime, timezone
from typing import Optional
from beanie import Document
from pydantic import Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RagDocument(Document):
    """
    Root metadata record for every uploaded source file.

    One RagDocument per upload (e.g. one English Physics PDF for Class 12 AHSEC).
    source_type drives the chunking strategy used during ingestion:
      book_pdf         → recursive/topic-wise chunks with overlap
      syllabus         → section-wise chunks
      pyq              → question-answer pair chunks
      chapter_question → question-answer pair chunks
    """

    subject_id: str
    chapter_id: Optional[str] = None
    medium: str
    source_type: str
    file_url: Optional[str] = None
    original_filename: Optional[str] = None
    page_count: Optional[int] = None
    status: str = "pending"
    error_message: Optional[str] = None
    ingested_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    class Settings:
        name = "rag_documents"


class Chunk(Document):
    """
    A single text chunk extracted from a RagDocument.

    The embedding lives in Cloudflare Vectorize (referenced by vector_id).
    This document is the join table — Vectorize returns vector_id, we look
    up chunk_text + metadata here.

    Metadata fields mirrored into CF Vectorize for pre-filter at query time:
      subject_id, chapter_id, topic_id, medium, source_type, chunk_type
    """

    document_id: str
    subject_id: str
    chapter_id: Optional[str] = None
    topic_id: Optional[str] = None
    medium: str
    source_type: str
    chunk_type: str = "topic_chunk"
    chunk_text: str
    chunk_index: int = 0
    token_count: int = 0
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    vector_id: Optional[str] = None
    embedding_model: str = "cf/baai/bge-m3"
    embedding_dim: int = 1024
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    class Settings:
        name = "chunks"


class ContentNode(Document):
    """
    Editable structured content for a topic/chapter/medium.

    node_type controls the shape of content:
      note        → prose explanation (markdown)
      definition  → term + body dict
      mcq         → question + options + answer
      short_qa    → question + answer
      long_qa     → question + answer

    Versioned: each publish bumps version. Status lifecycle:
      draft → review → published → archived
    """

    subject_id: str
    chapter_id: Optional[str] = None
    topic_id: Optional[str] = None
    medium: str
    node_type: str
    status: str = "draft"
    version: int = 1
    content: dict = Field(default_factory=dict)
    generated_by: Optional[str] = None
    published_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    class Settings:
        name = "content_nodes"


class PageAsset(Document):
    """
    Tracks a rendered frontend page stored as static JSON on Cloudflare.

    cloudflare_path is the key in R2 / KV (e.g.
    /learn/english/math/trigonometry/basic-identities.json).
    page_type:
      chapter_view  → chapter-level browse page
      topic_view    → topic-level content page
      cached_note   → AI-generated note page snapshot
    """

    subject_id: str
    chapter_id: Optional[str] = None
    topic_id: Optional[str] = None
    medium: str
    cloudflare_path: str
    page_type: str = "topic_view"
    invalidated: bool = False
    last_rendered_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    class Settings:
        name = "page_assets"


class GenerationJob(Document):
    """
    Background ingestion / content-generation job.

    job_type:
      ingest_document     → PDF → chunks → CF Vectorize
      reindex_document    → delete + re-ingest
      generate_notes      → produce ContentNode notes via Sarvam
      generate_questions  → produce ContentNode MCQ/QA via Sarvam
      render_page         → write page JSON to Cloudflare

    Status lifecycle: pending → running → done / failed
    """

    job_type: str
    subject_id: Optional[str] = None
    chapter_id: Optional[str] = None
    topic_id: Optional[str] = None
    document_id: Optional[str] = None
    medium: Optional[str] = None
    status: str = "pending"
    progress: int = 0
    total_chunks: int = 0
    processed_chunks: int = 0
    error_message: Optional[str] = None
    result: Optional[dict] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    class Settings:
        name = "generation_jobs"
