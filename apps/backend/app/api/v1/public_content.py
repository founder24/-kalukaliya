"""
Public Content API - Unauthenticated endpoints for FAQ JSON-LD, published topics,
and the library bundle used by the frontend library page.
"""

import logging
import re
from typing import Optional

from beanie import PydanticObjectId
from fastapi import APIRouter, HTTPException, Query, Response

from app.config import settings
from app.models.content import Board, Chapter, Class, Stream, Subject, QuestionPaper

logger = logging.getLogger(__name__)

router = APIRouter()


def _slugify(text: str) -> str:
    """Generate a URL-friendly slug from text."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-")


@router.get("/library-bundle")
async def get_library_bundle(
    response: Response,
    slim: int = Query(0),
    boot: Optional[str] = Query(None),
):
    """
    Return the full content hierarchy for the library page.

    When slim=1, returns minimal data (titles, slugs, counts) without
    full chapter content. No authentication required.

    When boot=<boardId>, returns slim metadata for all boards/classes/streams/subjects
    but chapters are scoped to only that board — a lightweight first-paint payload
    (~150-300KB vs ~1MB for the full bundle).
    """
    response.headers["Cache-Control"] = "public, max-age=60, s-maxage=300"

    is_boot = bool(boot)
    is_slim = bool(slim) or is_boot

    try:
        boards = await Board.find({"status": "active"}).to_list()
        classes = await Class.find({"status": "active"}).to_list()
        streams = await Stream.find({"status": "active"}).to_list()
        subjects = await Subject.find({"status": "active"}).to_list()
        chapters = await Chapter.find().to_list()
    except Exception as e:
        logger.warning(f"Library bundle DB query failed (DB may not be ready): {e}")
        return {"boards": []}

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
    flat_classes = []
    flat_streams = []
    flat_subjects = []
    flat_chapters = []

    # Determine which subject IDs belong to the boot board (for chapter scoping)
    boot_subject_ids: set[str] = set()
    if is_boot:
        for board in boards:
            if str(board.id) != boot:
                continue
            for cls in classes_by_board.get(str(board.id), []):
                for stream in streams_by_class.get(str(cls.id), []):
                    for subj in subjects_by_stream.get(str(stream.id), []):
                        boot_subject_ids.add(str(subj.id))

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

                    # Compute per-subject chapter stats from the chapter list
                    notes_count = sum(
                        1 for ch in subj_chapters
                        if ch.notes_generated or ch.content_en or ch.content_as
                    )
                    chapter_count = len(subj_chapters)
                    notes_pct = int(notes_count / chapter_count * 100) if chapter_count else 0

                    chapter_list = []
                    for ch in subj_chapters:
                        ch_data = {
                            "id": str(ch.id),
                            "title": ch.title,
                            "slug": ch.slug,
                            "subject_id": subj_id,
                            "order": ch.chapter_number,
                            "topic_count": len(ch.published_topics),
                            "notes_generated": ch.notes_generated or bool(ch.content_en or ch.content_as),
                            "status": ch.status,
                        }
                        chapter_list.append(ch_data)

                        # Include in flat_chapters when:
                        #   full bundle (not slim, not boot): all chapters
                        #   boot bundle: only this board's chapters
                        if not is_slim:
                            flat_chapters.append(ch_data)
                        elif is_boot and subj_id in boot_subject_ids:
                            flat_chapters.append(ch_data)

                    subj_slug = subj.slug or _slugify(subj.name)
                    subj_data = {
                        "id": subj_id,
                        "name": subj.name,
                        "slug": subj_slug,
                        "stream_id": stream_id,
                        "status": subj.status,
                        "description": subj.description,
                        "tags": subj.tags or [],
                        "seo_stats": subj.seo_stats,
                        "icon": subj.icon,
                        "gradient": subj.gradient,
                        "thumbnailUrl": subj.thumbnail_url,
                        "has_document": subj.has_document,
                        "chapter_count": chapter_count,
                        "notes_count": notes_count,
                        "notes_pct": notes_pct,
                    }
                    if not is_slim:
                        subj_data["chapters"] = chapter_list

                    result_subjects.append(subj_data)
                    flat_subjects.append(subj_data)

                stream_entry = {
                    "id": stream_id,
                    "name": stream.name,
                    "slug": _slugify(stream.name),
                    "class_id": cls_id,
                    "subjects": result_subjects,
                }
                result_streams.append(stream_entry)
                flat_streams.append(
                    {
                        "id": stream_id,
                        "name": stream.name,
                        "slug": _slugify(stream.name),
                        "class_id": cls_id,
                    }
                )

            cls_entry = {
                "id": cls_id,
                "name": cls.name,
                "slug": _slugify(cls.name),
                "board_id": board_id,
                "streams": result_streams,
            }
            result_classes.append(cls_entry)
            flat_classes.append(
                {
                    "id": cls_id,
                    "name": cls.name,
                    "slug": _slugify(cls.name),
                    "board_id": board_id,
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

    result = {
        "boards": result_boards,
        "classes": flat_classes,
        "streams": flat_streams,
        "subjects": flat_subjects,
    }
    if not is_slim:
        result["chapters"] = flat_chapters
    elif is_boot and flat_chapters:
        result["chapters"] = flat_chapters
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


@router.get("/question-papers")
async def get_question_papers(
    response: Response,
    board: str = Query(None),
    class_level: str = Query(None),
    subject: str = Query(None),
    limit: int = Query(100, ge=1, le=500),
    skip: int = Query(0, ge=0),
):
    """
    Return published question papers with R2 image URLs.
    Supports optional filtering by board, class_level, and subject.
    Results are sorted by year (newest first) and paginated via limit/skip.
    """
    response.headers["Cache-Control"] = "public, max-age=60, s-maxage=300"

    try:
        query = {"status": "published"}
        if board:
            query["board"] = board
        if class_level:
            query["class_level"] = class_level
        if subject:
            query["subject"] = subject

        papers = (
            await QuestionPaper.find(query)
            .sort("-year")
            .skip(skip)
            .limit(limit)
            .to_list()
        )
    except Exception as e:
        logger.warning(f"Question papers DB query failed: {e}")
        return []

    asset_base = settings.CF_WORKER_URL.rstrip("/")

    return [
        {
            "id": str(paper.id),
            "title": paper.title,
            "slug": paper.slug,
            "r2_key": paper.r2_key,
            "image_url": f"{asset_base}/assets/{paper.r2_key}",
            "board": paper.board,
            "class_level": paper.class_level,
            "subject": paper.subject,
            "year": paper.year,
        }
        for paper in papers
    ]
