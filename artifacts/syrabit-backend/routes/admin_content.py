"""Syrabit.ai — Admin content CRUD & thumbnails"""
import re, json, asyncio, uuid, logging, base64
from r2_storage import (
    r2_upload, r2_public_url, r2_primary_read_url, make_key,
    content_type_for,
)
from config import R2_ENABLED

_PRERENDER_LOG = logging.getLogger("syrabit.prerender_refresh")


def _schedule_prerender_refresh(reason: str = "content_update"):
    """Queue a debounced Cloudflare Pages rebuild so prerendered subject/
    chapter HTML stays in sync with admin edits (Task #387).

    Wraps `pages_deploy.schedule_refresh` in a try/except so admin write
    paths never fail because the deploy hook is misconfigured.

    Also pipes ``reason`` into the Cache-Tag purger so edge-cached HTML
    carrying ``Cache-Tag: <entity>:*`` is invalidated immediately.
    """
    try:
        from pages_deploy import schedule_refresh
        schedule_refresh(reason)
    except Exception as exc:
        _PRERENDER_LOG.warning("schedule_refresh(%r) failed: %s", reason, exc)
    # Cache-Tag purge — fire-and-forget, gated by CF_TIERED_CACHE_ON.
    # The reason string ("subject_updated:<slug>", "chapter_deleted:..")
    # is parsed into a small set of tags so the purge stays scoped to
    # the affected entity. When the flag is off this is a cheap no-op.
    try:
        tags = _cache_tags_for_reason(reason)
        if tags:
            from cf_tiered_cache import purge_by_cache_tags
            asyncio.create_task(purge_by_cache_tags(tags))
    except Exception as exc:
        _PRERENDER_LOG.warning(
            "cache-tag purge for reason=%r failed: %s", reason, exc,
        )


def _cache_tags_for_reason(reason: str) -> list[str]:
    """Map a ``_schedule_prerender_refresh`` reason into Cache-Tag tokens.

    Tag format MUST match what the SSR layer emits via
    ``cf_enterprise.build_cache_tag`` (``syrabit-<entity>-<id>``,
    hyphen-separated). A mismatch here means the purge silently
    leaves stale HTML in the edge cache.
    """
    if not reason:
        return []
    tags = ["syrabit-html"]
    if ":" in reason:
        suffix = reason.split(":", 1)[1].strip()
        if suffix and suffix.replace("-", "").replace("_", "").isalnum():
            event = reason.split(":", 1)[0]
            if "subject" in event:
                tags.append(f"syrabit-subject-{suffix}")
            elif "chapter" in event:
                tags.append(f"syrabit-chapter-{suffix}")
            elif "topic" in event:
                tags.append(f"syrabit-topic-{suffix}")
    return tags


async def _trigger_prerender_now(reason: str = "bulk_admin_op"):
    """Force-fire the Cloudflare Pages deploy hook immediately, bypassing
    the debounce window (Task #398). Use for very large bulk operations
    (seed/reset, mass imports) where waiting for the coalesce window
    would needlessly delay the rebuild.

    Wrapped in try/except so admin write paths never fail because the
    deploy hook is misconfigured or the network is flaky.
    """
    try:
        from pages_deploy import trigger_now
        await trigger_now(reason)
    except Exception as exc:
        _PRERENDER_LOG.warning("trigger_now(%r) failed: %s", reason, exc)


def _schedule_indexnow_for_subject(subject_doc: dict):
    try:
        from routes.bot_discovery import indexnow_batcher
        board_slug = subject_doc.get("board_slug", "")
        class_slug = subject_doc.get("class_slug", "")
        subject_slug = subject_doc.get("slug", "")
        if board_slug and class_slug and subject_slug:
            path = f"/{board_slug}/{class_slug}/{subject_slug}"
            async def _do_indexnow():
                await indexnow_batcher.queue_raw_paths([path])
                await indexnow_batcher.flush(source="admin_subject_update")
            loop = asyncio.get_running_loop()
            loop.create_task(_do_indexnow())
    except Exception:
        pass

def _schedule_indexnow_for_chapter(chapter_doc: dict):
    """On chapter publish/update, fan a SINGLE URL set out to every
    discovery channel via ``publish_url_set`` — Bing IndexNow + Yandex
    IndexNow + central api.indexnow.org + Google Indexing API in one
    call. The URL set is the canonical Task #11 fan-out: the chapter
    root plus all seven page-types
    (``notes / mcqs / flashcards / pyqs / summary / definitions /
    revision``) at the new
    ``/board/{board}/class/{class}/subject/{subject}/chapter/{chapter}/{type}``
    pattern, so every public-facing URL family for the chapter is
    discovered together. Falls back to the legacy single-path push
    only when the new SEO route family is unreachable (e.g. tests
    running without ``routes.seo_pages`` imported)."""
    try:
        from deps import db
        subject_id = chapter_doc.get("subject_id", "")
        chapter_slug = chapter_doc.get("slug", "")
        if not subject_id or not chapter_slug:
            return

        async def _do():
            subj = await db.subjects.find_one(
                {"id": subject_id},
                {"_id": 0, "board_slug": 1, "class_slug": 1, "slug": 1},
            )
            if not subj:
                return
            bs = subj.get("board_slug", "")
            cs = subj.get("class_slug", "")
            ss = subj.get("slug", "")
            if not (bs and cs and ss):
                return

            # Task #11 — fan all 8 canonical SEO URLs out at once.
            from routes.bot_discovery import (
                publish_url_set, BASE_URL,
            )
            try:
                from routes.seo_pages import list_seo_page_types
                page_types = list_seo_page_types()
            except Exception:
                page_types = [
                    "notes", "mcqs", "flashcards", "pyqs",
                    "summary", "definitions", "revision",
                ]
            chapter_root = (
                f"{BASE_URL}/board/{bs}/class/{cs}"
                f"/subject/{ss}/chapter/{chapter_slug}"
            )
            urls = [chapter_root] + [
                f"{chapter_root}/{pt}" for pt in page_types
            ]
            # Also keep the legacy short path for back-compat with the
            # pre-Task-#11 SPA route until that surface is retired.
            urls.append(f"{BASE_URL}/{bs}/{cs}/{ss}/{chapter_slug}")
            try:
                await publish_url_set(urls, source="admin_chapter_update")
            except Exception as e:
                logger.warning(
                    f"[indexnow] publish_url_set failed for chapter "
                    f"{chapter_slug}: {e}; falling back to legacy push"
                )
                from routes.bot_discovery import indexnow_batcher
                await indexnow_batcher.queue_raw_paths(
                    [u[len(BASE_URL):] for u in urls if u.startswith(BASE_URL)]
                )
                await indexnow_batcher.flush(source="admin_chapter_update")

        loop = asyncio.get_running_loop()
        loop.create_task(_do())
    except Exception:
        pass
from typing import Any, Optional
from datetime import datetime, timezone
from fastapi import (
    APIRouter, HTTPException, Depends, Query, Body, File, UploadFile, Request, BackgroundTasks,
    Form,
)

from models import (
    SubjectCreate, ChapterCreate, ChunkCreate,
)
from deps import (
    db,
    is_mongo_available,
    mark_mongo_down,
    supa,
)
from cache import _invalidate_content_cache


def _purge_for_route(route: str, prefixes, content_id: str = "") -> None:
    """Cache-purge wrapper for admin write routes (Task #795).

    Calls ``_invalidate_content_cache`` for each content prefix, which
    fans out to (1) the in-process content cache, (2) Redis, and (3) the
    debounced Cloudflare zone purge + worker /api/edge/purge call (see
    ``cache._fire_cf_edge_purge`` → ``cloudflare_client.purge_content_prefixes``
    + ``purge_worker_cache``). Emits a single audit log line so admins can
    correlate writes to purge events in production logs.

    Designed to be safe to call from any handler — failures in the purge
    path are logged but never raised, since a purge is best-effort cache
    hygiene, not a correctness barrier.
    """
    if not prefixes:
        return
    prefix_list = list(prefixes)
    for p in prefix_list:
        try:
            _invalidate_content_cache(p)
        except Exception as e:
            logger.warning(
                f"admin.purge invalidate failed prefix={p} route={route}: {e}"
            )
    logger.info(
        f"admin.purge route={route} prefixes={prefix_list} id={content_id or '-'}"
    )


