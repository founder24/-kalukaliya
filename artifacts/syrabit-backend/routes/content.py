"""Syrabit.ai — Content & library routes"""
import re, json, asyncio, logging, base64
import cachetools
from typing import Optional
from datetime import datetime, timezone
from fastapi import (
    APIRouter, HTTPException, Depends, Query, Response,
)

from models import (
    DocumentUpload, LibraryBundleOut,
)
from deps import (
    db,
    is_mongo_available,
)
from cache import (
    _cache_key,
    _get_content_cache,
    _invalidate_content_cache,
    _redis_cache_search,
    _redis_get_search,
    _set_content_cache,
    get_hierarchy_cache,
    set_hierarchy_cache,
)
from auth_deps import (
    get_admin_user,
)
from utils import (
    _slow_query,
    slugify_title,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_PUBLISHED_OR_LEGACY = {"$or": [{"status": {"$exists": False}}, {"status": "published"}]}

# Task #701 — track subjects served via the relaxed status filter introduced
# in Task #700. The public chapter resolver tolerates draft/unpublished
# subjects so live URLs no longer 404, but we want the admin Control Center
# to surface that drift (subject id, last-served timestamp, hit count) so an
# operator can flip the status to "published" with one click and silence the
# WARN logs.
#
# Storage is a Redis hash so all Gunicorn workers see the same state and the
# one-click publish flow is reliably self-healing across the fleet (preload_app
# + workers=3 means an in-memory dict would split per worker). When Redis is
# unavailable (unit tests, local dev without Redis) we fall back to an in-
# memory dict — single-process callers still get correct behaviour.
_DRAFT_SERVED_REDIS_KEY = "syrabit:draft_served_subjects"
_DRAFT_SERVED_FALLBACK: dict = {}

def _draft_served_redis():
    """Return the Redis client if available, else None (fall back to memory)."""
    try:
        from deps import redis_client
        return redis_client
    except Exception:
        return None

def _record_draft_served(subj: dict) -> None:
    """Record (or refresh) a subject that was served via the relaxed filter."""
    if not subj:
        return
    sid = subj.get("id")
    if not sid:
        return
    now_iso = datetime.now(timezone.utc).isoformat()
    rc = _draft_served_redis()
    if rc is not None:
        try:
            raw = rc.hget(_DRAFT_SERVED_REDIS_KEY, sid)
            if raw:
                try:
                    entry = json.loads(raw)
                except Exception:
                    entry = None
            else:
                entry = None
            if entry:
                entry["count"] = int(entry.get("count", 0)) + 1
                entry["last_served_at"] = now_iso
                entry["status"] = subj.get("status")
                entry["name"] = subj.get("name") or entry.get("name") or ""
                entry["slug"] = subj.get("slug") or entry.get("slug") or ""
            else:
                entry = {
                    "id": sid,
                    "name": subj.get("name") or "",
                    "slug": subj.get("slug") or "",
                    "status": subj.get("status"),
                    "first_served_at": now_iso,
                    "last_served_at": now_iso,
                    "count": 1,
                }
            rc.hset(_DRAFT_SERVED_REDIS_KEY, sid, json.dumps(entry, default=str))
            return
        except Exception as exc:
            logger.warning("draft-served Redis record failed (%s); using fallback", exc)
    # In-memory fallback (no Redis configured).
    entry = _DRAFT_SERVED_FALLBACK.get(sid)
    if entry:
        entry["count"] = entry.get("count", 0) + 1
        entry["last_served_at"] = now_iso
        entry["status"] = subj.get("status")
        entry["name"] = subj.get("name") or entry.get("name") or ""
        entry["slug"] = subj.get("slug") or entry.get("slug") or ""
    else:
        _DRAFT_SERVED_FALLBACK[sid] = {
            "id": sid,
            "name": subj.get("name") or "",
            "slug": subj.get("slug") or "",
            "status": subj.get("status"),
            "first_served_at": now_iso,
            "last_served_at": now_iso,
            "count": 1,
        }

def get_draft_served_subjects() -> list:
    """Return the tracked entries sorted by most-recently-served first."""
    items: list = []
    rc = _draft_served_redis()
    if rc is not None:
        try:
            raw_map = rc.hgetall(_DRAFT_SERVED_REDIS_KEY) or {}
            for raw in raw_map.values():
                try:
                    items.append(json.loads(raw))
                except Exception:
                    continue
        except Exception as exc:
            logger.warning("draft-served Redis read failed (%s); using fallback", exc)
            items = list(_DRAFT_SERVED_FALLBACK.values())
    else:
        items = list(_DRAFT_SERVED_FALLBACK.values())
    return sorted(items, key=lambda e: e.get("last_served_at") or "", reverse=True)

def clear_draft_served_subject(subject_id: str) -> bool:
    """Remove a subject from the tracker (e.g. after it's been published).

    Always clears both Redis and the in-memory fallback so a publish action
    is single-source-of-truth across the worker fleet.
    """
    cleared = False
    rc = _draft_served_redis()
    if rc is not None:
        try:
            cleared = bool(rc.hdel(_DRAFT_SERVED_REDIS_KEY, subject_id))
        except Exception as exc:
            logger.warning("draft-served Redis clear failed (%s); using fallback", exc)
    if _DRAFT_SERVED_FALLBACK.pop(subject_id, None) is not None:
        cleared = True
    return cleared

@router.get("/content/library-bundle", response_model=LibraryBundleOut)
async def get_library_bundle(nocache: Optional[str] = None, include_seo: Optional[str] = None, slim: Optional[str] = None, boot: Optional[str] = None, response: Response = None):
    # Mode precedence: slim > boot > seo > full. `boot=<board_id>` returns
    # slim metadata + chapters scoped to that one board only (Task: PSI
    # /library payload split — shrinks 1.0MB full bundle to ~200KB so first
    # paint isn't competing with chapter download bytes).
    boot_id = (boot or "").strip() or None
    if slim == "1":
        cache_key = "library-bundle:slim"
    elif boot_id:
        cache_key = f"library-bundle:boot:{boot_id}"
    elif include_seo:
        cache_key = "library-bundle:seo"
    else:
        cache_key = "library-bundle"
    if not nocache:
        cached = _get_content_cache(cache_key)
        if cached:
            if response:
                response.headers["Cache-Control"] = "public, max-age=600, s-maxage=3600, stale-while-revalidate=86400"
                response.headers["CDN-Cache-Control"] = "public, max-age=3600, stale-while-revalidate=86400"
            return cached
    try:
        if not await is_mongo_available():
            return {"boards": [], "classes": [], "streams": [], "subjects": []}
        async with _slow_query("library_bundle"):
            # When boot is set we need subject_ids first to scope the
            # chapter query, so split into two phases. The metadata phase
            # is unchanged from full-bundle cost; only the chapter query
            # shrinks (the actual payload win).
            try:
                (boards_data, classes_data, streams_data, subjects_data,
                 pyq_data, fc_data) = await asyncio.wait_for(
                    asyncio.gather(
                        db.boards.find(_PUBLISHED_OR_LEGACY, {"_id": 0, "id": 1, "name": 1, "slug": 1}).to_list(100),
                        db.classes.find(_PUBLISHED_OR_LEGACY, {"_id": 0, "id": 1, "name": 1, "slug": 1, "board_id": 1}).to_list(100),
                        db.streams.find(_PUBLISHED_OR_LEGACY, {"_id": 0, "id": 1, "name": 1, "slug": 1, "class_id": 1}).to_list(100),
                        db.subjects.find({"status": "published"}, {"_id": 0}).to_list(500),
                        db.topic_pyq_collections.find({}, {"_id": 0, "subject_id": 1, "total": 1}).to_list(2000),
                        db.flashcard_collections.find({}, {"_id": 0, "subject_id": 1, "total": 1}).to_list(2000),
                    ),
                    timeout=10.0,
                )
            except asyncio.TimeoutError:
                logger.warning("library-bundle MongoDB metadata query timed out after 10s")
                return {"boards": [], "classes": [], "streams": [], "subjects": []}

            if boot_id:
                # Walk board → classes → streams → subjects to derive the
                # scoped subject_id set. All in-memory; collections are
                # already loaded.
                class_ids = {c["id"] for c in classes_data if c.get("board_id") == boot_id}
                stream_ids = {s["id"] for s in streams_data if s.get("class_id") in class_ids}
                scoped_subject_ids = [s["id"] for s in subjects_data if s.get("stream_id") in stream_ids]
                if not scoped_subject_ids:
                    chapters_data: list = []
                else:
                    try:
                        chapters_data = await asyncio.wait_for(
                            db.chapters.find(
                                {"subject_id": {"$in": scoped_subject_ids}},
                                {"_id": 0, "id": 1, "title": 1, "slug": 1, "subject_id": 1, "order_index": 1, "notes_generated": 1},
                            ).sort("order_index", 1).to_list(2000),
                            timeout=8.0,
                        )
                    except asyncio.TimeoutError:
                        logger.warning("library-bundle boot chapters query timed out")
                        chapters_data = []
            else:
                try:
                    chapters_data = await asyncio.wait_for(
                        db.chapters.find(
                            {},
                            {"_id": 0, "id": 1, "title": 1, "slug": 1, "subject_id": 1, "order_index": 1, "notes_generated": 1},
                        ).sort("order_index", 1).to_list(2000),
                        timeout=8.0,
                    )
                except asyncio.TimeoutError:
                    logger.warning("library-bundle full chapters query timed out")
                    chapters_data = []

        chapters_by_subject: dict = {}
        chapter_id_to_subject: dict = {}
        for ch in chapters_data:
            if not ch.get("slug"):
                title = ch.get("title", "")
                if title:
                    ch["slug"] = slugify_title(title)
            sid = ch.get("subject_id", "")
            ch_id = ch.get("id", "")
            if sid and ch_id:
                chapters_by_subject.setdefault(sid, []).append(ch)
                chapter_id_to_subject[ch_id] = sid

        all_chapter_ids = list(chapter_id_to_subject.keys())

        seo_stats_by_subject: dict = {}
        if include_seo:
            seo_topics_data = []
            seo_page_type_counts = []
            try:
                seo_topics_data = await asyncio.wait_for(
                    db.topics.find(
                        {"chapter_id": {"$in": all_chapter_ids}, "status": "published"},
                        {"_id": 0, "id": 1, "title": 1, "slug": 1, "chapter_id": 1, "order": 1},
                    ).sort("order", 1).to_list(10000),
                    timeout=5.0,
                )
                if seo_topics_data:
                    seo_topic_ids = [t["id"] for t in seo_topics_data]
                    seo_page_type_counts = await asyncio.wait_for(
                        db.seo_pages.aggregate([
                            {"$match": {"topic_id": {"$in": seo_topic_ids}, "status": "published"}},
                            {"$group": {"_id": {"topic_id": "$topic_id", "page_type": "$page_type"}}},
                        ]).to_list(5000),
                        timeout=5.0,
                    )
            except asyncio.TimeoutError:
                logger.warning("library-bundle SEO query timed out — continuing without SEO data")
            except Exception as seo_err:
                logger.warning(f"library-bundle SEO query error: {seo_err}")

            seo_page_types_by_topic: dict = {}
            for doc in seo_page_type_counts:
                tid = doc["_id"]["topic_id"]
                pt = doc["_id"]["page_type"]
                seo_page_types_by_topic.setdefault(tid, []).append(pt)

            topics_by_chapter: dict = {}
            topic_id_to_chapter: dict = {}
            for t in seo_topics_data:
                cid = t.get("chapter_id", "")
                tid = t.get("id", "")
                page_types = seo_page_types_by_topic.get(tid, [])
                if not page_types:
                    continue
                topic_id_to_chapter[tid] = cid
                topics_by_chapter.setdefault(cid, []).append({
                    "id": tid,
                    "title": t.get("title", ""),
                    "slug": t.get("slug", ""),
                    "page_types": sorted(page_types),
                })

            for tid, ptypes in seo_page_types_by_topic.items():
                cid = topic_id_to_chapter.get(tid)
                if not cid:
                    continue
                sid = chapter_id_to_subject.get(cid)
                if not sid:
                    continue
                stats = seo_stats_by_subject.setdefault(sid, {"topic_count": 0, "notes": 0, "definition": 0, "important-questions": 0, "mcqs": 0, "examples": 0})
                stats["topic_count"] += 1
                for pt in ptypes:
                    if pt in stats:
                        stats[pt] += 1

            for ch in chapters_data:
                ch_id = ch.get("id", "")
                ch["seo_topics"] = topics_by_chapter.get(ch_id, [])

        pyq_total_by_subject: dict = {}
        for p in pyq_data:
            sid = p.get("subject_id", "")
            if sid:
                pyq_total_by_subject[sid] = pyq_total_by_subject.get(sid, 0) + (p.get("total") or 0)

        fc_total_by_subject: dict = {}
        for f in fc_data:
            sid = f.get("subject_id", "")
            if sid:
                fc_total_by_subject[sid] = fc_total_by_subject.get(sid, 0) + (f.get("total") or 0)

        for s in subjects_data:
            if "thumbnail_url" in s and "thumbnailUrl" not in s:
                s["thumbnailUrl"] = s.pop("thumbnail_url")
            sid = s.get("id", "")
            chs = chapters_by_subject.get(sid, [])
            total_ch = len(chs)
            notes_ch = sum(1 for c in chs if c.get("notes_generated"))
            s["notes_count"]   = notes_ch
            s["chapter_count"] = total_ch
            s["notes_pct"]     = round(notes_ch / total_ch * 100) if total_ch else 0
            s["pyq_count"]     = pyq_total_by_subject.get(sid, 0)
            s["flash_count"]   = fc_total_by_subject.get(sid, 0)
            s["seo_stats"]     = seo_stats_by_subject.get(sid, {})

        bundle = {"boards": boards_data, "classes": classes_data, "streams": streams_data, "subjects": subjects_data}
        if slim != "1":
            bundle["chapters"] = chapters_data
        if boot_id:
            # Marker so the client knows chapters[] is scoped to this board
            # only and a full fetch is still required for cross-board search.
            bundle["boot"] = boot_id
            bundle["chapters_partial"] = True
        _set_content_cache(cache_key, bundle)
        if response:
            response.headers["Cache-Control"] = "public, max-age=600, s-maxage=3600, stale-while-revalidate=86400"
            response.headers["CDN-Cache-Control"] = "public, max-age=3600, stale-while-revalidate=86400"
        return bundle
    except Exception:
        return {"boards": [], "classes": [], "streams": [], "subjects": []}

@router.get("/content/boards")
async def get_boards(nocache: Optional[str] = None, response: Response = None):
    if not nocache:
        cached = _get_content_cache("boards")
        if cached:
            if response: response.headers["Cache-Control"] = "public, max-age=600, stale-while-revalidate=7200"
            return cached
    try:
        if not await is_mongo_available():
            return []
        boards = await db.boards.find(_PUBLISHED_OR_LEGACY, {"_id": 0}).to_list(100)
        _set_content_cache("boards", boards)
        if response: response.headers["Cache-Control"] = "public, max-age=600, stale-while-revalidate=7200"
        return boards
    except Exception:
        return []

@router.get("/content/classes")
async def get_classes(board_id: Optional[str] = None, nocache: Optional[str] = None, response: Response = None):
    ck = f"classes:{board_id or 'all'}"
    if not nocache:
        cached = _get_content_cache(ck)
        if cached:
            if response: response.headers["Cache-Control"] = "public, max-age=600, stale-while-revalidate=7200"
            return cached
    try:
        if not await is_mongo_available():
            return []
        query = {"board_id": board_id} if board_id else {}
        query = {"$and": [query, _PUBLISHED_OR_LEGACY]} if query else _PUBLISHED_OR_LEGACY
        classes = await db.classes.find(query, {"_id": 0}).to_list(100)
        _set_content_cache(ck, classes)
        if response: response.headers["Cache-Control"] = "public, max-age=600, stale-while-revalidate=7200"
        return classes
    except Exception:
        return []

@router.get("/content/streams")
async def get_streams(class_id: Optional[str] = None, nocache: Optional[str] = None, response: Response = None):
    ck = f"streams:{class_id or 'all'}"
    if not nocache:
        cached = _get_content_cache(ck)
        if cached:
            if response: response.headers["Cache-Control"] = "public, max-age=600, stale-while-revalidate=7200"
            return cached
    try:
        if not await is_mongo_available():
            return []
        query = {"class_id": class_id} if class_id else {}
        query = {"$and": [query, _PUBLISHED_OR_LEGACY]} if query else _PUBLISHED_OR_LEGACY
        streams = await db.streams.find(query, {"_id": 0}).to_list(100)
        _set_content_cache(ck, streams)
        if response: response.headers["Cache-Control"] = "public, max-age=600, stale-while-revalidate=7200"
        return streams
    except Exception:
        return []

@router.get("/content/subjects-by-course-type")
async def get_subjects_by_course_type(board_id: str, nocache: Optional[str] = None, response: Response = None):
    ck = f"subjects_by_course_type:{board_id}"
    if not nocache:
        cached = _get_content_cache(ck)
        if cached:
            if response: response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=3600"
            return cached
    try:
        if not await is_mongo_available():
            return []
        all_classes = await db.classes.find({"$and": [{"board_id": board_id}, _PUBLISHED_OR_LEGACY]}, {"_id": 0}).to_list(50)
        class_ids = [c["id"] for c in all_classes]
        all_streams = await db.streams.find({"$and": [{"class_id": {"$in": class_ids}}, _PUBLISHED_OR_LEGACY]}, {"_id": 0}).to_list(200)
        stream_ids = [s["id"] for s in all_streams]
        all_subjects = await db.subjects.find({"stream_id": {"$in": stream_ids}, "status": "published"}, {"_id": 0}).to_list(1000)
        for s in all_subjects:
            if "thumbnail_url" in s and "thumbnailUrl" not in s:
                s["thumbnailUrl"] = s.pop("thumbnail_url")
        stream_map = {s["id"]: s for s in all_streams}
        COURSE_TYPES = [
            {"slug": "major", "name": "Major", "description": "Major Discipline Course", "icon": "target"},
            {"slug": "minor", "name": "Minor", "description": "Minor Elective Course", "icon": "book"},
            {"slug": "sec",   "name": "SEC",   "description": "Skill Enhancement Course", "icon": "zap"},
            {"slug": "vac",   "name": "VAC",   "description": "Value-Added Course", "icon": "sparkles"},
            {"slug": "mdc",   "name": "MDC",   "description": "Multidisciplinary Course", "icon": "globe"},
            {"slug": "aec",   "name": "AEC",   "description": "Ability Enhancement Course", "icon": "brain"},
        ]
        result = []
        for ct in COURSE_TYPES:
            matching_stream_ids = [s["id"] for s in all_streams if s.get("slug") == ct["slug"]]
            ct_subjects = []
            seen_names = set()
            for subj in all_subjects:
                if subj.get("stream_id") in matching_stream_ids:
                    name_key = subj.get("name", "").strip().lower()
                    if name_key not in seen_names:
                        seen_names.add(name_key)
                        stream_info = stream_map.get(subj.get("stream_id"), {})
                        ct_subjects.append({**subj, "stream_name": stream_info.get("name", ""), "stream_slug": stream_info.get("slug", "")})
            result.append({**ct, "subjects": ct_subjects, "subject_count": len(ct_subjects)})
        _set_content_cache(ck, result)
        if response: response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=3600"
        return result
    except Exception as e:
        logger.error(f"Failed to fetch subjects by course type: {e}")
        return []

@router.get("/content/subjects")
async def get_subjects(stream_id: Optional[str] = None, class_id: Optional[str] = None, nocache: Optional[str] = None, response: Response = None):
    ck = f"subjects:{stream_id or ''}:{class_id or ''}"
    if not nocache:
        cached = _get_content_cache(ck)
        if cached:
            if response: response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=3600"
            return cached
    try:
        if not await is_mongo_available():
            return []
        if stream_id:
            stream = await db.streams.find_one({"$and": [{"id": stream_id}, _PUBLISHED_OR_LEGACY]}, {"_id": 0, "id": 1})
            if not stream:
                return []
            subjects = await db.subjects.find({"stream_id": stream_id, "status": "published"}, {"_id": 0}).to_list(100)
        elif class_id:
            streams = await db.streams.find({"$and": [{"class_id": class_id}, _PUBLISHED_OR_LEGACY]}, {"_id": 0}).to_list(100)
            stream_ids = [s["id"] for s in streams]
            subjects = await db.subjects.find({"stream_id": {"$in": stream_ids}, "status": "published"}, {"_id": 0}).to_list(500)
        else:
            subjects = await db.subjects.find({"status": "published"}, {"_id": 0}).to_list(500)
        for s in subjects:
            if "thumbnail_url" in s and "thumbnailUrl" not in s:
                s["thumbnailUrl"] = s.pop("thumbnail_url")
        _set_content_cache(ck, subjects)
        if response: response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=3600"
        return subjects
    except Exception:
        return []

@router.get("/content/resolve-subject/{board_slug}/{class_slug}/{stream_slug}/{subject_slug}")
async def resolve_subject(board_slug: str, class_slug: str, stream_slug: str, subject_slug: str, response: Response = None):
    ck = f"resolve:{board_slug}:{class_slug}:{stream_slug}:{subject_slug}"
    cached = _get_content_cache(ck)
    if cached:
        if response: response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=3600"
        return cached
    if not await is_mongo_available():
        raise HTTPException(503, "Content database unavailable")
    board = await db.boards.find_one({"$and": [{"slug": board_slug}, _PUBLISHED_OR_LEGACY]}, {"_id": 0})
    if not board: raise HTTPException(404, "Board not found")
    cls = await db.classes.find_one({"$and": [{"slug": class_slug, "board_id": board["id"]}, _PUBLISHED_OR_LEGACY]}, {"_id": 0})
    if not cls: raise HTTPException(404, "Class not found")
    stream = await db.streams.find_one({"$and": [{"slug": stream_slug, "class_id": cls["id"]}, _PUBLISHED_OR_LEGACY]}, {"_id": 0})
    if not stream: raise HTTPException(404, "Stream not found")
    subj = await db.subjects.find_one({"slug": subject_slug, "stream_id": stream["id"], "status": "published"}, {"_id": 0})
    if not subj: raise HTTPException(404, "Subject not found")
    result = {
        "id": subj["id"], "name": subj["name"],
        "description": subj.get("description", ""),
        "icon": subj.get("icon", ""), "tags": subj.get("tags", []),
        "gradient": subj.get("gradient", ""), "chapter_count": subj.get("chapter_count", 0),
        "board_name": board.get("name", ""), "class_name": cls.get("name", ""),
        "stream_name": stream.get("name", ""),
        "board_slug": board_slug, "class_slug": class_slug,
        "stream_slug": stream_slug, "slug": subject_slug,
    }
    _set_content_cache(ck, result)
    if response: response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=3600"
    return result

@router.get("/content/resolve-subject/{board_slug}/{class_slug}/{subject_slug}")
async def resolve_subject_no_stream(board_slug: str, class_slug: str, subject_slug: str, response: Response = None):
    ck = f"resolve-ns:{board_slug}:{class_slug}:{subject_slug}"
    cached = _get_content_cache(ck)
    if cached:
        if response: response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=3600"
        return cached
    if not await is_mongo_available():
        raise HTTPException(503, "Content database unavailable")
    board = await db.boards.find_one({"$and": [{"slug": board_slug}, _PUBLISHED_OR_LEGACY]}, {"_id": 0})
    if not board: raise HTTPException(404, "Board not found")
    cls = await db.classes.find_one({"$and": [{"slug": class_slug, "board_id": board["id"]}, _PUBLISHED_OR_LEGACY]}, {"_id": 0})
    if not cls: raise HTTPException(404, "Class not found")
    streams = await db.streams.find({"$and": [{"class_id": cls["id"]}, _PUBLISHED_OR_LEGACY]}, {"_id": 0}).to_list(100)
    stream_ids = [s["id"] for s in streams]
    subj = await db.subjects.find_one({"slug": subject_slug, "stream_id": {"$in": stream_ids}, "status": "published"}, {"_id": 0})
    if not subj: raise HTTPException(404, "Subject not found")
    stream = next((s for s in streams if s["id"] == subj.get("stream_id")), None)
    result = {
        "id": subj["id"], "name": subj["name"], "description": subj.get("description", ""),
        "icon": subj.get("icon", ""), "tags": subj.get("tags", []),
        "board_name": board.get("name", ""), "class_name": cls.get("name", ""),
        "stream_name": stream.get("name", "") if stream else "",
        "board_slug": board_slug, "class_slug": class_slug,
        "stream_slug": stream.get("slug", "") if stream else "",
        "slug": subject_slug,
    }
    _set_content_cache(ck, result)
    if response: response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=3600"
    return result

@router.get("/content/subjects/{subject_id}/og-image.png")
async def get_subject_og_image(subject_id: str, response: Response = None):
    if not await is_mongo_available():
        raise HTTPException(503, "Content database unavailable")
    subj = await db.subjects.find_one(
        {"id": subject_id},
        {"_id": 0, "thumbnail_url": 1, "thumbnailUrl": 1}
    )
    if not subj:
        raise HTTPException(status_code=404, detail="Subject not found")
    raw = subj.get("thumbnailUrl") or subj.get("thumbnail_url") or ""
    if not raw:
        raise HTTPException(status_code=404, detail="No thumbnail available")
    if raw.startswith("data:"):
        raw = raw.split(",", 1)[-1]
    try:
        img_bytes = base64.b64decode(raw)
    except Exception:
        raise HTTPException(status_code=500, detail="Invalid thumbnail data")
    if response:
        response.headers["Cache-Control"] = "public, max-age=86400, stale-while-revalidate=604800"
    return Response(content=img_bytes, media_type="image/png")

@router.get("/content/subjects/{subject_id}")
async def get_subject(subject_id: str, nocache: Optional[str] = None, response: Response = None):
    ck = f"subject:{subject_id}"
    cached = _get_content_cache(ck) if not nocache else None
    if cached:
        if response: response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=3600"
        return cached
    if not await is_mongo_available():
        raise HTTPException(503, "Content database unavailable")
    subj = await db.subjects.find_one({"id": subject_id}, {"_id": 0})
    if not subj:
        raise HTTPException(status_code=404, detail="Subject not found")
    if "thumbnail_url" in subj and "thumbnailUrl" not in subj:
        subj["thumbnailUrl"] = subj.pop("thumbnail_url")
    sid = subj.get("stream_id")
    if sid:
        try:
            cached_h = get_hierarchy_cache(sid)
            if cached_h:
                subj.update(cached_h)
            else:
                h = {}
                stream = await db.streams.find_one({"id": sid}, {"_id": 0, "name": 1, "slug": 1, "class_id": 1})
                if stream:
                    h["stream_name"] = stream.get("name", "")
                    h["stream_slug"] = stream.get("slug", "")
                    cls_id = stream.get("class_id")
                    if cls_id:
                        cls = await db.classes.find_one({"id": cls_id}, {"_id": 0, "name": 1, "slug": 1, "board_id": 1})
                        if cls:
                            h["class_name"] = cls.get("name", "")
                            h["class_slug"] = cls.get("slug", "")
                            board_id = cls.get("board_id")
                            if board_id:
                                board = await db.boards.find_one({"id": board_id}, {"_id": 0, "name": 1, "slug": 1})
                                if board:
                                    h["board_name"] = board.get("name", "")
                                    h["board_slug"] = board.get("slug", "")
                if h:
                    set_hierarchy_cache(sid, h)
                    subj.update(h)
        except Exception as _e:
            logger.warning(f"subject context enrich failed: {_e}")
    _set_content_cache(ck, subj)
    if response: response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=3600"
    return subj

# ── Document endpoints (upload / read / delete) ─────────────────────────────

@router.get("/content/subjects/{subject_id}/document")
async def get_subject_document(subject_id: str):
    """Return document/chapters for a subject - checks multiple sources"""
    
    # First check if subject has document_text (old direct upload)
    subj = await db.subjects.find_one({"id": subject_id}, {"_id": 0})
    if not subj:
        raise HTTPException(status_code=404, detail="Subject not found")
    
    if subj.get("document_text"):
        return {
            "subject_id": subject_id,
            "document_name": subj.get("document_name", "document.txt"),
            "document_text": subj.get("document_text", ""),
            "document_type": subj.get("document_type", "text"),
            "document_url": subj.get("document_url", ""),
            "uploaded_at": subj.get("document_uploaded_at", ""),
        }
    
    # Check content_uploads collection
    upload = await db.content_uploads.find_one(
        {"subject_id": subject_id},
        {"_id": 0}
    )
    
    if upload:
        return {
            "subject_id": subject_id,
            "document_id": upload.get("id"),
            "document_name": upload.get("file_name") or upload.get("title", "Content"),
            "document_text": upload.get("content", ""),
            "document_type": upload.get("file_ext", "txt"),
            "document_url": upload.get("file_url", ""),
            "uploaded_at": upload.get("uploaded_at", ""),
            "is_pdf": upload.get("file_ext") == "pdf",
        }
    
    # Check chapters (manually created content)
    chapters = await db.chapters.find(
        {"subject_id": subject_id, "status": "published"},
        {"_id": 0}
    ).sort("order", 1).limit(10).to_list(10)
    
    if chapters and len(chapters) > 0:
        # Combine all chapters into one document view
        combined_content = f"# {subj.get('name', 'Subject')} - Study Material\n\n"
        for i, chapter in enumerate(chapters, 1):
            combined_content += f"## Chapter {i}: {chapter.get('title', 'Untitled')}\n\n"
            if chapter.get('description'):
                combined_content += f"{chapter.get('description')}\n\n"
            if chapter.get('content'):
                combined_content += f"{chapter.get('content')}\n\n"
            combined_content += "---\n\n"
        
        return {
            "subject_id": subject_id,
            "document_name": f"{subj.get('name', 'Subject')} - Chapters.md",
            "document_text": combined_content,
            "document_type": "markdown",
            "document_url": "",
            "uploaded_at": chapters[0].get("created_at", ""),
        }
    
    raise HTTPException(status_code=404, detail="No content available for this subject")

@router.post("/admin/content/subjects/{subject_id}/document")
async def upload_subject_document(
    subject_id: str,
    data: DocumentUpload,
    admin: dict = Depends(get_admin_user),
):
    """Admin uploads a text document for a subject card."""
    subj = await db.subjects.find_one({"id": subject_id})
    if not subj:
        raise HTTPException(status_code=404, detail="Subject not found")

    # Enforce reasonable size limit — 500KB of text
    if len(data.document_text) > 500_000:
        raise HTTPException(status_code=413, detail="Document too large (max 500KB text)")

    await db.subjects.update_one(
        {"id": subject_id},
        {"$set": {
            "document_name": data.document_name,
            "document_text": data.document_text,
            "document_type": data.document_type,
            "document_uploaded_at": datetime.now(timezone.utc).isoformat(),
            "has_document": True,
        }}
    )
    logger.info(f"Admin uploaded document '{data.document_name}' for subject {subject_id}")
    # Task #795: rewriting `document_*` + `has_document` on the subject
    # changes what `/api/content/subjects` and the library bundle return,
    # so the edge cache must drop the stale subject list.
    _invalidate_content_cache("subjects")
    logger.info(
        f"admin.purge route=admin.subject_document_upload prefixes=['subjects'] id={subject_id}"
    )
    return {
        "message": "Document uploaded",
        "subject_id": subject_id,
        "document_name": data.document_name,
        "size_chars": len(data.document_text),
    }

@router.delete("/admin/content/subjects/{subject_id}/document")
async def delete_subject_document(subject_id: str, admin: dict = Depends(get_admin_user)):
    """Admin removes the document from a subject."""
    await db.subjects.update_one(
        {"id": subject_id},
        {"$unset": {"document_name": "", "document_text": "", "document_type": "", "document_uploaded_at": "", "has_document": ""}}
    )
    # Task #795: same reason as the POST handler above — the unset of
    # `has_document` flips a public-payload field, so purge subjects.
    _invalidate_content_cache("subjects")
    logger.info(
        f"admin.purge route=admin.subject_document_delete prefixes=['subjects'] id={subject_id}"
    )
    return {"message": "Document removed"}

@router.get("/content/chapters/{subject_id}")
async def get_chapters(subject_id: str, response: Response = None):
    ck = f"chapters:{subject_id}"
    cached = _get_content_cache(ck)
    if cached:
        if response: response.headers["Cache-Control"] = "public, max-age=600, stale-while-revalidate=7200"
        return cached
    try:
        if not await is_mongo_available():
            return []
        chapters = await db.chapters.find({"subject_id": subject_id}, {"_id": 0}).sort("order_index", 1).to_list(100)
        for ch in chapters:
            if not ch.get("slug") and ch.get("title"):
                ch["slug"] = slugify_title(ch["title"])
        _set_content_cache(ck, chapters)
        if response: response.headers["Cache-Control"] = "public, max-age=600, stale-while-revalidate=7200"
        return chapters
    except Exception:
        return []

_slug_hierarchy_cache: cachetools.TTLCache = cachetools.TTLCache(maxsize=512, ttl=1800)


def warm_slug_hierarchy_cache(
    subjects: list,
    streams: list,
    classes: list,
    boards: list,
) -> int:
    """Pre-populate _slug_hierarchy_cache from pre-fetched lookup data.

    Called by neural_mesh._warm_chapter_paths_sample() at startup — the
    data is already in memory, so we pay zero extra DB round-trips here.
    Returns the number of entries populated.
    """
    stream_map = {s["id"]: s for s in streams}
    class_map = {c["id"]: c for c in classes}
    board_map = {b["id"]: b for b in boards}

    warmed = 0
    for subj in subjects:
        stream_id = subj.get("stream_id")
        stream = stream_map.get(stream_id) if stream_id else None
        class_id = (stream or {}).get("class_id") or subj.get("class_id")
        cls = class_map.get(class_id) if class_id else None
        if cls is None:
            continue
        board = board_map.get(cls.get("board_id", ""))
        if board is None:
            continue
        if not (board.get("name") and cls.get("name") and subj.get("name")):
            continue
        hk = f"{board.get('slug', '')}:{cls.get('slug', '')}:{subj.get('slug', '')}"
        if hk in _slug_hierarchy_cache:
            continue
        try:
            _slug_hierarchy_cache[hk] = {
                "board": board,
                "cls": cls,
                "subj": subj,
                "stream": stream,
            }
            warmed += 1
        except Exception:
            pass
    return warmed


async def _resolve_slug_hierarchy(board_slug, class_slug, subject_slug):
    hk = f"{board_slug}:{class_slug}:{subject_slug}"
    cached = _slug_hierarchy_cache.get(hk)
    if cached:
        return cached
    board = await db.boards.find_one({"slug": board_slug}, {"_id": 0, "id": 1, "name": 1, "slug": 1})
    if not board:
        board = await db.boards.find_one(
            {"slug": re.compile(f"^{re.escape(board_slug)}$", re.IGNORECASE)},
            {"_id": 0, "id": 1, "name": 1, "slug": 1},
        )
    if not board:
        return None
    cls = await db.classes.find_one({"slug": class_slug, "board_id": board["id"]}, {"_id": 0, "id": 1, "name": 1, "slug": 1, "board_id": 1})
    if not cls:
        cls = await db.classes.find_one(
            {"slug": re.compile(f"^{re.escape(class_slug)}$", re.IGNORECASE), "board_id": board["id"]},
            {"_id": 0, "id": 1, "name": 1, "slug": 1, "board_id": 1},
        )
    if not cls:
        return None
    streams = await db.streams.find({"class_id": cls["id"]}, {"_id": 0, "id": 1, "name": 1}).to_list(100)
    stream_ids = [s["id"] for s in streams]
    subj = await db.subjects.find_one({"slug": subject_slug, "stream_id": {"$in": stream_ids}, "status": "published"}, {"_id": 0})
    if not subj:
        subj = await db.subjects.find_one(
            {"slug": re.compile(f"^{re.escape(subject_slug)}$", re.IGNORECASE), "stream_id": {"$in": stream_ids}, "status": "published"},
            {"_id": 0},
        )
    if not subj:
        # Task #700 — chapter pages were 404ing whenever an admin flipped
        # a subject's status to "draft"/"unpublished" (or never set the
        # field at all on legacy rows). The chapter content itself still
        # existed, but the strict ``status: "published"`` filter above
        # would skip the subject and the resolver returned None, surfacing
        # as "Chapter not found" to the user. We now retry with status
        # filtered out (only excluding the explicit "archived" tombstone),
        # log a WARN so admins can see and fix the drift, and let the
        # chapter render. SEO surfaces (sitemaps, library) keep their own
        # ``published`` filters — this relaxation is local to the
        # public chapter URL resolver only.
        relaxed_filter = {
            "slug": subject_slug,
            "stream_id": {"$in": stream_ids},
            "status": {"$ne": "archived"},
        }
        subj = await db.subjects.find_one(relaxed_filter, {"_id": 0})
        if not subj:
            subj = await db.subjects.find_one(
                {**relaxed_filter, "slug": re.compile(f"^{re.escape(subject_slug)}$", re.IGNORECASE)},
                {"_id": 0},
            )
        if subj:
            logger.warning(
                "chapter resolver matched subject %r via relaxed status filter "
                "(status=%r). Publish the subject to silence this warning.",
                subject_slug, subj.get("status"),
            )
            _record_draft_served(subj)
    if not subj:
        return None
    stream = next((s for s in streams if s["id"] == subj.get("stream_id")), None)
    result = {"board": board, "cls": cls, "subj": subj, "stream": stream}
    _slug_hierarchy_cache[hk] = result
    return result

@router.get("/content/chapter-by-slug/{board_slug}/{class_slug}/{stream_slug}/{subject_slug}/{chapter_slug}")
async def get_chapter_by_slug_with_stream(board_slug: str, class_slug: str, stream_slug: str, subject_slug: str, chapter_slug: str, response: Response = None):
    try:
        return await get_chapter_by_slug(board_slug, class_slug, subject_slug, chapter_slug, response)
    except HTTPException:
        return await _chapter_fallback_search(subject_slug, chapter_slug, response)


# ── Task #295 — Assamese-slug resolver ─────────────────────────────────────
# Serves chapter pages mounted at the dedicated /as/<board>/<class>/<subj>/<slug_as>
# SPA route. Resolves the chapter by its stored Assamese slug (chapters.slug_as)
# first, then falls back to the English slug so URLs created BEFORE the
# backfill script ran (or chapters that simply have no slug_as yet) still
# resolve under /as/ instead of 404-ing. Returns the same payload shape as
# /content/chapter-by-slug/* so the frontend can swap endpoints transparently.
@router.get("/content/chapter-by-slug-as/{board_slug}/{class_slug}/{stream_slug}/{subject_slug}/{chapter_slug}")
async def get_chapter_by_slug_as_with_stream(board_slug: str, class_slug: str, stream_slug: str, subject_slug: str, chapter_slug: str, response: Response = None):
    return await get_chapter_by_slug_as(board_slug, class_slug, subject_slug, chapter_slug, response)


@router.get("/content/chapter-by-slug-as/{board_slug}/{class_slug}/{subject_slug}/{chapter_slug}")
async def get_chapter_by_slug_as(board_slug: str, class_slug: str, subject_slug: str, chapter_slug: str, response: Response = None):
    if not await is_mongo_available():
        raise HTTPException(503, "Content database unavailable")
    hier = await _resolve_slug_hierarchy(board_slug, class_slug, subject_slug)
    if not hier:
        raise HTTPException(404, "Board, class, or subject not found")
    subj = hier["subj"]
    from urllib.parse import unquote
    decoded_slug = unquote(chapter_slug)
    # Try Assamese slug first (the canonical lookup for this route),
    # then fall back to the English slug so /as/ URLs work even when
    # the backfill hasn't translated this chapter yet.
    chapter = await db.chapters.find_one(
        {"slug_as": decoded_slug, "subject_id": subj["id"]}, {"_id": 0, "id": 1}
    )
    if not chapter:
        return await get_chapter_by_slug(board_slug, class_slug, subject_slug, chapter_slug, response)
    # Reuse the canonical English-slug resolver for the heavy chunk-merge
    # + cache logic by looking up the actual English slug we just matched.
    full = await db.chapters.find_one({"id": chapter["id"]}, {"_id": 0, "slug": 1})
    eng_slug = (full or {}).get("slug") or chapter_slug
    return await get_chapter_by_slug(board_slug, class_slug, subject_slug, eng_slug, response)

@router.get("/content/chapter/{board_slug}/{class_slug}/{stream_slug}/{subject_slug}/{chapter_slug}")
async def get_chapter_legacy_with_stream(board_slug: str, class_slug: str, stream_slug: str, subject_slug: str, chapter_slug: str, response: Response = None):
    try:
        return await get_chapter_by_slug(board_slug, class_slug, subject_slug, chapter_slug, response)
    except HTTPException:
        return await _chapter_fallback_search(subject_slug, chapter_slug, response)

@router.get("/content/chapter/{board_slug}/{class_slug}/{subject_slug}/{chapter_slug}")
async def get_chapter_legacy(board_slug: str, class_slug: str, subject_slug: str, chapter_slug: str, response: Response = None):
    return await get_chapter_by_slug(board_slug, class_slug, subject_slug, chapter_slug, response)

async def _chapter_fallback_search(subject_slug: str, chapter_slug: str, response: Response = None):
    if not await is_mongo_available():
        raise HTTPException(503, "Content database unavailable")
    subj = await db.subjects.find_one({"slug": subject_slug, "status": "published"}, {"_id": 0})
    if not subj:
        subj = await db.subjects.find_one(
            {"slug": re.compile(f"^{re.escape(subject_slug)}$", re.IGNORECASE), "status": "published"}, {"_id": 0}
        )
    if not subj:
        # Task #700 — match the relaxed-status policy in the main resolver
        # so the streamSlug-fallback path doesn't 404 chapters whose
        # subject is in draft/unpublished/no-status state.
        subj = await db.subjects.find_one(
            {"slug": subject_slug, "status": {"$ne": "archived"}}, {"_id": 0}
        )
        if not subj:
            subj = await db.subjects.find_one(
                {"slug": re.compile(f"^{re.escape(subject_slug)}$", re.IGNORECASE),
                 "status": {"$ne": "archived"}},
                {"_id": 0},
            )
        if subj:
            logger.warning(
                "chapter fallback matched subject %r via relaxed status filter "
                "(status=%r). Publish the subject to silence this warning.",
                subject_slug, subj.get("status"),
            )
            _record_draft_served(subj)
    if not subj:
        raise HTTPException(404, "Subject not found")
    stream = await db.streams.find_one({"id": subj.get("stream_id")}, {"_id": 0, "id": 1, "name": 1, "class_id": 1})
    cls = await db.classes.find_one({"id": stream["class_id"]}, {"_id": 0}) if stream else None
    board = await db.boards.find_one({"id": cls["board_id"]}, {"_id": 0}) if cls else None
    if board and cls:
        return await get_chapter_by_slug(board["slug"], cls["slug"], subject_slug, chapter_slug, response)
    raise HTTPException(404, "Chapter not found")

@router.get("/content/chapter-by-slug/{board_slug}/{class_slug}/{subject_slug}/{chapter_slug}")
async def get_chapter_by_slug(board_slug: str, class_slug: str, subject_slug: str, chapter_slug: str, response: Response = None):
    ck = f"ch-slug:{board_slug}:{class_slug}:{subject_slug}:{chapter_slug}"
    cached = _get_content_cache(ck)
    if cached:
        if response: response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=3600"
        return cached
    if not await is_mongo_available():
        raise HTTPException(503, "Content database unavailable")
    hier = await _resolve_slug_hierarchy(board_slug, class_slug, subject_slug)
    if not hier:
        raise HTTPException(404, "Board, class, or subject not found")
    board, cls, subj, stream = hier["board"], hier["cls"], hier["subj"], hier["stream"]
    import unicodedata
    from urllib.parse import unquote
    decoded_slug = unquote(chapter_slug)
    normalized_slug = unicodedata.normalize("NFKC", decoded_slug.strip().lower())
    chapter = await db.chapters.find_one({"slug": chapter_slug, "subject_id": subj["id"]}, {"_id": 0})
    if not chapter and decoded_slug != chapter_slug:
        chapter = await db.chapters.find_one({"slug": decoded_slug, "subject_id": subj["id"]}, {"_id": 0})
    if not chapter:
        # Perf fix (was 1216ms in Railway logs): the original code did THREE
        # separate cursor scans of db.chapters for the same subject_id —
        # one streaming async-for and two .to_list(200) calls — even though
        # they all needed the exact same dataset. Fetch once, match in
        # memory across all three strategies (NFKC-normalised slug, auto
        # slug from title, and lenient word-normalised title).
        all_chapters = await db.chapters.find({"subject_id": subj["id"]}, {"_id": 0}).to_list(200)
        slug_normalized_words = re.sub(r'-+', ' ', normalized_slug).strip()
        for c in all_chapters:
            c_slug = (c.get("slug") or "").strip().lower()
            c_slug_normalized = unicodedata.normalize("NFKC", c_slug)
            if c_slug_normalized and c_slug_normalized == normalized_slug:
                chapter = c
                break
        if not chapter:
            for c in all_chapters:
                title = c.get("title", "")
                if slugify_title(title) == normalized_slug:
                    chapter = c
                    break
        if not chapter:
            for c in all_chapters:
                title = c.get("title", "")
                title_normalized = re.sub(r'[^\w]+', ' ', title.strip().lower(), flags=re.UNICODE).strip()
                if title_normalized == slug_normalized_words:
                    chapter = c
                    break
    if not chapter: raise HTTPException(404, "Chapter not found")
    chapter_content = chapter.get("content", "")
    if chapter_content:
        content = chapter_content
    else:
        chunks = await db.chunks.find({"chapter_id": chapter["id"]}, {"_id": 0}).sort("order_index", 1).to_list(200)
        content_parts = [c["content"] for c in chunks if c.get("content")]
        content = "\n\n".join(content_parts)
    content_as = chapter.get("content_as", "")
    word_count = len(content.split()) if content else 0
    chapter_content_type = chapter.get("content_type") or chapter.get("category") or "notes"
    is_question_paper = chapter_content_type in ("question_paper", "pyq")
    result = {
        "title": f"{chapter.get('title', chapter_slug)} — {subj['name']}",
        "topic_title": chapter.get("title", chapter_slug),
        "chapter_id": chapter.get("id", ""),
        "content": content or f"# {chapter.get('title', chapter_slug)}\n\nContent for this chapter is being prepared. Check back soon!",
        "content_as": "" if is_question_paper else (content_as or ""),
        "has_assamese": False if is_question_paper else bool(content_as and content_as.strip()),
        "content_type": chapter_content_type,
        "meta_description": chapter.get("description", f"{chapter.get('title', '')} notes for {subj['name']}"),
        "board_name": board.get("name", ""), "class_name": cls.get("name", ""),
        "subject_name": subj.get("name", ""), "chapter_title": chapter.get("title", ""),
        "stream_name": stream.get("name", "") if stream else "",
        "word_count": word_count, "generated_at": chapter.get("created_at", ""),
        "updated_at": chapter.get("updated_at", ""),
        "is_fallback": True,
        # Task #333: Bing keyword data is refreshed monthly by the
        # `_bing_keyword_refresh_loop`; surface it so ChapterPage.jsx
        # can populate <meta keywords> with what students actually
        # search for instead of a static template.
        "bing_keywords": chapter.get("bing_keywords") or [],
        # Unified SEO bundle (meta_title / meta_description / og_* /
        # twitter_* / geo_tags / jsonld_keywords) produced by the same
        # monthly refresh loop via `seo_keyword_service`. Empty dict
        # for chapters that haven't been swept yet — the frontend
        # falls back to its existing template logic in that case.
        "seo_bundle": chapter.get("seo_bundle") or {},
    }
    _set_content_cache(ck, result)
    if response: response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=3600"
    return result

@router.get("/content/chapters/{chapter_id}/topic-content")
async def get_chapter_topic_content(chapter_id: str, response: Response = None):
    """
    Returns SEO topic content grouped for a chapter.
    Each topic includes all available page types (notes, MCQs, definitions, etc.)
    rendered inline for the content card lesson view.
    """
    ck = f"ch-topic-content:{chapter_id}"
    cached = _get_content_cache(ck)
    if cached:
        if response:
            response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=3600"
        return cached

    try:
        if not await is_mongo_available():
            return {"topics": [], "chapter_id": chapter_id}

        topics = await db.topics.find(
            {"chapter_id": chapter_id, "status": "published"},
            {"_id": 0}
        ).sort("order", 1).to_list(100)

        if not topics:
            result = {"topics": [], "chapter_id": chapter_id}
            _set_content_cache(ck, result)
            return result

        topic_ids = [t["id"] for t in topics]
        pages = await db.seo_pages.find(
            {"topic_id": {"$in": topic_ids}, "status": "published"},
            {"_id": 0, "id": 1, "topic_id": 1, "page_type": 1, "title": 1,
             "content": 1, "word_count": 1, "meta_description": 1}
        ).to_list(500)

        pages_by_topic = {}
        for p in pages:
            tid = p["topic_id"]
            if tid not in pages_by_topic:
                pages_by_topic[tid] = []
            pages_by_topic[tid].append({
                "page_type": p.get("page_type", "notes"),
                "title": p.get("title", ""),
                "content": p.get("content", ""),
                "word_count": p.get("word_count", 0),
                "meta_description": p.get("meta_description", ""),
            })

        enriched = []
        for t in topics:
            topic_pages = pages_by_topic.get(t["id"], [])
            if not topic_pages:
                continue
            enriched.append({
                "id": t["id"],
                "title": t.get("title", ""),
                "slug": t.get("slug", ""),
                "definition": t.get("definition", ""),
                "order": t.get("order", 0),
                "page_types": [p["page_type"] for p in topic_pages],
                "pages": topic_pages,
            })

        result = {"topics": enriched, "chapter_id": chapter_id, "total": len(enriched)}
        _set_content_cache(ck, result)
        if response:
            response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=3600"
        return result
    except Exception as exc:
        logger.error(f"topic-content error: {exc}")
        return {"topics": [], "chapter_id": chapter_id}


@router.get("/content/chapters/{chapter_id}/topic-summary")
async def get_chapter_topic_summary(chapter_id: str, response: Response = None):
    """
    Lightweight: returns topics list with available page_types (no content).
    Used for initial chapter card rendering before user expands a topic.
    """
    ck = f"ch-topic-summary:{chapter_id}"
    cached = _get_content_cache(ck)
    if cached:
        if response:
            response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=3600"
        return cached

    try:
        if not await is_mongo_available():
            return {"topics": [], "chapter_id": chapter_id}

        topics = await db.topics.find(
            {"chapter_id": chapter_id, "status": "published"},
            {"_id": 0, "id": 1, "title": 1, "slug": 1, "definition": 1, "order": 1}
        ).sort("order", 1).to_list(100)

        if not topics:
            result = {"topics": [], "chapter_id": chapter_id}
            _set_content_cache(ck, result)
            return result

        topic_ids = [t["id"] for t in topics]
        pages = await db.seo_pages.find(
            {"topic_id": {"$in": topic_ids}, "status": "published"},
            {"_id": 0, "topic_id": 1, "page_type": 1}
        ).to_list(500)

        types_by_topic = {}
        for p in pages:
            tid = p["topic_id"]
            if tid not in types_by_topic:
                types_by_topic[tid] = []
            types_by_topic[tid].append(p["page_type"])

        enriched = []
        for t in topics:
            pt = types_by_topic.get(t["id"], [])
            enriched.append({
                "id": t["id"],
                "title": t.get("title", ""),
                "slug": t.get("slug", ""),
                "definition": t.get("definition", ""),
                "order": t.get("order", 0),
                "page_types": pt,
                "has_content": len(pt) > 0,
            })

        result = {"topics": enriched, "chapter_id": chapter_id, "total": len(enriched)}
        _set_content_cache(ck, result)
        if response:
            response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=3600"
        return result
    except Exception as exc:
        logger.error(f"topic-summary error: {exc}")
        return {"topics": [], "chapter_id": chapter_id}


@router.get("/content/topic/{topic_id}/page/{page_type}")
async def get_single_topic_page(topic_id: str, page_type: str, response: Response = None):
    """
    Returns a single SEO page content for a topic.
    Used for lazy-loading individual page types when user expands a topic tab.
    """
    ck = f"topic-page:{topic_id}:{page_type}"
    cached = _get_content_cache(ck)
    if cached:
        if response:
            response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=3600"
        return cached

    try:
        if not await is_mongo_available():
            raise HTTPException(503, "Content database unavailable")

        page = await db.seo_pages.find_one(
            {"topic_id": topic_id, "page_type": page_type, "status": "published"},
            {"_id": 0, "id": 1, "title": 1, "content": 1, "word_count": 1,
             "page_type": 1, "meta_description": 1, "topic_id": 1}
        )
        if not page:
            raise HTTPException(404, "Page not found")

        _set_content_cache(ck, page)
        if response:
            response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=3600"
        return page
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(500, "Failed to load topic content")


@router.get("/content/chunks/{chapter_id}")
async def get_chunks(chapter_id: str):
    ck = f"chunks:{chapter_id}"
    cached = _get_content_cache(ck)
    if cached: return cached
    try:
        if not await is_mongo_available():
            return []
        chunks = await db.chunks.find({"chapter_id": chapter_id}, {"_id": 0}).to_list(200)
        _set_content_cache(ck, chunks)
        return chunks
    except Exception:
        return []

@router.get("/content/chapters/{chapter_id}/topic-pyqs")
async def get_chapter_topic_pyqs(chapter_id: str, limit: int = 20, response: Response = None):
    """Public — important/topic questions for a chapter generated by the agentic pipeline."""
    ck = f"topic-pyqs:{chapter_id}:{limit}"
    cached = _get_content_cache(ck)
    if cached:
        if response: response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=3600"
        return cached
    try:
        if not await is_mongo_available():
            return {"pyqs": [], "mark_wise": {}, "total": 0}
        # Priority: ai_pyq_collections (agentic pipeline) → topic_pyq_collections (legacy pipeline)
        doc = await db.ai_pyq_collections.find_one({"chapter_id": chapter_id}, {"_id": 0})
        if not doc:
            doc = await db.topic_pyq_collections.find_one({"chapter_id": chapter_id}, {"_id": 0})
        if not doc:
            return {"pyqs": [], "mark_wise": {}, "total": 0}
        raw_pyqs = doc.get("pyqs") or []
        mark_wise_raw = doc.get("mark_wise") or {}
        # If flat pyqs list is empty but mark_wise has data, flatten it with marks field
        if not raw_pyqs and mark_wise_raw:
            for mark_str, qs in mark_wise_raw.items():
                for q in qs:
                    if isinstance(q, str):
                        raw_pyqs.append({"question": q, "marks": int(mark_str)})
                    elif isinstance(q, dict):
                        raw_pyqs.append({**q, "marks": int(mark_str)})
        pyqs = raw_pyqs[:limit]
        result = {
            "pyqs": pyqs,
            "mark_wise": mark_wise_raw,
            "total": doc.get("total", len(raw_pyqs)),
            "source": doc.get("source", "pipeline"),
        }
        _set_content_cache(ck, result)
        if response: response.headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=3600"
        return result
    except Exception:
        return {"pyqs": [], "mark_wise": {}, "total": 0}

@router.get("/content/chapters/{chapter_id}/flashcards")
async def get_chapter_flashcards(chapter_id: str, limit: int = 10):
    """Public — flashcard preview for a chapter (top N)."""
    ck = f"flashcards:{chapter_id}:{limit}"
    cached = _get_content_cache(ck)
    if cached:
        return cached
    try:
        if not await is_mongo_available():
            return {"flashcards": [], "total": 0}
        doc = await db.flashcard_collections.find_one({"chapter_id": chapter_id}, {"_id": 0})
        if not doc:
            return {"flashcards": [], "total": 0}
        all_fc = doc.get("flashcards") or []
        flashcards = all_fc[:limit]
        result = {"flashcards": flashcards, "total": doc.get("total", len(all_fc))}
        _set_content_cache(ck, result)
        return result
    except Exception:
        return {"flashcards": [], "total": 0}

@router.get("/content/search")
async def search_content(q: str):
    if len(q) < 2:
        return []
    try:
        if not await is_mongo_available():
            return []
        q_hash = _cache_key(q)
        cached_redis = _redis_get_search(q_hash)
        if cached_redis is not None:
            return cached_redis
        ck = f"search:{q.lower().strip()}"
        cached = _get_content_cache(ck)
        if cached:
            return cached
        async with _slow_query(f"content_search q={q[:30]}"):
            regex = re.compile(q, re.IGNORECASE)
            subjects = await db.subjects.find(
                {"$or": [{"name": regex}, {"description": regex}, {"tags": regex}], "status": "published"},
                {"_id": 0}
            ).to_list(20)
        _set_content_cache(ck, subjects)
        _redis_cache_search(q_hash, subjects)
        return subjects
    except Exception:
        return []

# ─────────────────────────────────────────────
# LIBRARY SEARCH & SYLLABUS ROUTES (RAG System)
# ─────────────────────────────────────────────

@router.get("/library_search")
async def library_search(
    board: Optional[str] = None,
    class_: Optional[str] = Query(None, alias="class"),
    subject: Optional[str] = None,
    chapter: Optional[str] = None,
    query: str = "",
):
    """Library-search API for RAG system. Returns structured content from MongoDB library_scrapes collection."""
    try:
        if not await is_mongo_available():
            return {"board": board, "class": class_, "subject": subject, "chapter": chapter, "pages": [], "source": "none"}
        
        lib_filter = {}
        if board:
            lib_filter["board"] = board
        if class_:
            lib_filter["class"] = class_
        if subject:
            lib_filter["subject"] = subject
        if chapter:
            lib_filter["chapter"] = chapter
        
        if query:
            query_regex = re.compile(query, re.IGNORECASE)
            lib_filter["$or"] = [
                {"sections.theory": query_regex},
                {"sections.formulas": query_regex},
                {"sections.examples": query_regex},
                {"title": query_regex},
            ]
        
        pages = await db.library_scrapes.find(lib_filter, {"_id": 0}).to_list(10)
        logger.info(f"Library search: {board}/{class_}/{subject}/{chapter} - found {len(pages)} pages")
        return {
            "board": board,
            "class": class_,
            "subject": subject,
            "chapter": chapter,
            "pages": pages,
            "source": "library",
            "count": len(pages)
        }
    except Exception as e:
        logger.error(f"Library search error: {e}")
        return {"board": board, "class": class_, "subject": subject, "chapter": chapter, "pages": [], "source": "error"}


