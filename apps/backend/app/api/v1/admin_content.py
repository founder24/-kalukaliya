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
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel

from app.api.v1.admin import _validate_admin_session, _csrf_check
from app.models.content import Board, Class, Stream, Subject, Chapter, Topic
from app.services.content_generation import content_generation_service
from app.services.content_publisher import content_publisher_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin Content"])


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
    pass


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
    _validate_admin_session(request)
    await _csrf_check(request)

    board = Board(name=body.name, slug=_slugify(body.name))
    await board.insert()
    return {"id": str(board.id), "name": board.name, "slug": board.slug}


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
            }
            for b in boards
        ],
        "total": len(boards),
    }


@router.patch("/content/boards/{board_id}")
async def update_board(request: Request, board_id: str, body: BoardUpdate):
    """Update a board."""
    _validate_admin_session(request)
    await _csrf_check(request)

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
    _validate_admin_session(request)
    await _csrf_check(request)

    cls = Class(name=body.name, board_id=PydanticObjectId(body.board_id))
    await cls.insert()
    return {"id": str(cls.id), "name": cls.name, "board_id": str(cls.board_id)}


@router.get("/content/classes")
async def list_classes(request: Request, board_id: Optional[str] = Query(None)):
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
    _validate_admin_session(request)
    await _csrf_check(request)

    stream = Stream(name=body.name, class_id=PydanticObjectId(body.class_id))
    await stream.insert()
    return {"id": str(stream.id), "name": stream.name, "class_id": str(stream.class_id)}


@router.get("/content/streams")
async def list_streams(request: Request, class_id: Optional[str] = Query(None)):
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
    _validate_admin_session(request)
    await _csrf_check(request)

    subject = Subject(name=body.name, stream_id=PydanticObjectId(body.stream_id))
    await subject.insert()
    return {
        "id": str(subject.id),
        "name": subject.name,
        "stream_id": str(subject.stream_id),
    }


@router.get("/content/subjects")
async def list_subjects(request: Request, stream_id: Optional[str] = Query(None)):
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
    _validate_admin_session(request)
    await _csrf_check(request)

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
):
    """List chapters, optionally filtered by subject_id and/or status."""
    _validate_admin_session(request)

    query = {}
    if subject_id:
        query["subject_id"] = PydanticObjectId(subject_id)
    if status:
        query["status"] = status
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
    _validate_admin_session(request)

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
    _validate_admin_session(request)
    await _csrf_check(request)

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
    _validate_admin_session(request)
    await _csrf_check(request)

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
    _validate_admin_session(request)
    await _csrf_check(request)

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
    _validate_admin_session(request)

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
    _validate_admin_session(request)
    await _csrf_check(request)

    # Find the chapter containing this topic
    chapters = await Chapter.find({"published_topics.id": topic_id}).to_list()
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
    _validate_admin_session(request)
    await _csrf_check(request)

    chapters = await Chapter.find({"published_topics.id": topic_id}).to_list()
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
    _validate_admin_session(request)
    await _csrf_check(request)

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
    _validate_admin_session(request)
    await _csrf_check(request)

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
    _validate_admin_session(request)

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
    _validate_admin_session(request)

    chapters = await Chapter.find(
        {"subject_id": PydanticObjectId(subject_id)}
    ).to_list()

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
async def generate_notes(request: Request, chapter_id: str):
    """Generate English notes and Assamese translation for a chapter using AI."""
    _validate_admin_session(request)
    await _csrf_check(request)

    try:
        chapter = await content_generation_service.generate_notes(chapter_id)
        return {
            "status": "generated",
            "chapter_id": chapter_id,
            "word_count": chapter.word_count,
            "meta_description": chapter.meta_description,
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Generate notes error: {e}")
        raise HTTPException(status_code=500, detail="Generation failed")


@router.post("/content/chapters/{chapter_id}/generate-notes/as")
async def generate_notes_assamese(request: Request, chapter_id: str):
    """Generate Assamese translation only for a chapter."""
    _validate_admin_session(request)
    await _csrf_check(request)

    try:
        chapter = await content_generation_service.generate_assamese_only(chapter_id)
        return {"status": "translated", "chapter_id": chapter_id}
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
    """Full publish pipeline: Azure Search + Cloudflare + status update."""
    _validate_admin_session(request)
    await _csrf_check(request)

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
    """Publish chapter to Azure Search index only."""
    _validate_admin_session(request)
    await _csrf_check(request)

    chapter = await Chapter.get(PydanticObjectId(chapter_id))
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    result = await content_publisher_service.publish_to_azure_search(chapter)
    return {"chapter_id": chapter_id, "result": result}


@router.post("/content/chapters/{chapter_id}/publish/pages")
async def publish_pages(request: Request, chapter_id: str):
    """Publish chapter pages to Cloudflare only."""
    _validate_admin_session(request)
    await _csrf_check(request)

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
    _validate_admin_session(request)
    await _csrf_check(request)

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
