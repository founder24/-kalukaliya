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
        logger.error(f"MongoDB not initialized: {str(e)}")
        return {"status": "unhealthy", "error": str(e)}
    except Exception as e:
        logger.error(f"MongoDB ping failed: {str(e)}")
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
        logger.error(f"Redis ping failed: {str(e)}")
        return {"status": "unhealthy", "error": str(e)}


async def vertex_search_ping() -> Dict[str, Any]:
    """Ping Vertex AI Search (Discovery Engine) service"""
    try:
        from app.services.search.vertex_search import search_service

        if not search_service._initialized:
            return {"status": "degraded", "error": "Search client not configured"}

        await search_service.warm_up()
        return {"status": "healthy"}
    except Exception as e:
        logger.error(f"Vertex Search ping failed: {str(e)}")
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
        logger.error(f"Vertex AI check failed: {str(e)}")
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

    if settings.startup_errors:
        return JSONResponse(
            status_code=503,
            content={
                "status": "degraded",
                "service": "syrabit-backend",
                "config_error_count": len(settings.startup_errors),
                "warnings": warnings,
            },
        )

    response = {
        "status": "healthy",
        "service": "syrabit-backend",
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
        _safe_check(vertex_search_ping()),
        _safe_check(vertex_ping()),
        _safe_check(sarvam_ping()),
    )
    checks = {
        "mongodb": results[0],
        "redis": results[1],
        "vertex_search": results[2],
        "vertex_ai": results[3],
        "sarvam_ai": results[4],
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


@router.get("/circuit-breakers")
async def circuit_breaker_status():
    """
    Get status of all circuit breakers.
    Useful for monitoring service resilience.
    """
    from app.core.circuit_breaker import (
        vertex_circuit_breaker,
        sarvam_circuit_breaker,
        vertex_search_circuit_breaker,
    )

    return {
        "vertex_ai": vertex_circuit_breaker.get_status(),
        "sarvam_ai": sarvam_circuit_breaker.get_status(),
        "vertex_search": vertex_search_circuit_breaker.get_status(),
    }
