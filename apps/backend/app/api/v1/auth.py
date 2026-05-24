from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional
from datetime import datetime, timedelta, timezone
from jose import jwt, JWTError
import hashlib
import logging
import time
import uuid

from app.config import settings
from app.models.user import User
from app.services.comms.resend_client import (
    send_welcome_email,
    send_password_reset_email,
)

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
    consent_dpdp: bool = False

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long")
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.islower() for c in v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
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
        if not any(c.isupper() for c in v):
            raise ValueError("Password must contain at least one uppercase letter")
        if not any(c.islower() for c in v):
            raise ValueError("Password must contain at least one lowercase letter")
        if not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one digit")
        return v


class MessageResponse(BaseModel):
    message: str


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


# ─── Token Helpers ───────────────────────────────────────────────────────────


def create_access_token(user_id: str, expires_delta: timedelta = None) -> str:
    """Create JWT access token"""
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.JWT_EXPIRY_MINUTES)
    )
    to_encode = {"sub": user_id, "exp": expire, "type": "access"}
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(user_id: str, expires_delta: timedelta = None) -> str:
    """Create JWT refresh token"""
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(days=settings.REFRESH_TOKEN_EXPIRY_DAYS)
    )
    to_encode = {
        "sub": user_id,
        "exp": expire,
        "type": "refresh",
        "jti": str(uuid.uuid4()),
    }
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_reset_token(user_id: str) -> str:
    """Create a password-reset JWT token (1 hour expiry)"""
    expire = datetime.now(timezone.utc) + timedelta(hours=1)
    to_encode = {"sub": user_id, "exp": expire, "type": "reset"}
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


# ─── Auth Dependencies ───────────────────────────────────────────────────────


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> User:
    """Get current user from JWT token (required — raises 401 if invalid)"""
    token = credentials.credentials
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
        user_id = payload.get("sub")
        token_type = payload.get("type")

        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")

        if token_type != "access":
            raise HTTPException(status_code=401, detail="Invalid token type")

        # Check if token has been blacklisted (logout)
        try:
            from app.db.redis import get_redis

            redis = get_redis()
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            blacklisted = await redis.get(f"blacklisted_token:{token_hash}")
            if blacklisted:
                raise HTTPException(status_code=401, detail="Token has been revoked")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Redis unavailable for token blacklist check: {e}")
            raise HTTPException(status_code=503, detail="Token validation service unavailable")

        user = await User.get(user_id)
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user
    except HTTPException:
        raise
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
        payload = jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
        user_id = payload.get("sub")
        token_type = payload.get("type")

        if not user_id or token_type != "access":
            return None

        user = await User.get(user_id)
        return user
    except JWTError:
        return None


# ─── Rate Limiting Helper ─────────────────────────────────────────────────────


