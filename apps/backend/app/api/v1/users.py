from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timezone
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
    plan: Optional[str] = None  # alias for subscription_tier for frontend compat
    monthly_message_count: int
    preferred_language: str
    onboarding_done: bool = False
    ads_opt_out: bool = False


class UpdateProfileRequest(BaseModel):
    name: Optional[str] = None
    preferred_language: Optional[str] = None


@router.get("/me", response_model=UserProfile)
async def get_current_user_profile(user: User = Depends(get_current_user)):
    """Get current user profile"""
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

    # Delete user
    await user.delete()

    logger.info(f"User account deleted: {user.email}")
    return {"status": "success", "message": "Account deleted"}


@router.post("/onboarding")
async def save_onboarding(
    request: Request,
    user: User = Depends(get_current_user),
):
    """Save user onboarding preferences (language, grade, board)."""
    body = await request.json()
    update_data = {}
    if "language" in body:
        update_data["language"] = body["language"]
    if "grade" in body:
        update_data["grade"] = body["grade"]
    if "board" in body:
        update_data["board"] = body["board"]
    if "stream" in body:
        update_data["stream"] = body["stream"]
    if update_data:
        update_data["onboarding_complete"] = True
        update_data["updated_at"] = datetime.now(timezone.utc)
        await user.update({"$set": update_data})
    return {"message": "Onboarding saved", "data": update_data}


@router.get("/credits")
async def get_credits(user: User = Depends(get_current_user)):
    """Get current user credit balance and limits."""
    tier = getattr(user, "subscription_tier", "free")
    monthly_limit = 100 if tier == "free" else 1000
    current_count = getattr(user, "monthly_message_count", 0)
    return {
        "credits_remaining": max(0, monthly_limit - current_count),
        "credits_used": current_count,
        "monthly_limit": monthly_limit,
        "tier": tier,
        "lifetime_messages": getattr(user, "total_lifetime_messages", 0),
    }
