"""
Admin AI Endpoints
AI provider configuration, circuit breaker management, token usage, routing table.
Covers all 3 production AI providers: Sarvam AI, Vertex AI / Gemini, CF Workers AI.
"""

from fastapi import APIRouter, Depends
import logging

from app.api.v1.admin import require_admin_session, csrf_guard
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Admin AI"],
    dependencies=[Depends(require_admin_session), Depends(csrf_guard)],
)


def _cf_configured() -> bool:
    return bool(
        (settings.CF_ACCOUNT_ID or settings.CLOUDFLARE_ACCOUNT_ID)
        and (settings.CF_WORKER_AI_TOKEN or settings.CF_API_TOKEN)
    )


def _vertex_configured() -> bool:
    return bool(
        getattr(settings, "VERTEX_PROJECT_ID", None)
        or getattr(settings, "GOOGLE_APPLICATION_CREDENTIALS_JSON", None)
    )


@router.get("/ai/providers")
async def ai_providers():
    """AI provider config and health — all 3 production providers."""
    from app.core.circuit_breaker import sarvam_circuit_breaker

    cb_status = sarvam_circuit_breaker.get_status()

    # ── 1. Sarvam AI ────────────────────────────────────────────────────────
    sarvam = {
        "name": "sarvam_ai",
        "display_name": "Sarvam AI",
        "models": {
            "chat": settings.SARVAM_MODEL,
            "available": ["sarvam-30b", "sarvam-105b"],
        },
        "base_url": settings.SARVAM_BASE_URL,
        "configured": bool(settings.SARVAM_API_KEY),
        "status": "configured" if settings.SARVAM_API_KEY else "not_configured",
        "languages": ["en", "as", "hi", "bn", "ta", "te", "kn", "ml", "mr", "gu", "pa", "or"],
        "circuit_breaker": cb_status,
        "role": "primary — Assamese and all Indic-language responses",
    }

    # ── 2. Vertex AI / Gemini ────────────────────────────────────────────────
    vertex_model = getattr(settings, "VERTEX_GEMINI_MODEL", "gemini-2.5-flash")
    vertex_project = getattr(settings, "VERTEX_PROJECT_ID", None)
    vertex_location = getattr(settings, "VERTEX_LOCATION", "us-central1")
    vertex_ok = _vertex_configured()
    vertex = {
        "name": "vertex_ai",
        "display_name": "Vertex AI / Gemini",
        "models": {
            "chat": vertex_model,
            "available": ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-1.5-flash"],
        },
        "project_id": vertex_project,
        "location": vertex_location,
        "configured": vertex_ok,
        "status": "configured" if vertex_ok else "not_configured",
        "circuit_breaker": {"state": "unavailable"},
        "role": "secondary — English reasoning + search grounding",
    }

    # ── 3. Cloudflare Workers AI ─────────────────────────────────────────────
    cf_ok = _cf_configured()
    cf = {
        "name": "cf_workers_ai",
        "display_name": "Cloudflare Workers AI",
        "models": {
            "chat": settings.CF_AI_MODEL,
            "vision": settings.CF_AI_VISION_MODEL,
            "tts": settings.CF_AI_TTS_MODEL,
            "embedding": "@cf/baai/bge-m3",
        },
        "account_id": settings.CF_ACCOUNT_ID or settings.CLOUDFLARE_ACCOUNT_ID,
        "configured": cf_ok,
        "status": "configured" if cf_ok else "not_configured",
        "circuit_breaker": {"state": "unavailable"},
        "role": "embeddings (bge-m3 1024-dim) + TTS + vision",
    }

    overall = (
        "healthy"
        if sarvam["configured"]
        else ("degraded" if cf_ok or vertex_ok else "critical")
    )

    return {
        "overall_status": overall,
        "providers": [sarvam, vertex, cf],
    }


@router.get("/ai/circuit-breakers")
async def get_circuit_breakers():
    """State of all AI circuit breakers: CLOSED / OPEN / HALF_OPEN + failure count."""
    from app.core.circuit_breaker import sarvam_circuit_breaker

    sarvam_status = sarvam_circuit_breaker.get_status()

    return {
        "circuit_breakers": [
            {
                "provider": "sarvam_ai",
                "state": sarvam_status.get("state", "unknown"),
                "failure_count": sarvam_status.get("failure_count", 0),
                "last_failure_at": sarvam_status.get("last_failure_at"),
                "next_attempt_at": sarvam_status.get("next_attempt_at"),
            },
            {
                "provider": "vertex_ai",
                "state": "unavailable",
                "note": "No circuit breaker configured for Vertex AI",
            },
            {
                "provider": "cf_workers_ai",
                "state": "unavailable",
                "note": "No circuit breaker configured for CF Workers AI",
            },
        ]
    }


