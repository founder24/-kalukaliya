from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import logging

from app.models.user import User
from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Users"])


class UserProfile(BaseModel):
    name: str
    email: str
    subscription_tier: str
    monthly_message_count: int
    preferred_language: str


@router.get("/me", response_model=UserProfile)
async def get_current_user_profile(user: User = None):
    """Get current user profile"""
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    return UserProfile(
        name=user.name or "",
        email=user.email or "",
        subscription_tier=user.subscription_tier,
        monthly_message_count=user.monthly_message_count,
        preferred_language=user.preferred_language,
    )


@router.put("/me")
async def update_user_profile(
    name: str = None,
    preferred_language: str = None,
    user: User = None
):
    """Update user profile"""
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    updates = {}
    if name:
        updates["name"] = name
    if preferred_language:
        updates["preferred_language"] = preferred_language
    
    if updates:
        await user.update({"$set": updates})
    
    return {"status": "success", "message": "Profile updated"}


@router.delete("/me")
async def delete_account(user: User = None):
    """Delete user account (GDPR/DPDP compliance)"""
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    # Cascade delete chats
    from app.models.chat import Chat
    await Chat.find({"user_id": str(user.id)}).delete()
    
    # Delete user
    await user.delete()
    
    logger.info(f"User account deleted: {user.email}")
    return {"status": "success", "message": "Account deleted"}
