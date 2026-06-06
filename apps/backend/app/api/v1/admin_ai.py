"""
Admin AI Endpoints
AI provider configuration, system status, and circuit breaker management.
"""

from fastapi import APIRouter, Request
import logging

from app.api.v1.admin import _validate_admin_session
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin AI"])


@router.get("/ai/providers")
async def ai_providers(request: Request):
    """AI provider config and health."""
    await _validate_admin_session(request)

    providers = []

    # Vertex AI (Google)
    providers.append(
        {
            "name": "vertex_ai",
            "model": settings.VERTEX_GEMINI_MODEL,
            "location": settings.VERTEX_LOCATION,
            "configured": bool(settings.VERTEX_PROJECT_ID),
            "status": "configured" if settings.VERTEX_PROJECT_ID else "not_configured",
        }
    )

    # Sarvam AI (Indic)
    providers.append(
        {
            "name": "sarvam_ai",
            "model": settings.SARVAM_MODEL,
            "base_url": settings.SARVAM_BASE_URL,
            "configured": bool(settings.SARVAM_API_KEY),
            "status": "configured" if settings.SARVAM_API_KEY else "not_configured",
        }
    )

    return {"providers": providers}


@router.post("/ai/reset-circuit")
async def reset_circuit_breakers(request: Request):
    """Reset all AI circuit breakers to CLOSED state. Clears accumulated failures.
    Use before running integration tests or after a transient AI outage."""
    await _validate_admin_session(request)

    from app.core.circuit_breaker import (
        vertex_circuit_breaker,
        sarvam_circuit_breaker,
        vertex_search_circuit_breaker,
    )

    before = {
        "vertex_ai": vertex_circuit_breaker.get_status()["state"],
        "sarvam_ai": sarvam_circuit_breaker.get_status()["state"],
        "vertex_search": vertex_search_circuit_breaker.get_status()["state"],
    }

    vertex_circuit_breaker.reset()
    sarvam_circuit_breaker.reset()
    vertex_search_circuit_breaker.reset()

    after = {
        "vertex_ai": vertex_circuit_breaker.get_status()["state"],
        "sarvam_ai": sarvam_circuit_breaker.get_status()["state"],
        "vertex_search": vertex_search_circuit_breaker.get_status()["state"],
    }

    logger.info("Circuit breakers manually reset by admin")
    return {
        "status": "ok",
        "message": "All circuit breakers reset to CLOSED",
        "before": before,
        "after": after,
    }


@router.get("/ai/status")
async def ai_status(request: Request):
    """Current AI system status."""
    await _validate_admin_session(request)

    vertex_ok = bool(settings.VERTEX_PROJECT_ID)
    sarvam_ok = bool(settings.SARVAM_API_KEY)

    overall = "healthy"
    if not vertex_ok and not sarvam_ok:
        overall = "degraded"
    elif not vertex_ok or not sarvam_ok:
        overall = "partial"

    return {
        "overall_status": overall,
        "vertex_ai": "ok" if vertex_ok else "not_configured",
        "sarvam_ai": "ok" if sarvam_ok else "not_configured",
        "active_model": settings.VERTEX_GEMINI_MODEL,
    }
