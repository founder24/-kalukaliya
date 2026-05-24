"""
Tests for the ContentTranslator service and admin translation endpoints.
"""

import os

os.environ.setdefault("JWT_SECRET", "test-secret-at-least-32-chars-long-for-testing")
os.environ.setdefault("ALLOWED_ORIGINS", "http://test,https://syrabit.ai")
os.environ.setdefault("APP_ENV", "test")

import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.fixture
def sample_knowledge_obj():
    """Create a mock KnowledgeObject for translation testing."""
    from app.models.knowledge import ContentMetadata, GeneratedContent

    obj = MagicMock()
    obj.slug = "seba-10-science-chemical-reactions"
    obj.title = "Chemical Reactions and Equations"
    obj.description = "Study of chemical reactions for Class 10 Science"
    obj.body_markdown = "# Chemical Reactions\n\nA chemical reaction is a process."
    obj.metadata = ContentMetadata(
        board="SEBA",
        class_level="10",
        subject="science",
        chapter="chemical-reactions",
        chapter_number=1,
        difficulty="medium",
        language="en",
        estimated_read_time_minutes=10,
        keywords=["chemical reactions", "equations"],
    )
    obj.generated = GeneratedContent(
        mcqs=[
            {
                "question": "What is a chemical reaction?",
                "options": [
                    "A physical change",
                    "A chemical change",
                    "No change",
                    "A nuclear change",
                ],
                "answer": "A chemical change",
                "explanation": "A chemical reaction involves chemical change.",
            }
        ],
        summary="Chemical reactions involve transformation of substances.",
        definitions=[
            {
                "term": "Chemical Reaction",
                "definition": "A process where substances are converted.",
            }
        ],
        important_questions=[
            {
                "question": "Explain types of chemical reactions.",
                "answer": "Combination, decomposition, displacement.",
                "marks": 5,
            }
        ],
    )
    obj.status = "published"
    obj.published_at = None
    obj.content_blocks = []
    return obj


class TestTranslateText:
    """Tests for the translate_text method."""

    @pytest.mark.anyio
    async def test_translate_text_returns_translated(self):
        """Test that translate_text calls sarvam and returns result."""
        from app.services.content.translator import ContentTranslator

        translator = ContentTranslator()
        fake_response = "\u09f0\u09be\u09b8\u09be\u09af\u09bc\u09a8\u09bf\u0995 \u09ac\u09bf\u0995\u09cd\u09f0\u09bf\u09af\u09bc\u09be"

        with patch(
            "app.services.content.translator.sarvam_client.generate",
            new_callable=AsyncMock,
            return_value=fake_response,
        ) as mock_generate:
            result = await translator.translate_text("Chemical Reactions")
            assert result == fake_response
            mock_generate.assert_called_once()
            # Check system prompt is passed
            call_args = mock_generate.call_args
            assert "Assamese" in call_args[0][0]

    @pytest.mark.anyio
    async def test_translate_text_empty_returns_empty(self):
        """Test that empty text returns unchanged."""
        from app.services.content.translator import ContentTranslator

        translator = ContentTranslator()
        result = await translator.translate_text("")
        assert result == ""

    @pytest.mark.anyio
    async def test_translate_text_with_context(self):
        """Test that context is included in the user message."""
        from app.services.content.translator import ContentTranslator

        translator = ContentTranslator()

        with patch(
            "app.services.content.translator.sarvam_client.generate",
            new_callable=AsyncMock,
            return_value="translated",
        ) as mock_generate:
            await translator.translate_text("Hello", context="title")
            call_args = mock_generate.call_args
            assert "title" in call_args[0][1]

    @pytest.mark.anyio
    async def test_translate_text_chunks_long_content(self):
        """Test that long text is chunked and translated in parts."""
        from app.services.content.translator import ContentTranslator

        translator = ContentTranslator()
        # Create text with more than 800 words
        long_text = "\n\n".join([f"Paragraph {i} " + "word " * 100 for i in range(10)])

        with patch(
            "app.services.content.translator.sarvam_client.generate",
            new_callable=AsyncMock,
            return_value="chunk_translated",
        ) as mock_generate:
            result = await translator.translate_text(long_text)
            # Should be called multiple times for chunks
            assert mock_generate.call_count > 1
            assert "chunk_translated" in result


