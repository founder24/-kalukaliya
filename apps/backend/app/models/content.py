"""
Content Hierarchy Models - Board > Class > Stream > Subject > Chapter > Topic
Used for the educational content management system.
"""

from datetime import datetime, timezone
from typing import Optional, Union
from uuid import uuid4

from beanie import Document, PydanticObjectId
from pydantic import BaseModel, Field
from pymongo import ASCENDING, IndexModel

# Flexible ID type: accepts MongoDB ObjectIds AND legacy short/UUID string IDs
# stored in the DB (e.g. 'b1', 's13', '0bd48cd1-3912-47f8-...')
FlexId = Union[PydanticObjectId, str]


class Board(Document):
    name: str
    slug: str
    status: str = "active"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "boards"


class Class(Document):
    name: str
    board_id: FlexId
    status: str = "active"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "classes"


class Stream(Document):
    name: str
    class_id: FlexId
    status: str = "active"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "streams"


class Subject(Document):
    name: str
    name_as: Optional[str] = None           # Assamese name
    stream_id: Optional[FlexId] = None
    status: str = "active"
    slug: Optional[str] = None
    description: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    icon: Optional[str] = None
    gradient: Optional[str] = None
    thumbnail_url: Optional[str] = None
    has_document: bool = False
    seo_stats: Optional[dict] = None
    # Subject-level question papers: [{id, name, class_name, year, description, pages:[{id,url}]}]
    pyq_papers: list[dict] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "subjects"