@router.post("/ai/reset-circuit")
async def reset_circuit_breakers():
    """Reset AI circuit breakers to CLOSED state."""
    from app.core.circuit_breaker import sarvam_circuit_breaker

    before = {"sarvam_ai": sarvam_circuit_breaker.get_status()["state"]}
    sarvam_circuit_breaker.reset()
    after = {"sarvam_ai": sarvam_circuit_breaker.get_status()["state"]}

    logger.info("Circuit breakers manually reset by admin")
    return {
        "status": "ok",
        "message": "Circuit breakers reset to CLOSED",
        "before": before,
        "after": after,
    }


@router.get("/ai/usage")
async def ai_usage():
    """Last 24h token counts per provider from ai_usage_logs collection."""
    try:
        from app.db.mongo import get_mongo_client
        from datetime import datetime, timezone, timedelta

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        since = datetime.now(timezone.utc) - timedelta(hours=24)
        pipeline = [
            {"$match": {"created_at": {"$gte": since}}},
            {
                "$group": {
                    "_id": "$provider",
                    "calls": {"$sum": 1},
                    "input_tokens": {"$sum": "$input_tokens"},
                    "output_tokens": {"$sum": "$output_tokens"},
                    "total_latency_ms": {"$sum": "$latency_ms"},
                    "avg_latency_ms": {"$avg": "$latency_ms"},
                }
            },
        ]
        _agg_cursor = await db.ai_usage_logs.aggregate(pipeline)
        rows = await _agg_cursor.to_list(length=10)

        if not rows:
            return {
                "source": "unavailable",
                "message": "ai_usage_logs collection is empty — usage will appear once chat requests are processed",
                "providers": [],
            }

        return {
            "source": "ai_usage_logs",
            "window_hours": 24,
            "providers": [
                {
                    "provider": r["_id"],
                    "calls": r["calls"],
                    "input_tokens": r["input_tokens"],
                    "output_tokens": r["output_tokens"],
                    "avg_latency_ms": round(r["avg_latency_ms"], 1),
                }
                for r in rows
            ],
        }
    except Exception as e:
        logger.error(f"AI usage query error: {e}")
        return {"source": "unavailable", "error": str(e), "providers": []}


@router.get("/ai/routing-pools")
async def ai_routing_pools():
    """Current language → model routing table."""
    return {
        "routing_table": [
            {
                "language": "as",
                "language_name": "Assamese",
                "primary_provider": "sarvam_ai",
                "primary_model": settings.SARVAM_MODEL,
                "fallback_provider": "sarvam_ai",
                "fallback_model": "sarvam-30b",
            },
            {
                "language": "en",
                "language_name": "English",
                "primary_provider": "sarvam_ai",
                "primary_model": settings.SARVAM_MODEL,
                "fallback_provider": "sarvam_ai",
                "fallback_model": "sarvam-30b",
            },
            {
                "language": "embedding",
                "language_name": "Embeddings (all languages)",
                "primary_provider": "cf_workers_ai",
                "primary_model": "@cf/baai/bge-m3",
            },
        ],
        "source": "config",
    }


@router.get("/ai/status")
async def ai_status():
    """Current AI system status (compact)."""
    sarvam_ok = bool(settings.SARVAM_API_KEY)
    cf_ok = _cf_configured()
    vertex_ok = _vertex_configured()

    overall = "healthy" if sarvam_ok else ("degraded" if cf_ok or vertex_ok else "critical")

    return {
        "overall_status": overall,
        "sarvam_ai": "ok" if sarvam_ok else "not_configured",
        "vertex_ai": "ok" if vertex_ok else "not_configured",
        "cf_workers_ai": "ok" if cf_ok else "not_configured",
        "active_chat_model": settings.SARVAM_MODEL,
        "active_embedding_model": "@cf/baai/bge-m3",
    }


@router.get("/ai/overview")
async def ai_overview():
    """AI system overview — alias for /ai/status with provider detail."""
    sarvam_ok = bool(settings.SARVAM_API_KEY)
    cf_ok = _cf_configured()
    vertex_ok = _vertex_configured()
    overall = "healthy" if sarvam_ok else ("degraded" if cf_ok or vertex_ok else "critical")
    return {
        "overall_status": overall,
        "sarvam_ai": "ok" if sarvam_ok else "not_configured",
        "vertex_ai": "ok" if vertex_ok else "not_configured",
        "cf_workers_ai": "ok" if cf_ok else "not_configured",
        "active_chat_model": settings.SARVAM_MODEL,
        "active_embedding_model": "@cf/baai/bge-m3",
        "providers_count": sum([sarvam_ok, cf_ok, vertex_ok]),
    }


