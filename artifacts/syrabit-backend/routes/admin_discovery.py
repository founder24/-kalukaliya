"""Admin endpoints for Google Books API (Phase 2: Discovery / citations).

  GET /api/admin/discovery/books/search?query=&max_results=&lang=
  GET /api/admin/discovery/books/volume/{volume_id}

Discovery Engine (Vertex AI Search) is NOT wired here because it requires
a Google service account JSON. Provide GOOGLE_APPLICATION_CREDENTIALS_JSON
to enable.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query

from fastapi import Body
from auth_deps import get_admin_user
import books_client
import discovery_engine_client

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/admin/discovery/books/search")
async def admin_books_search(
    query: str = Query(..., min_length=1, max_length=300),
    max_results: int = Query(10, ge=1, le=40),
    start_index: int = Query(0, ge=0),
    lang: Optional[str] = Query(None, description="ISO-639-1 e.g. 'en','hi','as'."),
    print_type: str = Query("all", description="all|books|magazines"),
    order_by: str = Query("relevance", description="relevance|newest"),
    admin: dict = Depends(get_admin_user),
):
    """Search Google Books for citation-quality references."""
    return await books_client.search_volumes(
        query, max_results=max_results, start_index=start_index,
        lang_restrict=lang, print_type=print_type, order_by=order_by,
    )


@router.get("/admin/discovery/books/volume/{volume_id}")
async def admin_books_volume(
    volume_id: str,
    admin: dict = Depends(get_admin_user),
):
    """Fetch a single Google Books volume by ID."""
    return await books_client.get_volume(volume_id)


@router.post("/admin/discovery/engine/search")
async def admin_discovery_engine_search(
    payload: dict = Body(...),
    admin: dict = Depends(get_admin_user),
):
    """Run a Vertex AI Search (Discovery Engine) query.

    Body: {"query": str, "page_size"?: int, "data_store"?: str,
           "location"?: str, "collection"?: str, "serving_config"?: str}
    Falls back to env vars (GCP_DISCOVERY_*) when fields omitted.
    """
    query = (payload.get("query") or "").strip()
    if not query:
        return {"status": "error", "error": "query required"}
    raw_page_size = payload.get("page_size")
    try:
        page_size = int(raw_page_size) if raw_page_size is not None else 10
    except (TypeError, ValueError):
        return {"status": "error",
                "error": f"page_size must be an integer, got {raw_page_size!r}"}
    page_size = max(1, min(50, page_size))
    return await discovery_engine_client.search(
        query,
        page_size=page_size,
        data_store=payload.get("data_store"),
        location=payload.get("location"),
        collection=payload.get("collection"),
        serving_config=payload.get("serving_config"),
    )