async def _check_rate_limit(request: Request, endpoint: str, max_attempts: int) -> None:
    """
    IP-based rate limiting using Upstash Redis.
    Raises HTTP 429 if limit exceeded. Silently skips if Redis unavailable.
    """
    try:
        from app.db.redis import get_redis

        redis = get_redis()
        client_ip = request.client.host if request.client else "unknown"
        minute_bucket = int(time.time() // 60)
        rate_key = f"auth_limit:{endpoint}:{client_ip}:{minute_bucket}"

        attempt_count = await redis.incr(rate_key)
        if attempt_count == 1:
            await redis.expire(rate_key, 60)

        if attempt_count > max_attempts:
            raise HTTPException(
                status_code=429,
                detail=f"Too many {endpoint} attempts. Please try again in 1 minute.",
            )
    except HTTPException:
        raise  # Re-raise 429
    except Exception:
        pass  # Redis unavailable - skip rate limiting gracefully


# ─── Routes ──────────────────────────────────────────────────────────────────


@router.post("/signup", response_model=TokenResponse)
async def signup(request_body: SignupRequest, request: Request):
    """Register a new user with email + password. Sends a welcome email via Resend."""
    await _check_rate_limit(request, "signup", 5)

    # Check if user exists
    existing_user = await User.find_one({"email": request_body.email})
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")

    # Create user
    hashed_pw = User.hash_password(request_body.password)
    user = User(
        email=request_body.email,
        hashed_password=hashed_pw,
        name=request_body.name,
        auth_provider="local",
        consent_dpdp=request_body.consent_dpdp,
    )
    await user.insert()

    # Generate tokens
    access_token = create_access_token(str(user.id))
    refresh_token = create_refresh_token(str(user.id))

    # Send welcome email (fire-and-forget — don't block signup on email delivery)
    try:
        await send_welcome_email(email=request_body.email, name=request_body.name)
    except Exception as e:
        logger.warning(f"Welcome email failed for {request_body.email}: {e}")

    logger.info(f"New user signed up: {request_body.email}")
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/login", response_model=TokenResponse)
async def login(request_body: LoginRequest, request: Request):
    """Authenticate user with email + password and return tokens"""
    await _check_rate_limit(request, "login", 10)

    user = await User.find_one({"email": request_body.email})

    if not user or not user.hashed_password:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not user.verify_password(request_body.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Generate tokens
    access_token = create_access_token(str(user.id))
    refresh_token = create_refresh_token(str(user.id))

    logger.info(f"User logged in: {request_body.email}")
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
            await send_password_reset_email(
                email=request.email, reset_token=reset_token
            )
            logger.info(f"Password reset email sent to {request.email}")
        except Exception as e:
            logger.error(f"Failed to send password reset email to {request.email}: {e}")
    else:
        # Don't reveal whether the email exists — log and return same response
        logger.info(
            f"Password reset requested for non-existent/non-local email: {request.email}"
        )

    return MessageResponse(
        message="If an account with that email exists, a password reset link has been sent."
    )


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(request: ResetPasswordRequest):
    """
    Reset password using the token from the email link.
    Token is a JWT with type=reset, 1 hour expiry.
    Tokens are single-use (enforced via Redis).
    """
    try:
        payload = jwt.decode(
            request.token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
        token_type = payload.get("type")
        user_id = payload.get("sub")

        if token_type != "reset" or not user_id:
            raise HTTPException(
                status_code=400, detail="Invalid or expired reset token"
            )

    except JWTError:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    # SEC-C4: Check if reset token has already been used
    token_hash = hashlib.sha256(request.token.encode()).hexdigest()
    try:
        from app.db.redis import get_redis

        redis = get_redis()
        used = await redis.get(f"used_reset:{token_hash}")
        if used:
            raise HTTPException(
                status_code=400, detail="Reset token has already been used"
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Redis unavailable for reset token single-use check: {e}")
        pass  # Redis unavailable - allow reset (defense in depth, token still has 1h expiry)

    user = await User.get(user_id)
    if not user:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    # AUTH-W4: Reject password reset for OAuth accounts
    if (
        hasattr(user, "auth_provider")
        and user.auth_provider
        and user.auth_provider != "local"
    ):
        raise HTTPException(
            status_code=400,
            detail="Password reset is not available for OAuth accounts. Please sign in with your OAuth provider.",
        )

    # Update password
    user.hashed_password = User.hash_password(request.new_password)
    user.updated_at = datetime.now(timezone.utc)
    await user.save()

    # Mark token as used in Redis
    try:
        from app.db.redis import get_redis

        redis = get_redis()
        await redis.set(f"used_reset:{token_hash}", "1", ex=3600)  # 1 hour TTL
    except Exception as e:
        logger.error(f"Redis unavailable for marking reset token as used: {e}")

    logger.info(f"Password reset successful for user {user.email}")
    return MessageResponse(
        message="Password reset successful. You can now log in with your new password."
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token_endpoint(body: RefreshTokenRequest, request: Request = None):
    """Refresh access token using refresh token"""
    # Rate limit refresh endpoint (10 attempts per minute per IP)
    if request:
        await _check_rate_limit(request, "refresh", 10)

    try:
        payload = jwt.decode(
            body.refresh_token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")

        user_id = payload.get("sub")
        jti = payload.get("jti")

        # Check if token has been revoked
        if jti:
            try:
                from app.db.redis import get_redis

                redis = get_redis()
                revoked = await redis.get(f"revoked_refresh:{jti}")
                if revoked:
                    raise HTTPException(
                        status_code=401, detail="Token has been revoked"
                    )
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Redis unavailable for token revocation check: {e}")
                raise HTTPException(status_code=503, detail="Token validation service unavailable")

        user = await User.get(user_id)
        if not user:
            raise HTTPException(status_code=401, detail="User not found")

        # Generate new tokens
        new_access_token = create_access_token(str(user.id))
        new_refresh_token = create_refresh_token(str(user.id))

        # Revoke old refresh token jti
        if jti:
            try:
                from app.db.redis import get_redis

                redis = get_redis()
                await redis.set(
                    f"revoked_refresh:{jti}",
                    "1",
                    ex=settings.REFRESH_TOKEN_EXPIRY_DAYS * 86400,
                )
            except Exception as e:
                logger.error(
                    f"Redis unavailable for refresh token revocation storage: {e}"
                )

        return TokenResponse(
            access_token=new_access_token, refresh_token=new_refresh_token
        )
    except HTTPException:
        raise
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")


@router.post("/logout", response_model=MessageResponse)
async def logout(
    body: LogoutRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    user: User = Depends(get_current_user),
):
    """
    Logout user by blacklisting their access token in Redis.
    Revokes the refresh token as well.
    Raises 503 if Redis is unavailable (fail-closed).
    """
    token = credentials.credentials

    try:
        from app.db.redis import get_redis

        redis = get_redis()

        # Blacklist the access token
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        payload = jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
        exp = payload.get("exp", 0)
        now = int(datetime.now(timezone.utc).timestamp())
        ttl = max(exp - now, 0)
        if ttl > 0:
            await redis.set(f"blacklisted_token:{token_hash}", "1", ex=ttl)

        # Revoke the refresh token
        try:
            refresh_payload = jwt.decode(
                body.refresh_token,
                settings.JWT_SECRET,
                algorithms=[settings.JWT_ALGORITHM],
            )
            jti = refresh_payload.get("jti")
            if jti:
                refresh_exp = refresh_payload.get("exp", 0)
                refresh_ttl = max(refresh_exp - now, 0)
                if refresh_ttl > 0:
                    await redis.set(f"revoked_refresh:{jti}", "1", ex=refresh_ttl)
        except JWTError:
            pass  # Invalid refresh token - ignore
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Redis unavailable for token blacklisting during logout: {e}")
        raise HTTPException(status_code=503, detail="Token revocation service unavailable")

    return MessageResponse(message="Logged out successfully")
