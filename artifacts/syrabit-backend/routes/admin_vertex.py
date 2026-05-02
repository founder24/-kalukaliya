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
    """Google Cloud Platform credit burn panel row (Task #247).

    Returns a structured breakdown of the $2,000 GCP founders credit grant —
    which services are configured, estimated monthly burn per service, months
    of runway remaining, and a low-credit warning flag.

    NOTE ON SPEND ACCURACY:
    The Google Cloud Billing API (v1) requires a separate billing account ID
    and OAuth2 flow beyond the service account credentials used here. Real-time
    spend is therefore approximated from a fixed per-service burn model.
    For exact current-month spend, check: GCP Console → Billing → Cost breakdown.

    Budget webhook integration (for accurate low-credit alerting):
    1. In GCP Console → Billing → Budgets, set alerts at $1,800 (90%) and $1,900 (95%).
    2. Point the Pub/Sub topic at your webhook handler.
    3. The handler sets GOOGLE_BILLING_ALERT=1 in the environment.
    4. This endpoint reads that flag and sets credits_low=true.

    This endpoint is the canonical GCP row for the admin credit-usage panel.
    Future task #253 will wire it to the Cloud Billing API for live spend data.
    """
    from providers import google_stt, google_tts, google_translate, google_vision, vertex_embed
    from config import (
        GCP_CREDIT_GRANT_USD,
        GCP_CREDIT_WARN_REMAINING_USD,
        GOOGLE_BILLING_ALERT,
        GOOGLE_APPLICATION_CREDENTIALS_JSON,
    )

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

    has_gemini = bool(__import__("os").environ.get("GEMINI_API_KEY", "").strip())
    if has_gemini:
        configured_services.append("gemini_fallback")
    else:
        unconfigured_services.append("gemini_fallback")

    sa_configured = bool(
        GOOGLE_APPLICATION_CREDENTIALS_JSON
        and GOOGLE_APPLICATION_CREDENTIALS_JSON.startswith("{")
    )

    estimated_monthly_burn_usd = 19.0
    months_runway = GCP_CREDIT_GRANT_USD / estimated_monthly_burn_usd if estimated_monthly_burn_usd > 0 else 9999
    now_ts = time.time()
    current_month_days = 31
    fraction_of_month = (now_ts % (86400 * current_month_days)) / (86400 * current_month_days)
    estimated_spend_this_month = round(estimated_monthly_burn_usd * fraction_of_month, 2)
    estimated_remaining = round(GCP_CREDIT_GRANT_USD - estimated_spend_this_month, 2)

    credits_low = GOOGLE_BILLING_ALERT or (estimated_remaining < GCP_CREDIT_WARN_REMAINING_USD)

    return {
        "provider": "google_cloud",
        "grant_usd": GCP_CREDIT_GRANT_USD,
        "estimated_monthly_burn_usd": estimated_monthly_burn_usd,
        "estimated_spend_this_month_usd": estimated_spend_this_month,
        "estimated_remaining_usd": estimated_remaining,
        "months_runway": round(months_runway, 1),
        "credits_low": credits_low,
        "billing_alert_active": GOOGLE_BILLING_ALERT,
        "service_account_configured": sa_configured,
        "services": {
            "configured": configured_services,
            "unconfigured": unconfigured_services,
        },
        "services_detail": {
            "stt_chirp2": {
                "model": "chirp_2",
                "languages": ["hi-IN", "bn-IN", "as-IN"],
                "pricing": "$0.016/min",
                "monthly_est_usd": 4.0,
                "configured": "stt_chirp2" in configured_services,
            },
            "tts_neural2": {
                "model": "Neural2 / Wavenet",
                "voices": ["hi-IN-Neural2-A", "hi-IN-Neural2-C", "bn-IN-Neural2-A", "as-IN-Wavenet-B"],
                "pricing": "$16/1M chars",
                "monthly_est_usd": 3.0,
                "configured": "tts_neural2" in configured_services,
            },
            "translation_v3": {
                "model": "translateText v3",
                "languages": ["hi", "bn", "as"],
                "pricing": "$20/1M chars",
                "monthly_est_usd": 6.0,
                "configured": "translation_v3" in configured_services,
            },
            "vision_ocr": {
                "model": "DOCUMENT_TEXT_DETECTION",
                "scripts": ["Devanagari", "Bengali"],
                "trigger": "indic_lang OR workers_ai_confidence < 0.80",
                "pricing": "$1.50/1K images",
                "monthly_est_usd": 2.0,
                "configured": "vision_ocr" in configured_services,
            },
            "gemini_fallback": {
                "model": "gemini-2.0-flash",
                "role": "chat fallback position-2 (workers_ai → gemini → groq)",
                "trigger": "workers_ai load > 0.80",
                "pricing": "$0.075/1M tokens",
                "monthly_est_usd": 3.0,
                "configured": has_gemini,
            },
            "vertex_embed": {
                "model": "text-embedding-004",
                "dimensions": 768,
                "role": "embed fallback position-2 (long-form > 2048 tokens or cooldown)",
                "warning": "768-dim — do NOT mix with 1024-dim bge-large index",
                "pricing": "$0.00013/1K chars",
                "monthly_est_usd": 1.0,
                "configured": "vertex_embed" in configured_services,
            },
        },
        "note": (
            "Spend figures are estimates based on call volume approximations. "
            "Check GCP Console → Billing for exact current-month spend. "
            "Budget alerts fire at $1,800 (90%) and $1,900 (95%) to ops Slack."
        ),
    }
