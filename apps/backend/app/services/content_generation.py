"""
ContentGenerationService - Generates educational content using Cloudflare Workers AI.

Pipeline (fully automatic):
   generate_notes()          → Workers AI (EN + AS) → MongoDB
                              → auto-calls publish_chapter()
                                 ↳ GCS upload (source of truth for CF Pages)
                                 ↳ Vertex AI Search indexing (RAG)
                                 ↳ Cloudflare prerender / KV invalidation
                                 ↳ Topic embeddings (cosine similarity)
                                 ↳ status = "published"

   generate_assamese_only()  → Workers AI (AS, chunked) → MongoDB
                              → re-publishes to GCS so CF Pages gets bilingual JSON
                              → re-indexes in Vertex AI Search with updated content

   ensure_topics()           → Workers AI → generates 4-6 topic titles for chapters
                              that have no published_topics yet, saves to MongoDB.
"""

import logging
import re
import uuid
from datetime import datetime, timezone

from beanie import PydanticObjectId

from app.models.content import Chapter
from app.services.ai.workers_ai_client import workers_ai_client

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
            logger.info(
                f"GCS re-sync after Assamese update for {chapter.title!r}: "
                f"gcs={gcs_result.get('status')}"
            )
            return {"gcs": gcs_result}
        except Exception as e:
            logger.warning(
                f"GCS re-sync failed after Assamese update for "
                f"{chapter.title!r}: {e}"
            )
            return {"status": "error", "detail": str(e)}

    # ──────────────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────────────

    async def ensure_topics(self, chapter: Chapter, subject_name: str = "") -> Chapter:
        """Generate 4-6 topic titles for a chapter that has no published_topics.

        Uses Workers AI to derive a realistic topic list from the chapter title
        and subject name, then saves the result to MongoDB.  Idempotent — if
        topics already exist the chapter is returned unchanged.

        Args:
            chapter:      The Chapter document (Beanie).
            subject_name: Optional subject name for extra context in the prompt.

        Returns:
            The (possibly updated) Chapter document.
        """
        if chapter.published_topics:
            return chapter   # already has topics

        ctx = f"Subject: {subject_name}\n" if subject_name else ""
        prompt = (
            f"{ctx}Chapter title: {chapter.title}\n\n"
            "List 4 to 6 specific topic titles that this chapter would cover "
            "in an Indian university / higher secondary curriculum. "
            "Output ONLY a numbered list — one topic per line, no extra text.\n"
            "Example:\n1. Introduction and Overview\n2. Key Concepts\n..."
        )
        try:
            raw = await workers_ai_client.generate(
                "You are a curriculum designer for Indian higher education. "
                "Output ONLY the numbered topic list, nothing else.",
                prompt,
            )
        except Exception as e:
            logger.warning(f"ensure_topics Workers AI call failed for {chapter.title!r}: {e}")
            return chapter

        # Parse numbered / bulleted lines into topic titles
        from app.models.content import Topic  # local import avoids circular

        topics = []
        for line in raw.strip().split("\n"):
            line = line.strip()
            # Strip leading number/bullet:  "1. Foo"  "- Foo"  "• Foo"
            clean = re.sub(r"^[\d]+[.)]\s*|^[-•*]\s*", "", line).strip()
            if clean and len(clean) > 3:
                topics.append(
                    Topic(
                        id=str(uuid.uuid4()),
                        title=clean,
                        definition=None,
                        topic_slug=re.sub(r"[^a-z0-9]+", "-", clean.lower()).strip("-"),
                        definition_status="pending",
                        wikidata_uri=None,
                    )
                )

        if not topics:
            logger.warning(f"ensure_topics produced no topics for {chapter.title!r} (raw: {raw[:200]!r})")
            return chapter

        chapter.published_topics = topics
        chapter.updated_at = datetime.now(timezone.utc)
        await chapter.save()
        logger.info(f"ensure_topics: seeded {len(topics)} topics for {chapter.title!r}")
        return chapter

    async def generate_notes(self, chapter_id: str, force: bool = False) -> Chapter:
        """Generate English notes + Assamese translation, then auto-publish.

        Full pipeline on success:
          1. Workers AI → English study notes
          2. Workers AI → per-topic definitions (1–2 sentences each, soft-fail)
          3. Workers AI → Assamese translation (chunked, soft-fail)
          4. Workers AI → SEO meta description + keywords
          5. Workers AI → 5-entry FAQ JSON-LD
          6. Save to MongoDB (status='generated')
          7. publish_chapter() → GCS + Vertex Search + CF prerender + embeddings
                               → status='published' in MongoDB

        Args:
            chapter_id: The chapter to generate notes for.
            force: When False (default), skip if notes_en OR content_en already
                   exists (notes_en is checked first as the primary pipeline field).
                   Set True to regenerate and re-publish.
        """
        chapter = await Chapter.get(PydanticObjectId(chapter_id))
        if not chapter:
            raise ValueError(f"Chapter {chapter_id} not found")

        if not force and (
            (chapter.notes_en and chapter.notes_en.strip())
            or (chapter.content_en and chapter.content_en.strip())
        ):
            present_field = "notes_en" if (chapter.notes_en and chapter.notes_en.strip()) else "content_en"
            logger.info(
                f"Skipping generation for chapter {chapter_id} "
                f"({chapter.title!r}) — {present_field} already present. "
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

        # ── 2. English content via Workers AI ─────────────────────────────────
        logger.info(f"Generating English notes for {chapter.title!r}")
        content_en = await workers_ai_client.generate(system_prompt, user_message)
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
                def_response = await workers_ai_client.generate(
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

        # ── 4. Assamese translation via Workers AI (chunked, soft-fail) ───────
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
                part = await workers_ai_client.generate(translate_prompt, chunk, is_assamese=True)
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
                "Check the Workers AI internal generation configuration before retrying."
            )

        # ── 4. SEO meta + keywords via Workers AI ─────────────────────────────
        meta_prompt = (
            "Extract a concise meta description (max 160 chars) and "
            "comma-separated keywords from this content. "
            "Format: META: <description>\nKEYWORDS: <keywords>"
        )
        try:
            meta_response = await workers_ai_client.generate(
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

        # ── 5. FAQ JSON-LD via Workers AI ─────────────────────────────────────
        if not chapter.faq_jsonld or len(chapter.faq_jsonld) < 2:
            faq_prompt = (
                f"Generate exactly 5 frequently asked questions and answers about: {chapter.title}. "
                f"Topics covered: {topics_text}\n\n"
                "Format each as:\nQ: <question>\nA: <answer>\n\n"
                "Make questions specific and educational. Answers should be 1-3 sentences."
            )
            try:
                faq_response = await workers_ai_client.generate(
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

        We chunk at roughly 400 words to retain formulas and Markdown structure
        while staying well inside the configured Workers AI token budget.

        After a successful translation the chapter JSON is re-uploaded to GCS
        and re-indexed in Vertex AI Search so that Cloudflare Pages and RAG
        always serve the latest bilingual content.

        Source field priority: notes_en (primary pipeline) → content_en (legacy).
        Target field: notes_as (primary pipeline); content_as also written for
        backward-compatibility with legacy reader code that still checks content_as.

        Args:
            chapter_id: The chapter to translate.
            force: When False (default), skip if notes_as or content_as already exists.
                   Set True to re-translate and re-publish.
        """
        chapter = await Chapter.get(PydanticObjectId(chapter_id))
        if not chapter:
            raise ValueError(f"Chapter {chapter_id} not found")

        # Read from notes_en first (primary pipeline field), fall back to content_en
        source_en = (chapter.notes_en or "").strip() or (chapter.content_en or "").strip()
        if not source_en:
            raise ValueError(
                f"Chapter {chapter_id} has no English content to translate "
                "(neither notes_en nor content_en is set)"
            )

        # Skip if we already have a translation in the primary field (notes_as),
        # or in the legacy field (content_as) for chapters pre-dating the migration.
        if not force:
            if (chapter.notes_as and chapter.notes_as.strip()):
                logger.info(
                    f"Skipping Assamese translation for chapter {chapter_id} "
                    f"({chapter.title!r}) — notes_as already present. "
                    "Pass force=True to overwrite."
                )
                return chapter
            if (chapter.content_as and chapter.content_as.strip()):
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

        # Split into ~400-word chunks to protect formatting and output quality.
        words = source_en.split()
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
            part = await workers_ai_client.generate(translate_prompt, chunk, is_assamese=True)
            if part and part.strip():
                translated_parts.append(part.strip())

        notes_as = "\n\n".join(translated_parts)
        # Write to the primary pipeline field (notes_as) AND keep content_as in sync
        # so legacy reader code that still checks content_as continues to work.
        chapter.notes_as = notes_as
        chapter.content_as = notes_as
        chapter.updated_at = datetime.now(timezone.utc)
        await chapter.save()
        logger.info(
            f"Assamese translation saved for {chapter.title!r} "
            f"({len(notes_as.split())} words) → notes_as + content_as"
        )

        # Re-sync GCS + Vertex Search so CF Pages and RAG pick up the Assamese content
        await self._gcs_update(chapter)

        return chapter


content_generation_service = ContentGenerationService()
