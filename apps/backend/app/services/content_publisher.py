"""Content publishing service for Azure Search indexing and Cloudflare cache."""

import asyncio
import logging
from datetime import datetime, timezone

import httpx
from azure.search.documents import SearchClient
from azure.core.credentials import AzureKeyCredential
from beanie import PydanticObjectId

from app.config import settings
from app.models.content import Chapter

logger = logging.getLogger(__name__)

# Approximate tokens per chunk target
CHUNK_TOKEN_LIMIT = 512


def _chunk_content(content: str) -> list[str]:
    """
    Split content into ~512-token segments by paragraphs.

    Strategy: split by double newlines (paragraphs), then merge adjacent
    paragraphs until reaching the token limit.
    """
    if not content:
        return []

    paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
    chunks = []
    current_chunk = ""

    for para in paragraphs:
        # Rough token estimate: ~4 chars per token
        current_tokens = len(current_chunk) // 4
        para_tokens = len(para) // 4

        if current_tokens + para_tokens > CHUNK_TOKEN_LIMIT and current_chunk:
            chunks.append(current_chunk.strip())
            current_chunk = para
        else:
            if current_chunk:
                current_chunk += "\n\n" + para
            else:
                current_chunk = para

    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    return chunks


class ContentPublisherService:
    """Handles publishing content to Azure Search and Cloudflare."""

    async def publish_to_azure_search(self, chapter) -> dict:
        """
        Chunk chapter.content_en into ~512-token segments and upload to Azure Search.

        Each chunk becomes a document with: id, title, content, tier_access,
        language, source_url, last_updated.
        """
        if not settings.AZURE_SEARCH_ENDPOINT or not settings.AZURE_SEARCH_ADMIN_KEY:
            raise RuntimeError(
                "Azure Search not configured: AZURE_SEARCH_ENDPOINT and "
                "AZURE_SEARCH_ADMIN_KEY are required for publishing"
            )

        if not chapter.content_en:
            raise RuntimeError(
                f"Chapter {chapter.id} has no English content to publish"
            )

        chunks = _chunk_content(chapter.content_en)
        if not chunks:
            raise RuntimeError(
                f"Chapter {chapter.id} content produced no chunks"
            )

        # Build source URL from slug
        source_url = f"https://syrabit.ai/chapters/{chapter.slug}"

        # Prepare documents for upload
        documents = []
        for idx, chunk_text in enumerate(chunks):
            doc = {
                "id": f"{str(chapter.id)}-{idx}",
                "title": chapter.title,
                "content": chunk_text,
                "tier_access": "free",
                "language": "en",
                "source_url": source_url,
                "last_updated": datetime.now(timezone.utc).isoformat(),
            }
            documents.append(doc)

        # Upload using admin client (sync client in thread)
        search_client = SearchClient(
            endpoint=settings.AZURE_SEARCH_ENDPOINT,
            index_name=settings.AZURE_SEARCH_INDEX_NAME,
            credential=AzureKeyCredential(settings.AZURE_SEARCH_ADMIN_KEY),
        )

        try:
            result = await asyncio.to_thread(
                search_client.upload_documents, documents=documents
            )
            succeeded = sum(1 for r in result if r.succeeded)
            failed = sum(1 for r in result if not r.succeeded)

            logger.info(
                f"Published {succeeded} chunks to Azure Search for chapter "
                f"{chapter.id} ({failed} failed)"
            )

            return {
                "status": "indexed",
                "chunks_total": len(chunks),
                "chunks_succeeded": succeeded,
                "chunks_failed": failed,
                "index_name": settings.AZURE_SEARCH_INDEX_NAME,
            }
        finally:
            search_client.close()

    async def publish_to_cloudflare(self, chapter) -> dict:
        """
        Trigger Cloudflare cache invalidation/prerender for the chapter URL.

        Makes HTTP POST to CF_WORKER_URL/api/prerender with the chapter slug.
        """
        if not settings.CF_WORKER_URL:
            raise RuntimeError(
                "Cloudflare Worker URL not configured (CF_WORKER_URL is empty)"
            )

        source_url = f"https://syrabit.ai/chapters/{chapter.slug}"

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(
                    f"{settings.CF_WORKER_URL}/api/prerender",
                    json={
                        "url": source_url,
                        "slug": chapter.slug,
                    },
                )
                response.raise_for_status()

                logger.info(
                    f"Triggered Cloudflare prerender for chapter {chapter.id}: "
                    f"{chapter.slug}"
                )

                return {
                    "status": "prerender_triggered",
                    "url": source_url,
                    "cf_status_code": response.status_code,
                }
            except httpx.HTTPStatusError as e:
                logger.error(
                    f"Cloudflare prerender failed: HTTP {e.response.status_code}"
                )
                return {
                    "status": "prerender_failed",
                    "url": source_url,
                    "error": f"HTTP {e.response.status_code}",
                }
            except Exception as e:
                logger.error(f"Cloudflare prerender error: {e}")
                return {
                    "status": "prerender_failed",
                    "url": source_url,
                    "error": str(e),
                }

    async def publish_chapter(self, chapter_id: str) -> dict:
        """
        Full publish pipeline: index to Azure Search + Cloudflare prerender.

        Sets chapter.status = 'published' on success.
        If Azure Search succeeds but Cloudflare fails, status is still set to
        'published' but the response includes a warning and clearly indicates
        which services succeeded or failed.
        """
        chapter = await Chapter.get(PydanticObjectId(chapter_id))
        if not chapter:
            raise RuntimeError(f"Chapter not found: {chapter_id}")

        if not chapter.content_en:
            raise RuntimeError(
                f"Chapter {chapter_id} has no English content. "
                "Generate content before publishing."
            )

        # Publish to Azure Search (raises on failure - correct behavior)
        search_result = await self.publish_to_azure_search(chapter)

        # Publish to Cloudflare (returns error dict on failure, does not raise)
        cf_result = await self.publish_to_cloudflare(chapter)

        # Determine if Cloudflare succeeded or failed
        cf_failed = cf_result.get("status") == "prerender_failed"

        # Update chapter status - always set to published since Azure succeeded
        chapter.status = "published"
        chapter.updated_at = datetime.now(timezone.utc)
        await chapter.save()

        # Build response with clear service status indicators
        warnings = []
        if cf_failed:
            cf_error = cf_result.get("error", "unknown error")
            logger.warning(
                f"Chapter {chapter_id} published to Azure Search but "
                f"Cloudflare prerender failed: {cf_error}"
            )
            warnings.append(
                f"Cloudflare prerender failed: {cf_error}. "
                "Content is indexed but not prerendered at the edge."
            )
        else:
            logger.info(f"Published chapter {chapter_id} successfully")

        response = {
            "chapter_id": str(chapter.id),
            "status": "published",
            "services": {
                "azure_search": "success",
                "cloudflare": "failed" if cf_failed else "success",
            },
            "azure_search": search_result,
            "cloudflare": cf_result,
        }

        if warnings:
            response["warnings"] = warnings

        return response

    async def regenerate_sitemap(self) -> dict:
        """
        Generate sitemap XML from all published chapters.

        Returns the sitemap XML string and count of entries.
        """
        chapters = await Chapter.find({"status": "published"}).to_list()

        xml_entries = []
        for ch in chapters:
            url = f"https://syrabit.ai/chapters/{ch.slug}"
            lastmod = ch.updated_at.strftime("%Y-%m-%d") if ch.updated_at else ""
            xml_entries.append(
                f"  <url>\n"
                f"    <loc>{url}</loc>\n"
                f"    <lastmod>{lastmod}</lastmod>\n"
                f"    <changefreq>weekly</changefreq>\n"
                f"    <priority>0.8</priority>\n"
                f"  </url>"
            )

        sitemap_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + "\n".join(xml_entries)
            + "\n</urlset>"
        )

        logger.info(f"Regenerated sitemap with {len(chapters)} published chapters")

        return {
            "status": "generated",
            "entries_count": len(chapters),
            "sitemap_xml": sitemap_xml,
        }