class Topic(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    title: str
    definition: Optional[str] = None
    topic_slug: str
    definition_status: str = "pending"
    wikidata_uri: Optional[str] = None


class Chapter(Document):
    title: str
    title_as: Optional[str] = None
    slug: str
    # Assamese URL slug — read via getattr in the AS chapter resolver; must be
    # declared so Beanie (Pydantic v2) does not silently drop it on load.
    slug_as: Optional[str] = None
    subject_id: FlexId
    chapter_number: int
    status: str = "draft"
    # Section discriminator — values: 'notes' | 'qa' | 'question_paper' | 'formula' | 'summary' | 'solution' | 'reference'
    content_type: Optional[str] = "notes"
    content_en: Optional[str] = None           # user-facing: HTML/Markdown notes, summaries
    content_as: Optional[str] = None           # user-facing: Assamese notes
    rag_text_en: Optional[str] = None          # retrieval-only: full plain-text from book PDF
    rag_text_as: Optional[str] = None          # retrieval-only: Assamese plain-text
    notes_en: Optional[str] = None             # structured study notes (Markdown), English
    notes_as: Optional[str] = None             # structured study notes (Markdown), Assamese
    # Q&A section — student-facing questions & answers (Markdown)
    qa_text_en: Optional[str] = None           # user-facing: Q&A content, English
    qa_text_as: Optional[str] = None           # user-facing: Q&A content, Assamese
    # Q&A section — retrieval-ready Q&A (plain text, expanded)
    qa_rag_text_en: Optional[str] = None       # retrieval-only: expanded Q&A, English
    qa_rag_text_as: Optional[str] = None       # retrieval-only: expanded Q&A, Assamese
    source_pdf_url: Optional[str] = None       # URL of the PDF this chapter's content was ingested from
    pyq_pdf_url: Optional[str] = None          # URL to PYQ PDF/image (user-facing, language-agnostic) — legacy single-upload
    pyq_papers: list[dict] = Field(default_factory=list)  # [{id, title, year, url, uploaded_at}] — multi-upload PYQ papers
    pyq_rag_text: Optional[str] = None         # PYQ RAG plain text, English (staff-entered, retrieval-only)
    pyq_rag_text_as: Optional[str] = None      # PYQ RAG plain text, Assamese (staff-entered, retrieval-only)
    # Structured RAG section fields — dual-layer editor (Task #5)
    rag_sections_en: list[dict] = Field(default_factory=list)     # [{title, content}] — Notes RAG, English
    rag_sections_as: list[dict] = Field(default_factory=list)     # [{title, content}] — Notes RAG, Assamese
    qa_rag_sections_en: list[dict] = Field(default_factory=list)  # [{section, question, answer, solution}] — Q&A RAG, English
    qa_rag_sections_as: list[dict] = Field(default_factory=list)  # [{section, question, answer, solution}] — Q&A RAG, Assamese
    meta_description: Optional[str] = None
    meta_description_as: Optional[str] = None  # Assamese meta description
    keywords: Optional[str] = None
    word_count: Optional[int] = None
    notes_generated: bool = False              # legacy flag — kept for backward compat
    published_topics: list[Topic] = Field(default_factory=list)
    faq_jsonld: Optional[list[dict]] = None
    # Sync lifecycle timestamps — set by their respective operations
    content_saved_at: Optional[datetime] = None   # stamped on every student-content PATCH save
    rag_updated_at: Optional[datetime] = None     # stamped when rag_text_en/as changes
    rag_indexed_at: Optional[datetime] = None     # stamped after successful Vectorize reindex
    # Per-section RAG timestamps — dual-layer editor
    notes_rag_updated_at: Optional[datetime] = None   # stamped when rag_sections_en/as are saved
    notes_rag_indexed_at: Optional[datetime] = None   # stamped after Notes sections reindex
    qa_rag_updated_at: Optional[datetime] = None      # stamped when qa_rag_sections_en/as are saved
    qa_rag_indexed_at: Optional[datetime] = None      # stamped after Q&A sections reindex
    pyq_rag_updated_at: Optional[datetime] = None     # stamped when pyq_rag_text is saved
    pyq_rag_indexed_at: Optional[datetime] = None     # stamped after PYQ reindex
    published_at: Optional[datetime] = None       # stamped when chapter is published
    version: int = 0                              # optimistic locking counter
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "chapters"
        indexes = [
            # Partial unique index on (subject_id, slug_as) — only enforced for
            # non-null slug_as values so existing chapters with slug_as=null are
            # unaffected.  Prevents concurrent Assamese seed workers from
            # persisting the same slug_as for two different chapters in the same
            # subject; the second write gets a DuplicateKeyError and the caller
            # retries with a numeric-suffix candidate.
            IndexModel(
                [("subject_id", ASCENDING), ("slug_as", ASCENDING)],
                unique=True,
                partialFilterExpression={"slug_as": {"$type": "string"}},
                name="chapters_subject_slug_as_unique",
            ),
        ]


class ContentAuditLog(Document):
    """Lightweight audit trail for chapter CMS operations.

    action: "created" | "updated" | "deleted" | "rag_updated"
    actor_id: the admin JWT `sub` (user ObjectId string)
    actor_email: resolved email at write time for display
    changes: dict summarising what was different (field-level diff summary)
    """

    chapter_id: str
    subject_id: Optional[str] = None
    action: str
    actor_id: str
    actor_email: Optional[str] = None
    version_before: Optional[int] = None
    version_after: Optional[int] = None
    changes: Optional[dict] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "content_audit_log"


class TopicEmbedding(Document):
    """Stores pre-computed embeddings for topic titles (CF bge-m3, 1024 dims).

    Used for fast cosine-similarity matching in the chat pipeline to decide
    whether a user query is related to any published topic before invoking RAG.
    Embedding model: @cf/baai/bge-m3 via Cloudflare Workers AI (multilingual EN+AS).
    """

    topic_id: str
    topic_title: str
    chapter_id: FlexId
    chapter_title: str
    subject_slug: str
    board_slug: str
    class_level: str
    embedding: list[float] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "topic_embeddings"
        indexes = [[("topic_id", 1)]]


class QuestionPaper(Document):
    title: str
    slug: str
    r2_key: str
    board: str
    class_level: str
    subject: str
    year: Optional[int] = None
    status: str = "published"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "question_papers"
