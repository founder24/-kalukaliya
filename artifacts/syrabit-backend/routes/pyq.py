"""Syrabit.ai — PYQ upload, processing, and serving"""
import re, json, asyncio, uuid, logging, base64, httpx
from typing import List
from datetime import datetime, timezone
from fastapi import (
    APIRouter, HTTPException, Depends, File, UploadFile, Form,
)
from pydantic import BaseModel

from config import Configurator
from deps import (
    db,
    supa,
)
from auth_deps import (
    get_admin_user,
)
from db_ops import _THREAD_POOL
from utils import _extract_keywords
import vertex_services

logger = logging.getLogger(__name__)

router = APIRouter()

# ══════════════════════════════════════════════════════════════════════════════
#  PYQ — Previous Year Questions Upload & Management
# ══════════════════════════════════════════════════════════════════════════════

def _normalize_iso_for_sort(value):
    """Normalize an ISO-8601 timestamp string for stable cross-format
    comparison.

    Task #738 made every new write timezone-aware (suffix ``+00:00``),
    but historical PYQ rows on disk are still naive (no suffix).
    MongoDB sorts strings lexicographically, so a mixed result set can
    have rows from the same wall-clock instant appear in surprising
    order. This helper coerces both shapes to a canonical aware form
    so a Python-side ``sorted(..., key=...)`` re-sort is correct
    regardless of which dialect is on disk.

    Empty / non-string values sort to the start (so descending sort
    pushes them to the end).
    """
    if not isinstance(value, str) or not value:
        return ""
    if value.endswith("Z"):
        return value[:-1] + "+00:00"
    if len(value) >= 6 and value[-6] in "+-" and value[-3] == ":":
        return value
    return value + "+00:00"


def _get_db():
    return db

_PYQ_BUCKET   = "study-materials"
_PYQ_PREFIX   = "pyqs"
_PYQ_MAX_MB   = 50  # MB per file — stored in Supabase, not MongoDB


async def _upsert_pyq_html_page(db_handle, slug: str, page_doc: dict) -> None:
    """Upsert a `pyq_html_pages` row with guaranteed publish-date stamps.

    Centralises every write into the collection so future call sites cannot
    silently regress the legacy "missing created_at" bug fixed in Task #341.

    - `created_at` is written via `$setOnInsert` so it is stamped exactly once
      (on the original insert) and never overwritten on later upserts.
    - `updated_at` is always refreshed via `$set`.
    - If the caller already supplied either timestamp in `page_doc`, we honor
      it (useful for backfills / tests) but still ensure both fields exist.
    """
    if db_handle is None:
        return

    now = datetime.now(timezone.utc).isoformat()
    doc = dict(page_doc)
    created_at = doc.pop("created_at", None) or now
    updated_at = doc.pop("updated_at", None) or now
    doc["slug"] = slug
    doc["updated_at"] = updated_at

    await db_handle.pyq_html_pages.update_one(
        {"slug": slug},
        {
            "$set": doc,
            "$setOnInsert": {"created_at": created_at},
        },
        upsert=True,
    )


def _pyq_supabase_upload(raw: bytes, storage_path: str, mime: str) -> str:
    """Upload bytes to Supabase storage and return the public URL.
    Raises on failure so caller can fall back to base64."""
    supa.storage.from_(_PYQ_BUCKET).upload(
        path=storage_path,
        file=raw,
        file_options={"content-type": mime, "upsert": "true"},
    )
    return supa.storage.from_(_PYQ_BUCKET).get_public_url(storage_path)


