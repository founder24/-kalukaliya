"""
Content Hierarchy Models - Board > Class > Stream > Subject > Chapter > Topic
Used for the educational content management system.
"""

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from beanie import Document, PydanticObjectId
from pydantic import BaseModel, Field


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
    board_id: PydanticObjectId
    status: str = "active"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "classes"


class Stream(Document):
    name: str
    class_id: PydanticObjectId
    status: str = "active"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "streams"


class Subject(Document):
    name: str
    stream_id: PydanticObjectId
    status: str = "active"
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
    wikidata_uri: Optional[str] = None  # Auto-resolved at publish time


class Chapter(Document):
    title: str
    slug: str
    subject_id: PydanticObjectId
    chapter_number: int
    status: str = "draft"
    content_en: Optional[str] = None
    content_as: Optional[str] = None
    meta_description: Optional[str] = None
    keywords: Optional[str] = None
    word_count: Optional[int] = None
    published_topics: list[Topic] = Field(default_factory=list)
    faq_jsonld: Optional[list[dict]] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "chapters"


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
