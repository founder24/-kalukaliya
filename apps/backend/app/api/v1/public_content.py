"""
Public Content API - Unauthenticated endpoints for FAQ JSON-LD, published topics,
and the library bundle used by the frontend library page.
"""

import logging
import re

from beanie import PydanticObjectId
from fastapi import APIRouter, HTTPException, Query, Response

from app.models.content import Board, Chapter, Class, Stream, Subject

logger = logging.getLogger(__name__)

router = APIRouter()


def _slugify(text: str) -> str:
    """Generate a URL-friendly slug from text."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-")


@router.get("/library-bundle")
async def get_library_bundle(response: Response, slim: int = Query(0)):
    """
    Return the full content hierarchy for the library page as flat arrays.

    Returns separate top-level arrays for boards, classes, streams, subjects,
    and chapters (unless slim=1, which omits chapters).
    No authentication required.
    """
    response.headers["Cache-Control"] = "public, max-age=60, s-maxage=300"

    boards = await Board.find({"status": "active"}).to_list()
    classes = await Class.find({"status": "active"}).to_list()
    streams = await Stream.find({"status": "active"}).to_list()
    subjects = await Subject.find({"status": "active"}).to_list()
    chapters = await Chapter.find_all().to_list()

    # Build lookup maps for parent relationships
    board_by_id: dict[str, object] = {str(b.id): b for b in boards}
    class_by_id: dict[str, object] = {str(c.id): c for c in classes}
    stream_by_id: dict[str, object] = {str(s.id): s for s in streams}

    # Count chapters per subject
    chapters_by_subject: dict[str, list] = {}
    for ch in chapters:
        key = str(ch.subject_id)
        chapters_by_subject.setdefault(key, []).append(ch)

    # Build flat boards array
    result_boards = []
    for board in boards:
        result_boards.append({
            "id": str(board.id),
            "name": board.name,
            "slug": board.slug,
        })

    # Build flat classes array
    result_classes = []
    for cls in classes:
        result_classes.append({
            "id": str(cls.id),
            "name": cls.name,
            "slug": _slugify(cls.name),
            "board_id": str(cls.board_id),
        })

    # Build flat streams array
    result_streams = []
    for stream in streams:
        result_streams.append({
            "id": str(stream.id),
            "name": stream.name,
            "slug": _slugify(stream.name),
            "class_id": str(stream.class_id),
        })

    # Build flat subjects array with parent slugs for URL construction
    result_subjects = []
    for subj in subjects:
        stream_id = str(subj.stream_id)
        stream_obj = stream_by_id.get(stream_id)
        class_id = str(stream_obj.class_id) if stream_obj else ""
        class_obj = class_by_id.get(class_id) if class_id else None
        board_id = str(class_obj.board_id) if class_obj else ""
        board_obj = board_by_id.get(board_id) if board_id else None

        subj_chapters = chapters_by_subject.get(str(subj.id), [])

        result_subjects.append({
            "id": str(subj.id),
            "name": subj.name,
            "slug": _slugify(subj.name),
            "stream_id": stream_id,
            "board_id": board_id,
            "class_id": class_id,
            "boardSlug": board_obj.slug if board_obj else "",
            "classSlug": _slugify(class_obj.name) if class_obj else "",
            "chapter_count": len(subj_chapters),
        })

    result = {
        "boards": result_boards,
        "classes": result_classes,
        "streams": result_streams,
        "subjects": result_subjects,
    }

    # Include chapters unless slim mode is requested
    if not slim:
        result_chapters = []
        for ch in chapters:
            result_chapters.append({
                "id": str(ch.id),
                "title": ch.title,
                "slug": ch.slug,
                "subject_id": str(ch.subject_id),
                "chapter_number": ch.chapter_number,
                "topic_count": len(ch.published_topics),
            })
        result["chapters"] = result_chapters

    return result


@router.get("/chapters/{chapter_id}/faq-jsonld")
async def get_faq_jsonld(chapter_id: str):
    """Get FAQ JSON-LD structured data for a chapter (no auth required)."""
    chapter = await Chapter.get(PydanticObjectId(chapter_id))
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    return {"chapter_id": chapter_id, "faq_jsonld": chapter.faq_jsonld or []}


@router.get("/chapters/{chapter_id}/published-topics")
async def get_published_topics(chapter_id: str):
    """Get published topics for a chapter (no auth required)."""
    chapter = await Chapter.get(PydanticObjectId(chapter_id))
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    return {
        "chapter_id": chapter_id,
        "topics": [t.model_dump() for t in chapter.published_topics],
        "total": len(chapter.published_topics),
    }
