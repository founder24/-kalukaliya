"""
Staff content API — authenticated with regular user JWT (role=staff|admin).
Provides subject/chapter navigation and full chapter content + RAG editing.
"""
import asyncio
import mimetypes
from datetime import datetime, timezone
from typing import Optional

import httpx
from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel

from app.api.v1.auth import get_current_user
from app.config import settings
from app.models.content import Board, Chapter, Class, Stream, Subject
from app.models.user import User

import logging
logger = logging.getLogger(__name__)


# ── CF cache purge helper ─────────────────────────────────────────────────────

async def _purge_library_bundle_cache() -> None:
    """
    Fire-and-forget purge of the CF CDN cache for all library-bundle variants.
    Requires CF_ZONE_ID and CF_API_TOKEN in settings; silently skips if absent.
    """
    zone_id = getattr(settings, "CF_ZONE_ID", None)
    api_token = getattr(settings, "CF_API_TOKEN", None)
    if not zone_id or not api_token:
        return
    urls = [
        "https://api.syrabit.ai/api/v1/content/library-bundle",
        "https://api.syrabit.ai/api/v1/content/library-bundle?slim=1",
        "https://api.syrabit.ai/api/v1/public_content/library-bundle",
        "https://api.syrabit.ai/api/v1/public_content/library-bundle?slim=1",
        # Worker-proxied paths (no /v1/ prefix)
        "https://api.syrabit.ai/api/content/library-bundle",
        "https://api.syrabit.ai/api/content/library-bundle?slim=1",
    ]
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.post(
                f"https://api.cloudflare.com/client/v4/zones/{zone_id}/purge_cache",
                headers={
                    "Authorization": f"Bearer {api_token}",
                    "Content-Type": "application/json",
                },
                json={"files": urls},
            )
            if resp.status_code == 200:
                logger.info("staff_content: CF library-bundle cache purged")
            else:
                logger.warning(
                    "staff_content: CF cache purge returned %s — %s",
                    resp.status_code, resp.text[:200],
                )
    except Exception as exc:
        logger.warning("staff_content: CF cache purge failed: %s", exc)

router = APIRouter(prefix="/staff", tags=["Staff"])


# ── Auth ──────────────────────────────────────────────────────────────────────

async def require_staff_user(user: User = Depends(get_current_user)) -> User:
    """Require the logged-in user to have role='staff' or role='admin'."""
    if getattr(user, "role", None) not in ("staff", "admin"):
        raise HTTPException(status_code=403, detail="Staff access required")
    return user


# ── Boards ────────────────────────────────────────────────────────────────────

@router.get("/content/boards")
async def staff_list_boards(
    request: Request,
    _staff: User = Depends(require_staff_user),
):
    boards = await Board.find_all().to_list(length=500)
    return [
        {"id": str(b.id), "name": b.name, "slug": b.slug, "status": b.status}
        for b in boards
    ]


# ── Classes ───────────────────────────────────────────────────────────────────

@router.get("/content/classes")
async def staff_list_classes(
    request: Request,
    _staff: User = Depends(require_staff_user),
):
    classes = await Class.find_all().to_list(length=500)
    return [
        {"id": str(c.id), "name": c.name, "board_id": str(c.board_id), "status": c.status}
        for c in classes
    ]


# ── Streams (Courses) ─────────────────────────────────────────────────────────

@router.get("/content/streams")
async def staff_list_streams(
    request: Request,
    _staff: User = Depends(require_staff_user),
):
    """
    Return all streams (courses) with their class_id and resolved board_id.
    Board → Class → Stream is the full ancestry needed for cascaded filtering.
    """
    streams = await Stream.find_all().to_list(length=2000)
    classes = await Class.find_all().to_list(length=500)
    classes_map = {str(c.id): c for c in classes}

    result = []
    for s in streams:
        cls = classes_map.get(str(s.class_id)) if s.class_id else None
        result.append({
            "id":       str(s.id),
            "name":     s.name,
            "status":   s.status,
            "class_id": str(s.class_id) if s.class_id else None,
            "board_id": str(cls.board_id) if cls else None,
        })
    return result