@router.get("/intelligence/overview")
async def intelligence_overview():
    """
    AI intelligence metrics: conversation summary, content coverage, quiz stats.
    Aggregates from conversations and chapters collections.
    """
    from app.db.mongo import get_mongo_client
    try:
        db = get_mongo_client()[settings.MONGODB_DB_NAME]
        total_conversations = await db.conversations.count_documents({})
        active_7d_start = __import__("datetime").datetime.now(__import__("datetime").timezone.utc) - __import__("datetime").timedelta(days=7)
        active_7d = await db.conversations.count_documents({"updated_at": {"$gte": active_7d_start}})
        total_chapters = await db.chapters.count_documents({"status": "published"})
        chapters_with_notes = await db.chapter_notes.count_documents({})
        total_quizzes = await db.quiz_attempts.count_documents({}) if hasattr(db, "quiz_attempts") else 0
    except Exception:
        total_conversations = active_7d = total_chapters = chapters_with_notes = total_quizzes = 0

    return {
        "conversations": {
            "total": total_conversations,
            "active_last_7d": active_7d,
        },
        "content": {
            "published_chapters": total_chapters,
            "chapters_with_notes": chapters_with_notes,
            "coverage_pct": round(chapters_with_notes / max(total_chapters, 1) * 100, 1),
        },
        "quizzes": {
            "total_attempts": total_quizzes,
        },
        "models": {
            "chat": settings.SARVAM_MODEL,
            "embedding": settings.CF_AI_EMBED_MODEL,
        },
    }


@router.get("/health/llm-costs")
async def health_llm_costs(days: int = 7):
    """
    LLM cost estimate from the ai_usage_log collection.
    Aggregates token spend by provider over the last N days.
    """
    from app.db.mongo import get_mongo_client
    from datetime import datetime, timezone, timedelta
    try:
        db = get_mongo_client()[settings.MONGODB_DB_NAME]
        since = datetime.now(timezone.utc) - timedelta(days=days)
        pipeline = [
            {"$match": {"created_at": {"$gte": since}}},
            {
                "$group": {
                    "_id": "$provider",
                    "total_tokens": {"$sum": "$total_tokens"},
                    "prompt_tokens": {"$sum": "$prompt_tokens"},
                    "completion_tokens": {"$sum": "$completion_tokens"},
                    "calls": {"$sum": 1},
                }
            },
        ]
        rows = await (await db.ai_usage_log.aggregate(pipeline)).to_list(length=20)
        providers = {}
        for r in rows:
            providers[r["_id"] or "unknown"] = {
                "calls": r["calls"],
                "total_tokens": r["total_tokens"],
                "prompt_tokens": r["prompt_tokens"],
                "completion_tokens": r["completion_tokens"],
            }
        total_tokens = sum(p["total_tokens"] for p in providers.values())
        return {
            "days": days,
            "providers": providers,
            "total_tokens": total_tokens,
            "estimated_cost_usd": None,
            "source": "ai_usage_log",
        }
    except Exception as e:
        logger.error(f"LLM costs error: {e}")
        return {"days": days, "providers": {}, "total_tokens": 0, "source": "unavailable"}


@router.get("/ai/cache/stats")
async def ai_cache_stats():
    """
    AI response cache statistics — hit rate, backend config, circuit breaker state.
    Polled every 30 s by AdminHealth InfraTab.
    """
    from app.db.redis import get_redis

    managed: dict = {
        "backend": "redis",
        "namespace": "chat_cache",
        "ttl_seconds": 600,
        "max_entry_bytes": None,
        "breaker_open": False,
        "last_error": None,
        "hits": 0,
        "misses": 0,
        "errors": 0,
        "hit_rate": None,
        "bytes_stored": None,
        "entries_skipped_oversize": 0,
        "avg_saved_latency_ms": None,
        "estimated_total_saved_ms": None,
        "purge_count": 0,
    }
    l1: dict = {"size": 0, "maxsize": 0}

    try:
        redis = get_redis()
        keys = await redis.keys("chat_cache:*")
        managed["hits"] = len(keys)
        managed["hit_rate"] = None
        total_bytes = 0
        for k in keys[:50]:
            v = await redis.get(k)
            if v:
                total_bytes += len(v) if isinstance(v, (bytes, str)) else 0
        managed["bytes_stored"] = total_bytes
    except Exception as e:
        managed["breaker_open"] = True
        managed["last_error"] = str(e)[:120]

    return {"managed": managed, "l1": l1}


@router.post("/ai/cache/purge")
async def ai_cache_purge(pattern: str = "*"):
    """
    Purge AI response cache entries matching pattern.
    Returns {ok, deleted, l1_cleared} for the AdminHealth purge button.
    """
    from app.db.redis import get_redis

    deleted = 0
    try:
        redis = get_redis()
        prefix = "chat_cache:"
        safe_pattern = pattern.lstrip("*") or ""
        keys = await redis.keys(f"{prefix}{safe_pattern}*")
        if keys:
            deleted = await redis.delete(*keys)
    except Exception as e:
        logger.error(f"cache/purge error: {e}")
        return {"ok": False, "deleted": 0, "l1_cleared": 0, "error": str(e)[:120]}

    return {"ok": True, "deleted": deleted, "l1_cleared": 0}
