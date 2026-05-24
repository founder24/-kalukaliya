"""
SEO/GEO/AEO Pipeline Endpoints (Layer 4)
Handles topic extraction, page generation, quality management, and publishing.
"""

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request, Query

from app.api.v1.admin import _validate_admin_session, _csrf_check
from app.config import settings
from app.db.mongo import get_mongo_client
from app.services.ai.vertex_client import vertex_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin SEO Pipeline"])


def _get_db():
    return get_mongo_client()[settings.MONGODB_DB_NAME]


def _auto_slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# ---------------------------------------------------------------------------
# Background job helpers
# ---------------------------------------------------------------------------


async def _run_pipeline_job(job_id: str, subject_id: str = None, force: bool = False):
    """Background task for full pipeline execution."""
    db = _get_db()
    try:
        await db.seo_jobs.update_one(
            {"_id": ObjectId(job_id)},
            {"$set": {"status": "running", "updated_at": datetime.now(timezone.utc)}},
        )
        query = {}
        if subject_id:
            query["subject_id"] = subject_id
        chapters = await db.chapters.find(query).to_list(length=500)
        total = len(chapters)
        done = 0
        errors = 0
        skipped = 0

        for ch in chapters:
            try:
                ch_id = str(ch["_id"])
                existing = await db.seo_topics.find_one({"chapter_id": ch_id})
                if existing and not force:
                    skipped += 1
                    done += 1
                    continue
                title = ch.get("title", "")
                topics_list = ch.get("topics", [])
                if not topics_list:
                    prompt = f"Extract key topics from the chapter titled: {title}"
                    try:
                        import json as _json
                        result = await vertex_client.generate(
                            "You are an education expert. Return a JSON array of objects with title and definition fields.",
                            prompt,
                        )
                        topics_list = _json.loads(result) if result.startswith("[") else []
                    except Exception:
                        topics_list = []

                for t in topics_list:
                    t_title = t.get("title", "") if isinstance(t, dict) else str(t)
                    if not t_title:
                        continue
                    exists = await db.seo_topics.find_one({"title": t_title, "chapter_id": ch_id})
                    if exists and not force:
                        continue
                    await db.seo_topics.insert_one({
                        "title": t_title,
                        "slug": _auto_slug(t_title),
                        "definition": t.get("definition", "") if isinstance(t, dict) else "",
                        "subject_id": ch.get("subject_id", ""),
                        "subject_name": ch.get("subject_name", ""),
                        "chapter_id": ch_id,
                        "chapter_title": title,
                        "status": "ready",
                        "created_at": datetime.now(timezone.utc),
                        "updated_at": datetime.now(timezone.utc),
                    })
                done += 1
            except Exception as e:
                logger.error(f"Pipeline job topic extraction error: {e}")
                errors += 1
                done += 1

            await db.seo_jobs.update_one(
                {"_id": ObjectId(job_id)},
                {"$set": {"done": done, "total": total, "errors": errors, "skipped": skipped, "current": f"Extracting topics {done}/{total}", "updated_at": datetime.now(timezone.utc)}},
            )

        # Generate pages for new topics
        new_topics = await db.seo_topics.find({"status": "ready"}).to_list(length=1000)
        page_types = ["notes", "important-questions", "mcqs", "summary", "revision"]
        gen_total = len(new_topics) * len(page_types)
        gen_done = 0

        await db.seo_jobs.update_one(
            {"_id": ObjectId(job_id)},
            {"$set": {"total": total + gen_total, "current": "Generating pages...", "updated_at": datetime.now(timezone.utc)}},
        )

        for topic in new_topics:
            for pt in page_types:
                try:
                    existing_page = await db.seo_pages.find_one({"topic_id": str(topic["_id"]), "page_type": pt})
                    if existing_page and not force:
                        gen_done += 1
                        continue
                    sys_prompt = f"You are an SEO content expert. Generate a {pt} page for an educational topic."
                    t_title = topic["title"]
                    t_def = topic.get("definition", "")
                    user_msg = f"Topic: {t_title}\nDefinition: {t_def}\nGenerate comprehensive {pt} content optimized for search engines."
                    gen_content = await vertex_client.generate(sys_prompt, user_msg)
                    meta_prompt = "Generate a meta description (max 160 chars) for the following content."
                    meta_desc = await vertex_client.generate(meta_prompt, gen_content[:500])
                    await db.seo_pages.insert_one({
                        "topic_id": str(topic["_id"]),
                        "topic_title": t_title,
                        "chapter_id": topic.get("chapter_id", ""),
                        "subject_id": topic.get("subject_id", ""),
                        "subject_name": topic.get("subject_name", ""),
                        "page_type": pt,
                        "title": f"{t_title} - {pt.replace('-', ' ').title()}",
                        "content": gen_content,
                        "meta_description": meta_desc[:160] if meta_desc else "",
                        "answer_summary": gen_content[:300],
                        "status": "draft",
                        "quality_score": 0,
                        "geo_score": 0,
                        "combined_score": 0,
                        "generated_at": datetime.now(timezone.utc),
                        "updated_at": datetime.now(timezone.utc),
                    })
                except Exception as e:
                    logger.error(f"Page generation error: {e}")
                    errors += 1
                gen_done += 1

            await db.seo_jobs.update_one(
                {"_id": ObjectId(job_id)},
                {"$set": {"done": done + gen_done, "errors": errors, "current": f"Generating pages {gen_done}/{gen_total}", "updated_at": datetime.now(timezone.utc)}},
            )

        await db.seo_jobs.update_one(
            {"_id": ObjectId(job_id)},
            {"$set": {"status": "done", "done": done + gen_done, "errors": errors, "skipped": skipped, "updated_at": datetime.now(timezone.utc)}},
        )
    except Exception as e:
        logger.error(f"Pipeline job failed: {e}")
        await db.seo_jobs.update_one(
            {"_id": ObjectId(job_id)},
            {"$set": {"status": "error", "error": str(e), "updated_at": datetime.now(timezone.utc)}},
        )


