"""Content generation service for AI-powered notes and translation."""

import logging
from datetime import datetime, timezone

from beanie import PydanticObjectId

from app.models.content import Chapter
from app.services.ai.vertex_client import vertex_client
from app.services.ai.sarvam_client import sarvam_client

logger = logging.getLogger(__name__)


class ContentGenerationService:
    """Handles AI content generation for chapters (English notes + Assamese translation)."""

    async def generate_notes(self, chapter_id: str) -> dict:
        """
        Generate English notes for a chapter using Vertex AI, then translate to Assamese.

        1. Read chapter and its published_topics
        2. Build a structured prompt listing all topics
        3. Call vertex_client.generate() for English content (1500+ words)
        4. Call sarvam_client.generate() for Assamese translation
        5. Extract meta_description, keywords, word_count
        6. Set chapter.status = 'generated', save
        """
        chapter = await Chapter.get(PydanticObjectId(chapter_id))
        if not chapter:
            raise RuntimeError(f"Chapter not found: {chapter_id}")

        # Build topic listing for the prompt
        topics_text = ""
        topic_titles = []
        for topic in chapter.published_topics:
            topic_titles.append(topic.title)
            definition_part = f" - {topic.definition}" if topic.definition else ""
            topics_text += f"- {topic.title}{definition_part}\n"

        if not topics_text:
            topics_text = f"- {chapter.title} (general overview)\n"
            topic_titles.append(chapter.title)

        system_prompt = (
            "You are an expert educational content writer. Write comprehensive, "
            "well-structured educational notes for students. The notes should be "
            "at least 1500 words long, covering all topics in detail. Use clear "
            "headings, subheadings, examples, and explanations. Make the content "
            "engaging and easy to understand for students."
        )

        user_message = (
            f"Write detailed educational notes for the chapter: '{chapter.title}'\n\n"
            f"Cover the following topics thoroughly:\n{topics_text}\n"
            f"Requirements:\n"
            f"- Minimum 1500 words\n"
            f"- Use markdown formatting with headings and subheadings\n"
            f"- Include examples and explanations for each topic\n"
            f"- Make it suitable for students preparing for exams\n"
            f"- Include a brief introduction and conclusion"
        )

        # Generate English content
        content_en = await vertex_client.generate(
            system_prompt=system_prompt,
            user_message=user_message,
        )

        # Translate to Assamese
        translation_system_prompt = (
            "You are a professional translator specializing in educational content. "
            "Translate the following English educational content to Assamese. "
            "Maintain all formatting, headings, and structure. Keep technical terms "
            "in English where appropriate but provide Assamese explanations."
        )

        content_as = await sarvam_client.generate(
            system_prompt=translation_system_prompt,
            user_message=f"Translate the following to Assamese:\n\n{content_en}",
        )

        # Extract metadata
        meta_description = content_en[:160].strip()
        keywords = ", ".join(topic_titles)
        word_count = len(content_en.split()) if content_en else 0

        # Update chapter
        chapter.content_en = content_en
        chapter.content_as = content_as
        chapter.meta_description = meta_description
        chapter.keywords = keywords
        chapter.word_count = word_count
        chapter.status = "generated"
        chapter.updated_at = datetime.now(timezone.utc)
        await chapter.save()

        logger.info(
            f"Generated notes for chapter {chapter_id}: "
            f"{word_count} words EN, AS translated"
        )

        return {
            "chapter_id": str(chapter.id),
            "status": "generated",
            "word_count": word_count,
            "meta_description": meta_description,
            "keywords": keywords,
            "content_en_preview": content_en[:200] if content_en else "",
            "content_as_preview": content_as[:200] if content_as else "",
        }

    async def generate_assamese_only(self, chapter_id: str) -> dict:
        """
        Translate existing English content to Assamese using Sarvam AI.

        Requires chapter.content_en to already exist.
        """
        chapter = await Chapter.get(PydanticObjectId(chapter_id))
        if not chapter:
            raise RuntimeError(f"Chapter not found: {chapter_id}")

        if not chapter.content_en:
            raise RuntimeError(
                f"Chapter {chapter_id} has no English content to translate"
            )

        translation_system_prompt = (
            "You are a professional translator specializing in educational content. "
            "Translate the following English educational content to Assamese. "
            "Maintain all formatting, headings, and structure. Keep technical terms "
            "in English where appropriate but provide Assamese explanations."
        )

        content_as = await sarvam_client.generate(
            system_prompt=translation_system_prompt,
            user_message=f"Translate the following to Assamese:\n\n{chapter.content_en}",
        )

        chapter.content_as = content_as
        chapter.updated_at = datetime.now(timezone.utc)
        await chapter.save()

        logger.info(f"Generated Assamese translation for chapter {chapter_id}")

        return {
            "chapter_id": str(chapter.id),
            "status": "translated",
            "content_as_preview": content_as[:200] if content_as else "",
        }
