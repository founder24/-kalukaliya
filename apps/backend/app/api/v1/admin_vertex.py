"""
Admin Vertex AI Endpoints
Vertex AI / Gemini admin panel: health check, content gap analysis, and
generation tools (flashcards, MCQ, NLP concepts, quality scoring, SEO meta,
topic suggestions, semantic search, translation, OCR, provider routing).
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


def _vertex_ok() -> bool:
    return bool(
        getattr(settings, "VERTEX_PROJECT_ID", None)
        and getattr(settings, "GOOGLE_APPLICATION_CREDENTIALS_JSON", None)
    )


async def _gemini_generate(prompt: str, max_tokens: int = 1024) -> str:
    """Call Gemini 2.5 Flash for admin generation tasks."""
    import asyncio, json, os, tempfile
    try:
        from google import genai as google_genai
        from google.genai.types import GenerateContentConfig

        creds_json = getattr(settings, "GOOGLE_APPLICATION_CREDENTIALS_JSON", "")
        creds_data = json.loads(creds_json)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
            json.dump(creds_data, tf)
            creds_path = tf.name
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = creds_path

        client = google_genai.Client(
            vertexai=True,
            project=settings.VERTEX_PROJECT_ID,
            location=getattr(settings, "VERTEX_LOCATION", "us-central1"),
        )
        resp = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-2.5-flash",
            contents=[{"role": "user", "parts": [{"text": prompt}]}],
            config=GenerateContentConfig(
                temperature=0.3,
                max_output_tokens=max_tokens,
                thinking_config={"thinking_budget": 0},
            ),
        )
        return resp.text or ""
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Vertex AI call failed: {e}")


@router.get("/vertex/health")
async def vertex_health():
    """Vertex AI / Gemini connectivity and configuration health check."""
    configured = _vertex_ok()
    if not configured:
        return {
            "ok": False,
            "configured": False,
            "project": None,
            "location": None,
            "model": "gemini-2.5-flash",
            "message": "Set VERTEX_PROJECT_ID and GOOGLE_APPLICATION_CREDENTIALS_JSON secrets.",
        }
    try:
        import asyncio
        text = await _gemini_generate("Reply with exactly: ok", max_tokens=10)
        return {
            "ok": True,
            "configured": True,
            "project": settings.VERTEX_PROJECT_ID,
            "location": getattr(settings, "VERTEX_LOCATION", "us-central1"),
            "model": "gemini-2.5-flash",
            "ping_response": text.strip()[:50],
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        return {
            "ok": False,
            "configured": True,
            "project": settings.VERTEX_PROJECT_ID,
            "model": "gemini-2.5-flash",
            "error": str(e),
        }


@router.get("/vertex/provider-routing")
async def vertex_provider_routing():
    """Current AI provider routing table (which tasks go to which model)."""
    return {
        "routing": [
            {"task": "chat_english", "provider": "sarvam_ai", "model": settings.SARVAM_MODEL},
            {"task": "chat_assamese", "provider": "sarvam_ai", "model": settings.SARVAM_MODEL},
            {"task": "notes_generation", "provider": "vertex_ai", "model": "gemini-2.5-flash"},
            {"task": "tts", "provider": "cloudflare_workers_ai", "model": settings.CF_AI_TTS_MODEL},
            {"task": "translation", "provider": "sarvam_ai", "model": "sarvam-translate"},
            {"task": "embeddings", "provider": "cloudflare_workers_ai", "model": settings.CF_AI_EMBED_MODEL},
        ],
        "fallbacks": {
            "sarvam_ai": "vertex_ai",
            "vertex_ai": None,
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
    """Generate flashcards for given content using Gemini."""
    body = await request.json()
    content = body.get("content", "")
    count = min(int(body.get("count", 10)), 20)
    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    if not _vertex_ok():
        raise HTTPException(status_code=503, detail="Vertex AI not configured")
    prompt = (
        f"Generate {count} flashcards (Q&A pairs) from this content. "
        f"Return JSON: {{\"flashcards\": [{{\"q\": \"...\", \"a\": \"...\"}}]}}\n\n{content[:3000]}"
    )
    raw = await _gemini_generate(prompt, max_tokens=2048)
    import json, re
    try:
        clean = re.sub(r"```json?\s*|\s*```", "", raw).strip()
        return json.loads(clean)
    except Exception:
        return {"flashcards": [], "raw": raw[:500]}


@router.post("/vertex/mcq-generator")
async def vertex_mcq_generator(request: Request):
    """Generate multiple-choice questions from content using Gemini."""
    body = await request.json()
    content = body.get("content", "")
    count = min(int(body.get("count", 5)), 15)
    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    if not _vertex_ok():
        raise HTTPException(status_code=503, detail="Vertex AI not configured")
    prompt = (
        f"Generate {count} MCQs from this content. "
        f"Return JSON: {{\"questions\": [{{\"q\": \"...\", \"options\": [\"A\",\"B\",\"C\",\"D\"], \"answer\": \"A\"}}]}}\n\n{content[:3000]}"
    )
    raw = await _gemini_generate(prompt, max_tokens=2048)
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
    if not _vertex_ok():
        raise HTTPException(status_code=503, detail="Vertex AI not configured")
    prompt = (
        "Extract key concepts, named entities, and important terms from this text. "
        "Return JSON: {\"concepts\": [{\"term\": \"...\", \"type\": \"concept|entity|term\", \"importance\": 0.9}]}\n\n"
        + content[:3000]
    )
    raw = await _gemini_generate(prompt, max_tokens=1024)
    import json, re
    try:
        clean = re.sub(r"```json?\s*|\s*```", "", raw).strip()
        return json.loads(clean)
    except Exception:
        return {"concepts": [], "raw": raw[:500]}


@router.post("/vertex/ocr")
async def vertex_ocr(request: Request):
    """OCR endpoint stub — returns not-implemented until Vertex Vision is wired."""
    return {
        "ok": False,
        "message": "OCR via Vertex Vision API is not yet implemented. Upload the image to the RAG pipeline instead.",
    }


@router.post("/vertex/quality-score")
async def vertex_quality_score(request: Request):
    """Score the educational quality of content (0–100) using Gemini."""
    body = await request.json()
    content = body.get("content", "")
    if not content:
        raise HTTPException(status_code=400, detail="content is required")
    if not _vertex_ok():
        raise HTTPException(status_code=503, detail="Vertex AI not configured")
    prompt = (
        "Rate the educational quality of this content on a scale of 0-100 for Assam board HS students. "
        "Return JSON: {\"score\": 85, \"clarity\": 90, \"accuracy\": 80, \"completeness\": 85, \"feedback\": \"...\"}\n\n"
        + content[:3000]
    )
    raw = await _gemini_generate(prompt, max_tokens=512)
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
    """Generate SEO title + description for a chapter using Gemini."""
    body = await request.json()
    title = body.get("title", "")
    content = body.get("content", "")[:1500]
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    if not _vertex_ok():
        raise HTTPException(status_code=503, detail="Vertex AI not configured")
    prompt = (
        f"Generate an SEO-optimized title and meta description for this Assam board chapter. "
        f"Title: {title}\nContent preview: {content}\n"
        f"Return JSON: {{\"seo_title\": \"...\", \"meta_description\": \"...\"}}"
    )
    raw = await _gemini_generate(prompt, max_tokens=256)
    import json, re
    try:
        clean = re.sub(r"```json?\s*|\s*```", "", raw).strip()
        return json.loads(clean)
    except Exception:
        return {"seo_title": None, "meta_description": None, "raw": raw[:300]}


@router.post("/vertex/suggest-topics")
async def vertex_suggest_topics(request: Request):
    """Suggest related topics/subtopics for a chapter using Gemini."""
    body = await request.json()
    chapter_title = body.get("chapter_title", "")
    existing_topics = body.get("existing_topics", [])
    if not chapter_title:
        raise HTTPException(status_code=400, detail="chapter_title is required")
    if not _vertex_ok():
        raise HTTPException(status_code=503, detail="Vertex AI not configured")
    existing_str = ", ".join(existing_topics) if existing_topics else "none"
    prompt = (
        f"Suggest 5-8 important topics/subtopics for the chapter: '{chapter_title}' "
        f"(Assam board). Already covered: {existing_str}. "
        f"Return JSON: {{\"topics\": [\"...\", \"...\"]}}"
    )
    raw = await _gemini_generate(prompt, max_tokens=512)
    import json, re
    try:
        clean = re.sub(r"```json?\s*|\s*```", "", raw).strip()
        return json.loads(clean)
    except Exception:
        return {"topics": [], "raw": raw[:300]}


@router.post("/vertex/translate")
async def vertex_translate(request: Request):
    """Translate content to Assamese using Sarvam AI (proxies to the translation service)."""
    body = await request.json()
    text = body.get("text", "")
    target_lang = body.get("target_lang", "as")
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    try:
        import httpx
        sarvam_key = settings.SARVAM_API_KEY
        if not sarvam_key:
            raise HTTPException(status_code=503, detail="SARVAM_API_KEY not configured")
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.sarvam.ai/translate",
                headers={"api-subscription-key": sarvam_key, "Content-Type": "application/json"},
                json={"input": text[:5000], "source_language_code": "en-IN", "target_language_code": f"{target_lang}-IN"},
            )
            if resp.is_success:
                data = resp.json()
                return {"translated_text": data.get("translated_text", ""), "source_lang": "en", "target_lang": target_lang}
            raise HTTPException(status_code=502, detail=f"Sarvam translate error: {resp.text[:200]}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
