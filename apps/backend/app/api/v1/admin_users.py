"""
Admin Users Endpoints
User listing, status, plan, role, and credit management.
"""

from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Request
import logging

from app.api.v1.admin import require_admin_session, csrf_guard
from app.config import settings
from app.db.mongo import get_mongo_client

logger = logging.getLogger(__name__)

router = APIRouter(
    tags=["Admin Users"],
    dependencies=[Depends(require_admin_session), Depends(csrf_guard)],
)


@router.get("/users")
async def list_users(
    limit: int = 20,
    offset: int = 0,
    search: str = "",
):
    """Paginated user list with optional search."""
    limit = min(limit, 100)

    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        query = {}
        if search:
            query = {
                "$or": [
                    {"email": {"$regex": search, "$options": "i"}},
                    {"name": {"$regex": search, "$options": "i"}},
                ]
            }

        total = await db.users.count_documents(query)
        cursor = db.users.find(query).sort("created_at", -1).skip(offset).limit(limit)
        users_raw = await cursor.to_list(length=limit)

        users = []
        for u in users_raw:
            users.append(
                {
                    "id": str(u["_id"]),
                    "email": u.get("email"),
                    "name": u.get("name"),
                    "role": u.get("role"),
                    "subscription_tier": u.get("subscription_tier", "free"),
                    "subscription_status": u.get("subscription_status", "active"),
                    "monthly_message_count": u.get("monthly_message_count", 0),
                    "created_at": u.get("created_at", "").isoformat()
                    if u.get("created_at")
                    else None,
                    "updated_at": u.get("updated_at", "").isoformat()
                    if u.get("updated_at")
                    else None,
                }
            )

        return {
            "users": users,
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": offset + limit < total,
        }
    except Exception as e:
        logger.error(f"List users error: {e}")
        return {
            "users": [],
            "total": 0,
            "offset": offset,
            "limit": limit,
            "has_more": False,
        }


@router.patch("/users/{user_id}/status")
async def update_user_status(user_id: str, request: Request):
    """Update user status (active/suspended/banned)."""
    body = await request.json()
    status = body.get("status")
    if status not in ("active", "suspended", "banned"):
        raise HTTPException(
            status_code=400,
            detail="Invalid status. Must be active, suspended, or banned.",
        )

    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        result = await db.users.update_one(
            {"_id": ObjectId(user_id)},
            {
                "$set": {
                    "subscription_status": status,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
        )
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="User not found")

        return {"status": "ok", "user_id": user_id, "new_status": status}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Update user status error: {e}")
        raise HTTPException(status_code=500, detail="Failed to update user status")


@router.patch("/users/{user_id}/plan")
async def update_user_plan(user_id: str, request: Request):
    """Update user subscription tier (free/pro)."""
    body = await request.json()
    plan = body.get("plan")
    if plan not in ("free", "pro"):
        raise HTTPException(
            status_code=400, detail="Invalid plan. Must be free or pro."
        )

    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        result = await db.users.update_one(
            {"_id": ObjectId(user_id)},
            {
                "$set": {
                    "subscription_tier": plan,
                    "updated_at": datetime.now(timezone.utc),
                }
            },
        )
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="User not found")

        return {"status": "ok", "user_id": user_id, "new_plan": plan}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Update user plan error: {e}")
        raise HTTPException(status_code=500, detail="Failed to update user plan")


@router.patch("/users/{user_id}/role")
async def update_user_role(user_id: str, request: Request):
    """Update user role (student/educator/admin)."""
    body = await request.json()
    role = body.get("role")
    if role not in ("student", "educator", "admin"):
        raise HTTPException(
            status_code=400, detail="Invalid role. Must be student, educator, or admin."
        )

    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        result = await db.users.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {"role": role, "updated_at": datetime.now(timezone.utc)}},
        )
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="User not found")

        return {"status": "ok", "user_id": user_id, "new_role": role}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Update user role error: {e}")
        raise HTTPException(status_code=500, detail="Failed to update user role")


@router.patch("/users/{user_id}/credits")
async def adjust_user_credits(user_id: str, request: Request):
    """Adjust user credits (add/deduct/reset)."""
    body = await request.json()
    action = body.get("action")
    amount = body.get("amount", 0)

    if action not in ("add", "deduct", "reset"):
        raise HTTPException(
            status_code=400, detail="Invalid action. Must be add, deduct, or reset."
        )

    try:
        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        if action == "reset":
            update = {
                "$set": {
                    "monthly_message_count": 0,
                    "updated_at": datetime.now(timezone.utc),
                }
            }
        elif action == "add":
            update = {
                "$inc": {"monthly_message_count": -abs(int(amount))},
                "$set": {"updated_at": datetime.now(timezone.utc)},
            }
        else:  # deduct
            update = {
                "$inc": {"monthly_message_count": abs(int(amount))},
                "$set": {"updated_at": datetime.now(timezone.utc)},
            }

        result = await db.users.update_one({"_id": ObjectId(user_id)}, update)
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="User not found")

        return {"status": "ok", "user_id": user_id, "action": action, "amount": amount}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Adjust credits error: {e}")
        raise HTTPException(status_code=500, detail="Failed to adjust user credits")