# ── Subjects ──────────────────────────────────────────────────────────────────

@router.get("/content/subjects")
async def staff_list_subjects(
    request: Request,
    _staff: User = Depends(require_staff_user),
):
    """
    Return all subjects with resolved board_id and class_id so the frontend
    can filter by board/class without extra round-trips.
    Board → Class → Stream → Subject — we load all at once and map in Python.
    """
    subjects = await Subject.find_all().to_list(length=2000)
    streams   = await Stream.find_all().to_list(length=2000)
    classes   = await Class.find_all().to_list(length=1000)

    # Build lookup maps keyed by string ID so FlexId matches either form
    streams_map = {str(s.id): s for s in streams}
    classes_map = {str(c.id): c for c in classes}

    result = []
    for s in subjects:
        stream = streams_map.get(str(s.stream_id)) if s.stream_id else None
        cls    = classes_map.get(str(stream.class_id)) if (stream and stream.class_id) else None
        result.append({
            "id":          str(s.id),
            "name":        s.name,
            "status":      s.status,
            "stream_id":   str(s.stream_id)   if s.stream_id else None,
            "stream_name": stream.name         if stream      else None,
            "class_id":    str(cls.id)         if cls         else None,
            "board_id":    str(cls.board_id)   if cls         else None,
        })
    return result


# ── Chapters ──────────────────────────────────────────────────────────────────

@router.get("/content/chapters/{subject_id}")
async def staff_list_chapters(
    request: Request,
    subject_id: str,
    _staff: User = Depends(require_staff_user),
):
    # subject_id arrives as a plain hex string; MongoDB stores it as BSON ObjectId.
    # Try ObjectId first (the common case), fall back to raw string for legacy IDs.
    try:
        subject_oid = PydanticObjectId(subject_id)
        query_val: object = subject_oid
    except Exception:
        query_val = subject_id

    chapters = (
        await Chapter.find({"subject_id": query_val})
        .sort("chapter_number")
        .to_list(length=500)
    )

    # If the ObjectId query returned nothing, retry with the raw string
    # (covers chapters created with legacy short IDs like 's13')
    if not chapters and query_val != subject_id:
        chapters = (
            await Chapter.find({"subject_id": subject_id})
            .sort("chapter_number")
            .to_list(length=500)
        )
    return [
        {
            "id":              str(ch.id),
            "title":           ch.title,
            "title_as":        ch.title_as,
            "status":          ch.status,
            "content_type":    ch.content_type,
            "chapter_number":  ch.chapter_number,
            "has_content_en":  bool(ch.content_en),
            "has_content_as":  bool(ch.content_as),
            "has_notes_en":    bool(ch.notes_en),
            "has_qa_en":       bool(ch.qa_text_en),
            "has_qa_as":       bool(ch.qa_text_as),
            "has_rag_en":      bool(ch.rag_text_en),
            "has_rag_as":      bool(ch.rag_text_as),
            "word_count":      ch.word_count,
            "content_saved_at": ch.content_saved_at.isoformat() if ch.content_saved_at else None,
            "rag_updated_at":   ch.rag_updated_at.isoformat()   if ch.rag_updated_at   else None,
            "rag_indexed_at":   ch.rag_indexed_at.isoformat()   if ch.rag_indexed_at   else None,
            "published_at":     ch.published_at.isoformat()     if ch.published_at     else None,
        }
        for ch in chapters
    ]


