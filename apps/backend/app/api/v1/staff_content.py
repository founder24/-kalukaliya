"""
Staff content API — authenticated with regular user JWT (role=staff|admin).
Provides subject/chapter navigation and full chapter content + RAG editing.
"""
import asyncio
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
            "has_rag_en":      bool(ch.rag_text_en),
            "has_rag_as":      bool(ch.rag_text_as),
            "has_notes_en":    bool(ch.notes_en),
            "has_qa_en":       bool(ch.qa_text_en),
            "word_count":      ch.word_count,
            "content_saved_at": ch.content_saved_at.isoformat() if ch.content_saved_at else None,
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
    return {
        "id":              str(chapter.id),
        "title":           chapter.title,
        "title_as":        chapter.title_as,
        "status":          chapter.status,
        "content_type":    chapter.content_type,
        "chapter_number":  chapter.chapter_number,
        "meta_description": chapter.meta_description,
        "keywords":         chapter.keywords,
        # Content fields
        "content_en":       chapter.content_en      or "",
        "content_as":       chapter.content_as      or "",
        "notes_en":         chapter.notes_en        or "",
        "notes_as":         chapter.notes_as        or "",
        "qa_text_en":       chapter.qa_text_en      or "",
        "qa_text_as":       chapter.qa_text_as      or "",
        # RAG fields
        "rag_text_en":      chapter.rag_text_en     or "",
        "rag_text_as":      chapter.rag_text_as     or "",
        "qa_rag_text_en":   chapter.qa_rag_text_en  or "",
        "qa_rag_text_as":   chapter.qa_rag_text_as  or "",
        "pyq_rag_text":     chapter.pyq_rag_text    or "",
        # Timestamps
        "content_saved_at": chapter.content_saved_at.isoformat() if chapter.content_saved_at else None,
        "rag_updated_at":   chapter.rag_updated_at.isoformat()   if chapter.rag_updated_at   else None,
        "published_at":     chapter.published_at.isoformat()     if chapter.published_at     else None,
        "updated_at":       chapter.updated_at.isoformat()       if chapter.updated_at       else None,
    }


class ChapterEditBody(BaseModel):
    title:            Optional[str] = None
    title_as:         Optional[str] = None
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
    # RAG
    rag_text_en:      Optional[str] = None
    rag_text_as:      Optional[str] = None
    qa_rag_text_en:   Optional[str] = None
    qa_rag_text_as:   Optional[str] = None
    pyq_rag_text:     Optional[str] = None


_CONTENT_FIELDS = frozenset({
    "content_en", "content_as", "notes_en", "notes_as",
    "qa_text_en", "qa_text_as",
})
_RAG_FIELDS = frozenset({
    "rag_text_en", "rag_text_as", "qa_rag_text_en", "qa_rag_text_as", "pyq_rag_text",
})


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

    all_fields = (
        "title", "title_as", "status", "content_type",
        "meta_description", "keywords",
        "content_en", "content_as", "notes_en", "notes_as",
        "qa_text_en", "qa_text_as",
        "rag_text_en", "rag_text_as", "qa_rag_text_en", "qa_rag_text_as", "pyq_rag_text",
    )
    for field in all_fields:
        val = getattr(body, field, None)
        if val is not None:
            setattr(chapter, field, val)
            changed = True
            if field in _CONTENT_FIELDS:
                content_changed = True
            elif field in _RAG_FIELDS:
                rag_changed = True

    if not changed:
        return {"ok": True, "message": "No changes"}

    if content_changed:
        chapter.content_saved_at = now
        # Recompute word_count so library page chapter cards stay in sync
        content_en = chapter.content_en or ""
        chapter.word_count = len(content_en.split()) if content_en.strip() else 0
    if rag_changed:
        chapter.rag_updated_at = now
    chapter.updated_at = now
    await chapter.save()

    # Bust CDN cache so library page reflects the change immediately
    asyncio.create_task(_purge_library_bundle_cache())

    return {"ok": True}


# ── File attach (RAG) ─────────────────────────────────────────────────────────

_ALLOWED_RAG_FIELDS = {"rag_text_en", "rag_text_as", "qa_rag_text_en", "qa_rag_text_as", "pyq_rag_text"}


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
