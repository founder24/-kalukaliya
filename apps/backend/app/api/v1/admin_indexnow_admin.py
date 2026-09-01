"""
Admin IndexNow Endpoints
IndexNow submission history, manual ping, bulk backfill, and smoke test.
Uses the indexnow_submissions MongoDB collection for history.
"""

from fastapi import APIRouter, Depends, Request, HTTPException
import logging
from datetime import datetime, timezone, timedelta

from app.api.v1.admin import require_admin_session, csrf_guard
from app.config import settings
from app.core.security import is_safe_url
from app.db.mongo import get_mongo_client

logger = logging.getLogger(__name__)
INDEXNOW_SITE_HOSTS = {"syrabit.ai"}

router = APIRouter(
    tags=["Admin IndexNow"],
    dependencies=[Depends(require_admin_session), Depends(csrf_guard)],
)


def _db():
    return get_mongo_client()[settings.MONGODB_DB_NAME]


async def _require_trusted_site_url(url: object) -> str:
    if not isinstance(url, str):
        raise HTTPException(status_code=400, detail="Each URL must be a string")
    candidate = url.strip()
    if not await is_safe_url(
        candidate,
        allowed_schemes=["https"],
        allowed_hosts=INDEXNOW_SITE_HOSTS,
    ):
        raise HTTPException(
            status_code=400,
            detail="URLs must use HTTPS and the syrabit.ai host",
        )
    return candidate


@router.get("/indexnow/status")
async def indexnow_status():
    """IndexNow integration status and recent submission summary."""
    api_key = getattr(settings, "INDEXNOW_API_KEY", None)
    if not api_key:
        return {
            "configured": False,
            "api_key_set": False,
            "host": "syrabit.ai",
            "total_submitted": 0,
            "last_submitted_at": None,
            "message": "INDEXNOW_API_KEY not configured.",
        }
    try:
        db = _db()
        total = await db.indexnow_submissions.count_documents({})
        last = await db.indexnow_submissions.find_one({}, sort=[("submitted_at", -1)])
        success = await db.indexnow_submissions.count_documents({"status": "ok"})
        failed = await db.indexnow_submissions.count_documents({"status": {"$ne": "ok"}})
        return {
            "configured": True,
            "api_key_set": True,
            "host": "syrabit.ai",
            "total_submitted": total,
            "successful": success,
            "failed": failed,
            "last_submitted_at": last["submitted_at"].isoformat() if last and last.get("submitted_at") else None,
        }
    except Exception as e:
        logger.error(f"IndexNow status error: {e}")
        return {"configured": bool(api_key), "api_key_set": bool(api_key), "total_submitted": 0, "source": "unavailable"}


@router.post("/indexnow/ping")
async def indexnow_ping(request: Request):
    """
    Submit a single URL to IndexNow for immediate indexing.
    Body: { "url": "https://syrabit.ai/..." }
    """
    body = await request.json()
    url = body.get("url", "")
    if not url:
        raise HTTPException(status_code=400, detail="url is required")
    url = await _require_trusted_site_url(url)
    api_key = getattr(settings, "INDEXNOW_API_KEY", None)
    if not api_key:
        raise HTTPException(status_code=503, detail="INDEXNOW_API_KEY not configured")

    try:
        import httpx
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            resp = await client.get(
                "https://api.indexnow.org/indexnow",
                params={"url": url, "key": api_key, "keyLocation": f"https://syrabit.ai/{api_key}.txt"},
            )
        db = _db()
        await db.indexnow_submissions.insert_one({
            "url": url,
            "status": "ok" if resp.status_code in (200, 202) else f"http_{resp.status_code}",
            "http_status": resp.status_code,
            "submitted_at": datetime.now(timezone.utc),
            "batch": False,
        })
        return {"ok": resp.status_code in (200, 202), "http_status": resp.status_code, "url": url}
    except Exception as e:
        logger.error(f"IndexNow ping error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/indexnow/submit-urls")
async def indexnow_submit_urls(request: Request):
    """
    Batch submit up to 10,000 URLs to IndexNow.
    Body: { "urls": ["https://syrabit.ai/..."] }
    """
    body = await request.json()
    urls = body.get("urls", [])
    if not isinstance(urls, list) or not urls:
        raise HTTPException(status_code=400, detail="urls list is required")
    if len(urls) > 10000:
        raise HTTPException(status_code=400, detail="At most 10,000 URLs may be submitted")
    urls = [await _require_trusted_site_url(url) for url in urls]
    api_key = getattr(settings, "INDEXNOW_API_KEY", None)
    if not api_key:
        raise HTTPException(status_code=503, detail="INDEXNOW_API_KEY not configured")

    try:
        import httpx
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            resp = await client.post(
                "https://api.indexnow.org/indexnow",
                json={
                    "host": "syrabit.ai",
                    "key": api_key,
                    "keyLocation": f"https://syrabit.ai/{api_key}.txt",
                    "urlList": urls,
                },
                headers={"Content-Type": "application/json"},
            )
        db = _db()
        now = datetime.now(timezone.utc)
        docs = [{"url": u, "status": "ok" if resp.status_code in (200, 202) else f"http_{resp.status_code}", "submitted_at": now, "batch": True} for u in urls]
        if docs:
            await db.indexnow_submissions.insert_many(docs)
        return {"ok": resp.status_code in (200, 202), "submitted": len(urls), "http_status": resp.status_code}
    except Exception as e:
        logger.error(f"IndexNow submit-urls error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/indexnow/history")
