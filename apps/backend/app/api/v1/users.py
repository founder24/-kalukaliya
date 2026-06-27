from fastapi import APIRouter, Depends, Request, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import logging
import time
from datetime import datetime, timezone

from app.models.user import User
from app.api.v1.auth import get_current_user, get_current_user_optional
from app.core.anon import resolve_anon_id

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Users"])


class UserProfile(BaseModel):
    id: str
    name: str
    email: str
    role: Optional[str] = None
    subscription_tier: str
    plan: Optional[str] = (
        None  # HF-097: Explicit alias of subscription_tier for frontend compat
    )
    monthly_message_count: int
    preferred_language: str
    onboarding_done: bool = False
    ads_opt_out: bool = False


class UpdateProfileRequest(BaseModel):
    name: Optional[str] = None
    preferred_language: Optional[str] = None


class PatchProfileRequest(BaseModel):
    """Expanded profile update used by the frontend PATCH /user/profile."""
    name: Optional[str] = None
    preferred_language: Optional[str] = None
    ads_opt_out: Optional[bool] = None
    board_id: Optional[str] = None
    board_name: Optional[str] = None
    class_id: Optional[str] = None
    class_name: Optional[str] = None
    stream_id: Optional[str] = None
    stream_name: Optional[str] = None
    phone: Optional[str] = None


class OnboardingRequest(BaseModel):
    language: Optional[str] = None
    grade: Optional[str] = None
    board: Optional[str] = None
    stream: Optional[str] = None


# ─── /me endpoints (original) ────────────────────────────────────────────────

@router.get("/me", response_model=UserProfile)
async def get_current_user_profile(user: User = Depends(get_current_user)):
    """Get current user profile."""
    return UserProfile(
        id=str(user.id),
        name=user.name or "",
        email=user.email or "",
        role=user.role,
        subscription_tier=user.subscription_tier,
        plan=user.subscription_tier,
        monthly_message_count=user.monthly_message_count,
        preferred_language=user.preferred_language,
        onboarding_done=getattr(user, "onboarding_done", False),
        ads_opt_out=getattr(user, "ads_opt_out", False),
    )


@router.put("/me")
async def update_user_profile(
    body: UpdateProfileRequest,
    user: User = Depends(get_current_user),
):
    """Update user profile"""
    updates = {}
    if body.name:
        updates["name"] = body.name
    if body.preferred_language:
        updates["preferred_language"] = body.preferred_language

    if updates:
        await user.update({"$set": updates})

    return {"status": "success", "message": "Profile updated"}


@router.delete("/me")
async def delete_account(user: User = Depends(get_current_user)):
    """Delete user account (GDPR/DPDP compliance)"""
    from app.models.chat import Chat

    await Chat.find({"user_id": str(user.id)}).delete()

    from app.models.feedback import ChatFeedback

    await ChatFeedback.find({"user_id": str(user.id)}).delete()

    from app.db.mongo import get_mongo_client
    from app.config import settings

    client = get_mongo_client()
    db = client[settings.MONGODB_DB_NAME]
    await db.dead_letters.delete_many({"user_id": str(user.id)})

    await user.delete()

    logger.info(f"User account deleted: {user.email}")
    return {"status": "success", "message": "Account deleted"}


# ─── /profile aliases (frontend uses /user/profile) ──────────────────────────

@router.get("/profile", response_model=UserProfile)
async def get_profile_alias(user: User = Depends(get_current_user)):
    """Alias for GET /me — frontend calls /user/profile."""
    return UserProfile(
        id=str(user.id),
        name=user.name or "",
        email=user.email or "",
        role=user.role,
        subscription_tier=user.subscription_tier,
        plan=user.subscription_tier,
        monthly_message_count=user.monthly_message_count,
        preferred_language=user.preferred_language,
        onboarding_done=getattr(user, "onboarding_done", False),
        ads_opt_out=getattr(user, "ads_opt_out", False),
    )


