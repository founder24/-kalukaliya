from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, field_validator
from typing import Optional
from datetime import datetime, timedelta
from jose import jwt, JWTError
import secrets
import logging

from app.config import settings
from app.models.user import User
from app.services.comms.resend_client import send_welcome_email, send_password_reset_email

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Authentication"])
security = HTTPBearer()


# ─── Request / Response Models ───────────────────────────────────────────────


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class SignupRequest(BaseModel):
    email: EmailStr
    password: str
    name: Optional[str] = None

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long")
        return v


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long")
        return v


class MessageResponse(BaseModel):
    message: str


# ─── Token Helpers ───────────────────────────────────────────────────────────


def create_access_token(user_id: str, expires_delta: timedelta = None) -> str:
    """Create JWT access token"""
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=settings.JWT_EXPIRY_MINUTES))
    to_encode = {"sub": user_id, "exp": expire, "type": "access"}
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(user_id: str, expires_delta: timedelta = None) -> str:
    """Create JWT refresh token"""
    expire = datetime.utcnow() + (expires_delta or timedelta(days=settings.REFRESH_TOKEN_EXPIRY_DAYS))
    to_encode = {"sub": user_id, "exp": expire, "type": "refresh"}
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_reset_token(user_id: str) -> str:
    """Create a password-reset JWT token (1 hour expiry)"""
    expire = datetime.utcnow() + timedelta(hours=1)
    to_encode = {"sub": user_id, "exp": expire, "type": "reset"}
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


# ─── Auth Dependencies ───────────────────────────────────────────────────────


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> User:
    """Get current user from JWT token (required — raises 401 if invalid)"""
    token = credentials.credentials
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        user_id = payload.get("sub")
        token_type = payload.get("type")

        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")

        if token_type != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")

        user = await User.get(user_id)
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


# Optional security scheme — does NOT raise if header is missing
security_optional = HTTPBearer(auto_error=False)


async def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_optional),
) -> Optional[User]:
    """
    Get current user from JWT token if present.
    Returns None for anonymous/unsigned users (no 401 raised).
    """
    if credentials is None:
        return None

    token = credentials.credentials
    if not token:
        return None

    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        user_id = payload.get("sub")
        token_type = payload.get("type")

        if not user_id or token_type != "access":
            return None

        user = await User.get(user_id)
        return user
    except JWTError:
        return None


# ─── Routes ──────────────────────────────────────────────────────────────────


@router.post("/signup", response_model=TokenResponse)
async def signup(request: SignupRequest):
    """Register a new user with email + password. Sends a welcome email via Resend."""
    # Check if user exists
    existing_user = await User.find_one({"email": request.email})
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")

    # Create user
    hashed_pw = User.hash_password(request.password)
    user = User(
        email=request.email,
        hashed_password=hashed_pw,
        name=request.name,
        auth_provider="local",
    )
    await user.insert()

    # Generate tokens
    access_token = create_access_token(str(user.id))
    refresh_token = create_refresh_token(str(user.id))

    # Send welcome email (fire-and-forget — don't block signup on email delivery)
    try:
        await send_welcome_email(email=request.email, name=request.name)
    except Exception as e:
        logger.warning(f"Welcome email failed for {request.email}: {e}")

    logger.info(f"New user signed up: {request.email}")
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest):
    """Authenticate user with email + password and return tokens"""
    user = await User.find_one({"email": request.email})

    if not user or not user.hashed_password:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not user.verify_password(request.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Generate tokens
    access_token = create_access_token(str(user.id))
    refresh_token = create_refresh_token(str(user.id))

    logger.info(f"User logged in: {request.email}")
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(request: ForgotPasswordRequest):
    """
    Request a password reset email.
    Always returns success (don't reveal whether email exists).
    """
    user = await User.find_one({"email": request.email})

    if user and user.auth_provider == "local":
        # Generate a signed reset token (1 hour expiry)
        reset_token = create_reset_token(str(user.id))

        # Send reset email via Resend
        try:
            await send_password_reset_email(email=request.email, reset_token=reset_token)
            logger.info(f"Password reset email sent to {request.email}")
        except Exception as e:
            logger.error(f"Failed to send password reset email to {request.email}: {e}")
    else:
        # Don't reveal whether the email exists — log and return same response
        logger.info(f"Password reset requested for non-existent/non-local email: {request.email}")

    return MessageResponse(message="If an account with that email exists, a password reset link has been sent.")


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(request: ResetPasswordRequest):
    """
    Reset password using the token from the email link.
    Token is a JWT with type=reset, 1 hour expiry.
    """
    try:
        payload = jwt.decode(request.token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        token_type = payload.get("type")
        user_id = payload.get("sub")

        if token_type != "reset" or not user_id:
            raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    except JWTError:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    user = await User.get(user_id)
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    # Update password
    user.hashed_password = User.hash_password(request.new_password)
    user.updated_at = datetime.utcnow()
    await user.save()

    logger.info(f"Password reset successful for user {user.email}")
    return MessageResponse(message="Password reset successful. You can now log in with your new password.")


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token_endpoint(refresh_token: str, request: Request = None):
    """Refresh access token using refresh token"""
    # Rate limit refresh endpoint (10 attempts per minute per IP)
    if request:
        try:
            from app.db.redis import get_redis
            import time
            redis = get_redis()
            client_ip = request.client.host if hasattr(request, "client") else "unknown"
            rate_key = f"refresh_limit:{client_ip}:{int(time.time() // 60)}"

            attempt_count = await redis.incr(rate_key)
            if attempt_count == 1:
                await redis.expire(rate_key, 60)

            if attempt_count > 10:
                raise HTTPException(status_code=429, detail="Too many refresh attempts. Try again later.")
        except ImportError:
            pass  # Redis not available — skip rate limiting

    try:
        payload = jwt.decode(refresh_token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")

        user_id = payload.get("sub")
        user = await User.get(user_id)
        if not user:
            raise HTTPException(status_code=401, detail="User not found")

        # Generate new tokens
        new_access_token = create_access_token(str(user.id))
        new_refresh_token = create_refresh_token(str(user.id))

        return TokenResponse(access_token=new_access_token, refresh_token=new_refresh_token)
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