@router.post("/api/admin/pyq/upload")
async def admin_pyq_upload(
    files: List[UploadFile] = File(...),
    paper_type:  str = Form("major"),
    exam_year:   int = Form(...),
    exam_title:  str = Form(""),
    board_id:    str = Form(""),
    class_id:    str = Form(""),
    stream_id:   str = Form(""),
    subject_id:  str = Form(""),
    chapter_id:  str = Form(""),
    admin: dict = Depends(get_admin_user),
):
    """Upload PYQ files to Supabase Storage; store only metadata + URL in MongoDB."""
    if not files:
        raise HTTPException(400, "No files provided")

    max_bytes = _PYQ_MAX_MB * 1024 * 1024

    # Resolve display names from MongoDB
    subject_name = board_name = class_name = stream_name = ""
    _db = _get_db()
    try:
        if subject_id:
            s = await _db["subjects"].find_one({"id": subject_id}) or await _db["subjects"].find_one({"_id": subject_id})
            subject_name = (s or {}).get("name") or (s or {}).get("title") or ""
        if board_id:
            b = await _db["boards"].find_one({"id": board_id}) or await _db["boards"].find_one({"_id": board_id})
            board_name = (b or {}).get("name") or ""
        if class_id:
            c = await _db["classes"].find_one({"id": class_id}) or await _db["classes"].find_one({"_id": class_id})
            class_name = (c or {}).get("name") or ""
        if stream_id:
            st = await _db["streams"].find_one({"id": stream_id}) or await _db["streams"].find_one({"_id": stream_id})
            stream_name = (st or {}).get("name") or ""
    except Exception:
        pass

    saved_ids = []
    use_supabase = bool(supa)

    for upload in files:
        raw = await upload.read()
        if len(raw) > max_bytes:
            raise HTTPException(413, f"{upload.filename} exceeds {_PYQ_MAX_MB} MB limit")

        mime      = upload.content_type or "application/octet-stream"
        is_image  = mime.startswith("image/")
        is_pdf    = mime == "application/pdf" or (upload.filename or "").lower().endswith(".pdf")
        doc_id    = str(uuid.uuid4())
        safe_name = (upload.filename or "file").replace(" ", "_")

        # ── Try Supabase storage first ────────────────────────────────────────
        file_url: str = ""
        storage_path  = f"{_PYQ_PREFIX}/{doc_id}/{safe_name}"

        # Task #337 — Rekognition pre-upload moderation gate (runbook §3.5).
        # Flagged images are written to the moderation_queue collection
        # (status=pending_review) instead of returned to the user-visible
        # surface; the admin AdminModerationQueuePanel approves or rejects.
        # Caller surface receives 202 + queue_id so the React upload modal
        # can render an "under review" pill instead of a hard 4xx.
        if is_image:
            from services import moderation_queue as _mq
            verdict = await _mq.screen_image(
                raw,
                surface="pyq",
                owner_id=str(admin.get("sub", "admin")),
                filename=safe_name,
                mime=mime,
                db_handle=_db,
                supa_handle=supa if use_supabase else None,
                bucket=_PYQ_BUCKET,
                extra={
                    "exam_year": exam_year,
                    "exam_title": exam_title,
                    "subject_id": subject_id,
                    "doc_id": doc_id,
                },
            )
            if verdict.flagged and verdict.quarantined:
                logger.warning(
                    "[pyq/upload] Rekognition quarantined %s queue_id=%s max_conf=%.1f",
                    safe_name, verdict.queue_id, verdict.max_confidence,
                )
                # Skip storing the public artefact — admin queue holds the bytes.
                continue

        if use_supabase:
            try:
                file_url = await asyncio.get_event_loop().run_in_executor(
                    _THREAD_POOL,
                    lambda p=storage_path, r=raw, m=mime: _pyq_supabase_upload(r, p, m),
                )
                logger.info(f"PYQ uploaded to Supabase: {storage_path}")
            except Exception as e:
                logger.warning(f"Supabase PYQ upload failed, falling back to base64: {e}")
                file_url = ""

        # ── Fallback: base64 data-URL (images) or skip large PDFs ────────────
        if not file_url:
            if is_image:
                file_url = f"data:{mime};base64,{base64.b64encode(raw).decode()}"
            else:
                # For PDFs without Supabase we store an empty URL — warn admin
                file_url = ""
                logger.warning(f"PYQ PDF stored without file content (Supabase unavailable): {safe_name}")

        # Task #337 — Textract structured-OCR pipeline branch (runbook §3.4).
        # When the upload looks like a single-page printed past paper
        # (image OR small PDF), we fan it out to Textract synchronously
        # to extract FORMS+TABLES blocks. The structured payload is
        # persisted on the pyq doc so downstream graders / the admin
        # mark-sheet review screen can render answer fields without a
        # second OCR round-trip. Failure is non-fatal (the pyq doc is
        # still saved with `textract_status="degraded"`).
        textract_blob: dict = {}
        textract_status = "skipped"
        if (is_image or is_pdf) and len(raw) <= 9 * 1024 * 1024:
            try:
                from providers import aws_native as _awsn
                if _awsn.is_enabled("textract") and _awsn.is_configured():
                    textract_blob = await asyncio.to_thread(
                        _awsn.analyze_document_bytes, raw,
                    )
                    textract_status = "ok"
                    logger.info(
                        "[pyq/upload] Textract structured OCR: %d blocks for %s",
                        len(textract_blob.get("Blocks", []) or []), safe_name,
                    )
            except Exception as _tx_exc:
                textract_status = "degraded"
                logger.warning("[pyq/upload] Textract failed: %s", str(_tx_exc)[:200])

        # ── Build MongoDB document ────────────────────────────────────────────
        doc = {
            "id":            doc_id,
            "filename":      upload.filename or "upload",
            "mime_type":     mime,
            "textract_status": textract_status,
            "textract_block_count": len(textract_blob.get("Blocks", []) or []) if textract_blob else 0,
            "exam_title":    exam_title or f"{paper_type.upper()} {exam_year}",
            "exam_year":     exam_year,
            "paper_type":    paper_type,
            "board_id":      board_id,
            "board_name":    board_name,
            "class_id":      class_id,
            "class_name":    class_name,
            "stream_id":     stream_id,
            "stream_name":   stream_name,
            "subject_id":    subject_id,
            "subject_name":  subject_name,
            "chapter_id":    chapter_id,
            "file_url":      file_url,          # Supabase public URL or data-URL for images
            "storage_path":  storage_path if use_supabase and file_url and not file_url.startswith("data:") else "",
            "storage":       "supabase" if (use_supabase and file_url and not file_url.startswith("data:")) else "base64",
            "is_image":      is_image,
            "is_pdf":        is_pdf,
            # pages array — for images keep one entry; frontend uses file_url
            "pages": [{"file_url": file_url, "filename": upload.filename}] if is_image else [],
            "status":            "uploaded",
            "processing_status": "uploaded",
            "created_at":        datetime.now(timezone.utc).isoformat(),
            "created_by":        admin.get("username", "admin"),
        }
        await _db["pyq_uploads"].insert_one(doc)
        saved_ids.append(doc_id)

    return {"status": "ok", "uploaded": len(saved_ids), "ids": saved_ids,
            "storage": "supabase" if use_supabase else "base64"}


class _PYQTextUploadRequest(BaseModel):
    text:       str
    paper_type: str = "major"
    exam_year:  int
    exam_title: str = ""
    board_id:   str = ""
    class_id:   str = ""
    stream_id:  str = ""
    subject_id: str = ""
    chapter_id: str = ""