async def _run_backfill_notes_job(job_id: str):
    """Background task for backfilling notes on topics missing them."""
    db = _get_db()
    try:
        await db.seo_jobs.update_one(
            {"_id": ObjectId(job_id)},
            {"$set": {"status": "running", "updated_at": datetime.now(timezone.utc)}},
        )
        topics = await db.seo_topics.find({"definition": {"$in": ["", None]}}).to_list(length=500)
        total = len(topics)
        done = 0
        errors = 0
        for topic in topics:
            try:
                result = await vertex_client.generate(
                    "You are an education expert. Provide a concise definition/note for this topic.",
                    f"Topic: {topic['title']}",
                )
                await db.seo_topics.update_one(
                    {"_id": topic["_id"]},
                    {"$set": {"definition": result, "updated_at": datetime.now(timezone.utc)}},
                )
            except Exception as e:
                logger.error(f"Backfill notes error: {e}")
                errors += 1
            done += 1
            await db.seo_jobs.update_one(
                {"_id": ObjectId(job_id)},
                {"$set": {"done": done, "total": total, "errors": errors, "current": f"Backfilling {done}/{total}", "updated_at": datetime.now(timezone.utc)}},
            )
        await db.seo_jobs.update_one(
            {"_id": ObjectId(job_id)},
            {"$set": {"status": "done", "updated_at": datetime.now(timezone.utc)}},
        )
    except Exception as e:
        await db.seo_jobs.update_one(
            {"_id": ObjectId(job_id)},
            {"$set": {"status": "error", "error": str(e), "updated_at": datetime.now(timezone.utc)}},
        )


# ---------------------------------------------------------------------------
# Stats & Lists Endpoints
# ---------------------------------------------------------------------------


@router.get("/seo/stats")
async def seo_stats(request: Request):
    """Return SEO pipeline statistics."""
    _validate_admin_session(request)
    db = _get_db()
    topics_total = await db.seo_topics.count_documents({})
    pages_total = await db.seo_pages.count_documents({})
    pages_published = await db.seo_pages.count_documents({"status": "published"})
    pages_draft = await db.seo_pages.count_documents({"status": "draft"})
    coverage_pct = round((pages_published / topics_total * 100) if topics_total > 0 else 0, 1)
    return {
        "topics_total": topics_total,
        "pages_total": pages_total,
        "pages_published": pages_published,
        "pages_draft": pages_draft,
        "coverage_pct": coverage_pct,
    }


