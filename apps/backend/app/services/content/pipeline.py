"""
ContentPipeline - Orchestrates the content generation and publishing workflow.
Steps: fetch -> generate MCQs/summary -> render HTML -> index search ->
       compute hashes -> submit IndexNow -> push Cloudflare KV -> save.
Each step is fail-soft with logging.
"""

import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from app.config import settings
from app.services.content.renderer import content_renderer, PAGE_TYPES
from app.services.content.search_indexer import search_indexer

logger = logging.getLogger(__name__)


class ContentPipeline:
    """Orchestrates the content publishing pipeline."""

    async def run(self, knowledge_obj) -> dict:
        """
        Execute the full pipeline for a knowledge object.

        Returns:
            dict with status of each step
        """
        results = {
            "render": False,
            "search_index": False,
            "hashes_updated": False,
            "indexnow": False,
            "cloudflare_kv": False,
            "saved": False,
        }

        # Step 1: Render HTML for all page types
        try:
            rendered = {}
            for page_type in PAGE_TYPES:
                html = content_renderer.render(knowledge_obj, page_type)
                rendered[page_type] = html
            knowledge_obj.rendered_html = rendered
            results["render"] = True
            logger.info(f"Rendered all page types for slug={knowledge_obj.slug}")
        except Exception as e:
            logger.error(f"Render failed for slug={knowledge_obj.slug}: {e}")

        # Step 2: Index to Azure AI Search
        try:
            indexed = await search_indexer.index_knowledge_object(knowledge_obj)
            results["search_index"] = indexed
        except Exception as e:
            logger.error(f"Search indexing failed for slug={knowledge_obj.slug}: {e}")

        # Step 3: Compute derivative hashes
        try:
            if results["render"]:
                from app.models.knowledge import DerivativeHashes

                hashes = DerivativeHashes(
                    notes_html=_hash(rendered.get("notes", "")),
                    mcqs_html=_hash(rendered.get("mcqs", "")),
                    summary_html=_hash(rendered.get("summary", "")),
                    definitions_html=_hash(rendered.get("definitions", "")),
                    important_questions_html=_hash(
                        rendered.get("important-questions", "")
                    ),
                    search_index=_hash(knowledge_obj.body_markdown),
                )
                knowledge_obj.derivative_hashes = hashes
                results["hashes_updated"] = True
        except Exception as e:
            logger.error(f"Hash computation failed for slug={knowledge_obj.slug}: {e}")

        # Step 4: Submit IndexNow
        try:
            indexnow_ok = await self._submit_indexnow(knowledge_obj)
            results["indexnow"] = indexnow_ok
        except Exception as e:
            logger.error(f"IndexNow failed for slug={knowledge_obj.slug}: {e}")

        # Step 5: Push to Cloudflare KV
        try:
            kv_ok = await self._push_cloudflare_kv(knowledge_obj)
            results["cloudflare_kv"] = kv_ok
        except Exception as e:
            logger.error(f"Cloudflare KV push failed for slug={knowledge_obj.slug}: {e}")

        # Step 6: Save to database
        try:
            knowledge_obj.last_pipeline_run = datetime.now(timezone.utc)
            knowledge_obj.updated_at = datetime.now(timezone.utc)
            await knowledge_obj.save()
            results["saved"] = True
            logger.info(f"Pipeline complete for slug={knowledge_obj.slug}")
        except Exception as e:
            logger.error(f"Save failed for slug={knowledge_obj.slug}: {e}")

        return results

    async def _submit_indexnow(self, knowledge_obj) -> bool:
        """Submit URLs to IndexNow for rapid search engine indexing."""
        api_key = settings.INDEXNOW_API_KEY
        if not api_key:
            logger.info("IndexNow API key not configured - skipping")
            return False

        meta = knowledge_obj.metadata
        base_url = "https://syrabit.ai"
        urls = []
        for page_type in PAGE_TYPES:
            url = f"{base_url}/render/{meta.board}/{meta.class_level}/{meta.subject}/{meta.chapter}/{page_type}"
            urls.append(url)

        payload = {
            "host": "syrabit.ai",
            "key": api_key,
            "keyLocation": f"{base_url}/api/v1/indexnow/{api_key}.txt",
            "urlList": urls,
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    "https://api.indexnow.org/indexnow",
                    json=payload,
                )
                if resp.status_code in (200, 202):
                    logger.info(
                        f"IndexNow submitted {len(urls)} URLs for slug={knowledge_obj.slug}"
                    )
                    return True
                else:
                    logger.warning(
                        f"IndexNow returned {resp.status_code} for slug={knowledge_obj.slug}"
                    )
                    return False
        except httpx.HTTPError as e:
            logger.error(f"IndexNow HTTP error: {e}")
            return False

    async def _push_cloudflare_kv(self, knowledge_obj) -> bool:
        """Push rendered HTML to Cloudflare KV for edge caching."""
        token = settings.CLOUDFLARE_KV_API_TOKEN
        account_id = settings.CLOUDFLARE_ACCOUNT_ID
        namespace_id = settings.CLOUDFLARE_KV_NAMESPACE_ID

        if not all([token, account_id, namespace_id]):
            logger.info("Cloudflare KV not configured - skipping")
            return False

        meta = knowledge_obj.metadata
        base_key = f"{meta.board}/{meta.class_level}/{meta.subject}/{meta.chapter}"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                }
                kv_url = (
                    f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
                    f"/storage/kv/namespaces/{namespace_id}/bulk"
                )

                kv_pairs = []
                for page_type, html in knowledge_obj.rendered_html.items():
                    kv_pairs.append(
                        {
                            "key": f"{base_key}/{page_type}",
                            "value": html,
                        }
                    )

                resp = await client.put(
                    kv_url,
                    json=kv_pairs,
                    headers=headers,
                )

                if resp.status_code == 200:
                    logger.info(
                        f"Pushed {len(kv_pairs)} KV entries for slug={knowledge_obj.slug}"
                    )
                    return True
                else:
                    logger.warning(
                        f"Cloudflare KV returned {resp.status_code}: {resp.text[:200]}"
                    )
                    return False
        except httpx.HTTPError as e:
            logger.error(f"Cloudflare KV HTTP error: {e}")
            return False


def _hash(content: str) -> str:
    """Compute SHA256 hash of content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


# Singleton
content_pipeline = ContentPipeline()
