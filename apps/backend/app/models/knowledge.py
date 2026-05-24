from beanie import Document
from pydantic import BaseModel, Field
from pymongo import IndexModel
from datetime import datetime
from typing import Optional


class ContentBlock(BaseModel):
    body_markdown: str
    key_concepts: list[str] = []
    formulas: list[str] = []
    definitions: list[dict] = []  # [{term, definition}]
    faq: list[dict] = []  # [{question, answer}]
    prev_year_questions: list[dict] = []  # [{year, question, answer, marks}]
    learning_objectives: list[str] = []
    exam_weightage: float | None = None


class ContentMetadata(BaseModel):
    language: str = "en"
    difficulty: int = 3
    estimated_read_time_min: int = 5
    syllabus_year: str = "2025-26"
    board_name: str = ""
    last_updated: datetime = Field(default_factory=datetime.utcnow)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class DerivativeHashes(BaseModel):
    html_hash: str = ""
    search_hash: str = ""
    mcq_hash: str = ""
    summary_hash: str = ""


class GeneratedContent(BaseModel):
    mcqs: list[dict] = []  # [{question, options: [a,b,c,d], correct, explanation}]
    summary: str = ""
    definitions_rendered: list[dict] = []  # [{term, definition, example}]
    important_questions: list[dict] = []  # [{question, marks, frequency}]
    tutoring_context: str = ""


class KnowledgeObject(Document):
    slug: str
    board: str  # "ahsec", "gauhati_university"
    class_level: str  # "hs-1st-year", "hs-2nd-year", "2nd-sem", "4th-sem"
    subject: str  # "physics", "chemistry", etc.
    chapter: str  # "laws-of-motion"
    topic: str  # Human-readable: "Laws of Motion"
    content: ContentBlock
    metadata: ContentMetadata
    derivatives: DerivativeHashes = DerivativeHashes()
    generated: GeneratedContent = GeneratedContent()
    is_published: bool = True
    page_views: int = 0

    class Settings:
        name = "knowledge_objects"
        indexes = [
            IndexModel(
                [("board", 1), ("class_level", 1), ("subject", 1), ("chapter", 1)]
            ),
            IndexModel([("slug", 1)], unique=True),
            IndexModel([("topic", "text"), ("content.body_markdown", "text")]),
        ]
