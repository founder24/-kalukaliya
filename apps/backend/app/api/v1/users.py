from fastapi import APIRouter, Depends, Request, HTTPException, Path
from pydantic import BaseModel
from typing import Optional, List
import logging
import time
from datetime import datetime, timedelta, timezone

from app.models.user import User
from app.api.v1.auth import get_current_user, get_current_user_optional
from app.core.anon import resolve_anon_id

# AI-credit limits per tier (authoritative — matches billing pipeline)
CREDITS_LIMITS: dict[str, int] = {
    "free":    30,
    "starter": 100,
    "pro":     1000,
    "premium": 9999,
}
DELETION_GRACE_HOURS = 72  # hours before hard-delete fires

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
    saved_subjects: List[str] = []


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

@router.get("/me")
async def get_current_user_profile(user: User = Depends(get_current_user)):
    """Get current user profile — full response including academic + credit fields."""
    return _build_profile_response(user)


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

def _build_profile_response(user: User) -> dict:
    """Build the full profile dict sent to the frontend.

    Returns every field the profile page needs — academic details,
    credit limits, deletion state — rather than the slim UserProfile model.
    """
    tier = user.subscription_tier or "free"
    credits_limit = CREDITS_LIMITS.get(tier, CREDITS_LIMITS["free"])
    credits_used = user.credits_used or 0
    credits_remaining = user.credits_remaining if user.credits_remaining else max(0, credits_limit - credits_used)

    # Soft-delete state
    status = "active"
    deletion_hard_at = None
    if getattr(user, "deletion_requested", False) and user.deletion_scheduled_at:
        status = "pending_deletion"
        deletion_hard_at = (
            user.deletion_scheduled_at + timedelta(hours=DELETION_GRACE_HOURS)
        ).isoformat()

    return {
        "id":                   str(user.id),
        "name":                 user.name or "",
        "email":                user.email or "",
        "role":                 user.role,
        "subscription_tier":   tier,
        "plan":                 tier,
        "monthly_message_count": user.monthly_message_count,
        "preferred_language":  user.preferred_language,
        "onboarding_done":     user.onboarding_done,
        "ads_opt_out":         user.ads_opt_out,
        "saved_subjects":      user.saved_subjects or [],
        # Academic profile
        "phone":               user.phone,
        "board_id":            user.board_id,
        "board_name":          user.board_name,
        "class_id":            user.class_id,
        "class_name":          user.class_name,
        "stream_id":           user.stream_id,
        "stream_name":         user.stream_name,
        # Credits
        "credits_used":        credits_used,
        "credits_limit":       credits_limit,
        "credits_remaining":   credits_remaining,
        # Account state
        "status":              status,
        "deletion_hard_at":    deletion_hard_at,
    }


@router.get("/profile")
async def get_profile_alias(user: User = Depends(get_current_user)):
    """Full profile — frontend calls GET /user/profile."""
    return _build_profile_response(user)


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
    """Schedule account deletion — frontend calls DELETE /user/account.

    Implements a 72-hour grace period (GDPR/DPDP soft-delete):
    - Sets deletion_requested=True + deletion_scheduled_at=now()
    - Returns hard_delete_at so the frontend can show a countdown
    - A background job (or next cron run) hard-deletes after the window
    - Cancel via POST /user/account/cancel-delete within the window
    """
    now = datetime.now(timezone.utc)
    hard_delete_at = now + timedelta(hours=DELETION_GRACE_HOURS)

    await user.update({
        "$set": {
            "deletion_requested":    True,
            "deletion_scheduled_at": now,
        }
    })

    logger.info(f"Account deletion scheduled: {user.email} — hard_delete_at={hard_delete_at.isoformat()}")
    return {
        "status":          "pending_deletion",
        "hard_delete_at":  hard_delete_at.isoformat(),
        "message":         f"Account scheduled for deletion in {DELETION_GRACE_HOURS} hours",
    }


@router.post("/account/cancel-delete")
async def cancel_account_deletion(user: User = Depends(get_current_user)):
    """Cancel a scheduled account deletion within the grace window."""
    await user.update({
        "$set":   {"deletion_requested": False},
        "$unset": {"deletion_scheduled_at": ""},
    })
    logger.info(f"Account deletion cancelled: {user.email}")
    return {"status": "success", "message": "Account deletion cancelled"}


# ─── /memories endpoints (frontend MyMemoriesPage) ───────────────────────────

