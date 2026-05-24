"""
Admin Content Management Endpoints
Full hierarchy CRUD, bilingual content, AI generation, and publishing pipeline.
"""

from datetime import datetime, timezone
import re
import logging
import asyncio

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from fastapi.responses import JSONResponse
import httpx

from app.api.v1.admin import _validate_admin_session, _csrf_check
from app.config import settings
from app.db.mongo import get_mongo_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin Content"])


# --- Helpers ---

def auto_slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def chunk_text(text, max_tokens=512):
    """Split text into chunks of approximately max_tokens words."""
    words = text.split()
    chunks = []
    for i in range(0, len(words), max_tokens):
        chunks.append(" ".join(words[i:i + max_tokens]))
    return chunks


def _get_db():
    client = get_mongo_client()
    return client[settings.MONGODB_DB_NAME]


# ============================================================
# LAYER 1 - Hierarchy CRUD: Boards
# ============================================================

@router.post("/content/boards")
async def create_board(request: Request):
    _validate_admin_session(request)
    await _csrf_check(request)
    body = await request.json()
    db = _get_db()
    doc = {
        "name": body.get("name"),
        "slug": auto_slug(body.get("name", "")),
        "description": body.get("description", ""),
        "status": body.get("status", "draft"),
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    result = await db.boards.insert_one(doc)
    doc["id"] = str(result.inserted_id)
    doc.pop("_id", None)
    return doc


@router.get("/content/boards")
async def list_boards(request: Request):
    _validate_admin_session(request)
    db = _get_db()
    boards = await db.boards.find().to_list(1000)
    result = []
    for b in boards:
        b["id"] = str(b.pop("_id"))
        result.append(b)
    return {"boards": result}


@router.patch("/content/boards/{board_id}")
async def update_board(request: Request, board_id: str):
    _validate_admin_session(request)
    await _csrf_check(request)
    body = await request.json()
    db = _get_db()
    update_fields = {}
    for field in ["name", "description", "status"]:
        if field in body:
            update_fields[field] = body[field]
    if "name" in update_fields:
        update_fields["slug"] = auto_slug(update_fields["name"])
    update_fields["updated_at"] = datetime.now(timezone.utc)
    result = await db.boards.update_one({"_id": ObjectId(board_id)}, {"$set": update_fields})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Board not found")
    return {"status": "ok", "id": board_id, "updated_fields": list(update_fields.keys())}


@router.delete("/content/boards/{board_id}")
async def delete_board(request: Request, board_id: str):
    _validate_admin_session(request)
    await _csrf_check(request)
    db = _get_db()
    result = await db.boards.delete_one({"_id": ObjectId(board_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Board not found")
    return {"status": "ok", "deleted": board_id}


# ============================================================
# LAYER 1 - Hierarchy CRUD: Classes
# ============================================================

@router.post("/content/classes")
async def create_class(request: Request):
    _validate_admin_session(request)
    await _csrf_check(request)
    body = await request.json()
    db = _get_db()
    doc = {
        "board_id": body.get("board_id"),
        "name": body.get("name"),
        "slug": auto_slug(body.get("name", "")),
        "description": body.get("description", ""),
        "status": body.get("status", "draft"),
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    result = await db.classes.insert_one(doc)
    doc["id"] = str(result.inserted_id)
    doc.pop("_id", None)
    return doc


@router.get("/content/classes")
async def list_classes(request: Request, board_id: str = None):
    _validate_admin_session(request)
    db = _get_db()
    query = {}
    if board_id:
        query["board_id"] = board_id
    classes = await db.classes.find(query).to_list(1000)
    result = []
    for c in classes:
        c["id"] = str(c.pop("_id"))
        result.append(c)
    return {"classes": result}


@router.patch("/content/classes/{class_id}")
async def update_class(request: Request, class_id: str):
    _validate_admin_session(request)
    await _csrf_check(request)
    body = await request.json()
    db = _get_db()
    update_fields = {}
    for field in ["name", "description", "status", "board_id"]:
        if field in body:
            update_fields[field] = body[field]
    if "name" in update_fields:
        update_fields["slug"] = auto_slug(update_fields["name"])
    update_fields["updated_at"] = datetime.now(timezone.utc)
    result = await db.classes.update_one({"_id": ObjectId(class_id)}, {"$set": update_fields})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Class not found")
    return {"status": "ok", "id": class_id, "updated_fields": list(update_fields.keys())}


@router.delete("/content/classes/{class_id}")
async def delete_class(request: Request, class_id: str):
    _validate_admin_session(request)
    await _csrf_check(request)
    db = _get_db()
    result = await db.classes.delete_one({"_id": ObjectId(class_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Class not found")
    return {"status": "ok", "deleted": class_id}


# ============================================================
# LAYER 1 - Hierarchy CRUD: Streams
# ============================================================

@router.post("/content/streams")
async def create_stream(request: Request):
    _validate_admin_session(request)
    await _csrf_check(request)
    body = await request.json()
    db = _get_db()
    doc = {
        "class_id": body.get("class_id"),
        "name": body.get("name"),
        "slug": auto_slug(body.get("name", "")),
        "description": body.get("description", ""),
        "status": body.get("status", "draft"),
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    result = await db.streams.insert_one(doc)
    doc["id"] = str(result.inserted_id)
    doc.pop("_id", None)
    return doc


@router.get("/content/streams")
async def list_streams(request: Request, class_id: str = None):
    _validate_admin_session(request)
    db = _get_db()
    query = {}
    if class_id:
        query["class_id"] = class_id
    streams = await db.streams.find(query).to_list(1000)
    result = []
    for s in streams:
        s["id"] = str(s.pop("_id"))
        result.append(s)
    return {"streams": result}


@router.delete("/content/streams/{stream_id}")
async def delete_stream(request: Request, stream_id: str):
    _validate_admin_session(request)
    await _csrf_check(request)
    db = _get_db()
    result = await db.streams.delete_one({"_id": ObjectId(stream_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Stream not found")
    return {"status": "ok", "deleted": stream_id}


# ============================================================
# LAYER 1 - Hierarchy CRUD: Subjects
# ============================================================

@router.post("/content/subjects")
async def create_subject(request: Request):
    _validate_admin_session(request)
    await _csrf_check(request)
    body = await request.json()
    db = _get_db()

    # Denormalize parent names
    board_name = ""
    class_name = ""
    stream_name = ""
    stream_id = body.get("stream_id")
    if stream_id:
        stream_doc = await db.streams.find_one({"_id": ObjectId(stream_id)})
        if stream_doc:
            stream_name = stream_doc.get("name", "")
            class_id = stream_doc.get("class_id")
            if class_id:
                class_doc = await db.classes.find_one({"_id": ObjectId(class_id)})
                if class_doc:
                    class_name = class_doc.get("name", "")
                    board_id = class_doc.get("board_id")
                    if board_id:
                        board_doc = await db.boards.find_one({"_id": ObjectId(board_id)})
                        if board_doc:
                            board_name = board_doc.get("name", "")

    doc = {
        "stream_id": stream_id,
        "name": body.get("name"),
        "slug": auto_slug(body.get("name", "")),
        "description": body.get("description", ""),
        "tags": body.get("tags", []),
        "status": body.get("status", "draft"),
        "board_name": board_name,
        "class_name": class_name,
        "stream_name": stream_name,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    result = await db.subjects.insert_one(doc)
    doc["id"] = str(result.inserted_id)
    doc.pop("_id", None)
    return doc


@router.get("/content/subjects")
async def list_subjects(request: Request, stream_id: str = None):
    _validate_admin_session(request)
    db = _get_db()
    query = {}
    if stream_id:
        query["stream_id"] = stream_id
    subjects = await db.subjects.find(query).to_list(1000)
    result = []
    for s in subjects:
        s["id"] = str(s.pop("_id"))
        result.append(s)
    return {"subjects": result, "total": len(result)}


@router.patch("/content/subjects/{subject_id}")
async def update_subject(request: Request, subject_id: str):
    _validate_admin_session(request)
    await _csrf_check(request)
    body = await request.json()
    db = _get_db()
    allowed_fields = ["name", "description", "tags", "status", "category", "grade_level"]
    update_fields = {}
    for field in allowed_fields:
        if field in body:
            update_fields[field] = body[field]
    if not update_fields:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    if "name" in update_fields:
        update_fields["slug"] = auto_slug(update_fields["name"])
    update_fields["updated_at"] = datetime.now(timezone.utc)
    result = await db.subjects.update_one({"_id": ObjectId(subject_id)}, {"$set": update_fields})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Subject not found")
    return {"status": "ok", "subject_id": subject_id, "updated_fields": list(update_fields.keys())}


@router.delete("/content/subjects/{subject_id}")
async def delete_subject(request: Request, subject_id: str):
    _validate_admin_session(request)
    await _csrf_check(request)
    db = _get_db()
    result = await db.subjects.delete_one({"_id": ObjectId(subject_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Subject not found")
    return {"status": "ok", "deleted": subject_id}


# ============================================================
# LAYER 1 - Chapter CRUD
# ============================================================

@router.post("/content/chapters")
async def create_chapter(request: Request):
    _validate_admin_session(request)
    await _csrf_check(request)
    body = await request.json()
    db = _get_db()
    title = body.get("title", "")
    doc = {
        "subject_id": body.get("subject_id"),
        "title": title,
        "slug": body.get("slug") or auto_slug(title),
        "description": body.get("description", ""),
        "content": body.get("content", ""),
        "content_en": body.get("content", ""),
        "content_as": body.get("content_as", ""),
        "content_type": body.get("content_type", "notes"),
        "order": body.get("order", 0),
        "status": body.get("status", "draft"),
        "topics": body.get("topics", []),
        "word_count": 0,
        "has_assamese": False,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    result = await db.chapters.insert_one(doc)
    doc["id"] = str(result.inserted_id)
    doc.pop("_id", None)
    return doc


@router.get("/content/chapters/{subject_id}")
async def list_chapters(request: Request, subject_id: str):
    _validate_admin_session(request)
    db = _get_db()
    chapters = await db.chapters.find({"subject_id": subject_id}).to_list(1000)
    result = []
    for c in chapters:
        c["id"] = str(c.pop("_id"))
        result.append(c)
    return {"chapters": result}


@router.patch("/content/chapters/{chapter_id}")
async def update_chapter(request: Request, chapter_id: str):
    _validate_admin_session(request)
    await _csrf_check(request)
    body = await request.json()
    db = _get_db()
    allowed_fields = [
        "title", "slug", "description", "content", "content_en", "content_as",
        "content_type", "order", "status", "topics", "meta_description",
        "word_count", "has_assamese"
    ]
    update_fields = {}
    for field in allowed_fields:
        if field in body:
            update_fields[field] = body[field]
    if "title" in update_fields and "slug" not in update_fields:
        update_fields["slug"] = auto_slug(update_fields["title"])
    update_fields["updated_at"] = datetime.now(timezone.utc)
    result = await db.chapters.update_one({"_id": ObjectId(chapter_id)}, {"$set": update_fields})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Chapter not found")
    return {"status": "ok", "id": chapter_id, "updated_fields": list(update_fields.keys())}


@router.delete("/content/chapters/{chapter_id}")
async def delete_chapter(request: Request, chapter_id: str):
    _validate_admin_session(request)
    await _csrf_check(request)
    db = _get_db()
    result = await db.chapters.delete_one({"_id": ObjectId(chapter_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Chapter not found")
    return {"status": "ok", "deleted": chapter_id}


# ============================================================
# LAYER 2 - Topic Management
# ============================================================

@router.post("/content/chapters/{chapter_id}/topics")
async def add_topics(request: Request, chapter_id: str):
    _validate_admin_session(request)
    await _csrf_check(request)
    body = await request.json()
    db = _get_db()
    topics = body.get("topics", [])
    prepared_topics = []
    for t in topics:
        prepared_topics.append({
            "topic_id": str(ObjectId()),
            "title": t.get("title", ""),
            "topic_slug": auto_slug(t.get("title", "")),
            "definition": t.get("definition", ""),
            "definition_status": "draft",
            "created_at": datetime.now(timezone.utc),
        })
    result = await db.chapters.update_one(
        {"_id": ObjectId(chapter_id)},
        {"$push": {"topics": {"$each": prepared_topics}}, "$set": {"updated_at": datetime.now(timezone.utc)}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Chapter not found")
    return {"status": "ok", "topics_added": len(prepared_topics), "topics": prepared_topics}


@router.get("/content/chapters/{chapter_id}/topics")
async def get_topics(request: Request, chapter_id: str):
    _validate_admin_session(request)
    db = _get_db()
    chapter = await db.chapters.find_one({"_id": ObjectId(chapter_id)})
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")
    return {"topics": chapter.get("topics", [])}


@router.patch("/content/topics/{topic_id}")
async def update_topic(request: Request, topic_id: str):
    _validate_admin_session(request)
    await _csrf_check(request)
    body = await request.json()
    db = _get_db()
    # Find chapter containing this topic
    chapter = await db.chapters.find_one({"topics.topic_id": topic_id})
    if not chapter:
        raise HTTPException(status_code=404, detail="Topic not found")
    # Update the specific topic in the array
    update_fields = {}
    for field in ["title", "definition", "definition_status"]:
        if field in body:
            update_fields[f"topics.$.{field}"] = body[field]
    if "title" in body:
        update_fields["topics.$.topic_slug"] = auto_slug(body["title"])
    update_fields["updated_at"] = datetime.now(timezone.utc)
    await db.chapters.update_one(
        {"topics.topic_id": topic_id},
        {"$set": update_fields}
    )
    return {"status": "ok", "topic_id": topic_id}


@router.delete("/content/topics/{topic_id}")
async def delete_topic(request: Request, topic_id: str):
    _validate_admin_session(request)
    await _csrf_check(request)
    db = _get_db()
    result = await db.chapters.update_one(
        {"topics.topic_id": topic_id},
        {"$pull": {"topics": {"topic_id": topic_id}}, "$set": {"updated_at": datetime.now(timezone.utc)}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Topic not found")
    return {"status": "ok", "deleted": topic_id}


# ============================================================
# LAYER 2 - Bilingual Content Editing
# ============================================================

@router.put("/content/chapters/{chapter_id}/content/en")
async def update_english_content(request: Request, chapter_id: str):
    _validate_admin_session(request)
    await _csrf_check(request)
    body = await request.json()
    db = _get_db()
    content_text = body.get("content", "")
    word_count = len(content_text.split())
    result = await db.chapters.update_one(
        {"_id": ObjectId(chapter_id)},
        {"$set": {"content_en": content_text, "word_count": word_count, "updated_at": datetime.now(timezone.utc)}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Chapter not found")
    return {"status": "ok", "language": "en", "word_count": word_count}


@router.put("/content/chapters/{chapter_id}/content/as")
async def update_assamese_content(request: Request, chapter_id: str):
    _validate_admin_session(request)
    await _csrf_check(request)
    body = await request.json()
    db = _get_db()
    content_text = body.get("content", "")
    result = await db.chapters.update_one(
        {"_id": ObjectId(chapter_id)},
        {"$set": {"content_as": content_text, "has_assamese": True, "updated_at": datetime.now(timezone.utc)}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Chapter not found")
    return {"status": "ok", "language": "as", "word_count": len(content_text.split())}


@router.get("/content/chapters/{chapter_id}/content/{lang}")
async def get_chapter_content(request: Request, chapter_id: str, lang: str):
    _validate_admin_session(request)
    db = _get_db()
    chapter = await db.chapters.find_one({"_id": ObjectId(chapter_id)})
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")
    field = f"content_{lang}"
    content_text = chapter.get(field, "")
    return {"language": lang, "content": content_text, "word_count": len(content_text.split())}


# ============================================================
# LAYER 3 - AI Generation
# ============================================================

@router.post("/content/chapters/{chapter_id}/generate-notes")
async def generate_notes(request: Request, chapter_id: str):
    _validate_admin_session(request)
    await _csrf_check(request)
    db = _get_db()

    chapter = await db.chapters.find_one({"_id": ObjectId(chapter_id)})
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    topics = chapter.get("topics", [])
    topic_lines = []
    for t in topics:
        title = t.get("title", "")
        definition = t.get("definition", "")
        topic_lines.append(f"- {title}: {definition}")
    topic_list = "\n".join(topic_lines)

    system_prompt = (
        "You are an expert educational content writer for AHSEC/SEBA board students in Assam. "
        "Write comprehensive, well-structured study notes that are clear and informative. "
        "Use headings, bullet points, and examples where appropriate."
    )
    chapter_title = chapter.get("title", "")
    user_message = (
        f"Write detailed study notes for the chapter: {chapter_title}\n\n"
        f"Topics to cover:\n{topic_list}\n\n"
        "Requirements:\n"
        "- Minimum 1500 words\n"
        "- Cover each topic thoroughly\n"
        "- Include key definitions and explanations\n"
        "- Use clear headings for each topic\n"
        "- Add summary points at the end"
    )

    content_en = ""
    content_as = ""

    # Generate English content
    try:
        from app.services.ai.vertex_client import vertex_client
        content_en = await vertex_client.generate(system_prompt, user_message)
    except Exception as e:
        logger.error(f"Vertex AI generation failed for chapter {chapter_id}: {e}")
        content_en = ""

    # Generate Assamese translation
    try:
        from app.services.ai.sarvam_client import sarvam_client
        if content_en:
            translate_system = "You are an expert translator. Translate the following educational content to Assamese language accurately."
            translate_user = f"Translate this to Assamese:\n\n{content_en}"
            content_as = await sarvam_client.generate(translate_system, translate_user)
    except Exception as e:
        logger.error(f"Sarvam translation failed for chapter {chapter_id}: {e}")
        content_as = ""

    # Extract meta description
    meta_description = content_en[:160] if content_en else ""
    word_count = len(content_en.split()) if content_en else 0
    content_as_words = len(content_as.split()) if content_as else 0

    # Update chapter
    update_data = {
        "content_en": content_en,
        "content_as": content_as,
        "meta_description": meta_description,
        "word_count": word_count,
        "has_assamese": bool(content_as),
        "generated_at": datetime.now(timezone.utc),
        "status": "generated" if content_en else chapter.get("status", "draft"),
        "updated_at": datetime.now(timezone.utc),
    }
    await db.chapters.update_one({"_id": ObjectId(chapter_id)}, {"$set": update_data})

    return {
        "content": content_en,
        "content_as": content_as,
        "word_count": word_count,
        "content_as_words": content_as_words,
    }


# ============================================================
# LAYER 3 - Publishing
# ============================================================

@router.post("/content/chapters/{chapter_id}/publish")
async def publish_chapter(request: Request, chapter_id: str):
    _validate_admin_session(request)
    await _csrf_check(request)
    db = _get_db()

    chapter = await db.chapters.find_one({"_id": ObjectId(chapter_id)})
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    content_en = chapter.get("content_en", "")
    if not content_en:
        raise HTTPException(status_code=400, detail="No English content to publish")

    # Chunk content for Azure Search
    chunks = chunk_text(content_en, max_tokens=512)
    documents = []
    chapter_slug = chapter.get("slug", chapter_id)
    for i, chunk in enumerate(chunks):
        documents.append({
            "@search.action": "upload",
            "id": f"{chapter_id}-{i}",
            "title": chapter.get("title", ""),
            "content": chunk,
            "language": "en",
            "tier_access": "free",
            "source_url": f"/chapters/{chapter_slug}",
            "last_updated": datetime.now(timezone.utc).isoformat(),
        })

    # Push to Azure Search
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{settings.AZURE_SEARCH_ENDPOINT}/indexes/{settings.AZURE_SEARCH_INDEX_NAME}/docs/index?api-version=2023-11-01",
                headers={
                    "Content-Type": "application/json",
                    "api-key": settings.AZURE_SEARCH_ADMIN_KEY or "",
                },
                json={"value": documents},
            )
            response.raise_for_status()
    except Exception as e:
        logger.error(f"Azure Search indexing failed for chapter {chapter_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Search indexing failed: {str(e)}")

    # Mark as published
    await db.chapters.update_one(
        {"_id": ObjectId(chapter_id)},
        {"$set": {"status": "published", "published_at": datetime.now(timezone.utc), "updated_at": datetime.now(timezone.utc)}}
    )

    return {"status": "published", "chunks_indexed": len(documents)}


@router.post("/content/chapters/{chapter_id}/publish/search-index")
async def index_to_search(request: Request, chapter_id: str):
    _validate_admin_session(request)
    await _csrf_check(request)
    db = _get_db()

    chapter = await db.chapters.find_one({"_id": ObjectId(chapter_id)})
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    content_en = chapter.get("content_en", "")
    if not content_en:
        raise HTTPException(status_code=400, detail="No English content to index")

    chunks = chunk_text(content_en, max_tokens=512)
    documents = []
    chapter_slug = chapter.get("slug", chapter_id)
    for i, chunk in enumerate(chunks):
        documents.append({
            "@search.action": "upload",
            "id": f"{chapter_id}-{i}",
            "title": chapter.get("title", ""),
            "content": chunk,
            "language": "en",
            "tier_access": "free",
            "source_url": f"/chapters/{chapter_slug}",
            "last_updated": datetime.now(timezone.utc).isoformat(),
        })

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{settings.AZURE_SEARCH_ENDPOINT}/indexes/{settings.AZURE_SEARCH_INDEX_NAME}/docs/index?api-version=2023-11-01",
                headers={
                    "Content-Type": "application/json",
                    "api-key": settings.AZURE_SEARCH_ADMIN_KEY or "",
                },
                json={"value": documents},
            )
            response.raise_for_status()
    except Exception as e:
        logger.error(f"Azure Search indexing failed for chapter {chapter_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Search indexing failed: {str(e)}")

    return {"status": "indexed", "chunks_indexed": len(documents)}


# ============================================================
# Supporting Endpoints
# ============================================================

@router.post("/content/bulk-status")
async def bulk_update_status(request: Request):
    _validate_admin_session(request)
    await _csrf_check(request)
    body = await request.json()
    db = _get_db()
    scope = body.get("scope", "subjects")
    ids = body.get("ids", [])
    status = body.get("status", "draft")

    if not ids:
        raise HTTPException(status_code=400, detail="No ids provided")

    collection = db.subjects if scope == "subjects" else db.chapters
    object_ids = [ObjectId(i) for i in ids]
    result = await collection.update_many(
        {"_id": {"$in": object_ids}},
        {"$set": {"status": status, "updated_at": datetime.now(timezone.utc)}}
    )
    return {"status": "ok", "modified_count": result.modified_count}


@router.get("/content/subject/{subject_id}/chapter-cards")
async def get_chapter_cards(request: Request, subject_id: str):
    _validate_admin_session(request)
    db = _get_db()
    chapters = await db.chapters.find({"subject_id": subject_id}).to_list(1000)
    cards = []
    for c in chapters:
        cards.append({
            "id": str(c["_id"]),
            "title": c.get("title", ""),
            "status": c.get("status", "draft"),
            "word_count": c.get("word_count", 0),
            "has_assamese": c.get("has_assamese", False),
            "topics_count": len(c.get("topics", [])),
            "order": c.get("order", 0),
            "updated_at": c.get("updated_at"),
        })
    return {"cards": cards, "total": len(cards)}


@router.get("/content/chapters/{chapter_id}/stats")
async def get_chapter_stats(request: Request, chapter_id: str):
    _validate_admin_session(request)
    db = _get_db()
    chapter = await db.chapters.find_one({"_id": ObjectId(chapter_id)})
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")
    content_en = chapter.get("content_en", "")
    content_as = chapter.get("content_as", "")
    return {
        "id": str(chapter["_id"]),
        "title": chapter.get("title", ""),
        "word_count": len(content_en.split()) if content_en else 0,
        "word_count_as": len(content_as.split()) if content_as else 0,
        "topics_count": len(chapter.get("topics", [])),
        "has_assamese": chapter.get("has_assamese", False),
        "status": chapter.get("status", "draft"),
        "generated_at": chapter.get("generated_at"),
        "published_at": chapter.get("published_at"),
    }


@router.post("/content/chapters/{chapter_id}/translate")
async def translate_chapter(request: Request, chapter_id: str):
    _validate_admin_session(request)
    await _csrf_check(request)
    body = await request.json()
    db = _get_db()

    chapter = await db.chapters.find_one({"_id": ObjectId(chapter_id)})
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    target_lang = body.get("target_lang", "as")
    source_content = chapter.get("content_en", "")
    if not source_content:
        raise HTTPException(status_code=400, detail="No source content to translate")

    try:
        from app.services.ai.sarvam_client import sarvam_client
        lang_name = 'Assamese' if target_lang == 'as' else target_lang
        system_prompt = f"You are an expert translator. Translate the following educational content to {lang_name} language accurately, maintaining the structure and formatting."
        user_message = f"Translate this content:\n\n{source_content}"
        translated = await sarvam_client.generate(system_prompt, user_message)
    except Exception as e:
        logger.error(f"Translation failed for chapter {chapter_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Translation failed: {str(e)}")

    # Update chapter with translation
    update_field = f"content_{target_lang}"
    update_data = {update_field: translated, "has_assamese": True, "updated_at": datetime.now(timezone.utc)}
    await db.chapters.update_one({"_id": ObjectId(chapter_id)}, {"$set": update_data})

    return {"status": "ok", "language": target_lang, "word_count": len(translated.split())}


@router.post("/content/chapters/{chapter_id}/attach-file")
async def attach_file(request: Request, chapter_id: str):
    _validate_admin_session(request)
    await _csrf_check(request)
    db = _get_db()

    chapter = await db.chapters.find_one({"_id": ObjectId(chapter_id)})
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    body = await request.json()
    file_text = body.get("text", "")
    file_name = body.get("file_name", "uploaded_file")

    # Store the raw extracted text as an attachment
    attachment = {
        "file_name": file_name,
        "text": file_text,
        "attached_at": datetime.now(timezone.utc),
    }
    await db.chapters.update_one(
        {"_id": ObjectId(chapter_id)},
        {"$push": {"attachments": attachment}, "$set": {"updated_at": datetime.now(timezone.utc)}}
    )
    return {"status": "ok", "file_name": file_name, "text_length": len(file_text)}


@router.post("/content/upload-image")
async def upload_image(request: Request):
    _validate_admin_session(request)
    await _csrf_check(request)
    # Placeholder - return a dummy URL
    return {"status": "ok", "url": "/images/placeholder.png", "message": "Image upload placeholder"}


@router.post("/content/subject/{subject_id}/format-notes")
async def format_notes(request: Request, subject_id: str):
    _validate_admin_session(request)
    await _csrf_check(request)
    # Placeholder - return success
    return {"status": "ok", "message": "Notes formatting placeholder", "subject_id": subject_id}


@router.get("/content/chapters/{subject_id}/coverage")
async def get_coverage(request: Request, subject_id: str):
    _validate_admin_session(request)
    db = _get_db()
    chapters = await db.chapters.find({"subject_id": subject_id}).to_list(1000)
    coverage = []
    for c in chapters:
        topics = c.get("topics", [])
        total_topics = len(topics)
        covered_topics = sum(1 for t in topics if t.get("definition_status") == "complete")
        word_count = c.get("word_count", 0)
        coverage.append({
            "id": str(c["_id"]),
            "title": c.get("title", ""),
            "total_topics": total_topics,
            "covered_topics": covered_topics,
            "coverage_pct": round((covered_topics / total_topics * 100) if total_topics > 0 else 0, 1),
            "word_count": word_count,
            "has_content": word_count > 0,
            "has_assamese": c.get("has_assamese", False),
        })
    return {"coverage": coverage}


@router.post("/content/regenerate-sitemap")
async def regenerate_sitemap(request: Request):
    _validate_admin_session(request)
    await _csrf_check(request)
    # Placeholder - return success
    return {"status": "ok", "message": "Sitemap regeneration placeholder"}


# ============================================================
# Legacy endpoint (kept for backward compatibility)
# ============================================================

@router.get("/content/draft-served-subjects")
async def get_draft_served_subjects(request: Request):
    """Get subjects with status='draft' that are being served."""
    _validate_admin_session(request)
    db = _get_db()
    try:
        subjects = await db.subjects.find({"status": "draft"}).to_list(100)
        result = []
        for s in subjects:
            result.append({
                "id": str(s["_id"]),
                "name": s.get("name"),
                "status": s.get("status"),
                "category": s.get("category"),
                "created_at": s.get("created_at", "").isoformat() if s.get("created_at") else None,
                "updated_at": s.get("updated_at", "").isoformat() if s.get("updated_at") else None,
            })
        return {"subjects": result, "total": len(result)}
    except Exception as e:
        logger.error(f"Get draft subjects error: {e}")
        return {"subjects": [], "total": 0}

