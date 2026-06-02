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
        _status_q = {"status": {"$in": ["active", "published"]}}
        boards = await Board.find(_status_q).to_list()
        classes = await Class.find(_status_q).to_list()
        streams = await Stream.find(_status_q).to_list()
        subjects = await Subject.find(_status_q).to_list()
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


@router.get("/resolve-subject/{board}/{class_slug}/{subject_slug}")
async def resolve_subject(
    board: str,
    class_slug: str,
    subject_slug: str,
    response: Response,
):
    """
    Resolve a subject from URL slugs to its full document with breadcrumb context.

    Used by SubjectLandingPage for direct-URL loads: /{board}/{classSlug}/{subjectSlug}
    No authentication required — subject pages are publicly accessible.

    Returns the subject with board_name, class_name, stream_name filled in so the
    page can render breadcrumbs and metadata without a separate hierarchy fetch.
    Chapters are NOT included — use GET /content/chapters/{subject_id} for those.
    """
    response.headers["Cache-Control"] = "public, max-age=60, s-maxage=300"

    # 1. Resolve board by stored slug field
    board_doc = await Board.find_one({"slug": board, "status": "active"})
    if not board_doc:
        # Fallback: try case-insensitive slugify match on name
        all_boards = await Board.find({"status": "active"}).to_list()
        board_doc = next(
            (b for b in all_boards if _slugify(b.name) == board or b.slug == board),
            None,
        )
    if not board_doc:
        raise HTTPException(status_code=404, detail=f"Board '{board}' not found")

    # 2. Resolve class — Class has no stored slug, compute from name
    classes = await Class.find({"board_id": board_doc.id, "status": "active"}).to_list()
    matching_class = next(
        (c for c in classes if _slugify(c.name) == class_slug),
        None,
    )
    if not matching_class:
        raise HTTPException(
            status_code=404,
            detail=f"Class '{class_slug}' not found under board '{board}'",
        )

    # 3. Load all streams for this class
    streams = await Stream.find(
        {"class_id": matching_class.id, "status": "active"}
    ).to_list()
    if not streams:
        raise HTTPException(
            status_code=404,
            detail=f"No streams found for class '{class_slug}'",
        )

    # 4. Find the matching subject across all streams in this class
    #    Subject.slug may be stored or must be derived from name.
    stream_id_list = [s.id for s in streams]
    candidates = await Subject.find(
        {"stream_id": {"$in": stream_id_list}, "status": "active"}
    ).to_list()

    subject_doc = next(
        (s for s in candidates if (s.slug or _slugify(s.name)) == subject_slug),
        None,
    )
    if not subject_doc:
        raise HTTPException(
            status_code=404,
            detail=f"Subject '{subject_slug}' not found under '{board}/{class_slug}'",
        )

    # 5. Find the stream that owns this subject (for breadcrumb stream_name)
    stream_doc = next((s for s in streams if s.id == subject_doc.stream_id), None)

    # 6. Chapter count — lightweight aggregate, no content payload
    chapter_count = await Chapter.find({"subject_id": subject_doc.id}).count()

    return {
        "id": str(subject_doc.id),
        "name": subject_doc.name,
        "slug": subject_doc.slug or _slugify(subject_doc.name),
        "description": subject_doc.description,
        "tags": subject_doc.tags or [],
        "icon": subject_doc.icon,
        "gradient": subject_doc.gradient,
        "thumbnailUrl": subject_doc.thumbnail_url,
        "has_document": subject_doc.has_document,
        "seo_stats": subject_doc.seo_stats,
        "status": subject_doc.status,
        # Breadcrumb context — avoids a second round-trip from the page component
        "board_name": board_doc.name,
        "board_slug": board_doc.slug,
        "class_name": matching_class.name,
        "class_slug": _slugify(matching_class.name),
        "stream_name": stream_doc.name if stream_doc else "",
        "stream_slug": _slugify(stream_doc.name) if stream_doc else "",
        "chapter_count": chapter_count,
    }