@router.get("/content/chapter/{chapter_id}")
async def staff_get_chapter(
    request: Request,
    chapter_id: str,
    _staff: User = Depends(require_staff_user),
):
    """Load the full content of a single chapter for editing."""
    try:
        chapter = await Chapter.get(PydanticObjectId(chapter_id))
    except Exception:
        chapter = None
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    def _is_stale(updated_at, indexed_at):
        return bool(updated_at and (not indexed_at or updated_at > indexed_at))

    rag_stale        = _is_stale(chapter.rag_updated_at,       chapter.rag_indexed_at)
    notes_rag_stale  = _is_stale(chapter.notes_rag_updated_at, chapter.notes_rag_indexed_at)
    qa_rag_stale     = _is_stale(chapter.qa_rag_updated_at,    chapter.qa_rag_indexed_at)
    pyq_rag_stale    = _is_stale(chapter.pyq_rag_updated_at,   chapter.pyq_rag_indexed_at)

    def _ts(dt): return dt.isoformat() if dt else None

    return {
        "id":              str(chapter.id),
        "title":           chapter.title,
        "title_as":        chapter.title_as,
        "slug":            chapter.slug or "",
        "status":          chapter.status,
        "content_type":    chapter.content_type,
        "chapter_number":  chapter.chapter_number,
        "meta_description": chapter.meta_description,
        "keywords":         chapter.keywords,
        "notes_generated":  chapter.notes_generated,
        "pyq_pdf_url":      chapter.pyq_pdf_url or "",
        # Content fields
        "content_en":       chapter.content_en      or "",
        "content_as":       chapter.content_as      or "",
        "notes_en":         chapter.notes_en        or "",
        "notes_as":         chapter.notes_as        or "",
        "qa_text_en":       chapter.qa_text_en      or "",
        "qa_text_as":       chapter.qa_text_as      or "",
        # RAG blob fields (legacy / fallback)
        "rag_text_en":      chapter.rag_text_en     or "",
        "rag_text_as":      chapter.rag_text_as     or "",
        "qa_rag_text_en":   chapter.qa_rag_text_en  or "",
        "qa_rag_text_as":   chapter.qa_rag_text_as  or "",
        "pyq_rag_text":     chapter.pyq_rag_text    or "",
        "pyq_rag_text_as":  chapter.pyq_rag_text_as or "",
        # Structured RAG section fields
        "rag_sections_en":    chapter.rag_sections_en    or [],
        "rag_sections_as":    chapter.rag_sections_as    or [],
        "qa_rag_sections_en": chapter.qa_rag_sections_en or [],
        "qa_rag_sections_as": chapter.qa_rag_sections_as or [],
        # Timestamps + RAG sync status
        "content_saved_at":      _ts(chapter.content_saved_at),
        "rag_updated_at":        _ts(chapter.rag_updated_at),
        "rag_indexed_at":        _ts(chapter.rag_indexed_at),
        "rag_stale":             rag_stale,
        "notes_rag_updated_at":  _ts(chapter.notes_rag_updated_at),
        "notes_rag_indexed_at":  _ts(chapter.notes_rag_indexed_at),
        "notes_rag_stale":       notes_rag_stale,
        "qa_rag_updated_at":     _ts(chapter.qa_rag_updated_at),
        "qa_rag_indexed_at":     _ts(chapter.qa_rag_indexed_at),
        "qa_rag_stale":          qa_rag_stale,
        "pyq_rag_updated_at":    _ts(chapter.pyq_rag_updated_at),
        "pyq_rag_indexed_at":    _ts(chapter.pyq_rag_indexed_at),
        "pyq_rag_stale":         pyq_rag_stale,
        "published_at":          _ts(chapter.published_at),
        "updated_at":            _ts(chapter.updated_at),
        "word_count":            chapter.word_count,
    }


class ChapterEditBody(BaseModel):
    title:            Optional[str] = None
    title_as:         Optional[str] = None
    slug:             Optional[str] = None
    chapter_number:   Optional[int] = None
    status:           Optional[str] = None
    content_type:     Optional[str] = None
    meta_description: Optional[str] = None
    keywords:         Optional[str] = None
    # Content
    content_en:       Optional[str] = None
    content_as:       Optional[str] = None
    notes_en:         Optional[str] = None
    notes_as:         Optional[str] = None
    qa_text_en:       Optional[str] = None
    qa_text_as:       Optional[str] = None
    # RAG blob fields (legacy / fallback)
    rag_text_en:      Optional[str] = None
    rag_text_as:      Optional[str] = None
    qa_rag_text_en:   Optional[str] = None
    qa_rag_text_as:   Optional[str] = None
    pyq_rag_text:     Optional[str] = None      # PYQ RAG, English
    pyq_rag_text_as:  Optional[str] = None      # PYQ RAG, Assamese
    # Structured RAG section fields
    rag_sections_en:    Optional[list[dict]] = None
    rag_sections_as:    Optional[list[dict]] = None
    qa_rag_sections_en: Optional[list[dict]] = None
    qa_rag_sections_as: Optional[list[dict]] = None


