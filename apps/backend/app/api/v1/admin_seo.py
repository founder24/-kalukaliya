"""
Admin SEO Endpoints
SEO entity health, history, pipeline status, bulk generation, coverage,
entity refresh, deep scan history, and AI-powered SEO generation.
"""

from datetime import datetime, timezone
from typing import Optional

from beanie import PydanticObjectId
from fastapi import APIRouter, HTTPException, Request
import logging

from app.api.v1.admin import _validate_admin_session, _csrf_check
from app.models.content import Chapter, Subject

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin SEO"])

# In-memory scan history (persists per process lifetime)
_scan_history: list[dict] = []


@router.get("/seo/entity/status")
async def seo_entity_status(request: Request):
    """SEO entity health snapshot."""
    _validate_admin_session(request)

    try:
        chapters = await Chapter.find_all().to_list()
    except Exception as e:
        logger.error(f"Failed to query chapters for entity status: {e}")
        return {
            "entities_total": 0,
            "entities_indexed": 0,
            "entities_pending": 0,
            "entities_draft": 0,
            "entities_error": 0,
            "last_crawl": None,
            "error": "Database not available",
        }

    total = len(chapters)
    published = sum(1 for c in chapters if c.status == "published")
    draft = sum(1 for c in chapters if c.status == "draft")
    generated = sum(1 for c in chapters if c.status == "generated")

    return {
        "entities_total": total,
        "entities_indexed": published,
        "entities_pending": generated,
        "entities_draft": draft,
        "entities_error": total - published - draft - generated,
        "last_crawl": None,
    }


@router.get("/seo/entity/history")
async def seo_entity_history(request: Request):
    """SEO entity health history."""
    _validate_admin_session(request)

    return {
        "history": _scan_history[-20:],
    }


@router.get("/seo/pipeline-status")
async def seo_pipeline_status(request: Request):
    """SEO pipeline status grouped by subject with per-subject counts."""
    _validate_admin_session(request)

    chapters = await Chapter.find_all().to_list()
    subjects = await Subject.find_all().to_list()

    # Build subject name lookup
    subject_map = {str(s.id): s.name for s in subjects}

    # Group chapters by subject_id
    pipelines: dict[str, dict] = {}
    for ch in chapters:
        sid = str(ch.subject_id)
        if sid not in pipelines:
            pipelines[sid] = {
                "subject_id": sid,
                "subject_name": subject_map.get(sid, "Unknown"),
                "draft": 0,
                "generated": 0,
                "published": 0,
                "total": 0,
            }
        pipelines[sid]["total"] += 1
        status = ch.status
        if status in ("draft", "generated", "published"):
            pipelines[sid][status] += 1

    return {
        "pipelines": list(pipelines.values()),
        "total_chapters": len(chapters),
        "status": "active" if chapters else "idle",
    }


@router.post("/seo/bulk-generate")
async def seo_bulk_generate(request: Request):
    """Bulk generate SEO pages for specified topic IDs."""
    _validate_admin_session(request)
    await _csrf_check(request)

    body = await request.json()
    topic_ids = body.get("topic_ids", [])
    if not topic_ids:
        raise HTTPException(status_code=400, detail="topic_ids list is required")

    # Find chapters containing those topic IDs
    chapters = await Chapter.find(
        {"published_topics.id": {"$in": topic_ids}}
    ).to_list()

    if not chapters:
        return {
            "status": "no_matches",
            "message": "No chapters found containing the specified topic IDs",
            "topics_requested": len(topic_ids),
            "topics_processed": 0,
        }

    # Collect matching topics
    matched_topics = []
    for ch in chapters:
        for topic in ch.published_topics:
            if topic.id in topic_ids:
                matched_topics.append({
                    "title": topic.title,
                    "definition": topic.definition or "",
                })

    try:
        from app.services.seo_generator import SEOGeneratorService

        service = SEOGeneratorService()
        result = await service.generate_seo_pages(matched_topics)
        return {
            "status": "generated",
            "topics_requested": len(topic_ids),
            "topics_processed": len(matched_topics),
            "result": result,
        }
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"Bulk SEO generation failed: {e}")
        raise HTTPException(status_code=500, detail=f"Bulk generation failed: {e}")


@router.get("/seo/coverage")
async def seo_coverage(request: Request):
    """Return published vs draft vs generated counts per subject."""
    _validate_admin_session(request)

    chapters = await Chapter.find_all().to_list()
    subjects = await Subject.find_all().to_list()

    subject_map = {str(s.id): s.name for s in subjects}

    coverage: dict[str, dict] = {}
    for ch in chapters:
        sid = str(ch.subject_id)
        if sid not in coverage:
            coverage[sid] = {
                "subject_id": sid,
                "subject_name": subject_map.get(sid, "Unknown"),
                "draft": 0,
                "generated": 0,
                "published": 0,
                "total": 0,
            }
        coverage[sid]["total"] += 1
        status = ch.status
        if status in ("draft", "generated", "published"):
            coverage[sid][status] += 1

    return {
        "coverage": list(coverage.values()),
        "totals": {
            "draft": sum(c["draft"] for c in coverage.values()),
            "generated": sum(c["generated"] for c in coverage.values()),
            "published": sum(c["published"] for c in coverage.values()),
            "total": sum(c["total"] for c in coverage.values()),
        },
    }


@router.post("/seo/entity/refresh")
async def seo_entity_refresh(request: Request):
    """Re-probe entity SEO health: check published chapters for completeness."""
    _validate_admin_session(request)
    await _csrf_check(request)

    chapters = await Chapter.find({"status": "published"}).to_list()

    missing_content_en = 0
    missing_meta_description = 0
    missing_keywords = 0
    missing_published_topics = 0

    for ch in chapters:
        if not ch.content_en:
            missing_content_en += 1
        if not ch.meta_description:
            missing_meta_description += 1
        if not ch.keywords:
            missing_keywords += 1
        if not ch.published_topics:
            missing_published_topics += 1

    total_published = len(chapters)
    health_score = 0.0
    if total_published > 0:
        fields_checked = 4
        total_fields = total_published * fields_checked
        missing_total = (
            missing_content_en
            + missing_meta_description
            + missing_keywords
            + missing_published_topics
        )
        health_score = round((1 - missing_total / total_fields) * 100, 1)

    return {
        "status": "refreshed",
        "total_published": total_published,
        "health_score": health_score,
        "missing": {
            "content_en": missing_content_en,
            "meta_description": missing_meta_description,
            "keywords": missing_keywords,
            "published_topics": missing_published_topics,
        },
    }


@router.get("/seo/deep-scan-history")
async def seo_deep_scan_history(request: Request):
    """Return history of sitemap scan/regeneration events."""
    _validate_admin_session(request)

    return {
        "scan_history": _scan_history[-50:],
        "total_scans": len(_scan_history),
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

        # Record scan event in history
        _scan_history.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": "regenerate_sitemap",
            "entries_count": result.get("entries_count", 0),
            "status": result.get("status", "unknown"),
        })

        return result
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        logger.error(f"Sitemap regeneration failed: {e}")
        raise HTTPException(status_code=500, detail=f"Sitemap regeneration failed: {e}")