async def indexnow_history(days: int = 7, limit: int = 100):
    """Recent IndexNow submission history."""
    try:
        db = _db()
        since = datetime.now(timezone.utc) - timedelta(days=days)
        cursor = db.indexnow_submissions.find({"submitted_at": {"$gte": since}}).sort("submitted_at", -1).limit(limit)
        rows = await cursor.to_list(length=limit)
        entries = []
        for r in rows:
            entries.append({
                "id": str(r["_id"]),
                "url": r.get("url"),
                "status": r.get("status"),
                "batch": r.get("batch", False),
                "submitted_at": r["submitted_at"].isoformat() if r.get("submitted_at") else None,
            })
        return {"entries": entries, "days": days}
    except Exception as e:
        logger.error(f"IndexNow history error: {e}")
        return {"entries": [], "days": days}


@router.post("/indexnow/backfill-all")
async def indexnow_backfill_all():
    """
    Queue a full-site IndexNow backfill — submits all published chapter URLs.
    Returns immediately with a job_id; progress via /indexnow/backfill-progress.
    """
    import asyncio, uuid
    job_id = str(uuid.uuid4())
    api_key = getattr(settings, "INDEXNOW_API_KEY", None)
    if not api_key:
        raise HTTPException(status_code=503, detail="INDEXNOW_API_KEY not configured")

    async def _run_backfill():
        try:
            from app.db.mongo import get_mongo_client as _get_mongo
            import httpx
            db = _get_mongo()[settings.MONGODB_DB_NAME]
            await db.indexnow_jobs.update_one(
                {"job_id": job_id},
                {"$set": {"status": "running", "started_at": datetime.now(timezone.utc)}},
                upsert=True,
            )
            chapters = await db.chapters.find({"status": "published"}, {"slug": 1, "board": 1}).to_list(length=None)
            urls = [f"https://syrabit.ai/{c.get('board','ahsec')}/hs-1st-year/{c.get('slug','')}" for c in chapters]
            urls = [u for u in urls if u.endswith("/") is False][:10000]
            batch_size = 1000
            submitted = 0
            now = datetime.now(timezone.utc)
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
                for i in range(0, len(urls), batch_size):
                    batch = urls[i:i + batch_size]
                    resp = await client.post(
                        "https://api.indexnow.org/indexnow",
                        json={"host": "syrabit.ai", "key": api_key, "keyLocation": f"https://syrabit.ai/{api_key}.txt", "urlList": batch},
                        headers={"Content-Type": "application/json"},
                    )
                    docs = [{"url": u, "status": "ok" if resp.status_code in (200, 202) else f"http_{resp.status_code}", "submitted_at": now, "batch": True, "job_id": job_id} for u in batch]
                    if docs:
                        await db.indexnow_submissions.insert_many(docs)
                    submitted += len(batch)
            await db.indexnow_jobs.update_one(
                {"job_id": job_id},
                {"$set": {"status": "done", "submitted": submitted, "completed_at": datetime.now(timezone.utc)}},
            )
        except Exception as e:
            try:
                from app.db.mongo import get_mongo_client as _get_mongo
                db = _get_mongo()[settings.MONGODB_DB_NAME]
                await db.indexnow_jobs.update_one({"job_id": job_id}, {"$set": {"status": "failed", "error": str(e)}})
            except Exception:
                pass

    asyncio.create_task(_run_backfill())
    return {"ok": True, "job_id": job_id, "message": "Backfill started — check /indexnow/backfill-progress for status."}


@router.get("/indexnow/backfill-progress")
async def indexnow_backfill_progress():
    """Status of the most recent IndexNow backfill job."""
    try:
        db = _db()
        job = await db.indexnow_jobs.find_one({}, sort=[("started_at", -1)])
        if not job:
            return {"status": "no_job", "submitted": 0}
        return {
            "job_id": job.get("job_id"),
            "status": job.get("status"),
            "submitted": job.get("submitted", 0),
            "started_at": job["started_at"].isoformat() if job.get("started_at") else None,
            "completed_at": job["completed_at"].isoformat() if job.get("completed_at") else None,
            "error": job.get("error"),
        }
    except Exception as e:
        logger.error(f"IndexNow backfill progress error: {e}")
        return {"status": "unavailable", "error": str(e)}


@router.get("/indexnow/stats")
async def indexnow_stats():
    """Aggregate IndexNow submission stats across all jobs."""
    try:
        db = _db()
        pipeline = [
            {"$group": {
                "_id": "$status",
                "count": {"$sum": 1},
                "total_submitted": {"$sum": "$submitted"},
            }},
        ]
        rows = await (await db.indexnow_jobs.aggregate(pipeline)).to_list(length=10)
        by_status = {r["_id"]: {"jobs": r["count"], "urls": r.get("total_submitted", 0)} for r in rows}
        total_urls = sum(r.get("total_submitted", 0) for r in rows)
        return {
            "total_jobs": sum(r["count"] for r in rows),
            "total_urls_submitted": total_urls,
            "by_status": by_status,
            "source": "mongodb" if rows else "empty",
        }
    except Exception as e:
        logger.error(f"indexnow/stats error: {e}")
        return {"total_jobs": 0, "total_urls_submitted": 0, "by_status": {}, "source": "unavailable"}
