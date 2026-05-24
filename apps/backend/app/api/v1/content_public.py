"""
Public Content Endpoints
Public-facing endpoints for FAQ JSON-LD, published topics, and topic indexes.
No admin authentication required.
"""

import logging
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import APIRouter, HTTPException

from app.config import settings
from app.db.mongo import get_mongo_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Content Public"])


def _get_db():
    return get_mongo_client()[settings.MONGODB_DB_NAME]


@router.get("/content/chapters/{chapter_id}/faq-jsonld")
async def chapter_faq_jsonld(chapter_id: str):
    """Return FAQ JSON-LD schema from chapter faq_entries."""
    db = _get_db()
    try:
        oid = ObjectId(chapter_id)
    except (InvalidId, Exception):
        raise HTTPException(status_code=400, detail="Invalid ID format")
    chapter = await db.chapters.find_one({"_id": oid})
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")
    faq_entries = chapter.get("faq_entries", [])
    main_entity = []
    for entry in faq_entries:
        question = entry.get("question", "")
        answer = entry.get("answer", "")
        if question and answer:
            main_entity.append({
                "@type": "Question",
                "name": question,
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": answer,
                },
            })
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": main_entity,
    }


@router.get("/content/chapters/{chapter_id}/published-topics")
async def chapter_published_topics(chapter_id: str):
    """Return topics from the chapter that have definition_status=published."""
    db = _get_db()
    try:
        oid = ObjectId(chapter_id)
    except (InvalidId, Exception):
        raise HTTPException(status_code=400, detail="Invalid ID format")
    chapter = await db.chapters.find_one({"_id": oid})
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")
    topics = chapter.get("topics", [])
    published = [t for t in topics if t.get("definition_status") == "published"]
    return {"topics": published, "chapter_id": chapter_id, "chapter_title": chapter.get("title", "")}


@router.get("/content/subjects/{subject_id}/topic-index")
async def subject_topic_index(subject_id: str):
    """Return all topics organized by chapter for the subject."""
    db = _get_db()
    try:
        ObjectId(subject_id)
    except (InvalidId, Exception):
        raise HTTPException(status_code=400, detail="Invalid ID format")
    chapters = await db.chapters.find({"subject_id": subject_id}).sort("order", 1).to_list(length=200)
    if not chapters:
        raise HTTPException(status_code=404, detail="No chapters found for this subject")
    result = []
    for ch in chapters:
        topics = ch.get("topics", [])
        result.append({
            "chapter_id": str(ch["_id"]),
            "chapter_title": ch.get("title", ""),
            "order": ch.get("order", 0),
            "topics": topics,
        })
    return {"subject_id": subject_id, "chapters": result}