@router.get("/memories")
async def list_memories(
    user: User = Depends(get_current_user),
    # Frontend sends limit+offset; also accept page+per_page for API clients
    limit: Optional[int] = None,
    offset: Optional[int] = None,
    page: int = 1,
    per_page: int = 20,
    kind: Optional[str] = None,
    subject_id: Optional[str] = None,
    q: Optional[str] = None,
):
    """List saved memories for the current user from memory_brain collection.

    Accepts both pagination styles:
      - Frontend: ?limit=20&offset=0
      - API clients: ?page=1&per_page=20
    """
    from app.db.mongo import get_mongo_client
    from app.config import settings

    db = get_mongo_client()[settings.MONGODB_DB_NAME]

    # Resolve pagination — limit/offset takes precedence when provided
    if limit is not None:
        page_size = max(1, min(limit, 100))
        skip = max(0, offset or 0)
    else:
        page_size = max(1, min(per_page, 100))
        skip = (max(1, page) - 1) * page_size

    query: dict = {"user_id": str(user.id)}
    if kind and kind != "all":
        query["kind"] = kind
    if subject_id:
        query["subject_id"] = subject_id
    if q:
        # Search both text and content fields
        query["$or"] = [
            {"text":    {"$regex": q, "$options": "i"}},
            {"content": {"$regex": q, "$options": "i"}},
        ]

    try:
        total = await db.memory_brain.count_documents(query)
        cursor = db.memory_brain.find(query).sort("created_at", -1).skip(skip).limit(page_size)
        docs = await cursor.to_list(length=page_size)
    except Exception as e:
        logger.error(f"list_memories error: {e}")
        return {"items": [], "total": 0, "page": page, "pages": 0, "has_more": False}

    items = []
    for doc in docs:
        items.append({
            "id":           str(doc.get("_id", "")),
            "text":         doc.get("text", doc.get("content", "")),
            "kind":         doc.get("kind", "note"),
            "subject_id":   doc.get("subject_id"),
            "subject_name": doc.get("subject_name"),
            "chapter_name": doc.get("chapter_name"),
            "event":        doc.get("event"),
            "created_at":   doc.get("created_at", datetime.now(timezone.utc)).isoformat()
                            if doc.get("created_at") else None,
        })

    fetched_so_far = skip + len(items)
    has_more = fetched_so_far < total
    pages = max(1, -(-total // page_size))  # ceiling division

    return {
        "items":    items,
        "total":    total,
        "has_more": has_more,
        # Keep page/pages for API client compat
        "page":     page if limit is None else (skip // page_size + 1),
        "pages":    pages,
    }


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

    credits_used = user.credits_used or 0
    total_tokens = user.total_tokens_used or 0

    saved_subjects_count = len(user.saved_subjects or [])

    return {
        "conversations": conversations,
        "saved_subjects": saved_subjects_count,
        "total_tokens": total_tokens,
        "credits_used": credits_used,
    }


@router.post("/saved-subjects/{subject_id}")
async def toggle_saved_subject(
    subject_id: str = Path(..., description="Subject ID to toggle bookmark"),
    user: User = Depends(get_current_user),
):
    """Toggle a subject bookmark — adds if absent, removes if present.
    Frontend calls POST /api/v1/user/saved-subjects/{subjectId} (optimistic mutation).
    Returns the updated list so the frontend can sync on settled.
    """
    current: List[str] = user.saved_subjects or []
    if subject_id in current:
        updated = [s for s in current if s != subject_id]
        action = "removed"
    else:
        updated = current + [subject_id]
        action = "added"

    await user.update({"$set": {"saved_subjects": updated}})
    logger.info(
        "saved_subject_toggled",
        extra={"user_id": str(user.id), "subject_id": subject_id, "action": action},
    )
    return {"status": "success", "action": action, "saved_subjects": updated}


@router.get("/credits")
async def get_credits(
    request: Request,
    user: Optional[User] = Depends(get_current_user_optional),
):
    """Get user credits information — works for both authenticated and anonymous users."""
    MONTHLY_LIMIT_FREE = 30

    if user:
        tier = user.subscription_tier or "free"
        credits_used = user.monthly_message_count or 0
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
    # Read actual usage from the MongoDB quota_usage collection that
    # check_rate_limit() writes to on every streaming/non-streaming chat request.
    credits_used = 0
    try:
        import time as _time
        from app.db.mongo import get_mongo_client
        from app.config import settings as _settings
        _client = get_mongo_client()
        _db = _client[_settings.MONGODB_DB_NAME]
        _month_key = _time.strftime("%Y-%m", _time.gmtime())
        _doc = await _db.quota_usage.find_one({"user_id": anon_id, "month": _month_key})
        if _doc:
            credits_used = max(0, int(_doc.get("count", 0)))
    except Exception:
        pass  # fall back to 0 — still shows full allowance, rate limit still enforced
    credits_remaining = max(0, MONTHLY_LIMIT_FREE - credits_used)
    return {
        "credits_remaining": credits_remaining,
        "credits_used": credits_used,
        "monthly_limit": MONTHLY_LIMIT_FREE,
        "tier": "anonymous",
        "anon_id": anon_id,
    }
