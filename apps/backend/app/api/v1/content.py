"""
Public Content API - Renders and serves educational content pages.
Supports ISR (Incremental Static Regeneration) via Cache-Control headers.
"""

import logging
import re

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

from app.models.knowledge import KnowledgeObject
from app.services.content.renderer import content_renderer, PAGE_TYPES

logger = logging.getLogger(__name__)

router = APIRouter()

SAFE_PATH_RE = re.compile(r"^[a-zA-Z0-9-]+$")

# Cache-Control for CDN: 60s stale-while-revalidate, 1 hour max
ISR_CACHE_HEADER = "public, max-age=60, s-maxage=3600, stale-while-revalidate=3600"


def _validate_path_params(**params: str) -> None:
    """Validate path parameters against the safe pattern to prevent NoSQL injection."""
    for name, value in params.items():
        if not SAFE_PATH_RE.match(value):
            raise HTTPException(status_code=400, detail="Invalid path parameter")


@router.get(
    "/render/{board}/{class_level}/{subject}/{chapter}",
    response_class=HTMLResponse,
    summary="Render chapter notes page (default page type)",
)
async def render_chapter(
    board: str,
    class_level: str,
    subject: str,
    chapter: str,
):
    """Render the default (notes) page for a chapter."""
    _validate_path_params(
        board=board, class_level=class_level, subject=subject, chapter=chapter
    )
    obj = await KnowledgeObject.find_one(
        {
            "metadata.board": board,
            "metadata.class_level": class_level,
            "metadata.subject": subject,
            "metadata.chapter": chapter,
            "status": "published",
        }
    )
    if not obj:
        raise HTTPException(status_code=404, detail="Content not found")

    # Use cached HTML if available
    if "notes" in obj.rendered_html:
        html = obj.rendered_html["notes"]
    else:
        html = content_renderer.render(obj, "notes")
        # Cache-aside: persist rendered HTML for future requests
        try:
            if obj.rendered_html is None:
                obj.rendered_html = {}
            obj.rendered_html["notes"] = html
            await obj.save()
        except Exception as e:
            logger.warning(f"Failed to cache rendered HTML: {e}")

    return HTMLResponse(
        content=html,
        headers={"Cache-Control": ISR_CACHE_HEADER},
    )


@router.get(
    "/render/{board}/{class_level}/{subject}/{chapter}/{page_type}",
    response_class=HTMLResponse,
    summary="Render chapter page by type",
)
async def render_chapter_page_type(
    board: str,
    class_level: str,
    subject: str,
    chapter: str,
    page_type: str,
):
    """Render a specific page type for a chapter."""
    _validate_path_params(
        board=board, class_level=class_level, subject=subject, chapter=chapter
    )
    if page_type not in PAGE_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid page_type. Must be one of: {PAGE_TYPES}",
        )

    obj = await KnowledgeObject.find_one(
        {
            "metadata.board": board,
            "metadata.class_level": class_level,
            "metadata.subject": subject,
            "metadata.chapter": chapter,
            "status": "published",
        }
    )
    if not obj:
        raise HTTPException(status_code=404, detail="Content not found")

    # Use cached HTML if available
    if page_type in obj.rendered_html:
        html = obj.rendered_html[page_type]
    else:
        html = content_renderer.render(obj, page_type)
        # Cache-aside: persist rendered HTML for future requests
        try:
            if obj.rendered_html is None:
                obj.rendered_html = {}
            obj.rendered_html[page_type] = html
            await obj.save()
        except Exception as e:
            logger.warning(f"Failed to cache rendered HTML: {e}")

    return HTMLResponse(
        content=html,
        headers={"Cache-Control": ISR_CACHE_HEADER},
    )


@router.get(
    "/subject/{board}/{class_level}/{subject}",
    summary="List chapters for a subject",
)
async def list_chapters(
    board: str,
    class_level: str,
    subject: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    """List all published chapters for a given board/class/subject."""
    _validate_path_params(board=board, class_level=class_level, subject=subject)
    chapters = (
        await KnowledgeObject.find(
            {
                "metadata.board": board,
                "metadata.class_level": class_level,
                "metadata.subject": subject,
                "status": "published",
            }
        )
        .project(
            {
                "_id": 0,
                "slug": 1,
                "title": 1,
                "description": 1,
                "metadata.chapter": 1,
                "metadata.chapter_number": 1,
                "metadata.difficulty": 1,
                "metadata.estimated_read_time_minutes": 1,
            }
        )
        .skip(skip)
        .limit(limit)
        .to_list()
    )

    return {
        "board": board,
        "class_level": class_level,
        "subject": subject,
        "chapters": chapters,
        "count": len(chapters),
    }


@router.get(
    "/{slug}",
    summary="Get knowledge object by slug",
)
async def get_by_slug(slug: str):
    """Get a published knowledge object by slug (excludes derivatives and page_views)."""
    _validate_path_params(slug=slug)
    obj = await KnowledgeObject.find_one({"slug": slug, "status": "published"})
    if not obj:
        raise HTTPException(status_code=404, detail="Content not found")

    # Exclude heavy/private fields
    data = obj.model_dump(
        exclude={
            "rendered_html",
            "derivative_hashes",
            "page_views",
            "search_impressions",
        }
    )
    return data
