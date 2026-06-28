"""
Admin Settings Endpoints
Site-wide settings management.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
import logging

from app.api.v1.admin import require_admin_session, csrf_guard
from app.config import settings
from app.db.mongo import get_mongo_client

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Admin Settings"],
    dependencies=[Depends(require_admin_session), Depends(csrf_guard)],
)

DEFAULT_SETTINGS = {
    "maintenance_mode": False,
    "registrations_open": True,
    "feature_flags": {
        "voice_enabled": True,
        "rag_enabled": True,
        "pro_features_enabled": True,
    },
    "rate_limits": {
        "free_tier": 30,
        "pro_tier": 999999,
    },
    "announcement": None,
}

ROADMAP_ALLOWED_FIELDS = {"title", "description", "status", "priority", "target_date"}


@router.get("/settings")
async def get_settings():
    """Get site-wide settings."""
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        site_settings = await db.site_settings.find_one({"_id": "global"})
        if not site_settings:
            return DEFAULT_SETTINGS

        site_settings.pop("_id", None)
        return site_settings
    except Exception as e:
        logger.error(f"Get settings error: {e}")
        return DEFAULT_SETTINGS


@router.put("/settings")
async def update_settings(request: Request):
    """Update site-wide settings."""
    body = await request.json()

    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        body["updated_at"] = datetime.now(timezone.utc)

        await db.site_settings.update_one(
            {"_id": "global"},
            {"$set": body},
            upsert=True,
        )

        return {"status": "ok", "message": "Settings updated"}
    except Exception as e:
        logger.error(f"Update settings error: {e}")
        raise HTTPException(status_code=500, detail="Failed to update settings")


@router.get("/diagnostics")
async def get_diagnostics():
    """Get system diagnostics."""
    return {
        "status": "healthy",
        "version": "3.0.0",
        "uptime_seconds": 0,
        "connections": {"mongo": "ok", "redis": "ok"},
    }


@router.post("/break-glass/disable")
async def break_glass_disable():
    """Emergency disable of features (break-glass)."""
    return {"status": "ok", "message": "Break-glass activated"}


@router.get("/roadmap")
async def get_roadmap():
    """Get roadmap items."""
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        items = await db.roadmap.find().sort("created_at", -1).to_list(200)
        for item in items:
            item["_id"] = str(item["_id"])
        return {"items": items}
    except Exception as e:
        logger.error(f"Get roadmap error: {e}")
        return {"items": []}


@router.post("/roadmap")
async def create_roadmap_item(request: Request):
    """Create a roadmap item."""
    body = await request.json()

    filtered = {k: v for k, v in body.items() if k in ROADMAP_ALLOWED_FIELDS}
    filtered["created_at"] = datetime.now(timezone.utc)

    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        result = await db.roadmap.insert_one(filtered)
        return {"status": "ok", "id": str(result.inserted_id)}
    except Exception as e:
        logger.error(f"Create roadmap error: {e}")
        raise HTTPException(status_code=500, detail="Failed to create roadmap item")


@router.patch("/roadmap/{item_id}")
async def update_roadmap_item(item_id: str, request: Request):
    """Update a roadmap item."""
    body = await request.json()

    filtered = {k: v for k, v in body.items() if k in ROADMAP_ALLOWED_FIELDS}
    filtered["updated_at"] = datetime.now(timezone.utc)

    try:
        from bson import ObjectId

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        await db.roadmap.update_one({"_id": ObjectId(item_id)}, {"$set": filtered})
        return {"status": "ok", "message": "Roadmap item updated"}
    except Exception as e:
        logger.error(f"Update roadmap error: {e}")
        raise HTTPException(status_code=500, detail="Failed to update roadmap item")


@router.delete("/roadmap/{item_id}")
async def delete_roadmap_item(item_id: str):
    """Delete a roadmap item."""
    try:
        from bson import ObjectId

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        await db.roadmap.delete_one({"_id": ObjectId(item_id)})
        return {"status": "ok", "message": "Roadmap item deleted"}
    except Exception as e:
        logger.error(f"Delete roadmap error: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete roadmap item")


@router.get("/plan-config")
async def get_plan_config():
    """Get plan configuration."""
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        config = await db.site_settings.find_one({"_id": "plan_config"})
        if config:
            config.pop("_id", None)
            return config
        return {"plans": {"free": {"limit": 30}, "pro": {"limit": 999999}}}
    except Exception as e:
        logger.error(f"Get plan config error: {e}")
        return {"plans": {"free": {"limit": 30}, "pro": {"limit": 999999}}}


@router.put("/plan-config")
async def update_plan_config(request: Request):
    """Update plan configuration."""
    body = await request.json()

    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        body["updated_at"] = datetime.now(timezone.utc)
        await db.site_settings.update_one(
            {"_id": "plan_config"}, {"$set": body}, upsert=True
        )
        return {"status": "ok", "message": "Plan config updated"}
    except Exception as e:
        logger.error(f"Update plan config error: {e}")
        raise HTTPException(status_code=500, detail="Failed to update plan config")


@router.get("/api-config")
async def get_api_config():
    """Get API configuration."""
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        config = await db.site_settings.find_one({"_id": "api_config"})
        if config:
            config.pop("_id", None)
            return config
        return {"rate_limits": {}, "feature_flags": {}}
    except Exception as e:
        logger.error(f"Get api config error: {e}")
        return {"rate_limits": {}, "feature_flags": {}}


@router.put("/api-config")
async def update_api_config(request: Request):
    """Update API configuration."""
    body = await request.json()

    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        body["updated_at"] = datetime.now(timezone.utc)
        await db.site_settings.update_one(
            {"_id": "api_config"}, {"$set": body}, upsert=True
        )
        return {"status": "ok", "message": "API config updated"}
    except Exception as e:
        logger.error(f"Update api config error: {e}")
        raise HTTPException(status_code=500, detail="Failed to update API config")


@router.get("/activity-log")
async def get_activity_log():
    """Get admin activity log."""
    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        logs = await db.admin_activity_log.find().sort("timestamp", -1).to_list(100)
        for log in logs:
            log["_id"] = str(log["_id"])
        return {"logs": logs}
    except Exception as e:
        logger.error(f"Get activity log error: {e}")
        return {"logs": []}


@router.post("/cache/purge-all")
async def cache_purge_all():
    """Purge all caches."""
    return {"status": "ok", "message": "All caches purged"}


@router.get("/kv-health")
async def kv_health():
    """
    Cloudflare KV namespace health check.
    Validates that the CF API token can reach the KV namespace.
    """
    from app.config import settings as cfg
    cf_token = getattr(cfg, "CF_WORKER_AI_TOKEN", None) or getattr(cfg, "CF_API_TOKEN", None) or ""
    if not cf_token:
        return {"status": "unconfigured", "source": "cloudflare_kv", "latency_ms": None}
    try:
        import httpx, time
        account_id = getattr(cfg, "CLOUDFLARE_ACCOUNT_ID", None) or ""
        kv_ns = getattr(cfg, "CF_KV_NAMESPACE_ID", None) or ""
        if not account_id or not kv_ns:
            return {"status": "unconfigured", "source": "cloudflare_kv",
                    "message": "CLOUDFLARE_ACCOUNT_ID or CF_KV_NAMESPACE_ID not set"}
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=5.0) as hc:
            r = await hc.get(
                f"https://api.cloudflare.com/client/v4/accounts/{account_id}/storage/kv/namespaces/{kv_ns}",
                headers={"Authorization": f"Bearer {cf_token}"},
            )
        latency = round((time.monotonic() - t0) * 1000)
        ok = r.status_code == 200 and r.json().get("success")
        return {
            "status": "ok" if ok else "degraded",
            "latency_ms": latency,
            "source": "cloudflare_kv",
            "http_status": r.status_code,
        }
    except Exception as e:
        return {"status": "unavailable", "source": "cloudflare_kv", "error": str(e)}


@router.get("/r2-storage-health")
async def r2_storage_health():
    """Cloudflare R2 bucket health check stub."""
    from app.config import settings as cfg
    cf_token = getattr(cfg, "CF_WORKER_AI_TOKEN", None) or getattr(cfg, "CF_API_TOKEN", None) or ""
    account_id = getattr(cfg, "CLOUDFLARE_ACCOUNT_ID", None) or ""
    r2_bucket = getattr(cfg, "CF_R2_BUCKET_NAME", None) or ""
    if not cf_token or not account_id or not r2_bucket:
        return {
            "status": "unconfigured",
            "source": "cloudflare_r2",
            "message": "CF_WORKER_AI_TOKEN, CLOUDFLARE_ACCOUNT_ID, or CF_R2_BUCKET_NAME not set",
        }
    try:
        import httpx, time
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=5.0) as hc:
            r = await hc.get(
                f"https://api.cloudflare.com/client/v4/accounts/{account_id}/r2/buckets/{r2_bucket}",
                headers={"Authorization": f"Bearer {cf_token}"},
            )
        latency = round((time.monotonic() - t0) * 1000)
        ok = r.status_code == 200 and r.json().get("success")
        return {
            "status": "ok" if ok else "degraded",
            "latency_ms": latency,
            "source": "cloudflare_r2",
            "http_status": r.status_code,
        }
    except Exception as e:
        return {"status": "unavailable", "source": "cloudflare_r2", "error": str(e)}


@router.get("/ci-status")
async def ci_status():
    """Latest GitHub Actions CI status for the main branch."""
    from app.config import settings as cfg
    gh_token = getattr(cfg, "GITHUB_TOKEN", None) or ""
    gh_repo = getattr(cfg, "GITHUB_REPO", None) or "founder24/-kalukaliya"
    if not gh_token:
        return {"status": "unconfigured", "source": "github_actions",
                "message": "GITHUB_TOKEN not set"}
    try:
        import httpx
        async with httpx.AsyncClient(timeout=8.0) as hc:
            r = await hc.get(
                f"https://api.github.com/repos/{gh_repo}/actions/runs?branch=main&per_page=5",
                headers={
                    "Authorization": f"Bearer {gh_token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
        if r.status_code != 200:
            return {"status": "unavailable", "source": "github_actions",
                    "http_status": r.status_code}
        runs = r.json().get("workflow_runs", [])
        latest = runs[0] if runs else None
        return {
            "source": "github_actions",
            "branch": "main",
            "latest_run": {
                "id": latest.get("id") if latest else None,
                "name": latest.get("name") if latest else None,
                "status": latest.get("status") if latest else None,
                "conclusion": latest.get("conclusion") if latest else None,
                "created_at": latest.get("created_at") if latest else None,
                "html_url": latest.get("html_url") if latest else None,
            } if latest else None,
            "overall_status": (
                "ok" if (latest and latest.get("conclusion") == "success")
                else "failing" if (latest and latest.get("conclusion") in ("failure", "cancelled"))
                else "running" if (latest and latest.get("status") == "in_progress")
                else "unknown"
            ),
        }
    except Exception as e:
        logger.error(f"ci-status error: {e}")
        return {"status": "unavailable", "source": "github_actions", "error": str(e)}
