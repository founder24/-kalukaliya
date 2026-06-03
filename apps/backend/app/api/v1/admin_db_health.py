"""
Admin DB Health Endpoint
Audits all MongoDB databases for content completeness:
subjects, chapters (with notes), and knowledge objects.
"""

from datetime import datetime, timezone
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.api.v1.admin import _validate_admin_session
from app.db.mongo import get_mongo_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin DB Health"])

SKIP_DBS = {"admin", "local", "config"}

CONTENT_COLLECTIONS = {
    "boards", "classes", "streams", "subjects",
    "chapters", "knowledge_objects", "users",
}


async def _audit_database(client, db_name: str) -> dict:
    db = client[db_name]
    try:
        colls = set(await db.list_collection_names())
    except Exception as e:
        return {"db": db_name, "error": str(e)}

    async def count(coll, filt=None):
        if coll not in colls:
            return 0
        return await db[coll].count_documents(filt or {})

    boards   = await count("boards")
    classes  = await count("classes")
    subjects = await count("subjects")

    chapters_total     = await count("chapters")
    chapters_en        = await count("chapters", {"content_en": {"$exists": True, "$nin": [None, ""]}})
    chapters_as        = await count("chapters", {"content_as": {"$exists": True, "$nin": [None, ""]}})
    chapters_published = await count("chapters", {"status": "published"})

    ko_total     = await count("knowledge_objects")
    ko_body      = await count("knowledge_objects", {"body_markdown": {"$exists": True, "$nin": [None, ""]}})
    ko_published = await count("knowledge_objects", {"status": "published"})

    users = await count("users")

    # Sample subjects
    sample_subjects = []
    if "subjects" in colls:
        async for s in db["subjects"].find({}, {"name": 1, "status": 1, "slug": 1}).limit(6):
            sample_subjects.append({
                "name": s.get("name", ""),
                "status": s.get("status", ""),
                "slug": s.get("slug", ""),
            })

    # Sample chapters with English notes
    sample_chapters = []
    if "chapters" in colls and chapters_en > 0:
        async for c in db["chapters"].find(
            {"content_en": {"$exists": True, "$nin": [None, ""]}},
            {"title": 1, "status": 1}
        ).limit(5):
            sample_chapters.append({
                "title": c.get("title", ""),
                "status": c.get("status", ""),
            })

    # Verdict
    has_subjects = subjects > 0
    has_notes    = chapters_en > 0 or ko_body > 0
    if has_subjects and has_notes and (chapters_published > 0 or ko_published > 0):
        verdict = "complete"
    elif has_subjects and has_notes:
        verdict = "has_data"
    elif has_subjects:
        verdict = "no_notes"
    else:
        verdict = "empty"

    completeness_pct = 0
    if chapters_total > 0:
        completeness_pct = round((chapters_en / chapters_total) * 100, 1)

    return {
        "db": db_name,
        "verdict": verdict,
        "completeness_pct": completeness_pct,
        "collections_present": sorted(colls),
        "counts": {
            "boards":             boards,
            "classes":            classes,
            "subjects":           subjects,
            "chapters_total":     chapters_total,
            "chapters_with_en":   chapters_en,
            "chapters_with_as":   chapters_as,
            "chapters_published": chapters_published,
            "knowledge_objects":  ko_total,
            "ko_with_body":       ko_body,
            "ko_published":       ko_published,
            "users":              users,
        },
        "samples": {
            "subjects": sample_subjects,
            "chapters_with_notes": sample_chapters,
        },
    }


@router.get("/db-health")
async def admin_db_health(request: Request):
    """
    Audit all MongoDB databases for content completeness.
    Reports subjects, chapters with notes (EN/AS), and knowledge objects.
    Verdicts: complete | has_data | no_notes | empty
    """
    await _validate_admin_session(request)

    client = get_mongo_client()

    # List all non-system databases
    try:
        all_dbs = [
            d["name"]
            async for d in client.list_databases()
            if d["name"] not in SKIP_DBS
        ]
    except Exception as e:
        logger.error(f"db-health: cannot list databases: {e}")
        return JSONResponse(status_code=503, content={"error": str(e)})

    results = []
    for db_name in sorted(all_dbs):
        audit = await _audit_database(client, db_name)
        results.append(audit)

    # Sort: complete first, then has_data, then no_notes, then empty
    order = {"complete": 0, "has_data": 1, "no_notes": 2, "empty": 3}
    results.sort(key=lambda r: (order.get(r.get("verdict", "empty"), 9), r["db"]))

    # Build summary
    summary = {
        "total_databases": len(results),
        "complete":  sum(1 for r in results if r.get("verdict") == "complete"),
        "has_data":  sum(1 for r in results if r.get("verdict") == "has_data"),
        "no_notes":  sum(1 for r in results if r.get("verdict") == "no_notes"),
        "empty":     sum(1 for r in results if r.get("verdict") == "empty"),
        "recommended_db": next(
            (r["db"] for r in results if r.get("verdict") in ("complete", "has_data")),
            None,
        ),
        "current_db": (await client.server_info()).get("db", None),
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "databases": results,
    }
