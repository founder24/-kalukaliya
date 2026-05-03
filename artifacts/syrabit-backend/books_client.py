"""Google Books API client (volumes:search + volumes:get).

Wraps https://www.googleapis.com/books/v1/volumes for generating richer
source citations on educational content. Particularly useful for cross-
referencing NCERT-aligned topics with publisher metadata (ISBN, authors,
preview links) when building reference sections at the bottom of notes.

Auth: GOOGLE_BOOKS_API_KEY (falls back to GOOGLE_KG_API_KEY). The Books
API also works without a key at a lower rate limit.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

BOOKS_SEARCH_URL = "https://www.googleapis.com/books/v1/volumes"
_HTTP_TIMEOUT_S = 6.0


def _api_key() -> str:
    return (
        (os.environ.get("GOOGLE_BOOKS_API_KEY") or "").strip()
        or (os.environ.get("GOOGLE_KG_API_KEY") or "").strip()
    )


def is_configured() -> bool:
    return True  # public API works without a key


def _format_volume(item: Dict[str, Any]) -> Dict[str, Any]:
    info = item.get("volumeInfo") or {}
    identifiers = {
        i.get("type"): i.get("identifier")
        for i in (info.get("industryIdentifiers") or [])
        if isinstance(i, dict)
    }
    images = info.get("imageLinks") or {}
    return {
        "id": item.get("id"),
        "title": info.get("title"),
        "subtitle": info.get("subtitle"),
        "authors": info.get("authors") or [],
        "publisher": info.get("publisher"),
        "published_date": info.get("publishedDate"),
        "page_count": info.get("pageCount"),
        "categories": info.get("categories") or [],
        "language": info.get("language"),
        "isbn_10": identifiers.get("ISBN_10"),
        "isbn_13": identifiers.get("ISBN_13"),
        "preview_link": info.get("previewLink"),
        "info_link": info.get("infoLink"),
        "canonical_volume_link": info.get("canonicalVolumeLink"),
        "thumbnail": images.get("thumbnail") or images.get("smallThumbnail"),
        "description": info.get("description"),
        "average_rating": info.get("averageRating"),
        "ratings_count": info.get("ratingsCount"),
    }


async def search_volumes(
    query: str,
    *,
    max_results: int = 10,
    start_index: int = 0,
    lang_restrict: Optional[str] = None,
    print_type: str = "all",     # all|books|magazines
    order_by: str = "relevance",  # relevance|newest
    timeout_s: float = _HTTP_TIMEOUT_S,
) -> Dict[str, Any]:
    """Search Google Books for volumes matching `query`.

    Returns: {
        "status": "ok"|"missing"|"error",
        "query": str,
        "volumes": [...formatted volume dicts...],
        "total_items": int,
        "elapsed_ms": float,
        "error": Optional[str],
    }
    """
    q = (query or "").strip()
    if not q:
        return {"status": "error", "query": q, "volumes": [], "total_items": 0,
                "elapsed_ms": 0.0, "error": "empty_query"}

    params: List[tuple] = [
        ("q", q),
        ("maxResults", str(max(1, min(40, int(max_results))))),
        ("startIndex", str(max(0, int(start_index)))),
        ("printType", print_type if print_type in ("all", "books", "magazines") else "all"),
        ("orderBy", order_by if order_by in ("relevance", "newest") else "relevance"),
    ]
    if lang_restrict:
        params.append(("langRestrict", lang_restrict))
    key = _api_key()
    if key:
        params.append(("key", key))

    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.get(BOOKS_SEARCH_URL, params=params)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        if r.status_code != 200:
            return {"status": "error", "query": q, "volumes": [], "total_items": 0,
                    "elapsed_ms": elapsed_ms,
                    "error": f"HTTP {r.status_code}: {r.text[:200]}"}
        data = r.json() or {}
    except httpx.TimeoutException:
        return {"status": "error", "query": q, "volumes": [], "total_items": 0,
                "elapsed_ms": (time.perf_counter() - t0) * 1000.0,
                "error": "timeout"}
    except Exception as exc:
        return {"status": "error", "query": q, "volumes": [], "total_items": 0,
                "elapsed_ms": (time.perf_counter() - t0) * 1000.0,
                "error": f"{type(exc).__name__}: {str(exc)[:200]}"}

    items = data.get("items") or []
    volumes = [_format_volume(it) for it in items]
    if not volumes:
        return {"status": "missing", "query": q, "volumes": [],
                "total_items": int(data.get("totalItems") or 0),
                "elapsed_ms": elapsed_ms, "error": None}
    return {"status": "ok", "query": q, "volumes": volumes,
            "total_items": int(data.get("totalItems") or 0),
            "elapsed_ms": elapsed_ms, "error": None}


async def get_volume(volume_id: str, *, timeout_s: float = _HTTP_TIMEOUT_S) -> Dict[str, Any]:
    """Fetch a single volume by its Google Books ID."""
    vid = (volume_id or "").strip()
    if not vid:
        return {"status": "error", "error": "empty_volume_id"}
    key = _api_key()
    url = f"{BOOKS_SEARCH_URL}/{vid}" + (f"?key={key}" if key else "")
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.get(url)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        if r.status_code != 200:
            return {"status": "error", "elapsed_ms": elapsed_ms,
                    "error": f"HTTP {r.status_code}: {r.text[:200]}"}
        return {"status": "ok", "elapsed_ms": elapsed_ms,
                "volume": _format_volume(r.json() or {})}
    except Exception as exc:
        return {"status": "error",
                "elapsed_ms": (time.perf_counter() - t0) * 1000.0,
                "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