@router.post("/api/admin/pyq/upload-text")
async def admin_pyq_upload_text(
    payload: _PYQTextUploadRequest,
    admin: dict = Depends(get_admin_user),
):
    raw_text = (payload.text or "").strip()
    if not raw_text:
        raise HTTPException(400, "Text content is empty")

    _db = _get_db()
    subject_name = board_name = class_name = stream_name = ""
    try:
        if payload.subject_id:
            s = await _db["subjects"].find_one({"id": payload.subject_id}) or await _db["subjects"].find_one({"_id": payload.subject_id})
            subject_name = (s or {}).get("name") or (s or {}).get("title") or ""
        if payload.board_id:
            b = await _db["boards"].find_one({"id": payload.board_id}) or await _db["boards"].find_one({"_id": payload.board_id})
            board_name = (b or {}).get("name") or ""
        if payload.class_id:
            c = await _db["classes"].find_one({"id": payload.class_id}) or await _db["classes"].find_one({"_id": payload.class_id})
            class_name = (c or {}).get("name") or ""
        if payload.stream_id:
            st = await _db["streams"].find_one({"id": payload.stream_id}) or await _db["streams"].find_one({"_id": payload.stream_id})
            stream_name = (st or {}).get("name") or ""
    except Exception:
        pass

    doc_id = str(uuid.uuid4())
    raw_bytes = raw_text.encode("utf-8")
    safe_name = f"pyq-text-{payload.exam_year}-{doc_id[:8]}.txt"
    storage_path = f"{_PYQ_PREFIX}/{doc_id}/{safe_name}"

    file_url = ""
    use_supabase = bool(supa)
    if use_supabase:
        try:
            file_url = await asyncio.get_event_loop().run_in_executor(
                _THREAD_POOL,
                lambda p=storage_path, r=raw_bytes: _pyq_supabase_upload(r, p, "text/plain"),
            )
            logger.info(f"PYQ text uploaded to Supabase: {storage_path}")
        except Exception as e:
            logger.warning(f"Supabase PYQ text upload failed: {e}")
            file_url = ""

    exam_title = payload.exam_title or f"{payload.paper_type.upper()} {payload.exam_year}"
    doc = {
        "id":            doc_id,
        "filename":      safe_name,
        "mime_type":     "text/plain",
        "exam_title":    exam_title,
        "exam_year":     payload.exam_year,
        "paper_type":    payload.paper_type,
        "board_id":      payload.board_id,
        "board_name":    board_name,
        "class_id":      payload.class_id,
        "class_name":    class_name,
        "stream_id":     payload.stream_id,
        "stream_name":   stream_name,
        "subject_id":    payload.subject_id,
        "subject_name":  subject_name,
        "chapter_id":    payload.chapter_id,
        "file_url":      file_url,
        "storage_path":  storage_path if file_url else "",
        "storage":       "supabase" if file_url else "inline",
        "is_image":      False,
        "is_pdf":        False,
        "is_text":       True,
        "pages":         [],
        "status":            "uploaded",
        "processing_status": "uploaded",
        "raw_text":          raw_text[:10000],
        "created_at":        datetime.now(timezone.utc).isoformat(),
        "created_by":        admin.get("username", "admin"),
    }
    await _db["pyq_uploads"].insert_one(doc)

    geo_tags  = ["Dhemaji", "Jorhat", "Guwahati", "Assam"]
    slug      = _pyq_html_slug(board_name, subject_name, payload.exam_year, payload.paper_type)
    seo_title = (
        f"{board_name} {subject_name} Previous Year Question Paper {payload.exam_year} "
        f"({payload.paper_type.upper()}) — Dhemaji, Assam"
    ).strip()
    seo_desc = (
        f"Download and study the {board_name} {subject_name} {payload.paper_type.upper()} "
        f"question paper from {payload.exam_year}. Serving students in Dhemaji, Jorhat, "
        f"Guwahati and across Assam."
    )
    schema_json = json.dumps({
        "@context": "https://schema.org", "@type": "ExamPaper",
        "name": seo_title, "description": seo_desc,
        "about": {"@type": "Thing", "name": subject_name},
        "educationalLevel": class_name or "Higher Secondary",
        "provider": {"@type": "Organization", "name": board_name},
        "dateCreated": str(payload.exam_year),
        "contentLocation": {
            "@type": "Place", "name": "Assam, India",
            "containedInPlace": [{"@type": "Place", "name": g} for g in geo_tags],
        },
    }, ensure_ascii=False)

    questions = _parse_questions_from_text(raw_text)

    html_content = _build_pyq_html(
        questions=questions, raw_text=raw_text, seo_title=seo_title, seo_desc=seo_desc,
        schema_json=schema_json, geo_tags=geo_tags, board_name=board_name,
        subject_name=subject_name, exam_year=payload.exam_year, paper_type=payload.paper_type,
    )

    now = datetime.now(timezone.utc).isoformat()
    page_doc = {
        "slug": slug, "html_content": html_content, "seo_title": seo_title,
        "seo_description": seo_desc, "geo_tags": geo_tags, "schema_json": schema_json,
        "subject_id": payload.subject_id, "subject_name": subject_name,
        "board_id": payload.board_id, "board_name": board_name,
        "class_id": payload.class_id, "class_name": class_name,
        "stream_id": payload.stream_id, "stream_name": stream_name,
        "exam_year": payload.exam_year, "paper_type": payload.paper_type,
        "question_count": len(questions), "questions": questions,
        "raw_text": raw_text[:5000],
        "created_at": now, "updated_at": now,
        "created_by": admin.get("username", "admin"),
    }
    await _upsert_pyq_html_page(db, slug, page_doc)

    if raw_text.strip():
        asyncio.create_task(_index_pyq_rag_chunks(
            raw_text=raw_text, questions=questions, subject_id=payload.subject_id,
            board_id=payload.board_id, exam_year=payload.exam_year,
            paper_type=payload.paper_type, slug=slug,
        ))

    await _db["pyq_uploads"].update_one(
        {"id": doc_id},
        {"$set": {
            "processing_status": "ocr_done",
            "seo_url":           f"/pyq/{slug}",
            "pyq_html_slug":     slug,
            "question_count":    len(questions),
            "updated_at":        now,
        }},
    )

    logger.info(f"PYQ text upload processed: {doc_id} → {slug} ({len(questions)} questions, {len(raw_text)} chars)")

    return {
        "status":    "ok",
        "id":        doc_id,
        "slug":      slug,
        "seo_url":   f"/pyq/{slug}",
        "questions": len(questions),
        "storage":   "supabase" if file_url else "inline",
    }


def _parse_questions_from_text(raw_text: str) -> list:
    lines = raw_text.strip().split("\n")
    questions = []
    current_q = None
    for line in lines:
        line = line.strip()
        if not line:
            continue
        m = re.match(r'^(\d+)\s*[.)]\s*(.+)', line)
        if m:
            if current_q:
                questions.append(current_q)
            current_q = {
                "number": m.group(1),
                "text": m.group(2).strip(),
                "marks": "",
                "sub_parts": [],
            }
            marks_m = re.search(r'\[(\d+)\]|\((\d+)\s*marks?\)', current_q["text"], re.IGNORECASE)
            if marks_m:
                current_q["marks"] = marks_m.group(1) or marks_m.group(2) or ""
        elif current_q:
            sub_m = re.match(r'^([a-z])\s*[.)]\s*(.+)', line, re.IGNORECASE)
            if sub_m:
                current_q["sub_parts"].append({
                    "label": sub_m.group(1),
                    "text": sub_m.group(2).strip(),
                })
            else:
                current_q["text"] += " " + line
    if current_q:
        questions.append(current_q)
    return questions


@router.get("/api/admin/pyq/list")
async def admin_pyq_list(
    subject_id: str = "",
    board_id:   str = "",
    exam_year:  int = 0,
    admin: dict = Depends(get_admin_user),
):
    _db = _get_db()
    filt: dict = {}
    if subject_id: filt["subject_id"] = subject_id
    if board_id:   filt["board_id"]   = board_id
    if exam_year:  filt["exam_year"]  = exam_year

    docs = await _db["pyq_uploads"].find(filt, {"_id": 0}).sort("created_at", -1).to_list(200)
    # Belt-and-suspenders re-sort: rows written before Task #738 are
    # naive (no `+00:00`) and Mongo's lexicographic sort can interleave
    # them with new aware rows incorrectly. The migration script
    # `migrate_pyq_timestamps_to_aware` removes the gap permanently;
    # this Python re-sort guarantees correctness during the rollout.
    docs.sort(key=lambda d: _normalize_iso_for_sort(d.get("created_at")), reverse=True)
    return {"pyqs": docs}