class TestTranslateKnowledgeObject:
    """Tests for translate_knowledge_object method."""

    @pytest.mark.anyio
    async def test_creates_object_with_correct_slug(self, sample_knowledge_obj):
        """Test that translated object has -as slug suffix."""
        from app.services.content.translator import ContentTranslator

        translator = ContentTranslator()

        with (
            patch(
                "app.services.content.translator.sarvam_client.generate",
                new_callable=AsyncMock,
                return_value="translated_text",
            ),
            patch(
                "app.services.content.translator.KnowledgeObject",
            ) as MockKO,
        ):
            mock_instance = MagicMock()
            MockKO.return_value = mock_instance

            result = await translator.translate_knowledge_object(sample_knowledge_obj)
            # Check the constructor was called with slug ending in -as
            call_kwargs = MockKO.call_args[1]
            assert call_kwargs["slug"] == "seba-10-science-chemical-reactions-as"

    @pytest.mark.anyio
    async def test_sets_language_to_as(self, sample_knowledge_obj):
        """Test that translated object has language set to 'as'."""
        from app.services.content.translator import ContentTranslator

        translator = ContentTranslator()

        with (
            patch(
                "app.services.content.translator.sarvam_client.generate",
                new_callable=AsyncMock,
                return_value="translated_text",
            ),
            patch(
                "app.services.content.translator.KnowledgeObject",
            ) as MockKO,
        ):
            mock_instance = MagicMock()
            MockKO.return_value = mock_instance

            await translator.translate_knowledge_object(sample_knowledge_obj)
            call_kwargs = MockKO.call_args[1]
            assert call_kwargs["metadata"].language == "as"

    @pytest.mark.anyio
    async def test_preserves_metadata_fields(self, sample_knowledge_obj):
        """Test that non-translatable metadata fields are preserved."""
        from app.services.content.translator import ContentTranslator

        translator = ContentTranslator()

        with (
            patch(
                "app.services.content.translator.sarvam_client.generate",
                new_callable=AsyncMock,
                return_value="translated_text",
            ),
            patch(
                "app.services.content.translator.KnowledgeObject",
            ) as MockKO,
        ):
            mock_instance = MagicMock()
            MockKO.return_value = mock_instance

            await translator.translate_knowledge_object(sample_knowledge_obj)
            call_kwargs = MockKO.call_args[1]
            assert call_kwargs["metadata"].board == "SEBA"
            assert call_kwargs["metadata"].class_level == "10"
            assert call_kwargs["metadata"].subject == "science"
            assert call_kwargs["metadata"].chapter == "chemical-reactions"
            assert call_kwargs["metadata"].chapter_number == 1
            assert call_kwargs["status"] == "published"

    @pytest.mark.anyio
    async def test_translates_generated_content(self, sample_knowledge_obj):
        """Test that MCQs, definitions, and questions are translated."""
        from app.services.content.translator import ContentTranslator

        translator = ContentTranslator()

        with (
            patch(
                "app.services.content.translator.sarvam_client.generate",
                new_callable=AsyncMock,
                return_value="translated_text",
            ),
            patch(
                "app.services.content.translator.KnowledgeObject",
            ) as MockKO,
        ):
            mock_instance = MagicMock()
            MockKO.return_value = mock_instance

            await translator.translate_knowledge_object(sample_knowledge_obj)
            call_kwargs = MockKO.call_args[1]
            generated = call_kwargs["generated"]

            # MCQs translated
            assert len(generated.mcqs) == 1
            assert generated.mcqs[0]["question"] == "translated_text"
            assert generated.mcqs[0]["options"] == ["translated_text"] * 4
            assert generated.mcqs[0]["explanation"] == "translated_text"
            # Definitions translated
            assert len(generated.definitions) == 1
            assert generated.definitions[0]["term"] == "translated_text"
            assert generated.definitions[0]["definition"] == "translated_text"
            # Important questions translated
            assert len(generated.important_questions) == 1
            assert generated.important_questions[0]["question"] == "translated_text"
            # Summary translated
            assert generated.summary == "translated_text"


