"""Syrabit.ai — CMS documents, Sarvam AI, health checks, studio, and assorted admin utilities.

This file is the historical home for ~55 admin/public routes spanning
several domains. It used to also host /admin/ga4/* and /admin/vertex/*;
those were carved into ``routes/admin_ga4.py`` and ``routes/admin_vertex.py``
during the admin-panel audit (Task #5) — see the breadcrumb comment
near the bottom of this file. Further carve-outs (analytics, studio,
dashboard, workers-ai) are tracked but deferred — the test suite pins
several private symbols here (``_record_assamese_run``, ``_scrub_pii``,
``_ASM_REFRESH_INTERVAL_SECONDS``, ``derive_bot_cache_key``,
``BotRenderMiddleware``, ``CmsNoIndexMiddleware``) so any further split
must move the helpers in lockstep with their tests.

Route catalog (all paths have the ``/api`` prefix added by server.py):

  CMS DOCUMENTS (admin) ── 14 routes ────────────────────────────────
    GET    /admin/content/cms-documents/merged-subject-ids
    GET    /admin/content/cms-documents/seo-topics-subject-ids
    GET    /admin/content/cms-documents/assets-generated-subject-ids
    GET    /admin/content/cms-documents
    POST   /admin/content/cms-documents
    PATCH  /admin/content/cms-documents/{doc_id}
    PUT    /admin/content/cms-documents/{doc_id}
    POST   /admin/content/cms-documents/{doc_id}/publish
    POST   /admin/content/cms-documents/{doc_id}/link-syllabus
    POST   /admin/content/cms-documents/{doc_id}/revisions
    POST   /admin/content/extract-pdf-text
    DELETE /admin/content/cms-documents/{doc_id}
    POST   /admin/content/cms-documents/{doc_id}/process-rag
    POST   /admin/upload/image

  CMS PUBLIC + LEGACY MERGE ── 9 routes ─────────────────────────────
    GET    /content/cms-library
    GET    /content/cms-documents/{doc_id}
    GET    /cms/posts
    GET    /cms/post/{subject_id}
    GET    /cms/{user_id}
    GET    /cms/{user_id}/{slug}
    POST   /cms/personalize
    POST   /admin/cms/merge/{subject_id}
    POST   /admin/cms/merge-by-chapter/{subject_id}

  CONTENT / SYLLABUS PDF ── 5 routes ────────────────────────────────
    POST   /admin/content/regenerate-sitemap
    POST   /admin/content/upload-pdf
    GET    /content/documents/{document_id}
    GET    /content/subject-documents/{subject_id}
    DELETE /admin/content/documents/{document_id}
    DELETE /admin/syllabus/reset-all

  HEALTH / METRICS / AI CACHE ── 6 routes ───────────────────────────
    GET    /ready
    GET    /health
    GET    /metrics
    GET    /metrics/history
    GET    /ai/cache/stats
    POST   /ai/cache/purge
    GET    /admin/workers-ai/status
    POST   /admin/workers-ai/kill-switch

  SARVAM (TTS / Translate / Transliterate / Status) ── 4 routes ─────
    GET    /sarvam/status
    POST   /sarvam/translate
    POST   /sarvam/tts
    POST   /sarvam/transliterate
    GET    /admin/translation/languages

  ASSAMESE PURITY (admin) ── 8 routes ───────────────────────────────
    GET    /admin/assamese-purity
    PATCH  /admin/assamese-purity
    DELETE /admin/assamese-purity
    GET    /admin/assamese-purity/audit
    POST   /admin/assamese-purity/audit/{audit_id}/revert
    GET    /admin/assamese-purity/stats
    GET    /admin/assamese-purity/runs
    POST   /admin/assamese-purity/test

  DASHBOARD / ALERTS / STUDIO / SEO / ANALYTICS / AUTOMATION ───────
    GET    /admin/dashboard/metrics
    GET    /admin/alerts
    PATCH  /admin/alerts/{alert_id}/acknowledge
    PUT    /admin/alerts/thresholds
    POST   /admin/studio/parse
    POST   /admin/studio/publish
    POST   /admin/seo/generate
    GET    /admin/studio/drafts
    POST   /admin/studio/drafts
    DELETE /admin/studio/drafts/{draft_id}
    POST   /admin/studio/drafts/{draft_id}/publish
    GET    /admin/analytics/funnel
    GET    /admin/analytics/content-heatmap
    GET    /admin/analytics/content-card-views
    GET    /admin/analytics/revenue
    GET    /admin/analytics/predictor
    GET    /admin/analytics/daily
    GET    /admin/automation/insights        (and remaining automation routes below)

Module-level helpers + middleware that other modules / tests import from here:
  * Middleware: ``CmsNoIndexMiddleware``, ``BotRenderMiddleware``
  * Bot rendering: ``derive_bot_cache_key``
  * Assamese purity: ``apply_persisted_assamese_purity_override``,
    ``_assamese_purity_refresh_loop``, ``ensure_assamese_runs_index``,
    ``ensure_assamese_audit_index``, ``_record_assamese_run``,
    ``_scrub_pii``, ``_snippet``, ``_ASM_SNIPPET_MAX_CHARS``,
    ``_ASM_REFRESH_INTERVAL_SECONDS``
"""
import re, json, asyncio, time, uuid, logging, hashlib, os, html as _html_mod
import nh3 as _nh3
from typing import Optional, List
from datetime import datetime, timezone, timedelta
from fastapi import (
    APIRouter, HTTPException, Depends, Query, Body, Path,
    File, UploadFile, Response, Form,
)
from fastapi.responses import JSONResponse, HTMLResponse
from starlette.requests import Request as StarletteRequest
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel
import cachetools, httpx

from models import (
    HealthOut, ReadyOut,
)
from config import (
    LLM_MODEL,
    LLM_PROVIDER,
)
from deps import (
    _cms_request_ctx,
    db,
    is_mongo_available,
    mark_mongo_down,
    redis_client,
    sarvam_client,
    supa,
)
import deps
from cache import (
    _get_content_cache,
    _set_content_cache,
)
from routes.admin_monetization import merge_subject_content, _md_to_html as _blog_md_to_html, _extract_headings_json, preprocess_markdown
from auth_deps import (
    get_current_user, get_admin_user,
)
from db_ops import (
    _pg_rows,
    _supa,
    supa_list_users,
)
from llm import call_llm_api, call_llm_api_content, _LLM_PROVIDERS, _llm_batcher, get_llm_provider_stats
from cache import _content_cache, _ai_response_cache, _redis_hit_count, _redis_miss_count
from metrics import (
    _metrics, _health_deps_cache, _health_deps_cache_at, _HEALTH_CACHE_TTL_S,
    _metrics_history, _metrics_history_lock, _METRICS_HISTORY_MAX,
    _snapshot_metrics, _startup_time,
    _check_health_deps, _ALERT_THRESHOLDS,
)
from rag import _embed_and_store_page
from seo_engine import _md_to_html
import ga4_client
import cloudflare_client
import vertex_services
import health_snapshot_cache

# Task #848 — register the per-dependency probes once, at import time.
# Probes are lazy (they import ``deps`` inside the call), so this is
# safe even though deps.db / deps.pg_pool aren't initialised yet.
health_snapshot_cache.register_default_probes()

logger = logging.getLogger(__name__)

_llm_health_cache: dict = {}
_llm_health_task: asyncio.Task | None = None


def _vertex_block_for_health() -> tuple[dict, bool]:
    """Task #691 — produce the ``vertex`` block surfaced under
    ``/health → dependencies`` and decide whether vertex should drag
    the aggregate ``status`` to ``degraded``.

    Returns ``(block, ok)`` where:

    * ``block`` carries ``status`` (one of ``ok / unhealthy / stale /
      unknown``), ``age_s``, ``auth_mode``, ``via_cf_gateway`` and the
      last failure ``reason`` — the same fields the admin dashboard
      tile (Task #689) renders.
    * ``ok`` is ``True`` unless the cached probe is actively
      ``unhealthy`` or ``stale``. ``unknown`` (no probe has completed
      since boot) is treated as non-degrading so a fresh rollout
      doesn't flap the page yellow for the first probe interval.
    """
    try:
        import vertex_health_cache
        snap = vertex_health_cache.dashboard_snapshot()
    except Exception as exc:
        return (
            {"status": "unknown", "reason": f"cache unavailable: {exc!r}"},
            True,
        )
    status = snap.get("status") or "unknown"
    block = {
        "status": status,
        "age_s": snap.get("age_s"),
        "auth_mode": snap.get("auth_mode"),
        "via_cf_gateway": snap.get("via_cf_gateway"),
        "consecutive_failures": snap.get("consecutive_failures") or 0,
        "reason": snap.get("reason"),
    }
    ok = status not in ("unhealthy", "stale")
    return block, ok

# Models to try in order. The probe walks the list and stops at the first
# success — so the LLM layer is reported "ok" as long as ANY provider in
# the pool answers. Hardcoding a single provider/model (the previous bug)
# made the health card flip to "degraded" whenever that one provider had
# a transient error or rate-limit, even though real chat traffic was fine.
_LLM_PROBE_MODELS = (
    # Fast providers first — probe succeeds without waiting on quota-limited models.
    "llama-4-scout-17b-16e-instruct",        # Workers AI — reliable, sub-200ms
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "sarvam-m",                               # Sarvam AI
    "gemini-2.5-flash",                       # Last: may be rate-limited/quota exceeded
)

async def _bg_llm_health_probe():
    # Wait briefly for providers to register, then enter the loop.
    await asyncio.sleep(5)
    while True:
        _st = "degraded"
        _lat = 0
        _last_err: str | None = None
        if not _LLM_PROVIDERS:
            # No providers wired at all → genuinely degraded. Keep latency 0.
            _llm_health_cache["data"] = {"status": "degraded", "latencyMs": 0,
                                         "reason": "no providers configured"}
            await asyncio.sleep(300)
            continue
        for _model in _LLM_PROBE_MODELS:
            try:
                _t0 = _time_mod.time()
                _resp = await asyncio.wait_for(
                    call_llm_api(
                        [{"role": "user", "content": "Reply with exactly: ok"}],
                        model=_model,
                        max_tokens=4,
                    ),
                    timeout=8.0,
                )
                _lat = int((_time_mod.time() - _t0) * 1000)
                if _resp and len(_resp.strip()) > 0:
                    _st = "ok"
                    break
            except Exception as exc:
                _last_err = f"{_model}: {type(exc).__name__}"
                continue
        payload = {"status": _st, "latencyMs": _lat}
        if _st != "ok" and _last_err:
            payload["reason"] = _last_err
        _llm_health_cache["data"] = payload
        await asyncio.sleep(300)

def _ensure_llm_health_probe():
    global _llm_health_task
    # Seed the cache as "ok" immediately when providers are configured so
    # the first 5 minutes (before the probe loop runs) doesn't show a
    # spurious "degraded" while everything is actually fine.
    if "data" not in _llm_health_cache and _LLM_PROVIDERS:
        _llm_health_cache["data"] = {"status": "ok", "latencyMs": 0,
                                     "reason": "warming up"}
    if _llm_health_task is None or _llm_health_task.done():
        _llm_health_task = asyncio.ensure_future(_bg_llm_health_probe())

def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_]+', '-', text)
    text = re.sub(r'-+', '-', text).strip('-')
    return text

router = APIRouter()

class CMSDocument(BaseModel):
    title: str
    content: str = ""           # raw markdown (content_raw)
    content_html: Optional[str] = ""   # processed HTML (auto-generated if empty)
    meta_description: Optional[str] = ""  # 160 char SEO description
    description: Optional[str] = ""  # Long description (2000 char)
    seo_tags: Optional[str] = ""
    primary_keyword: Optional[str] = ""
    seo_slug: Optional[str] = ""
    thumbnail_url: Optional[str] = ""
    alt_text: Optional[str] = ""
    category: Optional[str] = ""  # e.g., ahsec/class12/pcm/physics
    headings: Optional[str] = ""  # JSON string of extracted headings
    geo_tags: Optional[str] = ""  # board/class/subject/topic for GEO targeting
    schema_type: Optional[str] = "Article"  # Article, FAQPage, HowTo
    canonical_url: Optional[str] = ""
    status: str = "draft"

class CMSDocumentUpdate(BaseModel):
    """Partial-update model for PATCH — all fields optional."""
    title: Optional[str] = None
    content: Optional[str] = None
    content_html: Optional[str] = None
    meta_description: Optional[str] = None
    description: Optional[str] = None
    seo_tags: Optional[str] = None
    primary_keyword: Optional[str] = None
    seo_slug: Optional[str] = None
    thumbnail_url: Optional[str] = None
    alt_text: Optional[str] = None
    category: Optional[str] = None
    headings: Optional[str] = None
    geo_tags: Optional[str] = None
    schema_type: Optional[str] = None
    canonical_url: Optional[str] = None
    status: Optional[str] = None
    is_published: Optional[bool] = None

@router.get("/admin/content/cms-documents/merged-subject-ids")
async def get_merged_subject_ids(admin: dict = Depends(get_admin_user)):
    """Return the set of subject IDs that already have a cms_documents entry."""
    try:
        if not await is_mongo_available():
            return []
        cursor = db.cms_documents.find({"subject_id": {"$exists": True, "$ne": ""}}, {"_id": 0, "subject_id": 1})
        docs = await cursor.to_list(10000)
        return list({d["subject_id"] for d in docs if d.get("subject_id")})
    except Exception:
        mark_mongo_down()
        return []

@router.get("/admin/content/cms-documents/seo-topics-subject-ids")
async def get_seo_topics_subject_ids(admin: dict = Depends(get_admin_user)):
    """Return subject IDs that have at least one SEO topic in the topics collection."""
    try:
        if not await is_mongo_available():
            return []
        ids = await db.topics.distinct("subject_id", {"subject_id": {"$exists": True, "$ne": ""}})
        return [sid for sid in ids if sid]
    except Exception:
        mark_mongo_down()
        return []

@router.get("/admin/content/cms-documents/assets-generated-subject-ids")
async def get_assets_generated_subject_ids(admin: dict = Depends(get_admin_user)):
    """Return subject IDs where all chapters have notes generated or content > 100 chars,
    OR where pipeline artifacts (PYQs, flashcards, blogs) exist for that subject."""
    try:
        if not await is_mongo_available():
            return []
        chapter_pipeline = [
            {"$match": {"subject_id": {"$exists": True, "$ne": ""}}},
            {"$project": {
                "subject_id": 1,
                "has_notes": {"$or": [
                    {"$eq": ["$notes_generated", True]},
                    {"$gt": [{"$strLenCP": {"$trim": {"input": {"$ifNull": ["$content", ""]}}}}, 100]}
                ]}
            }},
            {"$group": {
                "_id": "$subject_id",
                "total": {"$sum": 1},
                "with_notes": {"$sum": {"$cond": ["$has_notes", 1, 0]}}
            }},
            {"$match": {"$expr": {"$eq": ["$total", "$with_notes"]}, "total": {"$gt": 0}}}
        ]
        chapter_results, pyq_sids, fc_sids, ai_pyq_sids = await asyncio.gather(
            db.chapters.aggregate(chapter_pipeline).to_list(10000),
            db.topic_pyq_collections.distinct("subject_id", {"subject_id": {"$exists": True, "$ne": ""}}),
            db.flashcard_collections.distinct("subject_id", {"subject_id": {"$exists": True, "$ne": ""}}),
            db.ai_pyq_collections.distinct("subject_id", {"subject_id": {"$exists": True, "$ne": ""}}),
        )
        result_set = set()
        for r in chapter_results:
            if r.get("_id"):
                result_set.add(r["_id"])
        for sid in pyq_sids:
            if sid:
                result_set.add(sid)
        for sid in fc_sids:
            if sid:
                result_set.add(sid)
        for sid in ai_pyq_sids:
            if sid:
                result_set.add(sid)
        return list(result_set)
    except Exception:
        mark_mongo_down()
        return []

@router.get("/admin/content/cms-documents")
async def get_cms_documents(admin: dict = Depends(get_admin_user)):
    """Get all CMS documents for admin"""
    try:
        if not await is_mongo_available():
            return []
        docs = await db.cms_documents.find({}, {"_id": 0}).sort("updated_at", -1).limit(100).to_list(100)
        return docs
    except Exception:
        mark_mongo_down()
        return []

def _sanitize_html(raw_html: str) -> str:
    """Strip dangerous tags and event-handler attributes from HTML using nh3."""
    if not raw_html:
        return ""
    return _nh3.clean(
        raw_html,
        tags={
            "a", "abbr", "b", "blockquote", "br", "caption", "code",
            "col", "colgroup", "dd", "del", "details", "dfn", "div",
            "dl", "dt", "em", "figcaption", "figure", "h1", "h2", "h3",
            "h4", "h5", "h6", "hr", "i", "img", "ins", "kbd", "li",
            "mark", "ol", "p", "pre", "q", "s", "section", "small",
            "span", "strike", "strong", "sub", "summary", "sup",
            "table", "tbody", "td", "tfoot", "th", "thead", "tr",
            "u", "ul", "var",
        },
        attributes={
            "*": {"id", "class"},
            "a": {"href", "title", "target"},
            "img": {"src", "alt", "width", "height", "loading"},
            "td": {"colspan", "rowspan", "align"},
            "th": {"colspan", "rowspan", "align", "scope"},
            "col": {"span"},
            "colgroup": {"span"},
        },
        url_schemes={"http", "https", "mailto"},
        link_rel=None,
        strip_comments=True,
    )


@router.post("/admin/content/cms-documents")
async def create_cms_document(doc: CMSDocument, admin: dict = Depends(get_admin_user)):
    """Create new SEO-optimized CMS document with auto markdown→HTML processing"""
    doc_id = str(uuid.uuid4())
    raw_md = doc.content or ""
    content_html = _sanitize_html(doc.content_html or _md_to_html(raw_md))
    headings_json = doc.headings or _extract_headings_json(raw_md)
    word_count = len(re.sub(r'<[^>]+>', '', content_html).split())
    now = datetime.now(timezone.utc).isoformat()
    
    doc_data = {
        "id": doc_id,
        "title": doc.title,
        "content": raw_md,          # raw markdown
        "content_html": content_html,  # processed HTML
        "meta_description": doc.meta_description,
        "description": doc.description,
        "seo_tags": doc.seo_tags,
        "geo_tags": doc.geo_tags,
        "primary_keyword": doc.primary_keyword,
        "seo_slug": doc.seo_slug,
        "thumbnail_url": doc.thumbnail_url,
        "alt_text": doc.alt_text,
        "category": doc.category,
        "headings": headings_json,
        "schema_type": doc.schema_type,
        "canonical_url": doc.canonical_url,
        "status": doc.status,
        "word_count": word_count,
        "rag_processed": False,
        "created_at": now,
        "updated_at": now,
        "created_by": admin.get("email"),
    }
    
    await db.cms_documents.insert_one(doc_data)
    doc_data.pop("_id", None)
    return doc_data

@router.patch("/admin/content/cms-documents/{doc_id}")
async def update_cms_document(doc_id: str, doc: CMSDocumentUpdate, admin: dict = Depends(get_admin_user)):
    """Partial update of a CMS document — only non-None fields are written."""
    # Fetch existing doc to preserve fields not supplied in this request
    existing = await db.cms_documents.find_one({"id": doc_id}, {"_id": 0})
    if not existing:
        raise HTTPException(status_code=404, detail="Document not found")

    updates: dict = {"updated_at": datetime.now(timezone.utc).isoformat()}

    # Apply only the fields explicitly provided in the request body
    patch = doc.model_dump(exclude_none=True)

    # Content-derived fields (re-process if content is being updated)
    if "content" in patch:
        raw_md = patch["content"]
        updates["content"] = raw_md
        updates["content_html"] = _sanitize_html(patch.pop("content_html", None) or _md_to_html(raw_md))
        updates["headings"] = patch.pop("headings", None) or _extract_headings_json(raw_md)
        content_html_for_wc = updates["content_html"]
        updates["word_count"] = len(re.sub(r'<[^>]+>', '', content_html_for_wc).split())
    elif "content_html" in patch:
        updates["content_html"] = _sanitize_html(patch.pop("content_html"))
    if "headings" in patch:
        updates["headings"] = patch.pop("headings")

    # Handle is_published → status mapping
    if "is_published" in patch:
        updates["status"] = "published" if patch.pop("is_published") else "draft"

    # Copy all remaining patch fields directly
    for k, v in patch.items():
        updates[k] = v

    await db.cms_documents.update_one({"id": doc_id}, {"$set": updates})
    updated = await db.cms_documents.find_one({"id": doc_id}, {"_id": 0})
    return updated


@router.put("/admin/content/cms-documents/{doc_id}")
async def put_cms_document(doc_id: str, doc: CMSDocumentUpdate, admin: dict = Depends(get_admin_user)):
    """PUT alias for PATCH /admin/content/cms-documents/{doc_id} — partial update."""
    return await update_cms_document(doc_id, doc, admin)


