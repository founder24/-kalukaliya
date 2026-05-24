"""
Public Content API - Unauthenticated endpoints for FAQ JSON-LD and published topics.
"""

import logging

from beanie import PydanticObjectId
from fastapi import APIRouter, HTTPException

from app.models.content import Chapter

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/chapters/{chapter_id}/faq-jsonld")
async def get_faq_jsonld(chapter_id: str):
    """Get FAQ JSON-LD structured data for a chapter (no auth required)."""
    chapter = await Chapter.get(PydanticObjectId(chapter_id))
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    return {"chapter_id": chapter_id, "faq_jsonld": chapter.faq_jsonld or []}


@router.get("/chapters/{chapter_id}/published-topics")
async def get_published_topics(chapter_id: str):
    """Get published topics for a chapter (no auth required)."""
    chapter = await Chapter.get(PydanticObjectId(chapter_id))
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    return {
        "chapter_id": chapter_id,
        "topics": [t.model_dump() for t in chapter.published_topics],
        "total": len(chapter.published_topics),
    }
