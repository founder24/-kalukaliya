"""
Admin Users Endpoints
User listing, status, plan, role, and credit management.
"""

from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Request
import logging

from app.api.v1.admin import _validate_admin_session, _csrf_check
from app.config import settings
from app.db.mongo import get_mongo_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin Users"])


@router.get("/users")
async def list_users(
    request: Request,
    limit: int = 20,
    offset: int = 0,
    search: str = "",
):
    """Paginated user list with optional search."""
    await _validate_admin_session(request)
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
async def update_user_status(request: Request, user_id: str):
    """Update user status (active/suspended/banned)."""
    await _validate_admin_session(request)
    await _csrf_check(request)

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
async def update_user_plan(request: Request, user_id: str):
    """Update user subscription tier (free/pro)."""
    await _validate_admin_session(request)
    await _csrf_check(request)

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
async def update_user_role(request: Request, user_id: str):
    """Update user role (student/educator/admin)."""
    await _validate_admin_session(request)
    await _csrf_check(request)

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
async def adjust_user_credits(request: Request, user_id: str):
    """Adjust user credits (add/deduct/reset)."""
    await _validate_admin_session(request)
    await _csrf_check(request)

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