@router.get("/chapter-by-slug/{board}/{class_slug}/{subject_slug}/{chapter_slug}")
async def get_chapter_by_slug(
    board: str,
    class_slug: str,
    subject_slug: str,
    chapter_slug: str,
    response: Response,
):
    """
    Resolve a chapter from URL slugs and return its full content payload.

    Used by ChapterPage for direct-URL loads and prerendering.
    URL pattern: /{board}/{classSlug}/{subjectSlug}/{chapterSlug}
    No authentication required - chapter pages are publicly accessible.

    Returns the chapter with breadcrumb context (board, class, stream, subject names/slugs),
    content fields, and metadata for SEO/AEO structured data generation.
    """
    return await _resolve_chapter_by_slug(
        board, class_slug, None, subject_slug, chapter_slug, response, use_slug_as=False
    )


@router.get("/chapter-by-slug/{board}/{class_slug}/{stream_slug}/{subject_slug}/{chapter_slug}")
async def get_chapter_by_slug_with_stream(
    board: str,
    class_slug: str,
    stream_slug: str,
    subject_slug: str,
    chapter_slug: str,
    response: Response,
):
    """
    Resolve a chapter from URL slugs including an explicit stream segment.

    URL pattern: /{board}/{classSlug}/{streamSlug}/{subjectSlug}/{chapterSlug}
    No authentication required.
    """
    return await _resolve_chapter_by_slug(
        board, class_slug, stream_slug, subject_slug, chapter_slug, response, use_slug_as=False
    )


@router.get("/chapter-by-slug-as/{board}/{class_slug}/{subject_slug}/{chapter_slug}")
async def get_chapter_by_slug_as(
    board: str,
    class_slug: str,
    subject_slug: str,
    chapter_slug: str,
    response: Response,
):
    """
    Resolve a chapter using Assamese slug (slug_as) with English slug fallback.

    Used on /as/* routes where the URL may contain a translated Assamese slug.
    No authentication required.
    """
    return await _resolve_chapter_by_slug(
        board, class_slug, None, subject_slug, chapter_slug, response, use_slug_as=True
    )


@router.get("/chapter-by-slug-as/{board}/{class_slug}/{stream_slug}/{subject_slug}/{chapter_slug}")
async def get_chapter_by_slug_as_with_stream(
    board: str,
    class_slug: str,
    stream_slug: str,
    subject_slug: str,
    chapter_slug: str,
    response: Response,
):
    """
    Resolve a chapter using Assamese slug with stream segment.

    Used on /as/* routes with explicit stream in the URL.
    No authentication required.
    """
    return await _resolve_chapter_by_slug(
        board, class_slug, stream_slug, subject_slug, chapter_slug, response, use_slug_as=True
    )


