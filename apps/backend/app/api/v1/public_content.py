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


def _slugify(text: str, max_length: int = 200) -> str:
    """Generate a URL-friendly slug from text.

    max_length caps the input before regex substitution to prevent
    catastrophic backtracking on adversarial long strings (L-1 fix).
    """
    text = text[:max_length].lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-")


@router.get("/boards")
async def get_boards(response: Response):
    """Return all active/published boards."""
    response.headers["Cache-Control"] = "public, max-age=300, s-maxage=600"
    try:
        boards = await Board.find({"status": {"$in": ["active", "published"]}}).to_list(length=None)
        return [
            {"id": str(b.id), "name": b.name, "slug": b.slug, "status": b.status}
            for b in boards
        ]
    except Exception as e:
        logger.warning(f"get_boards failed: {e}")
        return []


def _flex_id_variants(id_str: str) -> list:
    """Return both string and ObjectId variants of an ID for cross-type matching.

    The DB stores reference fields (board_id, class_id) as either legacy string
    IDs (e.g. 's13', UUID) or MongoDB ObjectIds depending on when the document
    was created. Querying with $in across both types ensures matches regardless
    of how the FK was stored.
    """
    variants: list = [id_str]
    try:
        variants.append(PydanticObjectId(id_str))
    except Exception:
        pass
    return variants


@router.get("/classes")
async def get_classes(response: Response, board_id: Optional[str] = Query(None)):
    """Return all active/published classes, optionally filtered by board_id."""
    response.headers["Cache-Control"] = "public, max-age=300, s-maxage=600"
    try:
        query: dict = {"status": {"$in": ["active", "published"]}}
        if board_id:
            query["board_id"] = {"$in": _flex_id_variants(board_id)}
        classes = await Class.find(query).to_list(length=None)
        return [
            {"id": str(c.id), "name": c.name, "board_id": str(c.board_id), "status": c.status}
            for c in classes
        ]
    except Exception as e:
        logger.warning(f"get_classes failed: {e}")
        return []


@router.get("/streams")
async def get_streams(response: Response, class_id: Optional[str] = Query(None)):
    """Return all active/published streams, optionally filtered by class_id."""
    response.headers["Cache-Control"] = "public, max-age=300, s-maxage=600"
    try:
        query: dict = {"status": {"$in": ["active", "published"]}}
        if class_id:
            query["class_id"] = {"$in": _flex_id_variants(class_id)}
        streams = await Stream.find(query).to_list(length=None)
        return [
            {"id": str(s.id), "name": s.name, "class_id": str(s.class_id), "status": s.status}
            for s in streams
        ]
    except Exception as e:
        logger.warning(f"get_streams failed: {e}")
        return []


