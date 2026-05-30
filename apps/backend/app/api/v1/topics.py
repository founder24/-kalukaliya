"""
Public Topic Hub API - Unauthenticated endpoints for topic knowledge hubs.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.models.topic_hub import TopicHub
from app.services.knowledge_graph import knowledge_graph_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/{topic_slug}")
async def get_topic_hub(topic_slug: str, chapter_id: Optional[str] = Query(None)):
    """Get full topic hub data by slug."""
    query = {"topic_slug": topic_slug}
    if chapter_id:
        from beanie import PydanticObjectId

        query["chapter_id"] = PydanticObjectId(chapter_id)

    hub = await TopicHub.find_one(query)
    if not hub:
        raise HTTPException(status_code=404, detail="Topic hub not found")

    return hub.model_dump(by_alias=True)


@router.get("/{topic_slug}/mcqs")
async def get_topic_mcqs(topic_slug: str, chapter_id: Optional[str] = Query(None)):
    """Get MCQs for practice."""
    query = {"topic_slug": topic_slug}
    if chapter_id:
        from beanie import PydanticObjectId

        query["chapter_id"] = PydanticObjectId(chapter_id)

    hub = await TopicHub.find_one(query)
    if not hub:
        raise HTTPException(status_code=404, detail="Topic hub not found")

    return {
        "topic_slug": topic_slug,
        "title": hub.title,
        "mcqs": [mcq.model_dump() for mcq in hub.mcqs],
        "total": len(hub.mcqs),
    }


@router.get("/{topic_slug}/pyqs")
async def get_topic_pyqs(topic_slug: str, chapter_id: Optional[str] = Query(None)):
    """Get Previous Year Questions."""
    query = {"topic_slug": topic_slug}
    if chapter_id:
        from beanie import PydanticObjectId

        query["chapter_id"] = PydanticObjectId(chapter_id)

    hub = await TopicHub.find_one(query)
    if not hub:
        raise HTTPException(status_code=404, detail="Topic hub not found")

    return {
        "topic_slug": topic_slug,
        "title": hub.title,
        "pyqs": [pyq.model_dump() for pyq in hub.pyqs],
        "total": len(hub.pyqs),
    }


@router.get("/{topic_slug}/related")
async def get_related_topics(topic_slug: str):
    """Get knowledge graph neighbors."""
    related = await knowledge_graph_service.get_related_topics(topic_slug)
    return {
        "topic_slug": topic_slug,
        "related": related,
        "total": len(related),
    }


@router.get("/{topic_slug}/study-path")
async def get_study_path(topic_slug: str):
    """Get prerequisite chain (learning path)."""
    chain = await knowledge_graph_service.get_prerequisite_chain(topic_slug)
    return {
        "topic_slug": topic_slug,
        "study_path": chain,
        "total_steps": len(chain),
    }