_CONTENT_FIELDS = frozenset({
    "content_en", "content_as", "notes_en", "notes_as",
    "qa_text_en", "qa_text_as",
})
_RAG_FIELDS = frozenset({
    "rag_text_en", "rag_text_as", "qa_rag_text_en", "qa_rag_text_as",
    "pyq_rag_text", "pyq_rag_text_as",
})
# These blob fields are fallbacks for the Notes RAG layer — edits must also stamp
# notes_rag_updated_at so the per-section stale indicator fires correctly.
_NOTES_RAG_BLOB_FIELDS = frozenset({"rag_text_en", "rag_text_as"})
# Same for Q&A RAG blob fallbacks.
_QA_RAG_BLOB_FIELDS    = frozenset({"qa_rag_text_en", "qa_rag_text_as"})
# PYQ RAG blob fields — both EN and AS stamp pyq_rag_updated_at.
_PYQ_RAG_BLOB_FIELDS   = frozenset({"pyq_rag_text", "pyq_rag_text_as"})
_NOTES_RAG_SECTION_FIELDS = frozenset({"rag_sections_en", "rag_sections_as"})
_QA_RAG_SECTION_FIELDS    = frozenset({"qa_rag_sections_en", "qa_rag_sections_as"})


@router.patch("/content/chapter/{chapter_id}")
async def staff_update_chapter(
    request: Request,
    chapter_id: str,
    body: ChapterEditBody,
    _staff: User = Depends(require_staff_user),
):
    try:
        chapter = await Chapter.get(PydanticObjectId(chapter_id))
    except Exception:
        chapter = None
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    now = datetime.now(timezone.utc)
    changed = content_changed = rag_changed = False

    scalar_fields = (
        "title", "title_as", "slug", "chapter_number",
        "status", "content_type", "meta_description", "keywords",
        "content_en", "content_as", "notes_en", "notes_as",
        "qa_text_en", "qa_text_as",
        "rag_text_en", "rag_text_as", "qa_rag_text_en", "qa_rag_text_as",
        "pyq_rag_text", "pyq_rag_text_as",
    )
    notes_sections_changed = qa_sections_changed = pyq_rag_changed = False
    notes_blob_changed = qa_blob_changed = False

    for field in scalar_fields:
        val = getattr(body, field, None)
        if val is not None:
            setattr(chapter, field, val)
            changed = True
            if field in _CONTENT_FIELDS:
                content_changed = True
            elif field in _RAG_FIELDS:
                rag_changed = True
                if field in _PYQ_RAG_BLOB_FIELDS:
                    pyq_rag_changed = True
                # Fallback blob edits must also stamp per-scope stale timestamps
                # so the Notes / Q&A RAG sub-tab stale indicators fire correctly.
                if field in _NOTES_RAG_BLOB_FIELDS:
                    notes_blob_changed = True
                if field in _QA_RAG_BLOB_FIELDS:
                    qa_blob_changed = True

    # List fields — only update when explicitly provided (not None)
    for field in ("rag_sections_en", "rag_sections_as"):
        val = getattr(body, field, None)
        if val is not None:
            setattr(chapter, field, val)
            changed = True
            notes_sections_changed = True
    for field in ("qa_rag_sections_en", "qa_rag_sections_as"):
        val = getattr(body, field, None)
        if val is not None:
            setattr(chapter, field, val)
            changed = True
            qa_sections_changed = True

    if not changed:
        return {"ok": True, "message": "No changes"}

    if content_changed:
        chapter.content_saved_at = now
        # Recompute word_count so library page chapter cards stay in sync
        content_en = chapter.content_en or ""
        chapter.word_count = len(content_en.split()) if content_en.strip() else 0
        # notes_generated tracks whether structured notes exist
        chapter.notes_generated = bool(chapter.notes_en and chapter.notes_en.strip())
    if rag_changed:
        chapter.rag_updated_at = now
    # Per-scope stale tracking: blob fallbacks and structured sections both stamp
    # their respective per-scope timestamp so the sub-tab stale indicator fires.
    if notes_sections_changed or notes_blob_changed:
        chapter.notes_rag_updated_at = now
    if qa_sections_changed or qa_blob_changed:
        chapter.qa_rag_updated_at = now
    if pyq_rag_changed:
        chapter.pyq_rag_updated_at = now
    chapter.updated_at = now
    await chapter.save()

    # Bust CDN cache so library page reflects the change immediately
    asyncio.create_task(_purge_library_bundle_cache())

    return {"ok": True}


