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
    """Ping Upstash Redis connection.

    Returns "disabled" (not "unhealthy") when UPSTASH credentials are absent —
    Redis is an optional dependency; missing credentials is an intentional
    configuration choice, not a service failure.
    """
    from app.config import settings

    if not settings.UPSTASH_REDIS_REST_URL or not settings.UPSTASH_REDIS_REST_TOKEN:
        return {"status": "disabled", "reason": "UPSTASH credentials not configured"}

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
    # Redis is optional — "disabled" (no credentials) is acceptable, not a failure.
    CORE_SERVICES = {"mongodb", "redis"}
    ACCEPTABLE = {"healthy", "disabled"}

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
