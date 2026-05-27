"""
Integration tests for English chat path routing, model selection,
system prompt construction, and router error handling.
"""

import pytest


class TestDetectLanguageAndRoute:
    """Test that detect_language_and_route returns Vertex model for English text."""

    def test_english_text_routes_to_vertex(self):
        from app.services.ai.router import detect_language_and_route

        lang, model = detect_language_and_route("Hello, how are you?")
        assert lang == "en"
        assert "gemini" in model.lower()

    def test_assamese_text_routes_to_sarvam(self):
        from app.services.ai.router import detect_language_and_route

        lang, model = detect_language_and_route(
            "\u0986\u09aa\u09c1\u09a8\u09bf \u0995\u09c7\u09a8\u09c7\u0995\u09c8 \u0986\u099b\u09c7?"
        )
        assert lang == "as"
        assert "openhathi" in model.lower() or "sarvam" in model.lower()


class TestResolveLanguageAndModel:
    """Test ChatService.resolve_language_and_model returns Vertex for English override."""

    def test_explicit_en_override_returns_vertex(self):
        from app.services.chat_service import ChatService

        lang, model = ChatService.resolve_language_and_model(
            "some text", lang_override="en"
        )
        assert lang == "en"
        assert "gemini" in model.lower()

    def test_explicit_as_override_returns_sarvam(self):
        from app.services.chat_service import ChatService

        lang, model = ChatService.resolve_language_and_model(
            "some text", lang_override="as"
        )
        assert lang == "as"
        assert "openhathi" in model.lower() or "sarvam" in model.lower()


class TestBuildSystemPrompt:
    """Test ChatService.build_system_prompt content for English and Assamese."""

    def test_english_prompt_includes_enforcement(self):
        from app.services.chat_service import ChatService

        prompt = ChatService.build_system_prompt("en", [])
        assert "You MUST respond in English only" in prompt

    def test_english_prompt_with_context_includes_enforcement(self):
        from app.services.chat_service import ChatService

        chunks = [{"title": "Test Doc", "content": "Some content here"}]
        prompt = ChatService.build_system_prompt("en", chunks)
        assert "You MUST respond in English only" in prompt

    def test_assamese_prompt_does_not_include_english_enforcement(self):
        from app.services.chat_service import ChatService

        prompt = ChatService.build_system_prompt("as", [])
        assert "You MUST respond in English only" not in prompt

    def test_assamese_prompt_with_context_does_not_include_english_enforcement(self):
        from app.services.chat_service import ChatService

        chunks = [{"title": "Test Doc", "content": "Some content here"}]
        prompt = ChatService.build_system_prompt("as", chunks)
        assert "You MUST respond in English only" not in prompt


class TestRouterUnknownModel:
    """Test that generate_response raises RuntimeError for unknown model names."""

    @pytest.mark.anyio
    async def test_generate_response_raises_for_unknown_model(self):
        from app.services.ai.router import generate_response

        with pytest.raises(RuntimeError, match="Unknown model"):
            await generate_response(
                system_prompt="You are helpful.",
                user_message="Hello",
                model="unknown-model-xyz",
                stream=False,
            )

    @pytest.mark.anyio
    async def test_stream_response_raises_for_unknown_model(self):
        from app.services.ai.router import stream_response

        with pytest.raises(RuntimeError, match="Unknown model"):
            async for _ in stream_response(
                system_prompt="You are helpful.",
                user_message="Hello",
                model="unknown-model-xyz",
            ):
                pass