# ── RAG reindex ───────────────────────────────────────────────────────────────

@router.post("/content/chapter/{chapter_id}/reindex")
async def staff_reindex_chapter(
    request: Request,
    chapter_id: str,
    scope: str = Query(default="notes", description="notes | qa | pyq | all"),
    _staff: User = Depends(require_staff_user),
):
    """
    Trigger a Vectorize RAG reindex for a chapter.

    scope=notes  — reindex Notes sections (rag_sections_en/as, fallback rag_text_en/as)
    scope=qa     — reindex Q&A sections (qa_rag_sections_en/as, fallback qa_rag_text_en/as)
    scope=pyq    — reindex PYQ text (pyq_rag_text as a single question_paper chunk)
    scope=all    — reindex all three scopes sequentially

    Runs in a background task; returns immediately.
    """
    if scope not in ("notes", "qa", "pyq", "all"):
        raise HTTPException(status_code=400, detail="scope must be notes | qa | pyq | all")

    try:
        chapter = await Chapter.get(PydanticObjectId(chapter_id))
    except Exception:
        chapter = None
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    # Pre-flight: check there is something to index for the requested scope(s)
    scopes_to_run = ["notes", "qa", "pyq"] if scope == "all" else [scope]
    runnable = []
    for s in scopes_to_run:
        if s == "notes":
            has = bool(chapter.rag_sections_en or chapter.rag_sections_as or chapter.rag_text_en or chapter.rag_text_as)
        elif s == "qa":
            has = bool(chapter.qa_rag_sections_en or chapter.qa_rag_sections_as or chapter.qa_rag_text_en or chapter.qa_rag_text_as)
        else:  # pyq
            has = bool(chapter.pyq_rag_text or chapter.pyq_rag_text_as)
        if has:
            runnable.append(s)

    if not runnable:
        raise HTTPException(
            status_code=422,
            detail=f"No RAG content to index for scope '{scope}'. Add content first."
        )

    async def _do_reindex(ch_id: str, ch_scopes: list[str]):
        try:
            from app.services.rag.ingestion_v2 import ingest_chapter_v2
            fresh = await Chapter.get(PydanticObjectId(ch_id))
            if not fresh:
                return
            meta = {"subject_id": str(fresh.subject_id)}
            now = datetime.now(timezone.utc)

            for s in ch_scopes:
                if s == "notes":
                    await ingest_chapter_v2(
                        chapter_id=ch_id,
                        content_en=fresh.rag_text_en or None,
                        content_as=fresh.rag_text_as or None,
                        metadata=meta,
                        source_type="notes",
                        sections_en=fresh.rag_sections_en or None,
                        sections_as=fresh.rag_sections_as or None,
                        section_chunk_type="topic_section",
                    )
                    fresh2 = await Chapter.get(PydanticObjectId(ch_id))
                    if fresh2:
                        fresh2.notes_rag_indexed_at = now
                        fresh2.rag_indexed_at = now  # keep legacy in sync
                        await fresh2.save()
                elif s == "qa":
                    await ingest_chapter_v2(
                        chapter_id=ch_id,
                        content_en=fresh.qa_rag_text_en or None,
                        content_as=fresh.qa_rag_text_as or None,
                        metadata=meta,
                        source_type="important_questions",
                        sections_en=fresh.qa_rag_sections_en or None,
                        sections_as=fresh.qa_rag_sections_as or None,
                        section_chunk_type="qa_pair",
                    )
                    fresh2 = await Chapter.get(PydanticObjectId(ch_id))
                    if fresh2:
                        fresh2.qa_rag_indexed_at = now
                        await fresh2.save()
                else:  # pyq
                    await ingest_chapter_v2(
                        chapter_id=ch_id,
                        content_en=fresh.pyq_rag_text    or None,
                        content_as=fresh.pyq_rag_text_as or None,
                        metadata=meta,
                        source_type="pyq",
                    )
                    fresh2 = await Chapter.get(PydanticObjectId(ch_id))
                    if fresh2:
                        fresh2.pyq_rag_indexed_at = now
                        await fresh2.save()

            logger.info("staff_content: reindex complete scope=%s chapter=%s", ch_scopes, ch_id)
        except Exception as exc:
            logger.error("staff_content: reindex failed scope=%s chapter=%s: %s", ch_scopes, ch_id, exc)

    asyncio.create_task(_do_reindex(chapter_id, runnable))

    return {
        "ok": True,
        "message": f"RAG reindex started for scope(s): {', '.join(runnable)}",
        "scopes": runnable,
    }


