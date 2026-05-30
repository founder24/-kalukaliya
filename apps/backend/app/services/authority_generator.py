"""
AuthorityGeneratorService - AI content generation for the Authority Layer.
Generates MCQs, PYQ solutions, topic relations, and authority source suggestions.
"""

import json
import logging
from datetime import datetime, timezone

from beanie import PydanticObjectId

from app.models.topic_hub import TopicHub, TopicMCQ, TopicPYQ, TopicSource, TopicRelation
from app.services.ai.vertex_client import vertex_client

logger = logging.getLogger(__name__)


class AuthorityGeneratorService:
    """Service for generating authority content using Vertex AI."""

    async def generate_mcqs(self, topic_hub_id: str, count: int = 5) -> list[TopicMCQ]:
        """Generate MCQs for a topic using Vertex AI."""
        hub = await TopicHub.get(PydanticObjectId(topic_hub_id))
        if not hub:
            raise ValueError(f"TopicHub {topic_hub_id} not found")

        context_parts = [f"Topic: {hub.title}", f"Definition: {hub.definition}"]
        if hub.key_points:
            context_parts.append(f"Key Points: {'; '.join(hub.key_points)}")
        if hub.definition_extended:
            context_parts.append(f"Extended: {hub.definition_extended}")

        system_prompt = (
            "You are an expert educational content creator for Indian board exams (AHSEC, SEBA). "
            "Generate multiple-choice questions (MCQs) that test understanding of the given topic. "
            "Each MCQ must have exactly 4 options with one correct answer. "
            "Return a JSON array where each item has: question, options (array of 4 strings), "
            "correct_index (0-3), explanation, difficulty (easy/medium/hard)."
        )
        user_message = (
            f"{chr(10).join(context_parts)}\n\n"
            f"Generate exactly {count} MCQs of varying difficulty. Return only valid JSON array."
        )

        try:
            response = await vertex_client.generate(system_prompt, user_message)
            clean = response.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
                if clean.endswith("```"):
                    clean = clean[:-3]
                clean = clean.strip()

            mcq_data = json.loads(clean)
            if not isinstance(mcq_data, list):
                mcq_data = []

            mcqs = []
            for item in mcq_data[:count]:
                try:
                    mcq = TopicMCQ(
                        question=item["question"],
                        options=item["options"][:4],
                        correct_index=int(item.get("correct_index", 0)),
                        explanation=item.get("explanation"),
                        difficulty=item.get("difficulty", "medium"),
                        source="AI Generated",
                    )
                    mcqs.append(mcq)
                except (KeyError, ValueError, IndexError) as e:
                    logger.warning(f"Skipping malformed MCQ: {e}")
                    continue

            # Save to hub
            hub.mcqs.extend(mcqs)
            hub.updated_at = datetime.now(timezone.utc)
            await hub.save()

            return mcqs
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse MCQ response: {e}")
            return []
        except Exception as e:
            logger.error(f"MCQ generation failed for {topic_hub_id}: {e}")
            raise

    async def generate_pyq_solutions(self, topic_hub_id: str) -> list[dict]:
        """Generate solutions for linked PYQs that don't have solutions yet."""
        hub = await TopicHub.get(PydanticObjectId(topic_hub_id))
        if not hub:
            raise ValueError(f"TopicHub {topic_hub_id} not found")

        unsolved = [pyq for pyq in hub.pyqs if not pyq.solution]
        if not unsolved:
            return []

        results = []
        for pyq in unsolved:
            system_prompt = (
                "You are an expert teacher for Indian board exams. "
                "Provide a clear, step-by-step solution suitable for students. "
                "Be concise but thorough."
            )
            user_message = (
                f"Topic: {hub.title}\n"
                f"Question ({pyq.board} {pyq.year}, {pyq.marks or '?'} marks): {pyq.question}\n\n"
                "Provide the solution."
            )

            try:
                solution = await vertex_client.generate(system_prompt, user_message)
                pyq.solution = solution.strip()
                results.append({
                    "question": pyq.question,
                    "year": pyq.year,
                    "solution_generated": True,
                })
            except Exception as e:
                logger.error(f"Failed to generate solution for PYQ: {e}")
                results.append({
                    "question": pyq.question,
                    "year": pyq.year,
                    "solution_generated": False,
                    "error": str(e),
                })

        hub.updated_at = datetime.now(timezone.utc)
        await hub.save()
        return results

    async def generate_topic_relations(self, chapter_id: str) -> list[dict]:
        """Auto-infer inter-topic relations using AI. Delegates to knowledge_graph_service."""
        from app.services.knowledge_graph import knowledge_graph_service
        return await knowledge_graph_service.auto_generate_relations(chapter_id)

    async def enrich_authority_sources(self, topic_hub_id: str) -> list[TopicSource]:
        """Find and suggest official NCERT/board authority sources for a topic."""
        hub = await TopicHub.get(PydanticObjectId(topic_hub_id))
        if not hub:
            raise ValueError(f"TopicHub {topic_hub_id} not found")

        system_prompt = (
            "You are an educational reference librarian specializing in Indian education. "
            "Given a topic, suggest authoritative sources where students can find official content. "
            "Focus on NCERT textbooks, AHSEC/SEBA syllabus documents, and official board resources. "
            "Return a JSON array where each item has: source_type (ncert/ahsec_syllabus/official/reference), "
            "title, url (if known, else null), description."
        )
        user_message = (
            f"Topic: {hub.title}\n"
            f"Definition: {hub.definition}\n"
            f"Subject context: Find official Indian education sources.\n\n"
            "Return only valid JSON array."
        )

        try:
            response = await vertex_client.generate(system_prompt, user_message)
            clean = response.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
                if clean.endswith("```"):
                    clean = clean[:-3]
                clean = clean.strip()

            source_data = json.loads(clean)
            if not isinstance(source_data, list):
                return []

            sources = []
            for item in source_data:
                try:
                    source = TopicSource(
                        source_type=item.get("source_type", "reference"),
                        title=item["title"],
                        url=item.get("url"),
                        description=item.get("description"),
                    )
                    sources.append(source)
                except (KeyError, ValueError) as e:
                    logger.warning(f"Skipping malformed source: {e}")
                    continue

            return sources
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse sources response: {e}")
            return []
        except Exception as e:
            logger.error(f"Authority source enrichment failed: {e}")
            return []


authority_generator_service = AuthorityGeneratorService()
