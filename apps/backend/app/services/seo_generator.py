"""
SEOGeneratorService - Generates SEO-optimized pages and extracts topics using AI.
"""

import logging
from datetime import datetime, timezone

from beanie import PydanticObjectId

from app.models.content import Chapter, Topic
from app.services.ai.vertex_client import vertex_client

logger = logging.getLogger(__name__)


class SEOGeneratorService:
    """Service for generating SEO pages and extracting topics."""

    async def generate_seo_pages(self, topics: list[dict]) -> list[dict]:
        """Generate SEO-optimized content pages for given topics.

        For each topic, generates: notes, definition, MCQs, important questions, examples.
        """
        results = []
        for topic_data in topics:
            title = topic_data.get("title", "")
            topic_slug = topic_data.get("topic_slug", "")

            system_prompt = (
                "You are an expert educational content generator specialized in SEO. "
                "Generate comprehensive, well-structured content that is optimized for "
                "search engines and useful for students."
            )

            # Generate notes
            notes_prompt = (
                f"Generate detailed study notes for the topic: {title}. "
                "Include key concepts, explanations, and examples. "
                "Format with clear headings and bullet points."
            )
            notes = await vertex_client.generate(system_prompt, notes_prompt)

            # Generate definition
            def_prompt = (
                f"Write a clear, concise academic definition for: {title}. "
                "Include context and significance."
            )
            definition = await vertex_client.generate(system_prompt, def_prompt)

            # Generate MCQs
            mcq_prompt = (
                f"Generate 5 multiple choice questions about: {title}. "
                "Format each with question, 4 options (A-D), and correct answer."
            )
            mcqs = await vertex_client.generate(system_prompt, mcq_prompt)

            # Generate important questions
            iq_prompt = (
                f"Generate 5 important exam questions about: {title}. "
                "Include both short answer and long answer type questions."
            )
            important_questions = await vertex_client.generate(system_prompt, iq_prompt)

            # Generate examples
            examples_prompt = (
                f"Provide 3 detailed examples or solved problems for: {title}. "
                "Show step-by-step solutions where applicable."
            )
            examples = await vertex_client.generate(system_prompt, examples_prompt)

            results.append({
                "topic_slug": topic_slug,
                "title": title,
                "generated": {
                    "notes": notes,
                    "definition": definition,
                    "mcqs": mcqs,
                    "important_questions": important_questions,
                    "examples": examples,
                },
                "generated_at": datetime.now(timezone.utc).isoformat(),
            })

        return results

    async def extract_topics_from_content(self, chapter_id: str) -> list[dict]:
        """Use AI to extract topics from chapter content."""
        chapter = await Chapter.get(PydanticObjectId(chapter_id))
        if not chapter:
            raise ValueError(f"Chapter {chapter_id} not found")

        if not chapter.content_en:
            raise ValueError(f"Chapter {chapter_id} has no English content")

        system_prompt = (
            "You are an expert at analyzing educational content and identifying "
            "distinct topics. Extract all major topics from the given content."
        )
        user_message = (
            f"Extract all distinct topics from this chapter content. "
            f"For each topic, provide: title, a brief definition, and a URL-friendly slug.\n"
            f"Format each topic on a line as: TOPIC: <title> | <definition> | <slug>\n\n"
            f"Content:\n{chapter.content_en[:4000]}"
        )

        response = await vertex_client.generate(system_prompt, user_message)

        topics = []
        for line in response.split("\n"):
            line = line.strip()
            if line.startswith("TOPIC:"):
                parts = line[6:].split("|")
                if len(parts) >= 3:
                    topics.append({
                        "title": parts[0].strip(),
                        "definition": parts[1].strip(),
                        "topic_slug": parts[2].strip(),
                    })
                elif len(parts) == 2:
                    topics.append({
                        "title": parts[0].strip(),
                        "definition": parts[1].strip(),
                        "topic_slug": parts[0].strip().lower().replace(" ", "-"),
                    })

        return topics


seo_generator_service = SEOGeneratorService()