# ── R2 PYQ file upload ────────────────────────────────────────────────────────

_ALLOWED_PYQ_CONTENT_TYPES = {
    "application/pdf", "image/jpeg", "image/png",
    "image/webp", "image/gif", "image/tiff",
}

async def _upload_to_r2(data: bytes, key: str, content_type: str) -> str:
    """
    Upload bytes to Cloudflare R2 via the CF REST API.
    Returns the public URL for the object.
    Raises HTTPException on failure.
    """
    account_id  = getattr(settings, "CF_ACCOUNT_ID",      None) or getattr(settings, "CLOUDFLARE_ACCOUNT_ID", None)
    api_token   = getattr(settings, "CF_API_TOKEN",        None)
    bucket      = getattr(settings, "CF_R2_BUCKET",        "syrabit-assets")
    public_url  = getattr(settings, "CF_R2_PUBLIC_URL",    None)

    if not account_id or not api_token:
        raise HTTPException(
            status_code=503,
            detail="R2 upload unavailable — CF_ACCOUNT_ID or CF_API_TOKEN not configured"
        )

    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/r2/buckets/{bucket}/objects/{key}"
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.put(
                url,
                content=data,
                headers={
                    "Authorization": f"Bearer {api_token}",
                    "Content-Type": content_type,
                },
            )
        if resp.status_code not in (200, 201):
            logger.error("R2 upload failed: %s %s", resp.status_code, resp.text[:300])
            raise HTTPException(status_code=502, detail=f"R2 upload failed: HTTP {resp.status_code}")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"R2 upload error: {exc}")

    if public_url:
        return f"{public_url.rstrip('/')}/{key}"
    # Fallback: use CF API download URL
    return f"https://api.cloudflare.com/client/v4/accounts/{account_id}/r2/buckets/{bucket}/objects/{key}"


