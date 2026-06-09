"""
Health Check Endpoints: Basic and Deep Dependency Checks
"""

import asyncio
import os
import time

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Health"])


async def _safe_check(coro, timeout: float = 5.0) -> Dict[str, Any]:
    """Run a health check coroutine with a timeout."""
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError:
        return {"status": "unhealthy", "error": "timeout"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}


async def mongo_ping() -> Dict[str, Any]:
    """Ping MongoDB connection and verify Beanie ODM is initialized"""
    try:
        from app.db.mongo import get_mongo_client

        client = get_mongo_client()
        t0 = time.monotonic()
        await client.admin.command("ping")
        latency_ms = round((time.monotonic() - t0) * 1000, 1)

        # Verify Beanie ODM is initialized
        from app.models.content import Board

        Board.get_pymongo_collection()

        return {"status": "healthy", "latency_ms": latency_ms}
    except RuntimeError as e:
        logger.warning(f"MongoDB not initialized: {str(e)}")
        return {"status": "unhealthy", "error": str(e)}
    except Exception as e:
        logger.warning(f"MongoDB ping failed: {str(e)}")
        return {"status": "unhealthy", "error": str(e)}


async def redis_ping() -> Dict[str, Any]:
    """Ping Upstash Redis connection"""
    try:
        from app.db.redis import get_redis

        redis = get_redis()
        t0 = time.monotonic()
        result = await redis.ping()
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        if result:
            return {"status": "healthy", "latency_ms": latency_ms}
        else:
            return {"status": "unhealthy", "error": "Ping returned false"}
    except Exception as e:
        logger.warning(f"Redis ping failed: {str(e)}")
        return {"status": "unhealthy", "error": str(e)}


async def mongo_vector_search_ping() -> Dict[str, Any]:
    """Ping MongoDB vector search (topic embeddings cache)."""
    try:
        from app.services.ai.topic_matcher import topic_matcher

        if not topic_matcher._is_cache_valid():
            await topic_matcher._load_embeddings()

        count = len(topic_matcher._embeddings or [])
        if count == 0:
            return {"status": "degraded", "error": "No topic embeddings loaded"}
        return {"status": "healthy", "topic_count": count}
    except Exception as e:
        logger.warning(f"MongoDB vector search ping failed: {str(e)}")
        return {"status": "unhealthy", "error": str(e)}


async def vertex_ping() -> Dict[str, Any]:
    """Ping Vertex AI (lightweight check)"""
    try:
        from app.config import settings

        project_id = settings.VERTEX_PROJECT_ID
        # Treat placeholder/unconfigured values as missing
        _placeholder = {"not-configured", "not_configured", "", None}
        if project_id in _placeholder:
            return {"status": "degraded", "error": "VERTEX_PROJECT_ID not configured"}

        if settings.GOOGLE_APPLICATION_CREDENTIALS_JSON:
            return {"status": "healthy", "project_id": project_id}
        elif os.environ.get("K_SERVICE"):
            # Running on Cloud Run with Workload Identity - ADC is available
            return {
                "status": "healthy",
                "project_id": project_id,
                "auth": "workload_identity",
            }
        else:
            return {"status": "degraded", "error": "No credentials (SA key or Workload Identity)"}
    except Exception as e:
        logger.warning(f"Vertex AI check failed: {str(e)}")
        return {"status": "unhealthy", "error": str(e)}


async def sarvam_ping() -> Dict[str, Any]:
    """Check Sarvam AI configuration and lightweight endpoint reachability"""
    try:
        from app.config import settings

        if not settings.SARVAM_API_KEY:
            return {"status": "degraded", "error": "SARVAM_API_KEY not configured"}

        import httpx

        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                settings.SARVAM_BASE_URL,
                headers={"API-Subscription-Key": settings.SARVAM_API_KEY},
            )
        latency_ms = round((time.monotonic() - t0) * 1000, 1)

        # Any non-connection-error response means the endpoint is reachable
        if resp.status_code < 500:
            return {"status": "healthy", "latency_ms": latency_ms, "http": resp.status_code}
        return {"status": "unhealthy", "error": f"HTTP {resp.status_code}", "latency_ms": latency_ms}
    except Exception as e:
        logger.warning(f"Sarvam ping failed: {str(e)}")
        return {"status": "degraded", "error": str(e)[:120]}


