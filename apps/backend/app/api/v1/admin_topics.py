"""
Admin Topic Hub API - Authenticated endpoints for managing topic knowledge hubs.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from beanie import PydanticObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.api.v1.admin import _validate_admin_session, _csrf_check
from app.models.content import Chapter
from app.models.topic_hub import TopicHub, TopicSource
from app.services.authority_generator import authority_generator_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin Topics"])


def _parse_object_id(value: str) -> PydanticObjectId:
    """Validate and parse an ObjectId string, raising 400 on invalid input."""
    try:
        return PydanticObjectId(value)
    except (InvalidId, Exception):
        raise HTTPException(status_code=400, detail="Invalid ID format")


# --- Request Models ---


class GenerateHubRequest(BaseModel):
    chapter_id: str
    topic_slug: str


class GenerateMCQsRequest(BaseModel):
    count: int = 5


class AddSourceRequest(BaseModel):
    source_type: str
    title: str
    url: Optional[str] = None
    year: Optional[int] = None
    description: Optional[str] = None


# --- Endpoints ---


@router.post("/topics/generate-hub")
async def generate_topic_hub(request: Request, body: GenerateHubRequest):
    """Create a TopicHub from an existing Topic in a Chapter's published_topics."""
    _validate_admin_session(request)
    await _csrf_check(request)

    chapter = await Chapter.get(PydanticObjectId(body.chapter_id))
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    # Find the topic in published_topics
    topic = None
    for t in chapter.published_topics:
        if t.topic_slug == body.topic_slug:
            topic = t
            break

    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found in chapter")

    # Check if hub already exists
    existing = await TopicHub.find_one(
        {
            "topic_slug": body.topic_slug,
            "chapter_id": PydanticObjectId(body.chapter_id),
        }
    )
    if existing:
        raise HTTPException(
            status_code=409, detail="TopicHub already exists for this topic"
        )

    # Create the hub
    hub = TopicHub(
        topic_slug=topic.topic_slug,
        chapter_id=PydanticObjectId(body.chapter_id),
        subject_id=chapter.subject_id,
        title=topic.title,
        definition=topic.definition or f"{topic.title} is a topic in {chapter.title}",
        wikidata_uri=topic.wikidata_uri,
    )
    await hub.insert()

    return {
        "id": str(hub.id),
        "topic_slug": hub.topic_slug,
        "title": hub.title,
        "status": "created",
    }


@router.post("/topics/{hub_id}/generate-mcqs")
async def generate_mcqs(request: Request, hub_id: str, body: GenerateMCQsRequest):
    """AI-generate MCQs for a topic hub."""
    _validate_admin_session(request)
    await _csrf_check(request)

    _parse_object_id(hub_id)

    try:
        mcqs = await authority_generator_service.generate_mcqs(hub_id, count=body.count)
        return {
            "hub_id": hub_id,
            "generated": len(mcqs),
            "mcqs": [mcq.model_dump() for mcq in mcqs],
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/topics/{hub_id}/generate-relations")
async def generate_relations(request: Request, hub_id: str):
    """AI-infer topic relations for the hub's chapter."""
    _validate_admin_session(request)
    await _csrf_check(request)

    oid = _parse_object_id(hub_id)
    hub = await TopicHub.get(oid)
    if not hub:
        raise HTTPException(status_code=404, detail="TopicHub not found")

    relations = await authority_generator_service.generate_topic_relations(
        str(hub.chapter_id)
    )
    return {
        "hub_id": hub_id,
        "chapter_id": str(hub.chapter_id),
        "relations_generated": len(relations),
        "relations": relations,
    }


@router.post("/topics/{hub_id}/add-source")
async def add_source(request: Request, hub_id: str, body: AddSourceRequest):
    """Add an authority source to a topic hub."""
    _validate_admin_session(request)
    await _csrf_check(request)

    oid = _parse_object_id(hub_id)
    hub = await TopicHub.get(oid)
    if not hub:
        raise HTTPException(status_code=404, detail="TopicHub not found")

    source = TopicSource(
        source_type=body.source_type,
        title=body.title,
        url=body.url,
        year=body.year,
        description=body.description,
    )
    hub.sources.append(source)
    hub.updated_at = datetime.now(timezone.utc)
    await hub.save()

    return {
        "hub_id": hub_id,
        "source_added": source.model_dump(),
        "total_sources": len(hub.sources),
    }