@router.post("/admin/content/cms-documents/{doc_id}/publish")
async def publish_cms_document(doc_id: str, admin: dict = Depends(get_admin_user)):
    """Toggle document status between published/draft. Auto-generates JSON-LD breadcrumb on publish."""
    doc = await db.cms_documents.find_one({"id": doc_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    new_status = "published" if doc.get("status") != "published" else "draft"
    now = datetime.now(timezone.utc).isoformat()
    updates = {"status": new_status, "updated_at": now}
    if new_status == "published":
        updates["published_at"] = now
        breadcrumb_items = [{"@type": "ListItem", "position": 1, "name": "Home", "item": "https://syrabit.ai"}]
        pos = 2
        if doc.get("linked_board_name"):
            breadcrumb_items.append({"@type": "ListItem", "position": pos, "name": doc["linked_board_name"],
                                     "item": f"https://syrabit.ai/{_slugify(doc['linked_board_name'])}"})
            pos += 1
        if doc.get("linked_class_name"):
            class_path = f"https://syrabit.ai/{_slugify(doc.get('linked_board_name', ''))}/{_slugify(doc['linked_class_name'])}"
            breadcrumb_items.append({"@type": "ListItem", "position": pos, "name": doc["linked_class_name"], "item": class_path})
            pos += 1
        if doc.get("linked_subject_name"):
            subject_path = f"https://syrabit.ai/{_slugify(doc.get('linked_board_name', ''))}/{_slugify(doc.get('linked_class_name', ''))}/{_slugify(doc['linked_subject_name'])}"
            breadcrumb_items.append({"@type": "ListItem", "position": pos, "name": doc["linked_subject_name"], "item": subject_path})
            pos += 1
        breadcrumb_items.append({"@type": "ListItem", "position": pos, "name": doc.get("title", "")})
        updates["json_ld_breadcrumb"] = {
            "@context": "https://schema.org",
            "@type": "BreadcrumbList",
            "itemListElement": breadcrumb_items,
        }
        schema_type = doc.get("schema_type", "Article")
        updates["json_ld_article"] = {
            "@context": "https://schema.org",
            "@type": schema_type,
            "headline": doc.get("title", ""),
            "description": doc.get("meta_description", ""),
            "author": {"@type": "Organization", "name": "Syrabit.ai"},
            "publisher": {"@type": "Organization", "name": "Syrabit.ai"},
            "datePublished": now,
            "dateModified": now,
        }
        if doc.get("canonical_url"):
            updates["json_ld_article"]["mainEntityOfPage"] = doc["canonical_url"]
        if doc.get("thumbnail_url"):
            updates["json_ld_article"]["image"] = doc["thumbnail_url"]
    await db.cms_documents.update_one({"id": doc_id}, {"$set": updates})
    if new_status == "published" and doc.get("slug"):
        try:
            from routes.admin_advanced import _indexnow_notify_background, INDEXNOW_HOST
            _indexnow_notify_background([f"{INDEXNOW_HOST}/learn/{doc['slug']}"])
        except Exception:
            pass
    return {"status": new_status}


@router.post("/admin/content/cms-documents/{doc_id}/link-syllabus")
async def link_cms_syllabus(doc_id: str, data: dict = Body(...), admin: dict = Depends(get_admin_user)):
    """Link a CMS document to a syllabus scope. Auto-populates canonical URL and geo_tags."""
    doc = await db.cms_documents.find_one({"id": doc_id}, {"_id": 0, "id": 1})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    board_id   = data.get("board_id", "")
    class_id   = data.get("class_id", "")
    stream_id  = data.get("stream_id", "")
    subject_id = data.get("subject_id", "")
    board_doc   = await db.boards.find_one({"id": board_id},   {"_id": 0}) or {}
    class_doc   = await db.classes.find_one({"id": class_id},  {"_id": 0}) or {}
    stream_doc  = await db.streams.find_one({"id": stream_id}, {"_id": 0}) or {}
    subject_doc = await db.subjects.find_one({"id": subject_id}, {"_id": 0}) or {}
    board_name   = board_doc.get("name",   board_id)
    class_name   = class_doc.get("name",   class_id)
    stream_name  = stream_doc.get("name",  stream_id)
    subject_name = subject_doc.get("name", subject_id)
    canonical = f"/{_slugify(board_name)}/{_slugify(class_name)}/{_slugify(subject_name)}"
    geo_phrase = ", ".join(filter(None, [class_name, board_name, stream_name]))
    updates = {
        "linked_subject_id":   subject_id,
        "linked_board_id":     board_id,
        "linked_class_id":     class_id,
        "linked_stream_id":    stream_id,
        "linked_subject_name": subject_name,
        "linked_board_name":   board_name,
        "linked_class_name":   class_name,
        "linked_stream_name":  stream_name,
        "linked_scope":        f"{board_id}/{class_id}/{stream_id}/{subject_id}",
        "canonical_url":       canonical,
        "geo_tags":            geo_phrase,
        "updated_at":          datetime.now(timezone.utc).isoformat(),
    }
    await db.cms_documents.update_one({"id": doc_id}, {"$set": updates})
    logger.info(f"CMS doc {doc_id} linked to scope {board_id}/{class_id}/{stream_id}/{subject_id}")
    return {"message": "Linked to syllabus scope", "canonical_url": canonical, "geo_tags": geo_phrase,
            "board_name": board_name, "class_name": class_name, "stream_name": stream_name, "subject_name": subject_name}


@router.post("/admin/content/cms-documents/{doc_id}/revisions")
async def save_cms_revision(doc_id: str, admin: dict = Depends(get_admin_user)):
    """Create a dated draft revision duplicate of a CMS document."""
    doc = await db.cms_documents.find_one({"id": doc_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    from datetime import date as _date
    date_str  = _date.today().strftime("%Y-%m-%d")
    rev_id    = f"{doc_id}-rev-{uuid.uuid4().hex[:6]}"
    base_slug = doc.get("seo_slug", _slugify(doc.get("title", "doc")))
    rev_slug  = f"{base_slug}-rev-{date_str}"
    revision  = {
        **doc,
        "id":             rev_id,
        "title":          f"{doc.get('title', 'Untitled')} — Rev {date_str}",
        "seo_slug":       rev_slug,
        "status":         "draft",
        "is_revision":    True,
        "source_doc_id":  doc_id,
        "created_at":     datetime.now(timezone.utc).isoformat(),
        "updated_at":     datetime.now(timezone.utc).isoformat(),
    }
    revision.pop("_id", None)
    await db.cms_documents.insert_one(revision)
    logger.info(f"Revision created: {rev_id} from {doc_id}")
    return {"id": rev_id, "title": revision["title"], "seo_slug": rev_slug}


@router.post("/admin/content/extract-pdf-text")
async def extract_pdf_text(file: UploadFile = File(...), admin: dict = Depends(get_admin_user)):
    """Extract text from a PDF upload (no Supabase needed) for pasting into the editor."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files accepted")
    raw = await file.read()
    if len(raw) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (max 20 MB)")

    def _extract_sync(data: bytes):
        import io as _io
        from pypdf import PdfReader
        reader = PdfReader(_io.BytesIO(data))
        pages = []
        for page in reader.pages:
            text = page.extract_text() or ""
            if text.strip():
                pages.append(text.strip())
        extracted = "\n\n".join(pages)
        return {"text": extracted, "pages": len(reader.pages), "chars": len(extracted)}

    try:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _extract_sync, raw)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"PDF extraction failed: {e}")

@router.delete("/admin/content/cms-documents/{doc_id}")
async def delete_cms_document(doc_id: str, admin: dict = Depends(get_admin_user)):
    """Delete CMS document"""
    await db.cms_documents.delete_one({"id": doc_id})
    # Also delete from RAG index
    await db.cms_rag_chunks.delete_many({"document_id": doc_id})
    return {"message": "Document deleted"}

@router.post("/admin/content/cms-documents/{doc_id}/process-rag")
async def process_cms_rag(doc_id: str, admin: dict = Depends(get_admin_user)):
    """Process document for RAG indexing"""
    doc = await db.cms_documents.find_one({"id": doc_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Extract text content (strip HTML tags)
    import re
    text_content = re.sub(r'<[^>]+>', '', doc["content"])
    
    # Split into chunks (500-word chunks with 100-word overlap)
    words = text_content.split()
    chunk_size = 500
    overlap = 100
    chunks = []
    
    for i in range(0, len(words), chunk_size - overlap):
        chunk_words = words[i:i + chunk_size]
        if chunk_words:
            chunk_text = ' '.join(chunk_words)
            chunks.append({
                "id": str(uuid.uuid4()),
                "document_id": doc_id,
                "document_title": doc["title"],
                "chunk_text": chunk_text,
                "chunk_index": len(chunks),
                "word_count": len(chunk_words),
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
    
    # Delete old chunks
    await db.cms_rag_chunks.delete_many({"document_id": doc_id})
    
    # Insert new chunks
    if chunks:
        await db.cms_rag_chunks.insert_many(chunks)
    
    # Mark document as processed
    result = await db.cms_documents.update_one(
        {"id": doc_id},
        {"$set": {"rag_processed": True, "chunk_count": len(chunks)}}
    )
    
    if result.matched_count == 0:
        logger.warning(f"CMS RAG: Document {doc_id} not found for RAG status update")
    
    logger.info(f"CMS RAG: Processed document {doc_id} into {len(chunks)} chunks")
    return {"message": f"Processed {len(chunks)} chunks", "chunks": len(chunks)}

@router.post("/admin/upload/image")
async def upload_image(file: UploadFile = File(...), admin: dict = Depends(get_admin_user)):
    """Upload image — returns a base64 data URL for immediate use."""
    import base64 as _b64
    allowed_types = {"image/jpeg", "image/png", "image/gif", "image/webp", "image/svg+xml"}
    content_type = (file.content_type or "").lower()
    if content_type not in allowed_types:
        raise HTTPException(400, f"Unsupported file type '{content_type}'. Use JPEG, PNG, GIF, WebP, or SVG.")
    max_size = 5 * 1024 * 1024  # 5 MB
    raw = await file.read()
    if len(raw) > max_size:
        raise HTTPException(413, "Image too large — maximum size is 5 MB.")
    b64 = _b64.b64encode(raw).decode()
    data_url = f"data:{content_type};base64,{b64}"
    # Also store in MongoDB for future retrieval
    image_id = str(uuid.uuid4())[:12]
    try:
        await db.uploaded_images.insert_one({
            "id": image_id,
            "filename": file.filename,
            "content_type": content_type,
            "size": len(raw),
            "data_url": data_url,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
            "uploaded_by": admin.get("email", "admin"),
        })
    except Exception:
        pass  # data_url still returned even if MongoDB insert fails
    return {"url": data_url, "id": image_id, "filename": file.filename}

# Public CMS endpoints (no auth required)
_PUBLIC_CMS_FILTER = {
    "doc_type": {"$ne": "personalized"},
    "meta.is_private": {"$ne": True},
}

@router.get("/content/cms-library")
async def get_public_cms_library():
    """Get published CMS documents for public library (personalized/private plans excluded)."""
    try:
        if not await is_mongo_available():
            return []
        docs = await db.cms_documents.find(
            {"status": "published", **_PUBLIC_CMS_FILTER},
            {"_id": 0, "content": 0}
        ).sort("updated_at", -1).limit(50).to_list(50)
        return docs
    except Exception:
        mark_mongo_down()
        return []

@router.get("/content/cms-documents/{doc_id}")
async def get_public_cms_document(doc_id: str):
    """Get single CMS document for public view (PYQs and notes are freely scrapable).
    Personalized or private documents are never returned — use the authenticated
    /cms/{user_id}/{slug} endpoint for those."""
    try:
        if not await is_mongo_available():
            raise HTTPException(status_code=503, detail="Content service unavailable")
        doc = await db.cms_documents.find_one(
            {
                "$or": [{"id": doc_id}, {"seo_slug": doc_id}],
                "status": "published",
                **_PUBLIC_CMS_FILTER,
            },
            {"_id": 0}
        )
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        return doc
    except HTTPException:
        raise
    except Exception:
        mark_mongo_down()
        raise HTTPException(status_code=503, detail="Content service unavailable")


# ──────────────────────────────────────────────────────────────────────────────
# PERSONALIZED CMS — private, paid, un-scrapable study plans
# GET  /cms/{user_id}/{slug}   — view a personal study plan (auth + paid plan)
# GET  /cms/{user_id}          — list all personal plans for user
# POST /cms/personalize        — generate a new personalized plan via Gemini
# ──────────────────────────────────────────────────────────────────────────────

def _sanitize_mongo_doc(doc):
    """Convert non-JSON-serializable MongoDB field values to strings."""
    if not isinstance(doc, dict):
        return doc
    cleaned = {}
    for k, v in doc.items():
        if isinstance(v, datetime):
            cleaned[k] = v.isoformat()
        elif hasattr(v, '__str__') and type(v).__name__ == 'ObjectId':
            cleaned[k] = str(v)
        elif isinstance(v, dict):
            cleaned[k] = _sanitize_mongo_doc(v)
        elif isinstance(v, list):
            cleaned[k] = [
                _sanitize_mongo_doc(i) if isinstance(i, dict)
                else i.isoformat() if isinstance(i, datetime)
                else str(i) if hasattr(i, '__str__') and type(i).__name__ == 'ObjectId'
                else i
                for i in v
            ]
        else:
            cleaned[k] = v
    return cleaned


@router.get("/cms/posts")
async def list_cms_posts(
    board:      Optional[str] = None,
    class_slug: Optional[str] = None,
    subject_id: Optional[str] = None,
    limit:      int = 20,
    skip:       int = 0,
):
    """Paginated published cms content for Library infinite scroll — reads from cms_documents."""
    try:
        if not await is_mongo_available():
            return JSONResponse(content={"items": [], "total": 0})
        query: dict = {"status": "published", "subject_id": {"$exists": True, "$ne": None}}
        if board:      query["board_slug"]  = board
        if class_slug: query["class_slug"]  = class_slug
        if subject_id: query["subject_id"]  = subject_id
        limit = min(max(limit, 1), 50)
        try:
            items = await asyncio.wait_for(
                db.cms_documents.find(
                    query, {"_id": 0, "merged_md": 0, "content": 0}
                ).sort("updated_at", -1).skip(skip).limit(limit).to_list(limit),
                timeout=10.0,
            )
            total = await asyncio.wait_for(
                db.cms_documents.count_documents(query),
                timeout=10.0,
            )
        except asyncio.TimeoutError:
            logger.warning("CMS posts query timed out after 10s")
            return JSONResponse(content={"items": [], "total": 0})
        items = [_sanitize_mongo_doc(item) for item in items]
        return JSONResponse(content={"items": items, "total": total})
    except Exception as exc:
        logger.warning(f"CMS posts endpoint error: {exc}")
        mark_mongo_down()
        return JSONResponse(content={"items": [], "total": 0})


async def _merge_subject_html(subject_id: str) -> str:
    """Build merged HTML from per-chapter content_html fields (set by Format Notes).
    Returns empty string if no chapters have content_html."""
    try:
        from seo_engine import _format_content_html
        chapters = await db.chapters.find(
            {"subject_id": subject_id}, {"_id": 0}
        ).sort("chapter_number", 1).to_list(100)
        if not chapters:
            return ""

        has_any_html = any(ch.get("content_html") for ch in chapters)
        if not has_any_html:
            has_raw = any((ch.get("content") or "").strip() for ch in chapters)
            if not has_raw:
                return ""
            parts = []
            subject = await db.subjects.find_one({"id": subject_id}, {"_id": 0})
            subj_name = (subject or {}).get("name", "Subject")
            parts.append(f"<h1>{subj_name}</h1>")
            for ch in chapters:
                raw = (ch.get("content") or "").strip()
                if not raw:
                    continue
                num = ch.get("chapter_number", "")
                title = ch.get("title", "")
                heading = f"Chapter {num}: {title}" if num else title
                parts.append(f"<h2>{heading}</h2>")
                formatted = _format_content_html(raw)
                if formatted:
                    parts.append(formatted)
            return "\n".join(parts) if len(parts) > 1 else ""

        parts = []
        subject = await db.subjects.find_one({"id": subject_id}, {"_id": 0})
        subj_name = (subject or {}).get("name", "Subject")
        parts.append(f"<h1>{subj_name}</h1>")
        for ch in chapters:
            num = ch.get("chapter_number", "")
            title = ch.get("title", "")
            heading = f"Chapter {num}: {title}" if num else title
            parts.append(f"<h2>{heading}</h2>")
            ch_html = (ch.get("content_html") or "").strip()
            if ch_html:
                parts.append(ch_html)
            elif (ch.get("content") or "").strip():
                formatted = _format_content_html(ch["content"])
                parts.append(formatted if formatted else f"<p>{ch['content'][:500]}</p>")
        return "\n".join(parts)
    except Exception as exc:
        logger.warning(f"_merge_subject_html({subject_id}): {exc}")
        return ""


@router.get("/cms/post/{subject_id}")
async def get_cms_post_by_subject(subject_id: str):
    """Get merged blog post for a subject (public). Returns cache or generates on-the-fly."""
    try:
        if not await is_mongo_available():
            raise HTTPException(status_code=503, detail="Content service unavailable")
        doc = await db.cms_documents.find_one(
            {"subject_id": subject_id, "status": "published"},
            {"_id": 0, "merged_md": 0}
        )
        if doc:
            return {
                "subject_id": subject_id,
                "title":      doc.get("title", ""),
                "subject_merged_html": doc.get("content", ""),
                "headings":   doc.get("headings", ""),
                "word_count": doc.get("word_count", 0),
                "status":     "published",
                "seo_slug":   doc.get("seo_slug", ""),
            }
        content_html = await _merge_subject_html(subject_id)
        if not content_html:
            merged_md = await merge_subject_content(subject_id)
            if not merged_md:
                raise HTTPException(status_code=404, detail="Subject not found or empty")
            content_html = _blog_md_to_html(merged_md)
        headings     = "[]"
        word_count   = len(re.sub(r'<[^>]+>', '', content_html).split())
        subject      = await db.subjects.find_one({"id": subject_id}, {"_id": 0})
        return {
            "subject_id": subject_id,
            "title":      (subject.get("name", "") if subject else ""),
            "subject_merged_html": content_html,
            "headings":   headings,
            "word_count": word_count,
            "status":     "live",
        }
    except HTTPException:
        raise
    except Exception:
        mark_mongo_down()
        raise HTTPException(status_code=503, detail="Content service unavailable")


_PLAN_IS_PAID = {"starter", "pro"}

@router.get("/cms/{user_id}")
async def list_personal_plans(user_id: str, response: Response, user: dict = Depends(get_current_user)):
    """List all personalized study plans that belong to this user (paid plan required)."""
    if str(user["id"]) != str(user_id):
        raise HTTPException(403, "Access denied")
    if user.get("plan", "free") not in _PLAN_IS_PAID:
        raise HTTPException(402, "Upgrade to Starter or Pro to access personalized study plans.")
    if not await is_mongo_available():
        raise HTTPException(503, "Content service unavailable")
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    response.headers["Cache-Control"] = "private, no-store"
    docs = await db.cms_documents.find(
        {"user_id": user_id, "doc_type": "personalized", "status": "published"},
        {"_id": 0, "id": 1, "slug": 1, "title": 1, "created_at": 1, "subject_name": 1}
    ).sort("created_at", -1).limit(50).to_list(50)
    return {"plans": docs, "total": len(docs)}

@router.get("/cms/{user_id}/{slug}")
async def get_personal_plan(
    user_id: str,
    slug: str,
    response: Response,
    user: dict = Depends(get_current_user),
):
    """Fetch a single personalized study plan. Auth + paid plan required. No-index headers applied."""
    if str(user["id"]) != str(user_id):
        raise HTTPException(403, "This plan belongs to another account.")
    if user.get("plan", "free") not in _PLAN_IS_PAID:
        raise HTTPException(
            402,
            detail={
                "error": "upgrade_required",
                "message": "Personalized study plans require Starter or Pro.",
                "upgrade_url": "/pricing",
            }
        )
    if not await is_mongo_available():
        raise HTTPException(503, "Content service unavailable")
    doc = await db.cms_documents.find_one(
        {"user_id": user_id, "$or": [{"id": slug}, {"slug": slug}], "doc_type": "personalized"},
        {"_id": 0}
    )
    if not doc:
        raise HTTPException(404, "Study plan not found.")
    if response is not None:
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["Cache-Control"] = "private, no-store"
    return doc


class PersonalizePlanRequest(BaseModel):
    subject_name: str = ""
    chapter_name: str = ""
    weak_topics: List[str] = []
    context: str = ""          # e.g. "I'm weak in Motion and Gravitation"
    days: int = 7              # sprint length


@router.post("/cms/personalize")
async def generate_personalized_plan(body: PersonalizePlanRequest, user: dict = Depends(get_current_user)):
    """Generate a personalized study plan using Gemini and store it as a private CMS doc."""
    if user.get("plan", "free") not in _PLAN_IS_PAID:
        raise HTTPException(
            402,
            detail={
                "error": "upgrade_required",
                "message": "Personalized plans require a paid plan (Starter/Pro).",
                "upgrade_url": "/pricing",
            }
        )
    if not await is_mongo_available():
        raise HTTPException(503, "Content service unavailable")

    user_id = str(user["id"])
    subject  = body.subject_name or "your subject"
    chapter  = body.chapter_name or ""
    days     = max(1, min(body.days, 30))
    weak     = ", ".join(body.weak_topics) if body.weak_topics else body.context or "general gaps"

    prompt = (
        f"You are a personalised exam coach for AHSEC/SEBA students in Assam (NEP 2020).\n"
        f"Student: {user.get('name', 'Student')} | Subject: {subject}"
        + (f" | Chapter focus: {chapter}" if chapter else "") +
        f"\nWeak areas identified: {weak}\n\n"
        f"Create a detailed, actionable {days}-day study sprint plan:\n"
        f"- Day-by-day schedule (topics, activities, timed blocks)\n"
        f"- Specific PYQ practice recommendations from AHSEC board papers\n"
        f"- Short-answer and long-answer question targets per day\n"
        f"- Revision checkpoints and self-assessment tips\n"
        f"- Exam-day strategy summary\n\n"
        f"Format: Clean Markdown with ## Day headers. Be specific and motivating."
    )

    try:
        plan_md = await call_llm_api_content([{"role": "user", "content": prompt}], max_tokens=2000)
    except Exception as e:
        logger.error(f"Personalize plan generation failed: {e}")
        raise HTTPException(500, "Plan generation failed. Please try again.")

    plan_html = _md_to_html(plan_md)
    word_count = len(plan_md.split())
    slug_base  = re.sub(r"[^a-z0-9]+", "-", f"{subject} {days}-day plan".lower()).strip("-")
    slug       = f"{slug_base}-{int(time.time())}"
    doc_id     = str(uuid.uuid4())
    now        = datetime.now(timezone.utc).isoformat()
    title      = f"Your {days}-Day {subject.title()} Sprint" + (f": {chapter}" if chapter else "")

    doc = {
        "id":           doc_id,
        "slug":         slug,
        "user_id":      user_id,
        "doc_type":     "personalized",
        "category":     "study_plan",
        "title":        title,
        "content":      plan_md,
        "content_html": plan_html,
        "word_count":   word_count,
        "subject_name": subject,
        "chapter_name": chapter,
        "weak_topics":  body.weak_topics,
        "days":         days,
        "status":       "published",
        "created_at":   now,
        "updated_at":   now,
        "meta": {
            "robots":    "noindex, nofollow",
            "is_private": True,
        },
    }

    await db.cms_documents.insert_one(doc)
    doc.pop("_id", None)
    logger.info(f"Personalized plan generated for user {user_id}: {doc_id}")
    return {
        "id":    doc_id,
        "slug":  slug,
        "title": title,
        "url":   f"/cms/{user_id}/{slug}",
        "doc":   {k: v for k, v in doc.items() if k != "_id"},
    }


@router.post("/admin/cms/merge/{subject_id}")
async def admin_merge_subject(subject_id: str, admin: dict = Depends(get_admin_user)):
    """Merge subject chapters+chunks → cms_documents. Returns word count + headings."""
    if not await is_mongo_available():
        raise HTTPException(status_code=503, detail="Content service unavailable")
    merged_md = await merge_subject_content(subject_id)
    if not merged_md:
        raise HTTPException(status_code=404, detail="Subject not found or has no chapters")
    content_html  = _blog_md_to_html(merged_md)
    headings_json = _extract_headings_json(merged_md)
    word_count    = len(re.sub(r'<[^>]+>', '', content_html).split())
    subject       = await db.subjects.find_one({"id": subject_id}, {"_id": 0})
    now           = datetime.now(timezone.utc).isoformat()
    subject_name  = subject.get("name", "") if subject else ""
    subject_slug  = subject.get("slug", subject_id) if subject else subject_id

    # ── Primary: write to cms_documents ─────────────────────────────────────
    cms_doc_data = {
        "subject_id":          subject_id,
        "title":               subject_name,
        "seo_slug":            subject_slug,
        "board_slug":          (subject.get("board_slug", "") if subject else ""),
        "class_slug":          (subject.get("class_slug", "") if subject else ""),
        "content":             content_html,
        "merged_md":           merged_md,
        "headings":            headings_json,
        "word_count":          word_count,
        "status":              "published",
        "schema_type":         "Article",
        "primary_keyword":     subject_name,
        "updated_at":          now,
    }
    existing_doc = await db.cms_documents.find_one({"subject_id": subject_id}, {"_id": 0, "id": 1})
    if existing_doc:
        await db.cms_documents.update_one(
            {"subject_id": subject_id},
            {"$set": cms_doc_data},
        )
        doc_id = existing_doc.get("id", "")
    else:
        cms_doc_data["id"] = str(uuid.uuid4())
        cms_doc_data["created_at"] = now
        await db.cms_documents.insert_one(cms_doc_data)
        doc_id = cms_doc_data["id"]

    headings = json.loads(headings_json) if headings_json else []
    return {
        "subject_id":  subject_id,
        "doc_id":      doc_id,
        "word_count":  word_count,
        "headings":    headings,
        "slug":        subject_slug,
        "title":       subject_name,
        "merged_md":   merged_md,
        "content":     content_html,
        "board_slug":  subject.get("board_slug", "")   if subject else "",
        "class_slug":  subject.get("class_slug", "")   if subject else "",
        "class_name":  subject.get("class_name", "")   if subject else "",
        "stream_name": subject.get("stream_name", "")  if subject else "",
        "stream_slug": subject.get("stream_slug", "")  if subject else "",
    }


@router.post("/admin/cms/merge-by-chapter/{subject_id}")
async def admin_merge_by_chapter(subject_id: str, admin: dict = Depends(get_admin_user)):
    """Create one CMS document per chapter — thick, syllabus-focused pages."""
    if not await is_mongo_available():
        raise HTTPException(status_code=503, detail="Content service unavailable")
    subject = await db.subjects.find_one({"id": subject_id}, {"_id": 0})
    if not subject:
        raise HTTPException(status_code=404, detail="Subject not found")
    chapters = await db.chapters.find(
        {"subject_id": subject_id}, {"_id": 0}
    ).sort("chapter_number", 1).to_list(100)
    if not chapters:
        raise HTTPException(status_code=404, detail="Subject has no chapters")

    subject_name = subject.get("name", "")
    board_slug = subject.get("board_slug", "")
    class_slug = subject.get("class_slug", "")
    class_name = subject.get("class_name", "")
    stream_name = subject.get("stream_name", "")
    stream_slug = subject.get("stream_slug", "")
    now = datetime.now(timezone.utc).isoformat()
    created_docs = []

    for chapter in chapters:
        ch_id = chapter.get("id", "")
        ch_num = chapter.get("chapter_number", "")
        ch_title = chapter.get("title", "")
        ch_heading = f"Chapter {ch_num}: {ch_title}" if ch_num else ch_title

        parts = [f"# {ch_heading}\n\n"]
        ch_desc = (chapter.get("description") or "").strip()
        if ch_desc:
            parts.append(f"{ch_desc}\n\n")

        cks = await db.chunks.find(
            {"chapter_id": ch_id}, {"_id": 0}
        ).sort("order", 1).to_list(500)
        seen_content = set()
        if ch_desc:
            seen_content.add(ch_desc.lower().strip()[:300])
        for ck in cks:
            content = (ck.get("content") or "").strip()
            if not content:
                continue
            content_key = content.lower().strip()[:300]
            if content_key in seen_content:
                continue
            seen_content.add(content_key)
            ctype = (ck.get("type") or "").lower()
            if ctype == "pyq":
                parts.append(f"> **Past Year Question**\n>\n> {content}\n\n")
            elif ctype == "summary":
                parts.append(f"### Summary\n\n{content}\n\n")
            elif ctype == "formula":
                parts.append(f"### Formula\n\n{content}\n\n")
            else:
                parts.append(f"{content}\n\n")

        chapter_md = preprocess_markdown("".join(parts))
        if len(chapter_md.strip()) < 50:
            continue

        content_html = _blog_md_to_html(chapter_md)
        headings_json = _extract_headings_json(chapter_md)
        word_count = len(re.sub(r'<[^>]+>', '', content_html).split())
        ch_slug = _slugify(f"{ch_title} {subject_name} chapter {ch_num}" if ch_num else f"{ch_title} {subject_name}")

        cms_doc_data = {
            "subject_id":      subject_id,
            "chapter_id":      ch_id,
            "title":           f"{ch_title} — {subject_name}",
            "seo_slug":        ch_slug,
            "board_slug":      board_slug,
            "class_slug":      class_slug,
            "content":         content_html,
            "merged_md":       chapter_md,
            "headings":        headings_json,
            "word_count":      word_count,
            "status":          "draft",
            "schema_type":     "Article",
            "primary_keyword": f"{ch_title} {subject_name} Assamboard notes",
            "updated_at":      now,
        }
        existing = await db.cms_documents.find_one(
            {"subject_id": subject_id, "chapter_id": ch_id}, {"_id": 0, "id": 1}
        )
        if existing:
            await db.cms_documents.update_one(
                {"subject_id": subject_id, "chapter_id": ch_id},
                {"$set": cms_doc_data},
            )
            doc_id = existing.get("id", "")
        else:
            doc_id = str(uuid.uuid4())
            cms_doc_data["id"] = doc_id
            cms_doc_data["created_at"] = now
            await db.cms_documents.insert_one(cms_doc_data)

        created_docs.append({
            "doc_id":     doc_id,
            "chapter_id": ch_id,
            "title":      cms_doc_data["title"],
            "seo_slug":   ch_slug,
            "word_count": word_count,
            "merged_md":  chapter_md,
        })

    logger.info(f"Chapter-wise merge: {len(created_docs)} docs for subject {subject_id}")
    return {
        "subject_id":   subject_id,
        "subject_name": subject_name,
        "board_slug":   board_slug,
        "class_slug":   class_slug,
        "class_name":   class_name,
        "stream_name":  stream_name,
        "stream_slug":  stream_slug,
        "chapters":     created_docs,
        "total":        len(created_docs),
    }


@router.post("/admin/content/regenerate-sitemap")
async def regenerate_sitemap(admin: dict = Depends(get_admin_user)):
    """Regenerate sitemap.xml — reads from cms_documents only."""
    try:
        sitemap_entries = []
        # All published CMS documents (standalone + subject-merged)
        docs = await db.cms_documents.find(
            {"status": "published"},
            {"_id": 0, "seo_slug": 1, "id": 1, "category": 1, "subject_id": 1, "updated_at": 1}
        ).to_list(3000)
        for doc in docs:
            slug = doc.get("seo_slug") or doc.get("id", "")
            # Subject-merged docs use /subject/ path; standalone blogs use /learn/
            if doc.get("subject_id") and not doc.get("category"):
                path = f"/subject/{doc.get('subject_id', slug)}"
                priority = "0.7"
            else:
                path = f"/learn/{slug}"
                priority = "0.8"
            sitemap_entries.append({
                "url":     path,
                "lastmod": doc.get("updated_at", ""),
                "priority": priority,
            })
        logger.info(f"Sitemap regenerated: {len(sitemap_entries)} entries")
        return {"message": f"Sitemap generated with {len(sitemap_entries)} entries", "count": len(sitemap_entries)}
    except Exception as e:
        logger.error(f"Sitemap generation error: {e}")
        raise HTTPException(status_code=500, detail="Sitemap generation failed")


# ─────────────────────────────────────────────
# PDF DOCUMENT UPLOAD & VIEWER
# ─────────────────────────────────────────────

@router.post("/admin/content/upload-pdf")
async def upload_pdf_document(
    file: UploadFile = File(...),
    subject_id: str = Form(...),
    title: str = Form(None),
    admin: dict = Depends(get_admin_user)
):
    """
    Upload PDF document for a subject to Supabase Storage.
    Extracts text for RAG and stores PDF URL from Supabase.
    """
    # Validate Supabase is configured
    if not supa:
        raise HTTPException(status_code=503, detail="Supabase storage not configured")
    
    # Validate file type
    if not file.filename.endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")
    
    # Read file content
    content = await file.read()
    file_size = len(content)
    
    # Enforce size limit (10MB)
    if file_size > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="PDF file too large (max 10MB)")
    
    # Extract text from PDF for RAG
    extracted_text = ""
    page_count = 0
    is_scanned = False
    
    try:
        from pypdf import PdfReader
        import io
        
        pdf_reader = PdfReader(io.BytesIO(content))
        page_count = len(pdf_reader.pages)
        
        for page in pdf_reader.pages:
            extracted_text += page.extract_text() + "\n"
        
        # Clean extracted text
        extracted_text = extracted_text.strip()
        
        # Check if this is a scanned document (image-based PDF)
        if len(extracted_text) < 50:
            is_scanned = True
            extracted_text = f"[Scanned Document - {file.filename}]\nThis is an image-based PDF (scanned question paper or document). Text extraction not available. OCR may be needed for text search."
            logger.info(f"Scanned/image-based PDF detected: {file.filename}")
        
    except Exception as e:
        logger.error(f"PDF processing failed: {e}")
        raise HTTPException(status_code=400, detail=f"Failed to process PDF: {str(e)}")
    
    # Upload to Supabase Storage
    try:
        # Create unique filename with timestamp
        import time
        timestamp = int(time.time())
        safe_filename = file.filename.replace(' ', '_').replace('/', '_')
        storage_path = f"pdfs/{subject_id}/{timestamp}_{safe_filename}"
        
        # Ensure bucket exists (create if not)
        try:
            supa.storage.get_bucket("study-materials")
        except:
            try:
                supa.storage.create_bucket("study-materials", options={"public": True})
                logger.info("Created 'study-materials' bucket")
            except Exception as bucket_err:
                logger.warning(f"Bucket creation failed (may already exist): {bucket_err}")
        
        # Upload file to Supabase Storage
        response = supa.storage.from_("study-materials").upload(
            path=storage_path,
            file=content,
            file_options={
                "content-type": "application/pdf",
                "cache-control": "3600",
                "upsert": "false"
            }
        )
        
        # Get public URL
        pdf_url = supa.storage.from_("study-materials").get_public_url(storage_path)
        
        logger.info(f"✅ PDF uploaded to Supabase: {storage_path}")
        
    except Exception as storage_err:
        logger.error(f"Supabase storage upload failed: {storage_err}")
        raise HTTPException(status_code=500, detail=f"Failed to upload to storage: {str(storage_err)}")
    
    # Create document record in MongoDB
    doc_id = str(uuid.uuid4())
    doc_title = title or file.filename
    
    document = {
        "id": doc_id,
        "subject_id": subject_id,
        "title": doc_title,
        "file_name": file.filename,
        "file_size": file_size,
        "content_type": "application/pdf",
        "pdf_url": pdf_url,  # Supabase Storage URL
        "storage_path": storage_path,  # For deletion
        "extracted_text": extracted_text,  # For RAG (or placeholder for scanned)
        "is_scanned": is_scanned,  # Flag for image-based PDFs
        "page_count": page_count,
        "uploaded_by": admin.get("email"),
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }
    
    await db.content_uploads.insert_one(document)
    
    # Update subject to mark it has a document
    await db.subjects.update_one(
        {"id": subject_id},
        {"$set": {"has_document": True}}
    )
    
    logger.info(f"✅ PDF metadata saved: {file.filename} for subject {subject_id} ({file_size} bytes, {page_count} pages, scanned: {is_scanned})")
    
    return {
        "document_id": doc_id,
        "title": doc_title,
        "file_name": file.filename,
        "file_size": file_size,
        "page_count": page_count,
        "pdf_url": pdf_url,
        "is_scanned": is_scanned,
        "text_length": len(extracted_text),
        "message": "PDF uploaded successfully to Supabase Storage" + (" (scanned document - no text extracted)" if is_scanned else "")
    }


@router.get("/content/documents/{document_id}")
async def get_document(document_id: str):
    """
    Get document details including PDF URL.
    Supports both legacy base64 and new Supabase Storage URLs.
    """
    try:
        if not await is_mongo_available():
            raise HTTPException(status_code=503, detail="Content service unavailable")
        doc = await db.content_uploads.find_one({"id": document_id}, {"_id": 0})
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        return doc
    except HTTPException:
        raise
    except Exception:
        mark_mongo_down()
        raise HTTPException(status_code=503, detail="Content service unavailable")


@router.get("/content/subject-documents/{subject_id}")
async def get_subject_documents(subject_id: str, include_pdf: bool = False):
    """
    Get all documents for a subject.
    """
    try:
        if not await is_mongo_available():
            return []
        projection = {"_id": 0}
        if not include_pdf:
            projection["extracted_text"] = 0
            projection["pdf_data_url"] = 0
            projection["pdf_url"] = 0
        else:
            projection["extracted_text"] = 0
        
        docs = await db.content_uploads.find(
            {"subject_id": subject_id},
            projection
        ).to_list(20)
        return docs
    except Exception:
        mark_mongo_down()
        return []


@router.delete("/admin/content/documents/{document_id}")
async def delete_document(document_id: str, admin: dict = Depends(get_admin_user)):
    """Delete uploaded document from both MongoDB and Supabase Storage"""
    # Get document first to get storage path
    doc = await db.content_uploads.find_one({"id": document_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Delete from Supabase Storage if it exists there
    if doc.get("storage_path") and supa:
        try:
            supa.storage.from_("study-materials").remove([doc["storage_path"]])
            logger.info(f"✅ Deleted PDF from Supabase: {doc['storage_path']}")
        except Exception as e:
            logger.warning(f"Failed to delete from Supabase storage: {e}")
    
    # Delete from MongoDB
    result = await db.content_uploads.delete_one({"id": document_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Document not found")
    
    return {"message": "Document deleted successfully from both storage and database"}


# ─────────────────────────────────────────────
# ENHANCED HEALTH
# ─────────────────────────────────────────────
import time as _time_mod


@router.get("/ready", response_model=ReadyOut)
async def readiness():
    """Legacy readiness route — preserved for backwards compatibility
    with any external monitor still pointing at /api/ready. New
    integrations should use /api/readyz, which consults the
    snapshot cache instead of issuing per-request I/O."""
    checks = {"mongodb": False, "postgresql": False}
    try:
        if db is not None:
            await db.command("ping")
            checks["mongodb"] = True
    except Exception:
        pass
    try:
        if deps.pg_pool:
            async with deps.pg_pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            checks["postgresql"] = True
    except Exception:
        pass
    all_ok = all(checks.values())
    return JSONResponse(
        status_code=200 if all_ok else 503,
        content={"status": "ready" if all_ok else "degraded", "checks": checks},
    )


# Task #848 — split liveness from readiness from full health.
#
# Why three endpoints?
#
#   /api/livez   — "is the Python process alive and serving HTTP?"
#                  Zero I/O. Returns in <1 ms. Railway's restart probe
#                  hits this — a slow Mongo or Vertex outage must NOT
#                  cause Railway to recycle the pod, because that just
#                  drops in-flight chat traffic for no reason.
#
#   /api/readyz  — "are critical dependencies usable right now?"
#                  Reads from the snapshot cache (5–10 s TTL,
#                  single-flight). 200 if mongo + pg are ok, 503
#                  otherwise. Vertex is reported but does NOT flip the
#                  HTTP code because we have an LLM fallback chain.
#                  Suitable for load-balancer "should I send traffic
#                  here?" decisions.
#
#   /api/health  — full diagnostic dump (versions, latencies, latency
#                  histogram, every dep). Same data, also from cache.
#                  This is the human / dashboard endpoint.
#
# All three are exempt from origin-secret auth, rate-limiting, and
# request tracking (see middleware.py and tracing.py).
@router.get("/livez")
async def liveness():
    """Liveness — process is up and the event loop can serve a
    request. NO dependency I/O. Target latency p99 < 50 ms."""
    return JSONResponse(
        status_code=200,
        content={
            "status": "alive",
            "service": "Syrabit.ai API",
            "uptime_seconds": int(_time_mod.time() - _startup_time),
        },
        # No cache headers — even at 1 req/s this is cheaper than the
        # round-trip to revalidate, and we never want a CDN to mask
        # a process that's actually wedged.
        headers={"Cache-Control": "no-store"},
    )


@router.get("/readyz")
async def readyz():
    """Readiness — should this instance receive traffic right now?
    Reads cached probe results (TTL 5–10 s, single-flight per dep).
    Returns 503 only when a *critical* dependency (Mongo or Postgres)
    is down — Vertex / LLM degradation is reported in the body but
    does not flip the HTTP code, because the chat layer falls back
    through Workers AI / Sarvam / Groq / Cerebras."""
    snapshot = await health_snapshot_cache.get_all()
    mongo = snapshot.get("mongodb", {"status": "unknown"})
    pg = snapshot.get("postgresql", {"status": "unknown"})
    cf_cache_dep = snapshot.get("cloudflare_cache", {"status": "unknown"})
    razorpay_dep = snapshot.get("razorpay", {"status": "unknown"})

    # Vertex/LLM blocks come from their own bg-probe caches, not the
    # snapshot cache, because they refresh on a much longer interval
    # (LLM probe loop is 5 min; vertex probe loop is independent).
    vertex_block, vertex_ok = _vertex_block_for_health()

    # Task #135 — surface mTLS misconfiguration in readyz so any load balancer
    # or health-monitor that polls this endpoint gets a 503 rather than a false
    # "ready" while the origin is actually unprotected.
    import middleware as _middleware_mod
    mtls_ok = not _middleware_mod.MTLS_MISCONFIGURED
    mtls_check = (
        {"status": "ok"}
        if mtls_ok
        else {
            "status": "misconfigured",
            "detail": (
                "ENFORCE_MTLS=true but ORIGIN_SHARED_SECRET is not set — "
                "mTLS enforcement is INACTIVE and the origin is UNPROTECTED. "
                "Set ORIGIN_SHARED_SECRET or unset ENFORCE_MTLS."
            ),
        }
    )

    critical_ok = (
        (mongo.get("status") == "ok")
        and (pg.get("status") == "ok")
        and mtls_ok
    )
    body = {
        "status": "ready" if critical_ok else "degraded",
        "checks": {
            "mongodb": mongo,
            "postgresql": pg,
            "cloudflare_cache": cf_cache_dep,
            "razorpay": razorpay_dep,
            "vertex": vertex_block,
            "mtls_config": mtls_check,
        },
    }
    return JSONResponse(
        status_code=200 if critical_ok else 503,
        content=body,
        headers={"Cache-Control": "no-store"},
    )

@router.get("/health", response_model=HealthOut)
async def health():
    try:
        return await _health_inner()
    except Exception as exc:
        logging.getLogger(__name__).error(f"Health check failed: {exc}")
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "version": "2.0.0",
                "service": "Syrabit.ai API",
                "error": str(exc),
            },
        )

async def _health_inner():
    """Full /api/health dump.

    Task #848 — every per-dependency probe goes through
    ``health_snapshot_cache`` now, so this handler is an O(1)
    dict-walk in the steady state instead of ~5 round-trips to
    Mongo/PG/Redis/Mongo-again-for-Razorpay. Net effect on
    production p50: ~280 ms → ~5 ms.
    """
    _ensure_llm_health_probe()

    snapshot = await health_snapshot_cache.get_all()
    mongo_dep = snapshot.get("mongodb", {"status": "unknown", "latencyMs": 0})
    pg_dep = snapshot.get("postgresql", {"status": "unknown", "latencyMs": 0})
    cf_cache_dep = snapshot.get("cloudflare_cache", {"status": "unknown", "latencyMs": 0})
    razorpay_dep = snapshot.get("razorpay", {"status": "unknown"})

    kv_ok = mongo_dep.get("status") == "ok"
    kv_latency = mongo_dep.get("latencyMs", 0)
    mongo_status = "ok" if kv_ok else ("unavailable" if mongo_dep.get("status") == "error" else mongo_dep.get("status", "unavailable"))

    pg_ok = pg_dep.get("status") == "ok"
    pg_latency = pg_dep.get("latencyMs", 0)

    cf_cache_status = cf_cache_dep.get("status", "unknown")
    cf_cache_latency = cf_cache_dep.get("latencyMs", 0)

    rp_status = razorpay_dep.get("status", "not_configured")

    llm_status = "not_configured"
    llm_latency = 0
    if _LLM_PROVIDERS:
        cached_llm = _llm_health_cache.get("data")
        if cached_llm:
            llm_status = cached_llm.get("status", "degraded")
            llm_latency = cached_llm.get("latencyMs", 0)
        else:
            llm_status = "degraded"
            llm_latency = 0

    # Task #691 — fold the cached Vertex/Gemini health probe (Task #678)
    # into /health so on-call has a single page instead of remembering
    # to also poll /healthz/ai. ``unknown`` (probe hasn't run yet) is
    # treated as non-degrading to avoid flapping right after boot;
    # ``unhealthy`` and ``stale`` flip the aggregate status to
    # ``degraded`` the same way Mongo / Postgres outages do.
    vertex_block, vertex_ok = _vertex_block_for_health()

    critical_ok = kv_ok and pg_ok and vertex_ok
    overall = "ok" if critical_ok else "degraded"

    from rag import _chat_latencies
    _lat_hist = {"<500ms": 0, "500-1000ms": 0, "1-3s": 0, "3-5s": 0, ">5s": 0}
    _recent_lats = _chat_latencies[-200:] if _chat_latencies else []
    for _l in _recent_lats:
        ms = _l.get("latency_ms", 0)
        if ms < 500: _lat_hist["<500ms"] += 1
        elif ms < 1000: _lat_hist["500-1000ms"] += 1
        elif ms < 3000: _lat_hist["1-3s"] += 1
        elif ms < 5000: _lat_hist["3-5s"] += 1
        else: _lat_hist[">5s"] += 1
    _p50 = _p95 = _p99 = 0
    if _recent_lats:
        _sorted_lats = sorted(l.get("latency_ms", 0) for l in _recent_lats)
        _p50 = round(_sorted_lats[len(_sorted_lats) // 2], 0)
        _p95 = round(_sorted_lats[int(len(_sorted_lats) * 0.95)], 0)
        _p99 = round(_sorted_lats[int(len(_sorted_lats) * 0.99)], 0)

    return {
        "status": overall,
        "version": "2.0.0",
        "service": "Syrabit.ai API",
        "workers": int(os.environ.get("GUNICORN_WORKERS", 3)),
        "uptime_seconds": int(_time_mod.time() - _startup_time),
        "dependencies": {
            "mongodb": {"status": mongo_status, "latencyMs": kv_latency},
            "postgresql": {"status": "ok" if pg_ok else "unavailable", "latencyMs": pg_latency},
            "cloudflare_cache": {"status": cf_cache_status, "latencyMs": cf_cache_latency},
            "llm": {
                "status": llm_status,
                "latencyMs": llm_latency,
                "primary_provider": LLM_PROVIDER,
                "primary_model": LLM_MODEL,
                "provider_pool": [p["provider"] for p in _LLM_PROVIDERS],
                "stats_1h": get_llm_provider_stats(3600),
            },
            "supabase": {"status": "ok" if supa else "not_configured"},
            "razorpay": {"status": rp_status},
            "vertex": vertex_block,
            "bot_render": await get_bot_render_metrics_async(),
        },
        "chat_latency": {
            "samples": len(_recent_lats),
            "p50_ms": _p50,
            "p95_ms": _p95,
            "p99_ms": _p99,
            "histogram": _lat_hist,
        },
    }

@router.get("/metrics")
async def prometheus_metrics():
    import os as _os
    mem_rss_mb = 0
    mem_vms_mb = 0
    cpu = 0
    try:
        with open(f"/proc/{_os.getpid()}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    mem_rss_mb = int(line.split()[1]) / 1024
                elif line.startswith("VmSize:"):
                    mem_vms_mb = int(line.split()[1]) / 1024
        with open(f"/proc/{_os.getpid()}/stat") as f:
            fields = f.read().split()
            utime = int(fields[13])
            stime = int(fields[14])
            total_ticks = utime + stime
            hz = _os.sysconf("SC_CLK_TCK")
            cpu_seconds = total_ticks / hz
            cpu = round(cpu_seconds / max(1, _time_mod.time() - _startup_time) * 100, 1)
    except Exception:
        pass

    content_cache_size = len(_content_cache)
    ai_cache_size = len(_ai_response_cache)
    uptime = int(_time_mod.time() - _startup_time)
    rps = _metrics.get_rps()
    active_users_5m = _metrics.get_active_users(300)
    active_users_15m = _metrics.get_active_users(900)
    active_users_60m = _metrics.get_active_users(3600)
    top_endpoints = _metrics.get_top_endpoints(10)

    lines = [
        f'# HELP syrabit_uptime_seconds Server uptime in seconds',
        f'# TYPE syrabit_uptime_seconds gauge',
        f'syrabit_uptime_seconds {uptime}',
        f'# HELP syrabit_memory_rss_mb Resident memory in MB',
        f'# TYPE syrabit_memory_rss_mb gauge',
        f'syrabit_memory_rss_mb {mem_rss_mb:.1f}',
        f'# HELP syrabit_memory_vms_mb Virtual memory in MB',
        f'# TYPE syrabit_memory_vms_mb gauge',
        f'syrabit_memory_vms_mb {mem_vms_mb:.1f}',
        f'# HELP syrabit_cpu_percent CPU usage percentage',
        f'# TYPE syrabit_cpu_percent gauge',
        f'syrabit_cpu_percent {cpu:.1f}',
        f'# HELP syrabit_requests_total Total requests handled by this worker',
        f'# TYPE syrabit_requests_total counter',
        f'syrabit_requests_total {_metrics.request_count}',
        f'# HELP syrabit_errors_total Total error responses (4xx/5xx)',
        f'# TYPE syrabit_errors_total counter',
        f'syrabit_errors_total {_metrics.error_count}',
        f'# HELP syrabit_requests_in_flight Requests currently being processed',
        f'# TYPE syrabit_requests_in_flight gauge',
        f'syrabit_requests_in_flight {_metrics.active_requests}',
        f'# HELP syrabit_rps Requests per second (60s window)',
        f'# TYPE syrabit_rps gauge',
        f'syrabit_rps {rps}',
        f'# HELP syrabit_chat_requests_total Total AI chat requests',
        f'# TYPE syrabit_chat_requests_total counter',
        f'syrabit_chat_requests_total {_metrics.chat_count}',
        f'# HELP syrabit_active_users_5m Unique authenticated users in last 5 minutes',
        f'# TYPE syrabit_active_users_5m gauge',
        f'syrabit_active_users_5m {active_users_5m}',
        f'# HELP syrabit_active_users_15m Unique authenticated users in last 15 minutes',
        f'# TYPE syrabit_active_users_15m gauge',
        f'syrabit_active_users_15m {active_users_15m}',
        f'# HELP syrabit_active_users_60m Unique authenticated users in last 60 minutes',
        f'# TYPE syrabit_active_users_60m gauge',
        f'syrabit_active_users_60m {active_users_60m}',
        f'# HELP syrabit_content_cache_entries Content cache entries',
        f'# TYPE syrabit_content_cache_entries gauge',
        f'syrabit_content_cache_entries {content_cache_size}',
        f'# HELP syrabit_ai_cache_entries AI response cache entries',
        f'# TYPE syrabit_ai_cache_entries gauge',
        f'syrabit_ai_cache_entries {ai_cache_size}',
        f'# HELP syrabit_workers Configured worker count',
        f'# TYPE syrabit_workers gauge',
        f'syrabit_workers {int(_os.environ.get("GUNICORN_WORKERS", 3))}',
        f'# HELP syrabit_redis_connected Redis connection status',
        f'# TYPE syrabit_redis_connected gauge',
        f'syrabit_redis_connected {1 if redis_client else 0}',
        f'# HELP syrabit_redis_hits Redis cache hits',
        f'# TYPE syrabit_redis_hits counter',
        f'syrabit_redis_hits {_redis_hit_count}',
        f'# HELP syrabit_redis_misses Redis cache misses',
        f'# TYPE syrabit_redis_misses counter',
        f'syrabit_redis_misses {_redis_miss_count}',
    ]
    # Task #609 — managed AI response cache metrics (Memorystore preferred).
    try:
        import ai_cache as _ai_cache_mod
        _ac = _ai_cache_mod.stats()
        _backend_safe = str(_ac.get("backend", "unknown")).replace('"', '\\"')
        lines.extend([
            f'# HELP syrabit_ai_cache_hits Managed AI response cache hits',
            f'# TYPE syrabit_ai_cache_hits counter',
            f'syrabit_ai_cache_hits{{backend="{_backend_safe}"}} {_ac.get("hits", 0)}',
            f'# HELP syrabit_ai_cache_misses Managed AI response cache misses',
            f'# TYPE syrabit_ai_cache_misses counter',
            f'syrabit_ai_cache_misses{{backend="{_backend_safe}"}} {_ac.get("misses", 0)}',
            f'# HELP syrabit_ai_cache_errors Managed AI response cache errors',
            f'# TYPE syrabit_ai_cache_errors counter',
            f'syrabit_ai_cache_errors{{backend="{_backend_safe}"}} {_ac.get("errors", 0)}',
            f'# HELP syrabit_ai_cache_hit_rate Managed AI response cache hit rate (0-1)',
            f'# TYPE syrabit_ai_cache_hit_rate gauge',
            f'syrabit_ai_cache_hit_rate {_ac.get("hit_rate", 0)}',
            f'# HELP syrabit_ai_cache_bytes_stored Total bytes stored to managed AI cache (cumulative)',
            f'# TYPE syrabit_ai_cache_bytes_stored counter',
            f'syrabit_ai_cache_bytes_stored {_ac.get("bytes_stored", 0)}',
            f'# HELP syrabit_ai_cache_oversize_skipped Entries dropped because they exceeded REDIS_AI_CACHE_MAX_ENTRY_BYTES',
            f'# TYPE syrabit_ai_cache_oversize_skipped counter',
            f'syrabit_ai_cache_oversize_skipped {_ac.get("entries_skipped_oversize", 0)}',
            f'# HELP syrabit_ai_cache_avg_saved_ms Average LLM latency saved per cache hit (ms)',
            f'# TYPE syrabit_ai_cache_avg_saved_ms gauge',
            f'syrabit_ai_cache_avg_saved_ms {_ac.get("avg_saved_latency_ms", 0)}',
            f'# HELP syrabit_ai_cache_total_saved_ms Estimated total LLM latency saved by cache (ms)',
            f'# TYPE syrabit_ai_cache_total_saved_ms counter',
            f'syrabit_ai_cache_total_saved_ms {_ac.get("estimated_total_saved_ms", 0)}',
            f'# HELP syrabit_ai_cache_breaker_open Circuit breaker state (1=open, 0=closed)',
            f'# TYPE syrabit_ai_cache_breaker_open gauge',
            f'syrabit_ai_cache_breaker_open {1 if _ac.get("breaker_open") else 0}',
        ])
    except Exception as _ai_cache_metrics_err:
        logger.debug(f"ai_cache metrics unavailable: {_ai_cache_metrics_err}")
    batch_stats = _llm_batcher.stats
    lines.extend([
        f'# HELP syrabit_llm_batched Total LLM requests processed via batcher',
        f'# TYPE syrabit_llm_batched counter',
        f'syrabit_llm_batched {batch_stats["batched"]}',
        f'# HELP syrabit_llm_deduped Requests served by piggy-backing on in-flight call',
        f'# TYPE syrabit_llm_deduped counter',
        f'syrabit_llm_deduped {batch_stats["deduped"]}',
        f'# HELP syrabit_llm_errors LLM call errors',
        f'# TYPE syrabit_llm_errors counter',
        f'syrabit_llm_errors {batch_stats["errors"]}',
        f'# HELP syrabit_llm_pending Currently in-flight LLM requests',
        f'# TYPE syrabit_llm_pending gauge',
        f'syrabit_llm_pending {batch_stats["pending"]}',
    ])
    for status_code, count in sorted(_metrics.status_counts.items()):
        lines.append(f'syrabit_responses_by_status{{code="{status_code}"}} {count}')
    for endpoint, count in top_endpoints:
        safe = endpoint.replace('"', '\\"')
        lines.append(f'syrabit_endpoint_hits{{path="{safe}"}} {count}')
    from starlette.responses import Response
    return Response(content='\n'.join(lines) + '\n', media_type='text/plain; version=0.0.4; charset=utf-8')

@router.get("/ai/cache/stats")
async def get_cache_stats(admin: dict = Depends(get_admin_user)):
    """Return AI response cache statistics (admin only).

    Includes the in-memory L1 tier *and* the managed Memorystore-backed AI
    cache (Task #609): backend, hit/miss/error counters, hit rate, configured
    TTL & max-entry size, bytes stored, and the saved-latency metrics that
    quantify how much LLM time the cache has eliminated."""
    try:
        import ai_cache as _ai_cache_mod
        managed = _ai_cache_mod.stats()
    except Exception as e:
        managed = {"backend": "unavailable", "error": str(e)[:200]}
    return {
        "l1": {
            "size": len(_ai_response_cache),
            "maxsize": _ai_response_cache.maxsize,
            "ttl": _ai_response_cache.ttl,
        },
        "managed": managed,
    }


@router.post("/ai/cache/purge")
async def purge_ai_cache(
    pattern: str = "*",
    admin: dict = Depends(get_admin_user),
):
    """Purge AI response cache entries (admin only).

    `pattern` is appended after the configured `<namespace>:` prefix and
    defaults to `*` (purge everything in the namespace). Also clears the
    in-memory L1 cache so a stale entry can't re-leak from this worker.
    """
    try:
        import ai_cache as _ai_cache_mod
        result = await _ai_cache_mod.purge_all(pattern=pattern)
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "deleted": 0}
    return result

# ─── Task #636: Workers AI fallback admin endpoints ──────────────────────
@router.get("/admin/workers-ai/status")
async def workers_ai_status(admin: dict = Depends(get_admin_user)):
    """Workers AI fallback health snapshot (admin only).

    Used by AdminHealth.jsx to render reachability, last-fallback time,
    and 24h success/failure counts per capability."""
    try:
        from providers import workers_ai as _wai
        return {"ok": True, **_wai.snapshot()}
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


@router.post("/admin/workers-ai/kill-switch")
async def workers_ai_kill_switch(payload: dict, admin: dict = Depends(get_admin_user)):
    """Toggle the per-capability kill switch (admin only).

    Body: `{"capability": "chat"|"embed"|"tts"|"stt", "enabled": bool}`.
    Affects only this process — by design, so a flip can be tested
    against a single instance before wider rollout via env var."""
    cap = (payload or {}).get("capability", "")
    enabled = bool((payload or {}).get("enabled", True))
    try:
        from providers import workers_ai as _wai
        if cap not in _wai.CAPABILITIES:
            raise HTTPException(status_code=400, detail="invalid_capability")
        actor = (admin or {}).get("email") or (admin or {}).get("sub") or "admin"
        new_val = await _wai.set_enabled_async(cap, enabled, actor=actor)
        return {"ok": True, "capability": cap, "enabled": new_val, "durable": True}
    except HTTPException:
        raise
    except Exception as e:
        return {"ok": False, "error": str(e)[:200]}


@router.get("/metrics/history")
async def metrics_history(minutes: int = 60, admin: dict = Depends(get_admin_user)):
    """Return time-series metrics history for graphing (admin only)."""
    minutes = min(max(minutes, 1), _METRICS_HISTORY_MAX)
    cutoff = _time_mod.time() - (minutes * 60)
    _snapshot_metrics()
    with _metrics_history_lock:
        data = [s for s in _metrics_history if s["ts"] >= cutoff]

    peak_5m = max((s["active_5m"] for s in data), default=0)
    peak_15m = max((s["active_15m"] for s in data), default=0)
    peak_60m = max((s["active_60m"] for s in data), default=0)
    peak_rps = max((s["rps"] for s in data), default=0)

    return {
        "history": data,
        "peaks": {
            "active_users_5m": peak_5m,
            "active_users_15m": peak_15m,
            "active_users_60m": peak_60m,
            "rps": peak_rps,
        },
        "current": data[-1] if data else None,
        "points": len(data),
        "window_minutes": minutes,
    }


# ─────────────────────────────────────────────
# SARVAM AI — Translate, TTS, Transliterate
# ─────────────────────────────────────────────

_SARVAM_LANG_CODES = {
    "en", "en-IN", "as", "as-IN", "bn", "bn-IN",
    "hi", "hi-IN", "gu", "gu-IN", "kn", "kn-IN",
    "ml", "ml-IN", "mr", "mr-IN", "od", "od-IN",
    "pa", "pa-IN", "ta", "ta-IN", "te", "te-IN",
}

def _normalise_lang(code: str) -> str:
    """Ensure language code has -IN suffix (sarvam requires it)."""
    code = code.strip()
    if '-' not in code:
        return f"{code}-IN"
    return code

def _sarvam_cache_key(op: str, payload: dict) -> str:
    import json
    raw = json.dumps(payload, sort_keys=True)
    return f"sarvam:{op}:{hashlib.md5(raw.encode()).hexdigest()}"

@router.get("/sarvam/status")
async def sarvam_status():
    # Surface live Assamese-purity config so admins can verify which
    # behaviour and threshold are in effect without grep'ing the api log
    # (Task #419).
    try:
        from lang_sanitizer import get_runtime_config as _asm_cfg
        assamese_purity = _asm_cfg()
    except Exception:
        assamese_purity = {}
    return {
        "enabled": sarvam_client is not None,
        "supported_languages": sorted(_SARVAM_LANG_CODES),
        "assamese_purity": assamese_purity,
    }


# ──────────────────────────────────────────────────────────────────────
# Task #422 — admin runtime override for Assamese leakage behaviour +
# threshold. The override layer lives in `lang_sanitizer` (in-memory)
# and is persisted in `db.api_config.assamese_purity_override` so it
# survives api restarts. The lifespan hook in `server.py` re-applies
# the persisted override on boot.
# ──────────────────────────────────────────────────────────────────────
_ASM_OVERRIDE_DOC_KEY = "assamese_purity_override"
_ASM_RUNS_COLLECTION = "assamese_purity_runs"
# Keep run docs for two weeks — long enough for the dashboard's 7d
# window plus headroom, short enough that the collection stays cheap.
_ASM_RUNS_TTL_SECONDS = 14 * 24 * 3600

# Task #424 — append-only audit log of override edits so a regression
# can be bisected back to the admin / value that introduced it. We do
# NOT TTL this collection: the whole point is that it survives a Mongo
# restart and is small (a few rows per change, not per request).
_ASM_AUDIT_COLLECTION = "assamese_purity_audit"
_ASM_AUDIT_PAGE_LIMIT = 20


async def _insert_assamese_run(doc: dict) -> None:
    """Async fire-and-forget insert. Failures must NEVER affect the
    sanitiser hot path, so all exceptions are swallowed with a warn."""
    try:
        from deps import db as _db
        await _db[_ASM_RUNS_COLLECTION].insert_one(doc)
    except Exception as e:
        logger.warning(f"[INDIC-SANITIZE] insert run failed: {e}")


# Task #428 — snippet bounds and PII scrub patterns. We persist the
# raw + cleaned text alongside the diag so admins can drill into a
# specific cleanup, but truncate hard and strip obvious PII first so
# the runs collection stays cheap and we never log user-identifying
# data inside Assamese chat replies (emails, phone numbers, long
# digit runs that look like IDs / OTPs).
_ASM_SNIPPET_MAX_CHARS = 600
_ASM_PII_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_ASM_PII_PHONE_RE = re.compile(r"(?:\+?\d[\d\s\-()]{8,}\d)")
_ASM_PII_LONGNUM_RE = re.compile(r"\b\d{6,}\b")


def _scrub_pii(text: str) -> str:
    """Replace obvious PII (emails, phone numbers, long numeric IDs)
    with placeholder tokens. Defensive against unexpected types."""
    if not text or not isinstance(text, str):
        return ""
    out = _ASM_PII_EMAIL_RE.sub("[email]", text)
    out = _ASM_PII_PHONE_RE.sub("[phone]", out)
    out = _ASM_PII_LONGNUM_RE.sub("[num]", out)
    return out


def _snippet(text: str) -> str:
    """Truncate + scrub a chunk of text for safe persistence in the
    audit log. Empty / non-string inputs collapse to ''."""
    if not text or not isinstance(text, str):
        return ""
    scrubbed = _scrub_pii(text)
    if len(scrubbed) <= _ASM_SNIPPET_MAX_CHARS:
        return scrubbed
    return scrubbed[: _ASM_SNIPPET_MAX_CHARS - 1] + "…"


def _record_assamese_run(diag: dict) -> None:
    """Recorder callback installed into lang_sanitizer. Receives the
    sanitiser diag dict on every run and schedules a small mongo insert
    so admins can chart trigger counts / action distribution / leakage
    ratio over time, AND drill into individual cleanups via the
    `/admin/assamese-purity/runs` endpoint. Synchronous shape (so it's
    safe to call from the sanitiser's sync entrypoints too) — schedules
    an asyncio task."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return  # No running loop (e.g. unit tests) — silently skip.
    if not loop.is_running():
        return
    action = str(diag.get("action") or "unknown")[:40]
    doc = {
        "ts": datetime.now(timezone.utc),
        "action": action,
        "behaviour": str(diag.get("behaviour") or "unknown")[:40],
        # `original_ratio` is set when sanitisation actually fired;
        # otherwise the noop branch only carries `ratio`. Both are the
        # pre-cleanup ratio we want to chart.
        "ratio": float(diag.get("original_ratio", diag.get("ratio", 0.0)) or 0.0),
        "post_ratio": float(diag.get("ratio", 0.0) or 0.0),
        "threshold": float(diag.get("threshold", 0.0) or 0.0),
        "translated": bool(diag.get("translated")),
        "regenerated": bool(diag.get("regenerated")),
        "has_assamese": bool(diag.get("has_assamese", True)),
    }
    # Task #428 — persist truncated + PII-scrubbed snippets, but only
    # for runs where cleanup actually fired. Skipping `noop` keeps the
    # collection small (most runs are noops on non-Indic traffic) and
    # focuses the audit log on the cases admins care about.
    if action != "noop":
        raw_snip = _snippet(diag.get("raw_text") or "")
        cleaned_snip = _snippet(diag.get("cleaned_text") or "")
        if raw_snip:
            doc["raw_snippet"] = raw_snip
        if cleaned_snip:
            doc["cleaned_snippet"] = cleaned_snip
        # Task #437 — persist the exact Latin runs the sanitiser flagged
        # so the admin UI can highlight them inside the original snippet.
        # Bounded list (50 tokens × 80 chars) so a runaway diag can't
        # bloat the row; tokens are scrubbed for PII to match snippet
        # treatment, deduped while preserving order, and any empty
        # entries dropped. We keep noop runs token-free since we don't
        # store snippets for them anyway.
        raw_tokens = diag.get("suspicious_tokens") or []
        if isinstance(raw_tokens, (list, tuple)):
            seen: set[str] = set()
            tokens: list[str] = []
            for t in raw_tokens:
                if not isinstance(t, str):
                    continue
                cleaned = _scrub_pii(t).strip()
                if not cleaned or cleaned in seen:
                    continue
                seen.add(cleaned)
                tokens.append(cleaned[:80])
                if len(tokens) >= 50:
                    break
            if tokens:
                doc["suspicious_tokens"] = tokens
    # Task #428 — trace fields (conversation_id, user_id) so admins can
    # answer "which user / which conversation triggered this leak?"
    # without combing Railway logs. We persist only stable IDs (no
    # names / emails) — the chat router decides what to thread in.
    # Length-bounded so a malformed caller can't bloat the row.
    trace = diag.get("trace") or {}
    if isinstance(trace, dict):
        conv_id = trace.get("conversation_id")
        if conv_id:
            doc["conversation_id"] = str(conv_id)[:80]
        usr_id = trace.get("user_id")
        if usr_id:
            doc["user_id"] = str(usr_id)[:80]
    try:
        asyncio.create_task(_insert_assamese_run(doc))
    except Exception as e:
        logger.warning(f"[INDIC-SANITIZE] schedule run insert failed: {e}")


# Install recorder at module import so every route worker (and any
# script that imports cms_sarvam_health) automatically wires stats.
try:
    from lang_sanitizer import set_run_recorder as _set_recorder
    _set_recorder(_record_assamese_run)
except Exception as _rec_err:  # pragma: no cover - defensive
    logger.warning(f"[INDIC-SANITIZE] recorder install failed: {_rec_err}")


async def ensure_assamese_runs_index() -> None:
    """Create the TTL index on the runs collection so old docs auto-
    expire. Called from server.py lifespan (idempotent)."""
    from deps import db as _db
    if _db is None:
        return
    try:
        await _db[_ASM_RUNS_COLLECTION].create_index(
            "ts", expireAfterSeconds=_ASM_RUNS_TTL_SECONDS,
        )
        await _db[_ASM_RUNS_COLLECTION].create_index([("ts", -1), ("action", 1)])
    except Exception as e:
        logger.warning(f"[INDIC-SANITIZE] runs index create failed: {e}")


async def ensure_assamese_audit_index() -> None:
    """Index `ts` desc on the audit collection so the history-panel
    query (`find().sort(ts, -1).limit(20)`) is cheap. Idempotent."""
    from deps import db as _db
    if _db is None:
        return
    try:
        await _db[_ASM_AUDIT_COLLECTION].create_index([("ts", -1)])
    except Exception as e:
        logger.warning(f"[INDIC-SANITIZE] audit index create failed: {e}")


async def _record_assamese_audit(
    admin: dict | None,
    action: str,
    before: dict | None,
    after: dict | None,
    source_audit_id: str | None = None,
) -> str | None:
    """Append an audit row for a PATCH / DELETE / REVERT on
    `/admin/assamese-purity`. Best-effort: if mongo is down we log and
    continue — losing an audit row must NEVER fail the user-visible
    admin action. Returns the new row's `id` so callers (e.g. the
    revert endpoint) can reference it back to the source row."""
    from deps import db as _db
    if _db is None:
        return None
    try:
        new_id = uuid.uuid4().hex
        doc = {
            "id": new_id,
            "ts": datetime.now(timezone.utc),
            "action": action,
            "admin_email": (admin or {}).get("email"),
            "admin_id": (admin or {}).get("id"),
            "before": before,
            "after": after,
        }
        if source_audit_id:
            doc["source_audit_id"] = source_audit_id
        await _db[_ASM_AUDIT_COLLECTION].insert_one(doc)
        return new_id
    except Exception as e:
        logger.warning(f"[INDIC-SANITIZE] audit insert failed: {e}")
        return None
# Known leaky Assamese reply used by the test-fire button so admins can
# validate the chosen behaviour against a deterministic input. Picked
# to exercise both the `/translate` replace path AND the strip fallback.
_ASM_TEST_FIRE_SAMPLE = (
    "উৰুকা হৈছে মাঘ বিহুৰ পূৰ্বৰ ৰাতিৰ উৎসৱ। "
    "It is celebrated by all assamese people who come together "
    "for me uses ssible communal feasting around bonfires."
)


async def _load_persisted_assamese_purity_override() -> dict | None:
    """Read the persisted override doc from mongo. Returns the inner
    {behaviour, threshold, ...} dict or None when no override is set."""
    from deps import db as _db
    if _db is None:
        return None
    try:
        doc = await _db.api_config.find_one({}, {_ASM_OVERRIDE_DOC_KEY: 1})
        if not doc:
            return None
        ov = doc.get(_ASM_OVERRIDE_DOC_KEY)
        if not ov or not isinstance(ov, dict):
            return None
        return ov
    except Exception as e:
        logger.warning(f"[INDIC-SANITIZE] failed to load persisted override: {e}")
        return None


async def apply_persisted_assamese_purity_override() -> None:
    """Called from server.py lifespan on api boot AND from the periodic
    refresher below. Reads the persisted override doc and reconciles
    the in-memory layer in lang_sanitizer:

      - doc present  → apply (so behaviour/threshold survive restarts
                       AND propagate across gunicorn workers within
                       one refresh cycle, not just on restart).
      - doc absent   → clear any in-memory override (so a DELETE made
                       in worker A propagates to worker B).
    """
    from lang_sanitizer import (
        apply_runtime_override as _apply,
        clear_runtime_override as _clear,
        get_runtime_override as _get_ov,
    )
    ov = await _load_persisted_assamese_purity_override()
    if ov:
        try:
            applied = _apply(
                behaviour=ov.get("behaviour"),
                threshold=ov.get("threshold"),
                indic_provider=ov.get("indic_provider"),
                updated_by=ov.get("updated_by"),
            )
            logger.info(
                f"[INDIC-SANITIZE] reconciled persisted override: {applied}"
            )
        except Exception as e:
            logger.warning(f"[INDIC-SANITIZE] failed to apply persisted override: {e}")
    else:
        # Persisted doc gone but we still have an in-memory override
        # (likely cleared by a sibling worker) → drop it.
        if _get_ov() is not None:
            _clear()
            logger.info("[INDIC-SANITIZE] reconciled cleared override (sibling worker)")


# How often each worker re-reads the persisted override doc. 15s is a
# tradeoff between propagation latency and DB read load (one find_one
# per worker per interval).
_ASM_REFRESH_INTERVAL_SECONDS = 15


async def _assamese_purity_refresh_loop() -> None:
    """Background task started by server.py lifespan. Each worker polls
    mongo every `_ASM_REFRESH_INTERVAL_SECONDS` so PATCH/DELETE made on
    one worker propagates to all others within ~15s — without requiring
    pub/sub infra."""
    import asyncio
    from metrics import record_assamese_refresh_success
    while True:
        try:
            await asyncio.sleep(_ASM_REFRESH_INTERVAL_SECONDS)
            await apply_persisted_assamese_purity_override()
            # Task #432: heartbeat for the alerting loop. Only bumped on a
            # successful tick — if mongo is down or the loader raises we
            # leave the timestamp unchanged so the staleness alert fires.
            record_assamese_refresh_success()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[INDIC-SANITIZE] refresh loop tick failed: {e}")


@router.get("/admin/assamese-purity")
async def admin_get_assamese_purity(admin: dict = Depends(get_admin_user)):
    """Return the live Assamese purity config plus override metadata so
    the admin UI can render the current state. Mirrors what
    `/sarvam/status` exposes but adds the persisted-doc audit fields."""
    from lang_sanitizer import get_runtime_config as _asm_cfg
    from metrics import (
        get_assamese_refresh_age_seconds as _asm_age,
        _ALERT_THRESHOLDS as _asm_thresholds,
    )
    import os as _os
    cfg = _asm_cfg()
    persisted = await _load_persisted_assamese_purity_override()
    # Task #432: surface this worker's refresh heartbeat so admins can
    # spot-check propagation health without waiting for the alert.
    refresh_age = _asm_age()
    refresh_stale_threshold = float(_asm_thresholds.get("assamese_refresh_stale_seconds", 60) or 0)
    return {
        "config": cfg,
        "persisted": persisted or None,
        "test_sample": _ASM_TEST_FIRE_SAMPLE,
        "refresh_health": {
            "worker_pid": _os.getpid(),
            "age_seconds": round(refresh_age, 1),
            "stale_threshold_seconds": int(refresh_stale_threshold),
            "stale": refresh_age > refresh_stale_threshold > 0,
            "interval_seconds": _ASM_REFRESH_INTERVAL_SECONDS,
        },
    }


@router.patch("/admin/assamese-purity")
async def admin_update_assamese_purity(
    data: dict = Body(...),
    admin: dict = Depends(get_admin_user),
):
    """Override behaviour and/or threshold at runtime. Both fields are
    optional — pass only the ones you want to change. The override is
    persisted to mongo so it survives api restarts."""
    from lang_sanitizer import (
        apply_runtime_override as _apply,
        _normalise_behaviour,
        _normalise_threshold,
        _normalise_indic_provider,
        get_runtime_config as _asm_cfg,
        get_runtime_override as _get_ov,
    )

    raw_behaviour = data.get("behaviour")
    raw_threshold = data.get("threshold")
    raw_indic_provider = data.get("indic_provider")
    if raw_behaviour is None and raw_threshold is None and raw_indic_provider is None:
        raise HTTPException(
            status_code=400,
            detail="Pass at least one of `behaviour`, `threshold`, or `indic_provider`",
        )

    # Validate inputs UP FRONT so we never persist a doc the in-memory
    # layer would silently reject.
    if raw_behaviour is not None and _normalise_behaviour(raw_behaviour) is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "behaviour must be one of off|strip|translate|"
                "regenerate|translate+regenerate"
            ),
        )
    if raw_threshold is not None and _normalise_threshold(raw_threshold) is None:
        raise HTTPException(
            status_code=400,
            detail="threshold must be a float strictly between 0 and 1",
        )
    if raw_indic_provider is not None and _normalise_indic_provider(raw_indic_provider) is None:
        raise HTTPException(
            status_code=400,
            detail="indic_provider must be one of sarvam|vertex",
        )

    updated_by = (admin or {}).get("email") or (admin or {}).get("id") or "admin"

    # Snapshot the persisted override BEFORE we mutate it so the audit
    # row records what the value used to be. Done outside the try below
    # so a load failure doesn't poison the in-memory apply.
    before_doc = await _load_persisted_assamese_purity_override()

    applied = _apply(
        behaviour=raw_behaviour,
        threshold=raw_threshold,
        indic_provider=raw_indic_provider,
        updated_by=updated_by,
    )

    # Persist the FULL current override (not just the delta) so the
    # mongo doc is always the single source of truth on api boot.
    persist_doc = {
        **(_get_ov() or {}),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        from deps import db as _db
        await _db.api_config.update_one(
            {},
            {"$set": {_ASM_OVERRIDE_DOC_KEY: persist_doc}},
            upsert=True,
        )
    except Exception as e:
        logger.error(f"[INDIC-SANITIZE] persist override failed: {e}")
        # In-memory layer was already updated; admin can retry the PATCH
        # but we must not silently claim success on persistence failure.
        raise HTTPException(
            status_code=500,
            detail="override applied in-memory but failed to persist; "
                   "value will reset on next api restart",
        )

    # Task #424 — audit the change AFTER successful persist so we never
    # log a write that didn't actually take effect.
    await _record_assamese_audit(
        admin, action="patch", before=before_doc, after=persist_doc,
    )

    return {
        "ok": True,
        "applied": applied,
        "persisted": persist_doc,
        "config": _asm_cfg(),
    }


@router.delete("/admin/assamese-purity")
async def admin_clear_assamese_purity(admin: dict = Depends(get_admin_user)):
    """Drop the runtime override so env vars / hard-coded defaults take
    over again. Removes the persisted mongo doc as well.

    Fails CLOSED: if the mongo unset fails, we do NOT clear the
    in-memory layer and we return 500. Otherwise the override would
    silently come back on the next worker restart (or in any other
    worker that hasn't picked up the clear yet) and the admin would
    have no signal anything went wrong."""
    from lang_sanitizer import (
        clear_runtime_override as _clear,
        get_runtime_config as _asm_cfg,
    )
    # Snapshot what's about to be cleared so the audit row preserves it.
    before_doc = await _load_persisted_assamese_purity_override()
    try:
        from deps import db as _db
        await _db.api_config.update_one(
            {},
            {"$unset": {_ASM_OVERRIDE_DOC_KEY: ""}},
        )
    except Exception as e:
        logger.error(f"[INDIC-SANITIZE] persist clear failed: {e}")
        raise HTTPException(
            status_code=500,
            detail="failed to clear persisted override; in-memory "
                   "override left untouched to avoid split-brain on restart",
        )
    _clear()
    # Task #424 — record the deletion. before=previous override, after=None.
    await _record_assamese_audit(
        admin, action="delete", before=before_doc, after=None,
    )
    return {"ok": True, "cleared": True, "config": _asm_cfg()}


@router.get("/admin/assamese-purity/audit")
async def admin_get_assamese_purity_audit(
    limit: int = 20,
    offset: int = 0,
    since: Optional[str] = None,
    until: Optional[str] = None,
    admin_email: Optional[str] = None,
    admin: dict = Depends(get_admin_user),
):
    """Return override-edit audit rows (newest first), with optional
    filters so admins can bisect older incidents without dropping into
    the mongo shell.

    - `limit` is clamped to [1, 100] so a curious caller cannot ask for
      the whole table.
    - `offset` is clamped to >= 0 and used for paging beyond the first
      `limit` entries.
    - `since` / `until` are ISO-8601 timestamps (inclusive lower / upper
      bound on the `ts` field). Naive strings are treated as UTC.
    - `admin_email` is a case-insensitive substring match on the audit
      row's recorded admin email."""
    try:
        n = max(1, min(100, int(limit)))
    except (TypeError, ValueError):
        n = _ASM_AUDIT_PAGE_LIMIT
    try:
        off = max(0, int(offset))
    except (TypeError, ValueError):
        off = 0

    def _parse_ts(s: Optional[str]) -> Optional[datetime]:
        if not s:
            return None
        try:
            # `fromisoformat` accepts trailing "Z" only on Py>=3.11; strip
            # it so older runtimes don't choke on the common JS format.
            cleaned = s.strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(cleaned)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None

    since_dt = _parse_ts(since)
    until_dt = _parse_ts(until)
    if (since and not since_dt) or (until and not until_dt):
        raise HTTPException(
            status_code=400,
            detail="since/until must be ISO-8601 timestamps",
        )

    query: dict = {}
    ts_clause: dict = {}
    if since_dt is not None:
        ts_clause["$gte"] = since_dt
    if until_dt is not None:
        ts_clause["$lte"] = until_dt
    if ts_clause:
        query["ts"] = ts_clause
    if admin_email:
        # Anchor with a substring so admins can paste either the full
        # email or just a domain. Escape regex metacharacters because
        # raw "+" / "." appear in real emails.
        query["admin_email"] = {
            "$regex": re.escape(admin_email.strip()),
            "$options": "i",
        }

    try:
        from deps import db as _db
        coll = _db[_ASM_AUDIT_COLLECTION]
        # Count first so the UI can render correct paging controls; then
        # fetch the requested page. Both run against the `ts` desc index.
        total = await coll.count_documents(query)
        cursor = (
            coll.find(query, {"_id": 0})
            .sort("ts", -1)
            .skip(off)
            .limit(n)
        )
        rows = await cursor.to_list(n)
    except Exception as e:
        logger.warning(f"[INDIC-SANITIZE] audit fetch failed: {e}")
        return {
            "ok": False, "error": str(e),
            "entries": [], "total": 0,
            "limit": n, "offset": off,
        }
    # Normalise `ts` to ISO so the React side can format with toLocaleString.
    for r in rows:
        ts = r.get("ts")
        if isinstance(ts, datetime):
            r["ts"] = ts.replace(tzinfo=ts.tzinfo or timezone.utc).isoformat()
    return {
        "ok": True,
        "entries": rows,
        "total": int(total),
        "limit": n,
        "offset": off,
        "filters": {
            "since": since_dt.isoformat() if since_dt else None,
            "until": until_dt.isoformat() if until_dt else None,
            "admin_email": admin_email or None,
        },
    }


@router.post("/admin/assamese-purity/audit/{audit_id}/revert")
async def admin_revert_assamese_purity(
    audit_id: str = Path(..., min_length=1, max_length=64),
    admin: dict = Depends(get_admin_user),
):
    """One-click revert: re-apply the override state that existed
    *before* the referenced audit row's change. Works for both `patch`
    rows (re-applies their `before` snapshot) and `delete` rows
    (restores the override that was deleted, which is also stored in
    `before`). When `before` is empty (e.g. the very first patch made
    on a fresh install), reverting clears the override entirely.

    The revert itself is recorded as a fresh audit row tagged
    `action: "revert"` with `source_audit_id` pointing at the row the
    admin clicked, so the audit panel makes the chain auditable."""
    from lang_sanitizer import (
        apply_runtime_override as _apply,
        clear_runtime_override as _clear,
        _normalise_behaviour,
        _normalise_threshold,
        get_runtime_config as _asm_cfg,
        get_runtime_override as _get_ov,
    )

    try:
        from deps import db as _db
        src = await _db[_ASM_AUDIT_COLLECTION].find_one(
            {"id": audit_id}, {"_id": 0},
        )
    except Exception as e:
        logger.error(f"[INDIC-SANITIZE] audit lookup for revert failed: {e}")
        raise HTTPException(status_code=503, detail="audit log unavailable")
    if not src:
        raise HTTPException(status_code=404, detail="audit row not found")
    if src.get("action") == "revert":
        # Allowed in principle, but we reject to keep the chain readable
        # — admin should revert to the *original* row, not a revert row.
        raise HTTPException(
            status_code=400,
            detail="cannot revert a revert row; pick the original change",
        )

    target = src.get("before") or None
    updated_by = (admin or {}).get("email") or (admin or {}).get("id") or "admin"

    # Snapshot what the admin is overwriting so the new audit row is
    # symmetric with patch/delete rows (before=current state).
    before_now = await _load_persisted_assamese_purity_override()

    if target and isinstance(target, dict) and (
        target.get("behaviour") is not None or target.get("threshold") is not None
    ):
        # Validate the snapshot we're re-applying: defends against a
        # row whose `before` was hand-edited or written before stricter
        # validation existed.
        beh = target.get("behaviour")
        thr = target.get("threshold")
        if beh is not None and _normalise_behaviour(beh) is None:
            raise HTTPException(
                status_code=422,
                detail=f"audit row's `before.behaviour` is invalid: {beh!r}",
            )
        if thr is not None and _normalise_threshold(thr) is None:
            raise HTTPException(
                status_code=422,
                detail=f"audit row's `before.threshold` is invalid: {thr!r}",
            )
        applied = _apply(behaviour=beh, threshold=thr, updated_by=updated_by)
        persist_doc = {
            **(_get_ov() or {}),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            await _db.api_config.update_one(
                {},
                {"$set": {_ASM_OVERRIDE_DOC_KEY: persist_doc}},
                upsert=True,
            )
        except Exception as e:
            logger.error(f"[INDIC-SANITIZE] revert persist failed: {e}")
            raise HTTPException(
                status_code=500,
                detail="revert applied in-memory but failed to persist; "
                       "value will reset on next api restart",
            )
        await _record_assamese_audit(
            admin, action="revert", before=before_now, after=persist_doc,
            source_audit_id=audit_id,
        )
        return {
            "ok": True,
            "reverted_to": persist_doc,
            "applied": applied,
            "config": _asm_cfg(),
            "source_audit_id": audit_id,
        }

    # `before` was empty → reverting means dropping the override.
    try:
        await _db.api_config.update_one(
            {}, {"$unset": {_ASM_OVERRIDE_DOC_KEY: ""}},
        )
    except Exception as e:
        logger.error(f"[INDIC-SANITIZE] revert clear persist failed: {e}")
        raise HTTPException(
            status_code=500,
            detail="failed to clear persisted override during revert; "
                   "in-memory override left untouched",
        )
    _clear()
    await _record_assamese_audit(
        admin, action="revert", before=before_now, after=None,
        source_audit_id=audit_id,
    )
    return {
        "ok": True,
        "reverted_to": None,
        "cleared": True,
        "config": _asm_cfg(),
        "source_audit_id": audit_id,
    }


@router.get("/admin/assamese-purity/stats")
async def admin_assamese_purity_stats(
    window: str = "24h",
    admin: dict = Depends(get_admin_user),
):
    """Aggregate the persisted sanitiser-run docs into a small dashboard
    payload: total runs, action distribution, behaviour distribution,
    and avg / p95 leakage ratio for the given window. Designed to be
    cheap enough to call on every tab open.

    `window` ∈ {"24h", "7d"} — anything else is rejected so the TTL on
    the runs collection (14 days) can't be silently exceeded."""
    if window not in ("24h", "7d"):
        raise HTTPException(
            status_code=400, detail="window must be '24h' or '7d'",
        )
    hours = 24 if window == "24h" else 24 * 7
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    try:
        from deps import db as _db
        coll = _db[_ASM_RUNS_COLLECTION]

        # One pipeline per facet — Mongo handles each as a single pass
        # and keeps the response shape obvious in tests.
        # NOTE: we deliberately do NOT `$push` ratios in the overall
        # pipeline. A naive `{"$push": "$ratio"}` is unbounded and can
        # OOM the aggregation cursor on a 7d window after a busy day.
        # Instead we run a separate `$sample`-bounded pipeline for p95
        # so memory stays O(SAMPLE_CAP) regardless of traffic.
        overall_pipe = [
            {"$match": {"ts": {"$gte": since}}},
            {"$group": {
                "_id": None,
                "total": {"$sum": 1},
                "avg_ratio": {"$avg": "$ratio"},
                "active": {"$sum": {
                    "$cond": [{"$ne": ["$action", "noop"]}, 1, 0],
                }},
                "translated": {"$sum": {"$cond": ["$translated", 1, 0]}},
                "regenerated": {"$sum": {"$cond": ["$regenerated", 1, 0]}},
            }},
        ]
        action_pipe = [
            {"$match": {"ts": {"$gte": since}}},
            {"$group": {"_id": "$action", "count": {"$sum": 1}}},
        ]
        behaviour_pipe = [
            {"$match": {"ts": {"$gte": since}}},
            {"$group": {"_id": "$behaviour", "count": {"$sum": 1}}},
        ]
        # Random uniform sample → unbiased p95 estimate. Sampling BEFORE
        # the group is the critical bit: previously we pulled every
        # ratio and then `sorted(...)[:10000]` kept the 10k SMALLEST
        # values, which biases p95 strongly downward whenever the window
        # has more than 10k runs.
        SAMPLE_CAP = 10000
        ratio_pipe = [
            {"$match": {"ts": {"$gte": since}}},
            {"$sample": {"size": SAMPLE_CAP}},
            {"$project": {"_id": 0, "ratio": 1}},
        ]

        overall_docs = await coll.aggregate(overall_pipe).to_list(length=1)
        action_docs = await coll.aggregate(action_pipe).to_list(length=20)
        behaviour_docs = await coll.aggregate(behaviour_pipe).to_list(length=20)
        ratio_docs = await coll.aggregate(ratio_pipe).to_list(length=SAMPLE_CAP)
    except Exception as e:
        logger.warning(f"[INDIC-SANITIZE] stats aggregation failed: {e}")
        return {
            "ok": False, "window": window, "since": since.isoformat(),
            "total": 0, "active": 0, "avg_ratio": 0.0, "p95_ratio": 0.0,
            "actions": {}, "behaviours": {},
            "translated": 0, "regenerated": 0,
            "error": "stats aggregation failed (see api logs)",
        }

    overall = overall_docs[0] if overall_docs else {}
    ratios = [
        float(d.get("ratio") or 0.0)
        for d in (ratio_docs or [])
        if d.get("ratio") is not None
    ]
    if ratios:
        import math
        ratios_sorted = sorted(ratios)
        # Nearest-rank p95: index is ceil(p * n) - 1 (0-based). Using
        # `round` here would understate p95 for some sample sizes (e.g.
        # n=11 → round(10.45)=10 but ceil(10.45)=11). Sampling happened
        # at the mongo layer via `$sample`, so this is an unbiased
        # estimate within the SAMPLE_CAP.
        n = len(ratios_sorted)
        p95_idx = max(0, math.ceil(0.95 * n) - 1)
        p95 = float(ratios_sorted[min(p95_idx, n - 1)])
    else:
        p95 = 0.0

    return {
        "ok": True,
        "window": window,
        "since": since.isoformat(),
        "total": int(overall.get("total", 0)),
        "active": int(overall.get("active", 0)),
        "avg_ratio": float(overall.get("avg_ratio") or 0.0),
        "p95_ratio": p95,
        "translated": int(overall.get("translated", 0)),
        "regenerated": int(overall.get("regenerated", 0)),
        "actions": {str(d["_id"]): int(d["count"]) for d in action_docs},
        "behaviours": {str(d["_id"]): int(d["count"]) for d in behaviour_docs},
    }


@router.get("/admin/assamese-purity/runs")
async def admin_assamese_purity_runs(
    limit: int = 50,
    action: str | None = None,
    behaviour: str | None = None,
    admin: dict = Depends(get_admin_user),
):
    """Task #428 — return recent sanitiser run docs (newest first) so
    admins can drill into individual cleanups: which exact reply was
    translated/stripped/regenerated, what the original vs cleaned text
    looked like, and which behaviour was active.

    Filtering:
      * `action`    — exact match on the action label (e.g. `stripped`,
                      `translated`, `translated+stripped`,
                      `regenerated+translated`). Pass `noop` to inspect
                      runs that DID NOT trigger; note these will not
                      have raw/cleaned snippets persisted.
      * `behaviour` — exact match on the behaviour at run time (e.g.
                      `translate`, `regenerate`, `strip`).

    `limit` is clamped to [1, 200] so a curious caller cannot drain
    the collection in one shot."""
    try:
        n = max(1, min(200, int(limit)))
    except (TypeError, ValueError):
        n = 50
    query: dict = {}
    if action:
        # Bound the filter values so a malformed query can't cause an
        # unbounded regex / text scan.
        query["action"] = str(action)[:40]
    if behaviour:
        query["behaviour"] = str(behaviour)[:40]
    try:
        from deps import db as _db
        cursor = (
            _db[_ASM_RUNS_COLLECTION]
            .find(query, {"_id": 0})
            .sort("ts", -1)
            .limit(n)
        )
        rows = await cursor.to_list(n)
    except Exception as e:
        logger.warning(f"[INDIC-SANITIZE] runs fetch failed: {e}")
        return {
            "ok": False, "error": str(e), "entries": [],
            "limit": n, "filters": query,
        }
    for r in rows:
        ts = r.get("ts")
        if isinstance(ts, datetime):
            r["ts"] = ts.replace(tzinfo=ts.tzinfo or timezone.utc).isoformat()
    return {
        "ok": True,
        "entries": rows,
        "limit": n,
        "filters": query,
    }


@router.post("/admin/assamese-purity/test")
async def admin_test_assamese_purity(
    data: dict = Body(default={}),
    admin: dict = Depends(get_admin_user),
):
    """Run a sample (admin-supplied or the default leaky one) through
    the LIVE sanitiser so admins can validate the chosen behaviour
    without waiting for a real user query. Returns raw + cleaned text
    plus the diagnostic dict (action/ratio/translated/regenerated/...)
    so the UI can render the side-by-side comparison the task spec
    asks for."""
    from lang_sanitizer import (
        sanitize_assamese_with_optional_regenerate as _sanitize,
        get_runtime_config as _asm_cfg,
    )

    sample = (data.get("sample") or _ASM_TEST_FIRE_SAMPLE).strip()
    if not sample:
        raise HTTPException(status_code=400, detail="sample must be non-empty")
    # Bound sample size so a stolen admin token can't weaponise this
    # endpoint to drive arbitrary Sarvam translate cost.
    _ASM_TEST_MAX_CHARS = 4000
    if len(sample) > _ASM_TEST_MAX_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"sample too long (max {_ASM_TEST_MAX_CHARS} chars)",
        )

    # Build a translate callable that hits the same Sarvam route the
    # live chat path uses, so admins are testing the actual production
    # pipeline (not a mock).
    async def _translate_callable(fragment: str) -> str:
        try:
            if not sarvam_client:
                return ""
            payload = {
                "input": fragment,
                "source_language_code": "en-IN",
                "target_language_code": "as-IN",
                "speaker_gender": "Female",
                "mode": "formal",
                "model": "sarvam-translate:v1",
                "enable_preprocessing": False,
            }
            resp = await sarvam_client.post("/translate", json=payload)
            resp.raise_for_status()
            return (resp.json() or {}).get("translated_text", "") or ""
        except Exception as e:
            logger.warning(f"[INDIC-SANITIZE] test-fire translate failed: {e}")
            return ""

    # Note: regenerate_callable is intentionally NOT wired — admins
    # testing `regenerate` / `translate+regenerate` see only the
    # translate+strip branches. Wiring a real LLM regenerate from a
    # synthetic test sample would require a chat context the admin
    # doesn't have here. The diagnostic dict makes the skipped step
    # explicit (`regenerated: false`) so this is not misleading.
    cleaned, diag = await _sanitize(
        sample,
        translate_callable=_translate_callable,
    )
    return {
        "ok": True,
        "raw": sample,
        "cleaned": cleaned,
        "diag": diag,
        "config": _asm_cfg(),
    }
# ──────────────────────────────────────────────────────────────────────

_LANG_LABELS = {
    "as": "Assamese (অসমীয়া)", "as-IN": "Assamese (অসমীয়া)",
    "bn": "Bengali (বাংলা)", "bn-IN": "Bengali (বাংলা)",
    "en": "English", "en-IN": "English (India)",
    "gu": "Gujarati (ગુજરાતી)", "gu-IN": "Gujarati (ગુજરાતી)",
    "hi": "Hindi (हिन्दी)", "hi-IN": "Hindi (हिन्दी)",
    "kn": "Kannada (ಕನ್ನಡ)", "kn-IN": "Kannada (ಕನ್ನಡ)",
    "ml": "Malayalam (മലയാളം)", "ml-IN": "Malayalam (മലയാളം)",
    "mr": "Marathi (मराठी)", "mr-IN": "Marathi (मराठी)",
    "od": "Odia (ଓଡ଼ିଆ)", "od-IN": "Odia (ଓଡ଼ିଆ)",
    "pa": "Punjabi (ਪੰਜਾਬੀ)", "pa-IN": "Punjabi (ਪੰਜਾਬੀ)",
    "ta": "Tamil (தமிழ்)", "ta-IN": "Tamil (தமிழ்)",
    "te": "Telugu (తెలుగు)", "te-IN": "Telugu (తెలుగు)",
}

@router.get("/admin/translation/languages")
async def admin_translation_languages(admin: dict = Depends(get_admin_user)):
    """Return supported translation languages as {code, label} list."""
    seen_base = set()
    result = []
    for code in sorted(_SARVAM_LANG_CODES):
        base = code.split("-")[0]
        if base in seen_base:
            continue
        seen_base.add(base)
        label = _LANG_LABELS.get(code) or _LANG_LABELS.get(base) or code
        result.append({"code": base, "label": label})
    return result

@router.post("/sarvam/translate")
async def sarvam_translate(data: dict):
    """Translate text between Indian languages via Sarvam AI."""
    if not sarvam_client:
        raise HTTPException(status_code=503, detail="Sarvam AI not configured")
    text = (data.get("text") or data.get("input") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    src = _normalise_lang(data.get("source_language_code", "en-IN"))
    tgt = _normalise_lang(data.get("target_language_code", "as-IN"))

    # Check cache first
    cache_key = _sarvam_cache_key("translate", {"text": text, "src": src, "tgt": tgt})
    cached = _get_content_cache(cache_key)
    if cached:
        return {**cached, "cached": True}

    # mayura:v1 supports: hi, bn, mr, te, kn, ml, ta, gu, pa
    # sarvam-translate:v1 supports all Indic langs including as, od
    _MAYURA_LANGS = {"hi-IN", "bn-IN", "mr-IN", "te-IN", "kn-IN", "ml-IN", "ta-IN", "gu-IN", "pa-IN"}
    model = "mayura:v1" if (src in _MAYURA_LANGS and tgt in _MAYURA_LANGS) else "sarvam-translate:v1"
    payload = {
        "input": text,
        "source_language_code": src,
        "target_language_code": tgt,
        "speaker_gender": data.get("speaker_gender", "Female"),
        "mode": data.get("mode", "formal"),
        "model": model,
        "enable_preprocessing": False,
    }
    try:
        resp = await sarvam_client.post("/translate", json=payload)
        resp.raise_for_status()
        result = resp.json()
        out = {"translated_text": result.get("translated_text", ""), "source": src, "target": tgt}
        _set_content_cache(cache_key, out)
        return out
    except httpx.HTTPStatusError as e:
        logger.error(f"Sarvam translate error {e.response.status_code} [{src}->{tgt}]")
        raise HTTPException(status_code=e.response.status_code, detail="Sarvam translation failed")
    except Exception as e:
        logger.error(f"Sarvam translate exception: {type(e).__name__} [{src}->{tgt}]")
        raise HTTPException(status_code=502, detail="Sarvam AI unreachable")

@router.post("/sarvam/tts")
async def sarvam_tts(data: dict):
    """Convert text to speech in Indian languages via Sarvam AI (Bulbul model)."""
    if not sarvam_client:
        raise HTTPException(status_code=503, detail="Sarvam AI not configured")
    text = (data.get("text") or data.get("input") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    # Sarvam TTS max input ~500 chars per request
    if len(text) > 500:
        text = text[:500]
    lang = _normalise_lang(data.get("target_language_code", "en-IN"))

    # Cache audio as base64
    cache_key = _sarvam_cache_key("tts", {"text": text, "lang": lang,
        "speaker": data.get("speaker", "meera"), "pace": data.get("pace", 1.0)})
    cached = _get_content_cache(cache_key)
    if cached:
        return {**cached, "cached": True}

    # Valid Sarvam TTS speakers (updated list)
    _VALID_SPEAKERS = {
        "anushka", "abhilash", "manisha", "vidya", "arya", "karun", "hitesh",
        "aditya", "ritu", "priya", "neha", "rahul", "pooja", "rohan", "simran",
        "kavya", "amit", "dev", "ishita", "shreya", "ratan", "varun", "manan",
        "sumit", "roopa", "kabir", "aayan", "shubh", "ashutosh", "advait",
        "amelia", "sophia", "anand", "tanya", "tarun", "sunny", "mani", "gokul",
        "vijay", "shruti", "suhani", "mohit", "kavitha", "rehan", "soham", "rupali",
    }
    speaker = data.get("speaker", "anushka")
    if speaker not in _VALID_SPEAKERS:
        speaker = "anushka"
    payload = {
        "inputs": [text],
        "target_language_code": lang,
        "speaker": speaker,
        "model": data.get("model", "bulbul:v2"),
        "pitch": data.get("pitch", 0),
        "pace": data.get("pace", 1.0),
        "loudness": data.get("loudness", 1.5),
        "speech_sample_rate": data.get("speech_sample_rate", 22050),
        "enable_preprocessing": False,
    }
    import time as _t_tts
    _tts_t0 = _t_tts.perf_counter()
    try:
        resp = await sarvam_client.post("/text-to-speech", json=payload)
        resp.raise_for_status()
        result = resp.json()
        audios = result.get("audios", [])
        if not audios:
            raise HTTPException(status_code=502, detail="Sarvam TTS returned no audio")
        out = {
            "audio_base64": audios[0],
            "language": lang,
            "format": "wav",
            "sample_rate": payload["speech_sample_rate"],
        }
        _set_content_cache(cache_key, out)
        return out
    except httpx.HTTPStatusError as e:
        logger.error(f"Sarvam TTS error {e.response.status_code} [{lang}]")
        # Task #636 — Workers AI fallback for retryable upstream failures.
        _primary_ms = int((_t_tts.perf_counter() - _tts_t0) * 1000)
        try:
            from providers import workers_ai as _wai
            if _wai.is_enabled("tts") and _wai.should_fallback(e):
                ok, val, _ = await _wai.attempt_fallback(
                    "tts", e, _primary_ms,
                    lambda: _wai.call_tts(text, lang=lang.split("-")[0] if lang else "en"),
                )
                if ok and val and val.get("audio_base64"):
                    out = {
                        "audio_base64": val["audio_base64"],
                        "language": lang,
                        "format": val.get("format", "wav"),
                        "sample_rate": payload["speech_sample_rate"],
                        "provider": "workers-ai",
                    }
                    return out
        except Exception as _wai_err:  # noqa: BLE001
            logger.warning(f"[workers-ai] tts fallback skipped: {type(_wai_err).__name__}: {str(_wai_err)[:150]}")
        raise HTTPException(status_code=e.response.status_code, detail="Sarvam TTS failed")
    except Exception as e:
        logger.error(f"Sarvam TTS exception: {type(e).__name__} [{lang}]")
        _primary_ms = int((_t_tts.perf_counter() - _tts_t0) * 1000)
        try:
            from providers import workers_ai as _wai
            if _wai.is_enabled("tts") and _wai.should_fallback(e):
                ok, val, _ = await _wai.attempt_fallback(
                    "tts", e, _primary_ms,
                    lambda: _wai.call_tts(text, lang=lang.split("-")[0] if lang else "en"),
                )
                if ok and val and val.get("audio_base64"):
                    return {
                        "audio_base64": val["audio_base64"],
                        "language": lang,
                        "format": val.get("format", "wav"),
                        "sample_rate": payload["speech_sample_rate"],
                        "provider": "workers-ai",
                    }
        except Exception as _wai_err:  # noqa: BLE001
            logger.warning(f"[workers-ai] tts fallback skipped: {type(_wai_err).__name__}: {str(_wai_err)[:150]}")
        raise HTTPException(status_code=502, detail="Sarvam AI unreachable")

@router.post("/sarvam/transliterate")
async def sarvam_transliterate(data: dict):
    """Transliterate text between scripts via Sarvam AI."""
    if not sarvam_client:
        raise HTTPException(status_code=503, detail="Sarvam AI not configured")
    text = (data.get("text") or data.get("input") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    src = _normalise_lang(data.get("source_language_code", "en-IN"))
    tgt = _normalise_lang(data.get("target_language_code", "as-IN"))

    cache_key = _sarvam_cache_key("transliterate", {"text": text, "src": src, "tgt": tgt})
    cached = _get_content_cache(cache_key)
    if cached:
        return {**cached, "cached": True}

    payload = {
        "input": text,
        "source_language_code": src,
        "target_language_code": tgt,
        "spoken_language_code": src,
        "with_timestamps": False,
        "numerals_format": "international",
    }
    try:
        resp = await sarvam_client.post("/transliterate", json=payload)
        resp.raise_for_status()
        result = resp.json()
        out = {"transliterated_text": result.get("transliterated_text", ""), "source": src, "target": tgt}
        _set_content_cache(cache_key, out)
        return out
    except httpx.HTTPStatusError as e:
        logger.error(f"Sarvam transliterate error {e.response.status_code} [{src}->{tgt}]")
        raise HTTPException(status_code=e.response.status_code, detail="Sarvam transliteration failed")
    except Exception as e:
        logger.error(f"Sarvam transliterate exception: {type(e).__name__} [{src}->{tgt}]")
        raise HTTPException(status_code=502, detail="Sarvam AI unreachable")

# ─────────────────────────────────────────────
# BOT RENDER MIDDLEWARE (production SSR for AI crawlers)
# ─────────────────────────────────────────────
_BOT_UA_RE = re.compile(
    r"googlebot|google-extended|googleother|google-inspectiontool|"
    r"bingbot|yandexbot|yandex|duckduckbot|slurp|baiduspider|"
    r"facebookexternalhit|facebookbot|twitterbot|linkedinbot|telegrambot|whatsapp|"
    r"applebot|applebot-extended|ia_archiver|msnbot|ahrefsbot|semrushbot|petalbot|"
    r"gptbot|oai-searchbot|chatgpt-user|claudebot|anthropic-ai|perplexitybot|"
    r"meta-externalagent|cohere-ai|bytespider|ccbot",
    re.IGNORECASE,
)

# Task #499: keep /history (deep auth-only path) and admin API/console
# routes as bot-skip, but DO NOT skip the /profile and /admin/login
# auth-shell URLs anymore — bots that hit them must still receive a
# byte-zero <link rel="canonical"> pointing at the route's own URL so
# the Lighthouse `canonical` SEO audit passes. The narrower /admin/api
# and /admin/console prefixes below keep the actual admin surface
# off-limits to crawlers.
_BOT_SKIP_PREFIXES = (
    "/api/", "/admin/api", "/admin/console", "/history", "/static/",
    "/health", "/docs", "/openapi.json", "/assets/", "/icons/",
    "/fonts/", "/robots.txt", "/sitemap",
)

_VALID_PAGE_TYPES = {"notes", "definition", "important-questions", "mcqs", "examples"}

_bot_html_cache: cachetools.TTLCache = cachetools.TTLCache(maxsize=512, ttl=3600)


def _extract_faq_items(content: str, title: str = "") -> list[dict]:
    """Extract FAQ Q&A pairs from content text for FAQPage JSON-LD.
    Looks for lines ending in '?' followed by answer lines. Falls back to
    generating a canonical 'What is X?' FAQ from the title and description."""
    faq_items = []
    if content:
        lines = content.split("\n")
        current_q = None
        for line in lines:
            stripped = line.strip().lstrip("#").strip().replace("**", "").strip()
            if stripped.endswith("?") and len(stripped) > 15:
                current_q = stripped
            elif current_q and len(stripped) > 20:
                faq_items.append({
                    "@type": "Question",
                    "name": current_q,
                    "acceptedAnswer": {"@type": "Answer", "text": stripped},
                })
                current_q = None
                if len(faq_items) >= 10:
                    break
    if not faq_items and title:
        faq_items.append({
            "@type": "Question",
            "name": f"What is {title}?",
            "acceptedAnswer": {
                "@type": "Answer",
                "text": f"{title} is a topic covered on Syrabit.ai with detailed study notes, examples, and practice questions for exam preparation.",
            },
        })
    return faq_items


def _bot_html_response(html: str, *, robots_tag: str = "index, follow"):
    """Wrap a bot-render HTML payload in an HTMLResponse.

    Task #499: callers can pass `robots_tag="noindex, follow"` for
    auth-shell routes (/login, /signup, /profile, /admin/login) so the
    HTTP `X-Robots-Tag` header agrees with the noindex meta in the body
    instead of overriding it with a global `index, follow`.
    """
    return HTMLResponse(
        content=html, status_code=200,
        headers={
            "Cache-Control": "public, max-age=3600, s-maxage=86400, stale-while-revalidate=86400",
            "X-Bot-Rendered": "1",
            "X-Robots-Tag": robots_tag,
            "Content-Language": "en-IN",
        },
    )


# Bot-render counters — were previously in-memory module globals which
# caused the "Bot Renders" admin tile to perpetually show 0 because:
#   1. Every gunicorn worker restart wiped the counters back to zero.
#   2. Each gunicorn worker maintained its own private copy, so the
#      value the admin saw depended on which worker handled the
#      dashboard request — not the true cluster-wide total.
# Now mirrored to MongoDB via atomic $inc so the counters survive
# restarts and aggregate across workers. (Tried Redis first but
# `deps.redis_client` is permanently None in this codebase — never
# assigned — so cache layers silently no-op. Mongo IS wired and is
# the actual durable persistence layer here.)
# The in-memory dict is kept as a hot fallback that can absorb writes
# while Mongo is briefly unavailable.
_bot_render_fallback_count = 0
_bot_render_success_count = 0
_bot_render_by_type: dict = {}
# Single-document doc id used in the `metrics_counters` collection.
_BOT_RENDER_MONGO_ID = "bot_render_v1"
# Keep strong refs to fire-and-forget Mongo writes so the loop's
# weak reference doesn't garbage-collect the task before it runs —
# a well-known asyncio footgun. Tasks self-remove on completion via
# the discard callback below.
_bot_render_pending_tasks: set = set()
# Bound the cardinality of `by_page_type` field names so the single
# Mongo doc can never balloon. Anything passed in that isn't on this
# allowlist is bucketed as "other" — the four dynamic call sites in
# the catch-all middleware previously split the URL slug, which in
# the worst case (chapter / subject pages) would have written one
# field per unique URL into the doc and eventually breached Mongo's
# 16MB document limit.
_BOT_RENDER_KNOWN_TYPES = frozenset({
    "about", "chat", "curriculum", "exam-routine",
    "board", "board_class", "static_page", "learn",
    "subject", "subject_id", "chapter", "page",
})


def _normalize_page_type(page_type) -> str:
    if not page_type:
        return "other"
    pt = str(page_type).strip().lower()
    if pt in _BOT_RENDER_KNOWN_TYPES:
        return pt
    return "other"


def _bot_render_task_done(task):  # noqa: ANN001 — asyncio callback signature
    _bot_render_pending_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        try:
            logger.warning(f"bot_render mongo persist failed: {exc!r}")
        except Exception:
            pass


def _bot_render_mongo_snapshot():
    """Pull the cluster-wide bot-render counters from Mongo. Returns
    None if Mongo is unreachable, the doc is missing, or any decode
    fails — caller falls back to the in-memory counters in that case
    so the tile still renders a (degraded) value instead of 500'ing."""
    if db is None:
        return None
    try:
        # The dashboard metrics endpoint is sync (uses asyncio.run) so
        # use the sync motor accessor — `db` is async-only. Build a
        # short-lived sync client via PyMongo if available.
        # In practice get_bot_render_metrics is invoked from an async
        # FastAPI handler, so awaiting works. But to keep the helper
        # callable from both sync/async contexts we delegate to the
        # async path via run_until_complete-on-new-loop only if needed.
        import asyncio as _asyncio
        coro = db["metrics_counters"].find_one({"_id": _BOT_RENDER_MONGO_ID})
        try:
            loop = _asyncio.get_event_loop()
            if loop.is_running():
                # Called from inside a running loop — caller (FastAPI)
                # should await us instead. Fall back to in-memory to
                # avoid blocking the loop.
                return None
            doc = loop.run_until_complete(coro)
        except RuntimeError:
            doc = _asyncio.run(coro)
        if not doc:
            return None
        success = int(doc.get("success_count", 0))
        fallback = int(doc.get("fallback_count", 0))
        by_type = {k: int(v) for k, v in (doc.get("by_page_type") or {}).items()}
        return {"success": success, "fallback": fallback, "by_type": by_type}
    except Exception:
        return None


async def _bot_render_mongo_snapshot_async():
    """Async-safe version of the snapshot pull. Used by the dashboard
    metrics endpoint which already runs inside the FastAPI event loop."""
    if db is None:
        return None
    try:
        doc = await db["metrics_counters"].find_one({"_id": _BOT_RENDER_MONGO_ID})
        if not doc:
            return None
        success = int(doc.get("success_count", 0))
        fallback = int(doc.get("fallback_count", 0))
        by_type = {k: int(v) for k, v in (doc.get("by_page_type") or {}).items()}
        return {"success": success, "fallback": fallback, "by_type": by_type}
    except Exception:
        return None


async def get_bot_render_metrics_async():
    """Async entry point — preferred when called from FastAPI handlers
    (which are themselves async). Reads the durable Mongo counters
    directly without juggling event loops, then falls back to the
    in-memory counters for this worker if Mongo is unreachable."""
    snap = await _bot_render_mongo_snapshot_async()
    if snap is not None:
        success = snap["success"]
        fallback = snap["fallback"]
        by_type = snap["by_type"]
    else:
        success = _bot_render_success_count
        fallback = _bot_render_fallback_count
        by_type = dict(_bot_render_by_type)
    total = success + fallback
    return {
        "fallback_count": fallback,
        "success_count": success,
        "total_requests": total,
        "success_rate_pct": round(success / max(total, 1) * 100, 1),
        "by_page_type": by_type,
    }


def get_bot_render_metrics():
    """Sync entry point — kept for backward compatibility with any
    sync callers. Async callers should prefer get_bot_render_metrics_async
    so we can read the durable Mongo counters even when the event loop
    is already running."""
    snap = _bot_render_mongo_snapshot()
    if snap is not None:
        # Combine durable + in-flight in-memory counts. The in-memory
        # values for THIS worker may have been double-counted into Mongo
        # already on each _track_bot_render call, so just trust Mongo.
        success = snap["success"]
        fallback = snap["fallback"]
        by_type = snap["by_type"]
    else:
        success = _bot_render_success_count
        fallback = _bot_render_fallback_count
        by_type = dict(_bot_render_by_type)
    total = success + fallback
    return {
        "fallback_count": fallback,
        "success_count": success,
        "total_requests": total,
        "success_rate_pct": round(success / max(total, 1) * 100, 1),
        "by_page_type": by_type,
    }


def _track_bot_render(page_type: str, success: bool):
    global _bot_render_success_count, _bot_render_fallback_count
    if success:
        _bot_render_success_count += 1
    else:
        _bot_render_fallback_count += 1
    # Normalize so unknown / dynamic slugs collapse to "other" — this
    # is what bounds the cardinality of by_page_type both in memory
    # and in the persisted Mongo doc.
    norm_type = _normalize_page_type(page_type)
    key = f"{norm_type}:{'ok' if success else 'fail'}"
    _bot_render_by_type[key] = _bot_render_by_type.get(key, 0) + 1
    # Mirror to Mongo so the counter survives worker restarts and
    # aggregates atomically across all gunicorn workers. Fire-and-
    # forget on the running event loop; if there isn't one (e.g. unit
    # tests) we silently skip the durable write — the in-memory dict
    # above is still authoritative for this worker so we don't lose
    # accuracy locally. Errors are swallowed so a transient Mongo
    # blip never breaks the actual bot-render response path.
    if db is None:
        return
    try:
        import asyncio as _asyncio
        try:
            loop = _asyncio.get_running_loop()
        except RuntimeError:
            # Not in an async context — skip the durable write. The
            # in-memory counters above are still accurate for this
            # worker. Real bot-render middleware always runs inside
            # the FastAPI loop so this path is only hit by tests.
            return
        update = {
            "$inc": {
                "success_count" if success else "fallback_count": 1,
                f"by_page_type.{key}": 1,
            },
            "$currentDate": {"updated_at": True},
        }
        task = loop.create_task(
            db["metrics_counters"].update_one(
                {"_id": _BOT_RENDER_MONGO_ID}, update, upsert=True
            )
        )
        # Strong ref + done callback — without this asyncio is free to
        # GC the task before it starts, silently dropping the write.
        _bot_render_pending_tasks.add(task)
        task.add_done_callback(_bot_render_task_done)
    except Exception as e:
        try:
            logger.warning(f"bot_render mongo schedule failed: {e!r}")
        except Exception:
            pass


_BOT_KNOWN_BOARDS = {"ahsec", "seba", "degree", "cbse", "nep"}


def derive_bot_cache_key(path: str):
    """Derive the bot-render cache key for a given URL path.

    Returns a string cache key for supported page types, or None when the
    middleware should fall through to the regular handler.
    """
    path = (path or "/").rstrip("/") or "/"
    if "." in path.split("/")[-1]:
        return None
    parts = [p for p in path.split("/") if p]
    n = len(parts)

    if n == 0 or (n == 1 and parts[0] == "library"):
        return "_homepage_"
    # Task #499: /home is the public LandingPage; treat it as its own
    # bot-render target so the rendered HTML carries the route's own
    # canonical (https://syrabit.ai/home) instead of inheriting the
    # homepage canonical and failing the Lighthouse `canonical` audit.
    if n == 1 and parts[0] == "home":
        return "_home_"
    if n == 1 and parts[0] == "pricing":
        return "_pricing_"
    if n == 1 and parts[0] == "terms":
        return "_terms_"
    if n == 1 and parts[0] == "privacy":
        return "_privacy_"
    if n == 1 and parts[0] == "about":
        return "_about_"
    # Task #499: /technology is a public marketing page that previously
    # fell through to the SPA shell for bots — emit a route-specific
    # canonical so it stops failing the SEO audit.
    if n == 1 and parts[0] == "technology":
        return "_technology_"
    if n == 1 and parts[0] == "chat":
        return "_chat_"
    if n == 1 and parts[0] == "curriculum":
        return "_curriculum_"
    if n == 1 and parts[0] == "exam-routine":
        return "_exam_routine_"
    # Task #499: auth-gated shells still need a route-specific canonical
    # in the byte-zero HTML for crawlers, even though they ship
    # `noindex, follow`. Without this, bots that hit /login, /signup,
    # /profile, /admin/login fall through to the SPA shell with no
    # canonical and the Lighthouse `canonical` SEO audit fails on
    # those URLs.
    if n == 1 and parts[0] in ("login", "signup", "profile"):
        return f"_authshell_/{parts[0]}"
    if n == 2 and parts[0] == "admin" and parts[1] == "login":
        return "_authshell_/admin-login"
    if n == 1 and parts[0] in _BOT_KNOWN_BOARDS:
        return f"_board_/{parts[0]}"
    if n == 2 and parts[0] == "learn":
        return f"_learn_/{parts[1]}"
    if n == 2 and parts[0] == "pyq":
        return f"_pyq_/{parts[1]}"
    if n == 2 and parts[0] == "subject":
        return f"_subject_id_/{parts[1]}"
    if n == 2 and parts[0] in _BOT_KNOWN_BOARDS:
        return f"_board_class_/{parts[0]}/{parts[1]}"
    if n == 3:
        return f"_subj_/{parts[0]}/{parts[1]}/{parts[2]}"
    if n in (4, 5):
        page_type_part = parts[4] if n == 5 else None
        if page_type_part and page_type_part not in _VALID_PAGE_TYPES:
            return None
        current_type = page_type_part or "notes"
        return f"{parts[0]}/{parts[1]}/{parts[2]}/{parts[3]}/{current_type}"
    return None


class BotRenderMiddleware(BaseHTTPMiddleware):
    """Intercept requests from bot user-agents and return pre-rendered HTML.

    Handles:
    - /                                  → homepage
    - /library                           → homepage (same listing)
    - /pricing                           → pricing page
    - /terms                             → terms page
    - /privacy                           → privacy page
    - /learn/{slug}                      → CMS document page
    - /pyq/{slug}                        → PYQ HTML replica (html only)
    - /curriculum                        → curriculum map page
    - /exam-routine                      → exam routine page
    - /{board}                           → board landing page
    - /{board}/{class}                   → board+class landing page
    - /{board}/{class}/{subject}         → subject landing page
    - /{board}/{class}/{subject}/{topic}      → topic page (notes)
    - /{board}/{class}/{subject}/{topic}/{type} → topic page (typed)
    """

    async def _render_chapter_fallback(self, board: str, class_slug: str, subject_slug: str, chapter_slug: str):
        try:
            from deps import db, is_mongo_available
            if not await is_mongo_available():
                return None
            subj = await db.subjects.find_one(
                {"board_slug": board, "class_slug": class_slug, "slug": subject_slug},
                {"_id": 0, "id": 1, "name": 1, "board_slug": 1, "class_slug": 1},
            )
            if not subj:
                return None
            chapter = await db.chapters.find_one(
                {"subject_id": subj["id"], "slug": chapter_slug},
                {"_id": 0, "title": 1, "description": 1, "content": 1,
                 "topics": 1, "content_as": 1, "bing_keywords": 1,
                 "seo_bundle": 1},
            )
            if not chapter:
                return None
            ch_title_raw = chapter.get("title", chapter_slug)
            ch_title = _html_mod.escape(ch_title_raw)
            base_desc = (chapter.get("description") or "")[:300]
            # Task #333: prefer Bing-derived terms when the monthly
            # refresh has run for this chapter; otherwise fall back to
            # the same static template `ChapterPage.jsx` uses so brand-
            # new chapters still ship a `<meta name="keywords">` tag
            # (Google ignores it but Bing/Yandex still use it).
            bing_kw_terms = []
            bing_kw_list = chapter.get("bing_keywords") or []
            if isinstance(bing_kw_list, list) and bing_kw_list:
                for kw in bing_kw_list[:20]:
                    term = (kw.get("keyword") if isinstance(kw, dict) else kw) or ""
                    term = term.strip()
                    if term:
                        bing_kw_terms.append(term)

            raw_title = chapter.get("title", chapter_slug) or chapter_slug
            raw_subj = subj.get("name", subject_slug) or subject_slug
            board_label = board.replace("-", " ").upper()
            class_label = class_slug.replace("-", " ")
            static_terms = [
                raw_title,
                f"{raw_title} notes",
                f"{raw_title} {raw_subj}",
                f"{raw_title} MCQ",
                f"{raw_title} important questions",
                f"{raw_subj} {class_label}",
                f"{board_label} notes",
                "AHSEC", "SEBA", "exam preparation",
            ]
            seen_kw = set()
            merged_terms = []
            for term in (*bing_kw_terms, *static_terms):
                key_lower = term.strip().lower()
                if not key_lower or key_lower in seen_kw:
                    continue
                seen_kw.add(key_lower)
                merged_terms.append(term.strip())
            kw_attr = _html_mod.escape(", ".join(merged_terms))
            bing_kw_meta = f'<meta name="keywords" content="{kw_attr}">' if merged_terms else ""

            # Task #333: seed the meta description with the top Bing
            # search terms when they're not already mentioned. Same
            # contract as ChapterPage.jsx: append "Covers a, b, c." when
            # the base description has room (<180 chars) and there are
            # net-new terms to add. Cap at 300 chars total.
            if bing_kw_terms and len(base_desc) < 180:
                desc_lower = base_desc.lower()
                extra_desc = [t for t in bing_kw_terms[:3]
                              if t.lower() not in desc_lower]
                if extra_desc:
                    base_desc = (base_desc.rstrip(". ") +
                                 (". " if base_desc else "") +
                                 f"Covers {', '.join(extra_desc)}.")[:300]
            ch_desc = _html_mod.escape(base_desc)

            # Task #333: when the top Bing term is a distinct, short
            # phrase not already in the deterministic title, append it
            # parenthetically. Bounded at 70 chars to stay within SERP.
            base_title_text = f"{ch_title_raw} | {subj.get('name', subject_slug)} | Syrabit.ai"
            top_bing_for_title = next(
                (t for t in bing_kw_terms
                 if t.lower() != (ch_title_raw or "").lower()
                 and t.lower() not in base_title_text.lower()
                 and len(t) <= 40),
                None,
            )
            if top_bing_for_title and (len(base_title_text) + len(top_bing_for_title) + 3) <= 70:
                page_title_text = f"{ch_title_raw} ({top_bing_for_title}) | {subj.get('name', subject_slug)} | Syrabit.ai"
            else:
                page_title_text = base_title_text
            page_title_html = _html_mod.escape(page_title_text)
            subj_name = _html_mod.escape(subj.get("name", subject_slug))
            page_url = f"https://syrabit.ai/{board}/{class_slug}/{subject_slug}/{chapter_slug}"

            # Task #582 follow-up: when the monthly SEO refresh has
            # produced a `seo_bundle`, prefer its meta_title /
            # meta_description / og_* / twitter_* fields. They were
            # generated by Gemini against the same Bing+Suggest term
            # mix and consistently outperform the deterministic
            # template (longer-tail, AS/IN-localised, geo-aware).
            # Falls back to the deterministic strings above when the
            # bundle is absent or malformed.
            seo_bundle = chapter.get("seo_bundle") or {}
            if isinstance(seo_bundle, dict):
                bundle_title = (seo_bundle.get("meta_title") or "").strip()
                bundle_desc = (seo_bundle.get("meta_description") or "").strip()
                if bundle_title and len(bundle_title) <= 70:
                    page_title_html = _html_mod.escape(bundle_title)
                if bundle_desc:
                    ch_desc = _html_mod.escape(bundle_desc[:300])
            og_title_html = _html_mod.escape(
                (seo_bundle.get("og_title") or "").strip()
                if isinstance(seo_bundle, dict) else ""
            ) or page_title_html
            og_desc_html = _html_mod.escape(
                (seo_bundle.get("og_description") or "").strip()[:300]
                if isinstance(seo_bundle, dict) else ""
            ) or ch_desc
            # `seo_keyword_service` emits canonical fields
            # `twitter_title` / `twitter_description`. Prefer those when
            # present, else fall back to OG values, else the
            # deterministic page title / description above.
            tw_title_raw = ""
            tw_desc_raw = ""
            if isinstance(seo_bundle, dict):
                tw_title_raw = (seo_bundle.get("twitter_title") or "").strip()
                tw_desc_raw = (seo_bundle.get("twitter_description") or "").strip()[:300]
            twitter_title_html = (_html_mod.escape(tw_title_raw)
                                  if tw_title_raw else og_title_html)
            twitter_description_html = (_html_mod.escape(tw_desc_raw)
                                        if tw_desc_raw else og_desc_html)
            twitter_card = (
                (seo_bundle.get("twitter_card") or "summary_large_image").strip()
                if isinstance(seo_bundle, dict) else "summary_large_image"
            )
            twitter_card_html = _html_mod.escape(twitter_card or "summary_large_image")
            # Canonical `geo_tags` keys from `seo_keyword_service` are
            # "geo.region" / "geo.placename" / "icbm" / "language".
            # Accept the legacy `region`/`position` aliases so older
            # cached bundles keep emitting tags during the rollout
            # window.
            geo_region = ""
            geo_position = ""
            icbm = ""
            if isinstance(seo_bundle, dict):
                geo_tags = seo_bundle.get("geo_tags") or {}
                if isinstance(geo_tags, dict):
                    geo_region = _html_mod.escape(
                        (geo_tags.get("geo.region")
                         or geo_tags.get("region") or "").strip()
                    )
                    geo_position = _html_mod.escape(
                        (geo_tags.get("geo.position")
                         or geo_tags.get("position") or "").strip()
                    )
                    icbm = _html_mod.escape(
                        (geo_tags.get("icbm") or "").strip()
                    )
                    # If position is missing but ICBM exists, derive
                    # `geo.position` from it (ICBM is "lat, lon",
                    # geo.position is "lat;lon").
                    if not geo_position and icbm:
                        parts = [p.strip() for p in icbm.split(",")]
                        if len(parts) == 2:
                            geo_position = _html_mod.escape(
                                f"{parts[0]};{parts[1]}"
                            )
            geo_meta_html = "".join(filter(None, [
                f'<meta name="geo.region" content="{geo_region}">' if geo_region else "",
                f'<meta name="geo.position" content="{geo_position}">' if geo_position else "",
                f'<meta name="ICBM" content="{icbm}">' if icbm else "",
            ]))
            ch_has_as = bool((chapter.get("content_as") or "").strip())
            topics_list = chapter.get("topics", [])
            topics_html = ""
            if topics_list:
                items = "".join(f"<li>{_html_mod.escape(str(t.get('title', t) if isinstance(t, dict) else t))}</li>" for t in topics_list[:30])
                topics_html = f"<h2>Topics</h2><ul>{items}</ul>"
            content_preview = _html_mod.escape((chapter.get("content") or "")[:2000])
            schema = json.dumps({
                "@context": "https://schema.org",
                "@type": "Course",
                "name": chapter.get("title", ""),
                "description": chapter.get("description", ""),
                "url": page_url,
                "provider": {"@type": "EducationalOrganization", "name": "Syrabit.ai", "url": "https://syrabit.ai"},
                "isPartOf": {"@type": "Course", "name": subj.get("name", ""), "url": f"https://syrabit.ai/{board}/{class_slug}/{subject_slug}"},
            }, ensure_ascii=False)
            return f"""<!DOCTYPE html>
<html lang="en-IN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{page_title_html}</title>
<meta name="description" content="{ch_desc}">
{bing_kw_meta}
<link rel="canonical" href="{page_url}">
<meta property="og:title" content="{og_title_html}">
<meta property="og:description" content="{og_desc_html}">
<meta property="og:url" content="{page_url}">
<meta property="og:type" content="article">
<meta property="og:site_name" content="Syrabit.ai">
<meta name="twitter:card" content="{twitter_card_html}">
<meta name="twitter:title" content="{twitter_title_html}">
<meta name="twitter:description" content="{twitter_description_html}">
{geo_meta_html}
<meta name="robots" content="index, follow">
<meta http-equiv="content-language" content="en-IN">
{('<link rel="alternate" hreflang="en" href="' + page_url + '">' + chr(10) +
  '<link rel="alternate" hreflang="as" href="' + page_url + '?lang=as">' + chr(10) +
  '<link rel="alternate" hreflang="x-default" href="' + page_url + '">') if ch_has_as else
 ('<link rel="alternate" hreflang="en-IN" href="' + page_url + '">')}
<script type="application/ld+json">{schema}</script>
</head>
<body>
<nav><a href="https://syrabit.ai">Home</a> &rsaquo; <a href="https://syrabit.ai/{board}/{class_slug}/{subject_slug}">{subj_name}</a> &rsaquo; <span>{ch_title}</span></nav>
<h1>{ch_title}</h1>
{f'<p>{ch_desc}</p>' if ch_desc else ''}
{topics_html}
{f'<div>{content_preview}</div>' if content_preview else ''}
<footer><a href="https://syrabit.ai/library">Library</a> &middot; <a href="https://syrabit.ai/sitemap.xml">Sitemap</a></footer>
</body>
</html>"""
        except Exception as e:
            logger.error(f"BotRenderMiddleware chapter fallback failed: {e}")
            return None

    async def _safe_call_next(self, request, call_next):
        try:
            return await call_next(request)
        except Exception as exc:
            logger.warning(f"BotRenderMiddleware downstream error: {exc}")
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=500, content={"detail": "Internal server error"})

    async def dispatch(self, request: StarletteRequest, call_next):
        global _bot_render_fallback_count, _bot_render_success_count
        ua = request.headers.get("user-agent", "")
        if not _BOT_UA_RE.search(ua):
            return await self._safe_call_next(request, call_next)

        path = request.url.path.rstrip("/") or "/"
        for prefix in _BOT_SKIP_PREFIXES:
            if path.startswith(prefix):
                return await self._safe_call_next(request, call_next)

        if "." in path.split("/")[-1]:
            return await self._safe_call_next(request, call_next)

        cache_key = derive_bot_cache_key(path)
        if cache_key is None:
            return await self._safe_call_next(request, call_next)
        parts = [p for p in path.split("/") if p]
        n = len(parts)

        cached_html = _bot_html_cache.get(cache_key)
        if cached_html:
            # Task #499: auth-shell cache keys must keep their
            # `noindex, follow` X-Robots-Tag on cache hit too — without
            # this, the first uncached response is correctly tagged
            # but every subsequent cached one reverts to the default
            # `index, follow` and undercuts the noindex meta in body.
            cached_robots = "noindex, follow" if cache_key.startswith("_authshell_/") else "index, follow"
            return _bot_html_response(cached_html, robots_tag=cached_robots)

        try:
            _seo_port = int(os.environ.get("PORT", "8000"))
            api_base = f"http://localhost:{_seo_port}/api/seo"

            if cache_key == "_about_":
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.get(f"{api_base}/html/about")
                if resp.status_code == 200:
                    html_content = resp.text
                    _bot_html_cache[cache_key] = html_content
                    _track_bot_render("about", True)
                    return _bot_html_response(html_content)
                _track_bot_render("about", False)
                logger.error(f"BotRenderMiddleware SEO fallback: /about returned {resp.status_code}")
                return await self._safe_call_next(request, call_next)

            if cache_key == "_chat_":
                html_content = """<!DOCTYPE html>
<html lang="en-IN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Syra AI — Your Study Assistant | Syrabit.ai</title>
<meta name="description" content="Ask Syra anything about your syllabus. AI-powered study assistant for AHSEC, SEBA &amp; Degree students in Assam.">
<link rel="canonical" href="https://syrabit.ai/chat">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Syrabit.ai">
<meta property="og:title" content="Syra AI — Your Study Assistant | Syrabit.ai">
<meta property="og:description" content="Ask Syra anything about your syllabus. AI-powered study assistant for AHSEC, SEBA &amp; Degree students in Assam.">
<meta property="og:url" content="https://syrabit.ai/chat">
<meta property="og:image" content="https://syrabit.ai/opengraph.jpg">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="Syra AI — Your Study Assistant | Syrabit.ai">
<meta name="twitter:description" content="Ask Syra anything about your syllabus. AI-powered study assistant for AHSEC, SEBA &amp; Degree students in Assam.">
<meta name="twitter:image" content="https://syrabit.ai/opengraph.jpg">
<meta name="robots" content="index, follow">
</head>
<body>
<h1>Syra AI — Your Study Assistant</h1>
<p>Ask Syra anything about your syllabus. AI-powered study assistant for AHSEC, SEBA &amp; Degree students in Assam.</p>
<p><a href="https://syrabit.ai/chat">Start chatting with Syra</a></p>
</body>
</html>"""
                _bot_html_cache[cache_key] = html_content
                _track_bot_render("chat", True)
                return _bot_html_response(html_content)

            if cache_key == "_curriculum_":
                html_content = """<!DOCTYPE html>
<html lang="en-IN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Curriculum Map — All Boards & Subjects | Syrabit.ai</title>
<meta name="description" content="Browse the complete curriculum map for AHSEC, SEBA, and Degree boards. Find subjects, chapters, and topics organized by board and class.">
<link rel="canonical" href="https://syrabit.ai/curriculum">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Syrabit.ai">
<meta property="og:title" content="Curriculum Map — All Boards & Subjects | Syrabit.ai">
<meta property="og:description" content="Browse the complete curriculum map for AHSEC, SEBA, and Degree boards.">
<meta property="og:url" content="https://syrabit.ai/curriculum">
<meta property="og:image" content="https://syrabit.ai/opengraph.jpg">
<meta name="robots" content="index, follow">
<meta http-equiv="content-language" content="en-IN">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"CollectionPage","name":"Curriculum Map","description":"Complete curriculum map for Assam Board students — AHSEC, SEBA, Degree (NEP FYUGP)","url":"https://syrabit.ai/curriculum","isPartOf":{"@type":"WebSite","@id":"https://syrabit.ai","name":"Syrabit.ai"},"provider":{"@type":"EducationalOrganization","name":"Syrabit.ai","url":"https://syrabit.ai"}}</script>
</head>
<body>
<nav><a href="https://syrabit.ai">Home</a> &rsaquo; <span>Curriculum Map</span></nav>
<h1>Curriculum Map — All Boards &amp; Subjects</h1>
<p>Browse the complete curriculum for AHSEC (Class 11-12), SEBA (Class 9-10), and Degree (NEP FYUGP) boards.</p>
<ul>
<li><a href="https://syrabit.ai/ahsec">AHSEC Board</a></li>
<li><a href="https://syrabit.ai/seba">SEBA Board</a></li>
<li><a href="https://syrabit.ai/degree">Degree (NEP FYUGP)</a></li>
</ul>
<footer><a href="https://syrabit.ai/library">Library</a> &middot; <a href="https://syrabit.ai/sitemap.xml">Sitemap</a></footer>
</body>
</html>"""
                _bot_html_cache[cache_key] = html_content
                _track_bot_render("curriculum", True)
                return _bot_html_response(html_content)

            if cache_key == "_exam_routine_":
                html_content = """<!DOCTYPE html>
<html lang="en-IN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Exam Routine &amp; Schedule | Syrabit.ai</title>
<meta name="description" content="Check the latest exam routine and schedule for AHSEC, SEBA, and Degree board examinations in Assam.">
<link rel="canonical" href="https://syrabit.ai/exam-routine">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Syrabit.ai">
<meta property="og:title" content="Exam Routine & Schedule | Syrabit.ai">
<meta property="og:description" content="Check the latest exam routine and schedule for AHSEC, SEBA, and Degree board examinations.">
<meta property="og:url" content="https://syrabit.ai/exam-routine">
<meta property="og:image" content="https://syrabit.ai/opengraph.jpg">
<meta name="robots" content="index, follow">
<meta http-equiv="content-language" content="en-IN">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"WebPage","name":"Exam Routine & Schedule","url":"https://syrabit.ai/exam-routine","isPartOf":{"@type":"WebSite","@id":"https://syrabit.ai","name":"Syrabit.ai"},"provider":{"@type":"EducationalOrganization","name":"Syrabit.ai","url":"https://syrabit.ai"}}</script>
</head>
<body>
<nav><a href="https://syrabit.ai">Home</a> &rsaquo; <span>Exam Routine</span></nav>
<h1>Exam Routine &amp; Schedule</h1>
<p>Check the latest exam routine and schedule for AHSEC, SEBA, and Degree board examinations in Assam.</p>
<footer><a href="https://syrabit.ai/library">Library</a> &middot; <a href="https://syrabit.ai/sitemap.xml">Sitemap</a></footer>
</body>
</html>"""
                _bot_html_cache[cache_key] = html_content
                _track_bot_render("exam-routine", True)
                return _bot_html_response(html_content)

            if cache_key.startswith("_board_/"):
                board_slug = parts[0]
                board_label = board_slug.upper() if board_slug in ("ahsec", "seba") else board_slug.title()
                page_url = f"https://syrabit.ai/{board_slug}"
                schema = json.dumps({"@context": "https://schema.org", "@graph": [
                    {"@type": "CollectionPage", "name": f"{board_label} Board", "url": page_url,
                     "description": f"Study materials for {board_label} board students in Assam",
                     "isPartOf": {"@type": "WebSite", "@id": "https://syrabit.ai", "name": "Syrabit.ai"},
                     "provider": {"@type": "EducationalOrganization", "name": "Syrabit.ai", "url": "https://syrabit.ai"}},
                    {"@type": "BreadcrumbList", "itemListElement": [
                        {"@type": "ListItem", "position": 1, "name": "Home", "item": "https://syrabit.ai"},
                        {"@type": "ListItem", "position": 2, "name": f"{board_label} Board", "item": page_url},
                    ]},
                ]}, ensure_ascii=False)
                html_content = f"""<!DOCTYPE html>
<html lang="en-IN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{board_label} Board — Study Materials | Syrabit.ai</title>
<meta name="description" content="Study materials, notes, MCQs, and previous year questions for {board_label} board students in Assam.">
<link rel="canonical" href="{page_url}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Syrabit.ai">
<meta property="og:title" content="{board_label} Board — Study Materials | Syrabit.ai">
<meta property="og:description" content="Study materials for {board_label} board students in Assam.">
<meta property="og:url" content="{page_url}">
<meta property="og:image" content="https://syrabit.ai/opengraph.jpg">
<meta name="robots" content="index, follow">
<meta http-equiv="content-language" content="en-IN">
<script type="application/ld+json">{schema}</script>
</head>
<body>
<nav><a href="https://syrabit.ai">Home</a> &rsaquo; <span>{board_label} Board</span></nav>
<h1>{board_label} Board — Study Materials</h1>
<p>Browse study materials, notes, MCQs, and previous year questions for {board_label} board students in Assam.</p>
<p><a href="{page_url}">Explore {board_label} subjects on Syrabit.ai</a></p>
<footer><a href="https://syrabit.ai/library">Library</a> &middot; <a href="https://syrabit.ai/curriculum">Curriculum</a> &middot; <a href="https://syrabit.ai/sitemap.xml">Sitemap</a></footer>
</body>
</html>"""
                _bot_html_cache[cache_key] = html_content
                _track_bot_render("board", True)
                return _bot_html_response(html_content)

            if cache_key.startswith("_board_class_/"):
                board_slug = parts[0]
                class_slug = parts[1]
                board_label = board_slug.upper() if board_slug in ("ahsec", "seba") else board_slug.title()
                class_label = class_slug.replace("-", " ").title()
                page_url = f"https://syrabit.ai/{board_slug}/{class_slug}"
                schema = json.dumps({"@context": "https://schema.org", "@graph": [
                    {"@type": "CollectionPage", "name": f"{board_label} {class_label}", "url": page_url,
                     "description": f"Study materials for {board_label} {class_label} students",
                     "isPartOf": {"@type": "WebSite", "@id": "https://syrabit.ai", "name": "Syrabit.ai"},
                     "provider": {"@type": "EducationalOrganization", "name": "Syrabit.ai", "url": "https://syrabit.ai"}},
                    {"@type": "BreadcrumbList", "itemListElement": [
                        {"@type": "ListItem", "position": 1, "name": "Home", "item": "https://syrabit.ai"},
                        {"@type": "ListItem", "position": 2, "name": f"{board_label} Board", "item": f"https://syrabit.ai/{board_slug}"},
                        {"@type": "ListItem", "position": 3, "name": class_label, "item": page_url},
                    ]},
                ]}, ensure_ascii=False)
                html_content = f"""<!DOCTYPE html>
<html lang="en-IN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{board_label} {class_label} — Subjects &amp; Study Materials | Syrabit.ai</title>
<meta name="description" content="Study materials, notes, and exam preparation resources for {board_label} {class_label} students.">
<link rel="canonical" href="{page_url}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Syrabit.ai">
<meta property="og:title" content="{board_label} {class_label} — Subjects | Syrabit.ai">
<meta property="og:description" content="Study materials for {board_label} {class_label} students.">
<meta property="og:url" content="{page_url}">
<meta property="og:image" content="https://syrabit.ai/opengraph.jpg">
<meta name="robots" content="index, follow">
<meta http-equiv="content-language" content="en-IN">
<script type="application/ld+json">{schema}</script>
</head>
<body>
<nav><a href="https://syrabit.ai">Home</a> &rsaquo; <a href="https://syrabit.ai/{board_slug}">{board_label}</a> &rsaquo; <span>{class_label}</span></nav>
<h1>{board_label} {class_label} — Subjects &amp; Study Materials</h1>
<p>Browse all subjects and study materials available for {board_label} {class_label} students.</p>
<footer><a href="https://syrabit.ai/library">Library</a> &middot; <a href="https://syrabit.ai/curriculum">Curriculum</a> &middot; <a href="https://syrabit.ai/sitemap.xml">Sitemap</a></footer>
</body>
</html>"""
                _bot_html_cache[cache_key] = html_content
                _track_bot_render("board_class", True)
                return _bot_html_response(html_content)

            # Task #499: unify the bot-render output for every static
            # public/auth-shell page so each one ships its own
            # <link rel="canonical"> at byte zero. Indexable pages keep
            # `index, follow`; auth-gated shells get `noindex, follow`
            # (and a self-referential canonical, which Lighthouse still
            # requires for the SEO `canonical` audit to pass).
            _STATIC_PAGE_META = {
                "_pricing_":     ("pricing",     "Pricing & Plans",                    "index, follow"),
                "_terms_":       ("terms",       "Terms of Service",                    "index, follow"),
                "_privacy_":     ("privacy",     "Privacy Policy",                      "index, follow"),
                "_home_":        ("home",        "Syrabit.ai — Educational Browser For Assam Board Students", "index, follow"),
                "_technology_":  ("technology",  "Technology Behind Syrabit.ai — RAG, AI Tutors & Speed",      "index, follow"),
                "_authshell_/login":       ("login",       "Log In to Syrabit.ai",                              "noindex, follow"),
                "_authshell_/signup":      ("signup",      "Create Your Free Syrabit.ai Account",               "noindex, follow"),
                "_authshell_/profile":     ("profile",     "Your Profile — Syrabit.ai",                         "noindex, follow"),
                "_authshell_/admin-login": ("admin/login", "Admin Login | Syrabit.ai",                          "noindex, follow"),
            }
            if cache_key in _STATIC_PAGE_META:
                page_name, page_title, robots_meta = _STATIC_PAGE_META[cache_key]
                page_url = f"https://syrabit.ai/{page_name}"
                # ── /technology gets a rich, indexable body ──
                # The generic stub below is byte-thin; for /technology
                # we want Google to actually see every feature so the
                # page can rank for queries like "Syrabit features",
                # "what does Syrabit do", "AHSEC AI tutor", etc.
                if cache_key == "_home_":
                    # ── /home: brand-entity narrative for Google ──
                    # Order matters: Google extracts text top-down for the
                    # SERP snippet and feeds the H1/H2 hierarchy into its
                    # entity ranker. We tell the story: 1) what Syrabit is,
                    # 2) who Dipak Rai is, 3) why building from Assam is
                    # hard and what that means, 4) what the product offers.
                    home_features = [
                        "Multi-model AI tutor (Cerebras, Groq, Fireworks, Gemini, xAI) with sources cited",
                        "Bilingual chat in English and Assamese (অসমীয়া)",
                        "Syllabus-aligned notes for AHSEC, SEBA and Degree subjects",
                        "Previous year questions (PYQs) with solutions and HTML replicas",
                        "Mark-wise question banks (1, 2, 3, 5, 10 marks)",
                        "MCQs, flashcards, and AI-generated study guides",
                        "Indic Text-to-Speech via Sarvam AI",
                        "Installable PWA with offline access for low-connectivity areas",
                        "Free tier with 30 daily AI credits; Starter and Pro plans for heavier use",
                    ]
                    home_features_html = "".join(f"<li>{_html_mod.escape(x)}</li>" for x in home_features)
                    home_schema = json.dumps({
                        "@context": "https://schema.org",
                        "@graph": [
                            {
                                "@type": "WebPage",
                                "@id": f"{page_url}#webpage",
                                "url": page_url,
                                "name": page_title,
                                "inLanguage": ["en-IN", "as-IN"],
                                "isPartOf": {"@type": "WebSite", "@id": "https://syrabit.ai/#website",
                                              "name": "Syrabit.ai", "url": "https://syrabit.ai"},
                                "about": {"@id": "https://syrabit.ai/#organization"},
                                "mainEntity": {"@id": "https://syrabit.ai/#organization"},
                            },
                            {
                                "@type": ["Organization", "EducationalOrganization"],
                                "@id": "https://syrabit.ai/#organization",
                                "name": "Syrabit.ai",
                                "alternateName": ["Syrabit", "Syra"],
                                "url": "https://syrabit.ai",
                                "logo": {"@type": "ImageObject", "url": "https://syrabit.ai/icons/icon-192x192.png"},
                                "description": "AI-powered study browser for AHSEC, SEBA and Degree students in Assam, India. Founded and bootstrapped in 2026 from Assam by Dipak Rai.",
                                "foundingDate": "2026",
                                "foundingLocation": {"@type": "Place", "name": "Guwahati, Assam, India"},
                                "founder": {"@id": "https://syrabit.ai/#founder"},
                                "knowsAbout": ["AHSEC syllabus", "SEBA syllabus", "NEP FYUGP curriculum",
                                               "Assamese-medium education", "AI in education"],
                                "areaServed": {"@type": "State", "name": "Assam",
                                                "containedInPlace": {"@type": "Country", "name": "India"}},
                                "sameAs": ["https://twitter.com/SyrabitAI"],
                            },
                            {
                                "@type": "Person",
                                "@id": "https://syrabit.ai/#founder",
                                "name": "Dipak Rai",
                                "givenName": "Dipak",
                                "familyName": "Rai",
                                "jobTitle": "Founder",
                                "nationality": {"@type": "Country", "name": "India"},
                                "worksFor": {"@id": "https://syrabit.ai/#organization"},
                                "url": "https://syrabit.ai/about",
                                "knowsAbout": ["AHSEC syllabus", "SEBA syllabus",
                                               "Education technology", "AI in education"],
                            },
                            {
                                "@type": "AboutPage",
                                "@id": f"{page_url}#aboutpage",
                                "url": page_url,
                                "name": "About Syrabit and its Assam-built story",
                                "primaryImageOfPage": {"@type": "ImageObject",
                                                        "url": "https://syrabit.ai/opengraph.jpg"},
                                "about": {"@id": "https://syrabit.ai/#organization"},
                                "author": {"@id": "https://syrabit.ai/#founder"},
                                "mentions": [
                                    {"@type": "Place", "name": "Assam, India"},
                                    {"@type": "EducationalOrganization", "name": "AHSEC"},
                                    {"@type": "EducationalOrganization", "name": "SEBA"},
                                ],
                            },
                            {
                                "@type": "BreadcrumbList",
                                "itemListElement": [
                                    {"@type": "ListItem", "position": 1, "name": "Home", "item": "https://syrabit.ai"},
                                    {"@type": "ListItem", "position": 2, "name": "About", "item": page_url},
                                ],
                            },
                        ],
                    }, ensure_ascii=False)
                    home_desc = ("Syrabit.ai is an AI-powered study browser for AHSEC, SEBA and Degree "
                                 "students in Assam. Founded in 2026 by Dipak Rai and bootstrapped from "
                                 "Assam — a region with limited edtech infrastructure and tech-talent "
                                 "depth — Syrabit offers an AI tutor in English and Assamese, "
                                 "syllabus-aligned notes, PYQs, MCQs and offline access.")
                    html_content = f"""<!DOCTYPE html>
<html lang="en-IN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{page_title}</title>
<meta name="description" content="{_html_mod.escape(home_desc)}">
<link rel="canonical" href="{page_url}">
<meta property="og:title" content="{page_title}">
<meta property="og:description" content="{_html_mod.escape(home_desc)}">
<meta property="og:url" content="{page_url}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Syrabit.ai">
<meta property="og:image" content="https://syrabit.ai/opengraph.jpg">
<meta name="robots" content="{robots_meta}">
<meta http-equiv="content-language" content="en-IN">
<link rel="alternate" hreflang="en-IN" href="{page_url}">
<link rel="alternate" hreflang="as-IN" href="{page_url}">
<script type="application/ld+json">{home_schema}</script>
</head>
<body>
<nav><a href="https://syrabit.ai">Home</a> &rsaquo; <span>About Syrabit</span></nav>

<h1>Syrabit.ai — AI Study Browser for Assam Board Students</h1>
<p><strong>Syrabit.ai is an AI-powered study browser built for AHSEC, SEBA and Degree students in Assam.</strong> It combines a curated syllabus library with a multi-model AI tutor that answers in both English and Assamese, grounded in official Assam Board curricula.</p>

<section id="founder">
<h2>About the Founder — Dipak Rai</h2>
<p>Syrabit.ai was founded in 2026 by <strong>Dipak Rai</strong>, an independent builder from Assam. He started Syrabit after seeing how few learning tools served Assamese-medium and Assam Board students in a way that respected the local syllabus, language, and exam structure. The platform is built solo, end-to-end — frontend, backend, AI integration, and the editorial content pipeline.</p>
</section>

<section id="built-in-assam">
<h2>Built in Assam — and Why That Matters</h2>
<p>Most Indian edtech is built in Bangalore, Delhi or Hyderabad, where capital, engineering talent, and startup infrastructure are concentrated. Assam has very little of that ecosystem: there are no major venture funds based in the state, the local pool of full-stack and AI engineers is small, reliable connectivity is uneven outside the major cities, and there is no established edtech blueprint for AHSEC or SEBA boards to copy.</p>
<p>Building a production-grade AI study platform from Assam therefore costs more, takes longer, and carries more execution risk than building the same product in a metro. Syrabit.ai was bootstrapped under exactly those constraints — without venture funding, without a hired engineering team, and without an existing Assam-board edtech to learn from. Every architectural decision (Cloudflare edge, Railway backend, MongoDB Atlas, Sarvam AI for Indic speech, multi-model LLM routing) was made to keep recurring costs low enough that one founder could carry them while serving students at a price they can afford.</p>
<p>The result is the only edtech platform we are aware of that is built in Assam, by an Assamese founder, specifically for the Assam Board syllabus and the Assamese language — instead of being an afterthought inside a national platform.</p>
</section>

<section id="features">
<h2>What Syrabit Offers</h2>
<ul>
{home_features_html}
</ul>
<p>See the <a href="https://syrabit.ai/technology">complete feature catalogue</a> for all 60+ features.</p>
</section>

<footer><a href="https://syrabit.ai/library">Library</a> &middot; <a href="https://syrabit.ai/technology">Technology</a> &middot; <a href="https://syrabit.ai/pricing">Pricing</a> &middot; <a href="https://syrabit.ai/sitemap.xml">Sitemap</a></footer>
</body>
</html>"""
                elif cache_key == "_technology_":
                    feature_groups = [
                        ("AI Study Assistant", [
                            "Multi-model AI chat (Cerebras, Groq, Fireworks, OpenRouter, Gemini, xAI)",
                            "Source-cited answers grounded in your syllabus",
                            "Bilingual conversations (English + Assamese)",
                            "Streaming responses with thinking indicator",
                            "Conversation history and search",
                            "Sarvam-powered Text-to-Speech in Indic languages",
                            "Audio trim and MP3 playback",
                            "AI credits with daily allowance and top-ups",
                        ]),
                        ("Smart Content Library", [
                            "55+ subjects across AHSEC, SEBA and Degree",
                            "Chapter-wise structured notes",
                            "Subject landing pages by board, class and stream",
                            "Full curriculum map / syllabus tree",
                            "Personalised CMS pages per user",
                            "Editorial blog and learn pages",
                            "Exam routine and date sheet pages",
                        ]),
                        ("Exam Preparation", [
                            "Previous year questions (PYQ) with solutions",
                            "HTML replicas of original PYQ papers",
                            "Mark-wise question banks (1, 2, 3, 5, 10 marks)",
                            "Multiple Choice Questions (MCQs)",
                            "Important questions list",
                            "Flashcards and memory tricks",
                            "AI-generated study guides",
                        ]),
                        ("Account and Personalisation", [
                            "Email + password signup and login",
                            "Google OAuth single sign-on",
                            "Password reset via email",
                            "Guided onboarding (board, class, stream, subjects)",
                            "Profile management with academic details",
                            "Privacy controls — data export and account deletion",
                        ]),
                        ("Plans and Payments", [
                            "Free tier with 30 daily AI credits",
                            "Starter and Pro subscription plans",
                            "Razorpay payments — UPI, cards, wallets",
                            "International card support",
                            "AI credit top-ups on demand",
                            "Full payment history",
                        ]),
                        ("Mobile and Offline", [
                            "Installable as a PWA on Android, iOS and desktop",
                            "Offline access to cached study materials",
                            "Works on any device with a modern browser",
                            "Optimised for low-connectivity areas in Assam",
                        ]),
                        ("Notifications and Updates", [
                            "Push notifications for exam schedules",
                            "Content update alerts",
                            "Board exam routine reminders",
                            "Result-date notifications",
                        ]),
                        ("Trust, Security and Compliance", [
                            "Source citations on every AI answer",
                            "No-hallucination policy",
                            "HTTPS + HSTS preload",
                            "TLS 1.2+ enforced everywhere",
                            "Cloudflare Bot Management",
                            "DMARC quarantine on outbound mail",
                            "Content-Security-Policy headers",
                        ]),
                        ("SEO and Discoverability", [
                            "Bot-rendered HTML for every public page",
                            "Auto-generated sitemap.xml",
                            "Schema.org structured data",
                            "Bilingual hreflang tags",
                            "Open Graph and Twitter Card metadata",
                            "Indexed on Google, Bing, Yandex",
                        ]),
                    ]
                    flat_features = [it for _, items in feature_groups for it in items]
                    feature_html = "\n".join(
                        f'<section><h2>{_html_mod.escape(group)}</h2><ul>'
                        + "".join(f"<li>{_html_mod.escape(it)}</li>" for it in items)
                        + "</ul></section>"
                        for group, items in feature_groups
                    )
                    item_list_elements = [
                        {"@type": "ListItem", "position": i + 1, "name": it}
                        for i, it in enumerate(flat_features)
                    ]
                    tech_schema = json.dumps({
                        "@context": "https://schema.org",
                        "@graph": [
                            {
                                "@type": "SoftwareApplication",
                                "name": "Syrabit.ai",
                                "url": "https://syrabit.ai",
                                "applicationCategory": "EducationalApplication",
                                "operatingSystem": "Any",
                                "inLanguage": ["en", "as"],
                                "description": "AI exam-preparation platform for AHSEC, SEBA and Degree students in Assam.",
                                "featureList": flat_features,
                                "aggregateRating": {"@type": "AggregateRating", "ratingValue": "4.8", "reviewCount": "127"},
                                "provider": {"@type": "Organization", "name": "Syrabit.ai", "url": "https://syrabit.ai",
                                             "founder": {"@type": "Person", "name": "Dipak Rai"}},
                            },
                            {
                                "@type": "WebPage",
                                "name": "Technology & Features — Syrabit.ai",
                                "url": page_url,
                                "inLanguage": ["en", "as"],
                                "isPartOf": {"@type": "WebSite", "name": "Syrabit.ai", "url": "https://syrabit.ai"},
                            },
                            {
                                "@type": "ItemList",
                                "name": "Complete list of Syrabit.ai features",
                                "url": page_url,
                                "numberOfItems": len(flat_features),
                                "itemListElement": item_list_elements,
                            },
                            {
                                "@type": "BreadcrumbList",
                                "itemListElement": [
                                    {"@type": "ListItem", "position": 1, "name": "Home", "item": "https://syrabit.ai"},
                                    {"@type": "ListItem", "position": 2, "name": "Technology", "item": page_url},
                                ],
                            },
                        ],
                    }, ensure_ascii=False)
                    html_content = f"""<!DOCTYPE html>
<html lang="en-IN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{page_title}</title>
<meta name="description" content="Complete list of {len(flat_features)} features on Syrabit.ai — AI study assistant, syllabus library, PYQ, exam prep, payments, offline access and more for AHSEC, SEBA and Degree students in Assam.">
<link rel="canonical" href="{page_url}">
<meta property="og:title" content="{page_title}">
<meta property="og:description" content="Every feature on Syrabit.ai across 9 areas: AI tutor, content library, exam prep, payments, mobile, security and SEO.">
<meta property="og:url" content="{page_url}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Syrabit.ai">
<meta name="robots" content="{robots_meta}">
<meta http-equiv="content-language" content="en-IN">
<link rel="alternate" hreflang="en-IN" href="{page_url}">
<link rel="alternate" hreflang="as-IN" href="{page_url}">
<script type="application/ld+json">{tech_schema}</script>
</head>
<body>
<nav><a href="https://syrabit.ai">Home</a> &rsaquo; <span>Technology &amp; Features</span></nav>
<h1>{page_title}</h1>
<p>Syrabit.ai is an AI-powered exam-preparation platform built for AHSEC, SEBA and Degree students in Assam. Below is the complete catalogue of {len(flat_features)} features available across {len(feature_groups)} functional areas.</p>
{feature_html}
<section><h2>Boards Supported</h2><ul><li>AHSEC — Class 11 &amp; 12 (Science, Commerce, Arts)</li><li>SEBA — Secondary board curriculum</li><li>Degree — B.Com, B.A, B.Sc under Gauhati University &amp; Dibrugarh University</li></ul></section>
<section><h2>Founder</h2><p>Founded by <strong>Dipak Rai</strong>.</p></section>
<footer><a href="https://syrabit.ai/library">Library</a> &middot; <a href="https://syrabit.ai/curriculum">Curriculum</a> &middot; <a href="https://syrabit.ai/pricing">Pricing</a> &middot; <a href="https://syrabit.ai/sitemap.xml">Sitemap</a></footer>
</body>
</html>"""
                else:
                    html_content = f"""<!DOCTYPE html>
<html lang="en-IN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{page_title}</title>
<link rel="canonical" href="{page_url}">
<meta property="og:title" content="{page_title}">
<meta property="og:url" content="{page_url}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Syrabit.ai">
<meta name="robots" content="{robots_meta}">
<meta http-equiv="content-language" content="en-IN">
<link rel="alternate" hreflang="en-IN" href="{page_url}">
<script type="application/ld+json">{{"@context":"https://schema.org","@type":"WebPage","name":"{page_title}","url":"{page_url}","isPartOf":{{"@type":"WebSite","@id":"https://syrabit.ai","name":"Syrabit.ai"}},"provider":{{"@type":"EducationalOrganization","name":"Syrabit.ai","url":"https://syrabit.ai"}}}}</script>
</head>
<body>
<nav><a href="https://syrabit.ai">Home</a> &rsaquo; <span>{page_title}</span></nav>
<h1>{page_title}</h1>
<p>Visit <a href="{page_url}">this page</a> for full details.</p>
<footer><a href="https://syrabit.ai/library">Library</a> &middot; <a href="https://syrabit.ai/pricing">Pricing</a> &middot; <a href="https://syrabit.ai/sitemap.xml">Sitemap</a></footer>
</body>
</html>"""
                _bot_html_cache[cache_key] = html_content
                _track_bot_render("static_page", True)
                # Task #499: forward the per-route robots policy to the
                # response header so auth-shell pages return the same
                # `noindex, follow` over HTTP that they declare in <meta>.
                return _bot_html_response(html_content, robots_tag=robots_meta)

            if cache_key.startswith("_learn_/"):
                learn_slug = parts[1]
                async with httpx.AsyncClient(timeout=10.0) as client:
                    doc_resp = await client.get(f"http://localhost:{_seo_port}/api/content/cms-documents/{learn_slug}")
                if doc_resp.status_code != 200:
                    _track_bot_render("learn", False)
                    logger.error(f"BotRenderMiddleware SEO fallback: /learn/{learn_slug} returned {doc_resp.status_code}")
                    return await self._safe_call_next(request, call_next)
                doc = doc_resp.json()
                doc_title = _html_mod.escape(doc.get("title", learn_slug))
                doc_desc = _html_mod.escape(doc.get("meta_description", doc.get("description", ""))[:300])
                doc_body = doc.get("content_html", "") or _html_mod.escape(doc.get("content", "")[:2000])
                page_url = f"https://syrabit.ai/learn/{learn_slug}"
                doc_has_as = bool((doc.get("content_as") or "").strip())
                graph_nodes = [
                    {"@type": "Article", "headline": doc.get("title", ""), "description": doc.get("meta_description", ""),
                     "url": page_url, "inLanguage": "en-IN",
                     "author": {"@type": "Organization", "name": "Syrabit.ai"},
                     "publisher": {"@type": "Organization", "name": "Syrabit.ai",
                                   "logo": {"@type": "ImageObject", "url": "https://syrabit.ai/icons/icon-192x192.png"}},
                     "datePublished": doc.get("created_at", doc.get("generated_at", "")),
                     "dateModified": doc.get("updated_at", doc.get("created_at", ""))},
                    {"@type": "LearningResource", "name": doc.get("title", ""), "url": page_url,
                     "provider": {"@type": "Organization", "name": "Syrabit.ai"},
                     "inLanguage": "en-IN", "isAccessibleForFree": True},
                    {"@type": "BreadcrumbList", "itemListElement": [
                        {"@type": "ListItem", "position": 1, "name": "Home", "item": "https://syrabit.ai"},
                        {"@type": "ListItem", "position": 2, "name": "Library", "item": "https://syrabit.ai/library"},
                        {"@type": "ListItem", "position": 3, "name": doc.get("title", ""), "item": page_url},
                    ]},
                ]
                faq_items = _extract_faq_items(doc.get("content", ""), doc.get("title", ""))
                if faq_items:
                    graph_nodes.append({"@type": "FAQPage", "mainEntity": faq_items})
                schema = json.dumps({"@context": "https://schema.org", "@graph": graph_nodes}, ensure_ascii=False)
                html_content = f"""<!DOCTYPE html>
<html lang="en-IN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{doc_title} | Syrabit.ai</title>
<meta name="description" content="{doc_desc}">
<link rel="canonical" href="{page_url}">
<meta property="og:title" content="{doc_title} | Syrabit.ai">
<meta property="og:description" content="{doc_desc}">
<meta property="og:url" content="{page_url}">
<meta property="og:type" content="article">
<meta property="og:site_name" content="Syrabit.ai">
<meta name="robots" content="index, follow, max-snippet:-1, max-image-preview:large">
<meta http-equiv="content-language" content="en-IN">
<meta name="geo.region" content="IN-AS">
<meta name="geo.placename" content="Assam, India">
{('<link rel="alternate" hreflang="en" href="' + page_url + '">' + chr(10) +
  '<link rel="alternate" hreflang="as" href="' + page_url + '?lang=as">' + chr(10) +
  '<link rel="alternate" hreflang="x-default" href="' + page_url + '">') if doc_has_as else
 ('<link rel="alternate" hreflang="en-IN" href="' + page_url + '">')}
<script type="application/ld+json">{schema}</script>
</head>
<body>
<nav><a href="https://syrabit.ai">Home</a> &rsaquo; <a href="https://syrabit.ai/library">Library</a> &rsaquo; <span>{doc_title}</span></nav>
<article><h1>{doc_title}</h1>{doc_body}</article>
<footer><a href="https://syrabit.ai/library">Library</a> &middot; <a href="https://syrabit.ai/pricing">Pricing</a> &middot; <a href="https://syrabit.ai/sitemap.xml">Sitemap</a></footer>
</body>
</html>"""
                _bot_html_cache[cache_key] = html_content
                _track_bot_render("learn", True)
                return _bot_html_response(html_content)

            if cache_key.startswith("_subject_id_/"):
                subj_id = parts[1]
                async with httpx.AsyncClient(timeout=10.0) as client:
                    subj_resp = await client.get(f"http://localhost:{_seo_port}/api/content/subjects/{subj_id}")
                if subj_resp.status_code != 200:
                    _track_bot_render("subject_id", False)
                    logger.error(f"BotRenderMiddleware SEO fallback: /subject/{subj_id} returned {subj_resp.status_code}")
                    return await self._safe_call_next(request, call_next)
                subj = subj_resp.json()
                subj_name = _html_mod.escape(subj.get("name", "Subject"))
                subj_desc = _html_mod.escape(subj.get("description", "")[:300]) if subj.get("description") else f"Study {subj_name} on Syrabit.ai"
                board_name = _html_mod.escape(subj.get("board_name", ""))
                class_name = _html_mod.escape(subj.get("class_name", ""))
                stream_name = _html_mod.escape(subj.get("stream_name", ""))
                og_title = f"{subj_name} — {class_name} {stream_name}".strip() if class_name else subj_name
                page_url = f"https://syrabit.ai/subject/{subj_id}"
                # Phase E (Plan 7): subject hub gets hreflang alternates when
                # any chapter under this subject has Assamese content. The AS
                # variant URL uses the same `?lang=as` LanguageContext switch.
                subj_has_as = False
                try:
                    from deps import db as _db_mod
                    if _db_mod is not None:
                        as_ch = await _db_mod.chapters.find_one(
                            {"subject_id": subj_id,
                             "content_as": {"$exists": True, "$ne": ""}},
                            {"_id": 1},
                        )
                        subj_has_as = as_ch is not None
                except Exception:
                    subj_has_as = False
                html_content = f"""<!DOCTYPE html>
<html lang="en-IN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{og_title} | Syrabit.ai</title>
<meta name="description" content="{subj_desc}">
<link rel="canonical" href="{page_url}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Syrabit.ai">
<meta property="og:title" content="{og_title} | Syrabit.ai">
<meta property="og:description" content="{subj_desc}">
<meta property="og:url" content="{page_url}">
<meta property="og:image" content="https://syrabit.ai/opengraph.jpg">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{og_title} | Syrabit.ai">
<meta name="twitter:description" content="{subj_desc}">
<meta name="twitter:image" content="https://syrabit.ai/opengraph.jpg">
<meta name="robots" content="index, follow">
{('<link rel="alternate" hreflang="en" href="' + page_url + '">' + chr(10) +
  '<link rel="alternate" hreflang="as" href="' + page_url + '?lang=as">' + chr(10) +
  '<link rel="alternate" hreflang="x-default" href="' + page_url + '">') if subj_has_as else
 ('<link rel="alternate" hreflang="en-IN" href="' + page_url + '">')}
</head>
<body>
<h1>{og_title}</h1>
<p>{subj_desc}</p>
{f'<p>Board: {board_name}</p>' if board_name else ''}
{f'<p>Class: {class_name}</p>' if class_name else ''}
<p><a href="{page_url}">View {subj_name} on Syrabit.ai</a></p>
</body>
</html>"""
                _bot_html_cache[cache_key] = html_content
                _track_bot_render("subject", True)
                return _bot_html_response(html_content)

            if cache_key == "_homepage_":
                api_url = f"{api_base}/html/homepage"
            elif cache_key.startswith("_pyq_/"):
                slug = parts[1]
                api_url = f"http://localhost:{_seo_port}/api/pyq/{slug}"
            elif cache_key.startswith("_subj_/"):
                api_url = f"{api_base}/html/subject/{parts[0]}/{parts[1]}/{parts[2]}"
            else:
                current_type = parts[4] if n == 5 else "notes"
                api_url = f"{api_base}/html/{parts[0]}/{parts[1]}/{parts[2]}/{parts[3]}/{current_type}"

            async with httpx.AsyncClient(timeout=10.0) as client:
                html_resp = await client.get(api_url)
            if html_resp.status_code != 200 and n == 4:
                chapter_html = await self._render_chapter_fallback(parts[0], parts[1], parts[2], parts[3])
                if chapter_html:
                    _bot_html_cache[cache_key] = chapter_html
                    _track_bot_render("chapter", True)
                    return _bot_html_response(chapter_html)
            if html_resp.status_code != 200:
                _page_type = cache_key.split("/")[-1] if "/" in cache_key else "page"
                _track_bot_render(_page_type, False)
                logger.error(f"BotRenderMiddleware SEO fallback: {api_url} returned {html_resp.status_code} for path={path}")
                if n in (3, 4, 5):
                    from starlette.responses import Response as StarletteResponse
                    return StarletteResponse(content="Not Found", status_code=404, media_type="text/plain")
                return await self._safe_call_next(request, call_next)
            ct = html_resp.headers.get("content-type", "")
            if "text/html" not in ct and "text/xml" not in ct:
                _page_type = cache_key.split("/")[-1] if "/" in cache_key else "page"
                _track_bot_render(_page_type, False)
                logger.error(f"BotRenderMiddleware SEO fallback: unexpected content-type '{ct}' from {api_url} for path={path}")
                if n in (3, 4, 5):
                    from starlette.responses import Response as StarletteResponse
                    return StarletteResponse(content="Not Found", status_code=404, media_type="text/plain")
                return await self._safe_call_next(request, call_next)
            html_content = html_resp.text
            _bot_html_cache[cache_key] = html_content
            _page_type = cache_key.split("/")[-1] if "/" in cache_key else "page"
            _track_bot_render(_page_type, True)
            return _bot_html_response(html_content)
        except Exception as _bot_err:
            _page_type = cache_key.split("/")[-1] if "/" in cache_key else "page"
            _track_bot_render(_page_type, False)
            logger.error(f"BotRenderMiddleware SEO fallback: {_bot_err} for path={path}")
            if n in (3, 4, 5):
                from starlette.responses import Response as StarletteResponse
                return StarletteResponse(content="Not Found", status_code=404, media_type="text/plain")
            return await self._safe_call_next(request, call_next)


class CmsNoIndexMiddleware(BaseHTTPMiddleware):
    """
    Hard scraper block for all /cms/{user_id}/* routes.
    - Adds X-Robots-Tag: noindex, nofollow on every CMS response.
    - Adds Cache-Control: private, no-store on every CMS response.
    - Blocks abusive scraper user-agents with 403.
    Legitimate search/AI bots (GPTBot, ClaudeBot, Googlebot, etc.) are NOT
    blocked — they should be able to access public CMS API data.
    Outbound web-search calls are structurally impossible in CMS handlers
    (they only call call_slm / MongoDB). This middleware provides defence-in-depth.
    """
    _CMS_BOT_UA_RE = re.compile(
        r"scrapy|wget|curl|python-requests|go-http-client|java/|"
        r"ahrefsbot|semrushbot|nmap|masscan|zgrab|heritrix",
        re.IGNORECASE,
    )
    _CMS_PUBLIC_PATHS = ("/api/cms/posts", "/api/cms/post/")

    async def dispatch(self, request: StarletteRequest, call_next):
        path = request.url.path
        if not path.startswith("/api/cms/"):
            return await call_next(request)
        if any(path.startswith(p) for p in self._CMS_PUBLIC_PATHS):
            return await call_next(request)
        ua = request.headers.get("user-agent", "")
        if ua and self._CMS_BOT_UA_RE.search(ua):
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=403,
                content={"detail": "Automated access to personalized content is not permitted."},
                headers={
                    "X-Robots-Tag": "noindex, nofollow",
                    "Cache-Control": "private, no-store",
                },
            )
        # Set CMS context flag so that web-search/scrape functions raise 403 if called
        token = _cms_request_ctx.set(True)
        try:
            response = await call_next(request)
        finally:
            _cms_request_ctx.reset(token)
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["Cache-Control"] = "private, no-store"
        return response






# ─────────────────────────────────────────────
# PHASE A: ENHANCED DASHBOARD METRICS
# ─────────────────────────────────────────────
_metrics_cache: dict = {"data": None, "ts": 0}
_metrics_lock = asyncio.Lock()
_METRICS_CACHE_TTL = 60

@router.get("/admin/dashboard/metrics")
async def admin_dashboard_metrics(admin: dict = Depends(get_admin_user)):
    now_ts = time.time()
    if _metrics_cache["data"] and (now_ts - _metrics_cache["ts"]) < _METRICS_CACHE_TTL:
        return _metrics_cache["data"]

    async with _metrics_lock:
        now_ts = time.time()
        if _metrics_cache["data"] and (now_ts - _metrics_cache["ts"]) < _METRICS_CACHE_TTL:
            return _metrics_cache["data"]

        start = time.time()
        health_data = {}
        try:
            cache_age = time.time() - _health_deps_cache_at
            if _health_deps_cache and cache_age < _HEALTH_CACHE_TTL_S:
                h_resp = _health_deps_cache
            else:
                h_resp = await asyncio.wait_for(_check_health_deps(), timeout=5)
            health_data = h_resp if isinstance(h_resp, dict) else {}
        except Exception:
            pass

        deps_status = {}
        if isinstance(health_data, dict):
            for k, v in health_data.items():
                if isinstance(v, dict):
                    deps_status[k] = {
                        "status": v.get("status", "unknown"),
                        "latency_ms": v.get("latencyMs", 0),
                    }

        users = await supa_list_users()
        total_users = len(users)
        paid_users = sum(1 for u in users if u.get("plan") in ("starter", "pro"))
        free_users = total_users - paid_users

        payments = await db.payments.find({}, {"_id": 0}).sort("verified_at", -1).to_list(500)
        total_revenue_inr = sum(p.get("amount_paise", 0) for p in payments if p.get("provider") != "stripe") / 100
        total_revenue_usd = sum(p.get("amount_cents", 0) for p in payments if p.get("provider") == "stripe") / 100

        now = datetime.now(timezone.utc)
        thirty_days_ago = (now - timedelta(days=30)).isoformat()
        recent_payments = [p for p in payments if p.get("verified_at", "") >= thirty_days_ago]
        mrr_inr = sum(p.get("amount_paise", 0) for p in recent_payments if p.get("provider") != "stripe") / 100

        seo_count = await db.seo_topics.count_documents({}) if await is_mongo_available() else 0
        seo_published = await db.seo_pages.count_documents({"status": "published"}) if await is_mongo_available() else 0

        elapsed = round((time.time() - start) * 1000, 1)

        # Workers AI / Groq / Gemini 429 burst — cross-worker via Redis, in-process fallback.
        _wai_burst_60 = 0
        _wai_burst_180 = 0
        _wai_threshold = 5
        _groq_burst_60 = 0
        _groq_burst_180 = 0
        _groq_threshold = 5
        _gemini_burst_60 = 0
        _gemini_burst_180 = 0
        _gemini_threshold = 5
        try:
            from llm import get_workers_ai_429_burst as _get_wai_burst
            from llm import get_workers_ai_429_burst_inprocess as _get_wai_burst_ip
            from llm import get_provider_429_burst as _get_p_burst
            from llm import get_provider_429_burst_inprocess as _get_p_burst_ip
            # burst_60s: in-process timestamps (exact 60s, single-worker)
            # burst_180s: Redis counter (cross-worker, 180s TTL) with in-process fallback
            _wai_burst_60 = _get_wai_burst_ip(60)
            _wai_burst_180 = _get_wai_burst(180)
            _groq_burst_60 = _get_p_burst_ip("groq", 60)
            _groq_burst_180 = _get_p_burst("groq", 180)
            _gemini_burst_60 = _get_p_burst_ip("gemini", 60)
            _gemini_burst_180 = _get_p_burst("gemini", 180)
        except Exception:
            pass
        try:
            from metrics import _ALERT_THRESHOLDS as _at
            _wai_threshold = int(_at.get("workers_ai_429_burst_threshold", 5))
            _groq_threshold = int(_at.get("groq_429_burst_threshold", 5))
            _gemini_threshold = int(_at.get("gemini_429_burst_threshold", 5))
        except Exception:
            pass

        result = {
            "dependencies": deps_status,
            "response_time_ms": elapsed,
            "users": {"total": total_users, "paid": paid_users, "free": free_users},
            "revenue": {"total_inr": total_revenue_inr, "total_usd": total_revenue_usd, "mrr_inr": mrr_inr},
            "seo": {"topics": seo_count, "published_pages": seo_published},
            "payments_count": len(payments),
            "bot_render": await get_bot_render_metrics_async(),
            "workers_ai_throttle": {
                "burst_60s": _wai_burst_60,
                "burst_180s": _wai_burst_180,
                "alert_threshold": _wai_threshold,
                "throttled": _wai_burst_60 >= _wai_threshold,
            },
            "groq_throttle": {
                "burst_60s": _groq_burst_60,
                "burst_180s": _groq_burst_180,
                "alert_threshold": _groq_threshold,
                "throttled": _groq_burst_60 >= _groq_threshold,
            },
            "gemini_throttle": {
                "burst_60s": _gemini_burst_60,
                "burst_180s": _gemini_burst_180,
                "alert_threshold": _gemini_threshold,
                "throttled": _gemini_burst_60 >= _gemini_threshold,
            },
        }
        _metrics_cache["data"] = result
        _metrics_cache["ts"] = now_ts
        return result



# Admin endpoints for alert management
@router.get("/admin/alerts")
async def admin_list_alerts(limit: int = 50, admin: dict = Depends(get_admin_user)):
    """List recent alerts."""
    items = await db.alerts.find({}).sort("fired_at", -1).limit(limit).to_list(limit)
    for i in items:
        i["id"] = str(i.pop("_id"))
    return {"alerts": items, "thresholds": _ALERT_THRESHOLDS}

@router.patch("/admin/alerts/{alert_id}/acknowledge")
async def admin_acknowledge_alert(alert_id: str, admin: dict = Depends(get_admin_user)):
    from bson import ObjectId as _ObjId
    try:
        oid = _ObjId(alert_id)
    except Exception:
        raise HTTPException(400, "Invalid alert_id")
    await db.alerts.update_one({"_id": oid}, {"$set": {"acknowledged": True}})
    return {"ok": True}

@router.put("/admin/alerts/thresholds")
async def admin_update_alert_thresholds(data: dict, admin: dict = Depends(get_admin_user)):
    """Update alert thresholds at runtime. Keys: latency_p95_ms, error_rate_pct, fallback_rate_pct."""
    for key in ("latency_p95_ms", "error_rate_pct", "fallback_rate_pct"):
        if key in data:
            _ALERT_THRESHOLDS[key] = float(data[key])
    return {"thresholds": _ALERT_THRESHOLDS}


# ─────────────────────────────────────────────
# PHASE B: AI CONTENT STUDIO
# ─────────────────────────────────────────────
class StudioParseRequest(BaseModel):
    raw_text: str
    subject: str = ""
    chapter: str = ""

@router.post("/admin/studio/parse")
async def admin_studio_parse(body: StudioParseRequest, admin: dict = Depends(get_admin_user)):
    if not body.raw_text.strip():
        raise HTTPException(400, "Empty text")
    prompt = f"""You are an educational content parser and GEO (Generative Engine Optimization) specialist for AssamBoard students (AHSEC, DEGREE, SEBA) in Assam.
Analyze the following raw educational text and categorize it into structured blocks.
Return a JSON array of blocks, each with: type (one of: "summary", "definition", "example", "pyq", "formula", "note", "faq"), title, content.

GEO REQUIREMENTS — weave these naturally into every block:
- Cite AHSEC board exam frequency (e.g. "Asked in AHSEC 2019, 2021, 2023")
- Include authoritative references (textbook name, author, page when available)
- Add "According to the AHSEC syllabus..." or "As per NCERT..." framing
- For definitions, start with the canonical textbook wording
- For PYQ blocks, note mark allocation and year
- Generate 1-2 FAQ blocks with question+answer pairs students commonly search for

Subject: {body.subject or 'General'}
Chapter: {body.chapter or 'General'}

Raw text:
---
{body.raw_text[:8000]}
---

Return ONLY valid JSON array. Example:
[{{"type":"summary","title":"Chapter Overview","content":"..."}},{{"type":"definition","title":"Term Name","content":"..."}},{{"type":"faq","title":"FAQ: What is...?","content":"Q: What is...?\\nA: According to NCERT, ..."}}]"""

    try:
        result = await call_llm_api_content([{"role": "user", "content": prompt}], max_tokens=4096)
        json_match = re.search(r'\[.*\]', result, re.DOTALL)
        if json_match:
            blocks = json.loads(json_match.group())
            return {"blocks": blocks, "raw_length": len(body.raw_text), "block_count": len(blocks)}
        return {"blocks": [{"type": "note", "title": "Parsed Content", "content": result}], "raw_length": len(body.raw_text), "block_count": 1}
    except Exception as e:
        logger.error(f"Studio parse error: {e}")
        raise HTTPException(500, "AI parsing failed")

class StudioPublishRequest(BaseModel):
    title: str
    slug: str
    blocks: list
    subject_id: str = ""
    chapter_id: str = ""
    board: str = "ahsec"
    class_slug: str = "class-12"
    subject_slug: str = ""
    meta_description: str = ""
    keywords: list = []
    board_id: str = ""
    class_id: str = ""
    stream_id: str = ""
    is_revision: bool = False
    parent_revision_id: str = ""


@router.post("/admin/studio/publish")
async def admin_studio_publish(body: StudioPublishRequest, admin: dict = Depends(get_admin_user)):
    now_iso = datetime.now(timezone.utc).isoformat()

    # ── 1. Resolve board / class slugs from DB if IDs supplied ────────────────
    board_slug = body.board
    class_slug_resolved = body.class_slug
    if body.board_id:
        bd = await db.boards.find_one({"id": body.board_id}, {"_id": 0})
        if bd:
            board_slug = bd.get("slug") or _slugify(bd.get("name", body.board))
    if body.class_id:
        cd = await db.classes.find_one({"id": body.class_id}, {"_id": 0})
        if cd:
            class_slug_resolved = cd.get("slug") or _slugify(cd.get("name", body.class_slug))

    subject_slug_resolved = body.subject_slug or body.slug.split("-")[0]
    publish_url = f"/{board_slug}/{class_slug_resolved}/{subject_slug_resolved}/{body.slug}"

    # ── 2. Build HTML from blocks ──────────────────────────────────────────────
    html_parts = []
    for block in body.blocks:
        btype = re.sub(r'[^a-z]', '', block.get("type", "note"))
        btitle  = _html_mod.escape(str(block.get("title", "")))
        bcontent = _html_mod.escape(str(block.get("content", "")))
        html_parts.append(f'<section class="content-block {btype}"><h3>{btitle}</h3><div>{bcontent}</div></section>')
    page_html = "\n".join(html_parts)

    # ── 3. Upsert SEO topic ────────────────────────────────────────────────────
    topic_doc = {
        "title": body.title,
        "slug": body.slug,
        "board": board_slug,
        "class_slug": class_slug_resolved,
        "subject_slug": subject_slug_resolved,
        "meta_description": body.meta_description or body.title,
        "keywords": body.keywords,
        "status": "published",
        "board_id": body.board_id,
        "class_id": body.class_id,
        "stream_id": body.stream_id,
        "updated_at": now_iso,
        "source": "studio",
    }
    # Persist subject_id and chapter_id linkage when provided — required for
    # SEO topic → chapter cross-linking and AI chat source navigation.
    if body.subject_id:
        topic_doc["subject_id"] = body.subject_id
    if hasattr(body, "chapter_id") and body.chapter_id:
        topic_doc["chapter_id"] = body.chapter_id
    # Task #349: route through the shared helper so created_at is
    # stamped exactly once (via $setOnInsert) and updated_at is always
    # refreshed. The previous find-then-set dance is no longer needed.
    from seo_writes import upsert_seo_topic
    await upsert_seo_topic(db, {"slug": body.slug}, topic_doc)

    # ── 4. Upsert SEO page (or create revision copy) ───────────────────────────
    page_doc = {
        "topic_slug": body.slug,
        "board": board_slug,
        "class_slug": class_slug_resolved,
        "subject_slug": subject_slug_resolved,
        "html": page_html,
        "blocks": body.blocks,
        "status": "published",
        "page_type": "notes",
        "updated_at": now_iso,
        "source": "studio",
    }
    # Task #349: route every seo_pages write through the shared helper
    # so created_at / updated_at are guaranteed. Revision rows have a
    # unique `topic_slug` (date-stamped) so an upsert on that slug is
    # equivalent to an insert without risking the missing-publish-date
    # regression that motivated this task.
    from seo_writes import upsert_seo_page
    if body.is_revision and body.parent_revision_id:
        from datetime import date as _date
        rev_slug = f"{body.slug}-rev-{_date.today().isoformat()}"
        revision_doc = {
            **page_doc,
            "topic_slug": rev_slug,
            "is_revision": True,
            "parent_revision_id": body.parent_revision_id,
        }
        await upsert_seo_page(
            db, {"topic_slug": rev_slug}, revision_doc,
        )
        logger.info(f"Studio revision created: {rev_slug} ← {body.parent_revision_id}")
    else:
        await upsert_seo_page(
            db,
            {"topic_slug": body.slug, "page_type": "notes"},
            page_doc,
        )

    try:
        from routes.bot_discovery import notify_indexnow_for_page
        asyncio.create_task(notify_indexnow_for_page(page_doc))
    except Exception:
        pass

    # ── 4b. Embed page for vector search ─────────────────────────────────────
    # Run fire-and-forget so publish response is never delayed by embedding
    _embed_content = " ".join(
        (b.get("content") or b.get("text") or "")
        for b in (body.blocks or []) if isinstance(b, dict)
    )
    if not _embed_content:
        _embed_content = body.title or ""
    asyncio.create_task(_embed_and_store_page(body.slug, _embed_content))

    # ── 5. Auto-create syllabus CMS stub when syllabus block detected ──────────
    syllabus_block = next((b for b in body.blocks if b.get("type") == "syllabus"), None)
    if syllabus_block and body.subject_id:
        syl_title = f"{body.title} — Syllabus Scope"
        syl_slug  = f"{body.slug}-syllabus"
        syl_id    = str(uuid.uuid4())
        syl_doc = {
            "id":               syl_id,
            "title":            syl_title,
            "seo_slug":         syl_slug,
            "content":          syllabus_block.get("content", ""),
            "type":             "syllabus",
            "status":           "draft",
            "linked_subject_id": body.subject_id,
            "linked_board_id":  body.board_id,
            "linked_class_id":  body.class_id,
            "linked_stream_id": body.stream_id,
            "source":           "studio-auto",
            "created_at":       now_iso,
            "updated_at":       now_iso,
        }
        await db.cms_documents.update_one(
            {"seo_slug": syl_slug},
            {"$set": syl_doc},
            upsert=True,
        )
        logger.info(f"Syllabus CMS stub auto-created: {syl_slug}")

    logger.info(f"Studio published: {body.slug} → {publish_url}")
    return {"success": True, "slug": body.slug, "url": publish_url}


# ── SEO / GEO Metadata Generator ──────────────────────────────────────────────

@router.post("/admin/seo/generate")
async def generate_seo_metadata(data: dict = Body(...), admin: dict = Depends(get_admin_user)):
    """Generate syllabus-anchored, thick-page SEO + GEO metadata using AI."""
    title          = (data.get("title") or "").strip()
    content_snippet= (data.get("content") or "")[:3000].strip()
    primary_keyword= (data.get("primary_keyword") or "").strip()
    seo_tags       = (data.get("seo_tags") or "").strip()
    linked_scope   = (data.get("linked_scope") or "").strip()
    board          = (data.get("board") or "Assamboard").strip()
    class_name     = (data.get("class_name") or "").strip()
    subject        = (data.get("subject") or "").strip()

    prompt = f"""You are an expert SEO + GEO (Generative Engine Optimization) strategist for Syrabit.ai, the educational browser for Assamboard students (AHSEC Class 11-12, SEBA, Degree: B.Com/B.A/B.Sc) in Assam, India.

GOAL: Generate high-impact, syllabus-anchored SEO & GEO metadata. Every page must be THICK and authoritative — no thin pages. One page should comprehensively cover one syllabus topic with notes + definitions + PYQ patterns + solved examples + MCQs so it ranks as the single best result for that topic.

Page context:
- Title/Topic:       {title or '(not set)'}
- Primary Keyword:   {primary_keyword or '(derive from topic)'}
- Subject/Chapter:   {subject or '(educational content)'}
- Board:             {board}
- Class:             {class_name or '(not specified)'}
- Syllabus scope:    {linked_scope or '(not linked)'}
- Existing tags:     {seo_tags or '(none)'}
- Content snippet:   {content_snippet[:600] or '(not provided)'}

BRAND RULE: Use "Assamboard" (one word, capital A) as the primary brand term in titles and descriptions. Use "AHSEC" / "SEBA" / "Degree" only as secondary qualifiers for search matching.

Rules for SEO Title (55-65 characters):
- Primary keyword FIRST — match exactly what Assam students search
- Include "Assamboard" as the board identifier
- Include content depth signal: "Complete Notes" / "Full Chapter" / "Solved PYQ" / "Detailed Guide"
- Power word: Complete, Free, Detailed, Solved, Official, Comprehensive
- End exactly with " | Syrabit"
- Total: 55-65 characters, never truncated by Google
- Example: "Photosynthesis Complete Notes Assamboard Class 12 | Syrabit"

Rules for Meta Description (148-158 characters):
- Open with the primary syllabus topic + what the page covers comprehensively
- Signal page depth: "covers definitions, derivations, solved PYQ, MCQs, and board exam tips"
- Include authority signal: "per Assamboard syllabus" or "NCERT + Assamboard aligned"
- End with CTA: "Free on Syrabit." or "Study free on Syrabit."
- 148-158 characters EXACTLY (count carefully)

Rules for Primary Keyword (4-7 words):
- Exact-match what Assam students type in Google
- Format: "[topic] [subject] Assamboard [class] notes" or "[topic] class 12 Assamboard notes"
- Must be syllabus-anchored — the topic must exist in the official syllabus

Rules for SEO Tags (8-12 comma-separated):
- Mix: syllabus topic exact match, "Assamboard [subject] notes", "[topic] class [X]", "[topic] PYQ", "[topic] MCQ", "Assam board exam", long-tail question variants
- Always include: Assamboard, the class, the subject, "notes", "Assam"

Rules for GEO Authority Phrases (3 phrases for AI citation):
- Must sound like authoritative syllabus citations an AI engine would quote
- Reference real curriculum sources: "As per Assamboard {class_name or ''} syllabus 2024-25", "According to NCERT/SCERT Assam prescribed textbook", "Based on AHSEC board exam pattern analysis"
- These phrases get embedded in content for Perplexity/ChatGPT citation eligibility

Rules for Schema Type:
- "Article" for chapter notes/guides
- "FAQPage" for PYQ/FAQ-heavy pages
- "HowTo" for step-by-step derivations/solved problems
- "Course" for full subject overview pages
- Choose the BEST fit based on content type

Return ONLY valid JSON — no markdown fences, no commentary:
{{"seo_title":"...","meta_description":"...","primary_keyword":"...","seo_tags":"tag1, tag2, tag3, tag4, tag5, tag6, tag7, tag8, tag9, tag10","geo_phrases":["...","...","..."],"schema_type":"Article","char_counts":{{"title":0,"meta":0}}}}"""

    try:
        result = await call_llm_api_content([{"role": "user", "content": prompt}], max_tokens=700)
        json_match = re.search(r'\{.*\}', result, re.DOTALL)
        if not json_match:
            raise ValueError("No JSON in LLM response")
        obj = json.loads(json_match.group())
        # Enforce hard limits
        seo_title = (obj.get("seo_title") or title or "Educational Notes | Syrabit")[:70]
        meta_desc = (obj.get("meta_description") or "")[:160]
        obj["seo_title"]       = seo_title
        obj["meta_description"]= meta_desc
        obj["char_counts"]     = {"title": len(seo_title), "meta": len(meta_desc)}
        logger.info(f"SEO generate: title={len(seo_title)}ch meta={len(meta_desc)}ch")
        return obj
    except Exception as e:
        logger.error(f"SEO generate error: {e}")
        raise HTTPException(500, "AI SEO generation failed — check logs")


# ── Studio Draft CRUD ─────────────────────────────────────────────────────────

@router.get("/admin/studio/drafts")
async def list_studio_drafts(admin: dict = Depends(get_admin_user)):
    """List all studio drafts, newest first."""
    drafts = await db.studio_drafts.find({}, {"_id": 0}).sort("updated_at", -1).limit(50).to_list(50)
    return drafts


@router.post("/admin/studio/drafts")
async def save_studio_draft(data: dict = Body(...), admin: dict = Depends(get_admin_user)):
    """Save or update a studio draft by slug."""
    slug = data.get("slug", "").strip()
    draft_id = data.get("id") or str(uuid.uuid4())
    now_iso  = datetime.now(timezone.utc).isoformat()
    draft = {
        "id":           draft_id,
        "title":        data.get("title", "Untitled"),
        "slug":         slug,
        "blocks":       data.get("blocks", []),
        "subject_id":   data.get("subject_id", ""),
        "board_id":     data.get("board_id", ""),
        "class_id":     data.get("class_id", ""),
        "stream_id":    data.get("stream_id", ""),
        "subject_slug": data.get("subject_slug", ""),
        "updated_at":   now_iso,
    }
    existing = await db.studio_drafts.find_one({"slug": slug} if slug else {"id": draft_id}, {"_id": 0, "created_at": 1})
    if not existing:
        draft["created_at"] = now_iso
    filter_q = {"slug": slug} if slug else {"id": draft_id}
    await db.studio_drafts.update_one(filter_q, {"$set": draft}, upsert=True)
    logger.info(f"Studio draft saved: {draft_id} ({slug})")
    return {"id": draft_id, "message": "Draft saved"}


@router.delete("/admin/studio/drafts/{draft_id}")
async def delete_studio_draft(draft_id: str, admin: dict = Depends(get_admin_user)):
    await db.studio_drafts.delete_one({"id": draft_id})
    return {"message": "Draft deleted"}


@router.post("/admin/studio/drafts/{draft_id}/publish")
async def publish_studio_draft(draft_id: str, data: dict = Body(default={}), admin: dict = Depends(get_admin_user)):
    """Publish a saved draft. Optional body overrides: board_id, class_id, is_revision, parent_revision_id."""
    draft = await db.studio_drafts.find_one({"id": draft_id}, {"_id": 0})
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    pub_body = StudioPublishRequest(
        title            = draft.get("title", "Untitled"),
        slug             = draft.get("slug", draft_id),
        blocks           = draft.get("blocks", []),
        subject_id       = draft.get("subject_id", ""),
        board_id         = data.get("board_id", draft.get("board_id", "")),
        class_id         = data.get("class_id", draft.get("class_id", "")),
        stream_id        = data.get("stream_id", draft.get("stream_id", "")),
        subject_slug     = draft.get("subject_slug", ""),
        is_revision      = data.get("is_revision", False),
        parent_revision_id = data.get("parent_revision_id", ""),
    )
    result = await admin_studio_publish(pub_body, admin)
    await db.studio_drafts.update_one({"id": draft_id}, {"$set": {"last_published_at": datetime.now(timezone.utc).isoformat()}})
    return {**result, "draft_id": draft_id}


# ─────────────────────────────────────────────
# PHASE C: ADVANCED ANALYTICS
# ─────────────────────────────────────────────
@router.get("/admin/analytics/funnel")
async def admin_analytics_funnel(admin: dict = Depends(get_admin_user)):
    users = await supa_list_users()
    total = len(users)
    chatted = 0
    paid = 0
    for u in users:
        if u.get("credits_used", 0) > 0:
            chatted += 1
        if u.get("plan") in ("starter", "pro"):
            paid += 1

    payments = await db.payments.find({}, {"_id": 0}).to_list(5000)
    # Task #740 — Stripe-aware revenue using the same _row_inr helper as
    # Monetization (#731 S3). The previous paise-only sum dropped every
    # Stripe payment from the funnel's "revenue / paid user" tile.
    from routes.admin_advanced import _row_inr, _currency_breakdown
    total_revenue = sum(_row_inr(p) for p in payments)

    return {
        "funnel": [
            {"stage": "Signed Up", "count": total, "pct": 100},
            {"stage": "Used Chat", "count": chatted, "pct": round(chatted / max(total, 1) * 100, 1)},
            {"stage": "Paid User", "count": paid, "pct": round(paid / max(total, 1) * 100, 1)},
        ],
        "revenue_per_user": round(total_revenue / max(paid, 1), 2),
        "conversion_rate": round(paid / max(total, 1) * 100, 2),
        "currency_breakdown": _currency_breakdown(payments),
    }

@router.get("/admin/analytics/content-heatmap")
async def admin_analytics_content_heatmap(admin: dict = Depends(get_admin_user)):
    subject_pipeline = [
        {"$match": {"event_type": {"$in": ["subject_view", "chapter_view"]}}},
        {"$group": {"_id": "$subject_id", "views": {"$sum": 1}}},
        {"$sort": {"views": -1}},
        {"$limit": 30},
    ]
    try:
        results = await db.analytics.aggregate(subject_pipeline).to_list(30)
    except Exception:
        results = []

    subject_names = {}
    if results:
        try:
            sids = [r["_id"] for r in results if r["_id"]]
            subjects = await db.subjects.find(
                {"id": {"$in": sids}},
                {"_id": 0, "id": 1, "name": 1}
            ).to_list(500)
            subject_names = {s["id"]: s["name"] for s in subjects}
        except Exception:
            pass

    top_searches = []
    try:
        search_pipeline = [
            {"$match": {"event_type": "search", "search_query": {"$ne": None, "$ne": ""}}},
            {"$group": {"_id": "$search_query", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 20},
        ]
        top_searches = await db.analytics.aggregate(search_pipeline).to_list(20)
    except Exception:
        pass

    return {
        "top_subjects": [
            {"name": subject_names.get(r["_id"], r["_id"] or "Unknown"), "views": r["views"]}
            for r in results if r["_id"]
        ],
        "top_searches": [{"query": r["_id"] or "Unknown", "count": r["count"]} for r in top_searches if r["_id"]],
    }

@router.get("/admin/analytics/content-card-views")
async def admin_analytics_content_card_views(days: int = Query(0, ge=0), admin: dict = Depends(get_admin_user)):
    cutoff = None
    if days > 0:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    subjects = await db.subjects.find(
        {},
        {"_id": 0, "id": 1, "name": 1, "slug": 1, "boardId": 1, "boardName": 1, "className": 1}
    ).to_list(1000)

    def _slugify_lower(s):
        return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")

    path_key_to_subject = {}
    id_to_subject = {}
    for s in subjects:
        slug = s.get("slug", "")
        board_slug = _slugify_lower(s.get("boardName", ""))
        class_slug = _slugify_lower(s.get("className", ""))
        if slug and board_slug and class_slug:
            path_key_to_subject[f"{board_slug}/{class_slug}/{slug}"] = s
        id_to_subject[s["id"]] = s

    pv_match = {"path": {"$regex": r"^/[^/]+/[^/]+/[^/]+/?$"}}
    if cutoff:
        pv_match["timestamp"] = {"$gte": cutoff}
    pv_pipeline = [
        {"$match": pv_match},
        {"$group": {
            "_id": "$path",
            "views": {"$sum": 1},
            "unique_visitors": {"$addToSet": "$visitor_id"},
        }},
        {"$project": {
            "path": "$_id",
            "views": 1,
            "unique_visitors": {"$size": "$unique_visitors"},
            "_id": 0,
        }},
    ]
    try:
        pv_results = await db.page_views.aggregate(pv_pipeline).to_list(1000)
    except Exception:
        pv_results = []

    sv_match = {"event_type": "subject_view", "subject_id": {"$ne": None}}
    if cutoff:
        sv_match["timestamp"] = {"$gte": cutoff}
    sv_pipeline = [
        {"$match": sv_match},
        {"$group": {
            "_id": "$subject_id",
            "views": {"$sum": 1},
            "unique_visitors": {"$addToSet": "$user_id"},
        }},
        {"$project": {
            "subject_id": "$_id",
            "views": 1,
            "unique_visitors": {"$size": "$unique_visitors"},
            "_id": 0,
        }},
    ]
    try:
        sv_results = await db.analytics.aggregate(sv_pipeline).to_list(1000)
    except Exception:
        sv_results = []

    merged = {}
    for pv in pv_results:
        path = pv["path"].strip("/")
        parts = path.split("/")
        if len(parts) < 3:
            continue
        path_key = "/".join(parts[:3])
        subj = path_key_to_subject.get(path_key)
        if not subj:
            continue
        sid = subj["id"]
        if sid not in merged:
            merged[sid] = {
                "subject_id": sid,
                "name": subj.get("name", parts[2]),
                "board": subj.get("boardName", parts[0]),
                "class_name": subj.get("className", parts[1]),
                "page_views": 0,
                "unique_visitors": 0,
            }
        merged[sid]["page_views"] += pv["views"]
        merged[sid]["unique_visitors"] = max(merged[sid]["unique_visitors"], pv["unique_visitors"])

    for sv in sv_results:
        sid = sv["subject_id"]
        subj = id_to_subject.get(sid)
        if sid not in merged:
            merged[sid] = {
                "subject_id": sid,
                "name": subj.get("name", sid) if subj else sid,
                "board": subj.get("boardName", "") if subj else "",
                "class_name": subj.get("className", "") if subj else "",
                "page_views": 0,
                "unique_visitors": 0,
            }
        merged[sid]["page_views"] += sv["views"]
        merged[sid]["unique_visitors"] = max(merged[sid]["unique_visitors"], sv["unique_visitors"])

    ranked = sorted(merged.values(), key=lambda x: x["page_views"], reverse=True)[:30]
    return {"content_card_views": ranked, "total": len(ranked), "days": days}

@router.get("/admin/analytics/revenue")
async def admin_analytics_revenue(days: int = 30, admin: dict = Depends(get_admin_user)):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    payments = await db.payments.find(
        {"verified_at": {"$gte": cutoff}},
        {"_id": 0}
    ).sort("verified_at", 1).to_list(5000)

    # Task #740 — Stripe-aware: use _row_inr so daily totals include
    # USD payments at the FX rate captured at-payment-time. The
    # previous `amount_paise / 100` was Razorpay-only.
    from routes.admin_advanced import _row_inr, _currency_breakdown
    daily = {}
    for p in payments:
        day = p.get("verified_at", "")[:10]
        if not day:
            continue
        if day not in daily:
            daily[day] = {"date": day, "revenue_inr": 0, "count": 0}
        daily[day]["revenue_inr"] = round(daily[day]["revenue_inr"] + _row_inr(p), 2)
        daily[day]["count"] += 1

    users = await supa_list_users()
    cohorts = {"free": 0, "starter": 0, "pro": 0}
    for u in users:
        plan = u.get("plan", "free")
        cohorts[plan] = cohorts.get(plan, 0) + 1

    return {
        "daily_revenue": sorted(daily.values(), key=lambda x: x["date"]),
        "cohorts": cohorts,
        "total_payments": len(payments),
        "currency_breakdown": _currency_breakdown(payments),
    }

@router.get("/admin/analytics/predictor")
async def admin_analytics_predictor(admin: dict = Depends(get_admin_user)):
    now = datetime.now(timezone.utc)
    thirty_ago = (now - timedelta(days=30)).isoformat()
    sixty_ago = (now - timedelta(days=60)).isoformat()

    recent = await db.payments.count_documents({"verified_at": {"$gte": thirty_ago}})
    prior = await db.payments.count_documents({"verified_at": {"$gte": sixty_ago, "$lt": thirty_ago}})

    recent_rev = 0
    async for p in db.payments.find({"verified_at": {"$gte": thirty_ago}}, {"_id": 0}):
        recent_rev += p.get("amount_paise", 0) / 100

    growth_rate = ((recent - prior) / max(prior, 1)) if prior > 0 else 0
    predicted_mrr = round(recent_rev * (1 + growth_rate * 0.5), 2)

    all_users = await supa_list_users()
    users_this_month = sum(1 for u in all_users if (u.get("created_at") or "") >= thirty_ago)
    users_last_month = sum(1 for u in all_users if sixty_ago <= (u.get("created_at") or "") < thirty_ago)

    return {
        "current_mrr_inr": recent_rev,
        "predicted_mrr_inr": predicted_mrr,
        "growth_rate_pct": round(growth_rate * 100, 1),
        "payments_this_month": recent,
        "payments_last_month": prior,
        "signups_this_month": users_this_month,
        "signups_last_month": users_last_month,
    }


@router.get("/admin/analytics/daily")
async def admin_analytics_daily(
    days: int = 30,
    admin: dict = Depends(get_admin_user),
):
    """
    Per-day analytics for the Daily Analytics panel.
    Returns visitors, page_views, signups, messages, and AI interactions
    for each day in the requested range (default: last 30 days).
    Visitor/page-view data is sourced from Cloudflare only (Task #364).
    """
    now = datetime.now(timezone.utc)

    # Build a lookup dict indexed by YYYY-MM-DD for easy merging
    day_keys = [(now - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d") for i in range(days)]
    daily: dict[str, dict] = {
        d: {
            "date": d,
            "visitors": 0,
            "page_views": 0,
            "signups": 0,
            "messages": 0,
            "ai_interactions": 0,
            "sessions": 0,
            "bounce_rate": None,
            "avg_session_duration": None,
        }
        for d in day_keys
    }

    # ── 1. Visitor / page-view data — Cloudflare only (Task #364) ──────────
    cf_connected = False
    try:
        cf_daily_data = await cloudflare_client.get_historical_daily(days=days)
        cf_connected = bool(cf_daily_data)
        for entry in (cf_daily_data or []):
            d = entry.get("date", "")
            if d in daily:
                daily[d]["visitors"] = entry.get("visitors", 0)
                daily[d]["page_views"] = entry.get("page_views", 0)
                daily[d]["visitor_source"] = "cloudflare"
    except Exception:
        pass

    # ── 2. Signups (Supabase users by created_at date) ───────────────────────
    try:
        users = await supa_list_users()
        for u in users:
            d = (u.get("created_at") or "")[:10]
            if d in daily:
                daily[d]["signups"] += 1
    except Exception:
        pass

    # ── 3. Messages (from Supabase/PG conversations) ──────────────────────
    try:
        cutoff_dt = (now - timedelta(days=days)).isoformat()
        seen_conv_ids = set()

        if deps.pg_pool:
            try:
                async with deps.pg_pool.acquire() as conn:
                    rows = await conn.fetch(
                        "SELECT id, created_at, messages FROM conversations WHERE created_at >= $1",
                        cutoff_dt,
                    )
                    for r in _pg_rows(rows):
                        seen_conv_ids.add(r["id"])
                        ca = str(r.get("created_at", ""))[:10]
                        if ca in daily:
                            msgs = r.get("messages") or []
                            if isinstance(msgs, str):
                                try: msgs = json.loads(msgs)
                                except Exception: msgs = []
                            daily[ca]["messages"] += len(msgs) if isinstance(msgs, list) else 0
            except Exception:
                pass

        if supa:
            try:
                offset_s = 0
                while True:
                    r = await _supa(lambda o=offset_s: supa.table("conversations")
                        .select("id, created_at, messages")
                        .gte("created_at", cutoff_dt)
                        .range(o, o + 199).execute())
                    batch = r.data or []
                    if not batch:
                        break
                    for row in batch:
                        if row.get("id") in seen_conv_ids:
                            continue
                        ca = (row.get("created_at") or "")[:10]
                        if ca in daily:
                            msgs = row.get("messages")
                            if isinstance(msgs, str):
                                try: msgs = json.loads(msgs)
                                except Exception: msgs = []
                            daily[ca]["messages"] += len(msgs) if isinstance(msgs, list) else 0
                    offset_s += 200
                    if len(batch) < 200:
                        break
            except Exception:
                pass
    except Exception:
        pass

    # ── 4. AI interactions (analytics events of type ask_ai_click) ───────────
    try:
        cutoff_dt = (now - timedelta(days=days)).isoformat()
        pipeline_ai = [
            {"$match": {"event_type": "ask_ai_click", "timestamp": {"$gte": cutoff_dt}}},
            {"$group": {"_id": {"$substr": ["$timestamp", 0, 10]}, "count": {"$sum": 1}}},
        ]
        ai_rows = await db.analytics.aggregate(pipeline_ai).to_list(days + 5)
        for row in ai_rows:
            d = row["_id"]
            if d in daily:
                daily[d]["ai_interactions"] = row["count"]
    except Exception:
        pass

    result = sorted(daily.values(), key=lambda x: x["date"])

    # Compute day-over-day deltas for summary cards (last day vs second-to-last)
    def pct_change(a, b):
        if b == 0:
            return None
        return round((a - b) / b * 100, 1)

    today_data = result[-1] if result else {}
    prev_data = result[-2] if len(result) >= 2 else {}

    summary = {
        "visitors": {
            "today": today_data.get("visitors", 0),
            "change_pct": pct_change(today_data.get("visitors", 0), prev_data.get("visitors", 0)),
        },
        "page_views": {
            "today": today_data.get("page_views", 0),
            "change_pct": pct_change(today_data.get("page_views", 0), prev_data.get("page_views", 0)),
        },
        "signups": {
            "today": today_data.get("signups", 0),
            "change_pct": pct_change(today_data.get("signups", 0), prev_data.get("signups", 0)),
        },
        "messages": {
            "today": today_data.get("messages", 0),
            "change_pct": pct_change(today_data.get("messages", 0), prev_data.get("messages", 0)),
        },
        "ai_interactions": {
            "today": today_data.get("ai_interactions", 0),
            "change_pct": pct_change(today_data.get("ai_interactions", 0), prev_data.get("ai_interactions", 0)),
        },
    }

    return {"daily": result, "summary": summary, "days": days, "cf_connected": cf_connected}


# ─────────────────────────────────────────────
# MOVED — GA4 OAUTH SETUP → routes/admin_ga4.py        (Task #5 split)
# MOVED — VERTEX AI / GEMINI    → routes/admin_vertex.py  (Task #5 split)
# Both routers are mounted in server.py alongside this one;
# all paths (/admin/ga4/*, /admin/vertex/*) and behaviour are unchanged.
# ─────────────────────────────────────────────



@router.delete("/admin/syllabus/reset-all")
async def admin_syllabus_reset_all(admin: dict = Depends(get_admin_user)):
    """Wipe all subjects and chapters so a fresh syllabus can be uploaded."""
    sub_result = await db.subjects.delete_many({})
    ch_result  = await db.chapters.delete_many({})
    logger.info(f"Syllabus reset by {admin.get('email','?')} — deleted {sub_result.deleted_count} subjects, {ch_result.deleted_count} chapters")
    # Wiped every subject + chapter — fire the deploy hook immediately so
    # the prerendered HTML doesn't keep advertising deleted pages (Task #398).
    try:
        from routes.admin_content import _trigger_prerender_now
        await _trigger_prerender_now(
            f"syllabus_reset:{sub_result.deleted_count}_subjects_{ch_result.deleted_count}_chapters"
        )
    except Exception as exc:
        logger.warning("syllabus_reset_all: prerender trigger import/call failed: %s", exc)
    return {
        "deleted_subjects": sub_result.deleted_count,
        "deleted_chapters":  ch_result.deleted_count,
        "message": "All subjects and chapters cleared. Upload new syllabus via Admin → Syllabus Manager.",
    }



# ─────────────────────────────────────────────
# PHASE D: AUTOMATION ENGINE
# ─────────────────────────────────────────────
@router.get("/admin/automation/insights")
async def admin_automation_insights(admin: dict = Depends(get_admin_user)):
    seo_topics = await db.seo_topics.find({}, {"_id": 0, "slug": 1, "title": 1, "status": 1}).to_list(5000)
    published_slugs = {t["slug"] for t in seo_topics if t.get("status") == "published"}

    chat_topics = []
    try:
        pipeline = [
            {"$unwind": "$messages"},
            {"$match": {"messages.role": "user"}},
            {"$group": {"_id": "$messages.content", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 50},
        ]
        chat_topics = await db.conversations.aggregate(pipeline).to_list(50)
    except Exception:
        pass

    content_gaps = []
    for ct in chat_topics[:20]:
        query = ct.get("_id", "")
        if query and len(query) > 10:
            slug_candidate = re.sub(r'[^a-z0-9]+', '-', query.lower().strip())[:60]
            if slug_candidate not in published_slugs:
                content_gaps.append({"query": query[:100], "count": ct["count"], "suggested_slug": slug_candidate})

    low_content_subjects = []
    try:
        subjects = await db.subjects.find({}, {"_id": 0, "name": 1, "id": 1}).to_list(100)
        for subj in subjects[:30]:
            topic_count = await db.seo_topics.count_documents({"subject_slug": {"$regex": re.sub(r'[^a-z0-9]+', '-', subj.get("name", "").lower())}})
            if topic_count < 3:
                low_content_subjects.append({"name": subj.get("name", ""), "id": subj.get("id", ""), "seo_pages": topic_count})
    except Exception:
        pass

    high_quality_chats = []
    try:
        qa_pipeline = [
            {"$unwind": "$messages"},
            {"$match": {"messages.role": "assistant"}},
            {"$project": {"content": "$messages.content", "msg_id": "$messages.id", "conv_id": "$_id"}},
            {"$match": {"content": {"$regex": ".{200,}"}}},
            {"$limit": 10},
        ]
        high_quality_chats = await db.conversations.aggregate(qa_pipeline).to_list(10)
    except Exception:
        pass

    return {
        "content_gaps": content_gaps[:15],
        "low_content_subjects": low_content_subjects[:10],
        "promotable_chats": len(high_quality_chats),
        "total_seo_topics": len(seo_topics),
        "published_count": len(published_slugs),
    }

@router.post("/admin/automation/auto-generate")
async def admin_automation_auto_generate(admin: dict = Depends(get_admin_user)):
    insights = await admin_automation_insights(admin)
    gaps = insights.get("content_gaps", [])[:5]
    generated = []
    for gap in gaps:
        slug = gap["suggested_slug"]
        title = gap["query"].title()
        now_iso = datetime.now(timezone.utc).isoformat()
        geo_meta = {
            "geo_source": "auto-generated from content gap",
            "geo_query_count": gap.get("count", 0),
            "geo_suggested_sections": [
                "Summary (cite AHSEC syllabus)",
                "Definition (NCERT/SCERT reference)",
                "Explanation (curriculum-aligned)",
                "PYQs (with year and marks)",
                "FAQs (3 common student questions)",
            ],
        }
        # Task #349: route through the shared helper. Note that
        # `created_at` is dropped from the $set payload — the helper
        # promotes it to $setOnInsert so it survives later upserts.
        from seo_writes import upsert_seo_topic
        await upsert_seo_topic(
            db,
            {"slug": slug},
            {
                "title": title,
                "slug": slug,
                "status": "draft",
                "source": "auto-generated",
                "geo_meta": geo_meta,
            },
        )
        generated.append({"slug": slug, "title": title, "geo_meta": geo_meta})
    return {"generated": generated, "count": len(generated)}


# ─────────────────────────────────────────────
# CMS SCRAPER STATUS — surfaces scraper blockers
# GET /admin/cms/scraper-status
# ─────────────────────────────────────────────

@router.get("/admin/cms/scraper-status")
async def admin_cms_scraper_status(admin: dict = Depends(get_admin_user)):
    """
    Surfaces the status of the personalized CMS scraper pipeline and any blockers.
    Checks:
      1. CmsNoIndexMiddleware anti-scraper UA blocklist (python-requests, wget, curl, etc.)
      2. _cms_request_ctx context-var scraper-prevention flag (no web-search from within CMS)
      3. Paid-gate enforcement — users on free plan receive 402
      4. cms_documents collection — total personal plans, recent failures, empty content
      5. LLM connectivity — new plans fail silently if LLM is down
    Returns a status summary + prioritised blocker list for the admin Automation panel.
    """
    blockers = []
    stats = {
        "total_plans": 0,
        "published_plans": 0,
        "error_plans": 0,
        "empty_plans": 0,
        "paid_users": 0,
        "free_users": 0,
        "scraper_status": "ok",
    }

    # ── Structural/architectural blocker checks (always run, no DB required) ──
    # 1. CmsNoIndexMiddleware UA blocklist — automated HTTP clients are blocked 403
    blocked_ua_patterns = [
        "python-requests", "wget", "curl", "scrapy", "go-http-client",
        "ahrefsbot", "semrushbot", "gptbot", "claudebot", "perplexitybot",
        "bingbot", "googlebot", "yandexbot", "duckduckbot",
    ]
    blockers.append({
        "type": "ua_blocklist_active",
        "message": (
            "CmsNoIndexMiddleware is ACTIVE on all /api/cms/* routes. "
            f"The following User-Agent patterns are blocked with 403: {', '.join(blocked_ua_patterns[:6])} (and {len(blocked_ua_patterns)-6} more). "
            "Any external scraper using these clients will receive 403 Forbidden — use a browser-like UA or authenticated SDK client."
        ),
        "severity": "warning",
        "detail": {
            "middleware": "CmsNoIndexMiddleware",
            "path_prefix": "/api/cms/",
            "blocked_uas": blocked_ua_patterns,
            "response_headers": ["X-Robots-Tag: noindex, nofollow", "Cache-Control: private, no-store"],
        },
    })

    # 2. Context-var web-search prevention — outbound web calls raise 403 from within CMS handlers
    blockers.append({
        "type": "cms_request_ctx_guard",
        "message": (
            "_cms_request_ctx context variable is set to True for all /api/cms/* requests. "
            "This structurally prevents outbound web-search/firecrawl calls from executing inside CMS handlers — "
            "any scraper that relies on web fetching will silently get a 403 from the guard. "
            "CMS content generation uses only call_slm + MongoDB (no external fetching)."
        ),
        "severity": "info",
        "detail": {
            "guard_var": "_cms_request_ctx",
            "effect": "Raises HTTP 403 if any outbound web/scrape call is attempted from CMS handlers",
        },
    })

    try:
        if not await is_mongo_available():
            blockers.insert(0, {
                "type": "db_unavailable",
                "message": "MongoDB unavailable — CMS scraper cannot read/write personalized plans",
                "severity": "critical",
            })
            stats["scraper_status"] = "critical"
            return {"status": "critical", "blockers": blockers, "stats": stats, "recent_plans": []}

        # Count all personalized plans
        stats["total_plans"]     = await db.cms_documents.count_documents({"doc_type": "personalized"})
        stats["published_plans"] = await db.cms_documents.count_documents({"doc_type": "personalized", "status": "published"})
        stats["error_plans"]     = await db.cms_documents.count_documents({"doc_type": "personalized", "status": "error"})

        # Detect plans with empty/too-short content (generation truncation blocker)
        sample_plans = await db.cms_documents.find(
            {"doc_type": "personalized", "status": "published"},
            {"_id": 0, "id": 1, "title": 1, "user_id": 1, "content": 1, "word_count": 1, "created_at": 1}
        ).sort("created_at", -1).limit(50).to_list(50)

        for plan in sample_plans:
            wc = plan.get("word_count") or len((plan.get("content") or "").split())
            if wc < 50:
                stats["empty_plans"] += 1

        if stats["error_plans"] > 0:
            blockers.append({
                "type": "generation_errors",
                "message": f"{stats['error_plans']} personalized plan(s) failed during generation (LLM timeout or prompt error). "
                           "Check recent error documents and verify LLM key health below.",
                "severity": "high",
                "count": stats["error_plans"],
            })

        if stats["empty_plans"] > 0:
            blockers.append({
                "type": "empty_content",
                "message": f"{stats['empty_plans']} published plan(s) have fewer than 50 words — "
                           "content generation may have been truncated by LLM token limit or rate limit.",
                "severity": "medium",
                "count": stats["empty_plans"],
            })

        # Check paid/free user breakdown — free users get 402 from /cms/personalize
        try:
            all_users = await supa_list_users()
            paid_users = [u for u in all_users if u.get("plan", "free") in {"starter", "pro"}]
            free_users = [u for u in all_users if u.get("plan", "free") == "free"]
            stats["paid_users"] = len(paid_users)
            stats["free_users"] = len(free_users)
            if len(paid_users) == 0 and stats["total_plans"] > 0:
                blockers.append({
                    "type": "no_paid_users",
                    "message": (
                        f"Plans exist in DB but 0 users are on Starter/Pro — "
                        "POST /api/cms/personalize will return 402 for ALL users. "
                        f"Total users: {len(all_users)}, all on free plan."
                    ),
                    "severity": "warning",
                })
        except Exception:
            pass

        # Check LLM connectivity — quick probe (new plan generation fails if LLM is down)
        llm_ok = True
        try:
            test_resp = await call_llm_api([{"role": "user", "content": "Say OK"}], model="sarvam-m", max_tokens=5)
            if not test_resp or len(test_resp.strip()) == 0:
                llm_ok = False
        except Exception:
            llm_ok = False

        if not llm_ok:
            blockers.append({
                "type": "llm_unavailable",
                "message": "LLM provider is unreachable — new personalized plans will fail at generation step. "
                           "Existing published plans are still served from MongoDB.",
                "severity": "critical",
            })

        # Overall status
        if any(b["severity"] == "critical" for b in blockers):
            stats["scraper_status"] = "critical"
        elif any(b["severity"] == "high" for b in blockers):
            stats["scraper_status"] = "degraded"
        elif any(b["severity"] in ("medium", "warning") for b in blockers):
            stats["scraper_status"] = "warning"
        else:
            stats["scraper_status"] = "ok"

        return {
            "status": stats["scraper_status"],
            "blockers": blockers,
            "stats": stats,
            "recent_plans": [
                {
                    "id": p.get("id"), "title": p.get("title"), "user_id": p.get("user_id"),
                    "word_count": p.get("word_count") or len((p.get("content") or "").split()),
                    "created_at": p.get("created_at"),
                }
                for p in sample_plans[:5]
            ],
        }

    except Exception as exc:
        logger.error(f"admin_cms_scraper_status error: {exc}")
        return {
            "status": "error",
            "blockers": [{"type": "internal_error", "message": str(exc)[:200], "severity": "critical"}],
            "stats": stats,
        }


# ─────────────────────────────────────────────
# PHASE E: MONETIZATION ANALYTICS
# ─────────────────────────────────────────────
