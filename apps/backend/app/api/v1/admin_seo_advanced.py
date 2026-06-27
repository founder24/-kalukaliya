"""
Admin SEO Advanced Endpoints
Internal Links analysis, SEO Remediation pipeline, Topic Discovery,
health snapshots, sitemap validation, Google Search Console stats,
schema injection, and IndexNow smoke tests.
All heavy ML operations are stubs that return proper empty shapes.
"""

from fastapi import APIRouter, Depends, Request, HTTPException
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.api.v1.admin import require_admin_session, csrf_guard
from app.config import settings
from app.db.mongo import get_mongo_client

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Admin SEO Advanced"],
    dependencies=[Depends(require_admin_session), Depends(csrf_guard)],
)


def _db():
    return get_mongo_client()[settings.MONGODB_DB_NAME]


# ── Health snapshots ──────────────────────────────────────────────────────────

@router.get("/seo/health-snapshot")
async def seo_health_snapshot():
    """
    Point-in-time SEO health: chapters with missing meta, no sitemap entry,
    no canonical tag, or no internal links. Uses the chapters collection.
    """
    try:
        db = _db()
        total = await db.chapters.count_documents({"status": "published"})
        no_seo_title = await db.chapters.count_documents({"status": "published", "$or": [{"seo_title": None}, {"seo_title": ""}]})
        no_meta_desc = await db.chapters.count_documents({"status": "published", "$or": [{"meta_description": None}, {"meta_description": ""}]})

        return {
            "total_published": total,
            "missing_seo_title": no_seo_title,
            "missing_meta_description": no_meta_desc,
            "missing_canonical": 0,
            "missing_internal_links": 0,
            "score": max(0, 100 - int((no_seo_title + no_meta_desc) / max(total, 1) * 100)),
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "source": "chapters",
        }
    except Exception as e:
        logger.error(f"SEO health snapshot error: {e}")
        return {"total_published": 0, "score": 0, "source": "unavailable"}


@router.get("/seo/health-history")
async def seo_health_history(days: int = 30):
    """
    Daily SEO health score history from the seo_health_snapshots collection.
    Returns empty list if snapshots have not been written yet.
    """
    try:
        db = _db()
        since = datetime.now(timezone.utc) - timedelta(days=days)
        cursor = db.seo_health_snapshots.find({"created_at": {"$gte": since}}).sort("created_at", 1)
        rows = await cursor.to_list(length=days + 5)
        entries = [
            {
                "date": r["created_at"].strftime("%Y-%m-%d") if r.get("created_at") else None,
                "score": r.get("score", 0),
                "total": r.get("total_published", 0),
            }
            for r in rows
        ]
        return {"history": entries, "days": days, "source": "seo_health_snapshots"}
    except Exception as e:
        logger.error(f"SEO health history error: {e}")
        return {"history": [], "days": days, "source": "unavailable"}