@router.get("/api/admin/pyq/by-chapter/{chapter_id}")
async def admin_pyq_by_chapter(chapter_id: str, admin: dict = Depends(get_admin_user)):
    _db = _get_db()
    docs = await _db["pyq_uploads"].find(
        {"chapter_id": chapter_id},
        {"_id": 0}
    ).sort("created_at", -1).to_list(100)
    docs.sort(key=lambda d: _normalize_iso_for_sort(d.get("created_at")), reverse=True)
    return {"pyqs": docs}


class _BatchProcessRequest(BaseModel):
    pyq_ids: List[str]


@router.post("/api/admin/pyq/batch-process")
async def admin_pyq_batch_process(
    payload: _BatchProcessRequest,
    admin: dict = Depends(get_admin_user),
):
    results = []
    for pyq_id in payload.pyq_ids:
        try:
            result = await admin_pyq_agentic_process(
                _PYQAgenticRequest(pyq_id=pyq_id), admin=admin
            )
            results.append({"pyq_id": pyq_id, "status": "ok", **result})
        except HTTPException as e:
            results.append({"pyq_id": pyq_id, "status": "error", "detail": e.detail})
        except Exception as e:
            results.append({"pyq_id": pyq_id, "status": "error", "detail": str(e)})
    succeeded = sum(1 for r in results if r["status"] == "ok")
    return {"results": results, "total": len(results), "succeeded": succeeded}


@router.get("/api/pyq/download-url/{pyq_id}")
async def public_pyq_download_url(pyq_id: str):
    _db = _get_db()
    doc = await _db["pyq_uploads"].find_one(
        {"id": pyq_id},
        {"_id": 0, "file_url": 1, "filename": 1, "subject_name": 1, "exam_year": 1}
    )
    if not doc or not doc.get("file_url"):
        raise HTTPException(404, "PYQ file not found")
    return {
        "url": doc["file_url"],
        "filename": doc.get("filename", "pyq.pdf"),
        "subject": doc.get("subject_name", ""),
        "year": doc.get("exam_year", 0),
    }


@router.delete("/api/admin/pyq/{pyq_id}")
async def admin_pyq_delete(pyq_id: str, admin: dict = Depends(get_admin_user)):
    _db = _get_db()
    doc = await _db["pyq_uploads"].find_one({"id": pyq_id}, {"_id": 0, "storage_path": 1, "storage": 1})
    if not doc:
        raise HTTPException(404, "PYQ not found")

    if supa and doc.get("storage") == "supabase" and doc.get("storage_path"):
        try:
            await asyncio.get_event_loop().run_in_executor(
                _THREAD_POOL,
                lambda: supa.storage.from_(_PYQ_BUCKET).remove([doc["storage_path"]]),
            )
        except Exception as e:
            logger.warning(f"Supabase PYQ delete failed (continuing): {e}")

    await _db["pyq_uploads"].delete_one({"id": pyq_id})
    return {"status": "deleted", "id": pyq_id}


# ─────────────────────────────────────────────────────────────────────────────
# PYQ AGENTIC PROCESS — upload → OCR → classify pipeline
# ─────────────────────────────────────────────────────────────────────────────

class _PYQAgenticRequest(BaseModel):
    pyq_id: str


