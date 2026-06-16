"""
ContentPublisherService - Publishes content to Cloudflare and GCS.
"""

import asyncio
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import httpx
from beanie import PydanticObjectId

from app.config import settings
from app.models.content import Chapter
from app.services.indexnow import push_indexnow
from app.services.wikidata import batch_lookup_wikidata

logger = logging.getLogger(__name__)


class ContentPublisherService:
    """Service for publishing chapters to Cloudflare and GCS."""

    async def _resolve_hierarchy(self, chapter: Chapter) -> dict:
        """Resolve full Board > Class > Stream > Subject hierarchy for a chapter."""
        from app.models.content import Board, Class, Stream, Subject

        subject = await Subject.get(chapter.subject_id) if chapter.subject_id else None
        stream = (
            await Stream.get(subject.stream_id)
            if subject and subject.stream_id
            else None
        )
        cls = await Class.get(stream.class_id) if stream and stream.class_id else None
        board = await Board.get(cls.board_id) if cls and cls.board_id else None

        return {"subject": subject, "stream": stream, "cls": cls, "board": board}

    def _chunk_content(self, text: str, max_tokens: int = 512) -> list[str]:
        """Split content into chunks of approximately max_tokens tokens (~4 chars per token)."""
        max_chars = max_tokens * 4
        chunks = []
        while text:
            if len(text) <= max_chars:
                chunks.append(text)
                break
            # Find a good break point
            split_at = text.rfind("\n", 0, max_chars)
            if split_at == -1:
                split_at = text.rfind(" ", 0, max_chars)
            if split_at == -1:
                split_at = max_chars
            chunks.append(text[:split_at])
            text = text[split_at:].lstrip()
        return chunks

    async def publish_to_vertex_search(self, chapter: Chapter) -> dict:
        """Vertex AI Search removed — MongoDB vector search is now the sole RAG backend."""
        return {"status": "skipped", "reason": "vertex_search_removed"}

    async def _publish_to_vertex_search_legacy(self, chapter: Chapter) -> dict:
        """Legacy Vertex AI Search indexing — removed. Stub kept for reference."""
        return {"status": "skipped", "reason": "vertex_search_removed"}


    async def publish_to_cloudflare(self, chapter: Chapter) -> dict:
        """Trigger Cloudflare prerender for the chapter page."""
        cf_url = settings.CF_WORKER_URL
        if not cf_url:
            logger.warning("CF_WORKER_URL not configured, skipping")
            return {"status": "skipped", "reason": "not_configured"}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{cf_url}/api/prerender",
                    json={"slug": chapter.slug},
                )
                response.raise_for_status()
                return {"status": "published", "slug": chapter.slug}
        except Exception as e:
            logger.warning(f"Cloudflare publish failed (non-critical): {e}")
            return {"status": "error", "detail": str(e)}

    async def publish_to_gcs(self, chapter: Chapter) -> dict:
        """Write chapter content to GCS (source of truth for educational content)."""
        if not settings.GOOGLE_APPLICATION_CREDENTIALS_JSON:
            logger.warning("GCS not configured (missing GOOGLE_APPLICATION_CREDENTIALS_JSON), skipping")
            return {"status": "skipped", "reason": "not_configured"}

        try:
            from app.services.content.gcs_store import gcs_content_store

            data = {
                "id": str(chapter.id),
                "title": chapter.title,
                "slug": chapter.slug,
                "content_en": chapter.content_en,
                "content_as": getattr(chapter, "content_as", None),
                "meta_description": chapter.meta_description,
                "keywords": chapter.keywords,
                "published_topics": [
                    t.model_dump() for t in (chapter.published_topics or [])
                ],
                "faq_jsonld": getattr(chapter, "faq_jsonld", None),
                "chapter_number": getattr(chapter, "chapter_number", None),
                "subject_id": str(chapter.subject_id)
                if hasattr(chapter, "subject_id") and chapter.subject_id
                else None,
                "status": chapter.status,
                "updated_at": chapter.updated_at.isoformat()
                if chapter.updated_at
                else None,
            }
            path = await gcs_content_store.write_knowledge_object(chapter.slug, data)
            return {"status": "written", "path": path}
        except Exception as e:
            logger.error(f"GCS write failed for chapter {chapter.slug}: {e}")
            return {"status": "error", "detail": str(e)}

    async def trigger_pages_rebuild(self):
        """Trigger Cloudflare Pages rebuild to regenerate static content."""
        hook_url = settings.CF_PAGES_DEPLOY_HOOK
        if not hook_url:
            logger.warning("CF_PAGES_DEPLOY_HOOK not set, skipping rebuild trigger")
            return
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(hook_url)
            logger.info("Cloudflare Pages rebuild triggered")
        except Exception as e:
            logger.warning(f"Failed to trigger Pages rebuild: {e}")

    async def publish_chapter(self, chapter_id: str) -> dict:
        """Full publish pipeline: GCS + topic embeddings + CF rebuild."""
        chapter = await Chapter.get(PydanticObjectId(chapter_id))
        if not chapter:
            raise ValueError(f"Chapter {chapter_id} not found")

        # 1. Write to GCS (source of truth for educational content)
        gcs_result = await self.publish_to_gcs(chapter)

        # 2. Topic embeddings are generated in step 8; Vertex Search removed.
        search_result = {"status": "skipped", "reason": "vertex_search_removed"}

        # 3. Trigger Cloudflare prerender for the chapter page
        cf_result = await self.publish_to_cloudflare(chapter)

        # 4. Mark published in MongoDB (auth/session store, kept for backward compat)
        chapter.status = "published"
        chapter.updated_at = datetime.now(timezone.utc)
        await chapter.save()

        # 5. Trigger CF Pages rebuild (regenerates static HTML/JSON/XML from GCS)
        await self.trigger_pages_rebuild()

        # 6. IndexNow - instant search engine notification
        chapter_url = f"https://syrabit.ai/{chapter.slug}"
        topic_urls = [
            f"https://syrabit.ai/{chapter.slug}/topic/{t.topic_slug}"
            for t in (chapter.published_topics or [])
        ]
        indexnow_result = await push_indexnow([chapter_url] + topic_urls)

        # 7. Wikidata sameAs enrichment - resolve topic entities
        wikidata_result = {}
        if chapter.published_topics:
            topic_titles = [t.title for t in chapter.published_topics]
            wikidata_uris = await batch_lookup_wikidata(topic_titles)
            # Store sameAs URIs on topic objects for frontend JSON-LD consumption
            updated_topics = False
            for topic in chapter.published_topics:
                uri = wikidata_uris.get(topic.title)
                if uri and not topic.wikidata_uri:
                    topic.wikidata_uri = uri
                    updated_topics = True
            if updated_topics:
                # Persist updated wikidata_uri fields to MongoDB
                await chapter.save()
                wikidata_result = {t: u for t, u in wikidata_uris.items() if u}

        # 8. Generate and store topic embeddings for cosine similarity matching
        hierarchy = await self._resolve_hierarchy(chapter)
        topic_embedding_result = await self._generate_topic_embeddings(
            chapter, hierarchy
        )

        return {
            "chapter_id": chapter_id,
            "status": "published",
            "gcs": gcs_result,
            "search_index": search_result,
            "cloudflare": cf_result,
            "indexnow": indexnow_result,
            "wikidata": wikidata_result,
            "topic_embeddings": topic_embedding_result,
        }

    async def _generate_topic_embeddings(
        self, chapter: Chapter, hierarchy: dict
    ) -> dict:
        """Generate and upsert TopicEmbedding documents for each topic in a chapter."""
        from app.models.content import TopicEmbedding
        from app.services.ai.embedder import generate_embedding_vector

        if not chapter.published_topics:
            return {"status": "no_topics"}

        subject = hierarchy.get("subject")
        board = hierarchy.get("board")
        cls = hierarchy.get("cls")

        subject_slug = subject.name.lower().replace(" ", "-") if subject else ""
        board_slug = board.slug if board else ""
        class_level = cls.name if cls else ""

        generated = 0
        errors = 0

        for topic in chapter.published_topics:
            try:
                embedding = await generate_embedding_vector(topic.title)

                # Upsert by topic_id
                existing = await TopicEmbedding.find_one(
                    TopicEmbedding.topic_id == topic.id
                )
                if existing:
                    existing.topic_title = topic.title
                    existing.chapter_id = chapter.id
                    existing.chapter_title = chapter.title
                    existing.subject_slug = subject_slug
                    existing.board_slug = board_slug
                    existing.class_level = class_level
                    existing.embedding = embedding
                    existing.updated_at = datetime.now(timezone.utc)
                    await existing.save()
                else:
                    doc = TopicEmbedding(
                        topic_id=topic.id,
                        topic_title=topic.title,
                        chapter_id=chapter.id,
                        chapter_title=chapter.title,
                        subject_slug=subject_slug,
                        board_slug=board_slug,
                        class_level=class_level,
                        embedding=embedding,
                    )
                    await doc.insert()
                generated += 1
            except Exception as e:
                logger.error(
                    f"Failed to generate embedding for topic '{topic.title}': {e}"
                )
                errors += 1

        # Invalidate topic matcher cache so new embeddings are picked up
        try:
            from app.services.ai.topic_matcher import topic_matcher

            topic_matcher.invalidate_cache()
        except Exception:
            pass

        return {"status": "generated", "count": generated, "errors": errors}

    async def regenerate_sitemap(self) -> str:
        """Generate sitemap XML from all published chapters."""
        chapters = await Chapter.find({"status": "published"}).to_list(length=None)

        urlset = ET.Element(
            "urlset", xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        )
        for ch in chapters:
            url_el = ET.SubElement(urlset, "url")
            loc = ET.SubElement(url_el, "loc")
            loc.text = f"https://syrabit.ai/{ch.slug}"
            lastmod = ET.SubElement(url_el, "lastmod")
            lastmod.text = ch.updated_at.strftime("%Y-%m-%d")
            priority = ET.SubElement(url_el, "priority")
            priority.text = "0.8"

        xml_str = ET.tostring(urlset, encoding="unicode", xml_declaration=True)
        return xml_str


content_publisher_service = ContentPublisherService()
