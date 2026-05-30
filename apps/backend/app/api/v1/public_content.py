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
    Return the full content hierarchy for the library page.

    When slim=1, returns minimal data (titles, slugs, counts) without
    full chapter content. No authentication required.
    """
    response.headers["Cache-Control"] = "public, max-age=60, s-maxage=300"

    boards = await Board.find({"status": "active"}).to_list()
    classes = await Class.find({"status": "active"}).to_list()
    streams = await Stream.find({"status": "active"}).to_list()
    subjects = await Subject.find({"status": "active"}).to_list()
    chapters = await Chapter.find_all().to_list()

    # Index by parent ID for fast lookups
    classes_by_board: dict[str, list] = {}
    for cls in classes:
        key = str(cls.board_id)
        classes_by_board.setdefault(key, []).append(cls)

    streams_by_class: dict[str, list] = {}
    for stream in streams:
        key = str(stream.class_id)
        streams_by_class.setdefault(key, []).append(stream)

    subjects_by_stream: dict[str, list] = {}
    for subj in subjects:
        key = str(subj.stream_id)
        subjects_by_stream.setdefault(key, []).append(subj)

    chapters_by_subject: dict[str, list] = {}
    for ch in chapters:
        key = str(ch.subject_id)
        chapters_by_subject.setdefault(key, []).append(ch)

    result_boards = []
    for board in boards:
        board_id = str(board.id)
        board_classes = classes_by_board.get(board_id, [])

        result_classes = []
        for cls in board_classes:
            cls_id = str(cls.id)
            cls_streams = streams_by_class.get(cls_id, [])

            result_streams = []
            for stream in cls_streams:
                stream_id = str(stream.id)
                stream_subjects = subjects_by_stream.get(stream_id, [])

                result_subjects = []
                for subj in stream_subjects:
                    subj_id = str(subj.id)
                    subj_chapters = chapters_by_subject.get(subj_id, [])
                    subj_chapters.sort(key=lambda c: c.chapter_number)

                    chapter_list = []
                    for ch in subj_chapters:
                        ch_data = {
                            "id": str(ch.id),
                            "title": ch.title,
                            "slug": ch.slug,
                            "order": ch.chapter_number,
                            "topic_count": len(ch.published_topics),
                        }
                        chapter_list.append(ch_data)

                    subj_data = {
                        "id": subj_id,
                        "name": subj.name,
                        "slug": _slugify(subj.name),
                        "chapter_count": len(subj_chapters),
                    }
                    if not slim:
                        subj_data["chapters"] = chapter_list

                    result_subjects.append(subj_data)

                result_streams.append(
                    {
                        "id": stream_id,
                        "name": stream.name,
                        "slug": _slugify(stream.name),
                        "subjects": result_subjects,
                    }
                )

            result_classes.append(
                {
                    "id": cls_id,
                    "name": cls.name,
                    "slug": _slugify(cls.name),
                    "streams": result_streams,
                }
            )

        result_boards.append(
            {
                "id": board_id,
                "name": board.name,
                "slug": board.slug,
                "classes": result_classes,
            }
        )

    return {"boards": result_boards}


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
