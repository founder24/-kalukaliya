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
import asyncio
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
    """Google Cloud Platform credit burn panel row (Task #247 / #253 / #254).

    Live data is sourced from up to three GCP APIs (all optional, independent):

    1. Cloud Billing Budget API  — budget total + alert thresholds.
       Requires: GOOGLE_BILLING_ACCOUNT_ID + roles/billing.viewer on the account.
       Reflects in: live_budget_data=true, budget_warn_threshold_usd (auto-read).

    2. Cloud Billing API         — billing account status verification.
       Requires: same as above.
       Reflects in: billing_account_name, billing_account_open.

    3. BigQuery Billing Export   — real per-service month-to-date spend.
       Requires: GOOGLE_BILLING_ACCOUNT_ID + GCP Billing Export enabled to BigQuery
       (GCP Console → Billing → Billing export → BigQuery export → Enable).
       The service account needs roles/bigquery.jobUser + roles/bigquery.dataViewer.
       Reflects in: live_spend_data=true, services_detail[*].spend_mtd_usd (real).

    In-process call counters (Task #254) are always available: incremented on each
    successful GCP provider call, reset at month start and on process restart.
    See counters.counters_reset_on_restart.

    Budget webhook integration:
    1. In GCP Console → Billing → Budgets, set alerts at $1,800 (90%) and $1,900 (95%).
    2. Point the Pub/Sub topic at your webhook handler.
    3. The handler sets GOOGLE_BILLING_ALERT=1 in the environment.
    4. This endpoint reads that flag and sets credits_low=true.

    When live data is unavailable for a source the endpoint falls back gracefully:
    - Budget thresholds → hardcoded 90% / 95% of GCP_CREDIT_GRANT_USD.
    - MTD spend → proportional estimate based on $19/month model.
    - Per-service spend → proportional allocation from MTD total.
    """
    import calendar as _cal
    import datetime as _dt
    import os
    import gcp_billing
    from providers import google_stt, google_tts, google_translate, google_vision, vertex_embed
    from providers.gcp_counters import snapshot as _counters_snapshot
    from config import (
        GCP_CREDIT_GRANT_USD,
        GCP_CREDIT_WARN_REMAINING_USD,
        GOOGLE_BILLING_ALERT,
        GOOGLE_APPLICATION_CREDENTIALS_JSON,
        GOOGLE_BILLING_ACCOUNT_ID,
        GOOGLE_BILLING_BIGQUERY_PROJECT,
        GOOGLE_BILLING_BIGQUERY_DATASET,
        GOOGLE_BILLING_BIGQUERY_TABLE,
        GOOGLE_BILLING_BIGQUERY_LOCATION,
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

    _now = _dt.datetime.now(tz=_dt.timezone.utc)
    _days_in_month = 28 if _now.month == 2 else (30 if _now.month in {4, 6, 9, 11} else 31)
    _day_fraction = max(_now.day / _days_in_month, 1 / _days_in_month)
    counter_burn_rate = actual_spend_this_month / _day_fraction if actual_spend_this_month > 0 else 0.0

    _SERVICE_BURN_WEIGHTS: dict[str, float] = {
        "stt_chirp2":      4.0,
        "tts_neural2":     3.0,
        "translation_v3":  6.0,
        "vision_ocr":      2.0,
        "gemini_fallback": 3.0,
        "vertex_embed":    1.0,
    }
    _TOTAL_WEIGHT = sum(_SERVICE_BURN_WEIGHTS.values())
    _BASE_MONTHLY_BURN_USD = 19.0

    billing_summary, svc_spend = await asyncio.gather(
        gcp_billing.get_billing_summary(GOOGLE_BILLING_ACCOUNT_ID),
        gcp_billing.get_service_spend(
            GOOGLE_BILLING_BIGQUERY_PROJECT,
            GOOGLE_BILLING_BIGQUERY_DATASET,
            GOOGLE_BILLING_BIGQUERY_TABLE,
            location=GOOGLE_BILLING_BIGQUERY_LOCATION,
        ),
    )

    live_budget_data: bool = billing_summary["live_budget_data"]
    live_spend_data: bool = svc_spend["live_spend_data"]

    today = _dt.datetime.utcnow()
    days_in_month = _cal.monthrange(today.year, today.month)[1]
    fraction_elapsed = max(today.day / days_in_month, 0.001)

    if live_spend_data:
        total_spend_mtd: float = float(svc_spend["total_spend_usd"])
    elif live_budget_data and billing_summary.get("spend_mtd_usd_from_budget") is not None:
        total_spend_mtd = float(billing_summary["spend_mtd_usd_from_budget"])
    else:
        total_spend_mtd = round(_BASE_MONTHLY_BURN_USD * fraction_elapsed, 2)

    if live_budget_data and billing_summary.get("warn_threshold_usd") is not None:
        warn_threshold_usd: float = float(billing_summary["warn_threshold_usd"])
        crit_threshold_usd = billing_summary.get("critical_threshold_usd")
    else:
        warn_threshold_usd = GCP_CREDIT_GRANT_USD - GCP_CREDIT_WARN_REMAINING_USD
        crit_threshold_usd = GCP_CREDIT_GRANT_USD * 0.95

    grant_usd = billing_summary.get("budget_usd") or GCP_CREDIT_GRANT_USD
    estimated_remaining = round(grant_usd - total_spend_mtd, 2)

    monthly_run_rate = total_spend_mtd / fraction_elapsed if fraction_elapsed > 0 else _BASE_MONTHLY_BURN_USD
    months_runway = (estimated_remaining / monthly_run_rate) if monthly_run_rate > 0 else 9999.0

    credits_low = (
        GOOGLE_BILLING_ALERT
        or total_spend_mtd >= warn_threshold_usd
        or estimated_remaining < GCP_CREDIT_WARN_REMAINING_USD
    )

    def _svc_spend_mtd(service: str) -> tuple[float, bool]:
        """Return (spend_mtd_usd, is_live) for a service."""
        if live_spend_data:
            real = svc_spend["services"].get(service)
            if real is not None:
                return round(real, 4), True
        weight = _SERVICE_BURN_WEIGHTS.get(service, 0.0)
        est = round(total_spend_mtd * (weight / _TOTAL_WEIGHT), 4) if _TOTAL_WEIGHT else 0.0
        return est, False

    _stt_spend, _stt_live = _svc_spend_mtd("stt_chirp2")
    _tts_spend, _tts_live = _svc_spend_mtd("tts_neural2")
    _tr_spend, _tr_live = _svc_spend_mtd("translation_v3")
    _vis_spend, _vis_live = _svc_spend_mtd("vision_ocr")
    _gem_spend, _gem_live = _svc_spend_mtd("gemini_fallback")
    _vx_spend, _vx_live = _svc_spend_mtd("vertex_embed")

    if live_spend_data:
        spend_note = (
            "Month-to-date spend sourced from BigQuery Billing Export — real per-service "
            "figures from the GCP standard billing export table. "
            "Budget alert thresholds sourced from Cloud Billing Budget API. "
            "In-process counters (spend_this_month_usd) also available for real-time session view."
        )
    elif live_budget_data and billing_summary.get("spend_mtd_usd_from_budget") is not None:
        spend_note = (
            "Total MTD spend sourced from Cloud Billing Budget API (currentSpend field). "
            "Per-service breakdown is proportionally allocated from the total using "
            "historical burn-rate weights. Enable BigQuery Billing Export for exact "
            "per-service figures."
        )
    elif live_budget_data:
        spend_note = (
            "Budget thresholds sourced from Cloud Billing Budget API. "
            "MTD spend is a calendar-based estimate ($19/month baseline) — enable "
            "GCP Billing Export to BigQuery for real per-service spend figures."
        )
    else:
        spend_note = (
            "GOOGLE_BILLING_ACCOUNT_ID not set or Budget API unreachable — all figures "
            "are estimates based on a $19/month burn model. Set GOOGLE_BILLING_ACCOUNT_ID "
            "and grant roles/billing.viewer to enable live budget data. Enable GCP Billing "
            "Export to BigQuery for real per-service MTD spend."
        )

    return {
        "provider": "google_cloud",
        "grant_usd": grant_usd,
        "live_budget_data": live_budget_data,
        "live_spend_data": live_spend_data,
        "billing_account_id": GOOGLE_BILLING_ACCOUNT_ID or None,
        "billing_account_name": billing_summary.get("billing_account_name"),
        "billing_account_open": billing_summary.get("billing_account_open"),
        "billing_account_configured": billing_summary.get("billing_account_configured", False),
        "billing_api_error": billing_summary.get("error"),
        "spend_api_error": svc_spend.get("error") if not live_spend_data else None,
        "bq_configured": svc_spend.get("bq_configured", False),
        "bq_location": GOOGLE_BILLING_BIGQUERY_LOCATION,
        "spend_this_month_usd": actual_spend_this_month,
        "spend_mtd_usd": round(total_spend_mtd, 4),
        "spend_mtd_source": (
            "bigquery_billing_export" if live_spend_data
            else "budget_api_current_spend" if (live_budget_data and billing_summary.get("spend_mtd_usd_from_budget") is not None)
            else "estimated"
        ),
        "estimated_monthly_burn_usd": round(monthly_run_rate, 2),
        "estimated_remaining_usd": estimated_remaining,
        "months_runway": round(months_runway, 1),
        "budget_warn_threshold_usd": warn_threshold_usd,
        "budget_critical_threshold_usd": crit_threshold_usd,
        "credits_low": credits_low,
        "billing_alert_active": GOOGLE_BILLING_ALERT,
        "service_account_configured": sa_configured,
        "budgets": billing_summary.get("budgets", []),
        "counters": {
            "period": counters["period"],
            "process_uptime_hours": counters["process_uptime_hours"],
            "counters_reset_on_restart": counters["counters_reset_on_restart"],
            "counter_burn_rate_usd_per_month": round(counter_burn_rate, 2),
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
                "monthly_est_usd": _SERVICE_BURN_WEIGHTS["stt_chirp2"],
                "calls_this_month": svc_counters["stt"]["calls"],
                "audio_minutes_this_month": round(svc_counters["stt"].get("audio_minutes", 0.0), 2),
                "spend_this_month_usd": svc_counters["stt"]["estimated_spend_usd"],
                "spend_mtd_usd": _stt_spend,
                "spend_is_live": _stt_live,
                "configured": "stt_chirp2" in configured_services,
            },
            "tts_neural2": {
                "model": "Neural2 / Wavenet",
                "voices": ["hi-IN-Neural2-A", "hi-IN-Neural2-C", "bn-IN-Neural2-A", "as-IN-Wavenet-B"],
                "pricing": "$16/1M chars",
                "monthly_est_usd": _SERVICE_BURN_WEIGHTS["tts_neural2"],
                "calls_this_month": svc_counters["tts"]["calls"],
                "chars_this_month": svc_counters["tts"].get("chars", 0),
                "spend_this_month_usd": svc_counters["tts"]["estimated_spend_usd"],
                "spend_mtd_usd": _tts_spend,
                "spend_is_live": _tts_live,
                "configured": "tts_neural2" in configured_services,
            },
            "translation_v3": {
                "model": "translateText v3",
                "languages": ["hi", "bn", "as"],
                "pricing": "$20/1M chars",
                "monthly_est_usd": _SERVICE_BURN_WEIGHTS["translation_v3"],
                "calls_this_month": svc_counters["translate"]["calls"],
                "chars_this_month": svc_counters["translate"].get("chars", 0),
                "spend_this_month_usd": svc_counters["translate"]["estimated_spend_usd"],
                "spend_mtd_usd": _tr_spend,
                "spend_is_live": _tr_live,
                "configured": "translation_v3" in configured_services,
            },
            "vision_ocr": {
                "model": "DOCUMENT_TEXT_DETECTION",
                "scripts": ["Devanagari", "Bengali"],
                "trigger": "indic_lang OR workers_ai_confidence < 0.80",
                "pricing": "$1.50/1K images",
                "monthly_est_usd": _SERVICE_BURN_WEIGHTS["vision_ocr"],
                "calls_this_month": svc_counters["vision"]["calls"],
                "images_this_month": svc_counters["vision"].get("images", 0),
                "spend_this_month_usd": svc_counters["vision"]["estimated_spend_usd"],
                "spend_mtd_usd": _vis_spend,
                "spend_is_live": _vis_live,
                "configured": "vision_ocr" in configured_services,
            },
            "gemini_fallback": {
                "model": "gemini-2.0-flash",
                "role": "chat fallback position-2 (workers_ai → gemini → groq)",
                "trigger": "workers_ai load > 0.80",
                "pricing": "$0.075/1M tokens",
                "monthly_est_usd": _SERVICE_BURN_WEIGHTS["gemini_fallback"],
                "note": "Token counters not tracked in-process (Gemini billed via GCP Console)",
                "spend_mtd_usd": _gem_spend,
                "spend_is_live": _gem_live,
                "configured": has_gemini,
            },
            "vertex_embed": {
                "model": "text-embedding-004",
                "dimensions": 768,
                "role": "embed fallback for long-form > 2048 tokens or cooldown",
                "warning": "768-dim — do NOT mix with 1024-dim bge-large index",
                "pricing": "$0.00013/1K chars",
                "monthly_est_usd": _SERVICE_BURN_WEIGHTS["vertex_embed"],
                "calls_this_month": svc_counters["embed"]["calls"],
                "chars_this_month": svc_counters["embed"].get("chars", 0),
                "spend_this_month_usd": svc_counters["embed"]["estimated_spend_usd"],
                "spend_mtd_usd": _vx_spend,
                "spend_is_live": _vx_live,
                "configured": "vertex_embed" in configured_services,
            },
        },
        "note": spend_note,
    }
