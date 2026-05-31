"""
Admin SEO Endpoints - Layer 4
Entity health, pipeline status, bulk generation, coverage analysis.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from beanie import PydanticObjectId
from fastapi import APIRouter, HTTPException, Request, Query
from pydantic import BaseModel

from app.api.v1.admin import _validate_admin_session, _csrf_check
from app.models.content import Chapter, Subject
from app.services.seo_generator import seo_generator_service
from app.services.content_publisher import content_publisher_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin SEO"])

# In-memory scan history (resets on restart)
_scan_history: list[dict] = []


class BulkGenerateRequest(BaseModel):
    topic_ids: list[str]


class ExtractRequest(BaseModel):
    chapter_id: str


class GenerateSEORequest(BaseModel):
    topics: list[dict]


@router.get("/seo/entity/status")
async def seo_entity_status(request: Request):
    """SEO entity health from Chapter collection."""
    await _validate_admin_session(request)

    try:
        total = await Chapter.count()
        published = await Chapter.find({"status": "published"}).count()
        generated = await Chapter.find({"status": "generated"}).count()
        draft = await Chapter.find({"status": "draft"}).count()
    except Exception:
        total = published = generated = draft = 0

    return {
        "entities_total": total,
        "entities_published": published,
        "entities_generated": generated,
        "entities_draft": draft,
        "health_score": round((published / total * 100) if total > 0 else 0, 1),
        "last_check": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/seo/entity/history")
async def seo_entity_history(request: Request):
    """In-memory scan history."""
    await _validate_admin_session(request)

    return {"history": _scan_history[-50:], "total": len(_scan_history)}


@router.get("/seo/pipeline-status")
async def seo_pipeline_status(request: Request):
    """Generation/publish status per subject."""
    await _validate_admin_session(request)

    try:
        subjects = await Subject.find_all().to_list()
    except Exception:
        return {"pipelines": [], "total_subjects": 0}

    # Single aggregation to get chapter stats grouped by subject_id
    try:
        chapter_stats = await Chapter.aggregate(
            [
                {
                    "$group": {
                        "_id": "$subject_id",
                        "total": {"$sum": 1},
                        "published": {
                            "$sum": {"$cond": [{"$eq": ["$status", "published"]}, 1, 0]}
                        },
                        "generated": {
                            "$sum": {"$cond": [{"$eq": ["$status", "generated"]}, 1, 0]}
                        },
                        "draft": {
                            "$sum": {"$cond": [{"$eq": ["$status", "draft"]}, 1, 0]}
                        },
                    }
                }
            ]
        ).to_list()
    except Exception:
        chapter_stats = []

    # Build lookup from subject_id to stats
    stats_lookup = {}
    for stat in chapter_stats:
        stats_lookup[stat["_id"]] = stat

    pipelines = []
    for subj in subjects:
        stat = stats_lookup.get(subj.id, {})
        total = stat.get("total", 0)
        published = stat.get("published", 0)
        generated = stat.get("generated", 0)
        draft = stat.get("draft", 0)

        pipelines.append(
            {
                "subject_id": str(subj.id),
                "subject_name": subj.name,
                "total_chapters": total,
                "published": published,
                "generated": generated,
                "draft": draft,
                "completion_pct": round(
                    (published / total * 100) if total > 0 else 0, 1
                ),
            }
        )

    return {"pipelines": pipelines, "total_subjects": len(pipelines)}


@router.post("/seo/bulk-generate")
async def seo_bulk_generate(request: Request, body: BulkGenerateRequest):
    """Generate SEO pages for given topic_ids."""
    await _validate_admin_session(request)
    await _csrf_check(request)

    # Collect topics from chapters
    topics_to_generate = []
    for topic_id in body.topic_ids:
        chapters = await Chapter.find({"published_topics.id": topic_id}).to_list()
        for ch in chapters:
            for t in ch.published_topics:
                if t.id == topic_id:
                    topics_to_generate.append(
                        {
                            "title": t.title,
                            "topic_slug": t.topic_slug,
                            "definition": t.definition,
                        }
                    )

    if not topics_to_generate:
        raise HTTPException(status_code=404, detail="No topics found for given IDs")

    try:
        results = await seo_generator_service.generate_seo_pages(topics_to_generate)
        return {
            "status": "generated",
            "topics_processed": len(results),
            "results": results,
        }
    except Exception as e:
        logger.error(f"Bulk generate error: {e}")
        raise HTTPException(status_code=500, detail="Bulk generation failed")


@router.get("/seo/coverage")
async def seo_coverage(request: Request, subject_id: Optional[str] = Query(None)):
    """Published vs draft vs generated coverage per subject."""
    await _validate_admin_session(request)

    try:
        query = {}
        if subject_id:
            query["subject_id"] = PydanticObjectId(subject_id)
        chapters = await Chapter.find(query).to_list()
    except Exception:
        chapters = []

    coverage = {
        "total": len(chapters),
        "published": sum(1 for c in chapters if c.status == "published"),
        "generated": sum(1 for c in chapters if c.status == "generated"),
        "draft": sum(1 for c in chapters if c.status == "draft"),
    }
    coverage["coverage_pct"] = round(
        (coverage["published"] / coverage["total"] * 100)
        if coverage["total"] > 0
        else 0,
        1,
    )

    return coverage


@router.post("/seo/entity/refresh")
async def seo_entity_refresh(request: Request):
    """Re-probe entity health signals and record scan."""
    await _validate_admin_session(request)
    await _csrf_check(request)

    try:
        total = await Chapter.count()
        published = await Chapter.find({"status": "published"}).count()
        generated = await Chapter.find({"status": "generated"}).count()
    except Exception:
        total = published = generated = 0

    scan_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total": total,
        "published": published,
        "generated": generated,
        "health_score": round((published / total * 100) if total > 0 else 0, 1),
    }
    _scan_history.append(scan_entry)

    return {"status": "refreshed", "scan": scan_entry}


@router.get("/seo/deep-scan-history")
async def seo_deep_scan_history(request: Request):
    """Full history of entity scans."""
    await _validate_admin_session(request)

    return {"scans": _scan_history, "total": len(_scan_history)}


@router.post("/seo/extract")
async def seo_extract_topics(request: Request, body: ExtractRequest):
    """AI-extract topics from chapter content."""
    await _validate_admin_session(request)
    await _csrf_check(request)

    try:
        topics = await seo_generator_service.extract_topics_from_content(
            body.chapter_id
        )
        return {"chapter_id": body.chapter_id, "topics": topics, "count": len(topics)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Extract topics error: {e}")
        raise HTTPException(status_code=500, detail="Extraction failed")


@router.post("/seo/generate")
async def seo_generate_pages(request: Request, body: GenerateSEORequest):
    """Generate SEO page variations for topics."""
    await _validate_admin_session(request)
    await _csrf_check(request)

    try:
        results = await seo_generator_service.generate_seo_pages(body.topics)
        return {"status": "generated", "results": results, "count": len(results)}
    except Exception as e:
        logger.error(f"SEO generate error: {e}")
        raise HTTPException(status_code=500, detail="SEO generation failed")


@router.post("/seo/regenerate-sitemap")
async def seo_regenerate_sitemap(request: Request):
    """Rebuild sitemaps from published chapters."""
    await _validate_admin_session(request)
    await _csrf_check(request)

    try:
        sitemap_xml = await content_publisher_service.regenerate_sitemap()
        return {
            "status": "regenerated",
            "sitemap_length": len(sitemap_xml),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Sitemap regeneration error: {e}")
        raise HTTPException(status_code=500, detail="Sitemap regeneration failed")
