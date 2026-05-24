"""
Admin SEO Endpoints
SEO entity health, history, pipeline status, and AI-powered SEO generation.
"""

from fastapi import APIRouter, HTTPException, Request
import logging

from app.api.v1.admin import _validate_admin_session, _csrf_check

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


@router.post("/seo/extract")
async def seo_extract_topics(request: Request):
    """Extract topics from chapter content using AI."""
    _validate_admin_session(request)
    await _csrf_check(request)

    body = await request.json()
    chapter_id = body.get("chapter_id")
    if not chapter_id:
        raise HTTPException(status_code=400, detail="chapter_id is required")

    try:
        from app.services.seo_generator import SEOGeneratorService

        service = SEOGeneratorService()
        result = await service.extract_topics_from_content(chapter_id)
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"SEO extract failed for chapter {chapter_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Topic extraction failed: {e}")


@router.post("/seo/generate")
async def seo_generate_pages(request: Request):
    """Generate SEO page content variations for given topics."""
    _validate_admin_session(request)
    await _csrf_check(request)

    body = await request.json()
    topics = body.get("topics", [])
    if not topics:
        raise HTTPException(status_code=400, detail="topics list is required")

    try:
        from app.services.seo_generator import SEOGeneratorService

        service = SEOGeneratorService()
        result = await service.generate_seo_pages(topics)
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"SEO page generation failed: {e}")
        raise HTTPException(status_code=500, detail=f"SEO generation failed: {e}")


@router.post("/seo/regenerate-sitemap")
async def seo_regenerate_sitemap(request: Request):
    """Regenerate sitemap XML from all published chapters."""
    _validate_admin_session(request)
    await _csrf_check(request)

    try:
        from app.services.content_publisher import ContentPublisherService

        service = ContentPublisherService()
        result = await service.regenerate_sitemap()
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"Sitemap regeneration failed: {e}")
        raise HTTPException(status_code=500, detail=f"Sitemap regeneration failed: {e}")