async def _resolve_chapter_by_slug(
    board: str,
    class_slug: str,
    stream_slug: Optional[str],
    subject_slug: str,
    chapter_slug: str,
    response: Response,
    use_slug_as: bool = False,
) -> dict:
    """
    Internal resolver for chapter-by-slug endpoints.

    Resolution order:
    1. Board by slug field
    2. Class by _slugify(class.name) within that board
    3. Stream by _slugify(stream.name) if stream_slug provided, else all streams
    4. Subject by subject.slug or _slugify(subject.name) within resolved streams
    5. Chapter by chapter.slug (and slug_as when use_slug_as=True) within subject
    """
    response.headers["Cache-Control"] = "public, max-age=60, s-maxage=300"

    # 1. Resolve board
    board_doc = await Board.find_one({"slug": board, "status": "active"})
    if not board_doc:
        all_boards = await Board.find({"status": "active"}).to_list()
        board_doc = next(
            (b for b in all_boards if _slugify(b.name) == board or b.slug == board),
            None,
        )
    if not board_doc:
        raise HTTPException(status_code=404, detail=f"Board '{board}' not found")

    # 2. Resolve class
    classes = await Class.find({"board_id": board_doc.id, "status": "active"}).to_list()
    matching_class = next(
        (c for c in classes if _slugify(c.name) == class_slug),
        None,
    )
    if not matching_class:
        raise HTTPException(
            status_code=404,
            detail=f"Class '{class_slug}' not found under board '{board}'",
        )

    # 3. Resolve streams
    streams = await Stream.find(
        {"class_id": matching_class.id, "status": "active"}
    ).to_list()
    if not streams:
        raise HTTPException(
            status_code=404,
            detail=f"No streams found for class '{class_slug}'",
        )

    if stream_slug:
        target_streams = [s for s in streams if _slugify(s.name) == stream_slug]
        if not target_streams:
            raise HTTPException(
                status_code=404,
                detail=f"Stream '{stream_slug}' not found under '{board}/{class_slug}'",
            )
    else:
        target_streams = streams

    # 4. Resolve subject
    stream_id_list = [s.id for s in target_streams]
    candidates = await Subject.find(
        {"stream_id": {"$in": stream_id_list}, "status": "active"}
    ).to_list()

    subject_doc = next(
        (s for s in candidates if (s.slug or _slugify(s.name)) == subject_slug),
        None,
    )
    if not subject_doc:
        raise HTTPException(
            status_code=404,
            detail=f"Subject '{subject_slug}' not found under '{board}/{class_slug}'",
        )

    # 5. Resolve chapter
    chapters = await Chapter.find({"subject_id": subject_doc.id}).sort("+chapter_number").to_list()

    chapter_doc = None
    for ch in chapters:
        if ch.slug == chapter_slug:
            chapter_doc = ch
            break
        # For Assamese resolver, also check slug_as field if it exists
        if use_slug_as and getattr(ch, "slug_as", None) == chapter_slug:
            chapter_doc = ch
            break

    if not chapter_doc:
        raise HTTPException(
            status_code=404,
            detail=f"Chapter '{chapter_slug}' not found under '{board}/{class_slug}/{subject_slug}'",
        )

    # Determine the stream that owns this subject
    stream_doc = next((s for s in target_streams if s.id == subject_doc.stream_id), None)

    # Build topic_title from first published topic or chapter title
    topic_title = chapter_doc.title
    if chapter_doc.published_topics:
        topic_title = chapter_doc.published_topics[0].title

    # Build content (frontend expects "content" key for English)
    content_en = chapter_doc.content_en or ""
    content_as = chapter_doc.content_as or ""
    has_assamese = bool(content_as)

    # Compute prev/next chapters for navigation
    chapter_idx = next(
        (i for i, ch in enumerate(chapters) if ch.id == chapter_doc.id), None
    )
    prev_chapter = None
    next_chapter = None
    if chapter_idx is not None:
        if chapter_idx > 0:
            prev_ch = chapters[chapter_idx - 1]
            prev_chapter = {
                "chapter_id": str(prev_ch.id),
                "title": prev_ch.title,
                "slug": prev_ch.slug,
                "chapter_number": prev_ch.chapter_number,
            }
        if chapter_idx < len(chapters) - 1:
            next_ch = chapters[chapter_idx + 1]
            next_chapter = {
                "chapter_id": str(next_ch.id),
                "title": next_ch.title,
                "slug": next_ch.slug,
                "chapter_number": next_ch.chapter_number,
            }

    return {
        "chapter_id": str(chapter_doc.id),
        "title": chapter_doc.title,
        "chapter_title": chapter_doc.title,
        "chapter_slug": chapter_doc.slug,
        "topic_title": topic_title,
        "subject_name": subject_doc.name,
        "subject_slug": subject_doc.slug or _slugify(subject_doc.name),
        "board_name": board_doc.name,
        "board_slug": board_doc.slug,
        "class_name": matching_class.name,
        "class_slug": _slugify(matching_class.name),
        "stream_name": stream_doc.name if stream_doc else "",
        "stream_slug": _slugify(stream_doc.name) if stream_doc else "",
        "content": content_en,
        "content_as": content_as,
        "content_type": "chapter",
        "has_assamese": has_assamese,
        "meta_description": chapter_doc.meta_description or "",
        "word_count": chapter_doc.word_count or len(content_en.split()) if content_en else 0,
        "notes_generated": chapter_doc.notes_generated or bool(content_en or content_as),
        "chapter_number": chapter_doc.chapter_number,
        "topics": [t.model_dump() for t in chapter_doc.published_topics] if chapter_doc.published_topics else [],
        "faq_jsonld": chapter_doc.faq_jsonld or [],
        "prev_chapter": prev_chapter,
        "next_chapter": next_chapter,
        "generated_at": chapter_doc.created_at.isoformat() if chapter_doc.created_at else None,
        "updated_at": chapter_doc.updated_at.isoformat() if chapter_doc.updated_at else None,
    }


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
