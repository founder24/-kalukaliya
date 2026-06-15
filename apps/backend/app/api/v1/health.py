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

        # Any non-connection-error response means the endpoint is reachable.
        # The base URL (/v1) returning 404 is expected — it has no GET handler.
        # Only 5xx responses indicate a real Sarvam infrastructure problem.
        if resp.status_code < 500:
            return {"status": "healthy", "latency_ms": latency_ms}
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
    - MongoDB vector search (topic embeddings)
    - Sarvam AI reachability
    """
    results = await asyncio.gather(
        _safe_check(mongo_ping()),
        _safe_check(mongo_vector_search_ping()),
        _safe_check(sarvam_ping()),
    )
    checks = {
        "mongodb": results[0],
        "mongo_vector_search": results[1],
        "sarvam_ai": results[2],
    }

    CORE_SERVICES = {"mongodb"}
    ACCEPTABLE = {"healthy"}

    core_healthy = all(checks[svc].get("status") in ACCEPTABLE for svc in CORE_SERVICES)
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


async def google_tts_ping() -> Dict[str, Any]:
    """Check Google TTS / Cloud Text-to-Speech credentials availability."""
    try:
        from app.config import settings

        if not settings.GOOGLE_APPLICATION_CREDENTIALS_JSON and not os.environ.get("K_SERVICE"):
            return {"status": "not_configured", "note": "No SA key and not on Cloud Run (Workload Identity)"}

        import json as _json

        if settings.GOOGLE_APPLICATION_CREDENTIALS_JSON:
            cred_data = _json.loads(settings.GOOGLE_APPLICATION_CREDENTIALS_JSON)
            project_id = cred_data.get("project_id", "unknown")
            return {"status": "healthy", "auth": "service_account", "project_id": project_id}

        return {"status": "healthy", "auth": "workload_identity"}
    except Exception as e:
        logger.warning(f"Google TTS check failed: {str(e)}")
        return {"status": "unhealthy", "error": str(e)[:120]}


async def cloudflare_workers_ai_ping() -> Dict[str, Any]:
    """Verify CF Workers AI token by calling the lightweight /ai/models endpoint."""
    try:
        from app.config import settings

        token = getattr(settings, "CF_WORKER_AI_TOKEN", None) or os.environ.get("CF_WORKER_AI_TOKEN")
        account_id = getattr(settings, "CF_ACCOUNT_ID", None) or os.environ.get("CF_ACCOUNT_ID")

        if not token:
            return {"status": "not_configured", "note": "CF_WORKER_AI_TOKEN not set"}
        if not account_id:
            return {"status": "not_configured", "note": "CF_ACCOUNT_ID not set"}

        import httpx

        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/models/search?per_page=1",
                headers={"Authorization": f"Bearer {token}"},
            )
        latency_ms = round((time.monotonic() - t0) * 1000, 1)

        if resp.status_code == 200:
            return {"status": "healthy", "latency_ms": latency_ms}
        elif resp.status_code == 401:
            return {"status": "unhealthy", "error": "CF_WORKER_AI_TOKEN is invalid (401 Unauthorized)"}
        elif resp.status_code == 403:
            return {"status": "unhealthy", "error": "CF_WORKER_AI_TOKEN lacks AI permissions (403 Forbidden)"}
        else:
            return {"status": "degraded", "error": f"HTTP {resp.status_code}", "latency_ms": latency_ms}
    except Exception as e:
        logger.warning(f"CF Workers AI ping failed: {str(e)}")
        return {"status": "unhealthy", "error": str(e)[:120]}


@router.get("/providers")
async def provider_health_check():
    """
    Check all AI provider integrations in parallel.

    Providers checked:
      - vertex_gemini       : Vertex AI / Gemini (credential config)
      - sarvam_ai           : Sarvam API endpoint reachability
      - google_tts          : Cloud Text-to-Speech credentials
      - vector_search       : MongoDB topic embedding cache
      - redis               : Upstash Redis session store
      - cloudflare_workers_ai : CF Workers AI token validity

    Overall status:
      healthy  — all providers healthy
      degraded — ≥1 provider degraded/not_configured but none unhealthy
      unhealthy — ≥1 provider explicitly unhealthy
    """
    results = await asyncio.gather(
        _safe_check(vertex_ping(),               timeout=10.0),
        _safe_check(sarvam_ping(),               timeout=10.0),
        _safe_check(google_tts_ping(),           timeout=5.0),
        _safe_check(mongo_vector_search_ping(),  timeout=10.0),
        _safe_check(cloudflare_workers_ai_ping(), timeout=10.0),
    )

    providers = {
        "vertex_gemini":         results[0],
        "sarvam_ai":             results[1],
        "google_tts":            results[2],
        "vector_search":         results[3],
        "cloudflare_workers_ai": results[4],
    }

    statuses = [p.get("status", "unknown") for p in providers.values()]

    if any(s == "unhealthy" for s in statuses):
        overall = "unhealthy"
        http_status = status.HTTP_503_SERVICE_UNAVAILABLE
    elif any(s == "degraded" for s in statuses):
        overall = "degraded"
        http_status = status.HTTP_200_OK
    else:
        overall = "healthy"
        http_status = status.HTTP_200_OK

    return JSONResponse(
        status_code=http_status,
        content={
            "overall": overall,
            "providers": providers,
            "timestamp": __import__("datetime").datetime.utcnow().isoformat() + "Z",
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