@router.get("/seo/topics")
async def list_seo_topics(
    request: Request,
    subject_id: Optional[str] = None,
    search: Optional[str] = None,
):
    """List SEO topics with optional filters."""
    _validate_admin_session(request)
    db = _get_db()
    query = {}
    if subject_id:
        query["subject_id"] = subject_id
    if search:
        query["title"] = {"$regex": search, "$options": "i"}
    topics = await db.seo_topics.find(query).sort("created_at", -1).to_list(length=500)
    for t in topics:
        t["id"] = str(t.pop("_id"))
    return {"topics": topics}


@router.post("/seo/topics")
async def create_seo_topic(request: Request):
    """Create a new SEO topic."""
    _validate_admin_session(request)
    await _csrf_check(request)
    db = _get_db()
    body = await request.json()
    title = body.get("title", "")
    if not title:
        raise HTTPException(status_code=400, detail="Title is required")
    doc = {
        "title": title,
        "slug": _auto_slug(title),
        "definition": body.get("definition", ""),
        "subject_id": body.get("subject_id", ""),
        "subject_name": body.get("subject_name", ""),
        "chapter_id": body.get("chapter_id", ""),
        "chapter_title": body.get("chapter_title", ""),
        "status": "ready",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    result = await db.seo_topics.insert_one(doc)
    doc["id"] = str(result.inserted_id)
    return doc


@router.delete("/seo/topics/{topic_id}")
async def delete_seo_topic(request: Request, topic_id: str):
    """Delete an SEO topic."""
    _validate_admin_session(request)
    await _csrf_check(request)
    db = _get_db()
    result = await db.seo_topics.delete_one({"_id": ObjectId(topic_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Topic not found")
    return {"message": "Topic deleted"}


@router.get("/seo/pages")
async def list_seo_pages(
    request: Request,
    status: Optional[str] = None,
    page_type: Optional[str] = None,
):
    """List SEO pages with optional filters."""
    _validate_admin_session(request)
    db = _get_db()
    query = {}
    if status:
        query["status"] = status
    if page_type:
        query["page_type"] = page_type
    pages = await db.seo_pages.find(query).sort("generated_at", -1).to_list(length=500)
    for p in pages:
        p["id"] = str(p.pop("_id"))
    return {"pages": pages}


@router.patch("/seo/pages/{page_id}/status")
async def update_page_status(request: Request, page_id: str, status: str = Query(...)):
    """Update SEO page status."""
    _validate_admin_session(request)
    await _csrf_check(request)
    db = _get_db()
    update_fields = {"status": status, "updated_at": datetime.now(timezone.utc)}
    if status == "published":
        update_fields["published_at"] = datetime.now(timezone.utc)
    result = await db.seo_pages.update_one(
        {"_id": ObjectId(page_id)}, {"$set": update_fields}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Page not found")
    return {"message": "Status updated", "status": status}


# ---------------------------------------------------------------------------
# Topic Extraction & Generation
# ---------------------------------------------------------------------------


@router.post("/seo/extract-topics")
async def extract_topics(
    request: Request,
    subject_id: Optional[str] = None,
    force: bool = False,
):
    """Extract topics from chapters using AI."""
    _validate_admin_session(request)
    await _csrf_check(request)
    db = _get_db()
    query = {}
    if subject_id:
        query["subject_id"] = subject_id
    chapters = await db.chapters.find(query).to_list(length=500)
    created = 0
    skipped = 0
    errors = 0

    for ch in chapters:
        try:
            ch_id = str(ch["_id"])
            title = ch.get("title", "")
            topics_list = ch.get("topics", [])
            if not topics_list:
                try:
                    import json as _json
                    result = await vertex_client.generate(
                        "You are an education expert. Return a JSON array of objects with title and definition fields.",
                        f"Extract key topics from the chapter titled: {title}",
                    )
                    topics_list = _json.loads(result) if result.startswith("[") else []
                except Exception:
                    topics_list = []
                    errors += 1
                    continue

            for t in topics_list:
                t_title = t.get("title", "") if isinstance(t, dict) else str(t)
                if not t_title:
                    continue
                exists = await db.seo_topics.find_one({"title": t_title, "chapter_id": ch_id})
                if exists and not force:
                    skipped += 1
                    continue
                await db.seo_topics.insert_one({
                    "title": t_title,
                    "slug": _auto_slug(t_title),
                    "definition": t.get("definition", "") if isinstance(t, dict) else "",
                    "subject_id": ch.get("subject_id", ""),
                    "subject_name": ch.get("subject_name", ""),
                    "chapter_id": ch_id,
                    "chapter_title": title,
                    "status": "ready",
                    "created_at": datetime.now(timezone.utc),
                    "updated_at": datetime.now(timezone.utc),
                })
                created += 1
        except Exception as e:
            logger.error(f"Extract topics error: {e}")
            errors += 1

    return {"created": created, "skipped": skipped, "errors": errors}


@router.post("/seo/generate")
async def generate_seo_pages(request: Request):
    """Generate SEO pages from topics."""
    _validate_admin_session(request)
    await _csrf_check(request)
    db = _get_db()
    body = await request.json()
    topic_ids = body.get("topic_ids", [])
    page_types = body.get("page_types", ["notes", "important-questions", "mcqs", "summary", "revision"])
    generated = 0
    total = len(topic_ids) * len(page_types)

    for tid in topic_ids:
        topic = await db.seo_topics.find_one({"_id": ObjectId(tid)})
        if not topic:
            continue
        for pt in page_types:
            try:
                existing = await db.seo_pages.find_one({"topic_id": tid, "page_type": pt})
                if existing:
                    continue
                sys_prompt = f"You are an SEO content expert. Generate a {pt} page for an educational topic."
                t_title = topic["title"]
                t_def = topic.get("definition", "")
                user_msg = f"Topic: {t_title}\nDefinition: {t_def}\nGenerate comprehensive {pt} content optimized for search engines."
                content_text = await vertex_client.generate(sys_prompt, user_msg)
                meta_desc = await vertex_client.generate(
                    "Generate a meta description (max 160 chars) for the following content.",
                    content_text[:500],
                )
                await db.seo_pages.insert_one({
                    "topic_id": tid,
                    "topic_title": t_title,
                    "chapter_id": topic.get("chapter_id", ""),
                    "subject_id": topic.get("subject_id", ""),
                    "subject_name": topic.get("subject_name", ""),
                    "page_type": pt,
                    "title": f"{t_title} - {pt.replace('-', ' ').title()}",
                    "content": content_text,
                    "meta_description": meta_desc[:160] if meta_desc else "",
                    "answer_summary": content_text[:300],
                    "status": "draft",
                    "quality_score": 0,
                    "geo_score": 0,
                    "combined_score": 0,
                    "generated_at": datetime.now(timezone.utc),
                    "updated_at": datetime.now(timezone.utc),
                })
                generated += 1
            except Exception as e:
                logger.error(f"Generate page error: {e}")

    return {"total": total, "generated": generated}


# ---------------------------------------------------------------------------
# Pipeline Management
# ---------------------------------------------------------------------------


@router.post("/seo/auto-run")
async def auto_run_pipeline(request: Request):
    """Full pipeline run - extract topics then generate pages."""
    _validate_admin_session(request)
    await _csrf_check(request)
    db = _get_db()
    job_doc = {
        "status": "queued",
        "kind": "auto-run",
        "total": 0,
        "done": 0,
        "errors": 0,
        "skipped": 0,
        "current": "Queued",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    result = await db.seo_jobs.insert_one(job_doc)
    job_id = str(result.inserted_id)
    asyncio.create_task(_run_pipeline_job(job_id))
    return {"job_id": job_id}


@router.get("/seo/jobs/{job_id}")
async def get_job_status(request: Request, job_id: str):
    """Get pipeline job status."""
    _validate_admin_session(request)
    db = _get_db()
    job = await db.seo_jobs.find_one({"_id": ObjectId(job_id)})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id": str(job["_id"]),
        "status": job.get("status", "unknown"),
        "total": job.get("total", 0),
        "done": job.get("done", 0),
        "errors": job.get("errors", 0),
        "skipped": job.get("skipped", 0),
        "current": job.get("current", ""),
        "kind": job.get("kind", ""),
    }


@router.post("/seo/pilot")
async def pilot_run(
    request: Request,
    board_name: Optional[str] = None,
    class_name: Optional[str] = None,
    subject_keyword: Optional[str] = None,
    chapter_limit: int = 3,
):
    """Pilot run - process a few chapters to test the pipeline."""
    _validate_admin_session(request)
    await _csrf_check(request)
    db = _get_db()
    query = {}
    if board_name:
        query["board_name"] = {"$regex": board_name, "$options": "i"}
    if class_name:
        query["class_name"] = {"$regex": class_name, "$options": "i"}
    if subject_keyword:
        query["subject_name"] = {"$regex": subject_keyword, "$options": "i"}
    chapters = await db.chapters.find(query).limit(chapter_limit).to_list(length=chapter_limit)
    created = 0
    pages_generated = 0

    for ch in chapters:
        ch_id = str(ch["_id"])
        title = ch.get("title", "")
        topics_list = ch.get("topics", [])
        if not topics_list:
            try:
                import json as _json
                result = await vertex_client.generate(
                    "You are an education expert. Return a JSON array of objects with title and definition fields.",
                    f"Extract key topics from the chapter titled: {title}",
                )
                topics_list = _json.loads(result) if result.startswith("[") else []
            except Exception:
                topics_list = []

        for t in topics_list[:3]:
            t_title = t.get("title", "") if isinstance(t, dict) else str(t)
            if not t_title:
                continue
            topic_doc = {
                "title": t_title,
                "slug": _auto_slug(t_title),
                "definition": t.get("definition", "") if isinstance(t, dict) else "",
                "subject_id": ch.get("subject_id", ""),
                "subject_name": ch.get("subject_name", ""),
                "chapter_id": ch_id,
                "chapter_title": title,
                "status": "ready",
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
            ins = await db.seo_topics.insert_one(topic_doc)
            created += 1
            try:
                content_text = await vertex_client.generate(
                    "You are an SEO content expert. Generate comprehensive notes.",
                    f"Topic: {t_title}",
                )
                await db.seo_pages.insert_one({
                    "topic_id": str(ins.inserted_id),
                    "topic_title": t_title,
                    "chapter_id": ch_id,
                    "subject_id": ch.get("subject_id", ""),
                    "subject_name": ch.get("subject_name", ""),
                    "page_type": "notes",
                    "title": f"{t_title} - Notes",
                    "content": content_text,
                    "meta_description": "",
                    "answer_summary": content_text[:300] if content_text else "",
                    "status": "draft",
                    "quality_score": 0,
                    "geo_score": 0,
                    "combined_score": 0,
                    "generated_at": datetime.now(timezone.utc),
                    "updated_at": datetime.now(timezone.utc),
                })
                pages_generated += 1
            except Exception as e:
                logger.error(f"Pilot page generation error: {e}")

    return {
        "chapters_processed": len(chapters),
        "topics_created": created,
        "pages_generated": pages_generated,
    }


@router.get("/seo/subject-coverage")
async def subject_coverage(request: Request):
    """Per-subject topic and page coverage."""
    _validate_admin_session(request)
    db = _get_db()
    pipeline = [
        {"$group": {
            "_id": "$subject_id",
            "name": {"$first": "$subject_name"},
            "topic_count": {"$sum": 1},
        }}
    ]
    topic_agg = await db.seo_topics.aggregate(pipeline).to_list(length=200)
    subjects = []
    for item in topic_agg:
        sid = item["_id"]
        page_count = await db.seo_pages.count_documents({"subject_id": sid})
        published_count = await db.seo_pages.count_documents({"subject_id": sid, "status": "published"})
        tc = item["topic_count"]
        coverage_pct = round((published_count / tc * 100) if tc > 0 else 0, 1)
        subjects.append({
            "subject_id": sid,
            "name": item.get("name", ""),
            "topic_count": tc,
            "page_count": page_count,
            "published_count": published_count,
            "coverage_pct": coverage_pct,
        })
    return {"subjects": subjects}


@router.post("/seo/run-subject")
async def run_subject_pipeline(
    request: Request,
    subject_id: str = Query(...),
    force: bool = False,
):
    """Run pipeline for a single subject."""
    _validate_admin_session(request)
    await _csrf_check(request)
    db = _get_db()
    job_doc = {
        "status": "queued",
        "kind": "run-subject",
        "total": 0,
        "done": 0,
        "errors": 0,
        "skipped": 0,
        "current": "Queued",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    result = await db.seo_jobs.insert_one(job_doc)
    job_id = str(result.inserted_id)
    asyncio.create_task(_run_pipeline_job(job_id, subject_id=subject_id, force=force))
    return {"job_id": job_id}


@router.post("/seo/bulk-publish")
async def bulk_publish(
    request: Request,
    page_type: Optional[str] = None,
    subject_id: Optional[str] = None,
):
    """Publish all draft SEO pages."""
    _validate_admin_session(request)
    await _csrf_check(request)
    db = _get_db()
    query = {"status": "draft"}
    if page_type:
        query["page_type"] = page_type
    if subject_id:
        query["subject_id"] = subject_id
    now = datetime.now(timezone.utc)
    result = await db.seo_pages.update_many(
        query, {"$set": {"status": "published", "published_at": now, "updated_at": now}}
    )
    return {"message": "Bulk publish complete", "count": result.modified_count}


@router.get("/seo/insights")
async def seo_insights(request: Request):
    """Content gap analysis and suggestions."""
    _validate_admin_session(request)
    db = _get_db()
    all_topic_ids = [str(t["_id"]) async for t in db.seo_topics.find({}, {"_id": 1})]
    pages_topic_ids = await db.seo_pages.distinct("topic_id")
    topics_without_pages = [tid for tid in all_topic_ids if tid not in pages_topic_ids]

    page_types = ["notes", "important-questions", "mcqs", "summary", "revision"]
    missing_types_count = 0
    for tid in pages_topic_ids:
        existing_types = await db.seo_pages.distinct("page_type", {"topic_id": tid})
        missing = [pt for pt in page_types if pt not in existing_types]
        if missing:
            missing_types_count += 1

    return {
        "gaps": {
            "topics_without_pages": len(topics_without_pages),
            "topics_with_missing_types": missing_types_count,
        },
        "suggestions": [
            f"Generate pages for {len(topics_without_pages)} topics that have no pages yet",
            f"Fill missing page types for {missing_types_count} topics",
        ],
    }


# ---------------------------------------------------------------------------
# Quality & Review
# ---------------------------------------------------------------------------


@router.get("/seo/review-queue")
async def review_queue(
    request: Request,
    status: str = "draft",
    limit: int = 50,
):
    """Pages needing review."""
    _validate_admin_session(request)
    db = _get_db()
    pages = await db.seo_pages.find({"status": status}).sort("generated_at", -1).limit(limit).to_list(length=limit)
    for p in pages:
        p["id"] = str(p.pop("_id"))
    return {"pages": pages}


@router.post("/seo/review-queue/bulk-action")
async def review_bulk_action(request: Request):
    """Bulk approve or reject pages."""
    _validate_admin_session(request)
    await _csrf_check(request)
    db = _get_db()
    body = await request.json()
    action = body.get("action", "approve")
    page_ids = body.get("page_ids", [])
    min_score = body.get("min_score")
    now = datetime.now(timezone.utc)

    if action == "approve":
        new_status = "published"
    else:
        new_status = "rejected"

    query = {}
    if page_ids:
        query["_id"] = {"$in": [ObjectId(pid) for pid in page_ids]}
    elif min_score is not None:
        query["quality_score"] = {"$gte": min_score}
        query["status"] = "draft"

    update_fields = {"status": new_status, "updated_at": now}
    if new_status == "published":
        update_fields["published_at"] = now

    result = await db.seo_pages.update_many(query, {"$set": update_fields})
    return {"message": f"Bulk {action} complete", "count": result.modified_count}


@router.get("/seo/diagnose-topics")
async def diagnose_topics(
    request: Request,
    limit: int = 100,
    only_blocked: bool = False,
):
    """Find blocked or incomplete topics."""
    _validate_admin_session(request)
    db = _get_db()
    query = {}
    if only_blocked:
        query["status"] = "blocked"
    topics = await db.seo_topics.find(query).limit(limit).to_list(length=limit)
    blocked = 0
    ready = 0
    items = []
    for t in topics:
        status = t.get("status", "unknown")
        if status == "blocked":
            blocked += 1
        elif status == "ready":
            ready += 1
        items.append({
            "id": str(t["_id"]),
            "title": t.get("title", ""),
            "status": status,
            "definition": t.get("definition", ""),
            "chapter_title": t.get("chapter_title", ""),
        })
    return {"items": items, "summary": {"blocked": blocked, "ready": ready}}


@router.post("/seo/backfill-notes")
async def backfill_notes(request: Request):
    """Generate notes for topics missing definitions. Runs as background job."""
    _validate_admin_session(request)
    await _csrf_check(request)
    db = _get_db()
    job_doc = {
        "status": "queued",
        "kind": "backfill-notes",
        "total": 0,
        "done": 0,
        "errors": 0,
        "skipped": 0,
        "current": "Queued",
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    result = await db.seo_jobs.insert_one(job_doc)
    job_id = str(result.inserted_id)
    asyncio.create_task(_run_backfill_notes_job(job_id))
    return {"job_id": job_id}


@router.post("/seo/refresh-meta")
async def refresh_meta(request: Request):
    """Refresh meta descriptions for all published pages."""
    _validate_admin_session(request)
    await _csrf_check(request)
    db = _get_db()
    pages = await db.seo_pages.find({"status": "published"}).to_list(length=1000)
    updated = 0
    for page in pages:
        try:
            content_text = page.get("content", "")
            if not content_text:
                continue
            meta = await vertex_client.generate(
                "Generate a meta description (max 160 chars) for the following educational content.",
                content_text[:500],
            )
            await db.seo_pages.update_one(
                {"_id": page["_id"]},
                {"$set": {"meta_description": meta[:160], "updated_at": datetime.now(timezone.utc)}},
            )
            updated += 1
        except Exception as e:
            logger.error(f"Refresh meta error: {e}")
    return {"message": "Meta descriptions refreshed", "updated": updated}


@router.get("/seo/auto-publish/schedule")
async def auto_publish_schedule(request: Request):
    """Return auto-publish configuration and recent runs."""
    _validate_admin_session(request)
    db = _get_db()
    config = await db.seo_settings.find_one({"key": "auto_publish"}) or {}
    recent_runs = await db.seo_jobs.find({"kind": "auto-publish"}).sort("created_at", -1).limit(10).to_list(length=10)
    for r in recent_runs:
        r["id"] = str(r.pop("_id"))
    return {
        "config": config.get("value", {}),
        "last_marker": config.get("last_marker", None),
        "recent_runs": recent_runs,
    }


# ---------------------------------------------------------------------------
# Additional SEO endpoints (quality audit, duplicates, related)
# ---------------------------------------------------------------------------


@router.post("/seo/flag-low-quality")
async def flag_low_quality(request: Request):
    """Flag pages below quality threshold."""
    _validate_admin_session(request)
    await _csrf_check(request)
    db = _get_db()
    body = await request.json()
    threshold = body.get("threshold", 50)
    result = await db.seo_pages.update_many(
        {"quality_score": {"$lt": threshold}, "status": {"$ne": "flagged"}},
        {"$set": {"status": "flagged", "updated_at": datetime.now(timezone.utc)}},
    )
    return {"flagged": result.modified_count}


@router.post("/seo/quality-audit")
async def quality_audit(request: Request):
    """Audit page quality. Optionally unpublish below threshold."""
    _validate_admin_session(request)
    await _csrf_check(request)
    db = _get_db()
    body = await request.json()
    unpublish_below = body.get("unpublish_below", 30)
    dry_run = body.get("dry_run", True)
    pages = await db.seo_pages.find({"status": "published"}).to_list(length=2000)
    audited = 0
    unpublished = 0
    report = []
    for page in pages:
        score = page.get("quality_score", 0)
        audited += 1
        if score < unpublish_below:
            if not dry_run:
                await db.seo_pages.update_one(
                    {"_id": page["_id"]},
                    {"$set": {"status": "draft", "updated_at": datetime.now(timezone.utc)}},
                )
            unpublished += 1
            report.append({"id": str(page["_id"]), "title": page.get("title", ""), "score": score})
    return {"audited": audited, "unpublished": unpublished, "report": report[:50]}


@router.get("/seo/quality-summary")
async def quality_summary(request: Request):
    """Quality score distribution."""
    _validate_admin_session(request)
    db = _get_db()
    pipeline = [
        {"$group": {
            "_id": None,
            "avg_score": {"$avg": "$quality_score"},
            "count": {"$sum": 1},
        }}
    ]
    agg = await db.seo_pages.aggregate(pipeline).to_list(length=1)
    avg_score = agg[0]["avg_score"] if agg else 0
    dist = {}
    for bucket in ["0-20", "21-40", "41-60", "61-80", "81-100"]:
        low, high = [int(x) for x in bucket.split("-")]
        count = await db.seo_pages.count_documents({
            "quality_score": {"$gte": low, "$lte": high}
        })
        dist[bucket] = count
    return {"avg_score": round(avg_score, 1) if avg_score else 0, "distribution": dist}


@router.post("/seo/duplicate-scan")
async def duplicate_scan(request: Request):
    """Scan for duplicate content."""
    _validate_admin_session(request)
    await _csrf_check(request)
    db = _get_db()
    body = await request.json()
    similarity_threshold = body.get("similarity_threshold", 0.8)
    scope = body.get("scope", "all")
    pipeline = [
        {"$group": {
            "_id": "$topic_title",
            "count": {"$sum": 1},
            "page_ids": {"$push": {"$toString": "$_id"}},
            "page_types": {"$push": "$page_type"},
        }},
        {"$match": {"count": {"$gt": 1}}},
    ]
    if scope != "all":
        pipeline.insert(0, {"$match": {"page_type": scope}})
    duplicates = await db.seo_pages.aggregate(pipeline).to_list(length=200)
    pairs_created = 0
    for dup in duplicates:
        ids = dup["page_ids"]
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                existing = await db.seo_duplicate_pairs.find_one({
                    "page_a": ids[i], "page_b": ids[j]
                })
                if not existing:
                    await db.seo_duplicate_pairs.insert_one({
                        "page_a": ids[i],
                        "page_b": ids[j],
                        "topic_title": dup["_id"],
                        "similarity": similarity_threshold,
                        "status": "pending",
                        "created_at": datetime.now(timezone.utc),
                    })
                    pairs_created += 1
    return {"duplicates_found": len(duplicates), "pairs_created": pairs_created}


@router.get("/seo/duplicate-pairs")
async def get_duplicate_pairs(
    request: Request,
    status: str = "pending",
    limit: int = 50,
):
    """Get duplicate pairs."""
    _validate_admin_session(request)
    db = _get_db()
    pairs = await db.seo_duplicate_pairs.find({"status": status}).limit(limit).to_list(length=limit)
    for p in pairs:
        p["id"] = str(p.pop("_id"))
    return {"pairs": pairs}


@router.post("/seo/duplicate-pairs/{pair_id}/resolve")
async def resolve_duplicate_pair(request: Request, pair_id: str):
    """Resolve a duplicate pair."""
    _validate_admin_session(request)
    await _csrf_check(request)
    db = _get_db()
    body = await request.json()
    action = body.get("action", "keep_both")
    pair = await db.seo_duplicate_pairs.find_one({"_id": ObjectId(pair_id)})
    if not pair:
        raise HTTPException(status_code=404, detail="Pair not found")
    now = datetime.now(timezone.utc)
    if action == "delete_b":
        await db.seo_pages.delete_one({"_id": ObjectId(pair["page_b"])})
    elif action == "delete_a":
        await db.seo_pages.delete_one({"_id": ObjectId(pair["page_a"])})
    await db.seo_duplicate_pairs.update_one(
        {"_id": ObjectId(pair_id)},
        {"$set": {"status": "resolved", "action": action, "resolved_at": now}},
    )
    return {"message": "Pair resolved", "action": action}


@router.get("/seo/related-by-chapter/{chapter_id}")
async def related_by_chapter(
    chapter_id: str,
    limit: int = 10,
    exclude_topic_id: Optional[str] = None,
):
    """PUBLIC - Get related SEO content for a chapter. No auth required."""
    db = _get_db()
    query = {"chapter_id": chapter_id, "status": "published"}
    if exclude_topic_id:
        query["topic_id"] = {"$ne": exclude_topic_id}
    pages = await db.seo_pages.find(query).limit(limit).to_list(length=limit)
    for p in pages:
        p["id"] = str(p.pop("_id"))
    return {"pages": pages}
