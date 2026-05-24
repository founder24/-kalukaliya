"""
Admin Content Endpoints
Layer 1: Educational content hierarchy CRUD (boards, classes, streams, subjects, chapters)
Layer 2: Topic management and content editing
"""

import re
from datetime import datetime, timezone
from typing import Optional

from beanie import PydanticObjectId
from fastapi import APIRouter, HTTPException, Request
import logging

from app.api.v1.admin import _validate_admin_session, _csrf_check
from app.models.content import Board, Class, Stream, Subject, Chapter, Topic

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin Content"])


def _slugify(text: str) -> str:
    """Convert text to URL-friendly slug."""
    slug = text.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug.strip("-")


# ==============================================================================
# Layer 1: Board CRUD
# ==============================================================================


@router.post("/content/boards")
async def create_board(request: Request):
    """Create a new board."""
    _validate_admin_session(request)
    await _csrf_check(request)

    body = await request.json()
    name = body.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    board = Board(
        name=name,
        slug=_slugify(name),
        status=body.get("status", "active"),
    )
    await board.insert()
    return {"id": str(board.id), "name": board.name, "slug": board.slug, "status": board.status}


@router.get("/content/boards")
async def list_boards(request: Request):
    """List all boards."""
    _validate_admin_session(request)

    boards = await Board.find_all().to_list()
    return {
        "boards": [
            {
                "id": str(b.id),
                "name": b.name,
                "slug": b.slug,
                "status": b.status,
                "created_at": b.created_at.isoformat(),
                "updated_at": b.updated_at.isoformat(),
            }
            for b in boards
        ]
    }


@router.patch("/content/boards/{board_id}")
async def update_board(request: Request, board_id: PydanticObjectId):
    """Update a board."""
    _validate_admin_session(request)
    await _csrf_check(request)

    board = await Board.get(board_id)
    if not board:
        raise HTTPException(status_code=404, detail="Board not found")

    body = await request.json()
    if "name" in body:
        board.name = body["name"]
        board.slug = _slugify(body["name"])
    if "status" in body:
        board.status = body["status"]

    board.updated_at = datetime.now(timezone.utc)
    await board.save()
    return {"id": str(board.id), "name": board.name, "slug": board.slug, "status": board.status}


# ==============================================================================
# Layer 1: Class CRUD
# ==============================================================================


@router.post("/content/classes")
async def create_class(request: Request):
    """Create a new class."""
    _validate_admin_session(request)
    await _csrf_check(request)

    body = await request.json()
    name = body.get("name")
    board_id = body.get("board_id")
    if not name or not board_id:
        raise HTTPException(status_code=400, detail="name and board_id are required")

    cls = Class(
        name=name,
        board_id=PydanticObjectId(board_id),
        status=body.get("status", "active"),
    )
    await cls.insert()
    return {"id": str(cls.id), "name": cls.name, "board_id": str(cls.board_id), "status": cls.status}


@router.get("/content/classes")
async def list_classes(request: Request, board_id: Optional[str] = None):
    """List classes, optionally filtered by board_id."""
    _validate_admin_session(request)

    query = {}
    if board_id:
        query["board_id"] = PydanticObjectId(board_id)

    classes = await Class.find(query).to_list()
    return {
        "classes": [
            {
                "id": str(c.id),
                "name": c.name,
                "board_id": str(c.board_id),
                "status": c.status,
                "created_at": c.created_at.isoformat(),
                "updated_at": c.updated_at.isoformat(),
            }
            for c in classes
        ]
    }


# ==============================================================================
# Layer 1: Stream CRUD
# ==============================================================================


@router.post("/content/streams")
async def create_stream(request: Request):
    """Create a new stream."""
    _validate_admin_session(request)
    await _csrf_check(request)

    body = await request.json()
    name = body.get("name")
    class_id = body.get("class_id")
    if not name or not class_id:
        raise HTTPException(status_code=400, detail="name and class_id are required")

    stream = Stream(
        name=name,
        class_id=PydanticObjectId(class_id),
        status=body.get("status", "active"),
    )
    await stream.insert()
    return {"id": str(stream.id), "name": stream.name, "class_id": str(stream.class_id), "status": stream.status}


@router.get("/content/streams")
async def list_streams(request: Request, class_id: Optional[str] = None):
    """List streams, optionally filtered by class_id."""
    _validate_admin_session(request)

    query = {}
    if class_id:
        query["class_id"] = PydanticObjectId(class_id)

    streams = await Stream.find(query).to_list()
    return {
        "streams": [
            {
                "id": str(s.id),
                "name": s.name,
                "class_id": str(s.class_id),
                "status": s.status,
                "created_at": s.created_at.isoformat(),
                "updated_at": s.updated_at.isoformat(),
            }
            for s in streams
        ]
    }


