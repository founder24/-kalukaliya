"""Content hierarchy models for educational content management."""

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from beanie import Document
from pydantic import BaseModel, Field
from beanie import PydanticObjectId


class Board(Document):
    """Educational board (e.g., SEBA, CBSE)"""

    name: str
    slug: str
    status: str = "active"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "boards"


class Class(Document):
    """Class/grade level within a board"""

    name: str
    board_id: PydanticObjectId
    status: str = "active"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "classes"


class Stream(Document):
    """Stream within a class (e.g., Science, Arts)"""

    name: str
    class_id: PydanticObjectId
    status: str = "active"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "streams"


class Subject(Document):
    """Subject within a stream"""

    name: str
    stream_id: PydanticObjectId
    status: str = "active"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "subjects"


class Topic(BaseModel):
    """Embedded topic within a chapter"""

    id: str = Field(default_factory=lambda: str(uuid4()))
    title: str
    definition: Optional[str] = None
    topic_slug: str
    definition_status: str = "pending"


class Chapter(Document):
    """Chapter within a subject"""

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
