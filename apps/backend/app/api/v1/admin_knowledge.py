from fastapi import APIRouter, HTTPException, Request, BackgroundTasks
from pydantic import ValidationError
from app.api.v1.admin import _validate_admin_session, _csrf_check
from app.models.knowledge import KnowledgeObject, ContentBlock, ContentMetadata
from app.services.content.pipeline import ContentPipeline
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Admin Knowledge"])
pipeline = ContentPipeline()


@router.post("/content/knowledge")
async def create_or_update_knowledge(request: Request):
    """Create or update a knowledge object."""
    _validate_admin_session(request)
    await _csrf_check(request)

    body = await request.json()
    slug = body.get("slug")
    if not slug:
        raise HTTPException(status_code=400, detail="slug is required")

    # Check if exists - update; otherwise create
    existing = await KnowledgeObject.find_one(KnowledgeObject.slug == slug)

    if existing:
        # Update existing
        for field in [
            "board",
            "class_level",
            "subject",
            "chapter",
            "topic",
            "is_published",
        ]:
            if field in body:
                setattr(existing, field, body[field])
        if "content" in body:
            try:
                existing.content = ContentBlock(**body["content"])
            except ValidationError as e:
                raise HTTPException(status_code=422, detail=str(e))
        if "metadata" in body:
            try:
                existing.metadata = ContentMetadata(**body["metadata"])
            except ValidationError as e:
                raise HTTPException(status_code=422, detail=str(e))
        existing.metadata.last_updated = datetime.now(timezone.utc)
        await existing.save()
        return {"status": "updated", "slug": slug}
    else:
        # Create new
        try:
            content = ContentBlock(**(body.get("content", {"body_markdown": ""})))
            metadata = ContentMetadata(**(body.get("metadata", {})))
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=str(e))
        ko_data = {
            "slug": slug,
            "board": body.get("board", ""),
            "class_level": body.get("class_level", ""),
            "subject": body.get("subject", ""),
            "chapter": body.get("chapter", ""),
            "topic": body.get("topic", ""),
            "content": content,
            "metadata": metadata,
            "is_published": body.get("is_published", False),
        }
        ko = KnowledgeObject(**ko_data)
        await ko.insert()
        return {"status": "created", "slug": slug}


@router.post("/content/knowledge/{slug}/publish")
async def publish_knowledge(request: Request, slug: str):
    """Trigger full pipeline for a slug."""
    _validate_admin_session(request)
    await _csrf_check(request)

    result = await pipeline.publish(slug)
    if result["steps"].get("fetch") == "not_found":
        raise HTTPException(
            status_code=404, detail=f"KnowledgeObject '{slug}' not found"
        )
    return result


@router.post("/content/knowledge/bulk-publish")
async def bulk_publish_knowledge(
    request: Request, background_tasks: BackgroundTasks
):
    """Trigger pipeline for all or filtered knowledge objects as background task."""
    _validate_admin_session(request)
    await _csrf_check(request)

    body = await request.json() if await request.body() else {}
    board_filter = body.get("board")
    subject_filter = body.get("subject")

    async def run_bulk_publish():
        query_filter = {"is_published": True}
        if board_filter:
            query_filter["board"] = board_filter
        if subject_filter:
            query_filter["subject"] = subject_filter

        objects = await KnowledgeObject.find(query_filter).to_list()
        for ko in objects:
            try:
                await pipeline.publish(ko.slug)
            except Exception as e:
                logger.error(f"Bulk publish failed for {ko.slug}: {e}")

    background_tasks.add_task(run_bulk_publish)
    return {"status": "started", "message": "Bulk publish running in background"}


@router.get("/content/knowledge")
async def list_knowledge(
    request: Request,
    page: int = 1,
    per_page: int = 20,
    board: str = None,
    subject: str = None,
):
    """List all knowledge objects with pagination."""
    _validate_admin_session(request)

    query_filter = {}
    if board:
        query_filter["board"] = board
    if subject:
        query_filter["subject"] = subject

    total = await KnowledgeObject.find(query_filter).count()
    skip = (page - 1) * per_page
    objects = (
        await KnowledgeObject.find(query_filter).skip(skip).limit(per_page).to_list()
    )

    return {
        "items": [
            {
                "slug": ko.slug,
                "board": ko.board,
                "class_level": ko.class_level,
                "subject": ko.subject,
                "chapter": ko.chapter,
                "topic": ko.topic,
                "is_published": ko.is_published,
                "page_views": ko.page_views,
                "last_updated": (
                    ko.metadata.last_updated.isoformat()
                    if ko.metadata.last_updated
                    else None
                ),
            }
            for ko in objects
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
    }


@router.get("/content/knowledge/{slug}")
async def get_knowledge(request: Request, slug: str):
    """Get a single knowledge object with all generated content."""
    _validate_admin_session(request)

    ko = await KnowledgeObject.find_one(KnowledgeObject.slug == slug)
    if not ko:
        raise HTTPException(status_code=404, detail="Knowledge object not found")

    return ko.model_dump(by_alias=True)
