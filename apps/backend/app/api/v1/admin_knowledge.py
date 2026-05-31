"""
Admin Knowledge Endpoints - CRUD and pipeline management for KnowledgeObjects.
Protected by admin session cookie + CSRF validation.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.v1.admin import _validate_admin_session, _csrf_check
from app.models.knowledge import KnowledgeObject, ContentMetadata, GeneratedContent
from app.services.content.pipeline import content_pipeline

logger = logging.getLogger(__name__)

router = APIRouter()


class KnowledgeCreateRequest(BaseModel):
    """Request body for creating/updating a knowledge object."""

    slug: str
    title: str = ""
    description: str = ""
    body_markdown: str = ""
    metadata: Optional[dict] = None
    generated: Optional[dict] = None
    status: str = "draft"


class BulkPublishRequest(BaseModel):
    """Request body for bulk publishing."""

    slugs: list[str] = Field(default_factory=list)


@router.post("/content/knowledge")
async def create_or_update_knowledge(request: Request, body: KnowledgeCreateRequest):
    """Create or update a knowledge object."""
    await _validate_admin_session(request)
    await _csrf_check(request)

    existing = await KnowledgeObject.find_one({"slug": body.slug})

    if existing:
        # Update
        if body.title:
            existing.title = body.title
        if body.description:
            existing.description = body.description
        if body.body_markdown:
            existing.body_markdown = body.body_markdown
        if body.metadata:
            existing.metadata = ContentMetadata(**body.metadata)
        if body.generated:
            existing.generated = GeneratedContent(**body.generated)
        if body.status:
            existing.status = body.status
            if body.status == "published" and not existing.published_at:
                existing.published_at = datetime.now(timezone.utc)
        existing.updated_at = datetime.now(timezone.utc)
        await existing.save()
        return {"status": "updated", "slug": existing.slug}
    else:
        # Create
        obj = KnowledgeObject(
            slug=body.slug,
            title=body.title,
            description=body.description,
            body_markdown=body.body_markdown,
            metadata=ContentMetadata(**(body.metadata or {})),
            generated=GeneratedContent(**(body.generated or {})),
            status=body.status,
        )
        if body.status == "published":
            obj.published_at = datetime.now(timezone.utc)
        await obj.insert()
        return {"status": "created", "slug": obj.slug}


@router.post("/content/knowledge/{slug}/publish")
async def publish_knowledge(request: Request, slug: str):
    """Trigger the content pipeline for a knowledge object."""
    await _validate_admin_session(request)
    await _csrf_check(request)

    obj = await KnowledgeObject.find_one({"slug": slug})
    if not obj:
        raise HTTPException(status_code=404, detail="Knowledge object not found")

    obj.status = "published"
    if not obj.published_at:
        obj.published_at = datetime.now(timezone.utc)
    await obj.save()

    results = await content_pipeline.run(obj)
    return {"status": "published", "slug": slug, "pipeline": results}


@router.post("/content/knowledge/bulk-publish")
async def bulk_publish_knowledge(
    request: Request,
    body: BulkPublishRequest,
    background_tasks: BackgroundTasks,
):
    """Trigger the content pipeline for multiple knowledge objects in background."""
    await _validate_admin_session(request)
    await _csrf_check(request)

    if not body.slugs:
        raise HTTPException(status_code=400, detail="No slugs provided")

    async def _run_pipeline(slug: str):
        try:
            obj = await KnowledgeObject.find_one({"slug": slug})
            if obj:
                obj.status = "published"
                if not obj.published_at:
                    obj.published_at = datetime.now(timezone.utc)
                await obj.save()
                await content_pipeline.run(obj)
        except Exception as e:
            logger.error(f"Bulk publish failed for slug={slug}: {e}")

    for slug in body.slugs:
        background_tasks.add_task(_run_pipeline, slug)

    return {
        "status": "queued",
        "count": len(body.slugs),
        "slugs": body.slugs,
    }


@router.get("/content/knowledge")
async def list_knowledge(
    request: Request,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    status: Optional[str] = None,
):
    """List knowledge objects with pagination."""
    await _validate_admin_session(request)
    limit = min(limit, 100)

    query = {}
    if status:
        query["status"] = status

    objects = (
        await KnowledgeObject.find(query)
        .sort("-updated_at")
        .skip(skip)
        .limit(limit)
        .to_list()
    )

    total = await KnowledgeObject.find(query).count()

    return {
        "items": [
            {
                "slug": obj.slug,
                "title": obj.title,
                "status": obj.status,
                "metadata": obj.metadata.model_dump(),
                "published_at": obj.published_at.isoformat()
                if obj.published_at
                else None,
                "updated_at": obj.updated_at.isoformat(),
                "page_views": obj.page_views,
            }
            for obj in objects
        ],
        "total": total,
        "offset": skip,
        "limit": limit,
        "has_more": skip + limit < total,
    }


@router.get("/content/knowledge/{slug}")
async def get_knowledge(request: Request, slug: str):
    """Get a single knowledge object by slug (admin view - all fields)."""
    await _validate_admin_session(request)

    obj = await KnowledgeObject.find_one({"slug": slug})
    if not obj:
        raise HTTPException(status_code=404, detail="Knowledge object not found")

    return obj.model_dump()
