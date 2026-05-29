from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
import logging

from app.models.user import User
from app.api.v1.auth import get_current_user

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


class OnboardingRequest(BaseModel):
    language: Optional[str] = None
    grade: Optional[str] = None
    board: Optional[str] = None
    stream: Optional[str] = None


@router.get("/me", response_model=UserProfile)
async def get_current_user_profile(user: User = Depends(get_current_user)):
    """Get current user profile.

    Note (HF-047): No explicit rate limit here. Protected by JWT auth requirement
    and edge-level rate limiting which covers all /api/ paths.
    """
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
    # Cascade delete chats
    from app.models.chat import Chat

    await Chat.find({"user_id": str(user.id)}).delete()

    # Cascade delete feedback
    from app.models.feedback import ChatFeedback

    await ChatFeedback.find({"user_id": str(user.id)}).delete()

    # HF-040: Cascade delete dead letters
    from app.db.mongo import get_mongo_client
    from app.config import settings

    client = get_mongo_client()
    db = client[settings.MONGODB_DB_NAME]
    await db.dead_letters.delete_many({"user_id": str(user.id)})

    # Delete user
    await user.delete()

    logger.info(f"User account deleted: {user.email}")
    return {"status": "success", "message": "Account deleted"}


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


@router.get("/credits")
async def get_credits(user: User = Depends(get_current_user)):
    """Get user credits information."""
    tier = getattr(user, "subscription_tier", "free")
    credits_remaining = getattr(user, "credits_remaining", 0) or 0
    credits_used = getattr(user, "credits_used", 0) or 0

    tier_limits = {"free": 30, "pro": 999999, "premium": 999999}
    monthly_limit = tier_limits.get(tier, 30)

    return {
        "credits_remaining": credits_remaining,
        "credits_used": credits_used,
        "monthly_limit": monthly_limit,
        "tier": tier,
    }
