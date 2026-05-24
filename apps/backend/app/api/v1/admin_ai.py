"""
Admin AI Endpoints
Vertex AI health, intelligence overview, KV/R2/CI health, GA4.
"""

from fastapi import APIRouter, Request
import logging

from app.api.v1.admin import _validate_admin_session

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/vertex/health")
async def vertex_health(request: Request):
    """Check Vertex AI config from settings."""
    _validate_admin_session(request)
    try:
        from app.config import settings

        configured = bool(
            settings.VERTEX_PROJECT_ID and settings.GOOGLE_APPLICATION_CREDENTIALS_JSON
        )
        return {
            "status": "healthy" if configured else "not_configured",
            "project_id": settings.VERTEX_PROJECT_ID,
            "location": settings.VERTEX_LOCATION,
            "model": settings.VERTEX_GEMINI_MODEL,
        }
    except Exception as e:
        logger.error(f"Vertex health check error: {e}")
        return {"status": "error", "error": str(e)}


@router.get("/vertex/probe-status")
async def vertex_probe_status(request: Request):
    """Return cached probe state (placeholder)."""
    _validate_admin_session(request)
    return {"status": "unknown", "last_probe": None, "source": "placeholder"}


@router.get("/intelligence/overview")
async def intelligence_overview(request: Request):
    """Return AI provider summary."""
    _validate_admin_session(request)
    try:
        from app.config import settings

        vertex_ok = bool(
            settings.VERTEX_PROJECT_ID and settings.GOOGLE_APPLICATION_CREDENTIALS_JSON
        )
        sarvam_ok = bool(settings.SARVAM_API_KEY)

        return {
            "vertex_ai": {
                "status": "configured" if vertex_ok else "not_configured",
                "model": settings.VERTEX_GEMINI_MODEL,
            },
            "sarvam_ai": {
                "status": "configured" if sarvam_ok else "not_configured",
                "model": settings.SARVAM_MODEL,
            },
            "embedder": {
                "status": "configured"
                if settings.AZURE_SEARCH_ENDPOINT
                else "not_configured",
                "model": settings.AZURE_EMBEDDING_MODEL,
            },
        }
    except Exception as e:
        logger.error(f"Intelligence overview error: {e}")
        return {
            "vertex_ai": {"status": "error"},
            "sarvam_ai": {"status": "error"},
            "embedder": {"status": "error"},
        }


@router.get("/kv-health")
async def kv_health(request: Request):
    """Placeholder KV health."""
    _validate_admin_session(request)
    return {"status": "unknown", "source": "placeholder"}


@router.get("/r2-storage-health")
async def r2_storage_health(request: Request):
    """Placeholder R2 health."""
    _validate_admin_session(request)
    return {"status": "unknown", "source": "placeholder"}


@router.get("/ci-status")
async def ci_status(request: Request):
    """Placeholder CI status."""
    _validate_admin_session(request)
    return {"status": "unknown", "last_run": None, "source": "placeholder"}


@router.get("/ga4/status")
async def ga4_status(request: Request):
    """Placeholder GA4 status."""
    _validate_admin_session(request)
    return {"connected": False, "property_id": None, "source": "placeholder"}


@router.get("/ga4/auth-url")
async def ga4_auth_url(request: Request):
    """Placeholder GA4 auth URL."""
    _validate_admin_session(request)
    return {"url": None, "source": "placeholder"}


@router.get("/ga4/test")
async def ga4_test(request: Request):
    """Placeholder GA4 test."""
    _validate_admin_session(request)
    return {"status": "not_connected", "source": "placeholder"}