@router.get("/subjects")
async def get_subjects(
    response: Response,
    stream_id: Optional[str] = Query(None),
    board_id: Optional[str] = Query(None),
):
    """Return all active/published subjects, optionally filtered by stream_id or board_id."""
    response.headers["Cache-Control"] = "public, max-age=300, s-maxage=600"
    try:
        query: dict = {"status": {"$in": ["active", "published"]}}
        if stream_id:
            query["stream_id"] = stream_id
        subjects = await Subject.find(query).to_list(length=None)
        return [
            {
                "id": str(s.id),
                "name": s.name,
                "slug": s.slug,
                "stream_id": str(s.stream_id) if s.stream_id else None,
                "status": s.status,
                "description": s.description,
                "icon": s.icon,
                "thumbnail_url": s.thumbnail_url,
                "tags": s.tags,
            }
            for s in subjects
        ]
    except Exception as e:
        logger.warning(f"get_subjects failed: {e}")
        return []


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
        boards = await Board.find(_status_q).to_list(length=None)
        classes = await Class.find(_status_q).to_list(length=None)
        streams = await Stream.find(_status_q).to_list(length=None)
        chapters = await Chapter.find().to_list(length=None)
    except Exception as e:
        logger.warning(f"Library bundle DB query failed (DB may not be ready): {e}")
        return {"boards": []}

    # Load subjects separately so a validation error on one bad document
    # does not wipe out the entire library response.
    try:
        subjects = await Subject.find(_status_q).to_list(length=None)
    except Exception as subj_err:
        logger.error(
            f"Subject bulk load failed — attempting per-document fallback: {subj_err}"
        )
        subjects = []
        raw_cursor = Subject.find(_status_q)
        async for doc in raw_cursor:
            try:
                subjects.append(doc)
            except Exception as doc_err:
                logger.warning(f"Skipping invalid subject document: {doc_err}")

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
                        1
                        for ch in subj_chapters
                        if ch.notes_generated or ch.content_en or ch.content_as
                    )
                    chapter_count = len(subj_chapters)
                    notes_pct = (
                        int(notes_count / chapter_count * 100) if chapter_count else 0
                    )

                    chapter_list = []
                    for ch in subj_chapters:
                        ch_data = {
                            "id": str(ch.id),
                            "title": ch.title,
                            "title_as": ch.title_as or None,
                            "slug": ch.slug,
                            "subject_id": subj_id,
                            "order": ch.chapter_number,
                            "content_type": ch.content_type or "notes",
                            "topic_count": len(ch.published_topics),
                            "notes_generated": ch.notes_generated
                            or bool(ch.content_en or ch.content_as),
                            "has_assamese": bool(ch.content_as),
                            "has_qa": bool(ch.qa_text_en or ch.qa_text_as),
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

    # Include subjects not reached by the hierarchy walk.
    # This covers subjects with stream_id=None (migrated from legacy DBs without
    # a matching stream) and subjects whose stream was deleted/never linked.
    # They appear in flat listings (subject cards, search) but not under a board.
    included_subject_ids = {s["id"] for s in flat_subjects}
    for subj in subjects:
        subj_id = str(subj.id)
        if subj_id in included_subject_ids:
            continue

        subj_chapters = chapters_by_subject.get(subj_id, [])
        subj_chapters.sort(key=lambda c: c.chapter_number)
        notes_count = sum(
            1
            for ch in subj_chapters
            if ch.notes_generated or ch.content_en or ch.content_as
        )
        chapter_count = len(subj_chapters)
        notes_pct = int(notes_count / chapter_count * 100) if chapter_count else 0

        chapter_list = []
        for ch in subj_chapters:
            ch_data = {
                "id": str(ch.id),
                "title": ch.title,
                "title_as": ch.title_as or None,
                "slug": ch.slug,
                "subject_id": subj_id,
                "order": ch.chapter_number,
                "topic_count": len(ch.published_topics),
                "notes_generated": ch.notes_generated
                or bool(ch.content_en or ch.content_as),
                "has_assamese": bool(ch.content_as),
                "status": ch.status,
            }
            chapter_list.append(ch_data)
            if not is_slim:
                flat_chapters.append(ch_data)

        subj_slug = subj.slug or _slugify(subj.name)
        subj_data = {
            "id": subj_id,
            "name": subj.name,
            "slug": subj_slug,
            "stream_id": None,
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
        flat_subjects.append(subj_data)

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
    try:
        from beanie.exceptions import CollectionWasNotInitialized as _CWNI
    except ImportError:
        _CWNI = None

    response.headers["Cache-Control"] = "public, max-age=60, s-maxage=300"

    try:
        # 1. Resolve board by stored slug field
        board_doc = await Board.find_one({"slug": board, "status": "active"})
        if not board_doc:
            # Fallback: try case-insensitive slugify match on name
            all_boards = await Board.find({"status": "active"}).to_list(length=None)
            board_doc = next(
                (b for b in all_boards if _slugify(b.name) == board or b.slug == board),
                None,
            )
        if not board_doc:
            raise HTTPException(status_code=404, detail=f"Board '{board}' not found")

        # 2. Resolve class — Class has no stored slug, compute from name
        classes = await Class.find({"board_id": board_doc.id, "status": "active"}).to_list(length=None)
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
        ).to_list(length=None)
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
        ).to_list(length=None)

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
    except HTTPException:
        raise
    except Exception as e:
        if _CWNI and isinstance(e, _CWNI):
            raise HTTPException(status_code=503, detail="Content store not ready, please retry")
        logger.error(f"resolve_subject({board}/{class_slug}/{subject_slug}) failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to resolve subject")


