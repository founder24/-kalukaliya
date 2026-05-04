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
import json
import os
import time
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Body, Depends, File, HTTPException, UploadFile

import vertex_services
from auth_deps import get_admin_user
from deps import db

router = APIRouter()


# ── Task #323 — Credit-applications tracker ────────────────────────────────
# Reads docs/infra/credit-applications.json (workspace root, three levels up
# from this file: routes/ → syrabit-backend/ → artifacts/ → workspace root)
# and exposes:
#   1. /admin/credit-applications  — full tracker payload for an admin tab
#   2. application_status field on each provider in /admin/vertex/provider-routing
#      so the existing routing card can render an "Application status" badge.
def _resolve_credit_apps_path() -> Path:
    """Locate ``credit-applications.json`` in a deployment-safe way.

    Resolution order (first existing wins):
      1. ``$CREDIT_APPLICATIONS_PATH`` env override — explicit absolute path
         for non-monorepo container layouts (e.g. ``/app/data/...``).
      2. Monorepo layout: ``<workspace>/docs/infra/credit-applications.json``
         (this file lives at ``<workspace>/artifacts/syrabit-backend/routes/``).
      3. Backend-colocated fallback: ``<backend>/docs/infra/credit-applications.json``
         so the file can be copied next to the app inside a container image.
      4. CWD fallback: ``./docs/infra/credit-applications.json``.

    Returns the *first existing* candidate, or the monorepo path as a last
    resort so error messages stay informative.
    """
    override = os.environ.get("CREDIT_APPLICATIONS_PATH", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    here = Path(__file__).resolve()
    candidates = [
        here.parent.parent.parent.parent / "docs" / "infra" / "credit-applications.json",
        here.parent.parent / "docs" / "infra" / "credit-applications.json",
        Path.cwd() / "docs" / "infra" / "credit-applications.json",
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


_CREDIT_APPS_PATH = _resolve_credit_apps_path()


@lru_cache(maxsize=1)
def _load_credit_applications_cached(mtime: float) -> dict:
    """File-mtime-keyed cache. Re-reads when the JSON file is updated."""
    try:
        return json.loads(_CREDIT_APPS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"applications": [], "status_legend": {}, "last_updated": None}
    except json.JSONDecodeError as exc:
        # Surface parse errors loudly — silent failure here would render an
        # empty admin badge with no diagnostic.
        return {
            "applications": [],
            "status_legend": {},
            "last_updated": None,
            "error": f"credit-applications.json parse error: {exc}",
        }


def _credit_applications() -> dict:
    try:
        mtime = _CREDIT_APPS_PATH.stat().st_mtime
    except FileNotFoundError:
        return _load_credit_applications_cached(0.0)
    return _load_credit_applications_cached(mtime)


def _application_status_by_provider() -> dict[str, dict]:
    """Aggregate the JSON's per-application rows into a single status per
    provider key (matching PROVIDER_PRIORITY names) for the badge.

    Multiple programmes may target the same provider (e.g. Deepgram instant
    signup + Deepgram Startup Program). The "best" status wins, in this
    rank order: approved > submitted > in_progress > ready > not_started >
    rejected > expired > disabled.
    """
    rank = {
        "approved": 0,
        "submitted": 1,
        "in_progress": 2,
        "ready": 3,
        "not_started": 4,
        "rejected": 5,
        "expired": 6,
        "disabled": 7,
    }
    by_provider: dict[str, dict] = {}
    for app in _credit_applications().get("applications", []):
        # Normalise the provider key (deepgram_startup → deepgram, etc.) so
        # multi-programme rows aggregate under the routing-pool name.
        raw = (app.get("provider") or "").strip()
        if not raw:
            continue
        norm = raw.split("_startup")[0]
        existing = by_provider.get(norm)
        candidate = {
            "status": app.get("status", "not_started"),
            "programme": app.get("programme"),
            "approved_usd": app.get("approved_usd"),
            "tier_usd": app.get("tier_usd"),
            "expires_on": app.get("expires_on"),
            "url": app.get("url"),
            "notes": app.get("notes"),
        }
        if existing is None or rank.get(candidate["status"], 99) < rank.get(existing["status"], 99):
            by_provider[norm] = candidate
    return by_provider


@router.get("/admin/credit-applications", summary="Pre-seed credit-applications tracker (Task #323)")
async def credit_applications(_admin: dict = Depends(get_admin_user)) -> dict:
    """Return the full credit-applications.json payload for the admin tracker UI."""
    return _credit_applications()


@router.get("/admin/vertex/health")
async def vertex_health(admin: dict = Depends(get_admin_user)):
    """Check status of all Vertex AI / Gemini services."""
    return await vertex_services.health_check()


@router.get("/admin/vertex/provider-routing")
async def vertex_provider_routing(admin: dict = Depends(get_admin_user)):
    """Surface the live AI-Studio routing matrix.

    Reads PROVIDER_PRIORITY + POOL_WEIGHTS + PROVIDER_CREDITS from
    config.py and the canonical model strings from llm._PROVIDER_DEFAULT_MODELS,
    then annotates every (feature, provider) pair with:

      * effective draw weight (POOL_WEIGHTS overrides PROVIDER_CREDITS)
      * role — primary / fallback / last_resort (derived from weight ratios:
        if max-weight provider dominates next-highest by >= 10x it's strict
        primary; weight-0 entries are last_resort; everything else fallback)
      * model — canonical model string used for that provider
      * enabled — whether the relevant env credential(s) are present

    The frontend uses this to render the "Provider Routing" tile in
    AI Studio so admins see the truth from config, not stale documentation.
    """
    import os
    from config import PROVIDER_PRIORITY, PROVIDER_CREDITS, POOL_WEIGHTS
    from llm import _PROVIDER_DEFAULT_MODELS

    # Ground-truth enablement per provider — prefer the provider module's own
    # ENABLED flag (which already encodes the real candidate-chain logic, e.g.
    # CF gateway BYOK vs direct key, key + endpoint combos) and fall back to
    # raw env-var presence for providers that don't ship a module.
    def _gemini_or_vertex_configured() -> bool:
        """True if a Vertex SA JSON exists. Vertex SA is the only Gemini auth
        path since the 2026-05-03 vertex-only migration — direct GEMINI_API_KEY
        is no longer consulted."""
        return bool(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON", "").strip())

    def _flag(modpath: str, attr: str = "ENABLED") -> bool:
        try:
            mod = __import__(modpath, fromlist=[attr])
            return bool(getattr(mod, attr, False))
        except Exception:
            return False

    PROVIDER_META = {
        "vertex":           {"label": "Vertex AI / Gemini (google-ai-studio)", "env": ["GOOGLE_APPLICATION_CREDENTIALS_JSON", "VERTEX_PROJECT_ID"],
                             "enabled": _gemini_or_vertex_configured()},
        "azure_openai":     {"label": "Azure OpenAI",                "env": ["AZURE_OPENAI_KEY_1", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_KEY_2", "AZURE_OPENAI_ENDPOINT"],
                             "enabled": _flag("providers.azure_openai")},
        # Task #347 — bedrock entry removed from the admin routing config
        # (provider decommissioned; providers/bedrock.py deleted).
        "sarvam":           {"label": "Sarvam (Indic LLM)",          "env": ["SARVAM_API_KEY", "SARVAM_API_KEY_2", "SARVAM_API_KEY_3"],
                             "enabled": any(os.environ.get(k, "").strip() for k in ("SARVAM_API_KEY", "SARVAM_API_KEY_2", "SARVAM_API_KEY_3"))},
        "elevenlabs":       {"label": "ElevenLabs",                  "env": ["ELEVENLABS_API_KEY"],
                             "enabled": _flag("providers.elevenlabs")},
        "assemblyai":       {"label": "AssemblyAI",                  "env": ["ASSEMBLYAI_API_KEY"],
                             "enabled": _flag("providers.assemblyai")},
        "deepgram":         {"label": "Deepgram",                    "env": ["DEEPGRAM_API_KEY"],
                             "enabled": _flag("providers.deepgram")},
        "cohere":           {"label": "Cohere Embeddings",           "env": ["COHERE_API_KEY"],
                             "enabled": _flag("providers.cohere")},
        "voyage_ai":        {"label": "Voyage AI",                   "env": ["VOYAGE_AI_API_KEY", "VOYAGE_API_KEY"],
                             "enabled": bool(os.environ.get("VOYAGE_AI_API_KEY", "").strip()
                                             or os.environ.get("VOYAGE_API_KEY", "").strip())},
        "pinecone_ai":      {"label": "Pinecone (Inference + Rerank)", "env": ["PINECONE_API_KEY"],
                             "enabled": _flag("providers.pinecone_ai")},
        "exa_ai":           {"label": "Exa Neural Search",           "env": ["EXA_API_KEY"],
                             "enabled": bool(os.environ.get("EXA_API_KEY", "").strip())},
        "tavily":           {"label": "Tavily Search",               "env": ["TAVILY_API_KEY"],
                             "enabled": bool(os.environ.get("TAVILY_API_KEY", "").strip())},
        "mongodb_atlas":    {"label": "MongoDB Atlas $vectorSearch", "env": ["MONGO_URL", "MONGODB_URI"],
                             "enabled": bool(os.environ.get("MONGO_URL", "").strip()
                                             or os.environ.get("MONGODB_URI", "").strip())},
        "workers_ai":       {"label": "Cloudflare Workers AI",       "env": ["CF_AI_GATEWAY_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "CF_AI_GATEWAY_TOKEN"],
                             "enabled": _flag("providers.cloudflare_ai", attr="_ENABLED")},
        "workers_ai_indic": {"label": "Cloudflare Workers AI · IndicTrans2", "env": ["CF_AI_GATEWAY_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "CF_AI_GATEWAY_TOKEN"],
                             "enabled": _flag("providers.workers_indic")},
    }

    FEATURE_LABELS = {
        "english_rag_chat":   ("English chat (RAG)",       "Conversational answers grounded in English notes/MCQs."),
        "assamese_rag_chat":  ("Assamese chat (RAG)",      "Native Assamese chat. Strict chain — no silent downgrade to English LLMs."),
        "content":            ("Long-form content",        "Notes, MCQ, admin pipelines — long generations with 1M-token context where possible."),
        "assamese_content":   ("Assamese content",         "Translation-first content adaptation (IndicTrans2 dominant)."),
        "tts":                ("Text-to-speech",           "Voice synthesis for SyraAssistant + audio notes."),
        "stt":                ("Speech-to-text",           "Streaming transcription for SyraAssistant."),
        "voice":              ("Real-time voice",          "Combined STT+TTS for live voice conversations."),
        "embed":              ("Text embeddings",          "RAG ingestion + query — multilingual embeddings."),
        "rerank":             ("Semantic reranking",       "Post-retrieval re-scoring of vector hits."),
        "vector_search":      ("Vector search",            "Curated chapter-level vector index lookups."),
        "translate":          ("Translation EN↔Indic",     "Strict chain: IndicTrans2 dominant, Gemini polish for edge cases."),
        "vision":             ("Vision / OCR",             "Image analysis, OCR, multimodal reasoning."),
        "safety":             ("Prompt safety",            "Content moderation / guardrails."),
        "search_rag":         ("RAG-grounded web search",  "Web answers with citations."),
        "live_search":        ("Live web search",          "Freshness-critical real-time web search."),
    }

    def _provider_enabled(name: str) -> bool:
        return bool(PROVIDER_META.get(name, {}).get("enabled", False))

    def _missing_env_keys(name: str) -> list[str]:
        """Return the env-key alias list ONLY when none are set — semantics
        are OR-chain: the provider needs *any one* of these keys, not all
        of them. Returns [] (no missing) once at least one alias resolves,
        so the UI can render the full alternative list as a guidance hint
        without misleading admins into thinking every key is required.
        """
        meta = PROVIDER_META.get(name, {})
        keys = meta.get("env", [])
        any_set = any(os.environ.get(k, "").strip() for k in keys)
        return [] if any_set else list(keys)

    # Task #323 — annotate each provider with the credit-application status
    # so the routing card can render an "Application status" badge alongside
    # the existing weight/credit/role columns.
    app_status = _application_status_by_provider()

    features_out = []
    for feature, providers in PROVIDER_PRIORITY.items():
        pool_override = POOL_WEIGHTS.get(feature, {})
        weighted = []
        for p in providers:
            w = pool_override.get(p, PROVIDER_CREDITS.get(p, 0))
            weighted.append((p, w))

        positive_weights = [w for _, w in weighted if w > 0]
        max_w = max(positive_weights) if positive_weights else 0
        # secondary = next-highest *positive* weight strictly less than max_w
        secondary = max((w for w in positive_weights if w < max_w), default=0)
        # Mirror llm.select_provider strict-lock: ONLY when exactly one
        # max-weight contender AND it dominates next-highest by >=10x (or
        # is the only positive-weight provider).  Multiple providers tied
        # at max => weighted rotation, NEVER strict primary.
        max_contenders = [p for p, w in weighted if w == max_w and w > 0]
        strict_lock = (
            len(max_contenders) == 1
            and bool(max_w)
            and (secondary == 0 or max_w >= 10 * secondary)
        )

        provider_rows = []
        for p, w in weighted:
            if w == 0:
                role = "last_resort"
            elif w == max_w and strict_lock:
                role = "primary"
            elif w == max_w:
                role = "rotation"   # equal-weight rotation across multiple providers
            else:
                role = "fallback"
            meta = PROVIDER_META.get(p, {})
            provider_rows.append({
                "name": p,
                "label": meta.get("label", p),
                "weight": w,
                "credits_usd": PROVIDER_CREDITS.get(p, 0),
                "model": _PROVIDER_DEFAULT_MODELS.get(p, ""),
                "role": role,
                "enabled": _provider_enabled(p),
                "env_keys": meta.get("env", []),
                "missing_env_keys": _missing_env_keys(p),
                # Task #323 — credit-application status sourced from
                # docs/infra/credit-applications.json. None when the
                # provider has no tracked application (e.g. workers_ai).
                "application_status": app_status.get(p),
            })

        label, description = FEATURE_LABELS.get(feature, (feature, ""))
        features_out.append({
            "key": feature,
            "label": label,
            "description": description,
            "strict_lock": strict_lock,
            "providers": provider_rows,
        })

    # Task #323 — surface tracker rows for providers that aren't in any
    # routing pool (Bedrock disabled, Cloudflare/MongoDB infra-only) so
    # the badge isn't silently dropped. Pool membership wins; only providers
    # never referenced by any feature land here.
    pool_providers = {p for providers in PROVIDER_PRIORITY.values() for p in providers}
    infra_credits = [
        {"provider": prov, **info}
        for prov, info in app_status.items()
        if prov not in pool_providers
    ]
    infra_credits.sort(key=lambda r: (r.get("status") != "approved", r["provider"]))

    return {
        "features": features_out,
        "infra_credits": infra_credits,
        "notes": {
            "strict_lock": (
                "When the primary's weight is ≥ 10× the next-highest, "
                "select_provider() short-circuits to that primary deterministically "
                "(no weighted draw). The fallback is only chosen when the primary "
                "is excluded — saturated, unhealthy, or already-failed-this-request."
            ),
            "rotation": "Equal-weight pools draw via random.choices(weights=...) for ~uniform rotation across healthy providers.",
            "last_resort": "Weight-0 providers never enter the rotation pool — they're only used after all weighted providers are exhausted.",
        },
    }


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

    # Gemini is reachable only through Vertex SA now (2026-05-03 vertex-only
    # migration); SA presence is reported via the `vertex_embed`/`vertex_chat`
    # cards above. The standalone `gemini_fallback` row was removed.

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
                "model": "gemini-2.5-flash",
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