@pytest.mark.anyio
class TestAdminTranslateEndpoints:
    """Tests for admin translation API endpoints."""

    async def test_bulk_translate_returns_started(self, client):
        """Test bulk translate endpoint returns started status."""
        with (
            patch(
                "app.api.v1.admin._validate_admin_session",
                return_value={"sub": "admin-id", "type": "admin", "role": "admin"},
            ),
            patch(
                "app.api.v1.admin_translate._validate_admin_session",
                return_value={"sub": "admin-id", "type": "admin", "role": "admin"},
            ),
            patch(
                "app.api.v1.admin_translate._csrf_check",
                new_callable=AsyncMock,
            ),
            patch(
                "app.api.v1.admin_translate.translator.bulk_translate",
                new_callable=AsyncMock,
                return_value={
                    "translated": 0,
                    "skipped": 0,
                    "failed": 0,
                    "errors": [],
                },
            ),
        ):
            response = await client.post(
                "/api/v1/admin/content/translate/bulk",
                headers={"origin": "http://test"},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "started"

    async def test_status_endpoint_no_job(self, client):
        """Test status endpoint when no job has run."""
        with (
            patch(
                "app.api.v1.admin._validate_admin_session",
                return_value={"sub": "admin-id", "type": "admin", "role": "admin"},
            ),
            patch(
                "app.api.v1.admin_translate._validate_admin_session",
                return_value={"sub": "admin-id", "type": "admin", "role": "admin"},
            ),
        ):
            # Clear any translation_status from previous test
            from app.main import app

            if hasattr(app.state, "translation_status"):
                del app.state.translation_status

            response = await client.get("/api/v1/admin/content/translate/status")
            assert response.status_code == 200
            data = response.json()
            assert data["running"] is False

    async def test_translate_single_not_found(self, client):
        """Test single translate returns 404 for missing object."""
        with (
            patch(
                "app.api.v1.admin._validate_admin_session",
                return_value={"sub": "admin-id", "type": "admin", "role": "admin"},
            ),
            patch(
                "app.api.v1.admin_translate._validate_admin_session",
                return_value={"sub": "admin-id", "type": "admin", "role": "admin"},
            ),
            patch(
                "app.api.v1.admin_translate._csrf_check",
                new_callable=AsyncMock,
            ),
            patch(
                "app.api.v1.admin_translate.KnowledgeObject",
            ) as MockKO,
        ):
            MockKO.find_one = AsyncMock(return_value=None)
            response = await client.post(
                "/api/v1/admin/content/translate/nonexistent-slug",
                headers={"origin": "http://test"},
            )
            assert response.status_code == 404

    async def test_translate_single_already_exists(self, client):
        """Test single translate returns 409 if Assamese version exists."""
        mock_ko = MagicMock()
        mock_ko.slug = "test-slug"

        with (
            patch(
                "app.api.v1.admin._validate_admin_session",
                return_value={"sub": "admin-id", "type": "admin", "role": "admin"},
            ),
            patch(
                "app.api.v1.admin_translate._validate_admin_session",
                return_value={"sub": "admin-id", "type": "admin", "role": "admin"},
            ),
            patch(
                "app.api.v1.admin_translate._csrf_check",
                new_callable=AsyncMock,
            ),
            patch(
                "app.api.v1.admin_translate.KnowledgeObject",
            ) as MockKO,
        ):
            # First call finds the object, second call finds existing translation
            MockKO.find_one = AsyncMock(return_value=mock_ko)
            response = await client.post(
                "/api/v1/admin/content/translate/test-slug",
                headers={"origin": "http://test"},
            )
            assert response.status_code == 409