@router.patch("/profile")
async def patch_profile(
    body: PatchProfileRequest,
    user: User = Depends(get_current_user),
):
    """Update user profile — frontend calls PATCH /user/profile."""
    updates = {}
    if body.name is not None:
        updates["name"] = body.name
    if body.preferred_language is not None:
        updates["preferred_language"] = body.preferred_language
    if body.ads_opt_out is not None:
        updates["ads_opt_out"] = body.ads_opt_out
    if body.board_id is not None:
        updates["board_id"] = body.board_id
    if body.board_name is not None:
        updates["board_name"] = body.board_name
    if body.class_id is not None:
        updates["class_id"] = body.class_id
    if body.class_name is not None:
        updates["class_name"] = body.class_name
    if body.stream_id is not None:
        updates["stream_id"] = body.stream_id
    if body.stream_name is not None:
        updates["stream_name"] = body.stream_name
    if body.phone is not None:
        updates["phone"] = body.phone

    if updates:
        await user.update({"$set": updates})

    logger.info("Profile patched", extra={"user_id": str(user.id), "fields": list(updates.keys())})
    return {"status": "success", "message": "Profile updated"}


# ─── /account endpoints (frontend uses /user/account) ────────────────────────

@router.delete("/account")
async def delete_account_alias(user: User = Depends(get_current_user)):
    """Delete account — frontend calls DELETE /user/account."""
    from app.models.chat import Chat

    await Chat.find({"user_id": str(user.id)}).delete()

    from app.models.feedback import ChatFeedback

    await ChatFeedback.find({"user_id": str(user.id)}).delete()

    from app.db.mongo import get_mongo_client
    from app.config import settings

    client = get_mongo_client()
    db = client[settings.MONGODB_DB_NAME]
    await db.dead_letters.delete_many({"user_id": str(user.id)})
    await db.memory_brain.delete_many({"user_id": str(user.id)})

    await user.delete()

    logger.info(f"User account deleted via /account: {user.email}")
    return {"status": "success", "message": "Account deleted"}


@router.post("/account/cancel-delete")
async def cancel_account_deletion(user: User = Depends(get_current_user)):
    """Cancel a scheduled account deletion."""
    updates = {"deletion_scheduled_at": None, "deletion_requested": False}
    await user.update({"$unset": {"deletion_scheduled_at": ""}, "$set": {"deletion_requested": False}})
    logger.info(f"Account deletion cancelled: {user.email}")
    return {"status": "success", "message": "Account deletion cancelled"}


# ─── /memories endpoints (frontend MyMemoriesPage) ───────────────────────────

