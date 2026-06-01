"""
Health Check Endpoints: Basic and Deep Dependency Checks
"""

import asyncio

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
    """Ping MongoDB connection"""
    try:
        from app.db.mongo import get_mongo_client

        client = get_mongo_client()
        await client.admin.command("ping")
        return {"status": "healthy", "latency_ms": "N/A"}
    except Exception as e:
        logger.error(f"MongoDB ping failed: {str(e)}")
        return {"status": "unhealthy", "error": str(e)}


async def redis_ping() -> Dict[str, Any]:
    """Ping Upstash Redis connection"""
    try:
        from app.db.redis import get_redis

        redis = get_redis()
        result = await redis.ping()
        if result:
            return {"status": "healthy"}
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
            return {"status": "unhealthy", "error": "Search client not configured"}

        await search_service.warm_up()
        return {"status": "healthy"}
    except Exception as e:
        logger.error(f"Vertex Search ping failed: {str(e)}")
        return {"status": "unhealthy", "error": str(e)}


async def vertex_ping() -> Dict[str, Any]:
    """Ping Vertex AI (lightweight check)"""
    try:
        # Just check if credentials are loaded, don't make actual API call
        from app.config import settings

        if settings.VERTEX_PROJECT_ID and settings.GOOGLE_APPLICATION_CREDENTIALS_JSON:
            return {"status": "healthy", "project_id": settings.VERTEX_PROJECT_ID}
        else:
            return {"status": "unhealthy", "error": "Missing credentials"}
    except Exception as e:
        logger.error(f"Vertex AI check failed: {str(e)}")
        return {"status": "unhealthy", "error": str(e)}


@router.get("")
async def basic_health_check():
    """
    Basic health check - returns 200 if app is running.
    Does not check dependencies.
    Reports 'degraded' status if there are startup configuration errors.
    """
    from app.config import settings

    if settings.startup_errors:
        return {
            "status": "degraded",
            "service": "syrabit-backend",
            "config_errors": settings.startup_errors,
        }
    return {"status": "healthy", "service": "syrabit-backend"}


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
    )
    checks = {
        "mongodb": results[0],
        "redis": results[1],
        "vertex_search": results[2],
        "vertex_ai": results[3],
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
