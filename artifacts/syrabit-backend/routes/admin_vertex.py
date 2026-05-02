"""Syrabit.ai — Vertex AI / Gemini powered services (admin).

Carved out of ``cms_sarvam_health.py`` (Task #5 of the admin-panel
audit) so the routes live in a file whose name reflects what they do.
The 10 endpoints here wrap the Vertex AI / Gemini layer that powers
the admin Studio's translation, content enhancement, quality
scoring, topic suggestions, SEO meta generation, semantic search,
content-gap analysis and PDF extraction.

Auth flows through the Cloudflare AI Gateway BYOK binding — see
``docs/VERTEX_SETUP.md`` 'Migrating Railway → CF AI Gateway BYOK
(Task #666)'.

Routes (all ``/api/admin/vertex/*``):
  * GET  /health             — multi-service health check
  * GET  /probe-status       — cached state of the periodic Gemini probe (Task #689)
  * POST /translate          — translate to Assamese / regional languages
  * POST /semantic-search    — embedding search across published topics
  * POST /enhance            — improve AI-generated content
  * POST /quality-score      — score educational content
  * POST /suggest-topics     — suggest missing high-value topics
  * POST /seo-meta           — generate optimised SEO metadata
  * GET  /content-gaps       — cross-reference searches with published content
  * POST /extract-document   — extract structured data from PDF textbooks
  * GET  /gcp-credits        — Google Cloud credit burn panel row (Task #247)
"""
import time
from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile

import vertex_services
from auth_deps import get_admin_user
from deps import db

router = APIRouter()


@router.get("/admin/vertex/health")
async def vertex_health(admin: dict = Depends(get_admin_user)):
    """Check status of all Vertex AI / Gemini services."""
    return await vertex_services.health_check()


@router.get("/admin/vertex/probe-status")
async def vertex_probe_status(admin: dict = Depends(get_admin_user)):
    """Task #689 — Return the cached state of the periodic Gemini health
    probe (Task #677). Read-only: this does *not* trigger a fresh probe
    (use ``/admin/vertex/health`` for that). Surfaces last-checked
    timestamp, ok/fail, last reason, consecutive failure count and the
    derived ``status`` (``ok`` / ``unknown`` / ``stale`` / ``unhealthy``)
    so the admin dashboard can render a "Gemini upstream" tile without
    spending a Vertex API call on every dashboard refresh.
    """
    import vertex_health_cache
    return vertex_health_cache.dashboard_snapshot()


@router.post("/admin/vertex/translate")
async def vertex_translate(
    text: str = Body(...),
    target_lang: str = Body("as"),
    source_lang: str = Body("en"),
    admin: dict = Depends(get_admin_user),
):
    """Translate educational content to Assamese or other regional languages."""
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    result = await vertex_services.translate(text, target_lang=target_lang, source_lang=source_lang)
    if result is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Translation failed — Gemini auth now flows through the Cloudflare AI Gateway BYOK "
                "binding (google-ai-studio / google-vertex-ai). Verify CF_AI_GATEWAY_ACCOUNT_ID, "
                "CF_AI_GATEWAY_ID and the BYOK binding in the CF dashboard, then check "
                "/admin/cms/sarvam-health/vertex/health. See docs/VERTEX_SETUP.md "
                "'Migrating Railway → CF AI Gateway BYOK (Task #666)'."
            ),
        )
    return {"translated": result, "target_lang": target_lang, "source_lang": source_lang}


@router.post("/admin/vertex/semantic-search")
async def vertex_semantic_search(
    query: str = Body(...),
    top_k: int = Body(10),
    admin: dict = Depends(get_admin_user),
):
    """Semantic search across all published SEO topics using text embeddings."""
    topics = await db.seo_topics.find(
        {}, {"_id": 0, "slug": 1, "title": 1, "subject_name": 1, "class_name": 1, "status": 1}
    ).to_list(5000)
    results = await vertex_services.semantic_search(query, topics, text_key="title", top_k=top_k)
    return {"query": query, "results": results, "total_searched": len(topics)}