# ==============================================================================
# Layer 1: Subject CRUD
# ==============================================================================


@router.post("/content/subjects")
async def create_subject(request: Request):
    """Create a new subject."""
    _validate_admin_session(request)
    await _csrf_check(request)

    body = await request.json()
    name = body.get("name")
    stream_id = body.get("stream_id")
    if not name or not stream_id:
        raise HTTPException(status_code=400, detail="name and stream_id are required")

    subject = Subject(
        name=name,
        stream_id=PydanticObjectId(stream_id),
        status=body.get("status", "active"),
    )
    await subject.insert()
    return {"id": str(subject.id), "name": subject.name, "stream_id": str(subject.stream_id), "status": subject.status}


@router.get("/content/subjects")
async def list_subjects(request: Request, stream_id: Optional[str] = None):
    """List subjects, optionally filtered by stream_id."""
    _validate_admin_session(request)

    query = {}
    if stream_id:
        query["stream_id"] = PydanticObjectId(stream_id)

    subjects = await Subject.find(query).to_list()
    return {
        "subjects": [
            {
                "id": str(s.id),
                "name": s.name,
                "stream_id": str(s.stream_id),
                "status": s.status,
                "created_at": s.created_at.isoformat(),
                "updated_at": s.updated_at.isoformat(),
            }
            for s in subjects
        ]
    }


# ==============================================================================
# Layer 1: Chapter CRUD
# ==============================================================================


@router.post("/content/chapters")
async def create_chapter(request: Request):
    """Create a new chapter."""
    _validate_admin_session(request)
    await _csrf_check(request)

    body = await request.json()
    title = body.get("title")
    subject_id = body.get("subject_id")
    chapter_number = body.get("chapter_number")
    if not title or not subject_id or chapter_number is None:
        raise HTTPException(status_code=400, detail="title, subject_id, and chapter_number are required")

    chapter = Chapter(
        title=title,
        slug=_slugify(title),
        subject_id=PydanticObjectId(subject_id),
        chapter_number=chapter_number,
        status=body.get("status", "draft"),
    )
    await chapter.insert()
    return {
        "id": str(chapter.id),
        "title": chapter.title,
        "slug": chapter.slug,
        "subject_id": str(chapter.subject_id),
        "chapter_number": chapter.chapter_number,
        "status": chapter.status,
    }


@router.get("/content/chapters")
async def list_chapters(request: Request, subject_id: Optional[str] = None):
    """List chapters, optionally filtered by subject_id."""
    _validate_admin_session(request)

    query = {}
    if subject_id:
        query["subject_id"] = PydanticObjectId(subject_id)

    chapters = await Chapter.find(query).to_list()
    return {
        "chapters": [
            {
                "id": str(ch.id),
                "title": ch.title,
                "slug": ch.slug,
                "subject_id": str(ch.subject_id),
                "chapter_number": ch.chapter_number,
                "status": ch.status,
                "created_at": ch.created_at.isoformat(),
                "updated_at": ch.updated_at.isoformat(),
            }
            for ch in chapters
        ]
    }


