"""
Admin SEO Endpoints
All endpoints return placeholder/mock data with source:placeholder field.
"""
from fastapi import APIRouter, Request
import logging

from app.api.v1.admin import _validate_admin_session, _csrf_check

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/seo/entity/status")
async def seo_entity_status(request: Request):
    _validate_admin_session(request)
    return {
        "coverage_pct": 72,
        "total_entities": 150,
        "indexed": 108,
        "missing": 42,
        "source": "placeholder",
    }


@router.get("/seo/entity/history")
async def seo_entity_history(request: Request):
    _validate_admin_session(request)
    return {"snapshots": [], "source": "placeholder"}


@router.post("/seo/entity/refresh")
async def seo_entity_refresh(request: Request):
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"status": "triggered", "source": "placeholder"}


@router.get("/seo/health-history")
async def seo_health_history(request: Request):
    _validate_admin_session(request)
    return {"history": [], "source": "placeholder"}


@router.post("/seo/health-snapshot")
async def seo_health_snapshot(request: Request):
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"status": "ok", "source": "placeholder"}


@router.get("/seo/deep-scan-history")
async def seo_deep_scan_history(request: Request):
    _validate_admin_session(request)
    return {"scans": [], "source": "placeholder"}


@router.get("/seo/google-indexing-stats")
async def seo_google_indexing_stats(request: Request):
    _validate_admin_session(request)
    return {
        "indexed_pages": 0,
        "crawled_pages": 0,
        "errors": 0,
        "source": "placeholder",
    }


@router.get("/seo/pipeline-status")
async def seo_pipeline_status(request: Request):
    _validate_admin_session(request)
    return {
        "total_topics": 0,
        "published": 0,
        "has_content": 0,
        "needs_schema": 0,
        "needs_internal_links": 0,
        "pages_total": 0,
        "published_today": 0,
        "source": "placeholder",
    }


@router.get("/seo/sitemap-validate")
async def seo_sitemap_validate(request: Request):
    _validate_admin_session(request)
    return {"valid": True, "errors": [], "source": "placeholder"}


@router.get("/seo/topic-discovery/runs")
async def seo_topic_discovery_runs(request: Request):
    _validate_admin_session(request)
    return {"runs": [], "source": "placeholder"}


@router.get("/seo/topic-discovery/candidates")
async def seo_topic_discovery_candidates(request: Request):
    _validate_admin_session(request)
    return {"candidates": [], "total": 0, "source": "placeholder"}


@router.post("/seo/topic-discovery/run-now")
async def seo_topic_discovery_run_now(request: Request):
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"status": "triggered", "source": "placeholder"}


@router.post("/seo/topic-discovery/{candidate_id}/override")
async def seo_topic_discovery_override(candidate_id: str, request: Request):
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"status": "ok", "source": "placeholder"}


@router.get("/seo/remediation/status")
async def seo_remediation_status(request: Request):
    _validate_admin_session(request)
    return {
        "pending": 0,
        "completed": 0,
        "failed": 0,
        "source": "placeholder",
    }


@router.get("/seo/remediation/history")
async def seo_remediation_history(request: Request):
    _validate_admin_session(request)
    return {"history": [], "source": "placeholder"}


@router.post("/seo/remediation/{rec_id}/promote")
async def seo_remediation_promote(rec_id: str, request: Request):
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"status": "ok", "source": "placeholder"}


@router.post("/seo/remediation/trigger")
async def seo_remediation_trigger(request: Request):
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"status": "triggered", "source": "placeholder"}


@router.post("/seo/remediation/circuit/reset")
async def seo_remediation_circuit_reset(request: Request):
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"status": "reset", "source": "placeholder"}


@router.get("/seo/internal-links/status")
async def seo_internal_links_status(request: Request):
    _validate_admin_session(request)
    return {
        "total_links": 0,
        "approved": 0,
        "pending": 0,
        "rejected": 0,
        "source": "placeholder",
    }


@router.get("/seo/internal-links/pending")
async def seo_internal_links_pending(request: Request):
    _validate_admin_session(request)
    return {"pending": [], "source": "placeholder"}


@router.get("/seo/internal-links/history")
async def seo_internal_links_history(request: Request):
    _validate_admin_session(request)
    return {"history": [], "source": "placeholder"}


@router.post("/seo/internal-links/{rec_id}/approve")
async def seo_internal_links_approve(rec_id: str, request: Request):
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"status": "ok", "source": "placeholder"}


@router.post("/seo/internal-links/{rec_id}/reject")
async def seo_internal_links_reject(rec_id: str, request: Request):
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"status": "ok", "source": "placeholder"}


@router.post("/seo/internal-links/{rec_id}/revert")
async def seo_internal_links_revert(rec_id: str, request: Request):
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"status": "ok", "source": "placeholder"}


@router.post("/seo/internal-links/trigger")
async def seo_internal_links_trigger(request: Request):
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"status": "triggered", "source": "placeholder"}


@router.post("/seo/inject-schema/{slug}")
async def seo_inject_schema(slug: str, request: Request):
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"status": "ok", "source": "placeholder"}


@router.post("/seo/inject-schema-bulk")
async def seo_inject_schema_bulk(request: Request):
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"status": "ok", "count": 0, "source": "placeholder"}


@router.get("/seo/prewarm-coverage")
async def seo_prewarm_coverage(request: Request):
    _validate_admin_session(request)
    return {
        "total_pages": 0,
        "prewarmed": 0,
        "coverage_pct": 0,
        "source": "placeholder",
    }


@router.post("/seo/google-sitemap-ping")
async def seo_google_sitemap_ping(request: Request):
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"status": "ok", "source": "placeholder"}


@router.post("/seo/indexnow/smoke")
async def seo_indexnow_smoke(request: Request):
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"status": "ok", "source": "placeholder"}


@router.get("/seo/indexnow/smoke/history")
async def seo_indexnow_smoke_history(request: Request):
    _validate_admin_session(request)
    return {"history": [], "source": "placeholder"}


@router.get("/seo/daily-summary-dispatches")
async def seo_daily_summary_dispatches(request: Request):
    _validate_admin_session(request)
    return {"dispatches": [], "source": "placeholder"}