@router.get("")
async def basic_health_check():
    """
    Basic health check - returns 200 if app is running.
    Does not check dependencies.
    Returns 503 if there are startup configuration errors.
    """
    from app.config import settings

    warnings = []

    # Issue 12: Detect JWT algorithm mismatch
    if settings.JWT_ALGORITHM == "RS256":
        if not getattr(settings, "JWT_PRIVATE_KEY", None):
            warnings.append("JWT_ALGORITHM is RS256 but JWT_PRIVATE_KEY is not set")
        if not getattr(settings, "JWT_PUBLIC_KEY", None):
            warnings.append("JWT_ALGORITHM is RS256 but JWT_PUBLIC_KEY is not set")

    # Check MongoDB initialization state (fast — no I/O, just reads module-level flag)
    try:
        from app.db.mongo import get_mongo_client
        get_mongo_client()
        mongodb_ok = True
    except RuntimeError:
        mongodb_ok = False

    if settings.startup_errors or not mongodb_ok:
        errors = list(settings.startup_errors)
        if not mongodb_ok and not any("MongoDB" in e for e in errors):
            errors.append(
                "MongoDB not initialized — check Atlas connectivity/IP allowlist and MONGODB_URI secret. "
                "Hit /api/v1/health/deep for full dependency status."
            )
        return JSONResponse(
            status_code=503,
            content={
                "status": "degraded",
                "service": "syrabit-backend",
                "mongodb_initialized": mongodb_ok,
                "error_count": len(errors),
                # Do NOT expose error details publicly — they can contain
                # infrastructure hints (URI patterns, IP allowlist messages).
                # Full details are available at /api/v1/health/deep (admin only).
                "hint": "Check /api/v1/health/deep for detailed diagnostics.",
                "warnings": warnings,
            },
        )

    response = {
        "status": "healthy",
        "service": "syrabit-backend",
        "mongodb_initialized": True,
        "timestamp": __import__("datetime").datetime.utcnow().isoformat() + "Z",
    }
    if warnings:
        response["warnings"] = warnings
    return response


@router.get("/deep")
async def deep_health_check():
    """
    Deep health check - verifies all critical dependencies.
    Returns 503 if any dependency is unhealthy.

    Checks:
    - MongoDB connection
    - Redis connection
    - Vertex AI Search service
    - Vertex AI configuration
    """
    results = await asyncio.gather(
        _safe_check(mongo_ping()),
        _safe_check(redis_ping()),
        _safe_check(mongo_vector_search_ping()),
        _safe_check(sarvam_ping()),
    )
    checks = {
        "mongodb": results[0],
        "redis": results[1],
        "mongo_vector_search": results[2],
        "sarvam_ai": results[3],
    }

    # Determine overall status
    CORE_SERVICES = {"mongodb", "redis"}

    core_healthy = all(checks[svc].get("status") == "healthy" for svc in CORE_SERVICES)
    all_healthy = all(check.get("status") == "healthy" for check in checks.values())

    if not core_healthy:
        overall_status = "unhealthy"
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    elif not all_healthy:
        overall_status = "degraded"
        status_code = status.HTTP_200_OK
    else:
        overall_status = "healthy"
        status_code = status.HTTP_200_OK

    return JSONResponse(
        status_code=status_code,
        content={"status": overall_status, "checks": checks},
    )


