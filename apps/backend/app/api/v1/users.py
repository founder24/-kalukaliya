from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
import logging

from app.models.user import User
from app.api.v1.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Users"])


class UserProfile(BaseModel):
    name: str
    email: str
    subscription_tier: str
    monthly_message_count: int
    preferred_language: str


class UpdateProfileRequest(BaseModel):
    name: Optional[str] = None
    preferred_language: Optional[str] = None


@router.get("/me", response_model=UserProfile)
async def get_current_user_profile(user: User = Depends(get_current_user)):
    """Get current user profile"""
    return UserProfile(
        name=user.name or "",
        email=user.email or "",
        subscription_tier=user.subscription_tier,
        monthly_message_count=user.monthly_message_count,
        preferred_language=user.preferred_language,
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
