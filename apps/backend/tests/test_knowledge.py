"""
Tests for the canonical knowledge architecture:
- KnowledgeObject model creation
- Content renderer (notes, mcqs page types)
- Content API endpoints (mocked MongoDB)
- Sitemap endpoints
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.fixture
def sample_knowledge_data():
    """Sample knowledge object data for testing."""
    return {
        "slug": "seba-10-science-chemical-reactions",
        "title": "Chemical Reactions and Equations",
        "description": "Study of chemical reactions for Class 10 Science",
        "body_markdown": "# Chemical Reactions\n\nA chemical reaction is a process.\n\n## Types of Reactions\n\nThere are several types.",
        "metadata": {
            "board": "SEBA",
            "class_level": "10",
            "subject": "science",
            "chapter": "chemical-reactions",
            "chapter_number": 1,
            "difficulty": "medium",
            "language": "en",
            "estimated_read_time_minutes": 10,
            "keywords": ["chemical reactions", "equations", "science"],
        },
        "generated": {
            "mcqs": [
                {
                    "question": "What is a chemical reaction?",
                    "options": [
                        "A physical change",
                        "A chemical change",
                        "No change",
                        "A nuclear change",
                    ],
                    "answer": "A chemical change",
                },
                {
                    "question": "Which is an example of a combination reaction?",
                    "options": [
                        "Burning of magnesium",
                        "Electrolysis of water",
                        "Rusting",
                        "Digestion",
                    ],
                    "answer": "Burning of magnesium",
                },
            ],
            "summary": "Chemical reactions involve the transformation of reactants into products. This chapter covers types of reactions including combination, decomposition, displacement, and redox reactions.",
            "definitions": [
                {
                    "term": "Chemical Reaction",
                    "definition": "A process in which one or more substances are converted into new substances.",
                },
                {
                    "term": "Reactant",
                    "definition": "A substance that takes part in a chemical reaction.",
                },
            ],
            "important_questions": [
                {
                    "question": "Explain the types of chemical reactions with examples.",
                    "answer": "The main types are combination, decomposition, displacement, double displacement, and redox reactions.",
                    "marks": 5,
                },
            ],
        },
        "status": "published",
    }


@pytest.fixture
def mock_knowledge_obj(sample_knowledge_data):
    """Create a mock object that behaves like a KnowledgeObject without Beanie init."""
    from app.models.knowledge import ContentMetadata, GeneratedContent

    obj = MagicMock()
    obj.slug = sample_knowledge_data["slug"]
    obj.title = sample_knowledge_data["title"]
    obj.description = sample_knowledge_data["description"]
    obj.body_markdown = sample_knowledge_data["body_markdown"]
    obj.metadata = ContentMetadata(**sample_knowledge_data["metadata"])
    obj.generated = GeneratedContent(**sample_knowledge_data["generated"])
    obj.status = sample_knowledge_data["status"]
    obj.rendered_html = {}
    obj.page_views = 0
    return obj


class TestKnowledgeObjectModel:
    """Tests for the KnowledgeObject model schemas (sub-models, no Beanie required)."""

    def test_content_metadata_creation(self, sample_knowledge_data):
        """Test that ContentMetadata can be instantiated with valid data."""
        from app.models.knowledge import ContentMetadata

        meta = ContentMetadata(**sample_knowledge_data["metadata"])
        assert meta.board == "SEBA"
        assert meta.class_level == "10"
        assert meta.subject == "science"
        assert meta.chapter == "chemical-reactions"
        assert meta.difficulty == "medium"
        assert meta.language == "en"
        assert meta.chapter_number == 1
        assert len(meta.keywords) == 3

    def test_content_metadata_defaults(self):
        """Test ContentMetadata default values."""
        from app.models.knowledge import ContentMetadata

        meta = ContentMetadata()
        assert meta.board == ""
        assert meta.difficulty == "medium"
        assert meta.language == "en"
        assert meta.estimated_read_time_minutes == 5

    def test_generated_content(self, sample_knowledge_data):
        """Test GeneratedContent model instantiation."""
        from app.models.knowledge import GeneratedContent

        gen = GeneratedContent(**sample_knowledge_data["generated"])
        assert len(gen.mcqs) == 2
        assert len(gen.definitions) == 2
        assert len(gen.important_questions) == 1
        assert "Chemical reactions" in gen.summary

    def test_derivative_hashes(self):
        """Test DerivativeHashes model."""
        from app.models.knowledge import DerivativeHashes

        hashes = DerivativeHashes(
            notes_html="abc123",
            mcqs_html="def456",
        )
        assert hashes.notes_html == "abc123"
        assert hashes.summary_html is None

    def test_content_block(self):
        """Test ContentBlock model."""
        from app.models.knowledge import ContentBlock

        block = ContentBlock(block_type="heading", content="Introduction", order=0)
        assert block.block_type == "heading"
        assert block.content == "Introduction"
        assert block.language == "en"

    def test_knowledge_object_class_exists(self):
        """Test that KnowledgeObject class is properly defined."""
        from app.models.knowledge import KnowledgeObject

        assert KnowledgeObject.Settings.name == "knowledge_objects"
        assert len(KnowledgeObject.Settings.indexes) == 4


class TestContentRenderer:
    """Tests for the ContentRenderer."""

    def test_render_notes(self, mock_knowledge_obj):
        """Test rendering notes page type."""
        from app.services.content.renderer import content_renderer

        html = content_renderer.render(mock_knowledge_obj, "notes")
        assert "<!DOCTYPE html>" in html
        assert "Chemical Reactions" in html
        assert 'rel="canonical"' in html
        assert "application/ld+json" in html
        assert "BreadcrumbList" in html
        assert "Course" in html
        assert 'aria-current="page"' in html

    def test_render_mcqs(self, mock_knowledge_obj):
        """Test rendering mcqs page type."""
        from app.services.content.renderer import content_renderer

        html = content_renderer.render(mock_knowledge_obj, "mcqs")
        assert "<!DOCTYPE html>" in html
        assert "Quiz" in html
        assert "What is a chemical reaction?" in html
        assert "A chemical change" in html
        assert '<ol type="A">' in html

    def test_render_summary(self, mock_knowledge_obj):
        """Test rendering summary page type."""
        from app.services.content.renderer import content_renderer

        html = content_renderer.render(mock_knowledge_obj, "summary")
        assert "Chemical reactions involve" in html

    def test_render_definitions(self, mock_knowledge_obj):
        """Test rendering definitions page type."""
        from app.services.content.renderer import content_renderer

        html = content_renderer.render(mock_knowledge_obj, "definitions")
        assert "DefinedTermSet" in html
        assert "Chemical Reaction" in html
        assert "<dl" in html
        assert "<dt>" in html
        assert "<dd>" in html

    def test_render_important_questions(self, mock_knowledge_obj):
        """Test rendering important-questions page type."""
        from app.services.content.renderer import content_renderer

        html = content_renderer.render(mock_knowledge_obj, "important-questions")
        assert "FAQPage" in html
        assert "Explain the types of chemical reactions" in html
        assert "[5 marks]" in html

    def test_render_hreflang(self, mock_knowledge_obj):
        """Test that hreflang links are present."""
        from app.services.content.renderer import content_renderer

        html = content_renderer.render(mock_knowledge_obj, "notes")
        assert 'hreflang="en"' in html
        assert 'hreflang="as"' in html

    def test_render_page_nav(self, mock_knowledge_obj):
        """Test that page type navigation is rendered."""
        from app.services.content.renderer import content_renderer

        html = content_renderer.render(mock_knowledge_obj, "notes")
        assert "page-type-nav" in html
        assert "/notes" in html
        assert "/mcqs" in html
        assert "/summary" in html
        assert "/definitions" in html
        assert "/important-questions" in html

    def test_compute_hash(self):
        """Test hash computation for change detection."""
        from app.services.content.renderer import content_renderer

        hash1 = content_renderer.compute_hash("<p>Hello</p>")
        hash2 = content_renderer.compute_hash("<p>Hello</p>")
        hash3 = content_renderer.compute_hash("<p>World</p>")

        assert hash1 == hash2
        assert hash1 != hash3
        assert len(hash1) == 64  # SHA256 hex length


class TestSearchIndexer:
    """Tests for the SearchIndexer chunking logic."""

    def test_chunk_text_basic(self):
        """Test basic text chunking."""
        from app.services.content.search_indexer import SearchIndexer

        indexer = SearchIndexer()
        text = "Paragraph one.\n\nParagraph two.\n\nParagraph three."
        chunks = indexer.chunk_text(text, chunk_size=500)
        # All fits in one chunk at 500 tokens
        assert len(chunks) == 1
        assert "Paragraph one" in chunks[0]

    def test_chunk_text_empty(self):
        """Test chunking empty text."""
        from app.services.content.search_indexer import SearchIndexer

        indexer = SearchIndexer()
        chunks = indexer.chunk_text("")
        assert chunks == []

    def test_chunk_text_splits_large(self):
        """Test that large text gets split into multiple chunks."""
        from app.services.content.search_indexer import SearchIndexer

        indexer = SearchIndexer()
        # Create text larger than chunk_size tokens
        paragraphs = [f"Paragraph {i} with some content." for i in range(200)]
        text = "\n\n".join(paragraphs)
        chunks = indexer.chunk_text(text, chunk_size=50)  # small chunk size
        assert len(chunks) > 1

    def test_chunk_preserves_content(self):
        """Test that all content is preserved after chunking."""
        from app.services.content.search_indexer import SearchIndexer

        indexer = SearchIndexer()
        text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
        chunks = indexer.chunk_text(text, chunk_size=500)
        joined = " ".join(chunks)
        assert "First paragraph" in joined
        assert "Second paragraph" in joined
        assert "Third paragraph" in joined


@pytest.mark.anyio
class TestContentAPI:
    """Tests for public content API endpoints."""

    async def test_render_chapter_not_found(self, client):
        """Test render endpoint returns 404 when content not found."""
        with patch(
            "app.api.v1.content.KnowledgeObject.find_one",
            new_callable=AsyncMock,
            return_value=None,
        ):
            response = await client.get(
                "/api/v1/content/render/SEBA/10/science/chemical-reactions"
            )
            assert response.status_code == 404

    async def test_render_chapter_page_type_invalid(self, client):
        """Test render endpoint returns 400 for invalid page type."""
        response = await client.get(
            "/api/v1/content/render/SEBA/10/science/chemical-reactions/invalid-type"
        )
        assert response.status_code == 400

    async def test_get_by_slug_not_found(self, client):
        """Test get by slug returns 404 when not found."""
        with patch(
            "app.api.v1.content.KnowledgeObject.find_one",
            new_callable=AsyncMock,
            return_value=None,
        ):
            response = await client.get(
                "/api/v1/content/seba-10-science-chemical-reactions"
            )
            assert response.status_code == 404


@pytest.mark.anyio
class TestSitemapEndpoints:
    """Tests for SEO sitemap endpoints."""

    async def test_sitemap_index(self, client):
        """Test sitemap index returns valid XML."""
        response = await client.get("/api/v1/seo/sitemap.xml")
        assert response.status_code == 200
        assert "application/xml" in response.headers["content-type"]
        assert "sitemapindex" in response.text
        assert "sitemap-static.xml" in response.text

    async def test_sitemap_static(self, client):
        """Test static sitemap returns valid XML."""
        response = await client.get("/api/v1/seo/sitemap-static.xml")
        assert response.status_code == 200
        assert "application/xml" in response.headers["content-type"]
        assert "https://syrabit.ai/" in response.text
        assert "urlset" in response.text

    async def test_sitemap_subjects_fallback(self, client):
        """Test subjects sitemap falls back gracefully when DB is unavailable."""
        with patch(
            "app.api.v1.seo.KnowledgeObject.aggregate",
            side_effect=Exception("DB not available"),
        ):
            response = await client.get("/api/v1/seo/sitemap-subjects.xml")
            assert response.status_code == 200
            assert "urlset" in response.text

    async def test_sitemap_chapters_fallback(self, client):
        """Test chapters sitemap falls back gracefully when DB is unavailable."""
        with patch(
            "app.api.v1.seo.KnowledgeObject.find",
            side_effect=Exception("DB not available"),
        ):
            response = await client.get("/api/v1/seo/sitemap-chapters.xml")
            assert response.status_code == 200
            assert "urlset" in response.text