# ── Export & Risk Analysis ────────────────────────────────────────────────────

from fastapi.responses import StreamingResponse as _StreamingResponse
import io as _io
import csv as _csv


@router.get("/users/export")
async def users_export(plan: str = None, status_filter: str = None):
    """Export users as CSV — optionally filtered by plan or status."""
    try:
        db = get_mongo_client()[settings.MONGODB_DB_NAME]
        query: dict = {}
        if plan:
            query["plan"] = plan
        if status_filter:
            query["status"] = status_filter
        cursor = db.users.find(query).sort("created_at", -1).limit(10000)
        rows = await cursor.to_list(length=10000)
        output = _io.StringIO()
        fields = ["id", "email", "name", "plan", "status", "created_at", "last_active_at"]
        writer = _csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for u in rows:
            writer.writerow({
                "id": str(u["_id"]),
                "email": u.get("email", ""),
                "name": u.get("name", ""),
                "plan": u.get("plan", "free"),
                "status": u.get("status", "active"),
                "created_at": u["created_at"].isoformat() if u.get("created_at") else "",
                "last_active_at": u["last_active_at"].isoformat() if u.get("last_active_at") else "",
            })
        return _StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=users_export.csv"},
        )
    except Exception as e:
        logger.error(f"Users export error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/users/churn-risk")
async def users_churn_risk(days_inactive: int = 14, limit: int = 50):
    """
    List users at churn risk — active plan holders who haven't logged in recently.
    """
    try:
        db = get_mongo_client()[settings.MONGODB_DB_NAME]
        from datetime import datetime, timezone, timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=days_inactive)
        query = {
            "plan": {"$ne": "free"},
            "$or": [
                {"last_active_at": {"$lt": cutoff}},
                {"last_active_at": None},
            ],
        }
        cursor = db.users.find(query).sort("last_active_at", 1).limit(limit)
        rows = await cursor.to_list(length=limit)
        users_at_risk = []
        for u in rows:
            users_at_risk.append({
                "id": str(u["_id"]),
                "email": u.get("email"),
                "plan": u.get("plan"),
                "last_active_at": u["last_active_at"].isoformat() if u.get("last_active_at") else None,
                "days_inactive": (datetime.now(timezone.utc) - u["last_active_at"]).days if u.get("last_active_at") else None,
            })
        total = await db.users.count_documents(query)
        return {"users": users_at_risk, "total": total, "days_inactive_threshold": days_inactive}
    except Exception as e:
        logger.error(f"Churn risk error: {e}")
        return {"users": [], "total": 0}


@router.get("/users/{user_id}/quiz-quota")
async def get_user_quiz_quota(user_id: str):
    """Get a user's quiz quota configuration."""
    try:
        db = get_mongo_client()[settings.MONGODB_DB_NAME]
        user = await db.users.find_one({"_id": ObjectId(user_id)}, {"quiz_quota": 1, "plan": 1})
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return {
            "user_id": user_id,
            "plan": user.get("plan", "free"),
            "quiz_quota": user.get("quiz_quota", {"daily": 5, "custom": False}),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/users/{user_id}/quiz-quota")
async def set_user_quiz_quota(user_id: str, request: Request):
    """Override a user's quiz quota."""
    body = await request.json()
    daily = body.get("daily")
    if daily is None:
        raise HTTPException(status_code=400, detail="daily is required")
    try:
        db = get_mongo_client()[settings.MONGODB_DB_NAME]
        result = await db.users.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {"quiz_quota": {"daily": int(daily), "custom": True}, "updated_at": datetime.now(timezone.utc)}},
        )
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="User not found")
        return {"ok": True, "user_id": user_id, "quiz_quota": {"daily": daily, "custom": True}}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/users/{user_id}/quiz-quota/reset")
async def reset_user_quiz_quota(user_id: str):
    """Reset a user's quiz quota to plan defaults."""
    try:
        db = get_mongo_client()[settings.MONGODB_DB_NAME]
        result = await db.users.update_one(
            {"_id": ObjectId(user_id)},
            {"$unset": {"quiz_quota": ""}, "$set": {"updated_at": datetime.now(timezone.utc)}},
        )
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="User not found")
        return {"ok": True, "user_id": user_id, "quota_reset": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