async def _vertex_live_test() -> Dict[str, Any]:
    """Live-test Vertex AI / Gemini with a 1-token generate call.

    Uses the same auth path as production (GEMINI_API_KEY → GenAI REST,
    or service-account JSON → OAuth2 Vertex endpoint).  maxOutputTokens=1
    keeps cost and latency minimal while still exercising the full quota path
    so a 429 RESOURCE_EXHAUSTED is caught correctly.
    """
    import httpx as _httpx
    from app.services.ai.vertex_client import (
        vertex_client,
        GENAI_BASE_URL,
        _thinking_config,
    )

    t0 = time.monotonic()
    try:
        gen_cfg = {"maxOutputTokens": 1, **_thinking_config(vertex_client.model)}

        if vertex_client._use_genai_api:
            url = f"{GENAI_BASE_URL}/{vertex_client.model}:generateContent?key={vertex_client._api_key}"
            payload = {
                "contents": [{"parts": [{"text": "Reply with one word: ok"}]}],
                "generationConfig": gen_cfg,
            }
            async with _httpx.AsyncClient(timeout=8.0) as c:
                resp = await c.post(url, json=payload)
        else:
            token = await vertex_client._get_access_token()
            url = f"{vertex_client.base_url}/{vertex_client.model}:generateContent"
            payload = {
                "contents": [{"role": "user", "parts": [{"text": "Reply with one word: ok"}]}],
                "generationConfig": gen_cfg,
            }
            async with _httpx.AsyncClient(timeout=8.0) as c:
                resp = await c.post(
                    url,
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    json=payload,
                )

        latency_ms = round((time.monotonic() - t0) * 1000, 1)

        if resp.status_code == 200:
            return {
                "status": "healthy",
                "latency_ms": latency_ms,
                "model": vertex_client.model,
                "http": 200,
                "backend": "genai" if vertex_client._use_genai_api else "vertex",
            }
        # 429 = credits depleted — the most important failure mode to surface
        detail = ""
        try:
            detail = resp.json().get("error", {}).get("message", "")[:200]
        except Exception:
            pass
        label = "credits_depleted" if resp.status_code == 429 else f"http_{resp.status_code}"
        return {
            "status": "unhealthy",
            "http": resp.status_code,
            "error": label,
            "detail": detail,
            "latency_ms": latency_ms,
            "model": vertex_client.model,
        }
    except Exception as e:
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        return {"status": "unhealthy", "error": str(e)[:200], "latency_ms": latency_ms}


async def _sarvam_live_test() -> Dict[str, Any]:
    """Live-test Sarvam AI with a 1-token chat completion.

    Uses the correct api-subscription-key header.  A 401/403 here means the
    key is wrong or expired; a 402 means billing is exhausted.
    """
    import httpx as _httpx
    from app.config import settings as _s

    t0 = time.monotonic()
    if not _s.SARVAM_API_KEY:
        return {"status": "degraded", "error": "SARVAM_API_KEY not configured", "latency_ms": 0.0}

    try:
        url = f"{_s.SARVAM_BASE_URL}/chat/completions"
        payload = {
            "model": _s.SARVAM_MODEL,
            "messages": [{"role": "user", "content": "Reply with one word: ok"}],
            "max_tokens": 1,
            "enable_thinking": False,
        }
        async with _httpx.AsyncClient(timeout=8.0) as c:
            resp = await c.post(
                url,
                headers={"api-subscription-key": _s.SARVAM_API_KEY, "Content-Type": "application/json"},
                json=payload,
            )

        latency_ms = round((time.monotonic() - t0) * 1000, 1)

        if resp.status_code == 200:
            return {"status": "healthy", "latency_ms": latency_ms, "model": _s.SARVAM_MODEL, "http": 200}

        detail = ""
        try:
            body = resp.json()
            detail = (body.get("message") or body.get("detail") or body.get("error") or "")[:200]
        except Exception:
            pass
        _http_labels = {401: "auth_failed", 402: "billing_exhausted", 403: "forbidden", 429: "rate_limited"}
        label = _http_labels.get(resp.status_code, f"http_{resp.status_code}")
        return {
            "status": "unhealthy",
            "http": resp.status_code,
            "error": label,
            "detail": detail,
            "latency_ms": latency_ms,
            "model": _s.SARVAM_MODEL,
        }
    except Exception as e:
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        return {"status": "unhealthy", "error": str(e)[:200], "latency_ms": latency_ms}


