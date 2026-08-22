"""
Integration tests for English chat path routing, model selection,
system prompt construction, and router error handling.
"""

import pytest


class TestDetectLanguageAndRoute:
    """Test that every language routes through the Workers AI model."""

    def test_english_text_routes_to_workers_ai(self):
        from app.services.ai.router import detect_language_and_route

        lang, model = detect_language_and_route("Hello, how are you?")
        assert lang == "en"
        assert model.startswith("@cf/")

    def test_assamese_text_routes_to_workers_ai(self):
        from app.services.ai.router import detect_language_and_route

        lang, model = detect_language_and_route(
            "\u0986\u09aa\u09c1\u09a8\u09bf \u0995\u09c7\u09a8\u09c7\u0995\u09c8 \u0986\u099b\u09c7?"
        )
        assert lang == "as"
        assert model.startswith("@cf/")


class TestResolveLanguageAndModel:
    """Explicit language selection must use Workers AI for both chat modes."""

    def test_explicit_en_override_returns_workers_ai(self):
        from app.services.chat_service import ChatService

        lang, model = ChatService.resolve_language_and_model(
            "some text", lang_override="en"
        )
        assert lang == "en"
        assert model.startswith("@cf/")

    def test_explicit_as_override_returns_workers_ai(self):
        from app.services.chat_service import ChatService

        lang, model = ChatService.resolve_language_and_model(
            "some text", lang_override="as"
        )
        assert lang == "as"
        assert model.startswith("@cf/")


class TestBuildSystemPrompt:
    """Test ChatService.build_system_prompt content for English and Assamese."""

    def test_english_prompt_includes_enforcement(self):
        from app.services.chat_service import ChatService

        prompt = ChatService.build_system_prompt("en", [])
        assert "student selected English mode" in prompt
        assert "response must be in English only" in prompt

    def test_english_prompt_with_context_includes_enforcement(self):
        from app.services.chat_service import ChatService

        chunks = [{"title": "Test Doc", "content": "Some content here"}]
        prompt = ChatService.build_system_prompt("en", chunks)
        assert "student selected English mode" in prompt

    def test_assamese_prompt_does_not_include_english_enforcement(self):
        from app.services.chat_service import ChatService

        prompt = ChatService.build_system_prompt("as", [])
        assert "student selected English mode" not in prompt

    def test_assamese_prompt_with_context_does_not_include_english_enforcement(self):
        from app.services.chat_service import ChatService

        chunks = [{"title": "Test Doc", "content": "Some content here"}]
        prompt = ChatService.build_system_prompt("as", chunks)
        assert "student selected English mode" not in prompt