@router.post("/api/admin/pyq/agentic-process")
async def admin_pyq_agentic_process(
    payload: _PYQAgenticRequest,
    admin: dict = Depends(get_admin_user),
):
    """
    Agentic PYQ pipeline for a single already-uploaded PDF:
      1. Fetch PDF bytes from Supabase URL
      2. Run Gemini OCR → extract questions
      3. Build SEO HTML page → upsert into pyq_html_pages
      4. Index questions into RAG chunks (background)
    Updates processing_status on pyq_uploads throughout.
    Returns {status, seo_url, question_count, subject_id}.
    """
    pyq_id = payload.pyq_id
    _db = _get_db()
    pyq = await _db["pyq_uploads"].find_one({"id": pyq_id}, {"_id": 0})
    if not pyq:
        raise HTTPException(404, "PYQ not found")

    if not pyq.get("is_pdf"):
        raise HTTPException(400, "Agentic processing only supports PDF uploads")

    file_url = pyq.get("file_url", "")
    if not file_url or file_url.startswith("data:"):
        raise HTTPException(400, "PDF not stored in Supabase — re-upload the file")

    await _db["pyq_uploads"].update_one(
        {"id": pyq_id},
        {"$set": {"processing_status": "ocr_running", "updated_at": datetime.now(timezone.utc).isoformat()}},
    )

    # Fetch PDF bytes from storage URL
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
            resp = await client.get(file_url)
            resp.raise_for_status()
            raw = resp.content
    except Exception as e:
        await _db["pyq_uploads"].update_one(
            {"id": pyq_id},
            {"$set": {"processing_status": "fetch_error", "error_msg": str(e)}},
        )
        raise HTTPException(502, f"Could not fetch PDF from storage: {e}")

    # Resolve metadata from pyq doc
    board_name   = pyq.get("board_name", "")
    class_name   = pyq.get("class_name", "")
    subject_name = pyq.get("subject_name", "")
    stream_name  = pyq.get("stream_name", "")
    exam_year    = int(pyq.get("exam_year") or datetime.now(timezone.utc).year)
    paper_type   = pyq.get("paper_type", "major")
    board_id     = pyq.get("board_id", "")
    class_id     = pyq.get("class_id", "")
    stream_id    = pyq.get("stream_id", "")
    subject_id   = pyq.get("subject_id", "")

    # Gemini Vision OCR
    ocr_prompt = (
        "You are an OCR engine for Assam Board (AHSEC/SEBA/Dibrugarh University) question papers.\n"
        "Extract ALL questions from this PDF question paper.\n"
        "Return ONLY valid JSON in this exact shape:\n"
        '{"questions": [{"number": "1", "text": "...", "marks": "5", "sub_parts": []}], '
        '"raw_text": "...", "word_count": 0}\n'
        "- number: question number as a string\n"
        "- text: full question text\n"
        "- marks: marks as string (empty if not shown)\n"
        "- sub_parts: list of sub-question strings (empty list if none)\n"
        "- raw_text: all extracted text concatenated\n"
        "Do not include any markdown fences or extra text outside the JSON."
    )

    try:
        ocr_result_raw = await vertex_services.analyze_image(
            raw, mime_type="application/pdf", prompt=ocr_prompt, max_output_tokens=8192
        )
    except Exception as e:
        await _db["pyq_uploads"].update_one(
            {"id": pyq_id}, {"$set": {"processing_status": "ocr_error", "error_msg": str(e)}}
        )
        raise HTTPException(502, f"Gemini OCR failed: {e}")

    if not ocr_result_raw:
        await _db["pyq_uploads"].update_one({"id": pyq_id}, {"$set": {"processing_status": "ocr_error"}})
        raise HTTPException(
            502,
            "Gemini OCR returned empty response — Gemini auth is served via the Cloudflare AI "
            "Gateway BYOK binding (google-ai-studio). Check the BYOK binding + "
            "CF_AI_GATEWAY_ACCOUNT_ID/CF_AI_GATEWAY_ID, then hit "
            "/admin/cms/sarvam-health/vertex/health. See docs/VERTEX_SETUP.md "
            "'Migrating Railway → CF AI Gateway BYOK (Task #666)'.",
        )

    # Parse OCR JSON
    try:
        cleaned = ocr_result_raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        ocr_data = json.loads(cleaned)
    except Exception:
        ocr_data = {"questions": [], "raw_text": ocr_result_raw, "word_count": len(ocr_result_raw.split())}

    questions = ocr_data.get("questions") or []
    raw_text  = ocr_data.get("raw_text") or ocr_result_raw or ""

    # Build SEO metadata & HTML
    geo_tags  = ["Dhemaji", "Jorhat", "Guwahati", "Assam"]
    slug      = _pyq_html_slug(board_name, subject_name, exam_year, paper_type)
    seo_title = (
        f"{board_name} {subject_name} Previous Year Question Paper {exam_year} "
        f"({paper_type.upper()}) — Dhemaji, Assam"
    ).strip()
    seo_desc = (
        f"Download and study the {board_name} {subject_name} {paper_type.upper()} "
        f"question paper from {exam_year}. Serving students in Dhemaji, Jorhat, "
        f"Guwahati and across Assam."
    )
    schema_json = json.dumps({
        "@context": "https://schema.org", "@type": "ExamPaper",
        "name": seo_title, "description": seo_desc,
        "about": {"@type": "Thing", "name": subject_name},
        "educationalLevel": class_name or "Higher Secondary",
        "provider": {"@type": "Organization", "name": board_name},
        "dateCreated": str(exam_year),
        "contentLocation": {
            "@type": "Place", "name": "Assam, India",
            "containedInPlace": [{"@type": "Place", "name": g} for g in geo_tags],
        },
    }, ensure_ascii=False)

    html_content = _build_pyq_html(
        questions=questions, raw_text=raw_text, seo_title=seo_title, seo_desc=seo_desc,
        schema_json=schema_json, geo_tags=geo_tags, board_name=board_name,
        subject_name=subject_name, exam_year=exam_year, paper_type=paper_type,
    )

    # Persist html page
    now = datetime.now(timezone.utc).isoformat()
    page_doc = {
        "slug": slug, "html_content": html_content, "seo_title": seo_title,
        "seo_description": seo_desc, "geo_tags": geo_tags, "schema_json": schema_json,
        "subject_id": subject_id, "subject_name": subject_name, "board_id": board_id,
        "board_name": board_name, "class_id": class_id, "class_name": class_name,
        "stream_id": stream_id, "stream_name": stream_name, "exam_year": exam_year,
        "paper_type": paper_type, "question_count": len(questions),
        "questions": questions, "raw_text": raw_text[:5000],
        "created_at": now, "updated_at": now, "created_by": admin.get("username", "admin"),
    }
    await _upsert_pyq_html_page(db, slug, page_doc)

    # Index RAG chunks in background
    if raw_text.strip():
        asyncio.create_task(_index_pyq_rag_chunks(
            raw_text=raw_text, questions=questions, subject_id=subject_id,
            board_id=board_id, exam_year=exam_year, paper_type=paper_type, slug=slug,
        ))

    await _db["pyq_uploads"].update_one(
        {"id": pyq_id},
        {"$set": {
            "processing_status": "ocr_done",
            "seo_url":           f"/pyq/{slug}",
            "pyq_html_slug":     slug,
            "question_count":    len(questions),
            "updated_at":        now,
        }},
    )

    return {
        "status":         "ocr_done",
        "seo_url":        f"/pyq/{slug}",
        "slug":           slug,
        "question_count": len(questions),
        "subject_id":     subject_id,
    }


@router.get("/api/admin/pyq/{pyq_id}/status")
async def admin_pyq_get_status(pyq_id: str, admin: dict = Depends(get_admin_user)):
    """Lightweight status polling for the agentic pipeline."""
    _db = _get_db()
    doc = await _db["pyq_uploads"].find_one(
        {"id": pyq_id},
        {"_id": 0, "processing_status": 1, "seo_url": 1, "question_count": 1,
         "subject_id": 1, "pyq_html_slug": 1, "error_msg": 1},
    )
    if not doc:
        raise HTTPException(404, "PYQ not found")
    return doc


# ─────────────────────────────────────────────────────────────────────────────
# PYQ HTML REPLICA — Gemini Vision OCR → SEO HTML page
# ─────────────────────────────────────────────────────────────────────────────

def _pyq_html_slug(board_name: str, subject_name: str, exam_year: int, paper_type: str) -> str:
    """Generate a geo-optimised slug for a PYQ replica page."""
    import re as _re
    def _s(t: str) -> str:
        t = t.lower().strip()
        t = _re.sub(r'[^a-z0-9\s-]', '', t)
        t = _re.sub(r'[\s]+', '-', t)
        return _re.sub(r'-+', '-', t).strip('-')

    board_slug   = _s(board_name or "board")
    subject_slug = _s(subject_name or "subject")
    pt_slug      = _s(paper_type or "major") if paper_type and paper_type not in ("major", "") else ""
    geo_slug     = "dhemaji"   # primary geo anchor
    suffix       = f"-{pt_slug}" if pt_slug else ""
    return f"{board_slug}-{subject_slug}-pyq-{exam_year}{suffix}-{geo_slug}"