@router.post("/content/chapter/{chapter_id}/upload-pyq")
async def staff_upload_pyq(
    request: Request,
    chapter_id: str,
    file: UploadFile = File(...),
    _staff: User = Depends(require_staff_user),
):
    """
    Upload a PYQ PDF or image to Cloudflare R2, store the public URL as
    pyq_pdf_url on the chapter, and return it for immediate inline preview.
    """
    try:
        chapter = await Chapter.get(PydanticObjectId(chapter_id))
    except Exception:
        chapter = None
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    data = await file.read()
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (max 25 MB)")

    filename  = file.filename or "upload"
    ext       = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    ct        = file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"

    if ct not in _ALLOWED_PYQ_CONTENT_TYPES:
        # Be lenient: check extension too
        ext_ct_map = {"pdf": "application/pdf", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                      "png": "image/png", "webp": "image/webp", "gif": "image/gif", "tiff": "image/tiff"}
        ct = ext_ct_map.get(ext, ct)
        if ct not in _ALLOWED_PYQ_CONTENT_TYPES:
            raise HTTPException(status_code=400, detail="Only PDF and image files are allowed for PYQ upload")

    key        = f"pyq/{chapter_id}/{filename}"
    public_url = await _upload_to_r2(data, key, ct)

    chapter.pyq_pdf_url = public_url
    chapter.updated_at  = datetime.now(timezone.utc)
    await chapter.save()

    asyncio.create_task(_purge_library_bundle_cache())

    return {"ok": True, "pyq_pdf_url": public_url, "key": key}


# ── File attach (RAG) ─────────────────────────────────────────────────────────

_ALLOWED_RAG_FIELDS = {"rag_text_en", "rag_text_as", "qa_rag_text_en", "qa_rag_text_as", "pyq_rag_text", "pyq_rag_text_as"}


@router.post("/content/chapter/{chapter_id}/attach-file")
async def staff_attach_file(
    request: Request,
    chapter_id: str,
    field: str = Query("rag_text_en"),
    file: UploadFile = File(...),
    _staff: User = Depends(require_staff_user),
):
    """Extract text from PDF/TXT/MD and append to a chapter RAG field."""
    if field not in _ALLOWED_RAG_FIELDS:
        raise HTTPException(status_code=400, detail=f"Invalid field. Choose from: {', '.join(_ALLOWED_RAG_FIELDS)}")

    try:
        chapter = await Chapter.get(PydanticObjectId(chapter_id))
    except Exception:
        chapter = None
    if not chapter:
        raise HTTPException(status_code=404, detail="Chapter not found")

    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (max 10 MB)")

    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    text = ""
    if ext in ("txt", "md"):
        text = data.decode("utf-8", errors="replace")
    elif ext == "pdf":
        try:
            import pypdf
            from io import BytesIO
            reader = pypdf.PdfReader(BytesIO(data))
            text = "\n\n".join(p.extract_text() or "" for p in reader.pages)
        except ImportError:
            raise HTTPException(status_code=500, detail="pypdf not installed")
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"PDF extraction failed: {exc}")
    else:
        raise HTTPException(status_code=400, detail="Only pdf, txt, md files are supported")

    text = text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="No text could be extracted from the file")

    existing = getattr(chapter, field, None) or ""
    fingerprint = text[:200].strip()
    if fingerprint and fingerprint in existing:
        return {"text_extracted": len(text), "field": field, "skipped": True, "reason": "duplicate_content"}

    setattr(chapter, field, (existing + "\n\n" + text).strip() if existing else text)
    chapter.rag_updated_at = datetime.now(timezone.utc)
    chapter.updated_at = datetime.now(timezone.utc)
    await chapter.save()
    return {"text_extracted": len(text), "field": field, "skipped": False}


# ── Change password ───────────────────────────────────────────────────────────

class ChangePasswordBody(BaseModel):
    current_password: str
    new_password: str


@router.post("/auth/change-password")
async def staff_change_password(
    request: Request,
    body: ChangePasswordBody,
    staff: User = Depends(require_staff_user),
):
    if len(body.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    if not getattr(staff, "hashed_password", None):
        raise HTTPException(status_code=400, detail="No password set for this account")

    if not staff.verify_password(body.current_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    import bcrypt as _bcrypt

    def _bcrypt_safe(pw: str) -> bytes:
        raw = pw.encode("utf-8")
        import hashlib
        return hashlib.sha256(raw).digest() if len(raw) > 72 else raw

    new_hash = _bcrypt.hashpw(_bcrypt_safe(body.new_password), _bcrypt.gensalt()).decode()
    staff.hashed_password = new_hash
    staff.updated_at = datetime.now(timezone.utc)
    await staff.save()
    return {"ok": True, "message": "Password updated"}
