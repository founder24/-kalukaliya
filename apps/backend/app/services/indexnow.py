"""
IndexNow Service - Instantly notify search engines when content is published.
Supports: Bing, Yandex, Naver, Seznam (all share the IndexNow protocol).
Google also supports IndexNow via its Indexing API compatibility.
"""

import logging
import httpx
from app.config import settings

logger = logging.getLogger(__name__)

INDEXNOW_ENDPOINTS = [
    "https://api.indexnow.org/indexnow",
    "https://www.bing.com/indexnow",
    "https://yandex.com/indexnow",
]

SITE_URL = "https://syrabit.ai"


async def push_indexnow(urls: list[str]) -> dict:
    """
    Push URLs to IndexNow endpoints for instant discovery.

    Args:
        urls: List of full URLs to notify (e.g., ["https://syrabit.ai/AHSEC/class-12/..."])

    Returns:
        dict with status per endpoint
    """
    key = settings.INDEXNOW_KEY
    if not key:
        logger.warning("INDEXNOW_KEY not configured, skipping IndexNow push")
        return {"status": "skipped", "reason": "no_key"}

    if not urls:
        return {"status": "skipped", "reason": "no_urls"}

    payload = {
        "host": "syrabit.ai",
        "key": key,
        "keyLocation": f"{SITE_URL}/{key}.txt",
        "urlList": urls[:10000],  # IndexNow max 10k per batch
    }

    results = {}
    async with httpx.AsyncClient(timeout=10.0) as client:
        for endpoint in INDEXNOW_ENDPOINTS:
            try:
                response = await client.post(
                    endpoint,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                results[endpoint] = {
                    "status": response.status_code,
                    "ok": response.status_code in (200, 202),
                }
                if response.status_code in (200, 202):
                    logger.info(f"IndexNow push success to {endpoint}: {len(urls)} URLs")
                else:
                    logger.warning(f"IndexNow push to {endpoint}: HTTP {response.status_code}")
            except Exception as e:
                logger.error(f"IndexNow push failed for {endpoint}: {e}")
                results[endpoint] = {"status": "error", "detail": str(e)}

    return {"status": "pushed", "urls_count": len(urls), "endpoints": results}
