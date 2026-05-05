"""Syrabit.ai — Syllabus read + embedding admin routes"""
import logging
from fastapi import APIRouter, HTTPException, Depends, Query
from auth_deps import get_admin_user
from deps import db, is_mongo_available
# Task #383 — wire syllabus reads through the KvCache so a hot board/class
# pair only hits Mongo once per TTL. The cache is always-on (in-process
# LRU); the CF KV mirror activates when CF_EDGE_CACHE_ON + edge worker
# secret are configured.
from kv_cache import default_cache

logger = logging.getLogger(__name__)

# 5-minute TTL: syllabi are admin-edited a handful of times per week, so
# 5 minutes of staleness is acceptable in exchange for ~99% Mongo offload
# on the public read paths. Admin write endpoints invalidate explicitly.
_SYLLABUS_TTL_S = 5 * 60

def _get_syllabus_embedder():
    import server as _s
    return _s._syllabus_embedder

router = APIRouter()


def _syllabus_cache_key(board_id: str, class_id: str,
                        stream_id: str = "", subject_id: str = "") -> str:
    return f"syllabus/{board_id}/{class_id}/{stream_id or '_'}/{subject_id or '_'}"


@router.get("/syllabi/{board_id}/{class_id}")
async def get_syllabus(board_id: str, class_id: str):
    cache = default_cache()
    cache_key = _syllabus_cache_key(board_id, class_id)
    cached = cache.get_local(cache_key)
    if cached is not None:
        return cached
    try:
        if not await is_mongo_available():
            return {"board_id": board_id, "class_id": class_id, "content": "", "chapters": [], "topics": [], "found": False}
        syllabus = await db.syllabi.find_one({"board_id": board_id, "class_id": class_id, "stream_id": {"$exists": False}}, {"_id": 0})
        if not syllabus:
            syllabus = await db.syllabi.find_one({"board_id": board_id, "class_id": class_id}, {"_id": 0})
        if syllabus:
            await cache.set(cache_key, syllabus, ttl_s=_SYLLABUS_TTL_S)
            return syllabus
        miss_doc = {"board_id": board_id, "class_id": class_id, "content": "", "chapters": [], "topics": [], "found": False}
        # Cache misses too, but with a shorter TTL so a freshly-seeded
        # syllabus shows up within a minute instead of waiting 5.
        await cache.set(cache_key, miss_doc, ttl_s=60)
        return miss_doc
    except Exception as e:
        logger.error(f"Get syllabus error: {e}")
        return {"board_id": board_id, "class_id": class_id, "content": "", "chapters": [], "topics": [], "found": False}


@router.get("/syllabi/{board_id}/{class_id}/{stream_id}")
async def get_syllabus_stream(board_id: str, class_id: str, stream_id: str):
    try:
        if not await is_mongo_available():
            return {"board_id": board_id, "class_id": class_id, "stream_id": stream_id, "content": "", "chapters": [], "topics": [], "found": False}
        syllabus = await db.syllabi.find_one({"board_id": board_id, "class_id": class_id, "stream_id": stream_id, "subject_id": {"$exists": False}}, {"_id": 0})
        if not syllabus:
            syllabus = await db.syllabi.find_one({"board_id": board_id, "class_id": class_id, "stream_id": stream_id}, {"_id": 0})
        if not syllabus:
            syllabus = await db.syllabi.find_one({"board_id": board_id, "class_id": class_id}, {"_id": 0})
        if syllabus:
            return syllabus
        return {"board_id": board_id, "class_id": class_id, "stream_id": stream_id, "content": "", "chapters": [], "topics": [], "found": False}
    except Exception as e:
        logger.error(f"Get stream syllabus error: {e}")
        return {"board_id": board_id, "class_id": class_id, "stream_id": stream_id, "content": "", "chapters": [], "topics": [], "found": False}


