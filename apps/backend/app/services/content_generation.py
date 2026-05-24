"""
ContentGenerationService - Generates educational content using Vertex AI and Sarvam AI.
"""

import logging
from datetime import datetime, timezone

from beanie import PydanticObjectId

from app.models.content import Chapter
from app.services.ai.vertex_client import vertex_client
from app.services.ai.sarvam_client import sarvam_client

logger = logging.getLogger(__name__)


class ContentGenerationService:
    """Service for generating chapter notes in English and Assamese."""

    async def generate_notes(self, chapter_id: str) -> Chapter:
        """Generate English notes and Assamese translation for a chapter."""
        chapter = await Chapter.get(PydanticObjectId(chapter_id))
        if not chapter:
            raise ValueError(f"Chapter {chapter_id} not found")

        # Build prompt from chapter topics
        topics_text = "\n".join(
            f"- {t.title}" + (f": {t.definition}" if t.definition else "")
            for t in chapter.published_topics
        )

        system_prompt = (
            "You are an expert educational content writer. "
            "Generate comprehensive study notes for the following chapter. "
            "Use clear language suitable for students. "
            "Include explanations, examples, and key points."
        )
        user_message = (
            f"Chapter: {chapter.title}\n"
            f"Topics:\n{topics_text}\n\n"
            "Generate detailed study notes covering all topics."
        )

        # Generate English content via Vertex AI
        content_en = await vertex_client.generate(system_prompt, user_message)
        chapter.content_en = content_en

        # Generate Assamese translation via Sarvam AI
        translate_prompt = (
            "You are a professional translator. "
            "Translate the following educational content from English to Assamese. "
            "Maintain the structure and formatting."
        )
        content_as = await sarvam_client.generate(translate_prompt, content_en)
        chapter.content_as = content_as

        # Extract metadata
        meta_prompt = (
            "Extract a concise meta description (max 160 chars) and "
            "comma-separated keywords from this content. "
            "Format: META: <description>\nKEYWORDS: <keywords>"
        )
        meta_response = await vertex_client.generate(
            "You are an SEO specialist.", f"{meta_prompt}\n\nContent:\n{content_en[:2000]}"
        )

        # Parse meta response
        meta_description = ""
        keywords = ""
        for line in meta_response.split("\n"):
            if line.startswith("META:"):
                meta_description = line[5:].strip()[:160]
            elif line.startswith("KEYWORDS:"):
                keywords = line[9:].strip()

        chapter.meta_description = meta_description
        chapter.keywords = keywords
        chapter.word_count = len(content_en.split())
        chapter.status = "generated"
        chapter.updated_at = datetime.now(timezone.utc)
        await chapter.save()

        return chapter

    async def generate_assamese_only(self, chapter_id: str) -> Chapter:
        """Translate existing English content to Assamese using Sarvam AI."""
        chapter = await Chapter.get(PydanticObjectId(chapter_id))
        if not chapter:
            raise ValueError(f"Chapter {chapter_id} not found")

        if not chapter.content_en:
            raise ValueError(f"Chapter {chapter_id} has no English content to translate")

        translate_prompt = (
            "You are a professional translator. "
            "Translate the following educational content from English to Assamese. "
            "Maintain the structure and formatting."
        )
        content_as = await sarvam_client.generate(translate_prompt, chapter.content_en)
        chapter.content_as = content_as
        chapter.updated_at = datetime.now(timezone.utc)
        await chapter.save()

        return chapter


content_generation_service = ContentGenerationService()