async def _tts_live_test() -> Dict[str, Any]:
    """Live-test Google Cloud Text-to-Speech by synthesising a single word.

    A 403 here means the service account is missing roles/cloudtexttospeech.user.
    """
    t0 = time.monotonic()
    try:
        from app.services.ai.vertex_client import vertex_client

        audio_bytes = await vertex_client.text_to_speech("ok", lang="en")
        latency_ms = round((time.monotonic() - t0) * 1000, 1)

        if audio_bytes and len(audio_bytes) > 0:
            return {"status": "healthy", "latency_ms": latency_ms, "bytes": len(audio_bytes)}
        return {"status": "unhealthy", "error": "empty_audio", "latency_ms": latency_ms}
    except RuntimeError as e:
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        msg = str(e)
        # Surface the IAM-403 root cause clearly
        if "403" in msg or "PERMISSION_DENIED" in msg:
            return {
                "status": "unhealthy",
                "http": 403,
                "error": "iam_permission_denied",
                "detail": "Grant roles/cloudtexttospeech.user to the service account",
                "latency_ms": latency_ms,
            }
        return {"status": "unhealthy", "error": msg[:200], "latency_ms": latency_ms}
    except Exception as e:
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        return {"status": "unhealthy", "error": str(e)[:200], "latency_ms": latency_ms}


async def _search_live_test() -> Dict[str, Any]:
    """Live-test the vector search pipeline with a minimal query.

    Runs the full path: embed → cosine match → chunk retrieval.
    Returns hit_count so you can spot when the index is empty.
    """
    t0 = time.monotonic()
    try:
        from app.services.search.mongo_vector_search import mongo_vector_search

        chunks, _ = await mongo_vector_search.search_context(
            query="photosynthesis", lang="en", limit=3
        )
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        return {
            "status": "healthy" if chunks else "degraded",
            "latency_ms": latency_ms,
            "hit_count": len(chunks),
            "top_score": round(chunks[0].get("score", 0), 3) if chunks else None,
        }
    except Exception as e:
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        return {"status": "unhealthy", "error": str(e)[:200], "latency_ms": latency_ms}


@router.get("/providers")
async def providers_health_check():
    """
    Live-test all four AI providers and return their real-time status.

    Each check makes a genuine minimal API call (1 output token where possible)
    so transient credit depletion, auth errors, and IAM gaps are detected here
    rather than surfacing as user-facing 5xx errors.

    Checks:
      - vertex_gemini  — generateContent (1 token); 429 = credits depleted
      - sarvam_ai      — chat/completions (1 token); 401/402 = key/billing issue
      - google_tts     — synthesize "ok"; 403 = missing IAM role
      - vector_search  — embed + cosine search; degraded = empty index

    Note: this endpoint makes real outbound API calls. Cache the result
    externally if you poll it at high frequency.
    """
    results = await asyncio.gather(
        _safe_check(_vertex_live_test(), timeout=10.0),
        _safe_check(_sarvam_live_test(), timeout=10.0),
        _safe_check(_tts_live_test(), timeout=10.0),
        _safe_check(_search_live_test(), timeout=10.0),
    )

    checks = {
        "vertex_gemini": results[0],
        "sarvam_ai": results[1],
        "google_tts": results[2],
        "vector_search": results[3],
    }

    # Classify overall: any unhealthy → degraded (TTS is non-critical)
    CRITICAL = {"vertex_gemini", "sarvam_ai"}
    critical_ok = all(checks[k].get("status") == "healthy" for k in CRITICAL)
    all_ok = all(v.get("status") == "healthy" for v in checks.values())

    if not critical_ok:
        overall = "degraded"
        http_status = status.HTTP_200_OK  # still 200 — monitoring tools read the body
    elif not all_ok:
        overall = "partial"
        http_status = status.HTTP_200_OK
    else:
        overall = "healthy"
        http_status = status.HTTP_200_OK

    return JSONResponse(
        status_code=http_status,
        content={
            "status": overall,
            "checked_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
            "providers": checks,
        },
    )


@router.get("/circuit-breakers")
async def circuit_breaker_status():
    """
    Get status of all circuit breakers.
    Useful for monitoring service resilience.
    """
    from app.core.circuit_breaker import (
        vertex_circuit_breaker,
        sarvam_circuit_breaker,
    )

    return {
        "vertex_ai": vertex_circuit_breaker.get_status(),
        "sarvam_ai": sarvam_circuit_breaker.get_status(),
    }
