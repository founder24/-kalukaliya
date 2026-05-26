"""
ContentPublisherService - Publishes content to Azure Search and Cloudflare.
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
    """Service for publishing chapters to Azure Search and Cloudflare."""

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

    async def publish_to_azure_search(self, chapter: Chapter) -> dict:
        """Upload chapter content chunks to Azure Search index."""
        if not settings.AZURE_SEARCH_ENDPOINT or not settings.AZURE_SEARCH_ADMIN_KEY:
            logger.warning("Azure Search not configured, skipping")
            return {"status": "skipped", "reason": "not_configured"}

        try:
            from azure.search.documents import SearchClient
            from azure.core.credentials import AzureKeyCredential

            credential = AzureKeyCredential(settings.AZURE_SEARCH_ADMIN_KEY)
            client = SearchClient(
                endpoint=settings.AZURE_SEARCH_ENDPOINT,
                index_name=settings.AZURE_SEARCH_INDEX_NAME,
                credential=credential,
            )

            chunks = self._chunk_content(chapter.content_en or "")
            documents = []
            for i, chunk in enumerate(chunks):
                doc = {
                    "id": f"{str(chapter.id)}_{i}",
                    "chapter_id": str(chapter.id),
                    "title": chapter.title,
                    "slug": chapter.slug,
                    "content": chunk,
                    "chunk_index": i,
                    "meta_description": chapter.meta_description or "",
                    "keywords": chapter.keywords or "",
                }
                documents.append(doc)

            if documents:
                await asyncio.to_thread(
                    client.upload_documents, documents=documents
                )
                return {"status": "uploaded", "chunks": len(documents)}

            return {"status": "no_content"}
        except ImportError:
            logger.warning("azure-search-documents not installed, skipping")
            return {"status": "skipped", "reason": "package_not_installed"}
        except Exception as e:
            logger.error(f"Azure Search upload failed: {e}")
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

    async def publish_chapter(self, chapter_id: str) -> dict:
        """Full publish pipeline: Azure Search + Cloudflare, then mark as published."""
        chapter = await Chapter.get(PydanticObjectId(chapter_id))
        if not chapter:
            raise ValueError(f"Chapter {chapter_id} not found")

        search_result = await self.publish_to_azure_search(chapter)
        cf_result = await self.publish_to_cloudflare(chapter)

        chapter.status = "published"
        chapter.updated_at = datetime.now(timezone.utc)
        await chapter.save()

        return {
            "chapter_id": chapter_id,
            "status": "published",
            "azure_search": search_result,
            "cloudflare": cf_result,
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
