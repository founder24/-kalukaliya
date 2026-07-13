from beanie import Document
from pydantic import EmailStr, Field
from typing import Optional, Literal, List
from datetime import datetime, timezone
import bcrypt
import hashlib

# bcrypt silently truncates passwords > 72 bytes in older versions;
# bcrypt 4.x raises ValueError instead. We use SHA-256 to derive a
# fixed-length key, which is safe and backward-compatible for new users.
# For legacy passwords (hashed without SHA-256), verify_password tries
# the direct path first, then the SHA-256 path.
_BCRYPT_MAX = 72


def _bcrypt_safe(password: str) -> bytes:
    """Return password bytes safe for bcrypt (max 72 bytes via SHA-256)."""
    raw = password.encode("utf-8")
    if len(raw) <= _BCRYPT_MAX:
        return raw
    return hashlib.sha256(raw).digest()


class User(Document):
    """User Model - MongoDB Schema"""

    email: Optional[EmailStr] = None
    hashed_password: Optional[str] = None
    auth_provider: Literal["local", "anonymous"] = "anonymous"
    role: Optional[Literal["student", "educator", "staff", "admin"]] = "student"

    # Subscription
    subscription_tier: Literal["free", "pro"] = "free"
    subscription_status: Literal["active", "past_due", "cancelled"] = "active"
    razorpay_subscription_id: Optional[str] = None
    razorpay_customer_id: Optional[str] = None
    current_period_start: Optional[datetime] = None
    current_period_end: Optional[datetime] = None
    cancel_at_period_end: bool = False

    # Usage
    monthly_message_count: int = 0
    last_reset_date: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    total_lifetime_messages: int = 0
    credits_remaining: int = 0

    # Profile
    name: Optional[str] = None
    avatar_url: Optional[str] = None
    consent_dpdp: bool = False
    preferred_language: Literal["en", "as"] = "as"
    voice_enabled: bool = True
    theme: str = "light"
    saved_subjects: List[str] = Field(default_factory=list)
    phone: Optional[str] = None

    # Onboarding & preferences
    # NOTE: these fields are written via $set patches but must be declared here
    # so Beanie (Pydantic v2) does not silently drop them on document load.
    onboarding_done: bool = False
    ads_opt_out: bool = False
    grade: Optional[str] = None
    board: Optional[str] = None        # legacy free-text board name
    stream: Optional[str] = None       # legacy free-text stream name
    board_id: Optional[str] = None
    board_name: Optional[str] = None
    class_id: Optional[str] = None
    class_name: Optional[str] = None
    stream_id: Optional[str] = None
    stream_name: Optional[str] = None

    # Usage (written by billing/AI pipelines, read by stats endpoint)
    credits_used: int = 0
    total_tokens_used: int = 0

    # Metadata
    ip_address_first_seen: Optional[str] = None
    user_agent_first_seen: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "users"
        indexes = [
            # NOTE: email unique+sparse index is created explicitly in mongo.py
            # with the correct options. Do NOT define it here — beanie would
            # create a plain non-unique index named "email_1" which conflicts
            # with the unique+sparse one already on Atlas (IndexKeySpecsConflict).
            #
            # razorpay_subscription_id, preferred_language, and created_at are
            # also managed in create_indexes() in mongo.py to avoid duplicate
            # or conflicting beanie-generated definitions.
        ]

    def verify_password(self, password: str) -> bool:
        """Verify password against hashed password.

        Tries the safe (SHA-256 for >72 byte) path first, then falls back
        to the raw-bytes path for passwords hashed before this fix.
        """
        if not self.hashed_password:
            return False
        hashed = self.hashed_password.encode("utf-8")
        safe_bytes = _bcrypt_safe(password)
        try:
            if bcrypt.checkpw(safe_bytes, hashed):
                return True
        except Exception:
            pass
        raw = password.encode("utf-8")
        if raw != safe_bytes:
            try:
                return bcrypt.checkpw(raw[:_BCRYPT_MAX], hashed)
            except Exception:
                return False
        return False

    @classmethod
    def hash_password(cls, password: str) -> str:
        """Hash password using bcrypt with SHA-256 pre-hashing for >72 byte passwords."""
        return bcrypt.hashpw(_bcrypt_safe(password), bcrypt.gensalt()).decode("utf-8")

    def is_pro(self) -> bool:
        """Check if user has Pro subscription"""
        return self.subscription_tier == "pro" and self.subscription_status == "active"
