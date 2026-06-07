"""
ContentGenerationService - Generates educational content using Vertex AI and Sarvam AI.

Pipeline (fully automatic):
  generate_notes()          → Vertex AI (EN) + Sarvam AI (AS) → MongoDB
                              → auto-calls publish_chapter()
                                 ↳ GCS upload (source of truth for CF Pages)
                                 ↳ Vertex AI Search indexing (RAG)
                                 ↳ Cloudflare prerender / KV invalidation
                                 ↳ Topic embeddings (cosine similarity)
                                 ↳ status = "published"

  generate_assamese_only()  → Sarvam AI (AS, chunked) → MongoDB
                              → re-publishes to GCS so CF Pages gets bilingual JSON
                              → re-indexes in Vertex AI Search with updated content
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

    # ──────────────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────────────

    async def _auto_publish(self, chapter_id: str, chapter_title: str) -> dict:
        """Run the full publish pipeline after generation (soft-fail).

        Always soft-fails so a network/GCS/Vertex hiccup never prevents the
        generated content from being returned to the caller.  The chapter will
        be left at status='generated' and can be published later via the admin
        API if this step fails.
        """
        try:
            from app.services.content_publisher import content_publisher_service
            result = await content_publisher_service.publish_chapter(chapter_id)
            logger.info(
                f"Auto-publish complete for {chapter_title!r}: "
                f"gcs={result.get('gcs', {}).get('status')} "
                f"vtx={result.get('vertex_search', {}).get('status')} "
                f"emb={result.get('topic_embeddings', {}).get('count', 0)}"
            )
            return result
        except Exception as e:
            logger.warning(
                f"Auto-publish failed for {chapter_title!r} (chapter will stay "
                f"at status=generated — re-run /publish to retry): {e}"
            )
            return {"status": "error", "detail": str(e)}

    async def _gcs_update(self, chapter: Chapter) -> dict:
        """Push updated chapter JSON to GCS after an Assamese-only update (soft-fail)."""
        try:
            from app.services.content_publisher import content_publisher_service
            gcs_result = await content_publisher_service.publish_to_gcs(chapter)
            vtx_result = await content_publisher_service.publish_to_vertex_search(chapter)
            logger.info(
                f"GCS/Vertex re-sync after Assamese update for {chapter.title!r}: "
                f"gcs={gcs_result.get('status')} vtx={vtx_result.get('status')}"
            )
            return {"gcs": gcs_result, "vertex_search": vtx_result}
        except Exception as e:
            logger.warning(
                f"GCS/Vertex re-sync failed after Assamese update for "
                f"{chapter.title!r}: {e}"
            )
            return {"status": "error", "detail": str(e)}

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    async def generate_notes(self, chapter_id: str, force: bool = False) -> Chapter:
        """Generate English notes + Assamese translation, then auto-publish.

        Full pipeline on success:
          1. Vertex AI → English study notes
          2. Vertex AI → per-topic definitions (1–2 sentences each, soft-fail)
          3. Sarvam AI → Assamese translation (chunked, soft-fail)
          4. Vertex AI → SEO meta description + keywords
          5. Vertex AI → 5-entry FAQ JSON-LD
          6. Save to MongoDB (status='generated')
          7. publish_chapter() → GCS + Vertex Search + CF prerender + embeddings
                               → status='published' in MongoDB

        Args:
            chapter_id: The chapter to generate notes for.
            force: When False (default), skip if content_en already exists.
                   Set True to regenerate and re-publish.
        """
        chapter = await Chapter.get(PydanticObjectId(chapter_id))
        if not chapter:
            raise ValueError(f"Chapter {chapter_id} not found")

        if not force and chapter.content_en and chapter.content_en.strip():
            logger.info(
                f"Skipping generation for chapter {chapter_id} "
                f"({chapter.title!r}) — content_en already present. "
                "Pass force=True to overwrite."
            )
            return chapter

        # ── 1. Build prompt from published topics ──────────────────────────────
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

        # ── 2. English content via Vertex AI ───────────────────────────────────
        logger.info(f"Generating English notes for {chapter.title!r}")
        content_en = await vertex_client.generate(system_prompt, user_message)
        chapter.content_en = content_en

        # ── 3. Extract per-topic definitions from generated content ───────────
        if chapter.published_topics:
            topics_list = ", ".join(t.title for t in chapter.published_topics)
            def_prompt = (
                f"From the study notes below, extract a 1–2 sentence factual definition "
                f"for each of these topics: {topics_list}\n\n"
                "Format strictly as:\nTOPIC: <topic title>\nDEF: <definition>\n\n"
                "Only output the topic/definition pairs. No extra text."
            )
            try:
                def_response = await vertex_client.generate(
                    "You are an educational content extractor.",
                    f"{def_prompt}\n\nNotes:\n{content_en[:4000]}",
                )
                topic_map = {t.title.lower(): t for t in chapter.published_topics}
                current_topic = None
                for line in def_response.split("\n"):
                    line = line.strip()
                    if line.startswith("TOPIC:"):
                        title = line[6:].strip().lower()
                        current_topic = topic_map.get(title)
                    elif line.startswith("DEF:") and current_topic:
                        current_topic.definition = line[4:].strip()
                        current_topic = None
                defined = sum(1 for t in chapter.published_topics if t.definition)
                logger.info(f"Topic definitions extracted: {defined}/{len(chapter.published_topics)} for {chapter.title!r}")
            except Exception as e:
                logger.warning(f"Topic definition extraction failed for {chapter.title!r}: {e}")

        # ── 4. Assamese translation via Sarvam AI (chunked, soft-fail) ─────────
        translate_prompt = (
            "You are a professional translator. "
            "Translate the following educational content from English to Assamese. "
            "Output ONLY the Assamese translation. "
            "Maintain the structure and formatting exactly."
        )
        words = content_en.split()
        chunk_size = 400
        chunks = [
            " ".join(words[i : i + chunk_size])
            for i in range(0, len(words), chunk_size)
        ]
        translated_parts = []
        for idx, chunk in enumerate(chunks):
            try:
                part = await sarvam_client.generate(translate_prompt, chunk)
                if part and part.strip():
                    translated_parts.append(part.strip())
            except Exception as e:
                logger.warning(
                    f"Assamese chunk {idx + 1}/{len(chunks)} failed for "
                    f"{chapter.title!r}: {e}"
                )
        if translated_parts:
            chapter.content_as = "\n\n".join(translated_parts)
        else:
            logger.warning(
                f"Assamese translation produced no output for {chapter.title!r}. "
                "Run /generate-notes/as once SARVAM_API_KEY is configured."
            )

        # ── 4. SEO meta + keywords via Vertex AI ───────────────────────────────
        meta_prompt = (
            "Extract a concise meta description (max 160 chars) and "
            "comma-separated keywords from this content. "
            "Format: META: <description>\nKEYWORDS: <keywords>"
        )
        try:
            meta_response = await vertex_client.generate(
                "You are an SEO specialist.",
                f"{meta_prompt}\n\nContent:\n{content_en[:2000]}",
            )
            for line in meta_response.split("\n"):
                if line.startswith("META:"):
                    chapter.meta_description = line[5:].strip()[:160]
                elif line.startswith("KEYWORDS:"):
                    chapter.keywords = line[9:].strip()
        except Exception as e:
            logger.warning(f"SEO meta generation failed for {chapter.title!r}: {e}")

        # ── 5. FAQ JSON-LD via Vertex AI ───────────────────────────────────────
        if not chapter.faq_jsonld or len(chapter.faq_jsonld) < 2:
            faq_prompt = (
                f"Generate exactly 5 frequently asked questions and answers about: {chapter.title}. "
                f"Topics covered: {topics_text}\n\n"
                "Format each as:\nQ: <question>\nA: <answer>\n\n"
                "Make questions specific and educational. Answers should be 1-3 sentences."
            )
            try:
                faq_response = await vertex_client.generate(
                    "You are an educational FAQ writer for Indian board exam students.",
                    faq_prompt,
                )
                faq_entries = []
                current_q = ""
                for line in faq_response.split("\n"):
                    line = line.strip()
                    if line.startswith("Q:"):
                        current_q = line[2:].strip()
                    elif line.startswith("A:") and current_q:
                        faq_entries.append(
                            {"question": current_q, "answer": line[2:].strip()}
                        )
                        current_q = ""
                if len(faq_entries) >= 2:
                    chapter.faq_jsonld = faq_entries
            except Exception as e:
                logger.warning(f"FAQ generation failed for {chapter.title!r}: {e}")

        # ── 6. Save to MongoDB (status=generated) ─────────────────────────────
        chapter.word_count = len(content_en.split())
        chapter.status = "generated"
        chapter.notes_generated = True
        chapter.updated_at = datetime.now(timezone.utc)
        await chapter.save()
        logger.info(
            f"Notes saved to MongoDB for {chapter.title!r} "
            f"({chapter.word_count} EN words, has_as={bool(chapter.content_as)})"
        )

        # ── 7. Auto-publish: GCS + Vertex Search + CF + embeddings ────────────
        publish_result = await self._auto_publish(chapter_id, chapter.title)

        # Re-fetch so the returned object has status=published (if publish succeeded)
        chapter = await Chapter.get(PydanticObjectId(chapter_id))
        chapter._publish_result = publish_result  # attach for caller inspection
        return chapter

    async def generate_assamese_only(self, chapter_id: str, force: bool = False) -> Chapter:
        """Translate existing English content to Assamese, then re-sync to GCS + Vertex Search.

        The Sarvam sarvam-30b / sarvam-105b models are reasoning models with a
        4096-token completion budget on the starter plan.  Sending ~1000-1300
        English words in one shot exhausts that budget entirely on reasoning,
        leaving content=null.  We chunk into ~400-word segments so each request
        fits comfortably: ~600 prompt tokens + ~1500 reasoning + ~900 output ≈
        3000 tokens, well within the 4096 cap.

        After a successful translation the chapter JSON is re-uploaded to GCS
        and re-indexed in Vertex AI Search so that Cloudflare Pages and RAG
        always serve the latest bilingual content.

        Args:
            chapter_id: The chapter to translate.
            force: When False (default), skip if content_as already exists.
                   Set True to re-translate and re-publish.
        """
        chapter = await Chapter.get(PydanticObjectId(chapter_id))
        if not chapter:
            raise ValueError(f"Chapter {chapter_id} not found")

        if not chapter.content_en:
            raise ValueError(
                f"Chapter {chapter_id} has no English content to translate"
            )

        if not force and chapter.content_as and chapter.content_as.strip():
            logger.info(
                f"Skipping Assamese translation for chapter {chapter_id} "
                f"({chapter.title!r}) — content_as already present. "
                "Pass force=True to overwrite."
            )
            return chapter

        translate_prompt = (
            "You are a professional translator. "
            "Translate the following educational content from English to Assamese. "
            "Output ONLY the Assamese translation. "
            "Maintain the structure and formatting exactly."
        )

        # Split into ~400-word chunks so reasoning + output fit in 4096 tokens
        words = chapter.content_en.split()
        chunk_size = 400
        chunks = [
            " ".join(words[i : i + chunk_size])
            for i in range(0, len(words), chunk_size)
        ]

        translated_parts = []
        for idx, chunk in enumerate(chunks):
            logger.info(
                f"Translating chunk {idx + 1}/{len(chunks)} for {chapter.title!r}"
            )
            part = await sarvam_client.generate(translate_prompt, chunk)
            if part and part.strip():
                translated_parts.append(part.strip())

        content_as = "\n\n".join(translated_parts)
        chapter.content_as = content_as
        chapter.updated_at = datetime.now(timezone.utc)
        await chapter.save()
        logger.info(
            f"Assamese translation saved for {chapter.title!r} "
            f"({len(content_as.split())} words)"
        )

        # Re-sync GCS + Vertex Search so CF Pages and RAG pick up the Assamese content
        await self._gcs_update(chapter)

        return chapter


content_generation_service = ContentGenerationService()
