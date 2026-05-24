from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from app.models.knowledge import KnowledgeObject
from app.services.content.renderer import ContentRenderer
import logging

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Content"])
renderer = ContentRenderer()


@router.get(
    "/render/{board}/{class_level}/{subject}/{chapter}", response_class=HTMLResponse
)
async def render_chapter(board: str, class_level: str, subject: str, chapter: str):
    """Returns rendered HTML notes page for a chapter. Used by ISR/edge worker."""
    ko = await KnowledgeObject.find_one(
        KnowledgeObject.board == board,
        KnowledgeObject.class_level == class_level,
        KnowledgeObject.subject == subject,
        KnowledgeObject.chapter == chapter,
        KnowledgeObject.is_published == True,
    )
    if not ko:
        raise HTTPException(status_code=404, detail="Content not found")

    # Use cached generated HTML if available, otherwise render on-the-fly
    html = renderer.render(ko, "notes")
    return HTMLResponse(
        content=html,
        headers={
            "Cache-Control": "public, max-age=3600, s-maxage=86400, stale-while-revalidate=3600"
        },
    )


@router.get(
    "/render/{board}/{class_level}/{subject}/{chapter}/{page_type}",
    response_class=HTMLResponse,
)
async def render_chapter_page_type(
    board: str, class_level: str, subject: str, chapter: str, page_type: str
):
    """Returns rendered HTML for specific page type (mcqs/summary/definitions/important-questions)."""
    valid_types = ["mcqs", "summary", "definitions", "important-questions"]
    if page_type not in valid_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid page type. Must be one of: {valid_types}",
        )

    ko = await KnowledgeObject.find_one(
        KnowledgeObject.board == board,
        KnowledgeObject.class_level == class_level,
        KnowledgeObject.subject == subject,
        KnowledgeObject.chapter == chapter,
        KnowledgeObject.is_published == True,
    )
    if not ko:
        raise HTTPException(status_code=404, detail="Content not found")

    html = renderer.render(ko, page_type)
    return HTMLResponse(
        content=html,
        headers={
            "Cache-Control": "public, max-age=3600, s-maxage=86400, stale-while-revalidate=3600"
        },
    )


@router.get("/subject/{board}/{class_level}/{subject}")
async def list_chapters(board: str, class_level: str, subject: str):
    """List all published chapters for a subject."""
    chapters = await KnowledgeObject.find(
        KnowledgeObject.board == board,
        KnowledgeObject.class_level == class_level,
        KnowledgeObject.subject == subject,
        KnowledgeObject.is_published == True,
    ).to_list()

    return {
        "board": board,
        "class_level": class_level,
        "subject": subject,
        "chapters": [
            {
                "slug": ch.slug,
                "chapter": ch.chapter,
                "topic": ch.topic,
                "difficulty": ch.metadata.difficulty,
                "estimated_read_time_min": ch.metadata.estimated_read_time_min,
            }
            for ch in chapters
        ],
        "total": len(chapters),
    }


@router.get("/{slug}")
async def get_content_json(slug: str):
    """Returns KnowledgeObject JSON for frontend hydration."""
    ko = await KnowledgeObject.find_one(KnowledgeObject.slug == slug)
    if not ko:
        raise HTTPException(status_code=404, detail="Content not found")

    return ko.dict(by_alias=True)