@router.get("/chapters/{subject_id}")
async def get_chapters_by_subject(
    subject_id: str,
    response: Response,
):
    """
    Return the list of chapters for a given subject (by MongoDB ObjectId string).

    Used by SubjectLandingPage and chapter prefetch hooks.
    No authentication required — subject pages are publicly accessible.
    """
    response.headers["Cache-Control"] = "public, max-age=60, s-maxage=300"
    try:
        subject_oid = PydanticObjectId(subject_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid subject_id")

    chapters = (
        await Chapter.find({"subject_id": subject_oid})
        .sort([("chapter_number", 1)])
        .to_list(length=None)
    )
    return [
        {
            "chapter_id": str(ch.id),
            "title": ch.title,
            "title_as": ch.title_as,
            "slug": ch.slug or _slugify(ch.title),
            "chapter_number": ch.chapter_number,
            "notes_generated": ch.notes_generated,
            "has_assamese": bool(ch.content_as),
        }
        for ch in chapters
    ]


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


@router.get(
    "/chapter-by-slug/{board}/{class_slug}/{stream_slug}/{subject_slug}/{chapter_slug}"
)
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
        board,
        class_slug,
        stream_slug,
        subject_slug,
        chapter_slug,
        response,
        use_slug_as=False,
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


@router.get(
    "/chapter-by-slug-as/{board}/{class_slug}/{stream_slug}/{subject_slug}/{chapter_slug}"
)
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
        board,
        class_slug,
        stream_slug,
        subject_slug,
        chapter_slug,
        response,
        use_slug_as=True,
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
        all_boards = await Board.find({"status": "active"}).to_list(length=None)
        board_doc = next(
            (b for b in all_boards if _slugify(b.name) == board or b.slug == board),
            None,
        )
    if not board_doc:
        raise HTTPException(status_code=404, detail=f"Board '{board}' not found")

    # 2. Resolve class
    classes = await Class.find({"board_id": board_doc.id, "status": "active"}).to_list(length=None)
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
    ).to_list(length=None)
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
    ).to_list(length=None)

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
    chapters = (
        await Chapter.find({"subject_id": subject_doc.id})
        .sort("+chapter_number")
        .to_list(length=None)
    )

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
    stream_doc = next(
        (s for s in target_streams if s.id == subject_doc.stream_id), None
    )

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
        "word_count": chapter_doc.word_count or len(content_en.split())
        if content_en
        else 0,
        "notes_generated": chapter_doc.notes_generated
        or bool(content_en or content_as),
        "chapter_number": chapter_doc.chapter_number,
        "topics": [t.model_dump() for t in chapter_doc.published_topics]
        if chapter_doc.published_topics
        else [],
        "faq_jsonld": chapter_doc.faq_jsonld or [],
        "prev_chapter": prev_chapter,
        "next_chapter": next_chapter,
        "generated_at": chapter_doc.created_at.isoformat()
        if chapter_doc.created_at
        else None,
        "updated_at": chapter_doc.updated_at.isoformat()
        if chapter_doc.updated_at
        else None,
        "pyq_pdf_url": chapter_doc.pyq_pdf_url or None,
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


@router.get("/chapters/{chapter_id}/topics-published")
async def get_topics_published(chapter_id: str):
    """Alias for published-topics — frontend compatibility route."""
    return await get_published_topics(chapter_id)


@router.get("/chapters/{chapter_id}/topics-related")
async def get_topics_related(chapter_id: str, limit: int = Query(6, ge=1, le=20)):
    """Return topics related to a chapter.

    Fetches the chapter's own published topics and returns them as the
    related set. A real cross-chapter similarity query can replace this
    once a vector index is available.
    """
    try:
        chapter = await Chapter.get(PydanticObjectId(chapter_id))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid chapter_id")
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    topics = [t.model_dump() for t in (chapter.published_topics or [])][:limit]
    return {
        "chapter_id": chapter_id,
        "related_topics": topics,
        "total": len(topics),
    }


