import hashlib
import logging

import httpx

from app.config import settings
from app.models.knowledge import KnowledgeObject
from app.services.content.renderer import ContentRenderer
from app.services.content.search_indexer import SearchIndexer

logger = logging.getLogger(__name__)


class ContentPipeline:
    """Orchestrates the full publishing pipeline for a KnowledgeObject."""

    def __init__(self):
        self.renderer = ContentRenderer()
        self.indexer = SearchIndexer()

    async def publish(self, slug: str) -> dict:
        """Full publishing pipeline for a KnowledgeObject.

        Steps:
        1. Fetch KnowledgeObject by slug
        2. Generate MCQs/summary/important_questions if missing
        3. Render HTML for all page types
        4. Index in Azure Search
        5. Compute derivative hashes
        6. Submit to IndexNow
        7. Push HTML to Cloudflare KV
        8. Save updated KnowledgeObject

        Returns dict with status of each step.
        """
        result = {
            "slug": slug,
            "steps": {},
        }

        # Step 1: Fetch KnowledgeObject
        try:
            ko = await KnowledgeObject.find_one(KnowledgeObject.slug == slug)
            if not ko:
                result["steps"]["fetch"] = "not_found"
                return result
            result["steps"]["fetch"] = "ok"
        except Exception as e:
            logger.error(f"Failed to fetch KnowledgeObject '{slug}': {e}")
            result["steps"]["fetch"] = f"error: {e}"
            return result

        # Step 2: Generate content if missing
        try:
            updated = False
            if not ko.generated.mcqs:
                ko.generated.mcqs = await self._generate_mcqs(ko)
                updated = True
            if not ko.generated.summary:
                ko.generated.summary = await self._generate_summary(ko)
                updated = True
            if not ko.generated.important_questions:
                ko.generated.important_questions = (
                    await self._generate_important_questions(ko)
                )
                updated = True
            result["steps"]["generate"] = "ok" if updated else "skipped"
        except Exception as e:
            logger.error(f"Failed to generate content for '{slug}': {e}")
            result["steps"]["generate"] = f"error: {e}"

        # Step 3: Render HTML for all page types
        html_pages = {}
        try:
            page_types = [
                "notes",
                "mcqs",
                "summary",
                "definitions",
                "important-questions",
            ]
            for page_type in page_types:
                html_pages[page_type] = self.renderer.render(ko, page_type)
            result["steps"]["render"] = "ok"
        except Exception as e:
            logger.error(f"Failed to render HTML for '{slug}': {e}")
            result["steps"]["render"] = f"error: {e}"

        # Step 4: Index in Azure Search
        try:
            indexed = self.indexer.index(ko)
            result["steps"]["search_index"] = "ok" if indexed else "skipped"
        except Exception as e:
            logger.error(f"Failed to index '{slug}' in Azure Search: {e}")
            result["steps"]["search_index"] = f"error: {e}"

        # Step 5: Compute derivative hashes
        try:
            if html_pages.get("notes"):
                ko.derivatives.html_hash = hashlib.md5(
                    html_pages["notes"].encode()
                ).hexdigest()
            ko.derivatives.search_hash = hashlib.md5(
                ko.content.body_markdown.encode()
            ).hexdigest()
            if ko.generated.mcqs:
                ko.derivatives.mcq_hash = hashlib.md5(
                    str(ko.generated.mcqs).encode()
                ).hexdigest()
            if ko.generated.summary:
                ko.derivatives.summary_hash = hashlib.md5(
                    ko.generated.summary.encode()
                ).hexdigest()
            result["steps"]["hashes"] = "ok"
        except Exception as e:
            logger.error(f"Failed to compute hashes for '{slug}': {e}")
            result["steps"]["hashes"] = f"error: {e}"

        # Step 6: Submit to IndexNow
        try:
            indexnow_result = await self._submit_indexnow(ko)
            result["steps"]["indexnow"] = "ok" if indexnow_result else "skipped"
        except Exception as e:
            logger.error(f"Failed to submit IndexNow for '{slug}': {e}")
            result["steps"]["indexnow"] = f"error: {e}"

        # Step 7: Push HTML to Cloudflare KV
        try:
            kv_result = await self._push_to_cloudflare_kv(ko, html_pages)
            result["steps"]["cloudflare_kv"] = "ok" if kv_result else "skipped"
        except Exception as e:
            logger.error(f"Failed to push to Cloudflare KV for '{slug}': {e}")
            result["steps"]["cloudflare_kv"] = f"error: {e}"

        # Step 8: Save updated KnowledgeObject
        try:
            await ko.save()
            result["steps"]["save"] = "ok"
        except Exception as e:
            logger.error(f"Failed to save KnowledgeObject '{slug}': {e}")
            result["steps"]["save"] = f"error: {e}"

        return result

    async def _generate_mcqs(self, ko: KnowledgeObject) -> list[dict]:
        """Generate MCQs from content (basic extraction from key concepts).

        This is a placeholder - real AI generation will come later.
        """
        mcqs = []
        for concept in ko.content.key_concepts[:5]:
            mcq = {
                "question": f"What is {concept}?",
                "options": [
                    f"A correct definition of {concept}",
                    f"An incorrect statement about {concept}",
                    f"A common misconception about {concept}",
                    f"An unrelated concept",
                ],
                "correct": "a",
                "explanation": f"{concept} is a key concept in this chapter.",
            }
            mcqs.append(mcq)
        return mcqs

    async def _generate_summary(self, ko: KnowledgeObject) -> str:
        """Generate summary from body_markdown (first ~100 words as placeholder)."""
        words = ko.content.body_markdown.split()
        summary_words = words[:100]
        return " ".join(summary_words)

    async def _generate_important_questions(self, ko: KnowledgeObject) -> list[dict]:
        """Extract important questions from prev_year_questions."""
        questions = []
        for pq in ko.content.prev_year_questions:
            questions.append({
                "question": pq.get("question", ""),
                "marks": pq.get("marks", ""),
                "frequency": 1,
            })
        return questions

    async def _submit_indexnow(self, ko: KnowledgeObject) -> bool:
        """Submit URL to IndexNow API."""
        if not settings.INDEXNOW_KEY:
            logger.info("INDEXNOW_KEY not configured - skipping IndexNow submission")
            return False

        url = (
            f"https://syrabit.ai/{ko.board}/{ko.class_level}"
            f"/{ko.subject}/{ko.chapter}"
        )

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    "https://api.indexnow.org/indexnow",
                    json={
                        "host": "syrabit.ai",
                        "key": settings.INDEXNOW_KEY,
                        "urlList": [url],
                    },
                )
                if response.status_code in (200, 202):
                    logger.info(f"IndexNow submitted successfully for {url}")
                    return True
                else:
                    logger.warning(
                        f"IndexNow returned status {response.status_code}: "
                        f"{response.text}"
                    )
                    return False
        except Exception as e:
            logger.error(f"IndexNow submission failed: {e}")
            return False

    async def _push_to_cloudflare_kv(
        self, ko: KnowledgeObject, html_pages: dict
    ) -> bool:
        """Push rendered HTML to Cloudflare KV."""
        if not all([
            settings.CLOUDFLARE_KV_API_TOKEN,
            settings.CLOUDFLARE_ACCOUNT_ID,
            settings.CLOUDFLARE_KV_NAMESPACE_ID,
        ]):
            logger.info(
                "Cloudflare KV credentials not configured - skipping KV push"
            )
            return False

        account_id = settings.CLOUDFLARE_ACCOUNT_ID
        namespace_id = settings.CLOUDFLARE_KV_NAMESPACE_ID
        api_token = settings.CLOUDFLARE_KV_API_TOKEN

        base_key = f"{ko.board}/{ko.class_level}/{ko.subject}/{ko.chapter}"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                for page_type, html in html_pages.items():
                    if page_type == "notes":
                        kv_key = base_key
                    else:
                        kv_key = f"{base_key}/{page_type}"

                    api_url = (
                        f"https://api.cloudflare.com/client/v4/accounts/"
                        f"{account_id}/storage/kv/namespaces/"
                        f"{namespace_id}/values/{kv_key}"
                    )

                    response = await client.put(
                        api_url,
                        content=html.encode("utf-8"),
                        headers={
                            "Authorization": f"Bearer {api_token}",
                            "Content-Type": "text/html",
                        },
                    )

                    if response.status_code != 200:
                        logger.warning(
                            f"Cloudflare KV PUT failed for key '{kv_key}': "
                            f"status={response.status_code}, body={response.text}"
                        )
                        return False

            logger.info(
                f"Pushed {len(html_pages)} pages to Cloudflare KV for {ko.slug}"
            )
            return True

        except Exception as e:
            logger.error(f"Cloudflare KV push failed: {e}")
            return False
