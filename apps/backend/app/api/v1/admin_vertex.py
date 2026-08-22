"""
Admin Vertex AI Endpoints
Content gap analysis and generation tools (flashcards, MCQ, NLP concepts,
quality scoring, SEO meta, topic suggestions, semantic search, translation,
OCR, provider routing) — now served by Cloudflare Workers AI.
"""

from fastapi import APIRouter, Depends, Request, HTTPException
import logging
from datetime import datetime, timezone

from app.api.v1.admin import require_admin_session, csrf_guard
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Admin Vertex"],
    dependencies=[Depends(require_admin_session), Depends(csrf_guard)],
)


def _workers_ai_ok() -> bool:
    return bool(
        getattr(settings, "EDGE_SHARED_SECRET", None)
        and (
            getattr(settings, "WORKERS_AI_INTERNAL_URL", None)
            or getattr(settings, "CF_WORKER_URL", None)
        )
    )


async def _workers_ai_generate(prompt: str, max_tokens: int = 1024) -> str:
    """Call Cloudflare Workers AI for admin generation tasks."""
    from app.services.ai.workers_ai_client import workers_ai_client
    try:
        return await workers_ai_client.generate(
            system_prompt="You are a helpful educational AI assistant for Assam board students.",
            user_message=prompt,
            max_tokens=max_tokens,
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Workers AI call failed: {e}")


@router.get("/vertex/health")
async def vertex_health():
    """Cloudflare Workers AI connectivity and configuration health check."""
    configured = _workers_ai_ok()
    if not configured:
        return {
            "ok": False,
            "configured": False,
            "provider": "cloudflare_workers_ai",
            "model": settings.CF_AI_MODEL,
            "message": "Set EDGE_SHARED_SECRET and WORKERS_AI_INTERNAL_URL (or CF_WORKER_URL) secrets.",
        }
    try:
        text = await _workers_ai_generate("Reply with exactly: ok", max_tokens=10)
        return {
            "ok": True,
            "configured": True,
            "provider": "cloudflare_workers_ai",
            "model": settings.CF_AI_MODEL,
            "ping_response": text.strip()[:50],
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        return {
            "ok": False,
            "configured": True,
            "provider": "cloudflare_workers_ai",
            "model": settings.CF_AI_MODEL,
            "error": str(e),
        }


@router.get("/vertex/provider-routing")
async def vertex_provider_routing():
    """Current AI provider routing table (which tasks go to which model)."""
    return {
        "routing": [
            {"task": "chat_english", "provider": "cloudflare_workers_ai", "model": settings.CF_AI_MODEL},
            {"task": "chat_assamese", "provider": "cloudflare_workers_ai", "model": settings.CF_AI_MODEL},
            {"task": "notes_generation", "provider": "cloudflare_workers_ai", "model": settings.CF_AI_MODEL},
            {"task": "tts", "provider": "cloudflare_workers_ai", "model": settings.CF_AI_TTS_MODEL},
            {"task": "translation", "provider": "cloudflare_workers_ai", "model": settings.CF_AI_MODEL},
            {"task": "embeddings", "provider": "cloudflare_workers_ai", "model": settings.CF_AI_EMBED_MODEL},
        ],
        "fallbacks": {
            "cloudflare_workers_ai": None,
        },
    }


@router.get("/vertex/content-gaps")
async def vertex_content_gaps(limit: int = 20):
    """
    Find published chapters that have no AI-generated notes or embeddings.
    Uses the chapters collection to identify content gaps.
    """
    try:
        from app.db.mongo import get_mongo_client
        db = get_mongo_client()[settings.MONGODB_DB_NAME]

        pipeline = [
            {"$match": {"status": "published"}},
            {
                "$lookup": {
                    "from": "chapter_notes",
                    "localField": "_id",
                    "foreignField": "chapter_id",
                    "as": "notes",
                }
            },
            {"$match": {"notes": {"$size": 0}}},
            {"$limit": limit},
            {"$project": {"title": 1, "slug": 1, "subject_id": 1, "board": 1, "status": 1}},
        ]
        gaps = await (await db.chapters.aggregate(pipeline)).to_list(length=limit)
        result = []
        for g in gaps:
            result.append({
                "id": str(g["_id"]),
                "title": g.get("title"),
                "slug": g.get("slug"),
                "board": g.get("board"),
            })
        return {"gaps": result, "total": len(result), "source": "chapters"}
    except Exception as e:
        logger.error(f"Content gaps error: {e}")
        return {"gaps": [], "total": 0, "source": "unavailable"}


@router.post("/vertex/flashcards")
async def vertex_flashcards(request: Request):
    """Generate flashcards for given content using Cloudflare Workers AI."""
    body = await request.json()
    content = body.get("content", "")
    count = min(int(body.get("count", 10)), 20)
    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    if not _workers_ai_ok():
        raise HTTPException(status_code=503, detail="Workers AI not configured")
    prompt = (
        f"Generate {count} flashcards (Q&A pairs) from this content. "
        f"Return JSON: {{\"flashcards\": [{{\"q\": \"...\", \"a\": \"...\"}}]}}\n\n{content[:3000]}"
    )
    raw = await _workers_ai_generate(prompt, max_tokens=2048)
    import json, re
    try:
        clean = re.sub(r"```json?\s*|\s*```", "", raw).strip()
        return json.loads(clean)
    except Exception:
        return {"flashcards": [], "raw": raw[:500]}


@router.post("/vertex/mcq-generator")
async def vertex_mcq_generator(request: Request):
    """Generate multiple-choice questions from content using Cloudflare Workers AI."""
    body = await request.json()
    content = body.get("content", "")
    count = min(int(body.get("count", 5)), 15)
    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    if not _workers_ai_ok():
        raise HTTPException(status_code=503, detail="Workers AI not configured")
    prompt = (
        f"Generate {count} MCQs from this content. "
        f"Return JSON: {{\"questions\": [{{\"q\": \"...\", \"options\": [\"A\",\"B\",\"C\",\"D\"], \"answer\": \"A\"}}]}}\n\n{content[:3000]}"
    )
    raw = await _workers_ai_generate(prompt, max_tokens=2048)
    import json, re
    try:
        clean = re.sub(r"```json?\s*|\s*```", "", raw).strip()
        return json.loads(clean)
    except Exception:
        return {"questions": [], "raw": raw[:500]}


@router.post("/vertex/nlp-concepts")
async def vertex_nlp_concepts(request: Request):
    """Extract key concepts and named entities from content."""
    body = await request.json()
    content = body.get("content", "")
    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    if not _workers_ai_ok():
        raise HTTPException(status_code=503, detail="Workers AI not configured")
    prompt = (
        "Extract key concepts, named entities, and important terms from this text. "
        "Return JSON: {\"concepts\": [{\"term\": \"...\", \"type\": \"concept|entity|term\", \"importance\": 0.9}]}\n\n"
        + content[:3000]
    )
    raw = await _workers_ai_generate(prompt, max_tokens=1024)
    import json, re
    try:
        clean = re.sub(r"```json?\s*|\s*```", "", raw).strip()
        return json.loads(clean)
    except Exception:
        return {"concepts": [], "raw": raw[:500]}


@router.post("/vertex/ocr")
async def vertex_ocr(request: Request):
    """OCR endpoint stub — returns not-implemented until vision pipeline is wired."""
    return {
        "ok": False,
        "message": "OCR is not yet implemented. Upload the image to the RAG pipeline instead.",
    }


@router.post("/vertex/quality-score")
async def vertex_quality_score(request: Request):
    """Score the educational quality of content (0–100) using Cloudflare Workers AI."""
    body = await request.json()
    content = body.get("content", "")
    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    if not _workers_ai_ok():
        raise HTTPException(status_code=503, detail="Workers AI not configured")
    prompt = (
        "Rate the educational quality of this content on a scale of 0-100 for Assam board HS students. "
        "Return JSON: {\"score\": 85, \"clarity\": 90, \"accuracy\": 80, \"completeness\": 85, \"feedback\": \"...\"}\n\n"
        + content[:3000]
    )
    raw = await _workers_ai_generate(prompt, max_tokens=512)
    import json, re
    try:
        clean = re.sub(r"```json?\s*|\s*```", "", raw).strip()
        return json.loads(clean)
    except Exception:
        return {"score": None, "raw": raw[:300]}


@router.post("/vertex/semantic-search")
async def vertex_semantic_search(request: Request):
    """Semantic search over content using embeddings — proxies to RAG search."""
    body = await request.json()
    query = body.get("query", "")
    limit = min(int(body.get("limit", 5)), 20)
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    try:
        from app.db.mongo import get_mongo_client
        from app.services.ai.embedder import get_embedding
        db = get_mongo_client()[settings.MONGODB_DB_NAME]
        embedding = await get_embedding(query)
        pipeline = [
            {"$vectorSearch": {
                "index": "vector_index",
                "path": "embedding",
                "queryVector": embedding,
                "numCandidates": limit * 10,
                "limit": limit,
            }},
            {"$project": {"content": 1, "metadata": 1, "score": {"$meta": "vectorSearchScore"}}},
        ]
        results = await (await db.rag_chunks.aggregate(pipeline)).to_list(length=limit)
        return {"results": [
            {"content": r.get("content", "")[:300], "metadata": r.get("metadata", {}), "score": r.get("score")}
            for r in results
        ]}
    except Exception as e:
        logger.error(f"Semantic search error: {e}")
        return {"results": [], "error": str(e)}


@router.post("/vertex/seo-meta")
async def vertex_seo_meta(request: Request):
    """Generate SEO title + description for a chapter using Cloudflare Workers AI."""
    body = await request.json()
    title = body.get("title", "")
    content = body.get("content", "")[:1500]
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    if not _workers_ai_ok():
        raise HTTPException(status_code=503, detail="Workers AI not configured")
    prompt = (
        f"Generate an SEO-optimized title and meta description for this Assam board chapter. "
        f"Title: {title}\nContent preview: {content}\n"
        f"Return JSON: {{\"seo_title\": \"...\", \"meta_description\": \"...\"}}"
    )
    raw = await _workers_ai_generate(prompt, max_tokens=256)
    import json, re
    try:
        clean = re.sub(r"```json?\s*|\s*```", "", raw).strip()
        return json.loads(clean)
    except Exception:
        return {"seo_title": None, "meta_description": None, "raw": raw[:300]}


@router.post("/vertex/suggest-topics")
async def vertex_suggest_topics(request: Request):
    """Suggest related topics/subtopics for a chapter using Cloudflare Workers AI."""
    body = await request.json()
    chapter_title = body.get("chapter_title", "")
    existing_topics = body.get("existing_topics", [])
    if not chapter_title:
        raise HTTPException(status_code=400, detail="chapter_title is required")
    if not _workers_ai_ok():
        raise HTTPException(status_code=503, detail="Workers AI not configured")
    existing_str = ", ".join(existing_topics) if existing_topics else "none"
    prompt = (
        f"Suggest 5-8 important topics/subtopics for the chapter: '{chapter_title}' "
        f"(Assam board). Already covered: {existing_str}. "
        f"Return JSON: {{\"topics\": [\"...\", \"...\"]}}"
    )
    raw = await _workers_ai_generate(prompt, max_tokens=512)
    import json, re
    try:
        clean = re.sub(r"```json?\s*|\s*```", "", raw).strip()
        return json.loads(clean)
    except Exception:
        return {"topics": [], "raw": raw[:300]}


@router.post("/vertex/translate")
async def vertex_translate(request: Request):
    """Translate content to Assamese using Cloudflare Workers AI."""
    body = await request.json()
    text = body.get("text", "")
    target_lang = body.get("target_lang", "as")
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    if not _workers_ai_ok():
        raise HTTPException(status_code=503, detail="Workers AI not configured")
    lang_names = {"as": "Assamese", "hi": "Hindi", "bn": "Bengali"}
    target_name = lang_names.get(target_lang, target_lang)
    prompt = (
        f"Translate the following English text into {target_name}. "
        f"Return only the translated text, no explanations.\n\n{text[:5000]}"
    )
    try:
        translated = await _workers_ai_generate(prompt, max_tokens=2048)
        return {"translated_text": translated.strip(), "source_lang": "en", "target_lang": target_lang}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/vertex/probe-status")
async def vertex_probe_status():
    """
    Light probe of Workers AI availability.
    Returns status per provider without making expensive inference calls.
    """
    from app.config import settings as cfg
    results = {}

    cf_token = getattr(cfg, "CF_WORKER_AI_TOKEN", None) or getattr(cfg, "CF_API_TOKEN", None) or ""
    edge_secret = getattr(cfg, "EDGE_SHARED_SECRET", None) or ""
    worker_url = getattr(cfg, "WORKERS_AI_INTERNAL_URL", None) or getattr(cfg, "CF_WORKER_URL", None) or ""

    results["cloudflare_workers_ai"] = {
        "configured": bool(cf_token or (edge_secret and worker_url)),
        "status": "configured" if (cf_token or (edge_secret and worker_url)) else "missing_key",
        "model": getattr(cfg, "CF_AI_MODEL", None),
    }

    return {
        "probed_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(),
        "providers": results,
    }
