"""
TopicHub - Authority Layer + Knowledge Graph Layer for educational content.
Transforms topics from isolated definitions into interconnected knowledge hubs.
"""

from datetime import datetime, timezone
from typing import Optional

from beanie import Document, PydanticObjectId
from pydantic import BaseModel, Field


class TopicSource(BaseModel):
    """A linked authoritative source for a topic."""

    source_type: str  # "ncert", "ahsec_syllabus", "official", "pyq", "reference"
    title: str
    url: Optional[str] = None
    year: Optional[int] = None
    description: Optional[str] = None


class TopicMCQ(BaseModel):
    """A multiple-choice question linked to a topic."""

    question: str
    options: list[str]  # 4 options
    correct_index: int  # 0-3
    explanation: Optional[str] = None
    source: Optional[str] = None  # "PYQ 2023", "NCERT Exercise", etc.
    difficulty: str = "medium"  # easy, medium, hard


class TopicPYQ(BaseModel):
    """A Previous Year Question linked to a topic."""

    question: str
    year: int
    board: str  # "AHSEC", "SEBA"
    marks: Optional[int] = None
    answer_hint: Optional[str] = None
    solution: Optional[str] = None


class TopicRelation(BaseModel):
    """A semantic relationship between topics in the Knowledge Graph."""

    related_topic_slug: str
    relation_type: str  # "prerequisite", "builds_on", "related", "contrasts", "part_of", "leads_to"
    strength: float = 0.5  # 0.0-1.0 relationship strength
    description: Optional[str] = None


class TopicHub(Document):
    """
    The Authority Layer - makes each topic a rich knowledge hub.
    Links a topic to official sources, PYQs, MCQs, solutions, and related topics.
    """

    topic_slug: str
    chapter_id: PydanticObjectId
    subject_id: PydanticObjectId

    # Identity
    title: str
    definition: str
    definition_extended: Optional[str] = None
    wikidata_uri: Optional[str] = None

    # Authority sources
    sources: list[TopicSource] = Field(default_factory=list)

    # Assessment content
    mcqs: list[TopicMCQ] = Field(default_factory=list)
    pyqs: list[TopicPYQ] = Field(default_factory=list)

    # Knowledge Graph edges
    relations: list[TopicRelation] = Field(default_factory=list)

    # Content variants
    summary: Optional[str] = None
    key_points: list[str] = Field(default_factory=list)
    formula: Optional[str] = None
    diagram_url: Optional[str] = None
    diagram_alt: Optional[str] = None

    # Metadata
    difficulty_level: str = "medium"
    bloom_taxonomy: Optional[str] = None
    study_time_minutes: Optional[int] = None
    importance: str = "medium"

    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "topic_hubs"
        indexes = [
            "topic_slug",
            "chapter_id",
            "subject_id",
            [("topic_slug", 1), ("chapter_id", 1)],
        ]
