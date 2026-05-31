"""
Admin AI Endpoints
AI provider configuration and system status.
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