@router.get("/memories")
async def list_memories(
    user: User = Depends(get_current_user),
    page: int = 1,
    per_page: int = 20,
    kind: Optional[str] = None,
    q: Optional[str] = None,
):
    """List saved memories for the current user from memory_brain collection."""
    from app.db.mongo import get_mongo_client
    from app.config import settings

    db = get_mongo_client()[settings.MONGODB_DB_NAME]

    query: dict = {"user_id": str(user.id)}
    if kind and kind != "all":
        query["kind"] = kind
    if q:
        query["text"] = {"$regex": q, "$options": "i"}

    skip = (page - 1) * per_page
    try:
        total = await db.memory_brain.count_documents(query)
        cursor = db.memory_brain.find(query).sort("created_at", -1).skip(skip).limit(per_page)
        docs = await cursor.to_list(length=per_page)
    except Exception as e:
        logger.error(f"list_memories error: {e}")
        return {"items": [], "total": 0, "page": page, "pages": 0}

    items = []
    for doc in docs:
        items.append({
            "id": str(doc.get("_id", "")),
            "text": doc.get("text", doc.get("content", "")),
            "kind": doc.get("kind", "note"),
            "subject_name": doc.get("subject_name"),
            "chapter_name": doc.get("chapter_name"),
            "event": doc.get("event"),
            "created_at": doc.get("created_at", datetime.now(timezone.utc)).isoformat()
            if doc.get("created_at") else None,
        })

    pages = max(1, -(-total // per_page))  # ceiling division
    return {"items": items, "total": total, "page": page, "pages": pages}


@router.delete("/memories")
async def delete_all_memories(user: User = Depends(get_current_user)):
    """Delete all memories for the current user."""
    from app.db.mongo import get_mongo_client
    from app.config import settings

    db = get_mongo_client()[settings.MONGODB_DB_NAME]
    try:
        result = await db.memory_brain.delete_many({"user_id": str(user.id)})
        deleted = result.deleted_count
    except Exception as e:
        logger.error(f"delete_all_memories error: {e}")
        deleted = 0

    logger.info(f"Deleted {deleted} memories for user {user.id}")
    return {"status": "success", "deleted": deleted}


@router.delete("/memories/{memory_id}")
async def delete_memory(
    memory_id: str,
    user: User = Depends(get_current_user),
):
    """Delete a single memory by ID, scoped to the current user."""
    from app.db.mongo import get_mongo_client
    from app.config import settings
    from bson import ObjectId

    db = get_mongo_client()[settings.MONGODB_DB_NAME]

    try:
        oid = ObjectId(memory_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid memory ID")

    try:
        result = await db.memory_brain.delete_one({"_id": oid, "user_id": str(user.id)})
        if result.deleted_count == 0:
            raise HTTPException(status_code=404, detail="Memory not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"delete_memory error: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete memory")

    return {"status": "success", "message": "Memory deleted"}


# ─── Other original routes ────────────────────────────────────────────────────

@router.post("/onboarding")
async def save_onboarding(
    body: OnboardingRequest, user: User = Depends(get_current_user)
):
    """Save user onboarding preferences (language, grade, board, stream)."""
    updates = {}
    if body.language:
        updates["preferred_language"] = body.language
    if body.grade:
        updates["grade"] = body.grade
    if body.board:
        updates["board"] = body.board
    if body.stream:
        updates["stream"] = body.stream
    updates["onboarding_done"] = True

    if updates:
        await user.update({"$set": updates})

    logger.info("Onboarding saved", extra={"user_id": str(user.id)})
    return {"status": "success", "message": "Onboarding preferences saved"}


@router.get("/stats")
async def get_user_stats(user: User = Depends(get_current_user)):
    """Return aggregated activity stats for the profile page."""
    from app.models.chat import Chat

    try:
        conversations = await Chat.find({"user_id": str(user.id)}).count()
    except Exception:
        conversations = 0

    credits_used = getattr(user, "credits_used", 0) or 0
    total_tokens = getattr(user, "total_tokens_used", 0) or 0

    return {
        "conversations": conversations,
        "saved_subjects": 0,
        "total_tokens": total_tokens,
        "credits_used": credits_used,
    }


@router.get("/credits")
async def get_credits(
    request: Request,
    user: Optional[User] = Depends(get_current_user_optional),
):
    """Get user credits information — works for both authenticated and anonymous users."""
    MONTHLY_LIMIT_FREE = 30

    if user:
        tier = getattr(user, "subscription_tier", "free") or "free"
        credits_used = getattr(user, "monthly_message_count", 0) or 0
        tier_limits = {"free": MONTHLY_LIMIT_FREE, "pro": 999999, "premium": 999999}
        monthly_limit = tier_limits.get(tier, MONTHLY_LIMIT_FREE)
        credits_remaining = max(0, monthly_limit - credits_used)
        return {
            "credits_remaining": credits_remaining,
            "credits_used": credits_used,
            "monthly_limit": monthly_limit,
            "tier": tier,
        }

    anon_id = resolve_anon_id(request)
    credits_used = 0
    try:
        from app.db.redis import get_redis

        redis = get_redis()
        month_key = time.strftime("%Y-%m", time.gmtime())
        redis_key = f"rate:{anon_id}:{month_key}"
        val = await redis.get(redis_key)
        credits_used = max(0, int(val or 0))
    except Exception:
        pass

    credits_remaining = max(0, MONTHLY_LIMIT_FREE - credits_used)
    return {
        "credits_remaining": credits_remaining,
        "credits_used": credits_used,
        "monthly_limit": MONTHLY_LIMIT_FREE,
        "tier": "anonymous",
        "anon_id": anon_id,
    }
