"""
Tests for the KnowledgeObject model and sub-models.
"""

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime
from app.models.knowledge import (
    KnowledgeObject,
    ContentBlock,
    ContentMetadata,
    DerivativeHashes,
    GeneratedContent,
)


@pytest.fixture(autouse=True)
def mock_beanie_collection():
    """Patch Beanie's motor collection to avoid CollectionWasNotInitialized."""
    with patch.object(
        KnowledgeObject, "get_motor_collection", return_value=MagicMock()
    ):
        yield


class TestContentBlock:
    def test_defaults(self):
        block = ContentBlock(body_markdown="Test content")
        assert block.body_markdown == "Test content"
        assert block.key_concepts == []
        assert block.formulas == []
        assert block.definitions == []
        assert block.faq == []
        assert block.prev_year_questions == []
        assert block.learning_objectives == []
        assert block.exam_weightage is None

    def test_full_content(self):
        block = ContentBlock(
            body_markdown="# Test\n\nContent here",
            key_concepts=["Concept A", "Concept B"],
            formulas=["F=ma", "E=mc^2"],
            definitions=[{"term": "Force", "definition": "A push or pull"}],
            faq=[{"question": "What is force?", "answer": "A push or pull"}],
            prev_year_questions=[
                {
                    "year": "2023",
                    "question": "Define force",
                    "answer": "...",
                    "marks": 2,
                }
            ],
            learning_objectives=["Understand Newton's laws"],
            exam_weightage=15.0,
        )
        assert len(block.key_concepts) == 2
        assert block.exam_weightage == 15.0


class TestContentMetadata:
    def test_defaults(self):
        meta = ContentMetadata()
        assert meta.language == "en"
        assert meta.difficulty == 3
        assert meta.estimated_read_time_min == 5
        assert meta.syllabus_year == "2025-26"
        assert meta.board_name == ""

    def test_custom_values(self):
        meta = ContentMetadata(
            language="as",
            difficulty=5,
            estimated_read_time_min=10,
            syllabus_year="2024-25",
            board_name="AHSEC",
        )
        assert meta.language == "as"
        assert meta.difficulty == 5
        assert meta.board_name == "AHSEC"


class TestDerivativeHashes:
    def test_defaults(self):
        hashes = DerivativeHashes()
        assert hashes.html_hash == ""
        assert hashes.search_hash == ""
        assert hashes.mcq_hash == ""
        assert hashes.summary_hash == ""


class TestGeneratedContent:
    def test_defaults(self):
        gen = GeneratedContent()
        assert gen.mcqs == []
        assert gen.summary == ""
        assert gen.definitions_rendered == []
        assert gen.important_questions == []
        assert gen.tutoring_context == ""


class TestKnowledgeObject:
    def test_settings(self):
        assert KnowledgeObject.Settings.name == "knowledge_objects"

    def test_field_defaults(self):
        # Test that defaults work for optional fields
        ko = KnowledgeObject(
            slug="test-slug",
            board="ahsec",
            class_level="hs-1st-year",
            subject="physics",
            chapter="laws-of-motion",
            topic="Laws of Motion",
            content=ContentBlock(
                body_markdown="Test content about laws of motion..."
            ),
            metadata=ContentMetadata(),
        )
        assert ko.is_published is True
        assert ko.page_views == 0
        assert ko.derivatives.html_hash == ""
        assert ko.generated.mcqs == []

    def test_all_fields(self):
        ko = KnowledgeObject(
            slug="ahsec-hs1-physics-motion",
            board="ahsec",
            class_level="hs-1st-year",
            subject="physics",
            chapter="laws-of-motion",
            topic="Laws of Motion",
            content=ContentBlock(
                body_markdown="Content here",
                key_concepts=["Inertia", "Force"],
            ),
            metadata=ContentMetadata(board_name="AHSEC"),
            derivatives=DerivativeHashes(html_hash="abc123"),
            generated=GeneratedContent(summary="A summary"),
            is_published=False,
            page_views=42,
        )
        assert ko.slug == "ahsec-hs1-physics-motion"
        assert ko.board == "ahsec"
        assert ko.is_published is False
        assert ko.page_views == 42
        assert ko.derivatives.html_hash == "abc123"
        assert ko.generated.summary == "A summary"
        assert len(ko.content.key_concepts) == 2
