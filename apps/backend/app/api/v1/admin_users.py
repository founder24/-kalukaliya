"""
Admin User Management Endpoints
Paginated user list, status/plan/role/credits management.
"""

from fastapi import APIRouter, Request, HTTPException, Query
from typing import Optional
import logging

from app.api.v1.admin import _validate_admin_session, _csrf_check

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/users")
async def list_users(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    search: Optional[str] = Query(default=None),
):
    """Paginated user list with optional search."""
    _validate_admin_session(request)
    try:
        from app.models.user import User
        import re

        query = {}
        if search:
            pattern = re.escape(search)
            query = {
                "$or": [
                    {"name": {"$regex": pattern, "$options": "i"}},
                    {"email": {"$regex": pattern, "$options": "i"}},
                ]
            }

        total = await User.find(query).count()
        users_docs = await User.find(query).skip(offset).limit(limit).to_list()

        users = []
        for u in users_docs:
            tier = u.subscription_tier or "free"
            credits_limit = 999999 if tier == "pro" else 30
            users.append(
                {
                    "id": str(u.id),
                    "name": u.name,
                    "email": u.email,
                    "plan": tier,
                    "status": getattr(u, "account_status", "active"),
                    "role": u.role or "student",
                    "credits_used": u.monthly_message_count,
                    "credits_limit": credits_limit,
                    "created_at": u.created_at.isoformat() if u.created_at else None,
                }
            )

        return {"users": users, "total": total}
    except Exception as e:
        logger.error(f"Error listing users: {e}")
        return {"users": [], "total": 0}


@router.patch("/users/{user_id}/status")
async def update_user_status(user_id: str, request: Request):
    """Update user account status (active/suspended/banned)."""
    _validate_admin_session(request)
    await _csrf_check(request)
    try:
        from bson import ObjectId
        from app.db.mongo import get_mongo_client
        from app.config import settings

        body = await request.json()
        new_status = body.get("status")
        if new_status not in ("active", "suspended", "banned"):
            raise HTTPException(status_code=400, detail="Invalid status value")

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        result = await db.users.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {"account_status": new_status}},
        )
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="User not found")
        return {"status": "ok", "new_status": new_status}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating user status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/users/{user_id}/plan")
async def update_user_plan(user_id: str, request: Request):
    """Update user subscription tier."""
    _validate_admin_session(request)
    await _csrf_check(request)
    try:
        from bson import ObjectId
        from app.db.mongo import get_mongo_client
        from app.config import settings

        body = await request.json()
        plan = body.get("plan")
        if plan not in ("free", "starter", "pro"):
            raise HTTPException(status_code=400, detail="Invalid plan value")

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        result = await db.users.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {"subscription_tier": plan}},
        )
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="User not found")
        return {"status": "ok", "new_plan": plan}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating user plan: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/users/{user_id}/role")
async def update_user_role(user_id: str, request: Request):
    """Update user role."""
    payload = _validate_admin_session(request)
    await _csrf_check(request)
    try:
        from bson import ObjectId
        from datetime import datetime, timezone
        from app.db.mongo import get_mongo_client
        from app.config import settings

        body = await request.json()
        role = body.get("role")
        if role not in ("student", "educator", "admin"):
            raise HTTPException(status_code=400, detail="Invalid role value")

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]
        result = await db.users.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {"role": role}},
        )
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="User not found")

        # Audit log for admin role grants
        if role == "admin":
            await db.audit_log.insert_one(
                {
                    "action": "admin_role_granted",
                    "target_user_id": user_id,
                    "granted_by": payload.get("sub"),
                    "timestamp": datetime.now(timezone.utc),
                }
            )

        return {"status": "ok", "new_role": role}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating user role: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/users/{user_id}/credits")
async def update_user_credits(user_id: str, request: Request):
    """Adjust user message credits."""
    _validate_admin_session(request)
    await _csrf_check(request)
    try:
        from bson import ObjectId
        from app.db.mongo import get_mongo_client
        from app.config import settings

        body = await request.json()
        action = body.get("action")
        amount = body.get("amount", 0)

        if action not in ("add", "deduct", "reset"):
            raise HTTPException(status_code=400, detail="Invalid action")

        client = get_mongo_client()
        db = client[settings.MONGODB_DB_NAME]

        if action == "reset":
            update = {"$set": {"monthly_message_count": 0}}
        elif action == "add":
            # "add" credits means reducing monthly_message_count (used count).
            # Guard against driving the counter negative.
            user_doc = await db.users.find_one(
                {"_id": ObjectId(user_id)}, {"monthly_message_count": 1}
            )
            if user_doc is None:
                raise HTTPException(status_code=404, detail="User not found")
            current_count = user_doc.get("monthly_message_count", 0)
            decrement = min(abs(amount), current_count)
            update = {"$inc": {"monthly_message_count": -decrement}}
        else:  # deduct
            update = {"$inc": {"monthly_message_count": abs(amount)}}

        result = await db.users.update_one({"_id": ObjectId(user_id)}, update)
        if result.matched_count == 0:
            raise HTTPException(status_code=404, detail="User not found")
        return {"status": "ok", "action": action, "amount": amount}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating credits: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/users/{user_id}/quiz-quota")
async def get_quiz_quota(user_id: str, request: Request):
    """Return user quiz quota (placeholder)."""
    _validate_admin_session(request)
    return {"used": 0, "limit": 10, "remaining": 10, "source": "placeholder"}


@router.post("/users/{user_id}/quiz-quota/reset")
async def reset_quiz_quota(user_id: str, request: Request):
    """Reset user quiz quota (placeholder)."""
    _validate_admin_session(request)
    await _csrf_check(request)
    return {"cleared": 0, "limit": 10, "source": "placeholder"}


@router.get("/users/churn-risk")
async def churn_risk(request: Request):
    """Placeholder churn risk analysis."""
    _validate_admin_session(request)
    return {
        "at_risk_users": [],
        "total_at_risk": 0,
        "churn_rate": 0.0,
        "source": "placeholder",
    }
