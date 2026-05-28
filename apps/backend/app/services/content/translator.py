"""
ContentTranslator - Translates English educational content to Assamese
using the Sarvam AI client.
"""

import asyncio
import logging
from datetime import datetime, timezone

from app.models.knowledge import (
    KnowledgeObject,
    ContentMetadata,
    GeneratedContent,
)
from app.services.ai.sarvam_client import sarvam_client

logger = logging.getLogger(__name__)

TRANSLATION_SYSTEM_PROMPT = (
    "You are a translator for educational content. Translate the following "
    "English text to Assamese (\u0985\u09b8\u09ae\u09c0\u09af\u09bc\u09be). "
    "Preserve all mathematical formulas, chemical equations, proper nouns, "
    "and technical terms in English. Maintain the markdown formatting. "
    "The translation should be natural and academically appropriate for "
    "college students. Only output the translation, nothing else."
)

CHUNK_WORD_LIMIT = 800


class ContentTranslator:
    """Translates English KnowledgeObjects to Assamese using Sarvam AI."""

    async def _translate_with_retry(
        self, system_prompt: str, user_message: str, max_retries: int = 3
    ) -> str:
        """Translate with exponential backoff retry."""
        for attempt in range(max_retries):
            try:
                return await sarvam_client.generate(system_prompt, user_message)
            except Exception as e:
                if attempt == max_retries - 1:
                    raise
                wait_time = 2**attempt  # 1s, 2s, 4s
                logger.warning(
                    f"Translation attempt {attempt + 1} failed: {e}. Retrying in {wait_time}s..."
                )
                await asyncio.sleep(wait_time)
        raise RuntimeError("Translation failed after all retries")  # unreachable

    async def translate_text(self, text: str, context: str = "") -> str:
        """
        Translate a single text block from English to Assamese.
        Handles long content by chunking at ~800 words per call.
        """
        if not text or not text.strip():
            return text

        words = text.split()
        if len(words) <= CHUNK_WORD_LIMIT:
            user_message = text
            if context:
                user_message = f"[Context: {context}]\n\n{text}"
            return await self._translate_with_retry(
                TRANSLATION_SYSTEM_PROMPT, user_message
            )

        # Chunk long content at paragraph boundaries
        chunks = self._chunk_text(text, CHUNK_WORD_LIMIT)
        translated_chunks = []
        for chunk in chunks:
            user_message = chunk
            if context:
                user_message = f"[Context: {context}]\n\n{chunk}"
            translated = await self._translate_with_retry(
                TRANSLATION_SYSTEM_PROMPT, user_message
            )
            translated_chunks.append(translated)

        return "\n\n".join(translated_chunks)

    def _chunk_text(self, text: str, word_limit: int) -> list[str]:
        """Split text into chunks at paragraph boundaries, respecting word limit."""
        paragraphs = text.split("\n\n")
        chunks = []
        current_chunk = []
        current_word_count = 0

        for para in paragraphs:
            para_words = len(para.split())
            if current_word_count + para_words > word_limit and current_chunk:
                chunks.append("\n\n".join(current_chunk))
                current_chunk = [para]
                current_word_count = para_words
            else:
                current_chunk.append(para)
                current_word_count += para_words

        if current_chunk:
            chunks.append("\n\n".join(current_chunk))

        return chunks

    async def translate_knowledge_object(self, ko: KnowledgeObject) -> KnowledgeObject:
        """
        Create an Assamese version of a KnowledgeObject.
        Returns a new KnowledgeObject (does NOT save it).
        """
        new_slug = f"{ko.slug}-as"

        # Translate title, description, body
        translated_title = await self.translate_text(ko.title, context="title")
        translated_description = await self.translate_text(
            ko.description, context="description"
        )
        translated_body = await self.translate_text(
            ko.body_markdown, context="chapter body"
        )

        # Translate generated content
        translated_generated = await self._translate_generated_content(ko.generated)

        # Build new metadata
        new_metadata = ContentMetadata(
            board=ko.metadata.board,
            class_level=ko.metadata.class_level,
            subject=ko.metadata.subject,
            chapter=ko.metadata.chapter,
            chapter_number=ko.metadata.chapter_number,
            topic=ko.metadata.topic,
            difficulty=ko.metadata.difficulty,
            language="as",
            estimated_read_time_minutes=ko.metadata.estimated_read_time_minutes,
            keywords=ko.metadata.keywords,
        )

        # Create the new KnowledgeObject
        translated_ko = KnowledgeObject(
            slug=new_slug,
            title=translated_title,
            description=translated_description,
            body_markdown=translated_body,
            content_blocks=ko.content_blocks,
            metadata=new_metadata,
            generated=translated_generated,
            status=ko.status,
            published_at=ko.published_at,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        return translated_ko

    async def _translate_generated_content(
        self, generated: GeneratedContent
    ) -> GeneratedContent:
        """Translate all generated derivative content."""
        # Translate summary
        translated_summary = await self.translate_text(
            generated.summary, context="summary"
        )

        # Translate MCQs in parallel
        async def _translate_single_mcq(mcq):
            translated_mcq = dict(mcq)
            tasks = []
            if "question" in mcq:
                tasks.append(("question", self._translate_with_retry(TRANSLATION_SYSTEM_PROMPT, f"[Context: MCQ question]\n\n{mcq['question']}")))
            if "explanation" in mcq:
                tasks.append(("explanation", self._translate_with_retry(TRANSLATION_SYSTEM_PROMPT, f"[Context: MCQ explanation]\n\n{mcq['explanation']}")))

            # Gather question + explanation
            if tasks:
                results = await asyncio.gather(*[t[1] for t in tasks])
                for (key, _), result in zip(tasks, results):
                    translated_mcq[key] = result

            # Translate options in parallel
            if "options" in mcq and isinstance(mcq["options"], list):
                translated_mcq["options"] = await asyncio.gather(*[
                    self._translate_with_retry(TRANSLATION_SYSTEM_PROMPT, f"[Context: MCQ option]\n\n{opt}")
                    for opt in mcq["options"]
                ])
            return translated_mcq

        translated_mcqs = await asyncio.gather(*[
            _translate_single_mcq(mcq) for mcq in generated.mcqs
        ])

        # Translate definitions
        translated_definitions = []
        for defn in generated.definitions:
            translated_defn = dict(defn)
            if "term" in defn:
                translated_defn["term"] = await self.translate_text(
                    defn["term"], context="definition term"
                )
            if "definition" in defn:
                translated_defn["definition"] = await self.translate_text(
                    defn["definition"], context="definition"
                )
            translated_definitions.append(translated_defn)

        # Translate important questions
        translated_questions = []
        for q in generated.important_questions:
            translated_q = dict(q)
            if "question" in q:
                translated_q["question"] = await self.translate_text(
                    q["question"], context="important question"
                )
            translated_questions.append(translated_q)

        return GeneratedContent(
            mcqs=translated_mcqs,
            summary=translated_summary,
            definitions=translated_definitions,
            important_questions=translated_questions,
        )

    async def bulk_translate(
        self,
        app_state,
        board: str = None,
        subject: str = None,
        limit: int = 50,
        skip_existing: bool = True,
    ) -> dict:
        """
        Bulk translate published English KnowledgeObjects to Assamese.
        Updates app_state.translation_status with progress.
        """
        results = {"translated": 0, "skipped": 0, "failed": 0, "errors": []}

        try:
            # Build query filters
            query = {
                "status": "published",
                "metadata.language": "en",
            }
            if board:
                query["metadata.board"] = board
            if subject:
                query["metadata.subject"] = subject

            objects = await KnowledgeObject.find(query).limit(limit).to_list()
            app_state.translation_status["total"] = len(objects)

            for i, ko in enumerate(objects):
                try:
                    app_state.translation_status["current_slug"] = ko.slug

                    # Skip if Assamese version already exists
                    if skip_existing:
                        existing = await KnowledgeObject.find_one(
                            KnowledgeObject.slug == f"{ko.slug}-as"
                        )
                        if existing:
                            results["skipped"] += 1
                            app_state.translation_status["completed"] = i + 1
                            continue

                    # Translate
                    translated_ko = await self.translate_knowledge_object(ko)
                    await translated_ko.insert()
                    results["translated"] += 1

                except Exception as e:
                    results["failed"] += 1
                    error_msg = f"{ko.slug}: {str(e)}"
                    results["errors"].append(error_msg)
                    app_state.translation_status["errors"].append(error_msg)
                    logger.error(f"Translation failed for {ko.slug}: {e}")

                app_state.translation_status["completed"] = i + 1

                # Rate limiting between translations
                await asyncio.sleep(1.5)

        except Exception as e:
            logger.error(f"Bulk translation error: {e}")
            results["errors"].append(f"Bulk operation error: {str(e)}")
        finally:
            app_state.translation_status["running"] = False
            app_state.translation_status["finished_at"] = datetime.now(
                timezone.utc
            ).isoformat()
            app_state.translation_status["results"] = results

        return results