@router.get("/content/chapters/{chapter_id}")
async def get_chapter(request: Request, chapter_id: PydanticObjectId):
    """Get a single chapter with full content."""
    _validate_admin_session(request)

    chapter = await Chapter.get(chapter_id)
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
async def update_chapter(request: Request, chapter_id: PydanticObjectId):
    """Update chapter metadata/content."""
    _validate_admin_session(request)
    await _csrf_check(request)

    chapter = await Chapter.get(chapter_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    body = await request.json()
    allowed_fields = [
        "title", "status", "chapter_number", "meta_description",
        "keywords", "word_count", "content_en", "content_as", "faq_jsonld",
    ]
    for field in allowed_fields:
        if field in body:
            setattr(chapter, field, body[field])

    if "title" in body:
        chapter.slug = _slugify(body["title"])

    chapter.updated_at = datetime.now(timezone.utc)
    await chapter.save()
    return {
        "id": str(chapter.id),
        "title": chapter.title,
        "slug": chapter.slug,
        "status": chapter.status,
        "updated_at": chapter.updated_at.isoformat(),
    }


@router.delete("/content/chapters/{chapter_id}")
async def delete_chapter(request: Request, chapter_id: PydanticObjectId):
    """Delete a chapter."""
    _validate_admin_session(request)
    await _csrf_check(request)

    chapter = await Chapter.get(chapter_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    await chapter.delete()
    return {"status": "ok", "deleted_id": str(chapter_id)}


# ==============================================================================
# Layer 2: Topics
# ==============================================================================


@router.post("/content/chapters/{chapter_id}/topics")
async def add_topics(request: Request, chapter_id: PydanticObjectId):
    """Add topics to a chapter."""
    _validate_admin_session(request)
    await _csrf_check(request)

    chapter = await Chapter.get(chapter_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    body = await request.json()
    topics_data = body.get("topics", [])
    if not topics_data:
        raise HTTPException(status_code=400, detail="topics list is required")

    new_topics = []
    for t in topics_data:
        title = t.get("title")
        if not title:
            continue
        topic = Topic(
            title=title,
            definition=t.get("definition"),
            topic_slug=_slugify(title),
        )
        new_topics.append(topic)

    chapter.published_topics.extend(new_topics)
    chapter.updated_at = datetime.now(timezone.utc)
    await chapter.save()

    return {
        "added": len(new_topics),
        "total_topics": len(chapter.published_topics),
        "topics": [tp.model_dump() for tp in new_topics],
    }


@router.get("/content/chapters/{chapter_id}/topics")
async def list_topics(request: Request, chapter_id: PydanticObjectId):
    """List topics for a chapter."""
    _validate_admin_session(request)

    chapter = await Chapter.get(chapter_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    return {
        "chapter_id": str(chapter.id),
        "topics": [t.model_dump() for t in chapter.published_topics],
    }


@router.patch("/content/topics/{topic_id}")
async def update_topic(request: Request, topic_id: str):
    """Update a topic by its ID (searches across all chapters)."""
    _validate_admin_session(request)
    await _csrf_check(request)

    body = await request.json()

    # Find the chapter containing this topic
    chapter = await Chapter.find_one({"published_topics.id": topic_id})
    if not chapter:
        raise HTTPException(status_code=404, detail="Topic not found")

    # Find and update the topic in the list
    updated = False
    for topic in chapter.published_topics:
        if topic.id == topic_id:
            if "title" in body:
                topic.title = body["title"]
                topic.topic_slug = _slugify(body["title"])
            if "definition" in body:
                topic.definition = body["definition"]
            if "definition_status" in body:
                topic.definition_status = body["definition_status"]
            updated = True
            break

    if not updated:
        raise HTTPException(status_code=404, detail="Topic not found")

    chapter.updated_at = datetime.now(timezone.utc)
    await chapter.save()
    return {"status": "ok", "topic_id": topic_id}


@router.delete("/content/topics/{topic_id}")
async def delete_topic(request: Request, topic_id: str):
    """Delete a topic by its ID (searches across all chapters)."""
    _validate_admin_session(request)
    await _csrf_check(request)

    # Find the chapter containing this topic
    chapter = await Chapter.find_one({"published_topics.id": topic_id})
    if not chapter:
        raise HTTPException(status_code=404, detail="Topic not found")

    # Remove the topic
    chapter.published_topics = [t for t in chapter.published_topics if t.id != topic_id]
    chapter.updated_at = datetime.now(timezone.utc)
    await chapter.save()
    return {"status": "ok", "deleted_topic_id": topic_id}


# ==============================================================================
# Layer 2: Content editing
# ==============================================================================


@router.put("/content/chapters/{chapter_id}/content/en")
async def save_content_en(request: Request, chapter_id: PydanticObjectId):
    """Save English content body for a chapter."""
    _validate_admin_session(request)
    await _csrf_check(request)

    chapter = await Chapter.get(chapter_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    body = await request.json()
    content = body.get("content", "")
    chapter.content_en = content
    chapter.word_count = len(content.split()) if content else 0
    chapter.updated_at = datetime.now(timezone.utc)
    await chapter.save()
    return {"status": "ok", "word_count": chapter.word_count}


@router.put("/content/chapters/{chapter_id}/content/as")
async def save_content_as(request: Request, chapter_id: PydanticObjectId):
    """Save Assamese content body for a chapter."""
    _validate_admin_session(request)
    await _csrf_check(request)

    chapter = await Chapter.get(chapter_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    body = await request.json()
    content = body.get("content", "")
    chapter.content_as = content
    chapter.updated_at = datetime.now(timezone.utc)
    await chapter.save()
    return {"status": "ok", "chapter_id": str(chapter.id)}


@router.get("/content/chapters/{chapter_id}/content/{lang}")
async def get_content(request: Request, chapter_id: PydanticObjectId, lang: str):
    """Get content for a specific language."""
    _validate_admin_session(request)

    if lang not in ("en", "as"):
        raise HTTPException(status_code=400, detail="Language must be 'en' or 'as'")

    chapter = await Chapter.get(chapter_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    content = chapter.content_en if lang == "en" else chapter.content_as
    return {"chapter_id": str(chapter.id), "lang": lang, "content": content}


@router.get("/content/subjects/{subject_id}/topic-index")
async def get_topic_index(request: Request, subject_id: PydanticObjectId):
    """Get topic index for a subject (aggregate all topics from all chapters)."""
    _validate_admin_session(request)

    chapters = await Chapter.find({"subject_id": subject_id}).to_list()
    if not chapters:
        return {"subject_id": str(subject_id), "topics": [], "total": 0}

    all_topics = []
    for ch in chapters:
        for topic in ch.published_topics:
            all_topics.append({
                "topic_id": topic.id,
                "title": topic.title,
                "topic_slug": topic.topic_slug,
                "definition_status": topic.definition_status,
                "chapter_id": str(ch.id),
                "chapter_title": ch.title,
                "chapter_number": ch.chapter_number,
            })

    return {"subject_id": str(subject_id), "topics": all_topics, "total": len(all_topics)}


# ==============================================================================
# Layer 3: AI Generation + Publishing
# ==============================================================================


@router.post("/content/chapters/{chapter_id}/generate-notes")
async def generate_notes(request: Request, chapter_id: PydanticObjectId):
    """Generate English notes via Vertex AI and Assamese translation via Sarvam."""
    _validate_admin_session(request)
    await _csrf_check(request)

    try:
        from app.services.content_generation import ContentGenerationService

        service = ContentGenerationService()
        result = await service.generate_notes(str(chapter_id))
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"Generate notes failed for {chapter_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Generation failed: {e}")


@router.post("/content/chapters/{chapter_id}/generate-notes/as")
async def generate_notes_assamese(request: Request, chapter_id: PydanticObjectId):
    """Generate Assamese translation only from existing English content."""
    _validate_admin_session(request)
    await _csrf_check(request)

    try:
        from app.services.content_generation import ContentGenerationService

        service = ContentGenerationService()
        result = await service.generate_assamese_only(str(chapter_id))
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"Generate Assamese failed for {chapter_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Translation failed: {e}")


@router.post("/content/chapters/{chapter_id}/publish")
async def publish_chapter(request: Request, chapter_id: PydanticObjectId):
    """Full publish pipeline: Azure Search indexing + Cloudflare prerender."""
    _validate_admin_session(request)
    await _csrf_check(request)

    try:
        from app.services.content_publisher import ContentPublisherService

        service = ContentPublisherService()
        result = await service.publish_chapter(str(chapter_id))
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"Publish failed for {chapter_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Publishing failed: {e}")


@router.post("/content/chapters/{chapter_id}/publish/search-index")
async def publish_search_index(request: Request, chapter_id: PydanticObjectId):
    """Publish chapter to Azure Search index only."""
    _validate_admin_session(request)
    await _csrf_check(request)

    try:
        from app.services.content_publisher import ContentPublisherService

        chapter = await Chapter.get(chapter_id)
        if not chapter:
            raise HTTPException(status_code=404, detail="Chapter not found")

        service = ContentPublisherService()
        result = await service.publish_to_azure_search(chapter)
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"Search index publish failed for {chapter_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Search indexing failed: {e}")


@router.post("/content/chapters/{chapter_id}/publish/pages")
async def publish_pages(request: Request, chapter_id: PydanticObjectId):
    """Trigger Cloudflare prerender/cache invalidation only."""
    _validate_admin_session(request)
    await _csrf_check(request)

    try:
        from app.services.content_publisher import ContentPublisherService

        chapter = await Chapter.get(chapter_id)
        if not chapter:
            raise HTTPException(status_code=404, detail="Chapter not found")

        service = ContentPublisherService()
        result = await service.publish_to_cloudflare(chapter)
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"Cloudflare publish failed for {chapter_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Page publishing failed: {e}")


# ==============================================================================
# Layer 4: FAQ JSON-LD Generation
# ==============================================================================


@router.post("/content/chapters/{chapter_id}/faq-jsonld")
async def generate_faq_jsonld(request: Request, chapter_id: PydanticObjectId):
    """Generate FAQ JSON-LD from chapter published topics and store on document."""
    _validate_admin_session(request)
    await _csrf_check(request)

    chapter = await Chapter.get(chapter_id)
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    if not chapter.published_topics:
        raise HTTPException(
            status_code=400,
            detail="Chapter has no published topics to generate FAQ from",
        )

    # Generate FAQ JSON-LD entries from topics (skip those without definitions)
    faq_entries = []
    for topic in chapter.published_topics:
        if not topic.definition:
            continue
        faq_entries.append({
            "@type": "Question",
            "name": topic.title,
            "acceptedAnswer": {
                "@type": "Answer",
                "text": topic.definition,
            },
        })

    if not faq_entries:
        raise HTTPException(
            status_code=400,
            detail="No topics with definitions found to generate FAQ from",
        )

    # Store on chapter
    chapter.faq_jsonld = faq_entries
    chapter.updated_at = datetime.now(timezone.utc)
    await chapter.save()

    return {
        "chapter_id": str(chapter.id),
        "faq_jsonld": faq_entries,
        "entries_count": len(faq_entries),
    }
