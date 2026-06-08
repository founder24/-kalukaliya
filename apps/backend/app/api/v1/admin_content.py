"""
Admin Content Endpoints - Full CRUD for content hierarchy + AI generation + publishing.
Layer 1: Board/Class/Stream/Subject/Chapter CRUD
Layer 2: Topics, Content editing, Topic index
Layer 3: AI generation, Publishing, FAQ
"""

import re
import logging
from datetime import datetime, timezone
from typing import Optional

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.api.v1.admin import require_admin_session, csrf_guard
from app.models.content import Board, Class, Stream, Subject, Chapter, Topic
from app.services.content_generation import content_generation_service
from app.services.content_publisher import content_publisher_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin Content"], dependencies=[Depends(require_admin_session), Depends(csrf_guard)])


# --- Helpers ---


def _slugify(text: str) -> str:
    """Convert text to URL-friendly slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


# --- Request Models ---


class BoardCreate(BaseModel):
    name: str


class BoardUpdate(BaseModel):
    name: Optional[str] = None
    status: Optional[str] = None


class ClassCreate(BaseModel):
    name: str
    board_id: str


class StreamCreate(BaseModel):
    name: str
    class_id: str


class SubjectCreate(BaseModel):
    name: str
    stream_id: str


class ChapterCreate(BaseModel):
    title: str
    subject_id: str
    chapter_number: int


class ChapterUpdate(BaseModel):
    title: Optional[str] = None
    status: Optional[str] = None
    chapter_number: Optional[int] = None


class TopicCreate(BaseModel):
    title: str
    definition: Optional[str] = None
    topic_slug: Optional[str] = None


class TopicUpdate(BaseModel):
    title: Optional[str] = None
    definition: Optional[str] = None
    topic_slug: Optional[str] = None
    definition_status: Optional[str] = None


class ContentUpdate(BaseModel):
    content: str


class GenerateNotesRequest(BaseModel):
    force: bool = False


class PublishRequest(BaseModel):
    pass


class FAQEntry(BaseModel):
    question: str
    answer: str


class FAQRequest(BaseModel):
    faqs: list[FAQEntry]


# ============================
# LAYER 1: Board CRUD
# ============================


@router.post("/content/boards")
async def create_board(request: Request, body: BoardCreate):
    """Create a new board."""

    board = Board(name=body.name, slug=_slugify(body.name))
    await board.insert()
    return {"id": str(board.id), "name": board.name, "slug": board.slug}


@router.get("/content/boards")
async def list_boards(request: Request, skip: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000)):
    """List all boards."""

    boards = await Board.find_all().skip(skip).limit(limit).to_list(length=limit)
    return {
        "boards": [
            {
                "id": str(b.id),
                "name": b.name,
                "slug": b.slug,
                "status": b.status,
                "created_at": b.created_at.isoformat(),
            }
            for b in boards
        ],
        "total": len(boards),
    }


@router.patch("/content/boards/{board_id}")
async def update_board(request: Request, board_id: str, body: BoardUpdate):
    """Update a board."""

    board = await Board.get(PydanticObjectId(board_id))
    if not board:
        raise HTTPException(status_code=404, detail="Board not found")

    if body.name is not None:
        board.name = body.name
        board.slug = _slugify(body.name)
    if body.status is not None:
        board.status = body.status
    board.updated_at = datetime.now(timezone.utc)
    await board.save()

    return {
        "id": str(board.id),
        "name": board.name,
        "slug": board.slug,
        "status": board.status,
    }


# ============================
# LAYER 1: Class CRUD
# ============================


@router.post("/content/classes")
async def create_class(request: Request, body: ClassCreate):
    """Create a new class."""

    cls = Class(name=body.name, board_id=PydanticObjectId(body.board_id))
    await cls.insert()
    return {"id": str(cls.id), "name": cls.name, "board_id": str(cls.board_id)}


@router.get("/content/classes")
async def list_classes(request: Request, board_id: Optional[str] = Query(None), skip: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000)):
    """List classes, optionally filtered by board_id."""

    query = {}
    if board_id:
        query["board_id"] = PydanticObjectId(board_id)
    classes = await Class.find(query).skip(skip).limit(limit).to_list(length=limit)
    return {
        "classes": [
            {
                "id": str(c.id),
                "name": c.name,
                "board_id": str(c.board_id),
                "status": c.status,
            }
            for c in classes
        ],
        "total": len(classes),
    }


# ============================
# LAYER 1: Stream CRUD
# ============================


@router.post("/content/streams")
async def create_stream(request: Request, body: StreamCreate):
    """Create a new stream."""

    stream = Stream(name=body.name, class_id=PydanticObjectId(body.class_id))
    await stream.insert()
    return {"id": str(stream.id), "name": stream.name, "class_id": str(stream.class_id)}


@router.get("/content/streams")
async def list_streams(request: Request, class_id: Optional[str] = Query(None), skip: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000)):
    """List streams, optionally filtered by class_id."""

    query = {}
    if class_id:
        query["class_id"] = PydanticObjectId(class_id)
    streams = await Stream.find(query).skip(skip).limit(limit).to_list(length=limit)
    return {
        "streams": [
            {
                "id": str(s.id),
                "name": s.name,
                "class_id": str(s.class_id),
                "status": s.status,
            }
            for s in streams
        ],
        "total": len(streams),
    }


# ============================
# LAYER 1: Subject CRUD
# ============================


@router.post("/content/subjects")
async def create_subject(request: Request, body: SubjectCreate):
    """Create a new subject."""

    subject = Subject(name=body.name, stream_id=PydanticObjectId(body.stream_id))
    await subject.insert()
    return {
        "id": str(subject.id),
        "name": subject.name,
        "stream_id": str(subject.stream_id),
    }


@router.get("/content/subjects")
async def list_subjects(request: Request, stream_id: Optional[str] = Query(None), skip: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000)):
    """List subjects, optionally filtered by stream_id."""

    query = {}
    if stream_id:
        query["stream_id"] = PydanticObjectId(stream_id)
    subjects = await Subject.find(query).skip(skip).limit(limit).to_list(length=limit)
    return {
        "subjects": [
            {
                "id": str(s.id),
                "name": s.name,
                "stream_id": str(s.stream_id),
                "status": s.status,
            }
            for s in subjects
        ],
        "total": len(subjects),
    }


# ============================
# LAYER 1: Chapter CRUD
# ============================


@router.post("/content/chapters")
async def create_chapter(request: Request, body: ChapterCreate):
    """Create a new chapter."""

    slug = _slugify(body.title)
    chapter = Chapter(
        title=body.title,
        slug=slug,
        subject_id=PydanticObjectId(body.subject_id),
        chapter_number=body.chapter_number,
    )
    await chapter.insert()
    return {
        "id": str(chapter.id),
        "title": chapter.title,
        "slug": chapter.slug,
        "subject_id": str(chapter.subject_id),
        "chapter_number": chapter.chapter_number,
    }


@router.get("/content/chapters")
async def list_chapters(
    request: Request,
    subject_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
):
    """List chapters, optionally filtered by subject_id and/or status."""

    query = {}
    if subject_id:
        query["subject_id"] = PydanticObjectId(subject_id)
    if status:
        query["status"] = status
    chapters = await Chapter.find(query).skip(skip).limit(limit).to_list(length=limit)
    return {
        "chapters": [
            {
                "id": str(ch.id),
                "title": ch.title,
                "slug": ch.slug,
                "subject_id": str(ch.subject_id),
                "chapter_number": ch.chapter_number,
                "status": ch.status,
                "word_count": ch.word_count,
                "created_at": ch.created_at.isoformat(),
            }
            for ch in chapters
        ],
        "total": len(chapters),
    }


@router.get("/content/chapters/{chapter_id}")
async def get_chapter(request: Request, chapter_id: str):
    """Get a single chapter by ID."""

    chapter = await Chapter.get(PydanticObjectId(chapter_id))
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    return {
        "id": str(chapter.id),
        "title": chapter.title,
        "slug": chapter.slug,
        "subject_id": str(chapter.subject_id),
        "chapter_number": chapter.chapter_number,
        "status": chapter.status,
        "content_en": chapter.content_en,
        "content_as": chapter.content_as,
        "meta_description": chapter.meta_description,
        "keywords": chapter.keywords,
        "word_count": chapter.word_count,
        "published_topics": [t.model_dump() for t in chapter.published_topics],
        "faq_jsonld": chapter.faq_jsonld,
        "created_at": chapter.created_at.isoformat(),
        "updated_at": chapter.updated_at.isoformat(),
    }


@router.patch("/content/chapters/{chapter_id}")
async def update_chapter(request: Request, chapter_id: str, body: ChapterUpdate):
    """Update chapter fields."""

    chapter = await Chapter.get(PydanticObjectId(chapter_id))
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    if body.title is not None:
        chapter.title = body.title
        chapter.slug = _slugify(body.title)
    if body.status is not None:
        chapter.status = body.status
    if body.chapter_number is not None:
        chapter.chapter_number = body.chapter_number
    chapter.updated_at = datetime.now(timezone.utc)
    await chapter.save()

    return {"id": str(chapter.id), "title": chapter.title, "status": chapter.status}


@router.delete("/content/chapters/{chapter_id}")
async def delete_chapter(request: Request, chapter_id: str):
    """Delete a chapter."""

    chapter = await Chapter.get(PydanticObjectId(chapter_id))
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    await chapter.delete()
    return {"status": "deleted", "id": chapter_id}


# ============================
# LAYER 2: Topics
# ============================


@router.post("/content/chapters/{chapter_id}/topics")
async def add_topic(request: Request, chapter_id: str, body: TopicCreate):
    """Add a topic to a chapter."""

    chapter = await Chapter.get(PydanticObjectId(chapter_id))
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    topic_slug = body.topic_slug or _slugify(body.title)
    topic = Topic(title=body.title, definition=body.definition, topic_slug=topic_slug)
    chapter.published_topics.append(topic)
    chapter.updated_at = datetime.now(timezone.utc)
    await chapter.save()

    return {"id": topic.id, "title": topic.title, "topic_slug": topic.topic_slug}


@router.get("/content/chapters/{chapter_id}/topics")
async def list_topics(request: Request, chapter_id: str):
    """List topics for a chapter."""

    chapter = await Chapter.get(PydanticObjectId(chapter_id))
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    return {
        "topics": [t.model_dump() for t in chapter.published_topics],
        "total": len(chapter.published_topics),
    }


@router.patch("/content/topics/{topic_id}")
async def update_topic(request: Request, topic_id: str, body: TopicUpdate):
    """Update a topic by ID (searches across all chapters)."""

    # Find the chapter containing this topic
    chapters = await Chapter.find({"published_topics.id": topic_id}).to_list(length=None)
    if not chapters:
        raise HTTPException(status_code=404, detail="Topic not found")

    chapter = chapters[0]
    for topic in chapter.published_topics:
        if topic.id == topic_id:
            if body.title is not None:
                topic.title = body.title
            if body.definition is not None:
                topic.definition = body.definition
            if body.topic_slug is not None:
                topic.topic_slug = body.topic_slug
            if body.definition_status is not None:
                topic.definition_status = body.definition_status
            break

    chapter.updated_at = datetime.now(timezone.utc)
    await chapter.save()
    return {"status": "updated", "topic_id": topic_id}


@router.delete("/content/topics/{topic_id}")
async def delete_topic(request: Request, topic_id: str):
    """Delete a topic by ID."""

    chapters = await Chapter.find({"published_topics.id": topic_id}).to_list(length=None)
    if not chapters:
        raise HTTPException(status_code=404, detail="Topic not found")

    chapter = chapters[0]
    chapter.published_topics = [t for t in chapter.published_topics if t.id != topic_id]
    chapter.updated_at = datetime.now(timezone.utc)
    await chapter.save()
    return {"status": "deleted", "topic_id": topic_id}


# ============================
# LAYER 2: Content Editing
# ============================


@router.put("/content/chapters/{chapter_id}/content/en")
async def update_content_en(request: Request, chapter_id: str, body: ContentUpdate):
    """Update English content for a chapter."""

    chapter = await Chapter.get(PydanticObjectId(chapter_id))
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    chapter.content_en = body.content
    chapter.word_count = len(body.content.split())
    chapter.updated_at = datetime.now(timezone.utc)
    await chapter.save()

    return {"status": "updated", "word_count": chapter.word_count}


@router.put("/content/chapters/{chapter_id}/content/as")
async def update_content_as(request: Request, chapter_id: str, body: ContentUpdate):
    """Update Assamese content for a chapter."""

    chapter = await Chapter.get(PydanticObjectId(chapter_id))
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    chapter.content_as = body.content
    chapter.updated_at = datetime.now(timezone.utc)
    await chapter.save()

    return {"status": "updated"}


@router.get("/content/chapters/{chapter_id}/content/{lang}")
async def get_content(request: Request, chapter_id: str, lang: str):
    """Get content for a chapter in the specified language."""

    chapter = await Chapter.get(PydanticObjectId(chapter_id))
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    if lang == "en":
        content = chapter.content_en
    elif lang == "as":
        content = chapter.content_as
    else:
        raise HTTPException(status_code=400, detail="Language must be 'en' or 'as'")

    return {"chapter_id": chapter_id, "lang": lang, "content": content}


# ============================
# LAYER 2: Topic Index
# ============================


@router.get("/content/subjects/{subject_id}/topic-index")
async def get_topic_index(request: Request, subject_id: str):
    """Get a consolidated topic index for all chapters in a subject."""

    chapters = await Chapter.find(
        {"subject_id": PydanticObjectId(subject_id)}
    ).to_list(length=None)

    index = []
    for ch in chapters:
        for topic in ch.published_topics:
            index.append(
                {
                    "chapter_id": str(ch.id),
                    "chapter_title": ch.title,
                    "chapter_number": ch.chapter_number,
                    "topic_id": topic.id,
                    "title": topic.title,
                    "definition": topic.definition,
                    "topic_slug": topic.topic_slug,
                    "definition_status": topic.definition_status,
                }
            )

    return {"subject_id": subject_id, "topics": index, "total": len(index)}


# ============================
# LAYER 3: AI Generation
# ============================


@router.post("/content/chapters/{chapter_id}/generate-notes")
async def generate_notes(request: Request, chapter_id: str, body: GenerateNotesRequest = None):
    """Generate English notes + Assamese translation, then auto-publish.

    Full pipeline on success:
      1. Vertex AI  → English study notes
      2. Sarvam AI  → Assamese translation (chunked, soft-fail)
      3. Vertex AI  → SEO meta + keywords + 5-entry FAQ JSON-LD
      4. MongoDB    → save (status='generated')
      5. GCS        → upload bilingual JSON (source of truth for CF Pages)
      6. Vertex AI Search → index content chunks + topic micro-docs (RAG)
      7. Cloudflare → prerender / KV invalidation
      8. Topic embeddings → cosine similarity matching
      9. MongoDB    → status='published'

    Pass {"force": true} in the request body to overwrite existing content.
    By default (force=false) the endpoint is a no-op when content_en is already present.
    """

    force = body.force if body else False
    try:
        _ch_before = await Chapter.get(PydanticObjectId(chapter_id))
        had_content = bool(_ch_before and _ch_before.content_en and _ch_before.content_en.strip())

        chapter = await content_generation_service.generate_notes(chapter_id, force=force)
        was_skipped = not force and had_content

        publish_result = getattr(chapter, "_publish_result", {})
        return {
            "status": "skipped_existing" if was_skipped else "published",
            "chapter_id": chapter_id,
            "chapter_status": chapter.status,
            "word_count": chapter.word_count,
            "has_assamese": bool(chapter.content_as),
            "meta_description": chapter.meta_description,
            "pipeline": {
                "gcs": publish_result.get("gcs", {}).get("status"),
                "cloudflare": publish_result.get("cloudflare", {}).get("status"),
                "topic_embeddings": publish_result.get("topic_embeddings", {}).get("count", 0),
            },
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Generate notes error: {e}")
        raise HTTPException(status_code=500, detail="Generation failed")


@router.post("/content/chapters/{chapter_id}/generate-notes/as")
async def generate_notes_assamese(request: Request, chapter_id: str, body: GenerateNotesRequest = None):
    """Translate existing English content to Assamese, then re-sync GCS + Vertex Search.

    After translation the updated bilingual JSON is re-uploaded to GCS (so
    Cloudflare Pages picks it up) and re-indexed in Vertex AI Search (so RAG
    serves the latest content).

    Pass {"force": true} in the request body to overwrite existing content_as.
    By default (force=false) the endpoint is a no-op when content_as is already present.
    """

    force = body.force if body else False
    try:
        _ch_before = await Chapter.get(PydanticObjectId(chapter_id))
        had_content = bool(_ch_before and _ch_before.content_as and _ch_before.content_as.strip())

        chapter = await content_generation_service.generate_assamese_only(chapter_id, force=force)
        was_skipped = not force and had_content
        return {
            "status": "skipped_existing" if was_skipped else "translated_and_synced",
            "chapter_id": chapter_id,
            "assamese_word_count": len((chapter.content_as or "").split()),
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Generate Assamese error: {e}")
        raise HTTPException(status_code=500, detail="Translation failed")


# ============================
# LAYER 3: Publishing
# ============================


@router.post("/content/chapters/{chapter_id}/publish")
async def publish_chapter(request: Request, chapter_id: str):
    """Full publish pipeline: Vertex AI Search + Cloudflare + status update."""

    try:
        result = await content_publisher_service.publish_chapter(chapter_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Publish chapter error: {e}")
        raise HTTPException(status_code=500, detail="Publishing failed")


@router.post("/content/chapters/{chapter_id}/publish/search-index")
async def publish_search_index(request: Request, chapter_id: str):
    """Publish chapter to Vertex AI Search index only."""

    chapter = await Chapter.get(PydanticObjectId(chapter_id))
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    result = await content_publisher_service.publish_to_vertex_search(chapter)
    return {"chapter_id": chapter_id, "result": result}


@router.post("/content/chapters/{chapter_id}/publish/pages")
async def publish_pages(request: Request, chapter_id: str):
    """Publish chapter pages to Cloudflare only."""

    chapter = await Chapter.get(PydanticObjectId(chapter_id))
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    result = await content_publisher_service.publish_to_cloudflare(chapter)
    return {"chapter_id": chapter_id, "result": result}


# ============================
# LAYER 3: FAQ JSON-LD
# ============================


@router.post("/content/chapters/{chapter_id}/faq-jsonld")
async def set_faq_jsonld(request: Request, chapter_id: str, body: FAQRequest):
    """Set FAQ JSON-LD structured data for a chapter."""

    chapter = await Chapter.get(PydanticObjectId(chapter_id))
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    # Build JSON-LD structure
    faq_jsonld = [
        {
            "@type": "Question",
            "name": faq.question,
            "acceptedAnswer": {
                "@type": "Answer",
                "text": faq.answer,
            },
        }
        for faq in body.faqs
    ]

    chapter.faq_jsonld = faq_jsonld
    chapter.updated_at = datetime.now(timezone.utc)
    await chapter.save()

    return {"status": "updated", "faq_count": len(faq_jsonld)}


# ============================
# LAYER 4: Content Pipeline
# ============================


class PipelineGenerateRequest(BaseModel):
    knowledge_id: str


@router.post("/content/pipeline/generate")
async def trigger_pipeline(request: Request, body: PipelineGenerateRequest):
    """
    Trigger the full content pipeline for a knowledge object.
    Pipeline steps: render HTML -> index Vertex AI Search -> compute hashes ->
    submit IndexNow -> push Cloudflare KV -> save to database.
    """

    try:
        from app.db.mongo import get_mongo_client
        from app.config import settings

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        # Try to use Knowledge model if available
        try:
            from app.models.knowledge import Knowledge

            knowledge_obj = await Knowledge.get(PydanticObjectId(body.knowledge_id))
            if not knowledge_obj:
                raise HTTPException(
                    status_code=404, detail="Knowledge object not found"
                )
        except ImportError:
            raise HTTPException(status_code=501, detail="Knowledge model not available")

        from app.services.content.pipeline import content_pipeline

        result = await content_pipeline.run(knowledge_obj)
        return {
            "status": "completed",
            "knowledge_id": body.knowledge_id,
            "pipeline_results": result,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Pipeline trigger error: {e}")
        raise HTTPException(
            status_code=500, detail=f"Pipeline execution failed: {str(e)}"
        )


@router.get("/content/pipeline/status")
async def get_pipeline_status(request: Request, knowledge_id: str = Query(...)):
    """
    Check content pipeline status for a knowledge object.
    Returns the last pipeline run timestamp and current status.
    """

    try:
        from app.models.knowledge import Knowledge

        knowledge_obj = await Knowledge.get(PydanticObjectId(knowledge_id))
        if not knowledge_obj:
            raise HTTPException(status_code=404, detail="Knowledge object not found")

        return {
            "knowledge_id": knowledge_id,
            "last_pipeline_run": knowledge_obj.last_pipeline_run.isoformat()
            if knowledge_obj.last_pipeline_run
            else None,
            "has_rendered_html": bool(getattr(knowledge_obj, "rendered_html", None)),
            "has_derivative_hashes": bool(
                getattr(knowledge_obj, "derivative_hashes", None)
            ),
            "slug": getattr(knowledge_obj, "slug", None),
        }
    except ImportError:
        raise HTTPException(status_code=501, detail="Knowledge model not available")
    except Exception as e:
        logger.error(f"Pipeline status error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================
# LAYER 4b: CMS Documents (Blog/SEO posts)
# ============================


class CmsDocCreate(BaseModel):
    title: str
    content: str = ""
    meta_description: str = ""
    description: str = ""
    seo_tags: str = ""
    primary_keyword: str = ""
    seo_slug: str = ""
    category: str = ""
    geo_tags: str = ""
    schema_type: str = "Article"
    status: str = "draft"
    thumbnail_url: str = ""
    alt_text: str = ""
    linked_scope: str = ""


class CmsDocUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    meta_description: Optional[str] = None
    description: Optional[str] = None
    seo_tags: Optional[str] = None
    primary_keyword: Optional[str] = None
    seo_slug: Optional[str] = None
    category: Optional[str] = None
    geo_tags: Optional[str] = None
    schema_type: Optional[str] = None
    status: Optional[str] = None
    thumbnail_url: Optional[str] = None
    alt_text: Optional[str] = None
    linked_scope: Optional[str] = None


def _cms_doc_to_dict(doc) -> dict:
    return {
        "id": str(doc.id),
        "title": doc.title,
        "content": doc.content,
        "meta_description": doc.meta_description,
        "description": doc.description,
        "seo_tags": doc.seo_tags,
        "primary_keyword": doc.primary_keyword,
        "seo_slug": doc.seo_slug,
        "category": doc.category,
        "geo_tags": doc.geo_tags,
        "schema_type": doc.schema_type,
        "status": doc.status,
        "thumbnail_url": doc.thumbnail_url,
        "alt_text": doc.alt_text,
        "linked_scope": doc.linked_scope,
        "word_count": doc.word_count,
        "board_slug": doc.board_slug,
        "subject_id": doc.subject_id,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
    }


@router.get("/content/cms-documents")
async def list_cms_documents(request: Request):
    """List all CMS documents (admin)."""
    try:
        from app.models.cms import CmsDocument
        docs = await CmsDocument.find().sort([("updated_at", -1)]).to_list(length=None)
        return [_cms_doc_to_dict(d) for d in docs]
    except Exception as e:
        logger.error(f"CMS list error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/content/cms-documents")
async def create_cms_document(request: Request, body: CmsDocCreate):
    """Create a new CMS document."""
    try:
        from app.models.cms import CmsDocument
        word_count = len(body.content.split()) if body.content else 0
        board_slug = body.linked_scope.split("/")[0] if body.linked_scope else ""
        subject_id = body.linked_scope.split("/")[3] if body.linked_scope and len(body.linked_scope.split("/")) > 3 else ""
        doc = CmsDocument(
            **body.model_dump(),
            word_count=word_count,
            board_slug=board_slug,
            subject_id=subject_id,
        )
        await doc.insert()
        return _cms_doc_to_dict(doc)
    except Exception as e:
        logger.error(f"CMS create error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/content/cms-documents/{doc_id}")
async def update_cms_document(request: Request, doc_id: str, body: CmsDocUpdate):
    """Update a CMS document."""
    try:
        from app.models.cms import CmsDocument
        from beanie import PydanticObjectId
        doc = await CmsDocument.get(PydanticObjectId(doc_id))
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        updates = body.model_dump(exclude_none=True)
        for k, v in updates.items():
            setattr(doc, k, v)
        if "content" in updates:
            doc.word_count = len(updates["content"].split()) if updates["content"] else 0
        if "linked_scope" in updates:
            parts = updates["linked_scope"].split("/")
            doc.board_slug = parts[0] if parts else ""
            doc.subject_id = parts[3] if len(parts) > 3 else ""
        doc.updated_at = datetime.now(timezone.utc)
        await doc.save()
        return _cms_doc_to_dict(doc)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"CMS update error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/content/cms-documents/{doc_id}")
async def delete_cms_document(request: Request, doc_id: str):
    """Delete a CMS document."""
    try:
        from app.models.cms import CmsDocument
        from beanie import PydanticObjectId
        doc = await CmsDocument.get(PydanticObjectId(doc_id))
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        await doc.delete()
        return {"status": "deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"CMS delete error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/content/cms-documents/{doc_id}/publish")
async def toggle_cms_document_publish(request: Request, doc_id: str):
    """Toggle publish state of a CMS document."""
    try:
        from app.models.cms import CmsDocument
        from beanie import PydanticObjectId
        doc = await CmsDocument.get(PydanticObjectId(doc_id))
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        doc.status = "draft" if doc.status == "published" else "published"
        doc.updated_at = datetime.now(timezone.utc)
        await doc.save()
        return {"status": doc.status}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"CMS publish toggle error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/content/cms-documents/{doc_id}/revision")
async def save_cms_document_revision(request: Request, doc_id: str):
    """Save a named revision snapshot (lightweight - just returns the current doc)."""
    try:
        from app.models.cms import CmsDocument
        from beanie import PydanticObjectId
        doc = await CmsDocument.get(PydanticObjectId(doc_id))
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        return {"status": "ok", "revision_saved_at": datetime.now(timezone.utc).isoformat()}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================
# LAYER 4b: Translation Progress
# ============================


@router.get("/content/translation-progress")
async def get_translation_progress(request: Request):
    """Per-subject breakdown of chapters missing Assamese (content_as) translation."""

    import asyncio
    from collections import defaultdict

    chapters, subjects = await asyncio.gather(
        Chapter.find_all().to_list(length=None),
        Subject.find_all().to_list(length=None),
    )

    subject_name_map = {str(s.id): s.name for s in subjects}

    by_subject: dict[str, list] = defaultdict(list)
    for ch in chapters:
        by_subject[str(ch.subject_id)].append(ch)

    total = len(chapters)
    translated = sum(1 for ch in chapters if ch.content_as and ch.content_as.strip())
    missing = total - translated

    subject_groups = []
    for subj_id, chs in by_subject.items():
        subj_name = subject_name_map.get(subj_id, subj_id)
        subj_translated = sum(1 for ch in chs if ch.content_as and ch.content_as.strip())
        missing_chs = [ch for ch in chs if not (ch.content_as and ch.content_as.strip())]
        if not missing_chs:
            continue
        subject_groups.append({
            "subject_id": subj_id,
            "subject_name": subj_name,
            "total": len(chs),
            "translated": subj_translated,
            "missing": len(missing_chs),
            "chapters": [
                {
                    "id": str(ch.id),
                    "title": ch.title,
                    "chapter_number": ch.chapter_number,
                    "status": ch.status,
                }
                for ch in sorted(missing_chs, key=lambda c: (c.chapter_number or 0))
            ],
        })

    subject_groups.sort(key=lambda s: -s["missing"])

    return {
        "total": total,
        "translated": translated,
        "missing": missing,
        "subjects": subject_groups,
    }


# ============================
# LAYER 6: Agent Ingest (Replit Chat → MongoDB)
# ============================


class _AgentTopicIn(BaseModel):
    title: str
    definition: Optional[str] = None


class _AgentChapterIn(BaseModel):
    title: str
    chapter_number: int
    topics: list[_AgentTopicIn] = []


class AgentIngestRequest(BaseModel):
    subject_id: str
    chapters: list[_AgentChapterIn]
    trigger_generation: bool = False


@router.post("/ingest-from-agent")
async def ingest_from_agent(request: Request, body: AgentIngestRequest):
    """Bulk-create chapters + topics from a structured syllabus extracted by the Replit agent.

    Auth: admin session cookie OR Bearer token (type=admin, role=admin).
    CSRF check is intentionally skipped — this is a programmatic endpoint, not a browser form.

    Set trigger_generation=true to queue background note generation for every
    newly created chapter immediately after seeding.
    """

    subject = await Subject.get(PydanticObjectId(body.subject_id))
    if not subject:
        raise HTTPException(status_code=404, detail=f"Subject {body.subject_id} not found")

    created = []
    skipped = []

    for ch_input in body.chapters:
        slug = _slugify(ch_input.title)
        existing = await Chapter.find_one(
            Chapter.subject_id == body.subject_id,
            Chapter.slug == slug,
        )
        if existing:
            skipped.append({
                "id": str(existing.id),
                "title": existing.title,
                "reason": "already_exists",
            })
            continue

        topics = [
            Topic(
                title=t.title,
                definition=t.definition or "",
                topic_slug=_slugify(t.title),
            )
            for t in ch_input.topics
        ]

        chapter = Chapter(
            title=ch_input.title,
            slug=slug,
            subject_id=body.subject_id,
            chapter_number=ch_input.chapter_number,
            published_topics=topics,
            status="draft",
        )
        await chapter.insert()
        created.append({
            "id": str(chapter.id),
            "title": chapter.title,
            "slug": slug,
            "topics": len(topics),
        })

    generation_queued = False
    if body.trigger_generation and created:
        import asyncio
        for ch_info in created:
            asyncio.create_task(
                content_generation_service.generate_notes(ch_info["id"], force=False)
            )
        generation_queued = True

    logger.info(
        f"[ingest-from-agent] subject={body.subject_id} "
        f"created={len(created)} skipped={len(skipped)} gen_queued={generation_queued}"
    )

    return {
        "subject_id": body.subject_id,
        "subject_name": subject.name,
        "created": len(created),
        "skipped": len(skipped),
        "chapters": created,
        "skipped_details": skipped,
        "generation_queued": generation_queued,
    }


# ============================
# LAYER 5: GCS Sync
# ============================


@router.post("/sync-to-gcs")
async def sync_to_gcs(request: Request):
    """Sync all content hierarchy and library bundles to GCS."""

    try:
        from app.services.content.hierarchy_sync import sync_hierarchy_to_gcs

        result = await sync_hierarchy_to_gcs()
        return result
    except Exception as e:
        logger.error(f"GCS sync error: {e}")
        raise HTTPException(status_code=500, detail=f"Sync failed: {str(e)}")