from routes.content import (
    get_draft_served_subjects as _get_draft_served_subjects,
    clear_draft_served_subject as _clear_draft_served_subject,
)
from auth_deps import (
    get_admin_user,
)
from rag import (
    auto_chunk_content,
    rechunk_chapter,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_D1_TABLE_MAP = {
    "boards": ["boards"],
    "classes": ["classes"],
    "streams": ["streams"],
    "subjects": ["subjects", "seo_pages"],
    "chapters": ["chapters", "topics", "seo_pages"],
    "topics": ["topics", "seo_pages"],
    "seo_pages": ["seo_pages"],
}

async def _trigger_d1_sync_bg(tables: list):
    try:
        from d1_sync import is_d1_configured, sync_tables
        if is_d1_configured():
            await sync_tables(db, tables)
    except Exception as e:
        logger.warning(f"Background D1 sync failed for {tables}: {e}")

def _schedule_d1_sync_fire(*prefixes):
    tables = set()
    for p in prefixes:
        tables.update(_D1_TABLE_MAP.get(p, []))
    if tables:
        try:
            asyncio.create_task(_trigger_d1_sync_bg(list(tables)))
        except RuntimeError:
            pass

async def _cascade_delete_subject_assets(subject_id: str):
    try:
        import server as _srv
        if _srv._syllabus_embedder:
            chapters = await db.chapters.find({"subject_id": subject_id}, {"id": 1}).to_list(500)
            for ch in chapters:
                ch_id = ch.get("id")
                if ch_id:
                    await _srv._syllabus_embedder.remove_chapter_embeddings(ch_id)
    except Exception as exc:
        logger.warning(f"Vectorize cleanup failed for subject {subject_id}: {exc}")
    await db.chapters.delete_many({"subject_id": subject_id})
    # Task #383 — purge cross-pod KV mirror after a bulk chapter wipe.
    try:
        from routes.content import invalidate_chapters_kv
        await invalidate_chapters_kv(subject_id)
    except Exception as _kv_exc:
        logger.debug(f"chapters KV invalidate (delete) failed: {_kv_exc}")
    for coll_name in ["ai_pyq_collections", "flashcard_collections", "seo_topics", "chunks", "seo_pages", "cms_posts"]:
        try:
            await getattr(db, coll_name).delete_many({"subject_id": subject_id})
        except Exception as exc:
            logger.warning(f"Cascade cleanup failed for {coll_name} (subject {subject_id}): {exc}")

async def _cascade_delete_stream_children(stream_id: str):
    child_subjects = await db.subjects.find({"stream_id": stream_id}).to_list(None)
    for subj in child_subjects:
        await _cascade_delete_subject_assets(subj["id"])
    await db.subjects.delete_many({"stream_id": stream_id})

@router.get("/admin/content/boards")
async def admin_list_boards(admin: dict = Depends(get_admin_user)):
    """Admin boards — live MongoDB read, no cache, no status filter."""
    boards = await db.boards.find({}, {"_id": 0}).to_list(500)
    return boards

@router.get("/admin/content/classes")
async def admin_list_classes(admin: dict = Depends(get_admin_user)):
    """Admin classes — live MongoDB read, no cache, no status filter."""
    classes = await db.classes.find({}, {"_id": 0}).to_list(1000)
    return classes

@router.get("/admin/content/streams")
async def admin_list_streams(admin: dict = Depends(get_admin_user)):
    """Admin streams — live MongoDB read, no cache, no status filter."""
    streams = await db.streams.find({}, {"_id": 0}).to_list(1000)
    return streams

@router.get("/admin/content/subjects")
async def admin_list_subjects(admin: dict = Depends(get_admin_user)):
    """Admin subjects — live MongoDB read, no cache, no status filter (drafts/unpublished included)."""
    subjects = await db.subjects.find({}, {"_id": 0}).to_list(2000)
    for s in subjects:
        if "thumbnail_url" in s and "thumbnailUrl" not in s:
            s["thumbnailUrl"] = s.pop("thumbnail_url")
    return subjects

@router.get("/admin/content/chapters/{subject_id}")
async def admin_list_chapters(subject_id: str, admin: dict = Depends(get_admin_user)):
    """Admin chapter list — always reads live from DB, no caching, includes all statuses and coverage score."""
    chapters = await db.chapters.find({"subject_id": subject_id}).sort("order_index", 1).to_list(500)
    result = []
    for c in chapters:
        ch = {k: v for k, v in c.items() if k != "_id"}
        if "coverage_score" not in ch:
            ch["coverage_score"] = None
        result.append(ch)
    return result

@router.post("/admin/cache/flush")
async def admin_flush_cache(admin: dict = Depends(get_admin_user)):
    for prefix in ("boards", "classes", "streams", "subjects", "chapters"):
        _invalidate_content_cache(prefix)
    return {"message": "All content caches flushed"}

@router.post("/admin/cache/purge-all")
async def admin_purge_all_cache(admin: dict = Depends(get_admin_user), background_tasks: BackgroundTasks = None):
    for prefix in ("boards", "classes", "streams", "subjects", "chapters"):
        _invalidate_content_cache(prefix)
    cf_ok = False
    try:
        from cloudflare_client import purge_all_content_cache
        cf_ok = await purge_all_content_cache()
    except Exception:
        pass
    d1_ok = False
    try:
        from d1_sync import is_d1_configured, sync_full
        if is_d1_configured() and background_tasks:
            background_tasks.add_task(sync_full, db)
            d1_ok = True
    except Exception:
        pass
    return {
        "message": "All content caches purged (backend + Cloudflare edge + D1)",
        "cloudflare_purged": cf_ok,
        "d1_sync_queued": d1_ok,
    }

@router.post("/admin/prerender/refresh")
async def admin_prerender_refresh(
    admin: dict = Depends(get_admin_user),
    immediate: bool = Query(False, description="If true, fire the deploy hook now without debounce"),
):
    """Manually trigger a Cloudflare Pages rebuild so prerendered subject /
    chapter HTML reflects the latest content (Task #387).

    By default this enqueues a debounced refresh — multiple admin clicks
    within the coalesce window collapse to a single deploy. Pass
    `immediate=true` to bypass debounce (useful after a large bulk import
    where the admin wants the rebuild to start right now).
    """
    from pages_deploy import (
        is_configured, schedule_refresh, trigger_now, status as deploy_status,
    )
    if not is_configured():
        raise HTTPException(
            status_code=503,
            detail="CF_PAGES_DEPLOY_HOOK_URL not set — configure the Cloudflare Pages deploy hook to enable on-demand prerender refresh",
        )
    if immediate:
        ok = await trigger_now(reason=f"admin_manual:{admin.get('email', 'unknown')}")
    else:
        ok = schedule_refresh(reason=f"admin_manual:{admin.get('email', 'unknown')}")
    return {"queued": bool(ok), "immediate": immediate, "status": deploy_status()}


@router.get("/admin/prerender/status")
async def admin_prerender_status(admin: dict = Depends(get_admin_user)):
    """Inspect the Cloudflare Pages deploy-hook trigger state (Task #387)."""
    from pages_deploy import status as deploy_status
    return deploy_status()


@router.post("/admin/d1-sync")
async def admin_trigger_d1_sync(admin: dict = Depends(get_admin_user), tables: Optional[str] = Query(None)):
    from d1_sync import is_d1_configured, sync_full, sync_tables
    if not is_d1_configured():
        raise HTTPException(status_code=503, detail="D1 sync not configured — set D1_SYNC_SECRET and EDGE_WORKER_URL env vars")
    if tables:
        table_list = [t.strip() for t in tables.split(",") if t.strip()]
        result = await sync_tables(db, table_list)
    else:
        result = await sync_full(db)
        # Task #406 — fire the extended D1 mirror sync (seo_meta +
        # audit_log + syllabus_map) immediately after the primary
        # sync_full so the nightly scheduler that hits this endpoint
        # keeps the Pages SSR layer fresh. Wrapped so a failure of
        # the extended mirror never aborts the primary sync result.
        # Task #428 — surface the extended-mirror summary in the
        # response so a manual dry-run shows success + row_counts
        # without a separate /admin/cf-health round-trip.
        ext_summary: dict[str, Any]
        try:
            import d1_mirror as _d1_mirror
            ext_result = await _d1_mirror.sync_extended(db)
            logger.info(f"d1_mirror.sync_extended result: {ext_result}")
            ext_summary = ext_result if isinstance(ext_result, dict) else {"success": False, "reason": "non_dict_result"}
        except Exception as _ext_exc:
            logger.warning(f"d1_mirror.sync_extended failed (non-blocking): {_ext_exc}")
            ext_summary = {"success": False, "reason": f"{type(_ext_exc).__name__}: {_ext_exc}"}
        if isinstance(result, dict):
            result = {**result, "extended_mirror": ext_summary}
        else:
            result = {"primary": result, "extended_mirror": ext_summary}
    # Task #795: after MongoDB → D1 sync the worker's edge cache may still
    # serve responses backed by stale D1 reads until each key's TTL
    # expires. Force a worker-cache purge_all so the next read repopulates
    # from the freshly synced D1 tables. Best-effort — log but never raise
    # because the sync itself succeeded.
    try:
        from cloudflare_client import purge_worker_cache
        await purge_worker_cache(purge_all=True)
        logger.info("admin.purge route=admin.d1_sync worker=all")
    except Exception as e:
        logger.warning(f"admin.purge worker after d1-sync failed: {e}")
    return result

@router.get("/admin/d1-export")
async def admin_d1_export(request: Request):
    auth_header = request.headers.get("Authorization", "")
    from d1_sync import D1_SYNC_SECRET
    if not D1_SYNC_SECRET or D1_SYNC_SECRET == "REPLACE_WITH_SECURE_RANDOM_SECRET":
        raise HTTPException(status_code=503, detail="D1 sync secret not configured")
    if auth_header != f"Bearer {D1_SYNC_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")
    from d1_sync import export_content_catalog
    payload = await export_content_catalog(db)
    if not payload:
        raise HTTPException(status_code=500, detail="Export returned empty")
    return payload

_ALLOWED_HIERARCHY_STATUSES = {"published", "draft", "unpublished", "archived"}

def _normalize_hierarchy_status(value, default="published"):
    s = (value or "").strip().lower() if isinstance(value, str) else ""
    if not s:
        return default
    if s not in _ALLOWED_HIERARCHY_STATUSES:
        raise HTTPException(status_code=422, detail=f"Invalid status '{value}'. Must be one of: {sorted(_ALLOWED_HIERARCHY_STATUSES)}")
    return s