@router.get("/syllabi/{board_id}/{class_id}/{stream_id}/{subject_id}")
async def get_syllabus_subject(board_id: str, class_id: str, stream_id: str, subject_id: str):
    try:
        if not await is_mongo_available():
            return {"board_id": board_id, "class_id": class_id, "stream_id": stream_id, "subject_id": subject_id, "content": "", "chapters": [], "topics": [], "found": False}
        syllabus = await db.syllabi.find_one({"board_id": board_id, "class_id": class_id, "stream_id": stream_id, "subject_id": subject_id}, {"_id": 0})
        if not syllabus:
            syllabus = await db.syllabi.find_one({"board_id": board_id, "class_id": class_id, "stream_id": stream_id}, {"_id": 0})
        if not syllabus:
            syllabus = await db.syllabi.find_one({"board_id": board_id, "class_id": class_id}, {"_id": 0})
        if syllabus:
            return syllabus
        return {"board_id": board_id, "class_id": class_id, "stream_id": stream_id, "subject_id": subject_id, "content": "", "chapters": [], "topics": [], "found": False}
    except Exception as e:
        logger.error(f"Get subject syllabus error: {e}")
        return {"board_id": board_id, "class_id": class_id, "stream_id": stream_id, "subject_id": subject_id, "content": "", "chapters": [], "topics": [], "found": False}


# ─────────────────────────────────────────────
# SYLLABUS EMBEDDER — admin endpoints
# ─────────────────────────────────────────────

@router.post("/admin/syllabus/seed-embeddings")
async def admin_seed_syllabus_embeddings(
    admin: dict = Depends(get_admin_user),
    full: bool = Query(False, description="If true, re-embeds everything from scratch"),
):
    """
    Force re-embed of all chapters + topics into Cloudflare Vectorize.
    Use ?full=true to rebuild from scratch.
    Without ?full, only new/missing chapters are embedded incrementally.
    """
    emb = _get_syllabus_embedder()
    if emb is None:
        raise HTTPException(status_code=503, detail="SyllabusEmbedder not initialised")
    # Task #383 — reseed touches every syllabus; drop the read-cache so
    # downstream public reads don't serve a stale chapter list.
    try:
        default_cache().reset()
    except Exception:
        pass
    if full:
        return await emb.full_reseed()
    return await emb.reseed()


@router.post("/admin/syllabus/full-reseed")
async def admin_full_reseed_embeddings(admin: dict = Depends(get_admin_user)):
    """Re-embed everything from scratch into Cloudflare Vectorize."""
    emb = _get_syllabus_embedder()
    if emb is None:
        raise HTTPException(status_code=503, detail="SyllabusEmbedder not initialised")
    return await emb.full_reseed()


@router.get("/admin/syllabus/embedding-stats")
async def admin_syllabus_embedding_stats(admin: dict = Depends(get_admin_user)):
    """Return Vectorize index stats: total vectors, dimensions, metric, thresholds.

    Also includes the Cloudflare-auth circuit-breaker status so admins can see
    when the embedder loop has been suspended due to repeated 401s.
    """
    emb = _get_syllabus_embedder()
    if emb is None:
        raise HTTPException(status_code=503, detail="SyllabusEmbedder not initialised")
    stats = await emb.stats()
    try:
        import vectorize_client
        stats["auth_breaker"] = vectorize_client.auth_breaker_status()
    except Exception:
        pass
    return stats


@router.get("/admin/syllabus/test-classify")
async def admin_test_classify(
    q: str = Query(..., description="Query to test against the embedding space"),
    top_n: int = Query(5, ge=1, le=20),
    admin: dict = Depends(get_admin_user),
):
    """
    Diagnostic endpoint: test a query against the Vectorize embedding space.
    Returns top-N matches with similarity scores, embed text previews,
    and whether each would pass the classification threshold.
    """
    emb = _get_syllabus_embedder()
    if emb is None:
        raise HTTPException(status_code=503, detail="SyllabusEmbedder not initialised")

    from syllabus_embedder import SIMILARITY_THRESHOLD

    results = await emb.classify_top_n(q, top_n=top_n)
    best_match = await emb.classify(q)

    return {
        "query": q,
        "threshold": SIMILARITY_THRESHOLD,
        "best_match": {
            "subject": best_match.subject_name,
            "chapter": best_match.chapter_title,
            "level": best_match.level,
            "topic": best_match.topic,
            "similarity": best_match.similarity,
        } if best_match else None,
        "top_n": results,
    }


# ─────────────────────────────────────────────
# AI CHAT ROUTES
# ─────────────────────────────────────────────

