"""
Public Content Endpoints
Non-admin endpoints for public-facing content access (FAQ JSON-LD, published topics).
These endpoints do NOT require admin authentication.
"""

from beanie import PydanticObjectId
from fastapi import APIRouter, HTTPException

from app.models.content import Chapter

router = APIRouter(tags=["Public Content"])


@router.get("/content/chapters/{chapter_id}/faq-jsonld")
async def get_faq_jsonld(chapter_id: PydanticObjectId):
    """Return stored FAQ JSON-LD for the chapter (for frontend schema markup)."""
    chapter = await Chapter.get(chapter_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    return {"chapter_id": str(chapter.id), "faq_jsonld": chapter.faq_jsonld or []}


@router.get("/content/chapters/{chapter_id}/published-topics")
async def get_published_topics(chapter_id: PydanticObjectId):
    """Return published topics for TopicAnswerCard rendering."""
    chapter = await Chapter.get(chapter_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    return {
        "chapter_id": str(chapter.id),
        "topics": [t.model_dump() for t in chapter.published_topics],
    }