@router.post("/admin/content/boards")
async def admin_create_board(data: dict, admin: dict = Depends(get_admin_user)):
    try:
        if not await is_mongo_available():
            raise HTTPException(status_code=503, detail="MongoDB unavailable - please retry in a few seconds")
        name = (data.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=422, detail="Board name is required")
        board_id = str(uuid.uuid4())[:8]
        board = {
            "id": board_id,
            "name": name,
            "slug": name.lower().replace(" ", "-"),
            "description": data.get("description", ""),
            "status": _normalize_hierarchy_status(data.get("status")),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await asyncio.wait_for(db.boards.insert_one(board), timeout=8.0)
        _invalidate_content_cache("boards")
        _schedule_d1_sync_fire("boards")
        return {k: v for k, v in board.items() if k != "_id"}
    except HTTPException:
        raise
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Database timeout - please retry")
    except Exception as e:
        logger.error(f"Board creation failed: {e}")
        mark_mongo_down()
        raise HTTPException(status_code=503, detail="Database error - please retry")

@router.patch("/admin/content/boards/{board_id}")
async def admin_update_board(board_id: str, data: dict, admin: dict = Depends(get_admin_user)):
    allowed = {k: v for k, v in data.items() if k in ["name", "description", "status"]}
    if "name" in allowed:
        allowed["slug"] = allowed["name"].lower().replace(" ", "-")
    if "status" in allowed:
        allowed["status"] = _normalize_hierarchy_status(allowed["status"])
    if allowed:
        await db.boards.update_one({"id": board_id}, {"$set": allowed})
        _invalidate_content_cache("boards")
        _schedule_d1_sync_fire("boards")
    return {"message": "Board updated"}

@router.delete("/admin/content/boards/{board_id}")
async def admin_delete_board(board_id: str, admin: dict = Depends(get_admin_user)):
    board = await db.boards.find_one({"id": board_id})
    if not board:
        raise HTTPException(status_code=404, detail="Board not found")
    child_classes = await db.classes.find({"board_id": board_id}).to_list(None)
    for cls in child_classes:
        child_streams = await db.streams.find({"class_id": cls["id"]}).to_list(None)
        for stream in child_streams:
            await _cascade_delete_stream_children(stream["id"])
        await db.streams.delete_many({"class_id": cls["id"]})
    await db.classes.delete_many({"board_id": board_id})
    await db.boards.delete_one({"id": board_id})
    _invalidate_content_cache("boards")
    _invalidate_content_cache("classes")
    _invalidate_content_cache("streams")
    _invalidate_content_cache("subjects")
    _invalidate_content_cache("chapters")
    _schedule_d1_sync_fire("boards", "classes", "streams", "subjects", "chapters")
    _schedule_prerender_refresh(f"board_deleted:{board.get('slug') or board_id}")
    return {"message": "Board and all children deleted"}

@router.post("/admin/content/classes")
async def admin_create_class(data: dict, admin: dict = Depends(get_admin_user)):
    try:
        if not await is_mongo_available():
            raise HTTPException(status_code=503, detail="MongoDB unavailable - cannot create content")
        class_id = str(uuid.uuid4())[:8]
        cls = {
            "id": class_id,
            "board_id": data["board_id"],
            "name": data["name"],
            "slug": data["name"].lower().replace(" ", "-"),
            "description": data.get("description", ""),
            "status": _normalize_hierarchy_status(data.get("status")),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.classes.insert_one(cls)
        _invalidate_content_cache("classes")
        _schedule_d1_sync_fire("classes")
        return {k: v for k, v in cls.items() if k != "_id"}
    except HTTPException:
        raise
    except Exception:
        mark_mongo_down()
        raise HTTPException(status_code=503, detail="Database error")

@router.patch("/admin/content/classes/{class_id}")
async def admin_update_class(class_id: str, data: dict, admin: dict = Depends(get_admin_user)):
    allowed = {k: v for k, v in data.items() if k in ["name", "description", "status"]}
    if "name" in allowed:
        allowed["slug"] = allowed["name"].lower().replace(" ", "-")
    if "status" in allowed:
        allowed["status"] = _normalize_hierarchy_status(allowed["status"])
    if allowed:
        await db.classes.update_one({"id": class_id}, {"$set": allowed})
        _invalidate_content_cache("classes")
        _schedule_d1_sync_fire("classes")
    return {"message": "Class updated"}

@router.delete("/admin/content/classes/{class_id}")
async def admin_delete_class(class_id: str, admin: dict = Depends(get_admin_user)):
    cls = await db.classes.find_one({"id": class_id})
    if not cls:
        raise HTTPException(status_code=404, detail="Class not found")
    child_streams = await db.streams.find({"class_id": class_id}).to_list(None)
    for stream in child_streams:
        await _cascade_delete_stream_children(stream["id"])
    await db.streams.delete_many({"class_id": class_id})
    await db.classes.delete_one({"id": class_id})
    _invalidate_content_cache("classes")
    _invalidate_content_cache("streams")
    _invalidate_content_cache("subjects")
    _invalidate_content_cache("chapters")
    _schedule_d1_sync_fire("classes", "streams", "subjects", "chapters")
    _schedule_prerender_refresh(f"class_deleted:{cls.get('slug') or class_id}")
    return {"message": "Class and all children deleted"}

@router.post("/admin/content/streams")
async def admin_create_stream(data: dict, admin: dict = Depends(get_admin_user)):
    try:
        if not await is_mongo_available():
            raise HTTPException(status_code=503, detail="MongoDB unavailable - cannot create content")
        stream_id = str(uuid.uuid4())[:8]
        stream = {
            "id": stream_id,
            "class_id": data["class_id"],
            "name": data["name"],
            "slug": data["name"].lower().replace(" ", "-"),
            "description": data.get("description", ""),
            "icon": data.get("icon", "📚"),
            "status": _normalize_hierarchy_status(data.get("status")),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.streams.insert_one(stream)
        _invalidate_content_cache("streams")
        _schedule_d1_sync_fire("streams")
        return {k: v for k, v in stream.items() if k != "_id"}
    except HTTPException:
        raise
    except Exception:
        mark_mongo_down()
        raise HTTPException(status_code=503, detail="Database error")

@router.patch("/admin/content/streams/{stream_id}")
async def admin_update_stream(stream_id: str, data: dict, admin: dict = Depends(get_admin_user)):
    allowed = {k: v for k, v in data.items() if k in ["name", "description", "icon", "status"]}
    if "name" in allowed:
        allowed["slug"] = allowed["name"].lower().replace(" ", "-")
    if "status" in allowed:
        allowed["status"] = _normalize_hierarchy_status(allowed["status"])
    if allowed:
        await db.streams.update_one({"id": stream_id}, {"$set": allowed})
        _invalidate_content_cache("streams")
        _schedule_d1_sync_fire("streams")
    return {"message": "Stream updated"}

@router.delete("/admin/content/streams/{stream_id}")
async def admin_delete_stream(stream_id: str, admin: dict = Depends(get_admin_user)):
    stream = await db.streams.find_one({"id": stream_id})
    if not stream:
        raise HTTPException(status_code=404, detail="Stream not found")
    await _cascade_delete_stream_children(stream_id)
    await db.streams.delete_one({"id": stream_id})
    _invalidate_content_cache("streams")
    _invalidate_content_cache("subjects")
    _invalidate_content_cache("chapters")
    _schedule_d1_sync_fire("streams", "subjects", "chapters")
    _schedule_prerender_refresh(f"stream_deleted:{stream.get('slug') or stream_id}")
    return {"message": "Stream and all children deleted"}


# ─────────────────────────────────────────────
# ADMIN — FYUGP Auto-Assign
# Re-links subjects from PDF imports into pre-built FYUGP semester/stream slots
# ─────────────────────────────────────────────
@router.post("/admin/fyugp/auto-assign")
async def admin_fyugp_auto_assign(admin: dict = Depends(get_admin_user)):
    """
    Scans every subject that has paper_type + class_name data (from PDF imports)
    and re-links them to the canonical FYUGP Semester 1-4 classes (c7-c10) and
    their 6 pre-built course-type streams (Major/Minor/MDC/VAC/AEC/SEC).
    Safe to run multiple times — idempotent.
    """
    if not await is_mongo_available():
        raise HTTPException(status_code=503, detail="MongoDB unavailable")

    from syllabus_linker import _parse_semester_number, NEP_COURSE_STREAMS

    # FYUGP canonical class map: slug → id
    fyugp_classes = {
        "semester-1": "c7", "semester-2": "c8",
        "semester-3": "c9", "semester-4": "c10",
    }
    # Stream slug → canonical stream id per class
    fyugp_streams = {
        "c7":  {"major": "s30", "minor": "s31", "mdc": "s32", "vac": "s33", "aec": "s34", "sec": "s35"},
        "c8":  {"major": "s36", "minor": "s37", "mdc": "s38", "vac": "s39", "aec": "s40", "sec": "s41"},
        "c9":  {"major": "s42", "minor": "s43", "mdc": "s44", "vac": "s45", "aec": "s46", "sec": "s47"},
        "c10": {"major": "s48", "minor": "s49", "mdc": "s50", "vac": "s51", "aec": "s52", "sec": "s53"},
    }

    subjects = await db.subjects.find(
        {"source": "pdf_import", "paper_type": {"$exists": True, "$ne": ""}},
        {"_id": 0}
    ).to_list(5000)

    reassigned = 0
    skipped = 0

    for subj in subjects:
        paper_type = (subj.get("paper_type") or "").lower().strip()
        if paper_type not in NEP_COURSE_STREAMS:
            skipped += 1
            continue

        # Determine semester from class_name or class_slug
        sem_text = subj.get("className") or subj.get("class_slug") or ""
        sem_num  = _parse_semester_number(sem_text)
        if not sem_num or sem_num > 4:
            skipped += 1
            continue

        class_slug = f"semester-{sem_num}"
        class_id   = fyugp_classes.get(class_slug)
        stream_id  = fyugp_streams.get(class_id, {}).get(paper_type)
        if not class_id or not stream_id:
            skipped += 1
            continue

        # Already correct — skip
        if subj.get("stream_id") == stream_id:
            continue

        # Fetch stream + class metadata for denorm fields
        stream_doc = await db.streams.find_one({"id": stream_id}, {"_id": 0})
        class_doc  = await db.classes.find_one({"id": class_id}, {"_id": 0})
        stream_name = stream_doc.get("name", paper_type.upper()) if stream_doc else paper_type.upper()
        class_name  = class_doc.get("name", f"Semester {sem_num}") if class_doc else f"Semester {sem_num}"

        await db.subjects.update_one(
            {"id": subj["id"]},
            {"$set": {
                "stream_id":   stream_id,
                "stream_slug": paper_type,
                "class_slug":  class_slug,
                "class_id":    class_id,
                "className":   class_name,
                "streamName":  stream_name,
                "boardId":     "b2",
                "boardName":   "DEGREE",
                "board_slug":  "degree",
            }}
        )
        reassigned += 1

    _invalidate_content_cache("subjects")
    _invalidate_content_cache("streams")
    _invalidate_content_cache("classes")
    return {
        "message": f"FYUGP auto-assign complete",
        "reassigned": reassigned,
        "skipped": skipped,
        "total_scanned": len(subjects),
    }


# ─────────────────────────────────────────────
# ADMIN CONTENT MANAGEMENT — Subjects
# ─────────────────────────────────────────────
@router.post("/admin/content/subjects")
async def admin_create_subject(data: SubjectCreate, admin: dict = Depends(get_admin_user)):
    try:
        if not await is_mongo_available():
            raise HTTPException(status_code=503, detail="MongoDB unavailable - please retry in a few seconds")
        
        stream_name_val = ""
        board_id_val = ""
        board_name_val = ""
        board_slug_val = ""
        class_name_val = ""
        class_slug_val = ""
        stream_slug_val = ""
        stream_id_val = data.stream_id or ""

        if data.stream_id:
            stream = await asyncio.wait_for(
                db.streams.find_one({"id": data.stream_id}, {"_id": 0}), timeout=5.0
            )
            if not stream:
                raise HTTPException(status_code=404, detail="Stream not found")
            stream_name_val = stream.get("name", "")
            stream_slug_val = stream.get("slug", "")
            class_obj = await asyncio.wait_for(
                db.classes.find_one({"id": stream.get("class_id")}, {"_id": 0}), timeout=5.0
            )
            board = await asyncio.wait_for(
                db.boards.find_one({"id": class_obj.get("board_id") if class_obj else None}, {"_id": 0}), timeout=5.0
            )
            board_id_val = board.get("id", "") if board else ""
            board_name_val = board.get("name", "") if board else ""
            board_slug_val = board.get("slug", "") if board else ""
            class_name_val = class_obj.get("name", "") if class_obj else ""
            class_slug_val = class_obj.get("slug", "") if class_obj else ""
        elif data.stream_name:
            stream_name_val = data.stream_name.strip()
        else:
            raise HTTPException(status_code=400, detail="Stream selection or custom stream name is required")
        
        tags_val = data.tags
        if isinstance(tags_val, str):
            tags_val = [t.strip() for t in tags_val.split(",") if t.strip()] if tags_val else []

        subject_id = str(uuid.uuid4())
        subj = {
            "id": subject_id,
            "name": data.name,
            "stream_id": stream_id_val,
            "stream_slug": stream_slug_val,
            "streamName": stream_name_val,
            "board_id": board_id_val,
            "boardId": board_id_val,
            "boardName": board_name_val,
            "board_slug": board_slug_val,
            "class_id": (class_obj.get("id", "") if class_obj else "") if data.stream_id else "",
            "className": class_name_val,
            "class_slug": class_slug_val,
            "description": data.description or "",
            "tags": tags_val,
            "thumbnailUrl": data.thumbnail_url or "",
            "status": data.status or "published",
            "slug": re.sub(r'-+', '-', re.sub(r'[^\w\s-]', '', data.name.lower().strip()).replace(' ', '-')).strip('-'),
            "chapter_count": 0,
            "gradient": "math",
            "icon": "📄",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        await asyncio.wait_for(db.subjects.insert_one(subj), timeout=8.0)
        _invalidate_content_cache("subjects")
        _schedule_d1_sync_fire("subjects")
        _schedule_indexnow_for_subject(subj)
        _schedule_prerender_refresh("subject_created")
        logger.info(f"Subject created: {data.name} (id={subject_id}, stream={stream_id_val})")
        return {k: v for k, v in subj.items() if k != "_id"}
    except HTTPException:
        raise
    except asyncio.TimeoutError:
        logger.error(f"Subject creation timed out for: {data.name}")
        raise HTTPException(status_code=504, detail="Database timeout - please retry")
    except Exception as exc:
        logger.error(f"Subject creation failed for {data.name}: {exc}")
        mark_mongo_down()
        raise HTTPException(status_code=503, detail="Database error - please retry")

@router.put("/admin/content/subjects/{subject_id}")
async def admin_update_subject(subject_id: str, data: dict, admin: dict = Depends(get_admin_user)):
    if "thumbnail_url" in data:
        data["thumbnailUrl"] = data.pop("thumbnail_url")
    allowed = {k: v for k, v in data.items() if k in ["name", "description", "tags", "status", "thumbnailUrl"]}
    allowed["updated_at"] = datetime.now(timezone.utc).isoformat()
    result = await db.subjects.update_one({"id": subject_id}, {"$set": allowed})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Subject not found")
    _invalidate_content_cache("subjects")
    _schedule_d1_sync_fire("subjects")
    if allowed.get("status") == "published":
        _clear_draft_served_subject(subject_id)
    updated_subj = await db.subjects.find_one({"id": subject_id}, {"_id": 0, "board_slug": 1, "class_slug": 1, "slug": 1})
    if updated_subj:
        _schedule_indexnow_for_subject(updated_subj)
    _schedule_prerender_refresh("subject_updated")
    return {"message": "Updated"}

@router.get("/admin/content/draft-served-subjects")
async def admin_draft_served_subjects(admin: dict = Depends(get_admin_user)):
    """Task #701 — list of subjects currently being served via the relaxed
    status filter (Task #700). The public chapter resolver tolerates
    draft/unpublished subjects so live URLs don't 404, and records each hit.
    Surfacing them here lets the admin Control Center show a "Subjects served
    as draft" widget with a one-click publish action.

    Returns: { "items": [{id, name, slug, status, first_served_at,
    last_served_at, count}, ...], "total": int }
    """
    items = _get_draft_served_subjects()
    return {"items": items, "total": len(items)}


_ALLOWED_BULK_STATUSES = {"published", "draft", "unpublished", "archived"}
_ALLOWED_BULK_SCOPES = {"subjects", "chapters"}


@router.post("/admin/content/bulk-status")
async def admin_bulk_status_update(data: dict, admin: dict = Depends(get_admin_user)):
    """Bulk-update the `status` field of many subjects or chapters in one call.

    Body: { "scope": "subjects"|"chapters", "ids": [str, ...], "status": "published"|"draft"|"unpublished"|"archived" }
    Returns: { "matched": int, "modified": int, "scope": str, "status": str }
    """
    scope = (data.get("scope") or "").strip().lower()
    new_status = (data.get("status") or "").strip().lower()
    raw_ids = data.get("ids") or []

    if scope not in _ALLOWED_BULK_SCOPES:
        raise HTTPException(status_code=400, detail=f"scope must be one of {sorted(_ALLOWED_BULK_SCOPES)}")
    if new_status not in _ALLOWED_BULK_STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {sorted(_ALLOWED_BULK_STATUSES)}")
    if not isinstance(raw_ids, list) or not raw_ids:
        raise HTTPException(status_code=400, detail="ids must be a non-empty list")

    ids = [str(i) for i in raw_ids if i]
    if len(ids) > 500:
        raise HTTPException(status_code=400, detail="ids cannot exceed 500 per request")

    coll = db.subjects if scope == "subjects" else db.chapters
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        result = await coll.update_many(
            {"id": {"$in": ids}},
            {"$set": {"status": new_status, "updated_at": now_iso}},
        )
    except Exception as e:
        logger.exception("bulk-status update failed")
        raise HTTPException(status_code=503, detail=f"Database error: {e}")

    _invalidate_content_cache(scope)
    _schedule_d1_sync_fire(scope)

    if scope == "subjects":
        if new_status == "published":
            for sid in ids:
                _clear_draft_served_subject(sid)
        try:
            async for subj in db.subjects.find(
                {"id": {"$in": ids}},
                {"_id": 0, "board_slug": 1, "class_slug": 1, "slug": 1},
            ):
                _schedule_indexnow_for_subject(subj)
        except Exception:
            pass
    else:
        # Chapter status changes invalidate dependent subject views and
        # warrant per-chapter IndexNow pings, mirroring the single-PATCH path.
        try:
            _invalidate_content_cache("subjects")
        except Exception:
            pass
        try:
            async for ch in db.chapters.find(
                {"id": {"$in": ids}},
                {"_id": 0, "subject_id": 1, "slug": 1},
            ):
                _schedule_indexnow_for_chapter(ch)
        except Exception:
            pass

    _schedule_prerender_refresh(f"bulk_status_{scope}")

    return {
        "scope": scope,
        "status": new_status,
        "matched": result.matched_count,
        "modified": result.modified_count,
    }


@router.patch("/admin/content/subjects/{subject_id}")
async def admin_patch_subject(subject_id: str, data: dict, admin: dict = Depends(get_admin_user)):
    """Update subject (PATCH method)"""
    if "thumbnail_url" in data:
        data["thumbnailUrl"] = data.pop("thumbnail_url")
    allowed = {k: v for k, v in data.items() if k in ["name", "description", "tags", "status", "thumbnailUrl"]}
    allowed["updated_at"] = datetime.now(timezone.utc).isoformat()
    result = await db.subjects.update_one({"id": subject_id}, {"$set": allowed})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Subject not found")
    _invalidate_content_cache("subjects")
    _schedule_d1_sync_fire("subjects")
    if allowed.get("status") == "published":
        _clear_draft_served_subject(subject_id)
    updated_subj = await db.subjects.find_one({"id": subject_id}, {"_id": 0, "board_slug": 1, "class_slug": 1, "slug": 1})
    if updated_subj:
        _schedule_indexnow_for_subject(updated_subj)
    _schedule_prerender_refresh("subject_patched")
    return {"message": "Subject updated"}



@router.post("/admin/content/subjects/{subject_id}/thumbnail")
async def upload_subject_thumbnail(
    subject_id: str,
    file: UploadFile = File(...),
    admin: dict = Depends(get_admin_user),
):
    subj = await db.subjects.find_one({"id": subject_id})
    if not subj:
        raise HTTPException(status_code=404, detail="Subject not found")
    allowed_types = {"image/png", "image/jpeg", "image/webp", "image/gif", "image/svg+xml"}
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail=f"Unsupported image type: {file.content_type}")
    file_content = await file.read()
    max_size = 2 * 1024 * 1024
    if len(file_content) > max_size:
        raise HTTPException(status_code=400, detail="Image must be under 2 MB")
    import base64
    b64 = base64.b64encode(file_content).decode("utf-8")
    data_url = f"data:{file.content_type};base64,{b64}"
    await db.subjects.update_one(
        {"id": subject_id},
        {"$set": {"thumbnailUrl": data_url, "updated_at": datetime.now(timezone.utc).isoformat()}}
    )
    # Task #795: subject thumbnailUrl is part of the public subject list
    # surfaced in /api/content/subjects + /api/content/library-bundle, so
    # the edge cache must be invalidated for the change to be visible.
    _purge_for_route("admin.subject_thumbnail_upload", ["subjects"], subject_id)
    return {"thumbnailUrl": data_url}


# ─────────────────────────────────────────────────────────────────────────────
# AI THUMBNAIL GENERATOR — Vision analysis + PIL abstract variant generation
# ─────────────────────────────────────────────────────────────────────────────

def _hex_to_rgb(h: str) -> tuple:
    h = h.lstrip('#')
    if len(h) == 3:
        h = ''.join(c*2 for c in h)
    try:
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
    except Exception:
        return (100, 80, 200)


def _extract_dominant_colors(img_bytes: bytes, n: int = 5) -> list:
    """Fast dominant color extraction using PIL pixel sampling."""
    from PIL import Image
    import io as _io
    img = Image.open(_io.BytesIO(img_bytes)).convert('RGB').resize((120, 180))
    pixels = list(img.getdata())
    buckets: dict = {}
    for r, g, b in pixels:
        key = (r // 48 * 48, g // 48 * 48, b // 48 * 48)
        buckets[key] = buckets.get(key, 0) + 1
    top = sorted(buckets.items(), key=lambda x: -x[1])[:n]
    return [f'#{r:02x}{g:02x}{b:02x}' for (r, g, b), _ in top]


_VISION_ANALYSIS_PROMPT = (
    "Analyze this book/textbook cover image. I need TWO things:\n"
    "1) Color analysis of the image\n"
    "2) Bounding boxes of ALL text regions (title, subtitle, author, edition, publisher, etc.)\n\n"
    "Return ONLY valid JSON (no extra text):\n"
    "{\"dominant_colors\":[\"#hex1\",\"#hex2\",\"#hex3\"],"
    "\"secondary_colors\":[\"#hex4\",\"#hex5\"],"
    "\"style\":\"minimalist|bold|academic|colorful|dark|light\","
    "\"mood\":\"serious|vibrant|calm|educational|professional\","
    "\"bg_is_dark\":true,"
    "\"accent_color\":\"#hex\","
    "\"text_regions\":["
    "{\"x_pct\":10,\"y_pct\":5,\"w_pct\":80,\"h_pct\":12,\"label\":\"title\"},"
    "{\"x_pct\":20,\"y_pct\":85,\"w_pct\":60,\"h_pct\":8,\"label\":\"author\"}"
    "]}\n\n"
    "text_regions: each box as percentage of image dimensions (0-100). "
    "x_pct=left edge %, y_pct=top edge %, w_pct=width %, h_pct=height %. "
    "Include EVERY text element you can see. Be generous with box sizes — "
    "make each box slightly larger than the text to ensure full coverage."
)


async def _analyze_with_groq_vision(b64_img: str, mime: str = "image/jpeg") -> dict:
    """Analyse a book-cover image via PROVIDER_PRIORITY['vision'] weighted dispatch.

    Routes through call_vision_with_dispatch: Vertex/Gemini (weight 2000) →
    Azure OpenAI (weight 1, 2nd-to-last) → Workers AI (weight 0, last-resort).
    Returns {} if all providers fail — no Groq/OpenRouter fallback.
    """
    raw = None
    try:
        from llm import call_vision_with_dispatch
        raw = await call_vision_with_dispatch(b64_img, _VISION_ANALYSIS_PROMPT, lang="en", mime_type=mime)
    except Exception as _ve:
        logger.warning("Vision dispatch exhausted all providers: %s", _ve)

    if raw:
        try:
            m = re.search(r'\{.*\}', raw, re.DOTALL)
            if m:
                return json.loads(m.group())
        except Exception:
            pass
    return {}


def _sanitize_text_regions(raw_regions: list) -> list:
    """Validate and normalize text_regions from Vision API. Skips invalid entries."""
    clean = []
    if not isinstance(raw_regions, list):
        return clean
    for r in raw_regions:
        if not isinstance(r, dict):
            continue
        try:
            x = float(str(r.get("x_pct", 0)).replace("%", ""))
            y = float(str(r.get("y_pct", 0)).replace("%", ""))
            w = float(str(r.get("w_pct", 0)).replace("%", ""))
            h = float(str(r.get("h_pct", 0)).replace("%", ""))
            x = max(0, min(100, x))
            y = max(0, min(100, y))
            w = max(1, min(100 - x, w))
            h = max(1, min(100 - y, h))
            if w < 1 or h < 1:
                continue
            clean.append({"x_pct": x, "y_pct": y, "w_pct": w, "h_pct": h, "label": str(r.get("label", ""))})
        except (TypeError, ValueError):
            continue
    return clean


def _inpaint_region(img, box, method=0):
    """
    Inpaint a rectangular region of the image by sampling surrounding pixels.
    box = (x0, y0, x1, y1) in pixel coords.
    method: 0=gaussian blur fill, 1=edge-color gradient, 2=median + blur
    """
    from PIL import Image, ImageFilter

    W, H = img.size
    x0 = max(0, int(box[0]))
    y0 = max(0, int(box[1]))
    x1 = min(W, int(box[2]))
    y1 = min(H, int(box[3]))

    if x1 <= x0 or y1 <= y0:
        return img

    rw = x1 - x0
    rh = y1 - y0
    margin = max(4, min(rw, rh) // 6)

    border_pixels = []
    for x in range(max(0, x0 - margin), min(W, x1 + margin)):
        for dy in range(margin):
            if 0 <= y0 - margin + dy < H:
                border_pixels.append(img.getpixel((x, y0 - margin + dy)))
            if 0 <= y1 + dy < H:
                border_pixels.append(img.getpixel((x, y1 + dy)))
    for y in range(max(0, y0 - margin), min(H, y1 + margin)):
        for dx in range(margin):
            if 0 <= x0 - margin + dx < W:
                border_pixels.append(img.getpixel((x0 - margin + dx, y)))
            if 0 <= x1 + dx < W:
                border_pixels.append(img.getpixel((x1 + dx, y)))

    if not border_pixels:
        border_pixels = [(128, 128, 128)]

    if method == 0:
        patch = img.crop((max(0, x0 - margin * 2), max(0, y0 - margin * 2),
                          min(W, x1 + margin * 2), min(H, y1 + margin * 2)))
        blurred = patch.filter(ImageFilter.GaussianBlur(radius=max(rw, rh) // 2 + 4))
        bx0 = max(0, x0 - margin * 2)
        by0 = max(0, y0 - margin * 2)
        crop_x0 = x0 - bx0
        crop_y0 = y0 - by0
        fill_patch = blurred.crop((crop_x0, crop_y0, crop_x0 + rw, crop_y0 + rh))
        img.paste(fill_patch, (x0, y0))

    elif method == 1:
        top_colors = []
        bottom_colors = []
        left_colors = []
        right_colors = []

        for x in range(x0, x1):
            if y0 > 0:
                top_colors.append(img.getpixel((x, max(0, y0 - 1))))
            if y1 < H:
                bottom_colors.append(img.getpixel((x, min(H - 1, y1))))
        for y in range(y0, y1):
            if x0 > 0:
                left_colors.append(img.getpixel((max(0, x0 - 1), y)))
            if x1 < W:
                right_colors.append(img.getpixel((min(W - 1, x1), y)))

        def avg_color(pixels):
            if not pixels:
                return (128, 128, 128)
            r = sum(p[0] for p in pixels) // len(pixels)
            g = sum(p[1] for p in pixels) // len(pixels)
            b = sum(p[2] for p in pixels) // len(pixels)
            return (r, g, b)

        tc = avg_color(top_colors)
        bc = avg_color(bottom_colors)
        lc = avg_color(left_colors)
        rc = avg_color(right_colors)

        patch = Image.new('RGB', (rw, rh))
        for py in range(rh):
            for px in range(rw):
                ty = py / max(1, rh - 1)
                tx = px / max(1, rw - 1)
                vr = int(tc[0] * (1 - ty) + bc[0] * ty)
                vg = int(tc[1] * (1 - ty) + bc[1] * ty)
                vb = int(tc[2] * (1 - ty) + bc[2] * ty)
                hr = int(lc[0] * (1 - tx) + rc[0] * tx)
                hg = int(lc[1] * (1 - tx) + rc[1] * tx)
                hb = int(lc[2] * (1 - tx) + rc[2] * tx)
                fr = (vr + hr) // 2
                fg = (vg + hg) // 2
                fb = (vb + hb) // 2
                patch.putpixel((px, py), (fr, fg, fb))
        patch = patch.filter(ImageFilter.GaussianBlur(radius=2))
        img.paste(patch, (x0, y0))

    elif method == 2:
        expanded = img.crop((max(0, x0 - margin * 3), max(0, y0 - margin * 3),
                             min(W, x1 + margin * 3), min(H, y1 + margin * 3)))
        median = expanded.filter(ImageFilter.MedianFilter(size=5))
        blurred = median.filter(ImageFilter.GaussianBlur(radius=max(rw, rh) // 3 + 3))
        bx0 = max(0, x0 - margin * 3)
        by0 = max(0, y0 - margin * 3)
        crop_x0 = x0 - bx0
        crop_y0 = y0 - by0
        fill_patch = blurred.crop((crop_x0, crop_y0, crop_x0 + rw, crop_y0 + rh))
        img.paste(fill_patch, (x0, y0))

    return img


def _remove_text_variant(img_bytes: bytes, text_regions: list, variant: int) -> str:
    """
    Create a text-free replica of the source image using PIL inpainting.
    variant 0 = gaussian blur fill
    variant 1 = edge-color gradient fill
    variant 2 = median + blur fill
    Returns a PNG data URL.
    """
    from PIL import Image, ImageFilter
    import io as _io

    img = Image.open(_io.BytesIO(img_bytes)).convert('RGB')
    W, H = img.size

    padding_pct = [3, 4, 5][variant]

    for region in text_regions:
        x_pct = region.get("x_pct", 0)
        y_pct = region.get("y_pct", 0)
        w_pct = region.get("w_pct", 0)
        h_pct = region.get("h_pct", 0)

        px = max(0, int((x_pct - padding_pct) / 100 * W))
        py = max(0, int((y_pct - padding_pct) / 100 * H))
        pw = min(W, int((x_pct + w_pct + padding_pct) / 100 * W))
        ph = min(H, int((y_pct + h_pct + padding_pct) / 100 * H))

        img = _inpaint_region(img, (px, py, pw, ph), method=variant)

    if variant == 0:
        img = img.filter(ImageFilter.GaussianBlur(radius=0.5))
    elif variant == 2:
        from PIL import ImageEnhance
        img = ImageEnhance.Sharpness(img).enhance(1.1)

    buf = _io.BytesIO()
    img.save(buf, format='PNG', optimize=True)
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode()
    return f'data:image/png;base64,{b64}'


@router.post("/admin/thumbnail/generate-cms")
async def generate_cms_thumbnails(
    doc_id: str = Form(...),
    file: UploadFile = File(...),
    admin: dict = Depends(get_admin_user),
):
    """
    Upload a cover image → Groq Vision color-DNA analysis → 3 abstract copyright-safe variants.
    Works with CMS documents (doc_id) instead of subject_id.
    Returns: {original_url, variants:[v1,v2,v3], analysis:{colors,style,mood}}
    """
    img_bytes = await file.read()
    mime_type = file.content_type or "image/png"

    if len(img_bytes) > 3 * 1024 * 1024:
        raise HTTPException(400, "Image must be under 3 MB")

    # ── Resize for Vision ───────────────────────────────────────────────────
    from PIL import Image as _PILImage
    import io as _io
    try:
        src_img = _PILImage.open(_io.BytesIO(img_bytes)).convert('RGB')
        src_img.thumbnail((400, 600), _PILImage.LANCZOS)
        buf = _io.BytesIO()
        src_img.save(buf, format='PNG')
        buf.seek(0)
        img_bytes_resized = buf.read()
    except Exception as _pe:
        logger.warning(f"PIL resize failed: {_pe}")
        img_bytes_resized = img_bytes

    b64_src = base64.b64encode(img_bytes_resized).decode()
    original_url = f"data:image/png;base64,{b64_src}"

    # ── Vision analysis + PIL fallback ──────────────────────────────────────
    analysis = await _analyze_with_groq_vision(b64_src, "image/png")
    pil_colors = _extract_dominant_colors(img_bytes_resized)

    if analysis.get("dominant_colors"):
        colors = analysis["dominant_colors"][:3] + analysis.get("secondary_colors", [])[:2]
        colors = (colors + pil_colors)[:5]
    else:
        colors = pil_colors[:5]
        analysis = {"dominant_colors": colors, "style": "educational", "mood": "academic"}

    text_regions = _sanitize_text_regions(analysis.get("text_regions", []))

    # ── Generate 3 text-free replica variants in parallel ───────────────────
    loop = asyncio.get_event_loop()
    variants = await asyncio.gather(
        loop.run_in_executor(None, _remove_text_variant, img_bytes_resized, text_regions, 0),
        loop.run_in_executor(None, _remove_text_variant, img_bytes_resized, text_regions, 1),
        loop.run_in_executor(None, _remove_text_variant, img_bytes_resized, text_regions, 2),
    )

    # ── Persist thumbnail_variants to cms_documents ─────────────────────────
    await db.cms_documents.update_one(
        {"id": doc_id},
        {"$set": {
            "thumbnail_variants": {
                "original_url":  original_url,
                "variant1_url":  variants[0],
                "variant2_url":  variants[1],
                "variant3_url":  variants[2],
                "analysis":      analysis,
                "generated_at":  datetime.now(timezone.utc).isoformat(),
            },
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }},
    )
    logger.info(f"CMS thumbnail variants generated for doc {doc_id}: {len(colors)} colors")
    return {
        "original_url":  original_url,
        "variants":      list(variants),
        "analysis":      analysis,
        "auto_selected": 0,
    }


@router.post("/admin/thumbnail/generate")
async def generate_ai_thumbnails(
    subject_id: str = Form(...),
    file: Optional[UploadFile] = File(default=None),
    admin: dict = Depends(get_admin_user),
):
    """
    Upload a book cover (or use existing thumbnailUrl) → Vision analysis → 3 abstract variants.
    Returns: {original_url, variants:[v1,v2,v3], analysis:{colors,style,mood}, auto_selected:0}
    """
    # ── Get or read the source image ──────────────────────────────────────
    subj = await db.subjects.find_one({"id": subject_id}, {"_id": 0})
    if not subj:
        raise HTTPException(404, "Subject not found")

    img_bytes: Optional[bytes] = None
    mime_type = "image/png"

    if file and file.filename:
        img_bytes = await file.read()
        mime_type = file.content_type or "image/png"
    elif subj.get("thumbnailUrl", "").startswith("data:"):
        # decode existing base64 thumbnail
        data_url = subj["thumbnailUrl"]
        header, b64_str = data_url.split(",", 1)
        mime_type = header.split(":")[1].split(";")[0]
        img_bytes = base64.b64decode(b64_str)

    if not img_bytes:
        raise HTTPException(400, "No source image: upload a file or ensure the subject has an existing thumbnail")

    if len(img_bytes) > 3 * 1024 * 1024:
        raise HTTPException(400, "Image must be under 3 MB")

    # ── Resize source to 400×600 for Vision ───────────────────────────────
    from PIL import Image as _PILImage
    import io as _io
    try:
        src_img = _PILImage.open(_io.BytesIO(img_bytes)).convert('RGB')
        src_img.thumbnail((400, 600), _PILImage.LANCZOS)
        buf = _io.BytesIO()
        src_img.save(buf, format='PNG')
        buf.seek(0)
        img_bytes_resized = buf.read()
    except Exception as _pe:
        logger.warning(f"PIL resize failed: {_pe}")
        img_bytes_resized = img_bytes

    b64_src = base64.b64encode(img_bytes_resized).decode()
    original_url = f"data:image/png;base64,{b64_src}"

    # ── Step 1: Groq Vision analysis — colors + text bounding boxes ──────
    analysis = await _analyze_with_groq_vision(b64_src, "image/png")

    # ── Step 2: PIL color extraction (always-on fallback) ─────────────────
    pil_colors = _extract_dominant_colors(img_bytes_resized)

    if analysis.get("dominant_colors"):
        colors = analysis["dominant_colors"][:3] + analysis.get("secondary_colors", [])[:2]
        colors = (colors + pil_colors)[:5]
    else:
        colors = pil_colors[:5]
        analysis = {"dominant_colors": colors, "style": "educational", "mood": "academic"}

    text_regions = _sanitize_text_regions(analysis.get("text_regions", []))

    # ── Step 3: Generate 3 text-free replica variants ─────────────────────
    loop = asyncio.get_event_loop()
    variants = await asyncio.gather(
        loop.run_in_executor(None, _remove_text_variant, img_bytes_resized, text_regions, 0),
        loop.run_in_executor(None, _remove_text_variant, img_bytes_resized, text_regions, 1),
        loop.run_in_executor(None, _remove_text_variant, img_bytes_resized, text_regions, 2),
    )

    # ── Step 4: Persist to MongoDB ─────────────────────────────────────────
    thumbnails_data = {
        "original_url":    original_url,
        "variant1_url":    variants[0],
        "variant2_url":    variants[1],
        "variant3_url":    variants[2],
        "analysis":        analysis,
        "generated_at":    datetime.now(timezone.utc).isoformat(),
        "auto_selected":   0,
    }
    await db.subjects.update_one(
        {"id": subject_id},
        {"$set": {
            "thumbnail_variants": thumbnails_data,
            "thumbnailUrl": original_url,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }},
    )
    text_found = len(text_regions) > 0
    logger.info(f"AI thumbnails generated for subject {subject_id}: {len(text_regions)} text regions detected, {len(colors)} colors")
    # Task #795: this also overwrites `thumbnailUrl` and stores
    # `thumbnail_variants` on the subject document, both of which leak
    # into the public subject payload, so the edge cache must drop the
    # stale subject list.
    _purge_for_route("admin.thumbnail_generate", ["subjects"], subject_id)
    return {
        "original_url":  original_url,
        "variants":      list(variants),
        "analysis":      analysis,
        "auto_selected": 0,
        "text_regions_found": len(text_regions),
        "text_detection_status": "detected" if text_found else "none_found",
    }


@router.post("/admin/thumbnail/apply")
async def apply_thumbnail_variant(
    data: dict = Body(...),
    admin: dict = Depends(get_admin_user),
):
    """Set the active thumbnailUrl for a subject to one of the generated variants."""
    subject_id    = data.get("subject_id", "")
    variant_index = data.get("variant_index")
    if not subject_id or variant_index is None:
        raise HTTPException(400, "subject_id and variant_index required")
    try:
        variant_index = int(variant_index)
    except (TypeError, ValueError):
        raise HTTPException(400, "variant_index must be an integer 0, 1, or 2")
    if variant_index not in (0, 1, 2):
        raise HTTPException(400, "variant_index must be 0, 1, or 2")
    subject = await db.subjects.find_one({"id": subject_id})
    if not subject:
        raise HTTPException(404, f"Subject '{subject_id}' not found")
    variants = subject.get("thumbnail_variants") or {}
    variant_key = f"variant{variant_index + 1}_url"
    thumb_url = variants.get(variant_key)
    if not thumb_url:
        raise HTTPException(400, f"Variant '{variant_key}' not found for subject '{subject_id}'")
    await db.subjects.update_one(
        {"id": subject_id},
        {"$set": {"thumbnailUrl": thumb_url, "updated_at": datetime.now(timezone.utc).isoformat()}},
    )
    _invalidate_content_cache("subjects")
    return {"success": True}


@router.post("/admin/thumbnail/generate-bulk")
async def generate_ai_thumbnails_bulk(
    data: dict = Body(...),
    admin: dict = Depends(get_admin_user),
):
    """
    Bulk generate AI thumbnail variants for up to 50 subjects that already have a thumbnailUrl.
    Returns streaming-style progress list.
    """
    subject_ids = data.get("subject_ids", [])[:50]
    if not subject_ids:
        raise HTTPException(400, "subject_ids required")

    results = []
    for sid in subject_ids:
        subj = await db.subjects.find_one({"id": sid}, {"_id": 0, "thumbnailUrl": 1, "name": 1})
        if not subj or not subj.get("thumbnailUrl", "").startswith("data:"):
            results.append({"subject_id": sid, "status": "skipped", "reason": "no thumbnail"})
            continue
        try:
            data_url = subj["thumbnailUrl"]
            _, b64_str = data_url.split(",", 1)
            img_bytes = base64.b64decode(b64_str)
            colors    = _extract_dominant_colors(img_bytes)
            from PIL import Image as _PILImage
            import io as _io
            src_img = _PILImage.open(_io.BytesIO(img_bytes)).convert('RGB')
            src_img.thumbnail((400, 600), _PILImage.LANCZOS)
            buf = _io.BytesIO(); src_img.save(buf, format='PNG'); buf.seek(0)
            img_bytes_r = buf.read()
            pil_colors = _extract_dominant_colors(img_bytes_r)
            b64_src = base64.b64encode(img_bytes_r).decode()
            analysis = await _analyze_with_groq_vision(b64_src, "image/png")
            all_colors = (analysis.get("dominant_colors", [])[:3] + pil_colors)[:5] or pil_colors
            text_regions = _sanitize_text_regions(analysis.get("text_regions", []))
            loop = asyncio.get_event_loop()
            variants = await asyncio.gather(
                loop.run_in_executor(None, _remove_text_variant, img_bytes_r, text_regions, 0),
                loop.run_in_executor(None, _remove_text_variant, img_bytes_r, text_regions, 1),
                loop.run_in_executor(None, _remove_text_variant, img_bytes_r, text_regions, 2),
            )
            thumbnails_data = {
                "original_url": data_url,
                "variant1_url": variants[0],
                "variant2_url": variants[1],
                "variant3_url": variants[2],
                "analysis": analysis,
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }
            await db.subjects.update_one(
                {"id": sid},
                {"$set": {"thumbnail_variants": thumbnails_data, "updated_at": datetime.now(timezone.utc).isoformat()}}
            )
            results.append({"subject_id": sid, "name": subj.get("name",""), "status": "done"})
        except Exception as _be:
            logger.error(f"Bulk thumb error for {sid}: {_be}")
            results.append({"subject_id": sid, "status": "failed", "error": str(_be)})

    # Task #795: bulk run touched up to 50 subject docs in one shot —
    # one purge call covers all of them since `_invalidate_content_cache`
    # already drops the entire `subjects:*` keyspace + the library bundle.
    done_count = sum(1 for r in results if r["status"] == "done")
    if done_count:
        _purge_for_route(
            "admin.thumbnail_generate_bulk",
            ["subjects"],
            f"{done_count}/{len(subject_ids)} subjects",
        )

    return {"results": results, "total": len(subject_ids), "done": done_count}


def _generate_chapter_card_wallpaper(chapter_title: str, subject_name: str, variant: int = 0, size=(400, 225)) -> str:
    from PIL import Image as _PILImage, ImageDraw as _Draw
    import io as _io, hashlib as _hl

    seed = int(_hl.md5(f"{chapter_title}:{subject_name}:{variant}".encode()).hexdigest()[:8], 16)
    palette_sets = [
        [(99, 58, 237), (139, 92, 246), (59, 130, 246)],
        [(16, 185, 129), (6, 182, 212), (59, 130, 246)],
        [(236, 72, 153), (168, 85, 247), (99, 58, 237)],
        [(245, 158, 11), (249, 115, 22), (239, 68, 68)],
        [(20, 184, 166), (56, 189, 248), (99, 102, 241)],
    ]
    colors = palette_sets[(seed + variant) % len(palette_sets)]
    img = _PILImage.new('RGB', size, colors[0])
    draw = _Draw.Draw(img)
    rng_state = seed + variant * 7
    for i in range(8 + variant * 3):
        rng_state = (rng_state * 1103515245 + 12345) & 0x7FFFFFFF
        x = rng_state % size[0]
        rng_state = (rng_state * 1103515245 + 12345) & 0x7FFFFFFF
        y = rng_state % size[1]
        rng_state = (rng_state * 1103515245 + 12345) & 0x7FFFFFFF
        r = 30 + rng_state % 80
        c = colors[(i + variant) % len(colors)]
        alpha_c = tuple(min(255, v + 30) for v in c)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=alpha_c)
    for i in range(3 + variant):
        rng_state = (rng_state * 1103515245 + 12345) & 0x7FFFFFFF
        x1 = rng_state % size[0]
        rng_state = (rng_state * 1103515245 + 12345) & 0x7FFFFFFF
        y1 = rng_state % size[1]
        rng_state = (rng_state * 1103515245 + 12345) & 0x7FFFFFFF
        x2 = rng_state % size[0]
        rng_state = (rng_state * 1103515245 + 12345) & 0x7FFFFFFF
        y2 = rng_state % size[1]
        c = colors[(i + 1) % len(colors)]
        draw.line([(x1, y1), (x2, y2)], fill=c, width=2 + i)
    draw.rectangle([0, size[1] - 60, size[0], size[1]], fill=(0, 0, 0))
    buf = _io.BytesIO()
    img.save(buf, format='JPEG', quality=85)
    buf.seek(0)
    return f"data:image/jpeg;base64,{base64.b64encode(buf.read()).decode()}"


@router.post("/admin/thumbnail/generate-chapter-cards")
async def generate_chapter_card_thumbnails(
    data: dict = Body(...),
    admin: dict = Depends(get_admin_user),
):
    """
    Generate abstract educational wallpaper thumbnails for chapters within a subject.
    Creates 3 colour variants per chapter using deterministic seeded generation.
    """
    subject_id = data.get("subject_id", "")
    chapter_ids = data.get("chapter_ids", [])[:100]
    if not subject_id:
        raise HTTPException(400, "subject_id required")

    subject = await db.subjects.find_one({"id": subject_id}, {"_id": 0, "name": 1})
    if not subject:
        raise HTTPException(404, "Subject not found")
    subject_name = subject.get("name", "")

    query = {"subject_id": subject_id}
    if chapter_ids:
        query["id"] = {"$in": chapter_ids}
    chapters = await db.chapters.find(query, {"_id": 0, "id": 1, "title": 1}).to_list(100)

    loop = asyncio.get_event_loop()
    results = []
    for ch in chapters:
        ch_id = ch.get("id", "")
        ch_title = ch.get("title", "")
        try:
            variants = await asyncio.gather(
                loop.run_in_executor(None, _generate_chapter_card_wallpaper, ch_title, subject_name, 0),
                loop.run_in_executor(None, _generate_chapter_card_wallpaper, ch_title, subject_name, 1),
                loop.run_in_executor(None, _generate_chapter_card_wallpaper, ch_title, subject_name, 2),
            )
            thumb_data = {
                "variant1_url": variants[0],
                "variant2_url": variants[1],
                "variant3_url": variants[2],
                "generated_at": datetime.now(timezone.utc).isoformat(),
            }
            await db.chapters.update_one(
                {"id": ch_id},
                {"$set": {
                    "card_thumbnails": thumb_data,
                    "thumbnailUrl": variants[0],
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }}
            )
            results.append({"chapter_id": ch_id, "title": ch_title, "status": "done"})
        except Exception as _e:
            logger.error(f"Chapter card thumb error for {ch_id}: {_e}")
            results.append({"chapter_id": ch_id, "title": ch_title, "status": "failed", "error": str(_e)[:80]})

    # Task #795: each chapter doc had its `thumbnailUrl` and
    # `card_thumbnails` fields rewritten — those leak into chapter
    # listings, the chapter-by-slug payload, and the library bundle.
    # One purge for the chapters prefix invalidates all of them; we add
    # subjects too because the bundle nests chapters under subjects.
    done_count = sum(1 for r in results if r["status"] == "done")
    if done_count:
        _purge_for_route(
            "admin.thumbnail_generate_chapter_cards",
            ["chapters", "subjects"],
            f"{done_count}/{len(chapters)} chapters in {subject_id}",
        )

    return {
        "results": results,
        "total": len(chapters),
        "done": done_count,
    }


@router.delete("/admin/content/subjects/{subject_id}")
async def admin_delete_subject(subject_id: str, admin: dict = Depends(get_admin_user)):
    subj = await db.subjects.find_one({"id": subject_id})
    if not subj:
        raise HTTPException(status_code=404, detail="Subject not found")
    await _cascade_delete_subject_assets(subject_id)
    await db.subjects.delete_one({"id": subject_id})
    _invalidate_content_cache("subjects")
    _invalidate_content_cache("subjects_by_course_type")
    _invalidate_content_cache("chapters")
    _schedule_d1_sync_fire("subjects", "chapters")
    _schedule_prerender_refresh("subject_deleted")
    return {"message": "Deleted"}

@router.delete("/admin/content/seo-pages/rejected")
async def admin_delete_rejected_seo_pages(admin: dict = Depends(get_admin_user)):
    result = await db.seo_pages.delete_many({"status": "rejected"})
    return {"deleted": result.deleted_count}

async def _embed_chapter_bg(chapter_id: str, subject_id: str, title: str, description: str, topics: list, content: str):
    try:
        import server as _s
        emb = _s._syllabus_embedder
        if emb:
            count = await emb.embed_chapter(
                chapter_id=chapter_id,
                subject_id=subject_id,
                title=title,
                description=description,
                topics=topics,
                content=content,
            )
            logger.info(f"Embedded chapter '{title[:40]}': {count} embeddings")
    except Exception as exc:
        logger.warning(f"Chapter embedding failed for {chapter_id}: {exc}")


@router.post("/admin/content/chapters")
async def admin_create_chapter(data: ChapterCreate, admin: dict = Depends(get_admin_user)):
    chapter_id = str(uuid.uuid4())
    _order = data.order or data.order_index or 1
    _slug = data.slug.strip() if data.slug else ""
    if not _slug:
        _slug = re.sub(r'[^a-z0-9]+', '-', data.title.lower()).strip('-')
    existing = await db.chapters.find_one({"subject_id": data.subject_id, "slug": _slug})
    if existing:
        _slug = f"{_slug}-{chapter_id[:6]}"
    _category = data.category or "notes"
    _topics = data.topics or []
    chap = {
        "id": chapter_id,
        "subject_id": data.subject_id,
        "title": data.title,
        "slug": _slug,
        "description": data.description,
        "content": data.content,
        "content_as": data.content_as or "",
        "content_type": _category,
        "category": _category,
        "chapter_number": data.chapter_number,
        "order": _order,
        "order_index": _order,
        "status": data.status,
        "topics": _topics,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.chapters.insert_one(chap)
    # Task #383 — purge cross-pod KV mirror so the new chapter is
    # visible on every pod's /content/chapters/{subject_id} response
    # immediately, not only after the 5-minute TTL expires.
    try:
        from routes.content import invalidate_chapters_kv
        await invalidate_chapters_kv(data.subject_id)
    except Exception as _kv_exc:
        logger.debug(f"chapters KV invalidate (insert) failed: {_kv_exc}")

    await db.subjects.update_one(
        {"id": data.subject_id}, 
        {"$inc": {"chapter_count": 1}, "$set": {"has_document": True}}
    )
    
    chunks_created = []
    if data.content and len(data.content.strip()) > 100:
        try:
            chunks_created = await auto_chunk_content(
                chapter_id=chapter_id,
                content=data.content,
                subject_id=data.subject_id,
                category=_category,
                topics=_topics,
            )
            logger.info(f"Auto-chunked new chapter '{data.title}': {len(chunks_created)} chunks")
        except Exception as chunk_error:
            logger.error(f"Auto-chunking failed for chapter {chapter_id}: {chunk_error}")
    
    asyncio.create_task(_embed_chapter_bg(
        chapter_id, data.subject_id, data.title,
        data.description or "", _topics, data.content or "",
    ))

    # Permanent per-chapter quiz: generate ONCE at chapter-creation time
    # and pin it to MongoDB (``chapter_quizzes`` collection) so every
    # student who later clicks "Quiz me" gets the SAME stored quiz with
    # zero LLM round-trip and zero rate-limit charge. Best-effort
    # background task — chapter creation must not be blocked by a slow
    # / failed LLM call, and the lazy fallback in
    # ``edu_study.quiz_generate`` will recover the miss the first time
    # a student opens the quiz if this pre-generation skips or errors.
    try:
        from routes.edu_study import pregenerate_chapter_quiz
        # Intentionally NOT passing ``count=`` — the helper's default
        # is now ``_QUIZ_POOL_SIZE`` (≈24), which is the size of the
        # per-chapter conceptual question pool the user requested
        # ("3-4 quiz per chapter, one time, shuffle and provide to
        # user"). Hardcoding count=7 here would silently downgrade
        # every newly-created chapter to a 7-question pool and
        # defeat the purpose of the shuffle layer.
        asyncio.create_task(pregenerate_chapter_quiz(chap, response_lang="en"))
    except Exception as quiz_pregen_err:
        # Defensive: import / scheduling failure must never bubble up
        # to the admin caller. The chapter is already written; the
        # quiz can be generated lazily on first student click.
        logger.warning(
            f"Quiz pre-generation could not be scheduled for chapter {chapter_id}: "
            f"{quiz_pregen_err}"
        )
    
    result = {k: v for k, v in chap.items() if k != "_id"}
    result["chunks_created"] = len(chunks_created)
    _invalidate_content_cache("chapters")
    _invalidate_content_cache("subjects")
    _schedule_d1_sync_fire("chapters", "subjects")
    _schedule_indexnow_for_chapter(chap)
    _schedule_prerender_refresh("chapter_created")

    # SEO Phase D — auto cross-link the new chapter into the parent subject
    # hub + 2-3 sibling chapters, then fan out the patched URLs through the
    # Phase A IndexNow + cache purge + prewarm helper. Runs as a background
    # task so chapter creation stays fast; failures are logged and never
    # propagate back to the admin caller. `depth=0` is a structural guard
    # against cascading cross-links (Phase D contract: depth capped at 1).
    async def _do_cross_link():
        try:
            from syllabus_linker import cross_link_for_new_chapter
            from seo_fanout import fanout_for_urls
            urls = await cross_link_for_new_chapter(chapter_id, db=db, depth=0)
            if urls:
                fanout_for_urls(urls, source="phase_d_cross_link_new_chapter")
        except Exception as exc:
            logger.warning(f"phase_d cross-link failed for chapter {chapter_id}: {exc}")
    try:
        asyncio.create_task(_do_cross_link())
    except RuntimeError:
        pass

    return result

@router.post("/admin/content/chunks")
async def admin_create_chunk(data: ChunkCreate, admin: dict = Depends(get_admin_user)):
    """Create content chunk"""
    chunk_id = str(uuid.uuid4())
    _chunk_category = data.category or "notes"
    _chunk_content_type = _chunk_category
    chunk = {
        "id": chunk_id,
        "chapter_id": data.chapter_id,
        "content": data.content,
        "content_type": _chunk_content_type,
        "category": _chunk_category,
        "tags": data.tags,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    result = await db.chunks.insert_one(chunk)
    chunk["_id"] = str(result.inserted_id)
    # Task #795: chunks are addressed by /api/content/chunks/<chapter_id>
    # which is an explicitly cached prefix; without this purge the new
    # chunk wouldn't surface to readers until the per-key TTL expires.
    _purge_for_route("admin.chunk_create", ["chapters"], data.chapter_id)
    return chunk


_CONTENT_IMG_BUCKET = "study-materials"
_CONTENT_IMG_PREFIX = "content-images"
_CONTENT_IMG_MAX_MB = 10

def _content_img_supabase_upload(raw: bytes, storage_path: str, mime: str) -> str:
    supa.storage.from_(_CONTENT_IMG_BUCKET).upload(
        path=storage_path,
        file=raw,
        file_options={"content-type": mime, "upsert": "true"},
    )
    return supa.storage.from_(_CONTENT_IMG_BUCKET).get_public_url(storage_path)


@router.post("/admin/content/upload-image")
async def upload_content_image(
    file: UploadFile = File(...),
    admin: dict = Depends(get_admin_user),
):
    raw = await file.read()
    mime = file.content_type or "image/png"
    if not mime.startswith("image/"):
        raise HTTPException(400, "Only image files are supported")
    max_bytes = _CONTENT_IMG_MAX_MB * 1024 * 1024
    if len(raw) > max_bytes:
        raise HTTPException(413, f"Image exceeds {_CONTENT_IMG_MAX_MB} MB limit")

    ext = (file.filename or "img.png").rsplit(".", 1)[-1].lower()
    img_id = str(uuid.uuid4())
    safe_name = f"{img_id}.{ext}"
    r2_key = f"{_CONTENT_IMG_PREFIX}/{safe_name}"
    supa_path = r2_key

    # Priority 1: Cloudflare R2 (cheap, fast CDN)
    if R2_ENABLED:
        try:
            r2_url = await r2_upload(r2_key, raw, content_type=mime)
            # Task #383 — route the emitted URL through the R2-primary
            # helper. When R2_PRIMARY_ON is on this is identity (R2
            # was the canonical store and we just wrote to it); when
            # the flag is off we still emit the R2 URL because R2 was
            # the actual write backend. Centralising here means a
            # future "always serve via R2 even after Supabase write"
            # rollout flips one helper.
            url = r2_primary_read_url(r2_key, s3_fallback_url=r2_url)
            return {"url": url, "storage": "r2"}
        except Exception as exc:
            logger.warning(f"R2 image upload failed, falling back to Supabase: {exc}")

    # Priority 2: Supabase Storage
    if supa:
        try:
            sb_url = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: _content_img_supabase_upload(raw, supa_path, mime),
            )
            # Task #383 — when R2_PRIMARY_ON is on AND the same key
            # also lives in R2 (e.g. backfilled later), we want the
            # public URL we hand back to be the R2 one so the chapter
            # asset starts being served from R2 without a redeploy.
            # The helper falls back to the Supabase URL when R2 isn't
            # configured, so this is safe today and active tomorrow.
            url = r2_primary_read_url(r2_key, s3_fallback_url=sb_url)
            return {"url": url, "storage": "supabase"}
        except Exception as exc:
            logger.warning(f"Supabase image upload failed, using base64 fallback: {exc}")

    # Priority 3: base64 data-URL (last resort — not suitable for large images)
    b64 = base64.b64encode(raw).decode()
    return {"url": f"data:{mime};base64,{b64}", "storage": "base64"}


# Generic content upload endpoints
@router.post("/admin/content/upload")
async def upload_content_file(
    file: UploadFile = File(...),
    subject_id: str = Form(...),
    content_type: str = Form("document"),
    title: str = Form(None),
    description: str = Form(""),
    tags: str = Form(""),
    year: str = Form(""),
    admin: dict = Depends(get_admin_user)
):
    """Upload content file — stores PDFs in R2 (or Supabase), text inline."""
    content_id = str(uuid.uuid4())

    file_content = await file.read()
    file_ext = (file.filename or "file.txt").rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else "txt"
    text_content = ""
    file_url = ""
    storage_backend = "none"

    if file_ext == "pdf":
        mime = "application/pdf"
        r2_key = make_key("uploads/pdfs", file.filename or f"{content_id}.pdf")

        # Priority 1: R2
        if R2_ENABLED:
            try:
                r2_url = await r2_upload(r2_key, file_content, content_type=mime,
                                         cache_control="private, max-age=3600")
                # Task #383 — emitted URL goes through the R2-primary
                # helper so a future S3-fallback rewrite is one helper
                # change instead of touching every chapter PDF caller.
                file_url = r2_primary_read_url(r2_key, s3_fallback_url=r2_url)
                storage_backend = "r2"
            except Exception as exc:
                logger.warning(f"R2 PDF upload failed: {exc}")

        # Priority 2: Supabase Storage
        if not file_url and supa:
            supa_path = r2_key
            try:
                file_url = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: (
                        supa.storage.from_(_CONTENT_IMG_BUCKET).upload(
                            path=supa_path,
                            file=file_content,
                            file_options={"content-type": mime, "upsert": "true"},
                        ),
                        supa.storage.from_(_CONTENT_IMG_BUCKET).get_public_url(supa_path),
                    )[1],
                )
                storage_backend = "supabase"
            except Exception as exc:
                logger.warning(f"Supabase PDF upload failed: {exc}")

        # Priority 3: base64 fallback (only for small PDFs ≤ 2 MB)
        if not file_url:
            if len(file_content) <= 2 * 1024 * 1024:
                pdf_b64 = base64.b64encode(file_content).decode()
                file_url = f"data:application/pdf;base64,{pdf_b64}"
                storage_backend = "base64"
            else:
                raise HTTPException(
                    507,
                    "PDF too large to store without R2 or Supabase — "
                    "configure R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY",
                )
    else:
        text_content = file_content.decode("utf-8", errors="ignore")
        storage_backend = "inline"

    upload_data = {
        "id": content_id,
        "subject_id": subject_id,
        "content_type": content_type,
        "title": title or (file.filename or "").replace(f".{file_ext}", ""),
        "description": description,
        "tags": tags,
        "year": year,
        "file_name": file.filename,
        "file_ext": file_ext,
        "file_size": len(file_content),
        "file_url": file_url,
        "storage_backend": storage_backend,
        "content": text_content,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "uploaded_by": admin.get("email"),
        "status": "published",
    }
    
    await db.content_uploads.insert_one(upload_data)
    
    # Mark subject as having document
    await db.subjects.update_one(
        {"id": subject_id},
        {"$set": {"has_document": True, "document_type": file_ext}}
    )
    
    logger.info(f"Content uploaded: {file.filename} ({file_ext}) for subject {subject_id} via {storage_backend}")
    _purge_for_route("admin.content_upload", ["subjects", "chapters"], subject_id)
    _schedule_prerender_refresh(f"content_uploaded:{subject_id}")
    return {
        "id": content_id,
        "message": "Upload successful",
        "file_type": file_ext,
        "storage": storage_backend,
        "file_url": file_url if storage_backend != "base64" else None,
    }

@router.post("/admin/reset-and-seed-content")
async def reset_and_seed_content(admin: dict = Depends(get_admin_user)):
    """Delete all content and seed with 1000+ char dummy chapters"""
    # Delete all chapters
    await db.chapters.delete_many({})
    await db.content_uploads.delete_many({})
    
    # Get first subject to seed
    subjects = await db.subjects.find({"status": "published"}, {"_id": 0}).limit(3).to_list(3)
    
    if not subjects:
        raise HTTPException(status_code=404, detail="No subjects found - create subjects first")
    
    seeded_count = 0
    for subject in subjects:
        # Create 3 chapters with 1000+ char content
        chapters_data = [
            {
                "title": "Introduction and Basic Concepts",
                "content": f"""# Introduction to {subject.get('name', 'Subject')}

## Overview
This chapter covers fundamental concepts and provides a strong foundation for understanding {subject.get('name', 'this subject')}. We'll explore key definitions, important principles, and practical applications that are crucial for AssamBoard students.

## Key Concepts
Understanding the basics is essential. This subject involves:
- Theoretical foundations that build conceptual clarity
- Practical applications in real-world scenarios
- Problem-solving techniques for exam preparation
- Important formulas and their derivations
- Common mistakes to avoid during exams

## Fundamental Principles
The core principles include systematic study of:

1. **Definition and Scope**: Understanding what this field encompasses
2. **Historical Development**: How knowledge evolved over time
3. **Modern Applications**: Relevance in today's world
4. **Interdisciplinary Connections**: Links with other subjects

## Important Points for Exams
- Focus on conceptual clarity over rote learning
- Practice numerical problems regularly
- Understand derivations, don't just memorize
- Make concise notes for quick revision
- Solve previous year questions (PYQs)

## Study Tips
Allocate time systematically: 40% theory, 30% numericals, 30% revision.
Create mind maps for interconnected topics.
Practice explaining concepts to solidify understanding.

**Exam Tip**: Always read questions twice before answering. Time management is crucial in board exams.

**Character Count**: 1200+
"""
            },
            {
                "title": "Advanced Topics and Applications",
                "content": f"""# Advanced Topics in {subject.get('name', 'Subject')}

## Complex Concepts Explained
Building on fundamentals, we now explore advanced ideas that require deeper analytical thinking. These topics frequently appear in AHSEC board exams and competitive examinations.

## Theoretical Framework
Advanced study requires:
- Strong foundation in basics (revisit previous chapter if needed)
- Analytical reasoning and critical thinking skills
- Ability to connect multiple concepts simultaneously
- Mathematical proficiency for problem-solving
- Visualization of abstract concepts

## Key Advanced Topics

### Topic 1: Detailed Analysis
This involves understanding mechanisms, patterns, and underlying principles. Students must grasp:
- Cause and effect relationships
- Step-by-step processes
- Conditions and exceptions
- Practical implications

### Topic 2: Problem-Solving Strategies
Approach problems systematically:
1. Read and understand the question
2. Identify given data and what's asked
3. Choose appropriate formula/method
4. Solve step-by-step with units
5. Verify answer makes sense

### Topic 3: Applications
Real-world applications help remember concepts better. This topic has applications in:
- Industry and technology
- Environmental science
- Medical field
- Daily life phenomena

## Common Exam Questions
- Derivation-based questions (5 marks)
- Numerical problems (3 marks)
- Short answer questions (2 marks)
- Very short answers (1 mark)

## Preparation Strategy
- Solve at least 50 problems before exam
- Practice derivations until you can do them with eyes closed
- Make formula sheets for quick revision
- Group study helps clarify doubts

**Exam Tip**: In numericals, always write the formula first, then substitute values. This gets you partial marks even if the final answer is wrong.

**Character Count**: 1400+
"""
            },
            {
                "title": "Exam Preparation and Practice Questions",
                "content": f"""# Exam Preparation Guide - {subject.get('name', 'Subject')}

## Complete Revision Strategy
Last-minute preparation requires smart work, not just hard work. Follow this proven strategy used by AHSEC toppers.

## Week-wise Plan (4 Weeks Before Exam)

### Week 1: Concepts Revision
- Read all chapters once quickly
- Make short notes of important points
- List all formulas in one place
- Identify weak topics for extra focus

### Week 2: Problem Practice
- Solve 10 numericals daily
- Focus on previous year questions (PYQs)
- Time yourself while solving
- Review mistakes and redo wrong problems

### Week 3: Deep Dive Weak Areas
- Spend 70% time on difficult topics
- Watch video explanations if concepts unclear
- Discuss with teachers/peers
- Practice derivations thoroughly

### Week 4: Final Revision
- Revise notes daily
- Solve sample papers under exam conditions
- Don't start new topics
- Focus on high-weightage chapters

## Important Formulas
(This section would list 10-15 key formulas with explanations)

## Previous Year Questions (PYQs)

**2024 Question**: [Sample question text here]
**Answer**: Detailed step-by-step solution with explanation.

**2023 Question**: [Another sample question]
**Answer**: Complete solution with diagrams if needed.

**2022 Question**: [Third sample question]
**Answer**: Answer with exam tips included.

## Common Mistakes to Avoid
1. Not reading questions carefully
2. Skipping steps in derivations
3. Forgetting units in numerical answers
4. Poor time management
5. Leaving questions unattempted

## Exam Day Tips
- Reach 30 minutes early
- Read paper completely in first 15 minutes
- Start with questions you're most confident about
- Allocate time per question based on marks
- Reserve last 15 minutes for review

## Mark Distribution Strategy
- 1-mark questions: 30 seconds each
- 2-mark questions: 2 minutes each
- 3-mark questions: 4 minutes each
- 5-mark questions: 7-8 minutes each

**Final Tip**: Stay calm, attempt all questions, neat handwriting gets extra marks!

**Character Count**: 1600+
"""
            }
        ]
        
        for i, chapter_data in enumerate(chapters_data, 1):
            chapter_id = str(uuid.uuid4())
            chapter = {
                "id": chapter_id,
                "subject_id": subject["id"],
                "title": chapter_data["title"],
                "description": f"Chapter {i} - Essential concepts and exam preparation",
                "content": chapter_data["content"],
                "order": i,
                "status": "published",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            await db.chapters.insert_one(chapter)
            seeded_count += 1
        
        # Mark subject as having content
        await db.subjects.update_one(
            {"id": subject["id"]},
            {"$set": {"has_document": True, "chapter_count": 3}}
        )
    
    logger.info(f"Content reset and seeded: {seeded_count} chapters across {len(subjects)} subjects")
    # Task #795: this wiped every chapter + content_upload and rewrote
    # `has_document` / `chapter_count` on the seeded subjects. Fire a
    # full-content cache purge (CF zone purge_everything + worker
    # purge_all) so no edge POP is left holding the old bundle.
    try:
        from cloudflare_client import purge_all_content_cache
        await purge_all_content_cache()
    except Exception as e:
        logger.warning(f"admin.purge full content purge after reset-and-seed failed: {e}")
    _purge_for_route(
        "admin.reset_and_seed",
        ["chapters", "subjects"],
        f"{seeded_count} chapters / {len(subjects)} subjects",
    )
    # Bulk seed wipes & rewrites every chapter — fire the deploy hook
    # immediately rather than waiting for the coalesce window or the
    # nightly safety-net (Task #398).
    await _trigger_prerender_now(f"reset_and_seed:{seeded_count}_chapters")
    return {"message": f"Reset complete! Seeded {seeded_count} chapters with 1000+ chars each", "chapters": seeded_count}


@router.post("/admin/content/uploads/manual")
async def create_content_manual(data: dict, admin: dict = Depends(get_admin_user)):
    """Create content manually (not file upload)"""
    content_id = str(uuid.uuid4())
    
    content_data = {
        "id": content_id,
        "subject_id": data.get("subject_id"),
        "content_type": data.get("content_type", "chapter"),
        "title": data.get("title"),
        "description": data.get("description", ""),
        "content": data.get("content", ""),
        "tags": data.get("tags", ""),
        "year": data.get("year", ""),
        "exam_type": data.get("exam_type", ""),
        "category": data.get("category", ""),
        "order": data.get("order", 1),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by": admin.get("email"),
        "status": data.get("status", "published"),
    }
    
    await db.content_uploads.insert_one(content_data)
    content_data.pop("_id", None)
    # Task #795: `/api/content/subjects/{id}/document` falls back to
    # `db.content_uploads` and is edge-cached under the `/api/content/subjects`
    # prefix (worker uses startsWith). Purge so the new manual upload is
    # visible to students immediately.
    subject_id = data.get("subject_id") or ""
    _purge_for_route("admin.content_manual_create", ["subjects"], subject_id)
    return content_data

@router.get("/admin/content/uploads")
async def get_content_uploads(
    subject_id: str = None,
    type: str = None,
    admin: dict = Depends(get_admin_user)
):
    """Get uploaded content filtered by subject and type"""
    try:
        if not await is_mongo_available():
            return []
        query = {}
        if subject_id:
            query["subject_id"] = subject_id
        if type:
            query["content_type"] = type
        
        uploads = await db.content_uploads.find(query, {"_id": 0}).sort("uploaded_at", -1).limit(100).to_list(100)
        return uploads
    except Exception:
        mark_mongo_down()
        return []

@router.delete("/admin/content/uploads/{content_id}")
async def delete_content_upload(content_id: str, admin: dict = Depends(get_admin_user)):
    """Delete uploaded content"""
    # Look up subject_id BEFORE the delete so we can include it in the
    # audit log for traceability.
    upload = await db.content_uploads.find_one(
        {"id": content_id}, {"_id": 0, "subject_id": 1}
    )
    result = await db.content_uploads.delete_one({"id": content_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Content not found")
    # Task #795: matches `create_content_manual` — content_uploads feeds
    # the document fallback, which is edge-cached under
    # `/api/content/subjects`. Without this, deleted material can still
    # render to students for up to the cache TTL.
    subject_id = (upload or {}).get("subject_id") or ""
    _purge_for_route("admin.content_manual_delete", ["subjects"], subject_id)
    return {"message": "Content deleted"}


@router.patch("/admin/content/chapters/{chapter_id}")
async def admin_update_chapter(chapter_id: str, data: dict, admin: dict = Depends(get_admin_user)):
    allowed = {k: v for k, v in data.items() if k in ["title", "slug", "description", "content", "content_as", "content_type", "order", "status", "attached_files", "topics"]}
    if "slug" in allowed:
        allowed["slug"] = re.sub(r'[^a-z0-9]+', '-', (allowed["slug"] or "").lower()).strip('-')
    if "title" in allowed and not allowed.get("slug"):
        allowed["slug"] = re.sub(r'[^a-z0-9]+', '-', allowed["title"].lower()).strip('-')
    if allowed.get("slug"):
        chapter = await db.chapters.find_one({"id": chapter_id}, {"subject_id": 1})
        if chapter:
            dup = await db.chapters.find_one({"subject_id": chapter["subject_id"], "slug": allowed["slug"], "id": {"$ne": chapter_id}})
            if dup:
                allowed["slug"] = f"{allowed['slug']}-{chapter_id[:6]}"
    allowed["updated_at"] = datetime.now(timezone.utc).isoformat()
    
    content_updated = "content" in allowed and allowed["content"]
    topics_updated = "topics" in allowed
    title_updated = "title" in allowed
    
    result = await db.chapters.update_one({"id": chapter_id}, {"$set": allowed})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Chapter not found")
    # Task #383 — purge cross-pod KV mirror for this subject's chapter
    # index so renames/reorders/topic edits propagate immediately.
    try:
        _existing_chapter = await db.chapters.find_one({"id": chapter_id}, {"subject_id": 1})
        _subj_id = (_existing_chapter or {}).get("subject_id")
        if _subj_id:
            from routes.content import invalidate_chapters_kv
            await invalidate_chapters_kv(_subj_id)
    except Exception as _kv_exc:
        logger.debug(f"chapters KV invalidate (update) failed: {_kv_exc}")
    
    chunks_info = {}
    if content_updated:
        try:
            rechunk_result = await rechunk_chapter(chapter_id)
            chunks_info = {
                "chunks_deleted": rechunk_result["chunks_deleted"],
                "chunks_created": rechunk_result["chunks_created"]
            }
            logger.info(f"Re-chunked updated chapter {chapter_id}: {chunks_info}")
        except Exception as chunk_error:
            logger.error(f"Re-chunking failed for chapter {chapter_id}: {chunk_error}")
            chunks_info = {"error": str(chunk_error)}
    
    updated_ch = None
    if content_updated or topics_updated or title_updated:
        updated_ch = await db.chapters.find_one({"id": chapter_id}, {"_id": 0})
        if updated_ch:
            asyncio.create_task(_embed_chapter_bg(
                chapter_id,
                updated_ch.get("subject_id", ""),
                updated_ch.get("title", ""),
                updated_ch.get("description", ""),
                updated_ch.get("topics", []),
                updated_ch.get("content", ""),
            ))
    
    _invalidate_content_cache("chapters")
    _invalidate_content_cache("subjects")
    _schedule_d1_sync_fire("chapters", "subjects")
    if updated_ch is None:
        updated_ch = await db.chapters.find_one({"id": chapter_id}, {"_id": 0, "slug": 1, "subject_id": 1})
    if updated_ch:
        _schedule_indexnow_for_chapter(updated_ch)
    _schedule_prerender_refresh("chapter_updated")
    return {"message": "Chapter updated", **chunks_info}

