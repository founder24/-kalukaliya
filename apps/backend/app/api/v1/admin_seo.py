"""
Admin SEO Endpoints
SEO entity health, history, and pipeline status (placeholder).
"""

from fastapi import APIRouter, Request
import logging

from app.api.v1.admin import _validate_admin_session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin SEO"])


@router.get("/seo/entity/status")
async def seo_entity_status(request: Request):
    """SEO entity health snapshot (placeholder)."""
    _validate_admin_session(request)

    return {
        "source": "placeholder",
        "entities_total": 0,
        "entities_indexed": 0,
        "entities_pending": 0,
        "entities_error": 0,
        "last_crawl": None,
    }


@router.get("/seo/entity/history")
async def seo_entity_history(request: Request):
    """SEO entity health history (placeholder)."""
    _validate_admin_session(request)

    return {
        "source": "placeholder",
        "history": [],
    }


@router.get("/seo/pipeline-status")
async def seo_pipeline_status(request: Request):
    """SEO pipeline status (placeholder)."""
    _validate_admin_session(request)

    return {
        "source": "placeholder",
        "pipelines": [],
        "last_run": None,
        "status": "idle",
    }