def _build_pyq_html(
    questions: list,
    raw_text:  str,
    seo_title: str,
    seo_desc:  str,
    schema_json: str,
    geo_tags: list,
    board_name: str,
    subject_name: str,
    exam_year: int,
    paper_type: str,
) -> str:
    """Render the full HTML document for a PYQ replica page."""
    import html as _html

    geo_meta = "".join(
        f'<meta name="geo.placename" content="{_html.escape(g)}">\n    '
        for g in geo_tags
    )

    _ASM_RE = re.compile(r'[\u0980-\u09FF]')

    def _split_bilingual(txt):
        chars = list(txt)
        asm_idx = -1
        for ci, c in enumerate(chars):
            if _ASM_RE.match(c):
                asm_idx = ci
                break
        if asm_idx < 0:
            return txt, ""
        if asm_idx == 0:
            return "", txt.strip()
        return txt[:asm_idx].rstrip(), txt[asm_idx:].strip()

    question_rows = ""
    if questions:
        for q in questions:
            num   = _html.escape(str(q.get("number") or q.get("question_number") or ""))
            text  = str(q.get("text") or q.get("question_text") or q.get("q") or "")
            marks = _html.escape(str(q.get("marks") or ""))
            sub_parts = q.get("sub_parts") or []

            marks_html = f'<span class="marks">{marks}</span>' if marks else ""

            eng_text, asm_text = _split_bilingual(text)
            eng_escaped = _html.escape(eng_text)
            asm_html = ""
            if asm_text:
                asm_html = f'<span class="asm">{_html.escape(asm_text)}</span>'

            sp_html = ""
            if sub_parts:
                sp_items = ""
                for sp in sub_parts:
                    sp_text = sp.get('text', str(sp)) if isinstance(sp, dict) else str(sp)
                    sp_eng, sp_asm = _split_bilingual(sp_text)
                    sp_label = sp.get('label', '') if isinstance(sp, dict) else ''
                    label_prefix = f"({sp_label})&ensp;" if sp_label else ""
                    sp_line = f'<div class="sp">{label_prefix}{_html.escape(sp_eng)}'
                    if sp_asm:
                        sp_line += f'<span class="asm">{_html.escape(sp_asm)}</span>'
                    sp_line += '</div>'
                    sp_items += sp_line
                sp_html = f'<div class="sub-parts">{sp_items}</div>'

            question_rows += f"""
        <div class="q">
          <div class="q-head"><span class="q-num">{num}.</span><span class="q-text">{eng_escaped}{asm_html}</span>{marks_html}</div>
          {sp_html}
        </div>"""
    elif raw_text:
        lines = (raw_text or "").strip().split('\n')
        for line in lines:
            line = line.strip()
            if not line:
                continue
            eng, asm = _split_bilingual(line)
            row = f'<div class="q-line">{_html.escape(eng)}'
            if asm:
                row += f'<span class="asm">{_html.escape(asm)}</span>'
            row += '</div>'
            question_rows += row

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{_html.escape(seo_title)}</title>
  <meta name="description" content="{_html.escape(seo_desc)}">
  <meta name="robots" content="index, follow">
  <meta property="og:title" content="{_html.escape(seo_title)}">
  <meta property="og:description" content="{_html.escape(seo_desc)}">
  {geo_meta}
  <script type="application/ld+json">{schema_json}</script>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: #fff;
      color: #000;
      font-family: "Times New Roman", Times, "Noto Serif Bengali", serif;
      font-size: 13px;
      line-height: 1.45;
    }}
    .page-wrapper {{
      max-width: 700px;
      margin: 0 auto;
      padding: 1.5in 1in;
    }}
    @media (max-width: 700px) {{
      .page-wrapper {{ padding: 20px 14px; }}
      body {{ font-size: 12.5px; }}
    }}
    .page-header {{
      text-align: center;
      margin-bottom: 1.5em;
      border-bottom: 1.5px solid #000;
      padding-bottom: 0.6em;
    }}
    .page-header h1 {{ font-size: 1.1em; font-weight: bold; letter-spacing: 0.02em; }}
    .page-header .meta {{ font-size: 0.9em; margin-top: 0.2em; }}
    .page-header .meta-sub {{ font-size: 0.8em; color: #444; margin-top: 0.15em; }}
    .questions {{ margin-top: 1em; }}
    .q {{ margin-bottom: 0.6em; }}
    .q-head {{
      display: flex;
      align-items: flex-start;
      gap: 0.4em;
    }}
    .q-num {{
      flex-shrink: 0;
      font-weight: bold;
      min-width: 1.5em;
    }}
    .q-text {{
      flex: 1;
    }}
    .marks {{
      flex-shrink: 0;
      font-style: italic;
      font-size: 0.9em;
      white-space: nowrap;
      margin-left: 0.5em;
    }}
    .asm {{
      display: block;
      margin: 0;
      padding: 0;
      padding-left: 1.8em;
      line-height: 1.35;
    }}
    .sub-parts {{
      padding-left: 2.2em;
      margin-top: 0.15em;
    }}
    .sub-parts .sp {{
      margin-bottom: 0.15em;
    }}
    .sub-parts .sp .asm {{
      padding-left: 1.5em;
    }}
    .q-line {{
      margin-bottom: 0.1em;
    }}
    .q-line .asm {{
      padding-left: 1.5em;
    }}
    .geo-footer {{
      margin-top: 2em;
      font-size: 10px;
      color: #888;
      border-top: 1px solid #ccc;
      padding-top: 0.5em;
      text-align: center;
    }}
  </style>
</head>
<body>
  <div class="page-wrapper">
    <div class="page-header">
      <h1>{_html.escape(board_name)}</h1>
      <p class="meta">({_html.escape(subject_name)})</p>
      <p class="meta-sub">{_html.escape(paper_type.upper())} — {exam_year} &nbsp;|&nbsp; Full Marks · Time</p>
    </div>
    <div class="questions">
{question_rows}
    </div>
    <p class="geo-footer">
      Serving students in {", ".join(_html.escape(g) for g in geo_tags)} &middot; Syrabit.ai
    </p>
  </div>
</body>
</html>"""


@router.post("/api/admin/pyq/html-replica")
async def admin_pyq_html_replica(
    file:        UploadFile = File(...),
    board_id:    str = Form(""),
    class_id:    str = Form(""),
    stream_id:   str = Form(""),
    subject_id:  str = Form(""),
    paper_type:  str = Form("major"),
    exam_year:   int = Form(...),
    admin: dict = Depends(get_admin_user),
):
    """
    OCR a PYQ PDF via Gemini Vision, build an SEO HTML replica, persist to
    pyq_html_pages collection, and index extracted questions into RAG chunks.
    Returns { seo_url: "/pyq/{slug}" }.
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "Empty file")

    # Validate that the uploaded file is actually a PDF
    fname = (file.filename or "").lower()
    mime  = file.content_type or ""
    is_pdf = (
        mime == "application/pdf"
        or fname.endswith(".pdf")
        or raw[:4] == b"%PDF"
    )
    if not is_pdf:
        raise HTTPException(400, "Only PDF files are accepted for HTML replica generation")
    mime = "application/pdf"

    # ── Resolve names (async Motor) ────────────────────────────────────────────
    subject_name = board_name = class_name = stream_name = ""
    try:
        if subject_id and db is not None:
            s = await db.subjects.find_one({"id": subject_id}, {"_id": 0}) or await db.subjects.find_one({"_id": subject_id}, {"_id": 0})
            subject_name = (s or {}).get("name") or (s or {}).get("title") or ""
        if board_id and db is not None:
            b = await db.boards.find_one({"id": board_id}, {"_id": 0}) or await db.boards.find_one({"_id": board_id}, {"_id": 0})
            board_name = (b or {}).get("name") or ""
        if class_id and db is not None:
            c = await db.classes.find_one({"id": class_id}, {"_id": 0}) or await db.classes.find_one({"_id": class_id}, {"_id": 0})
            class_name = (c or {}).get("name") or ""
        if stream_id and db is not None:
            st = await db.streams.find_one({"id": stream_id}, {"_id": 0}) or await db.streams.find_one({"_id": stream_id}, {"_id": 0})
            stream_name = (st or {}).get("name") or ""
    except Exception:
        pass

    # ── Gemini Vision OCR ──────────────────────────────────────────────────────
    ocr_prompt = (
        "You are an OCR engine for Assam Board (AHSEC/SEBA/Dibrugarh University) question papers.\n"
        "Extract ALL questions from this PDF question paper.\n"
        "Return ONLY valid JSON in this exact shape:\n"
        '{"questions": [{"number": "1", "text": "...", "marks": "5", "sub_parts": []}], '
        '"raw_text": "...", "word_count": 0}\n'
        "- number: question number as a string\n"
        "- text: full question text\n"
        "- marks: marks as string (empty if not shown)\n"
        "- sub_parts: list of sub-question strings (empty list if none)\n"
        "- raw_text: all extracted text concatenated\n"
        "Do not include any markdown fences or extra text outside the JSON."
    )

    ocr_result_raw = await vertex_services.analyze_image(raw, mime_type=mime, prompt=ocr_prompt, max_output_tokens=8192)
    if not ocr_result_raw:
        raise HTTPException(
            502,
            "Gemini OCR failed — Gemini auth is served via the Cloudflare AI Gateway BYOK binding "
            "(google-ai-studio). Check the BYOK binding + CF_AI_GATEWAY_ACCOUNT_ID/CF_AI_GATEWAY_ID, "
            "then hit /admin/cms/sarvam-health/vertex/health. See docs/VERTEX_SETUP.md "
            "'Migrating Railway → CF AI Gateway BYOK (Task #666)'.",
        )

    # Parse JSON from Gemini response
    try:
        cleaned = ocr_result_raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
        ocr_data = json.loads(cleaned)
    except Exception:
        ocr_data = {"questions": [], "raw_text": ocr_result_raw, "word_count": len(ocr_result_raw.split())}

    questions = ocr_data.get("questions") or []
    raw_text  = ocr_data.get("raw_text") or ocr_result_raw or ""

    # ── Build SEO metadata ─────────────────────────────────────────────────────
    geo_tags  = ["Dhemaji", "Jorhat", "Guwahati", "Assam"]
    slug      = _pyq_html_slug(board_name, subject_name, exam_year, paper_type)
    seo_title = (
        f"{board_name} {subject_name} Previous Year Question Paper {exam_year} "
        f"({paper_type.upper()}) — Dhemaji, Assam"
    ).strip()
    seo_desc  = (
        f"Download and study the {board_name} {subject_name} {paper_type.upper()} "
        f"question paper from {exam_year}. Serving students in Dhemaji, Jorhat, "
        f"Guwahati and across Assam."
    )

    schema_json = json.dumps({
        "@context": "https://schema.org",
        "@type": "ExamPaper",
        "name": seo_title,
        "description": seo_desc,
        "about": {"@type": "Thing", "name": subject_name},
        "educationalLevel": class_name or "Higher Secondary",
        "provider": {"@type": "Organization", "name": board_name},
        "dateCreated": str(exam_year),
        "contentLocation": {
            "@type": "Place",
            "name": "Assam, India",
            "containedInPlace": [{"@type": "Place", "name": g} for g in geo_tags],
        },
    }, ensure_ascii=False)

    # ── Render HTML replica ────────────────────────────────────────────────────
    html_content = _build_pyq_html(
        questions=questions,
        raw_text=raw_text,
        seo_title=seo_title,
        seo_desc=seo_desc,
        schema_json=schema_json,
        geo_tags=geo_tags,
        board_name=board_name,
        subject_name=subject_name,
        exam_year=exam_year,
        paper_type=paper_type,
    )

    # ── Persist to MongoDB (upsert by slug) ───────────────────────────────────
    now = datetime.now(timezone.utc).isoformat()
    page_doc = {
        "slug":         slug,
        "html_content": html_content,
        "seo_title":    seo_title,
        "seo_description": seo_desc,
        "geo_tags":     geo_tags,
        "schema_json":  schema_json,
        "subject_id":   subject_id,
        "subject_name": subject_name,
        "board_id":     board_id,
        "board_name":   board_name,
        "class_id":     class_id,
        "class_name":   class_name,
        "stream_id":    stream_id,
        "stream_name":  stream_name,
        "exam_year":    exam_year,
        "paper_type":   paper_type,
        "question_count": len(questions),
        "created_at":   now,
        "updated_at":   now,
        "created_by":   admin.get("username", "admin"),
    }
    await _upsert_pyq_html_page(db, slug, page_doc)

    # ── Index extracted questions into RAG chunks (priority 1 / content_type=pyq) ──
    if raw_text.strip():
        asyncio.create_task(_index_pyq_rag_chunks(
            raw_text=raw_text,
            questions=questions,
            subject_id=subject_id,
            board_id=board_id,
            exam_year=exam_year,
            paper_type=paper_type,
            slug=slug,
        ))

    return {"seo_url": f"/pyq/{slug}", "slug": slug, "question_count": len(questions)}


async def _index_pyq_rag_chunks(
    raw_text: str,
    questions: list,
    subject_id: str,
    board_id: str,
    exam_year: int,
    paper_type: str,
    slug: str,
):
    """Background task: store PYQ question text as RAG chunks tagged content_type=pyq, priority=1.
    Deletes any existing chunks for this slug first to prevent index duplication on re-generation."""
    try:
        # Remove previous chunks for this slug (idempotent re-generation)
        del_res = await db.chunks.delete_many({"pyq_slug": slug, "content_type": "pyq"})
        if del_res.deleted_count:
            logger.info(f"PYQ RAG: removed {del_res.deleted_count} stale chunks for slug={slug}")

        # Build one chunk per question (or fall back to raw_text paragraphs)
        chunks_to_index = []
        if questions:
            for q in questions:
                q_text = str(q.get("text") or q.get("question_text") or "").strip()
                sub    = " ".join((s.get('text', str(s)) if isinstance(s, dict) else str(s)) for s in (q.get("sub_parts") or []) if s)
                full   = f"{q_text} {sub}".strip()
                if len(full) >= 30:
                    chunks_to_index.append(full)
        else:
            paragraphs = [p.strip() for p in raw_text.split('\n') if len(p.strip()) >= 50]
            chunks_to_index = paragraphs[:30]

        now = datetime.now(timezone.utc).isoformat()
        for i, chunk_text in enumerate(chunks_to_index):
            embedding = await vertex_services.embed_text(chunk_text, task_type="RETRIEVAL_DOCUMENT")
            chunk_doc = {
                "id":            str(uuid.uuid4()),
                "chapter_id":    f"pyq-{slug}",
                "subject_id":    subject_id,
                "board_id":      board_id,
                "content":       chunk_text,
                "content_type":  "pyq",
                "priority":      1,
                "chunk_index":   i,
                "tags":          _extract_keywords(chunk_text)[:5],
                "geo_tags":      ["Dhemaji", "Jorhat", "Guwahati", "Assam"],
                "exam_year":     exam_year,
                "paper_type":    paper_type,
                "pyq_slug":      slug,
                "char_count":    len(chunk_text),
                "created_at":    now,
            }
            if embedding:
                chunk_doc["embedding"] = embedding
            await db.chunks.insert_one(chunk_doc)

        logger.info(f"PYQ RAG: indexed {len(chunks_to_index)} chunks for slug={slug}")
    except Exception as exc:
        logger.warning(f"PYQ RAG indexing failed for slug={slug}: {exc}")


@router.get("/api/pyq/list")
async def public_pyq_list(
    board_id:   str = "",
    subject_id: str = "",
    exam_year:  int = 0,
):
    """Public list of available PYQ HTML replica pages.

    Intentionally public — used by admin dashboard preview, sitemap generators, and
    public topic-discovery pages. Contains only metadata (title, slug, year), never
    PII or unpublished content.
    """
    if db is None:
        return {"pages": []}
    filt: dict = {}
    if board_id:   filt["board_id"]   = board_id
    if subject_id: filt["subject_id"] = subject_id
    if exam_year:  filt["exam_year"]  = exam_year
    docs = await db.pyq_html_pages.find(
        filt,
        {"_id": 0, "slug": 1, "seo_title": 1, "seo_description": 1,
         "subject_name": 1, "board_name": 1, "exam_year": 1, "paper_type": 1,
         "question_count": 1, "created_at": 1}
    ).sort("created_at", -1).limit(200).to_list(200)
    # See _normalize_iso_for_sort docstring — re-sort to handle the
    # mixed naive/aware timestamp window during the Task #738 rollout.
    docs.sort(key=lambda d: _normalize_iso_for_sort(d.get("created_at")), reverse=True)
    return {"pages": docs}


async def _serve_pyq_html(slug: str):
    """Shared logic: fetch PYQ doc from MongoDB and return HTMLResponse."""
    from fastapi.responses import HTMLResponse as _HTMLResponse
    if db is None:
        raise HTTPException(503, "Database unavailable")
    doc = await db.pyq_html_pages.find_one({"slug": slug}, {"_id": 0})
    if not doc:
        raise HTTPException(404, f"PYQ page not found: {slug}")
    html_content = doc.get("html_content", "")
    if not html_content:
        raise HTTPException(404, "HTML content not available")
    safe_title = (
        doc.get("seo_title", "")[:120]
        .encode("ascii", "replace")
        .decode("ascii")
    )
    return _HTMLResponse(
        content=html_content,
        status_code=200,
        headers={
            "Cache-Control": "public, max-age=86400, s-maxage=604800",
            "X-PYQ-Title":   safe_title,
        },
    )


@router.get("/api/pyq/{slug}/meta")
async def public_pyq_page_meta(slug: str):
    """Slim JSON metadata for a PYQ replica page (Task #338).

    Companion to the HTML routes — lets the SPA hydrate
    `pyqDatasetSchema` with real values (subject, board, year,
    educational level, license, total questions, dates, author)
    instead of guessing from the URL slug. Cached at the edge for
    1h via the worker's `/api/pyq/` prefix policy.
    """
    if db is None:
        raise HTTPException(503, "Database unavailable")
    doc = await db.pyq_html_pages.find_one(
        {"slug": slug},
        {
            "_id": 0,
            "slug": 1, "seo_title": 1, "seo_description": 1,
            "subject_name": 1, "board_name": 1, "class_name": 1, "stream_name": 1,
            "exam_year": 1, "paper_type": 1, "question_count": 1,
            "created_at": 1, "updated_at": 1,
        },
    )
    if not doc:
        raise HTTPException(404, f"PYQ page not found: {slug}")
    board = doc.get("board_name") or ""
    subject = doc.get("subject_name") or ""
    klass = doc.get("class_name") or ""
    year = doc.get("exam_year")
    return {
        "slug": doc.get("slug") or slug,
        "title": doc.get("seo_title") or "",
        "description": doc.get("seo_description") or "",
        "subject": subject,
        "board": board,
        "class_name": klass,
        "stream": doc.get("stream_name") or "",
        "year": year,
        "paper_type": doc.get("paper_type") or "",
        "educational_level": (klass or board or "Higher Secondary").strip(),
        "total_questions": doc.get("question_count") or 0,
        # Boards retain copyright on their question papers; default to
        # an "all rights reserved" stance and let admins override later.
        "license": "https://syrabit.ai/terms",
        "author": board or "Syrabit.ai Editorial Team",
        "language": "en-IN",
        "published_at": doc.get("created_at") or "",
        "updated_at": doc.get("updated_at") or "",
    }


@router.get("/pyq/{slug}")
async def public_pyq_page_canonical(slug: str):
    """Canonical SEO URL — serves full HTML document with head/meta/JSON-LD.
    Production-accessible without any Vite/SPA layer."""
    return await _serve_pyq_html(slug)


@router.get("/api/pyq/{slug}")
async def public_pyq_page(slug: str):
    """Return stored HTML replica for a PYQ slug — sets correct content-type."""
    return await _serve_pyq_html(slug)


if __name__ == "__main__":
    import uvicorn
    PORT = int(os.getenv("PORT", 5000))
    logger.info(f"Starting server on port {PORT}")
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=PORT,
        log_level="info"
    )
