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

from auth_deps import get_admin_user
import books_client

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
