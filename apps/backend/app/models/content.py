"""
Content Hierarchy Models - Board > Class > Stream > Subject > Chapter > Topic
Used for the educational content management system.
"""

from datetime import datetime, timezone
from typing import Optional, Union
from uuid import uuid4

from beanie import Document, PydanticObjectId
from pydantic import BaseModel, Field

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
    subject_id: FlexId
    chapter_number: int
    status: str = "draft"
    content_en: Optional[str] = None
    content_as: Optional[str] = None
    meta_description: Optional[str] = None
    keywords: Optional[str] = None
    word_count: Optional[int] = None
    notes_generated: bool = False
    published_topics: list[Topic] = Field(default_factory=list)
    faq_jsonld: Optional[list[dict]] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "chapters"


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