@router.post("/admin/vertex/enhance")
async def vertex_enhance_content(
    content: str = Body(...),
    page_type: str = Body("notes"),
    subject: str = Body(""),
    topic: str = Body(""),
    class_name: str = Body("Class 11"),
    admin: dict = Depends(get_admin_user),
):
    """Improve AI-generated content with Gemini."""
    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    enhanced = await vertex_services.enhance_content(content, page_type, subject, topic, class_name)
    if enhanced is None:
        raise HTTPException(status_code=503, detail="Enhancement failed")
    return {"enhanced": enhanced, "original_length": len(content), "enhanced_length": len(enhanced)}


@router.post("/admin/vertex/quality-score")
async def vertex_quality_score(
    content: str = Body(...),
    page_type: str = Body("notes"),
    topic: str = Body(""),
    subject: str = Body(""),
    admin: dict = Depends(get_admin_user),
):
    """Score the quality of educational content with Gemini."""
    return await vertex_services.score_content(content, page_type, topic, subject)


@router.post("/admin/vertex/suggest-topics")
async def vertex_suggest_topics(
    subject: str = Body(...),
    class_name: str = Body("Class 11"),
    board: str = Body("AHSEC"),
    admin: dict = Depends(get_admin_user),
):
    """Suggest missing high-value topics for a subject using AI."""
    existing = await db.seo_topics.distinct(
        "title",
        {"subject_name": subject, "class_name": class_name}
    )
    suggestions = await vertex_services.suggest_topics(subject, class_name, existing, board)
    return {"subject": subject, "class_name": class_name, "suggestions": suggestions, "existing_count": len(existing)}


@router.post("/admin/vertex/seo-meta")
async def vertex_seo_meta(
    topic: str = Body(...),
    subject: str = Body(""),
    class_name: str = Body("Class 11"),
    page_type: str = Body("notes"),
    board: str = Body("AHSEC"),
    content_preview: str = Body(""),
    admin: dict = Depends(get_admin_user),
):
    """Generate optimised SEO metadata (title, description, keywords, OG tags)."""
    meta = await vertex_services.generate_seo_meta(topic, subject, class_name, page_type, board, content_preview)
    if not meta:
        raise HTTPException(status_code=503, detail="SEO meta generation failed")
    return meta


@router.get("/admin/vertex/content-gaps")
async def vertex_content_gaps(admin: dict = Depends(get_admin_user)):
    """Identify high-value content gaps by cross-referencing searches with published content."""
    published = await db.seo_topics.distinct("slug", {"status": "published"})

    search_pipeline = [
        {"$match": {"type": "search"}},
        {"$group": {"_id": "$query", "count": {"$sum": 1}}},
        {"$sort": {"count": -1}},
        {"$limit": 30},
    ]
    top_searches = []
    try:
        raw = await db.analytics.aggregate(search_pipeline).to_list(30)
        top_searches = [r["_id"] for r in raw if r.get("_id")]
    except Exception:
        pass

    subjects = await db.seo_topics.distinct("subject_name")
    gaps = await vertex_services.find_content_gaps(published, top_searches, subjects)
    return {"gaps": gaps, "published_count": len(published), "search_queries_analyzed": len(top_searches)}


