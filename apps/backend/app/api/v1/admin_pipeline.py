"""
Admin Pipeline Endpoints
Content generation pipeline, job status, D1 sync.
"""
from fastapi import APIRouter, Request
import logging

from app.api.v1.admin import _validate_admin_session, _csrf_check

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/pipeline/auto-generate")
async def pipeline_auto_generate(request: Request):
    """Placeholder auto-generate content."""
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"status": "triggered", "job_id": None, "source": "placeholder"}


@router.get("/pipeline/status/{job_id}")
async def pipeline_status(job_id: str, request: Request):
    """Placeholder job status."""
    _validate_admin_session(request)
    return {
        "job_id": job_id,
        "status": "unknown",
        "progress": 0,
        "source": "placeholder",
    }


@router.post("/d1-sync")
async def d1_sync(request: Request):
    """Placeholder D1 sync."""
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"status": "triggered", "synced": 0, "source": "placeholder"}
