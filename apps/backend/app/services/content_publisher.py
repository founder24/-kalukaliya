"""
ContentPublisherService - Publishes content to Vertex AI Search, GCS, and Cloudflare.
"""

import asyncio
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import httpx
from beanie import PydanticObjectId

from app.config import settings
from app.models.content import Chapter

logger = logging.getLogger(__name__)


class ContentPublisherService:
    """Service for publishing chapters to Vertex AI Search and Cloudflare."""

    def __init__(self):
        self._vertex_client = None

    def _get_vertex_client(self):
        """Lazily initialize and cache the DocumentServiceClient."""
        if self._vertex_client is None:
            from google.cloud import discoveryengine_v1
            from google.oauth2 import service_account

            credentials = service_account.Credentials.from_service_account_info(
                settings.google_credentials,
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
            )
            self._vertex_client = discoveryengine_v1.DocumentServiceClient(
                credentials=credentials,
            )
        return self._vertex_client

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
        """Upload chapter content chunks to Vertex AI Search (Discovery Engine)."""
        if (
            not settings.VERTEX_PROJECT_ID
            or not settings.GOOGLE_APPLICATION_CREDENTIALS_JSON
            or not settings.VERTEX_SEARCH_DATASTORE_ID
        ):
            logger.warning("Vertex AI Search not configured, skipping")
            return {"status": "skipped", "reason": "not_configured"}

        try:
            from google.cloud import discoveryengine_v1
            from google.protobuf import struct_pb2

            client = self._get_vertex_client()

            parent = client.branch_path(
                project=settings.VERTEX_PROJECT_ID,
                location=settings.VERTEX_SEARCH_LOCATION,
                data_store=settings.VERTEX_SEARCH_DATASTORE_ID,
                branch="default_branch",
            )

            chunks = self._chunk_content(chapter.content_en or "")
            documents = []
            for i, chunk in enumerate(chunks):
                doc_id = f"{str(chapter.id)}_{i}"
                struct_data = struct_pb2.Struct()
                struct_data.update(
                    {
                        "chapter_id": str(chapter.id),
                        "title": chapter.title,
                        "slug": chapter.slug,
                        "content": chunk,
                        "chunk_index": i,
                        "meta_description": chapter.meta_description or "",
                        "keywords": chapter.keywords or "",
                    }
                )
                doc = discoveryengine_v1.Document(
                    id=doc_id,
                    struct_data=struct_data,
                )
                documents.append(doc)

            if documents:
                for doc in documents:
                    doc.name = f"{parent}/documents/{doc.id}"
                    request = discoveryengine_v1.UpdateDocumentRequest(
                        document=doc,
                        allow_missing=True,
                    )
                    await asyncio.to_thread(client.update_document, request=request)
                return {"status": "uploaded", "chunks": len(documents)}

            return {"status": "no_content"}
        except ImportError:
            logger.warning("google-cloud-discoveryengine not installed, skipping")
            return {"status": "skipped", "reason": "package_not_installed"}
        except Exception as e:
            logger.error(f"Vertex AI Search upload failed: {e}")
            return {"status": "error", "detail": str(e)}

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
            logger.error(f"Cloudflare publish failed: {e}")
            return {"status": "error", "detail": str(e)}

    async def publish_to_gcs(self, chapter: Chapter) -> dict:
        """Serialize chapter data and write to GCS as a knowledge object."""
        from app.services.content.gcs_store import gcs_content_store

        if not gcs_content_store._configured:
            logger.warning("GCS content store not configured, skipping GCS publish")
            return {"status": "skipped", "reason": "not_configured"}

        try:
            data = {
                "id": str(chapter.id),
                "title": chapter.title,
                "slug": chapter.slug,
                "subject_id": str(chapter.subject_id),
                "chapter_number": chapter.chapter_number,
                "content_en": chapter.content_en,
                "meta_description": chapter.meta_description,
                "keywords": chapter.keywords,
                "published_topics": [
                    t.model_dump() for t in (chapter.published_topics or [])
                ],
                "status": chapter.status,
                "updated_at": chapter.updated_at.isoformat()
                if chapter.updated_at
                else None,
            }
            await gcs_content_store.write_knowledge_object(str(chapter.id), data)
            return {"status": "uploaded"}
        except Exception as e:
            logger.error(f"GCS publish failed for chapter {chapter.id}: {e}")
            return {"status": "error", "detail": str(e)}

    async def trigger_cloudflare_rebuild(self) -> dict:
        """POST to Cloudflare Pages deploy hook to trigger a site rebuild."""
        hook_url = settings.CF_PAGES_DEPLOY_HOOK
        if not hook_url:
            logger.info("CF_PAGES_DEPLOY_HOOK not configured, skipping rebuild")
            return {"status": "skipped", "reason": "not_configured"}

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(hook_url)
                response.raise_for_status()
                logger.info("Cloudflare Pages rebuild triggered successfully")
                return {"status": "triggered"}
        except Exception as e:
            logger.error(f"Cloudflare rebuild trigger failed: {e}")
            return {"status": "error", "detail": str(e)}

    async def publish_chapter(self, chapter_id: str) -> dict:
        """Full publish pipeline: GCS + Vertex AI Search + Cloudflare, then mark as published."""
        chapter = await Chapter.get(PydanticObjectId(chapter_id))
        if not chapter:
            raise ValueError(f"Chapter {chapter_id} not found")

        # 1. Write to GCS (fail-soft)
        gcs_result = await self.publish_to_gcs(chapter)

        # 2. Index in Vertex AI Search (fail-soft)
        search_result = await self.publish_to_vertex_search(chapter)

        # 3. Publish to Cloudflare worker (fail-soft)
        cf_result = await self.publish_to_cloudflare(chapter)

        # 4. Mark as published in MongoDB
        chapter.status = "published"
        chapter.updated_at = datetime.now(timezone.utc)
        await chapter.save()

        # 5. Trigger Cloudflare Pages rebuild (fail-soft)
        rebuild_result = await self.trigger_cloudflare_rebuild()

        return {
            "chapter_id": chapter_id,
            "status": "published",
            "gcs": gcs_result,
            "vertex_search": search_result,
            "cloudflare": cf_result,
            "cf_rebuild": rebuild_result,
        }

    async def regenerate_sitemap(self) -> str:
        """Generate sitemap XML from all published chapters."""
        chapters = await Chapter.find({"status": "published"}).to_list()

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
