"""
Tests for the ContentRenderer service.
Validates HTML output includes required SEO elements for each page type.
"""

import pytest
from unittest.mock import patch, MagicMock
from app.models.knowledge import (
    KnowledgeObject,
    ContentBlock,
    ContentMetadata,
    GeneratedContent,
)
from app.services.content.renderer import ContentRenderer


@pytest.fixture(autouse=True)
def mock_beanie_collection():
    """Patch Beanie's motor collection to avoid CollectionWasNotInitialized."""
    with patch.object(
        KnowledgeObject, "get_motor_collection", return_value=MagicMock()
    ):
        yield


@pytest.fixture
def sample_knowledge_object():
    """Create a sample KnowledgeObject for testing."""
    return KnowledgeObject(
        slug="ahsec-hs1-physics-laws-of-motion",
        board="ahsec",
        class_level="hs-1st-year",
        subject="physics",
        chapter="laws-of-motion",
        topic="Laws of Motion",
        content=ContentBlock(
            body_markdown="# Laws of Motion\n\nNewton's laws describe the relationship between force and motion. "
            * 20,  # Ensure 300+ words
            key_concepts=[
                "Newton's First Law",
                "Newton's Second Law",
                "Newton's Third Law",
            ],
            formulas=["F = ma", "p = mv"],
            definitions=[
                {
                    "term": "Inertia",
                    "definition": "The tendency of an object to resist changes in motion",
                },
                {"term": "Force", "definition": "A push or pull on an object"},
            ],
            faq=[
                {
                    "question": "What is Newton's First Law?",
                    "answer": "An object at rest stays at rest...",
                },
            ],
            prev_year_questions=[
                {
                    "year": "2023",
                    "question": "State Newton's Second Law",
                    "answer": "F=ma",
                    "marks": 2,
                },
            ],
            learning_objectives=["Understand Newton's three laws"],
        ),
        metadata=ContentMetadata(board_name="AHSEC"),
        generated=GeneratedContent(
            mcqs=[
                {
                    "question": "What is F=ma?",
                    "options": [
                        "Newton's 2nd law",
                        "Wrong1",
                        "Wrong2",
                        "Wrong3",
                    ],
                    "correct": "a",
                    "explanation": "It defines force",
                },
            ],
            summary="Laws of motion describe how objects move.",
            important_questions=[
                {"question": "Explain Newton's laws", "marks": 5, "frequency": 3}
            ],
        ),
    )


@pytest.fixture
def renderer():
    return ContentRenderer()


class TestContentRenderer:
    def test_render_notes_includes_title(self, renderer, sample_knowledge_object):
        html = renderer.render(sample_knowledge_object, "notes")
        assert "<title>" in html
        assert "Laws of Motion" in html
        assert "Notes" in html

    def test_render_notes_includes_canonical_url(
        self, renderer, sample_knowledge_object
    ):
        html = renderer.render(sample_knowledge_object, "notes")
        assert 'rel="canonical"' in html
        assert "ahsec/hs-1st-year/physics/laws-of-motion" in html

    def test_render_notes_includes_json_ld(self, renderer, sample_knowledge_object):
        html = renderer.render(sample_knowledge_object, "notes")
        assert "application/ld+json" in html
        assert "BreadcrumbList" in html
        assert "Course" in html

    def test_render_notes_includes_og_tags(self, renderer, sample_knowledge_object):
        html = renderer.render(sample_knowledge_object, "notes")
        assert 'property="og:title"' in html
        assert 'property="og:description"' in html
        assert 'property="og:url"' in html

    def test_render_notes_includes_hreflang(self, renderer, sample_knowledge_object):
        html = renderer.render(sample_knowledge_object, "notes")
        assert 'hreflang="en"' in html
        assert 'hreflang="as"' in html

    def test_render_mcqs_includes_quiz_schema(
        self, renderer, sample_knowledge_object
    ):
        html = renderer.render(sample_knowledge_object, "mcqs")
        assert "Quiz" in html
        assert "MCQs" in html

    def test_render_definitions_includes_defined_term_set(
        self, renderer, sample_knowledge_object
    ):
        html = renderer.render(sample_knowledge_object, "definitions")
        assert "DefinedTermSet" in html
        assert "Inertia" in html

    def test_render_summary_page(self, renderer, sample_knowledge_object):
        html = renderer.render(sample_knowledge_object, "summary")
        assert "Summary" in html
        assert "Laws of motion describe" in html

    def test_render_important_questions(self, renderer, sample_knowledge_object):
        html = renderer.render(sample_knowledge_object, "important-questions")
        assert "Important Questions" in html
        assert "Newton" in html

    def test_render_includes_navigation_links(
        self, renderer, sample_knowledge_object
    ):
        html = renderer.render(sample_knowledge_object, "notes")
        # Should have links to other page types
        assert "/mcqs" in html
        assert "/summary" in html
        assert "/definitions" in html

    def test_render_includes_faq_schema_when_faq_exists(
        self, renderer, sample_knowledge_object
    ):
        html = renderer.render(sample_knowledge_object, "notes")
        assert "FAQPage" in html

    def test_all_page_types_produce_html(self, renderer, sample_knowledge_object):
        for page_type in [
            "notes",
            "mcqs",
            "summary",
            "definitions",
            "important-questions",
        ]:
            html = renderer.render(sample_knowledge_object, page_type)
            assert html.startswith("<!DOCTYPE html>") or html.startswith(
                "\n<!DOCTYPE html>"
            )
            assert "</html>" in html

    def test_render_notes_contains_unescaped_html_tags(self, renderer, sample_knowledge_object):
        html = renderer.render(sample_knowledge_object, "notes")
        # Verify HTML tags are NOT escaped (no &lt; or &gt;)
        assert "&lt;section" not in html
        assert "<section" in html

    def test_render_json_ld_not_escaped(self, renderer, sample_knowledge_object):
        html = renderer.render(sample_knowledge_object, "notes")
        # JSON-LD should contain actual JSON, not escaped entities
        assert "&quot;" not in html.split("application/ld+json")[1].split("</script>")[0]
