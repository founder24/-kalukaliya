"""SEO content generation service for topic-level page variations."""

import json
import logging

from beanie import PydanticObjectId

from app.models.content import Chapter
from app.services.ai.vertex_client import vertex_client

logger = logging.getLogger(__name__)


class SEOGeneratorService:
    """Generates SEO page content variations and extracts topics from content."""

    async def generate_seo_pages(self, topics: list[dict]) -> dict:
        """
        Generate SEO page content variations for a list of topics.

        For each topic, generates: notes, definition, MCQ, important-questions,
        examples page content using Vertex AI.

        Args:
            topics: List of dicts with 'title' and optionally 'definition'

        Returns:
            Dict mapping topic titles to their generated content variations.
        """
        if not topics:
            raise RuntimeError("No topics provided for SEO page generation")

        results = {}

        for topic in topics:
            title = topic.get("title", "")
            definition = topic.get("definition", "")

            if not title:
                continue

            system_prompt = (
                "You are an SEO content specialist for an educational platform. "
                "Generate structured content for different page types. "
                "Each page type should be optimized for search engines while being "
                "genuinely helpful for students. Return the content in JSON format."
            )

            user_message = (
                f"Generate SEO-optimized educational content for the topic: '{title}'\n"
                f"Definition context: {definition}\n\n"
                f"Generate content for these 5 page types as a JSON object:\n"
                f"1. 'notes' - Detailed study notes (300-500 words)\n"
                f"2. 'definition' - Clear definition with explanation (150-250 words)\n"
                f"3. 'mcq' - 5 multiple choice questions with answers in JSON array format\n"
                f"4. 'important_questions' - 10 important questions for exam preparation\n"
                f"5. 'examples' - 3-5 practical examples with explanations\n\n"
                f"Return as valid JSON with keys: notes, definition, mcq, "
                f"important_questions, examples"
            )

            try:
                response = await vertex_client.generate(
                    system_prompt=system_prompt,
                    user_message=user_message,
                )

                # Try to parse as JSON, fall back to raw text
                try:
                    parsed = json.loads(response)
                except (json.JSONDecodeError, TypeError):
                    parsed = {"raw_content": response}

                results[title] = parsed
                logger.info(f"Generated SEO pages for topic: {title}")

            except Exception as e:
                logger.error(f"Failed to generate SEO pages for topic '{title}': {e}")
                results[title] = {"error": str(e)}

        return {
            "status": "generated",
            "topics_processed": len(results),
            "content": results,
        }

    async def extract_topics_from_content(self, chapter_id: str) -> dict:
        """
        Use AI to extract topic titles and definitions from chapter content.

        Reads chapter.content_en and asks Vertex AI to identify distinct topics
        with their definitions. Returns a list of {title, definition} dicts
        without saving (admin decides whether to accept).
        """
        chapter = await Chapter.get(PydanticObjectId(chapter_id))
        if not chapter:
            raise RuntimeError(f"Chapter not found: {chapter_id}")

        if not chapter.content_en:
            raise RuntimeError(
                f"Chapter {chapter_id} has no English content to extract topics from"
            )

        system_prompt = (
            "You are an educational content analyst. Extract distinct topics "
            "from the provided educational content. For each topic, provide a "
            "concise title and a brief definition (1-2 sentences). "
            "Return ONLY a valid JSON array of objects with 'title' and 'definition' keys."
        )

        user_message = (
            f"Extract all distinct topics from this educational content for the "
            f"chapter '{chapter.title}':\n\n"
            f"{chapter.content_en}\n\n"
            f"Return as a JSON array like: "
            f'[{{"title": "Topic Name", "definition": "Brief definition..."}}]'
        )

        response = await vertex_client.generate(
            system_prompt=system_prompt,
            user_message=user_message,
        )

        # Parse the AI response into a list of topics
        topics = []
        try:
            # Try to find JSON array in the response
            # Sometimes the AI wraps it in markdown code blocks
            cleaned = response.strip()
            if cleaned.startswith("```"):
                # Remove markdown code block
                lines = cleaned.split("\n")
                cleaned = "\n".join(lines[1:-1]) if len(lines) > 2 else cleaned
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

            parsed = json.loads(cleaned)
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict) and "title" in item:
                        topics.append({
                            "title": item["title"],
                            "definition": item.get("definition", ""),
                        })
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(
                f"Failed to parse AI response as JSON for chapter {chapter_id}: {e}"
            )
            # Return raw response so admin can still use it
            return {
                "chapter_id": str(chapter.id),
                "status": "parse_error",
                "raw_response": response,
                "topics": [],
            }

        logger.info(
            f"Extracted {len(topics)} topics from chapter {chapter_id}"
        )

        return {
            "chapter_id": str(chapter.id),
            "status": "extracted",
            "topics": topics,
            "count": len(topics),
        }