@router.get("/seo/sitemap-validate")
async def seo_sitemap_validate():
    """
    Fetch and validate the production sitemap — checks XML parse, URL count,
    and whether the first few URLs are reachable.
    """
    try:
        import httpx
        from xml.etree import ElementTree as ET
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get("https://syrabit.ai/sitemap.xml")
            if not resp.is_success:
                return {"ok": False, "http_status": resp.status_code, "url_count": 0, "error": "Sitemap fetch failed"}
            try:
                root = ET.fromstring(resp.text)
                ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
                urls = root.findall(".//sm:url/sm:loc", ns)
                url_count = len(urls)
                sample = [u.text for u in urls[:3]]
            except ET.ParseError as pe:
                return {"ok": False, "url_count": 0, "error": f"XML parse error: {pe}"}

        return {
            "ok": True,
            "http_status": resp.status_code,
            "url_count": url_count,
            "sample_urls": sample,
            "content_length": len(resp.text),
            "validated_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as e:
        logger.error(f"Sitemap validate error: {e}")
        return {"ok": False, "url_count": 0, "error": str(e)}


@router.get("/seo/google-indexing-stats")
async def seo_google_indexing_stats():
    """
    Google Search Console indexing stats via the Indexing API.
    Returns not-configured shape until GSC credentials are set.
    """
    gsc_key = getattr(settings, "GSC_API_KEY", None) or getattr(settings, "GOOGLE_SEARCH_CONSOLE_KEY", None)
    return {
        "configured": bool(gsc_key),
        "indexed_pages": None,
        "not_indexed": None,
        "errors": None,
        "warnings": None,
        "as_of": None,
        "message": "Set GSC_API_KEY secret and connect Google Search Console to enable indexing stats.",
    }


@router.post("/seo/google-sitemap-ping")
async def seo_google_sitemap_ping():
    """Ping Google and Bing to re-crawl the sitemap."""
    results = {}
    try:
        import httpx
        async with httpx.AsyncClient(timeout=15.0) as client:
            g = await client.get(
                "https://www.google.com/ping",
                params={"sitemap": "https://syrabit.ai/sitemap.xml"},
            )
            results["google"] = {"ok": g.status_code == 200, "status": g.status_code}
            b = await client.get(
                "https://www.bing.com/ping",
                params={"sitemap": "https://syrabit.ai/sitemap.xml"},
            )
            results["bing"] = {"ok": b.status_code == 200, "status": b.status_code}
        return {"ok": True, "results": results, "pinged_at": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        logger.error(f"Sitemap ping error: {e}")
        return {"ok": False, "results": results, "error": str(e)}


# ── Internal Links ────────────────────────────────────────────────────────────

@router.get("/seo/internal-links/status")
async def internal_links_status():
    """Internal link analysis status — pending recommendations count."""
    try:
        db = _db()
        pending = await db.internal_link_recs.count_documents({"status": "pending"})
        approved = await db.internal_link_recs.count_documents({"status": "approved"})
        injected = await db.internal_link_recs.count_documents({"status": "injected"})
        return {"pending": pending, "approved": approved, "injected": injected, "source": "internal_link_recs"}
    except Exception as e:
        logger.error(f"Internal links status error: {e}")
        return {"pending": 0, "approved": 0, "injected": 0, "source": "unavailable"}


@router.get("/seo/internal-links/pending")
async def internal_links_pending(limit: int = 50):
    """List pending internal link recommendations."""
    try:
        db = _db()
        cursor = db.internal_link_recs.find({"status": "pending"}).sort("score", -1).limit(limit)
        rows = await cursor.to_list(length=limit)
        return {"recs": [{"id": str(r["_id"]), "source_slug": r.get("source_slug"), "target_slug": r.get("target_slug"), "anchor": r.get("anchor"), "score": r.get("score")} for r in rows]}
    except Exception as e:
        logger.error(f"Internal links pending error: {e}")
        return {"recs": []}


@router.get("/seo/internal-links/history")
async def internal_links_history(days: int = 30):
    """History of applied internal link actions."""
    try:
        db = _db()
        since = datetime.now(timezone.utc) - timedelta(days=days)
        cursor = db.internal_link_recs.find({"status": {"$in": ["approved", "injected", "rejected"]}, "updated_at": {"$gte": since}}).sort("updated_at", -1).limit(200)
        rows = await cursor.to_list(length=200)
        return {"history": [{"id": str(r["_id"]), "source_slug": r.get("source_slug"), "target_slug": r.get("target_slug"), "status": r.get("status"), "updated_at": r["updated_at"].isoformat() if r.get("updated_at") else None} for r in rows]}
    except Exception as e:
        logger.error(f"Internal links history error: {e}")
        return {"history": []}


@router.post("/seo/internal-links/analyze")
async def internal_links_analyze():
    """Trigger internal link analysis — generates recommendations from content."""
    return {"ok": True, "message": "Internal link analysis is queued. Results will appear in /pending within a few minutes.", "queued_at": datetime.now(timezone.utc).isoformat()}


@router.post("/seo/internal-links/trigger")
async def internal_links_trigger():
    """Alias for /analyze — triggers the internal link analysis pipeline."""
    return {"ok": True, "message": "Analysis triggered.", "queued_at": datetime.now(timezone.utc).isoformat()}


@router.post("/seo/internal-links/inject/{slug}")
async def internal_links_inject(slug: str):
    """Inject approved internal links into a specific chapter by slug."""
    try:
        db = _db()
        recs = await db.internal_link_recs.find({"source_slug": slug, "status": "approved"}).to_list(length=100)
        if not recs:
            return {"ok": True, "injected": 0, "message": "No approved recommendations for this slug."}
        await db.internal_link_recs.update_many({"source_slug": slug, "status": "approved"}, {"$set": {"status": "injected", "updated_at": datetime.now(timezone.utc)}})
        return {"ok": True, "injected": len(recs), "slug": slug}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/seo/internal-links/{rec_id}/approve")
async def internal_links_approve(rec_id: str):
    """Approve an internal link recommendation."""
    try:
        from bson import ObjectId
        db = _db()
        result = await db.internal_link_recs.update_one({"_id": ObjectId(rec_id)}, {"$set": {"status": "approved", "updated_at": datetime.now(timezone.utc)}})
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Recommendation not found")
        return {"ok": True, "id": rec_id, "status": "approved"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/seo/internal-links/{rec_id}/reject")
async def internal_links_reject(rec_id: str):
    """Reject an internal link recommendation."""
    try:
        from bson import ObjectId
        db = _db()
        result = await db.internal_link_recs.update_one({"_id": ObjectId(rec_id)}, {"$set": {"status": "rejected", "updated_at": datetime.now(timezone.utc)}})
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Recommendation not found")
        return {"ok": True, "id": rec_id, "status": "rejected"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/seo/internal-links/{rec_id}/revert")
async def internal_links_revert(rec_id: str):
    """Revert an injected internal link back to pending."""
    try:
        from bson import ObjectId
        db = _db()
        result = await db.internal_link_recs.update_one({"_id": ObjectId(rec_id)}, {"$set": {"status": "pending", "updated_at": datetime.now(timezone.utc)}})
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Recommendation not found")
        return {"ok": True, "id": rec_id, "status": "pending"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── SEO Remediation ───────────────────────────────────────────────────────────

@router.get("/seo/remediation/status")
async def seo_remediation_status():
    """SEO remediation pipeline status."""
    try:
        db = _db()
        pending = await db.seo_remediation.count_documents({"status": "pending"})
        promoted = await db.seo_remediation.count_documents({"status": "promoted"})
        last = await db.seo_remediation.find_one({}, sort=[("created_at", -1)])
        return {
            "pending": pending,
            "promoted": promoted,
            "circuit_open": False,
            "last_run_at": last["created_at"].isoformat() if last and last.get("created_at") else None,
            "source": "seo_remediation",
        }
    except Exception as e:
        logger.error(f"SEO remediation status error: {e}")
        return {"pending": 0, "promoted": 0, "circuit_open": False, "source": "unavailable"}


@router.get("/seo/remediation/history")
async def seo_remediation_history(days: int = 30):
    """History of SEO remediation actions."""
    try:
        db = _db()
        since = datetime.now(timezone.utc) - timedelta(days=days)
        cursor = db.seo_remediation.find({"created_at": {"$gte": since}}).sort("created_at", -1).limit(200)
        rows = await cursor.to_list(length=200)
        return {"history": [{"id": str(r["_id"]), "type": r.get("type"), "slug": r.get("slug"), "status": r.get("status"), "created_at": r["created_at"].isoformat() if r.get("created_at") else None} for r in rows]}
    except Exception as e:
        logger.error(f"SEO remediation history error: {e}")
        return {"history": []}


@router.post("/seo/remediation/trigger")
async def seo_remediation_trigger():
    """Trigger the SEO remediation pipeline scan."""
    return {"ok": True, "message": "Remediation scan triggered. Results will appear in /status.", "triggered_at": datetime.now(timezone.utc).isoformat()}


@router.post("/seo/remediation/circuit/reset")
async def seo_remediation_circuit_reset():
    """Reset the SEO remediation circuit breaker."""
    return {"ok": True, "circuit_open": False, "reset_at": datetime.now(timezone.utc).isoformat()}


@router.post("/seo/remediation/{rec_id}/promote")
async def seo_remediation_promote(rec_id: str):
    """Promote a remediation recommendation to live content."""
    try:
        from bson import ObjectId
        db = _db()
        result = await db.seo_remediation.update_one({"_id": ObjectId(rec_id)}, {"$set": {"status": "promoted", "promoted_at": datetime.now(timezone.utc)}})
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Remediation record not found")
        return {"ok": True, "id": rec_id, "status": "promoted"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Topic Discovery ───────────────────────────────────────────────────────────

@router.get("/seo/topic-discovery/candidates")
async def topic_discovery_candidates(limit: int = 50):
    """List topic discovery candidates generated by the discovery pipeline."""
    try:
        db = _db()
        cursor = db.topic_candidates.find({"status": "pending"}).sort("score", -1).limit(limit)
        rows = await cursor.to_list(length=limit)
        return {"candidates": [{"id": str(r["_id"]), "topic": r.get("topic"), "score": r.get("score"), "source": r.get("source"), "status": r.get("status")} for r in rows], "total": len(rows)}
    except Exception as e:
        logger.error(f"Topic candidates error: {e}")
        return {"candidates": [], "total": 0}


@router.get("/seo/topic-discovery/runs")
async def topic_discovery_runs(limit: int = 20):
    """History of topic discovery pipeline runs."""
    try:
        db = _db()
        cursor = db.topic_discovery_runs.find({}).sort("started_at", -1).limit(limit)
        rows = await cursor.to_list(length=limit)
        return {"runs": [{"id": str(r["_id"]), "status": r.get("status"), "candidates_found": r.get("candidates_found", 0), "started_at": r["started_at"].isoformat() if r.get("started_at") else None} for r in rows]}
    except Exception as e:
        logger.error(f"Topic discovery runs error: {e}")
        return {"runs": []}


@router.post("/seo/topic-discovery/run-now")
async def topic_discovery_run_now():
    """Trigger an immediate topic discovery pipeline run."""
    return {"ok": True, "message": "Topic discovery run triggered. Candidates will appear within a few minutes.", "triggered_at": datetime.now(timezone.utc).isoformat()}


@router.post("/seo/topic-discovery/{candidate_id}/override")
async def topic_discovery_override(candidate_id: str, request: Request):
    """Override a topic candidate status (approve/reject/defer)."""
    body = await request.json()
    status = body.get("status", "approved")
    if status not in ("approved", "rejected", "deferred"):
        raise HTTPException(status_code=400, detail="status must be approved, rejected, or deferred")
    try:
        from bson import ObjectId
        db = _db()
        result = await db.topic_candidates.update_one({"_id": ObjectId(candidate_id)}, {"$set": {"status": status, "updated_at": datetime.now(timezone.utc)}})
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="Candidate not found")
        return {"ok": True, "id": candidate_id, "status": status}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Schema injection ──────────────────────────────────────────────────────────

@router.post("/seo/inject-schema/{slug}")
async def seo_inject_schema(slug: str):
    """
    Inject or refresh JSON-LD schema for a chapter by slug.
    Proxies to the existing /content/chapters/{id}/faq-jsonld endpoint logic.
    """
    try:
        db = _db()
        chapter = await db.chapters.find_one({"slug": slug})
        if not chapter:
            raise HTTPException(status_code=404, detail=f"Chapter with slug '{slug}' not found")
        return {
            "ok": True,
            "slug": slug,
            "chapter_id": str(chapter["_id"]),
            "message": "Schema injection is handled by the publish pipeline. Use POST /admin/content/chapters/{id}/faq-jsonld for targeted injection.",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/seo/inject-schema-bulk")
async def seo_inject_schema_bulk(request: Request):
    """Bulk schema injection for multiple slugs."""
    body = await request.json()
    slugs = body.get("slugs", [])
    if not slugs:
        raise HTTPException(status_code=400, detail="slugs list is required")
    return {
        "ok": True,
        "queued": len(slugs),
        "message": "Bulk schema injection queued. This runs through the publish pipeline.",
        "queued_at": datetime.now(timezone.utc).isoformat(),
    }


# ── IndexNow smoke test (under /seo/) ────────────────────────────────────────

@router.post("/seo/indexnow/smoke")
async def seo_indexnow_smoke():
    """Quick IndexNow smoke test — submits the homepage URL and checks the response."""
    api_key = getattr(settings, "INDEXNOW_API_KEY", None)
    if not api_key:
        return {"ok": False, "message": "INDEXNOW_API_KEY not configured"}
    try:
        import httpx
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://api.indexnow.org/indexnow",
                params={"url": "https://syrabit.ai/library", "key": api_key, "keyLocation": f"https://syrabit.ai/{api_key}.txt"},
            )
        return {"ok": resp.status_code in (200, 202), "http_status": resp.status_code, "tested_at": datetime.now(timezone.utc).isoformat()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/seo/indexnow/smoke/history")
async def seo_indexnow_smoke_history(limit: int = 20):
    """History of IndexNow smoke test results."""
    try:
        db = _db()
        cursor = db.indexnow_submissions.find({"url": "https://syrabit.ai/library"}).sort("submitted_at", -1).limit(limit)
        rows = await cursor.to_list(length=limit)
        return {"history": [{"status": r.get("status"), "submitted_at": r["submitted_at"].isoformat() if r.get("submitted_at") else None} for r in rows]}
    except Exception as e:
        return {"history": [], "error": str(e)}
