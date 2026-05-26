"""
Health Check Endpoints: Basic and Deep Dependency Checks
"""

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Health"])


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


async def azure_search_ping() -> Dict[str, Any]:
    """Ping Azure Search service"""
    try:
        from app.services.search.azure_search import search_service

        if not search_service.client:
            return {"status": "unhealthy", "error": "Search client not configured"}

        # Use async iteration for the async search client
        async for _ in search_service.client.search(search_text="*", top=1):
            break
        return {"status": "healthy"}
    except Exception as e:
        logger.error(f"Azure Search ping failed: {str(e)}")
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
    """
    return {"status": "healthy", "service": "syrabit-backend"}


@router.get("/deep")
async def deep_health_check():
    """
    Deep health check - verifies all critical dependencies.
    Returns 503 if any dependency is unhealthy.

    Checks:
    - MongoDB connection
    - Redis connection
    - Azure Search service
    - Vertex AI configuration
    """
    checks = {
        "mongodb": await mongo_ping(),
        "redis": await redis_ping(),
        "azure_search": await azure_search_ping(),
        "vertex_ai": await vertex_ping(),
    }

    # Determine overall status
    all_healthy = all(check.get("status") == "healthy" for check in checks.values())

    status_code = (
        status.HTTP_200_OK if all_healthy else status.HTTP_503_SERVICE_UNAVAILABLE
    )

    return JSONResponse(
        status_code=status_code,
        content={"status": "healthy" if all_healthy else "degraded", "checks": checks},
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
        azure_search_circuit_breaker,
    )

    return {
        "vertex_ai": vertex_circuit_breaker.get_status(),
        "sarvam_ai": sarvam_circuit_breaker.get_status(),
        "azure_search": azure_search_circuit_breaker.get_status(),
    }
