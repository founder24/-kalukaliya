"""
Wikidata Lookup Service - Resolve topic titles to Wikidata entity URIs.
Used at publish time to auto-attach sameAs links for Knowledge Graph enrichment.
"""

import asyncio
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

WIKIDATA_SEARCH_URL = "https://www.wikidata.org/w/api.php"


async def lookup_wikidata_uri(topic_title: str) -> Optional[str]:
    """
    Search Wikidata for an entity matching the topic title.

    Args:
        topic_title: The topic name (e.g., "Osmosis", "Photosynthesis")

    Returns:
        Wikidata URI string (e.g., "https://www.wikidata.org/wiki/Q178641") or None
    """
    if not topic_title or len(topic_title) < 3:
        return None

    params = {
        "action": "wbsearchentities",
        "search": topic_title,
        "language": "en",
        "limit": 1,
        "format": "json",
        "type": "item",
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                WIKIDATA_SEARCH_URL,
                params=params,
                headers={
                    "User-Agent": "SyrabitBot/1.0 (https://syrabit.ai; contact@syrabit.ai)"
                },
            )
            if response.status_code != 200:
                return None

            data = response.json()
            results = data.get("search", [])
            if results:
                entity_id = results[0].get("id")  # e.g., "Q178641"
                if entity_id:
                    return f"https://www.wikidata.org/wiki/{entity_id}"
    except Exception as e:
        logger.debug(f"Wikidata lookup failed for '{topic_title}': {e}")

    return None


async def batch_lookup_wikidata(topic_titles: list[str]) -> dict[str, Optional[str]]:
    """
    Look up Wikidata URIs for multiple topics.

    Args:
        topic_titles: List of topic names

    Returns:
        Dict mapping topic_title -> wikidata_uri (or None if not found)
    """
    results = {}

    # Rate limit: max 5 concurrent requests to Wikidata
    semaphore = asyncio.Semaphore(5)

    async def _lookup(title):
        async with semaphore:
            uri = await lookup_wikidata_uri(title)
            results[title] = uri

    tasks = [_lookup(title) for title in topic_titles if title]
    await asyncio.gather(*tasks, return_exceptions=True)

    found = sum(1 for v in results.values() if v)
    logger.info(f"Wikidata batch lookup: {found}/{len(topic_titles)} topics resolved")

    return results
