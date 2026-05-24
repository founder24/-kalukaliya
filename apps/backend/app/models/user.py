from beanie import Document
from pydantic import EmailStr, Field
from typing import Optional, Literal
from datetime import datetime
import bcrypt


class User(Document):
    """User Model - MongoDB Schema"""
    
    email: Optional[EmailStr] = None
    hashed_password: Optional[str] = None
    auth_provider: Literal["local", "anonymous"] = "anonymous"
    role: Optional[str] = None  # 'student', 'educator', 'staff', 'admin'
    
    # Subscription
    subscription_tier: Literal["free", "pro"] = "free"
    subscription_status: Literal["active", "past_due", "cancelled", "trialing"] = "active"
    razorpay_subscription_id: Optional[str] = None
    razorpay_customer_id: Optional[str] = None
    current_period_start: Optional[datetime] = None
    current_period_end: Optional[datetime] = None
    cancel_at_period_end: bool = False
    
    # Usage
    monthly_message_count: int = 0
    last_reset_date: datetime = Field(default_factory=datetime.utcnow)
    total_lifetime_messages: int = 0
    
    # Profile
    name: Optional[str] = None
    avatar_url: Optional[str] = None
    consent_dpdp: bool = False
    preferred_language: Literal["en", "as"] = "as"
    voice_enabled: bool = True
    theme: str = "light"
    
    # Metadata
    ip_address_first_seen: Optional[str] = None
    user_agent_first_seen: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    class Settings:
        name = "users"
        indexes = [
            [("email", 1)],  # Unique index created in mongo.py
            [("subscription.razorpay_subscription_id", 1)],
            [("preferred_language", 1)],
            [("created_at", -1)],
        ]

    def verify_password(self, password: str) -> bool:
        """Verify password against hashed password"""
        if not self.hashed_password:
            return False
        return bcrypt.checkpw(password.encode('utf-8'), self.hashed_password.encode('utf-8'))

    @classmethod
    def hash_password(cls, password: str) -> str:
        """Hash password using bcrypt"""
        return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

    def is_pro(self) -> bool:
        """Check if user has Pro subscription"""
        return self.subscription_tier == "pro" and self.subscription_status == "active"