@router.get("/chapters/{chapter_id}/topic-pyqs")
async def get_topic_pyqs(
    chapter_id: str,
    limit: int = Query(50, ge=1, le=200),
):
    """Return previous year questions scoped to a chapter.

    PYQ data is stored on the Chapter document's faq_jsonld field for now.
    Returns an empty structure when no PYQ data exists so the frontend
    ImportantQuestions component renders nothing rather than crashing.
    """
    try:
        chapter = await Chapter.get(PydanticObjectId(chapter_id))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid chapter_id")
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    pyqs = []
    mark_wise: dict = {}
    faq = chapter.faq_jsonld or []
    for i, item in enumerate(faq[:limit]):
        question = item.get("question", "")
        answer = item.get("answer", "")
        if not question:
            continue
        marks = item.get("marks", 2)
        entry = {
            "id": f"{chapter_id}-{i}",
            "question": question,
            "answer": answer,
            "marks": marks,
            "year": item.get("year"),
            "source": item.get("source", "faq"),
        }
        pyqs.append(entry)
        key = str(marks)
        mark_wise.setdefault(key, []).append(entry)

    return {
        "chapter_id": chapter_id,
        "total": len(pyqs),
        "pyqs": pyqs,
        "mark_wise": mark_wise,
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
            .to_list(length=limit)
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


@router.get("/cms/posts")
async def get_cms_posts(
    response: Response,
    limit: int = Query(12, ge=1, le=50),
    skip: int = Query(0, ge=0),
    board: Optional[str] = Query(None),
    class_slug: Optional[str] = Query(None),
):
    """
    Public paginated listing of published CMS blog posts.
    Used by the library page CmsPostsGrid component.
    """
    response.headers["Cache-Control"] = "public, max-age=60, s-maxage=300"
    try:
        from app.models.cms import CmsDocument
        query: dict = {"status": "published"}
        if board:
            query["board_slug"] = board
        total = await CmsDocument.find(query).count()
        docs = (
            await CmsDocument.find(query)
            .sort([("updated_at", -1)])
            .skip(skip)
            .limit(limit)
            .to_list(length=limit)
        )
        items = [
            {
                "id": str(d.id),
                "title": d.title,
                "word_count": d.word_count,
                "board_slug": d.board_slug,
                "subject_id": d.subject_id,
                "seo_slug": d.seo_slug,
                "meta_description": d.meta_description,
                "thumbnail_url": d.thumbnail_url,
                "updated_at": d.updated_at.isoformat() if d.updated_at else None,
            }
            for d in docs
        ]
        return {"items": items, "total": total}
    except Exception as e:
        logger.warning(f"CMS posts query failed (DB may not be ready): {e}")
        return {"items": [], "total": 0}


@router.get("/search")
async def search_content(
    response: Response,
    q: str = Query(..., min_length=2, max_length=200, description="Search query"),
    board: Optional[str] = Query(None, description="Filter by board slug"),
    limit: int = Query(10, ge=1, le=20),
):
    """
    Public full-text search backed by Vertex AI Search (Discovery Engine).
    Returns content chunks with title, snippet, and canonical URL.
    No authentication required — results filtered to free-tier content.
    """
    response.headers["Cache-Control"] = "public, max-age=30, s-maxage=120"
    query = q.strip()

    from app.services.search.mongo_vector_search import mongo_vector_search

    try:
        raw, _ = await mongo_vector_search.search_context(
            query=query,
            lang="en",
            limit=limit,
        )
        results = [
            {
                "id": r.get("id", ""),
                "title": r.get("title", ""),
                "snippet": (r.get("content", "") or "")[:300].strip(),
                "url": r.get("url", ""),
                "score": r.get("score", 0.0),
            }
            for r in raw
            if r.get("title") or r.get("content")
        ]
        return {"query": query, "results": results, "total": len(results), "available": True}
    except Exception as e:
        logger.warning(f"Public search failed for query '{query[:40]}': {e}")
        return {"query": query, "results": [], "total": 0, "available": True}


@router.get("/cms-library")
async def get_cms_library(
    response: Response,
    limit: int = Query(12, ge=1, le=50),
    skip: int = Query(0, ge=0),
    board: Optional[str] = Query(None),
    class_slug: Optional[str] = Query(None),
):
    """
    Alias for /cms/posts — used by older frontend builds.
    Delegates to the same CMS posts logic.
    """
    return await get_cms_posts(
        response=response,
        limit=limit,
        skip=skip,
        board=board,
        class_slug=class_slug,
    )