@router.post("/admin/vertex/extract-document")
async def vertex_extract_document(
    file: UploadFile = File(...),
    task: str = "extract_topics",
    admin: dict = Depends(get_admin_user),
):
    """Extract structured data from PDF textbooks/question papers using Gemini 1.5 Pro."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")
    pdf_bytes = await file.read()
    if len(pdf_bytes) > 20 * 1024 * 1024:  # 20MB limit
        raise HTTPException(status_code=400, detail="PDF too large — max 20MB")
    result = await vertex_services.extract_from_document(pdf_bytes, task=task)
    return result


@router.post("/admin/vertex/ocr")
async def vertex_ocr(
    file: UploadFile = File(...),
    admin: dict = Depends(get_admin_user),
):
    """Cloud Vision equivalent — extract text from AHSEC question paper/textbook images using Gemini Vision."""
    allowed = {"image/jpeg", "image/png", "image/webp", "image/gif"}
    ct = file.content_type or ""
    if ct not in allowed:
        raise HTTPException(status_code=400, detail=f"Unsupported image type: {ct}. Use JPEG, PNG, or WebP.")
    img_bytes = await file.read()
    if len(img_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image too large — max 10MB")
    result = await vertex_services.ocr_image(img_bytes, mime_type=ct)
    if "error" in result:
        raise HTTPException(status_code=503, detail=result["error"])
    return result


@router.post("/admin/vertex/nlp-concepts")
async def vertex_nlp_concepts(
    text: str = Body(...),
    subject: str = Body(""),
    class_name: str = Body("Class 11"),
    admin: dict = Depends(get_admin_user),
):
    """Cloud Natural Language equivalent — extract key concepts, entities and difficulty from educational text."""
    if not text or len(text.strip()) < 50:
        raise HTTPException(status_code=400, detail="text must be at least 50 characters")
    result = await vertex_services.extract_key_concepts(text, subject=subject, class_name=class_name)
    if "error" in result:
        raise HTTPException(status_code=503, detail=result["error"])
    return result


@router.post("/admin/vertex/flashcards")
async def vertex_flashcards(
    text: str = Body(...),
    subject: str = Body(""),
    class_name: str = Body("Class 11"),
    count: int = Body(10),
    admin: dict = Depends(get_admin_user),
):
    """Generate revision flashcards from chapter content for students."""
    if not text or len(text.strip()) < 100:
        raise HTTPException(status_code=400, detail="text must be at least 100 characters")
    count = max(5, min(count, 20))
    result = await vertex_services.generate_flashcards(text, subject=subject, count=count, class_name=class_name)
    if "error" in result:
        raise HTTPException(status_code=503, detail=result["error"])
    return result


@router.post("/admin/vertex/mcq-generator")
async def vertex_mcq_generator(
    text: str = Body(...),
    subject: str = Body(""),
    class_name: str = Body("Class 11"),
    count: int = Body(10),
    difficulty: str = Body("mixed"),
    admin: dict = Depends(get_admin_user),
):
    """Generate AHSEC-pattern MCQ questions from chapter text."""
    if not text or len(text.strip()) < 100:
        raise HTTPException(status_code=400, detail="text must be at least 100 characters")
    count = max(5, min(count, 20))
    result = await vertex_services.generate_mcqs(text, subject=subject, class_name=class_name,
                                                  count=count, difficulty=difficulty)
    if "error" in result:
        raise HTTPException(status_code=503, detail=result["error"])
    return result


@router.get("/admin/vertex/gcp-credits")
async def gcp_credit_burn_panel(admin: dict = Depends(get_admin_user)):
    """Google Cloud Platform credit burn panel row (Task #247 / #254).

    Returns a structured breakdown of the $2,000 GCP founders credit grant.
    Spend is derived from real in-process call counters (Task #254) incremented
    on each successful provider call. Counters reset at the start of each calendar
    month, and reset to zero on process restart (see counters.counters_reset_on_restart).

    Budget webhook integration (for accurate low-credit alerting):
    1. In GCP Console → Billing → Budgets, set alerts at $1,800 (90%) and $1,900 (95%).
    2. Point the Pub/Sub topic at your webhook handler.
    3. The handler sets GOOGLE_BILLING_ALERT=1 in the environment.
    4. This endpoint reads that flag and sets credits_low=true.
    """
    import os
    from providers import google_stt, google_tts, google_translate, google_vision, vertex_embed
    from providers.gcp_counters import snapshot as _counters_snapshot
    from config import (
        GCP_CREDIT_GRANT_USD,
        GCP_CREDIT_WARN_REMAINING_USD,
        GOOGLE_BILLING_ALERT,
        GOOGLE_APPLICATION_CREDENTIALS_JSON,
    )

    counters = _counters_snapshot()
    svc_counters = counters["services"]

    configured_services: list[str] = []
    unconfigured_services: list[str] = []

    def _check(module, name: str) -> None:
        if module.is_configured():
            configured_services.append(name)
        else:
            unconfigured_services.append(name)

    _check(google_stt, "stt_chirp2")
    _check(google_tts, "tts_neural2")
    _check(google_translate, "translation_v3")
    _check(google_vision, "vision_ocr")
    _check(vertex_embed, "vertex_embed")

    has_gemini = bool(os.environ.get("GEMINI_API_KEY", "").strip())
    if has_gemini:
        configured_services.append("gemini_fallback")
    else:
        unconfigured_services.append("gemini_fallback")

    sa_configured = bool(
        GOOGLE_APPLICATION_CREDENTIALS_JSON
        and GOOGLE_APPLICATION_CREDENTIALS_JSON.startswith("{")
    )

    actual_spend_this_month = counters["total_estimated_spend_usd"]
    estimated_remaining = round(GCP_CREDIT_GRANT_USD - actual_spend_this_month, 4)
    months_runway = (
        round(estimated_remaining / actual_spend_this_month, 1)
        if actual_spend_this_month > 0
        else 9999.0
    )

    credits_low = GOOGLE_BILLING_ALERT or (estimated_remaining < GCP_CREDIT_WARN_REMAINING_USD)

    return {
        "provider": "google_cloud",
        "grant_usd": GCP_CREDIT_GRANT_USD,
        "spend_this_month_usd": actual_spend_this_month,
        "estimated_remaining_usd": estimated_remaining,
        "months_runway": months_runway,
        "credits_low": credits_low,
        "billing_alert_active": GOOGLE_BILLING_ALERT,
        "service_account_configured": sa_configured,
        "counters": {
            "period": counters["period"],
            "process_uptime_hours": counters["process_uptime_hours"],
            "counters_reset_on_restart": counters["counters_reset_on_restart"],
        },
        "services": {
            "configured": configured_services,
            "unconfigured": unconfigured_services,
        },
        "services_detail": {
            "stt_chirp2": {
                "model": "chirp_2",
                "languages": ["hi-IN", "bn-IN", "as-IN"],
                "pricing": "$0.016/min",
                "calls_this_month": svc_counters["stt"]["calls"],
                "audio_minutes_this_month": round(svc_counters["stt"].get("audio_minutes", 0.0), 2),
                "spend_this_month_usd": svc_counters["stt"]["estimated_spend_usd"],
                "configured": "stt_chirp2" in configured_services,
            },
            "tts_neural2": {
                "model": "Neural2 / Wavenet",
                "voices": ["hi-IN-Neural2-A", "hi-IN-Neural2-C", "bn-IN-Neural2-A", "as-IN-Wavenet-B"],
                "pricing": "$16/1M chars",
                "calls_this_month": svc_counters["tts"]["calls"],
                "chars_this_month": svc_counters["tts"].get("chars", 0),
                "spend_this_month_usd": svc_counters["tts"]["estimated_spend_usd"],
                "configured": "tts_neural2" in configured_services,
            },
            "translation_v3": {
                "model": "translateText v3",
                "languages": ["hi", "bn", "as"],
                "pricing": "$20/1M chars",
                "calls_this_month": svc_counters["translate"]["calls"],
                "chars_this_month": svc_counters["translate"].get("chars", 0),
                "spend_this_month_usd": svc_counters["translate"]["estimated_spend_usd"],
                "configured": "translation_v3" in configured_services,
            },
            "vision_ocr": {
                "model": "DOCUMENT_TEXT_DETECTION",
                "scripts": ["Devanagari", "Bengali"],
                "trigger": "indic_lang OR workers_ai_confidence < 0.80",
                "pricing": "$1.50/1K images",
                "calls_this_month": svc_counters["vision"]["calls"],
                "images_this_month": svc_counters["vision"].get("images", 0),
                "spend_this_month_usd": svc_counters["vision"]["estimated_spend_usd"],
                "configured": "vision_ocr" in configured_services,
            },
            "gemini_fallback": {
                "model": "gemini-2.0-flash",
                "role": "chat fallback position-2 (workers_ai → gemini → groq)",
                "trigger": "workers_ai load > 0.80",
                "pricing": "$0.075/1M tokens",
                "note": "Token counters not tracked in-process (Gemini billed via GCP Console)",
                "configured": has_gemini,
            },
            "vertex_embed": {
                "model": "text-embedding-004",
                "dimensions": 768,
                "role": "embed fallback for long-form > 2048 tokens or cooldown",
                "warning": "768-dim — do NOT mix with 1024-dim bge-large index",
                "pricing": "$0.00013/1K chars",
                "calls_this_month": svc_counters["embed"]["calls"],
                "chars_this_month": svc_counters["embed"].get("chars", 0),
                "spend_this_month_usd": svc_counters["embed"]["estimated_spend_usd"],
                "configured": "vertex_embed" in configured_services,
            },
        },
        "note": (
            "spend_this_month_usd is derived from in-process call counters (calls × unit price). "
            "Counters reset at month start and on process restart. "
            "For exact billing, check GCP Console → Billing → Cost breakdown. "
            "Budget alerts fire at $1,800 (90%) and $1,900 (95%)."
        ),
    }
