from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional
from datetime import datetime, timedelta, timezone
import jwt
from jwt.exceptions import InvalidTokenError, ExpiredSignatureError
import hashlib
import hmac
import logging
import time
import uuid

from app.config import settings
from app.models.user import User
from app.services.comms.resend_client import (
    send_welcome_email,
    send_password_reset_email,
)

try:
    from beanie.exceptions import CollectionWasNotInitialized
except ImportError:  # pragma: no cover
    CollectionWasNotInitialized = None

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
    access_token: Optional[str] = None


class LogoutRequest(BaseModel):
    refresh_token: Optional[str] = Field(default=None)


# ─── Token Helpers ───────────────────────────────────────────────────────────


_PLACEHOLDER_SECRETS = {
    "dev-only-secret-not-for-production-use-32chars",
    "super_secret_jwt_key_32_chars_min",
    "CHANGE_ME_IN_PRODUCTION_AT_LEAST_32_CHARS_LONG",
    "test-secret-at-least-32-characters-long",
    "changeme",
    "secret",
    "your-secret-key",
    "your-256-bit-secret",
    "jwt-secret",
}


def _get_signing_key() -> tuple[str, str]:
    """Get the key and algorithm for signing JWTs.
    Returns (key, algorithm).

    Priority order:
    1. RS256 with JWT_PRIVATE_KEY — most secure, preferred for production
    2. HS256 with JWT_SECRET — acceptable if JWT_SECRET is non-placeholder and ≥ 32 chars
    3. HS256 with placeholder secret + CRITICAL log — degraded mode, tokens are weak
       but the app stays up so operators can see the log and fix configuration.
    """
    if settings.JWT_ALGORITHM == "RS256" and settings.JWT_PRIVATE_KEY:
        return settings.JWT_PRIVATE_KEY, "RS256"
    if settings.JWT_ALGORITHM == "RS256":
        logger.warning(
            "JWT_ALGORITHM is RS256 but JWT_PRIVATE_KEY is not set — falling back to HS256"
        )
    jwt_secret = settings.JWT_SECRET.strip()
    if settings.APP_ENV == "production":
        if not jwt_secret or jwt_secret in _PLACEHOLDER_SECRETS or len(jwt_secret) < 32:
            # Log critical but do NOT raise — raising here crashes every auth request
            # and fills Sentry with noise instead of one clear startup warning.
            # Operators must set JWT_SECRET (≥32 chars) or JWT_PRIVATE_KEY in Cloud Run.
            logger.critical(
                "SECURITY: JWT_SECRET is a placeholder/weak value in production. "
                "Set JWT_SECRET env var (≥32 random chars) in Cloud Run immediately. "
                "Tokens signed with this key are INSECURE."
            )
    return jwt_secret, "HS256"


def _get_verification_key() -> tuple[str, str]:
    """Get the key and algorithm for verifying JWTs.
    Returns (key, algorithm).

    Priority order:
    1. RS256 with JWT_PUBLIC_KEY — matches RS256 signing
    2. HS256 with JWT_SECRET — matches HS256 signing
    3. HS256 with placeholder secret + CRITICAL log — degraded mode, see _get_signing_key.
    """
    if settings.JWT_ALGORITHM == "RS256" and settings.JWT_PUBLIC_KEY:
        return settings.JWT_PUBLIC_KEY, "RS256"
    if settings.JWT_ALGORITHM == "RS256":
        logger.warning(
            "JWT_ALGORITHM is RS256 but JWT_PUBLIC_KEY is not set — falling back to HS256"
        )
    jwt_secret = settings.JWT_SECRET.strip()
    if settings.APP_ENV == "production":
        if not jwt_secret or jwt_secret in _PLACEHOLDER_SECRETS or len(jwt_secret) < 32:
            logger.critical(
                "SECURITY: JWT_SECRET is a placeholder/weak value in production. "
                "Set JWT_SECRET env var (≥32 random chars) in Cloud Run immediately."
            )
    return jwt_secret, "HS256"


def _decode_token_with_fallback(token: str) -> dict:
    """Decode a JWT trying HS256 first, then RS256 for legacy tokens.

    Tokens issued while JWT_ALGORITHM=RS256 was set in Cloud Run are still
    circulating during the HS256 migration window.  Trying RS256 as a fallback
    prevents InvalidAlgorithmError on logout for those tokens.
    Once all RS256 tokens have expired (after token TTL), remove the RS256 branch.
    """
    key, algorithm = _get_verification_key()
    try:
        return jwt.decode(token, key, algorithms=[algorithm])
    except Exception as primary_err:
        if algorithm == "HS256" and settings.JWT_PUBLIC_KEY:
            try:
                payload = jwt.decode(
                    token,
                    settings.JWT_PUBLIC_KEY,
                    algorithms=["RS256"],
                    options={"verify_exp": True},
                )
                logger.info("Decoded legacy RS256 token via fallback path (expected during migration)")
                return payload
            except Exception:
                pass
        raise primary_err


def create_access_token(user_id: str, expires_delta: timedelta = None) -> str:
    """Create JWT access token"""
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.JWT_EXPIRY_MINUTES)
    )
    to_encode = {"sub": user_id, "exp": expire, "type": "access"}
    key, algorithm = _get_signing_key()
    return jwt.encode(to_encode, key, algorithm=algorithm)


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
    key, algorithm = _get_signing_key()
    return jwt.encode(to_encode, key, algorithm=algorithm)


def create_reset_token(user_id: str) -> str:
    """Create a password-reset JWT token (1 hour expiry)"""
    expire = datetime.now(timezone.utc) + timedelta(hours=1)
    to_encode = {"sub": user_id, "exp": expire, "type": "reset"}
    if settings.RESET_TOKEN_SECRET:
        return jwt.encode(to_encode, settings.RESET_TOKEN_SECRET, algorithm="HS256")
    key, algorithm = _get_signing_key()
    return jwt.encode(to_encode, key, algorithm=algorithm)


# ─── Auth Dependencies ───────────────────────────────────────────────────────

security_optional = HTTPBearer(auto_error=False)


def _verify_edge_hmac(request: Request, edge_secret: str) -> tuple[bool, str]:
    """
    Verify per-request HMAC signature from edge worker.
    Returns (is_valid, user_id).

    Signature format: HMAC-SHA256(secret, "timestamp:user_id:path")
    """
    signature = request.headers.get("X-Edge-Signature")
    timestamp_str = request.headers.get("X-Edge-Timestamp")
    user_id = request.headers.get("X-User-ID")

    if not signature or not timestamp_str or not user_id:
        return False, ""

    # Reject stale requests (>30 seconds old)
    try:
        timestamp = int(timestamp_str)
    except (ValueError, TypeError):
        return False, ""

    now = int(time.time())
    if abs(now - timestamp) > 30:
        logger.warning(f"Edge HMAC timestamp too old: {abs(now - timestamp)}s")
        return False, ""

    # Compute expected signature
    message = f"{timestamp_str}:{user_id}:{request.url.path}"
    expected = hmac.HMAC(
        edge_secret.encode(), message.encode(), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(signature, expected):
        return False, ""

    return True, user_id


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_optional),
) -> User:
    """Get current user from JWT token (required -- raises 401 if invalid).
    Supports edge-trust bypass: if TRUST_EDGE_AUTH is enabled and X-Edge-Secret
    matches, trusts X-User-ID header directly (skips JWT decode).
    """
    # Edge-trust bypass with HMAC signature verification (SEC-002)
    edge_secret_header = request.headers.get("X-Edge-Secret") or ""
    if (
        settings.TRUST_EDGE_AUTH
        and settings.EDGE_SHARED_SECRET is not None
        and hmac.compare_digest(edge_secret_header, settings.EDGE_SHARED_SECRET)
    ):
        user_id_header = request.headers.get("X-User-ID")
        if user_id_header and user_id_header != "anonymous":
            # Prefer HMAC signature verification (new secure path)
            hmac_valid, hmac_user_id = _verify_edge_hmac(
                request, settings.EDGE_SHARED_SECRET
            )
            if hmac_valid:
                user = await User.get(hmac_user_id)
                if not user:
                    raise HTTPException(status_code=401, detail="User not found")
                return user

            # Backward compatibility: allow shared-secret-only during rollover
            # (will be removed once all edge workers are updated)
            if not request.headers.get("X-Edge-Signature"):
                logger.warning(
                    "Edge request using legacy shared-secret-only auth (no HMAC signature). "
                    "Update edge worker to include X-Edge-Signature."
                )
                user = await User.get(user_id_header)
                if not user:
                    raise HTTPException(status_code=401, detail="User not found")
                return user

            # Signature present but invalid - reject
            raise HTTPException(status_code=401, detail="Invalid edge signature")

    # No edge trust -- require credentials
    # X-User-JWT is set by the CF edge proxy to preserve the user's original JWT
    # when it overwrites Authorization with the Cloud Run OIDC identity token.
    user_jwt_header = request.headers.get("X-User-JWT", "")
    if user_jwt_header.startswith("Bearer "):
        user_jwt_header = user_jwt_header[7:]

    # Prefer X-User-JWT (original user token) if present; fall back to Authorization
    raw_token = user_jwt_header or (credentials.credentials if credentials else None)

    if raw_token is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    token = raw_token
    try:
        key, algorithm = _get_verification_key()
        payload = jwt.decode(token, key, algorithms=[algorithm])
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
            # Fail-closed for payment/subscription endpoints
            req_path = str(request.url.path) if request else ""
            if req_path.startswith("/api/v1/payments/") or req_path.startswith(
                "/api/v1/credit-topup"
            ):
                raise HTTPException(
                    status_code=503,
                    detail="Token validation service unavailable for payment operations",
                )
            # Fail-open for non-payment paths: JWT is still cryptographically valid

        user = await User.get(user_id)
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user
    except HTTPException:
        raise
    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="token_expired")
    except InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    except RuntimeError as e:
        logger.error(f"Authentication service configuration error: {e}")
        raise HTTPException(
            status_code=503, detail="Authentication service misconfigured"
        )
    except Exception as e:
        if CollectionWasNotInitialized and isinstance(e, CollectionWasNotInitialized):
            raise HTTPException(status_code=503, detail="Database service unavailable")
        logger.error(f"Unexpected error in authentication: {e}")
        raise HTTPException(status_code=401, detail="Not authenticated")


async def get_current_user_optional(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_optional),
) -> Optional[User]:
    """
    Get current user from JWT token if present.
    Returns None for anonymous/unsigned users (no 401 raised).
    Supports edge-trust bypass: if TRUST_EDGE_AUTH is enabled and X-Edge-Secret
    matches, trusts X-User-ID header directly.
    """
    # Edge-trust bypass: if edge shared secret matches, trust X-User-ID header
    edge_secret_opt = request.headers.get("X-Edge-Secret") or ""
    if (
        settings.TRUST_EDGE_AUTH
        and settings.EDGE_SHARED_SECRET is not None
        and hmac.compare_digest(edge_secret_opt, settings.EDGE_SHARED_SECRET)
    ):
        user_id_header = request.headers.get("X-User-ID")
        if not user_id_header or user_id_header == "anonymous":
            return None

        # Prefer HMAC signature verification (new secure path)
        hmac_valid, hmac_user_id = _verify_edge_hmac(
            request, settings.EDGE_SHARED_SECRET
        )
        if hmac_valid:
            user = await User.get(hmac_user_id)
            return user

        # Backward compatibility: allow shared-secret-only during rollover
        if not request.headers.get("X-Edge-Signature"):
            logger.warning(
                "Edge request using legacy shared-secret-only auth (no HMAC signature)."
            )
            user = await User.get(user_id_header)
            return user

        # Signature present but invalid
        return None

    # X-User-JWT fallback: CF edge proxy preserves original user JWT here
    # when it overwrites Authorization with Cloud Run OIDC identity token.
    user_jwt_opt = request.headers.get("X-User-JWT", "")
    if user_jwt_opt.startswith("Bearer "):
        user_jwt_opt = user_jwt_opt[7:]

    raw_token_opt = user_jwt_opt or (credentials.credentials if credentials else None)

    if not raw_token_opt:
        return None

    token = raw_token_opt
    try:
        key, algorithm = _get_verification_key()
        payload = jwt.decode(token, key, algorithms=[algorithm])
        user_id = payload.get("sub")
        token_type = payload.get("type")

        if not user_id or token_type != "access":
            return None

        # Check token blacklist
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        try:
            from app.db.redis import get_redis

            redis = get_redis()
            is_blacklisted = await redis.get(f"blacklisted_token:{token_hash}")
            if is_blacklisted:
                return None  # treat as anonymous
        except Exception:
            pass  # fail-open acceptable for optional auth

        user = await User.get(user_id)
        return user
    except InvalidTokenError:
        return None


# ─── Rate Limiting Helper ─────────────────────────────────────────────────────


async def _check_rate_limit(request: Request, endpoint: str, max_attempts: int) -> None:
    """
    IP-based rate limiting using Upstash Redis.
    Raises HTTP 429 if limit exceeded. Fails open (logs warning, allows request)
    if Redis is unavailable — blocking auth entirely is worse than a brief burst.
    In development mode, rate limiting is skipped entirely so local/Replit dev works
    without Redis configured.
    """
    if settings.APP_ENV == "development":
        return
    try:
        from app.db.redis import get_redis

        redis = get_redis()
        client_ip = (
            request.headers.get("CF-Connecting-IP")
            or request.headers.get("X-Real-IP")
            or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or (request.client.host if request.client else "unknown")
        )
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
    except Exception as e:
        # Fail-open: Redis unavailable → log and allow the request through.
        # Blocking auth entirely when Redis is down is worse than the risk of
        # a burst of unauthenticated attempts; bcrypt cost still throttles
        # brute-force, and Cloudflare WAF provides an outer rate-limit layer.
        logger.warning(
            f"Rate limiting unavailable ({endpoint}), failing open: {type(e).__name__}"
        )


# ─── Routes ──────────────────────────────────────────────────────────────────


@router.post("/signup", response_model=TokenResponse)
async def signup(request_body: SignupRequest, request: Request):
    """Register a new user with email + password. Sends a welcome email via Resend."""
    await _check_rate_limit(request, "signup", 5)

    # Check if user exists
    try:
        existing_user = await User.find_one({"email": request_body.email})
    except Exception as e:
        if CollectionWasNotInitialized and isinstance(e, CollectionWasNotInitialized):
            raise HTTPException(status_code=503, detail="Database service unavailable")
        logger.error(f"Unexpected database error: {e}")
        raise
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
    try:
        await user.insert()
    except Exception as e:
        # Catch MongoDB DuplicateKeyError from unique index on email.
        # This handles the race condition where two concurrent requests with the
        # same email both pass the existence check above and then both try to insert.
        _e_str = str(e).lower()
        if "duplicate" in _e_str or "11000" in _e_str or "e11000" in _e_str:
            raise HTTPException(status_code=400, detail="Email already registered")
        logger.error(f"User insert failed: {e}")
        raise

    # Generate tokens
    access_token = create_access_token(str(user.id))
    refresh_token = create_refresh_token(str(user.id))

    # Send welcome email (fire-and-forget — don't block signup on email delivery)
    try:
        await send_welcome_email(email=request_body.email, name=request_body.name)
    except Exception as e:
        logger.warning(f"Welcome email failed for {request_body.email}: {e}")

    logger.info(
        f"New user signed up: {request_body.email[:3]}***@{request_body.email.split('@')[1]}"
    )
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/login", response_model=TokenResponse)
async def login(request_body: LoginRequest, request: Request):
    """Authenticate user with email + password and return tokens"""
    await _check_rate_limit(request, "login", 10)

    try:
        user = await User.find_one({"email": request_body.email})
    except Exception as e:
        if CollectionWasNotInitialized and isinstance(e, CollectionWasNotInitialized):
            raise HTTPException(status_code=503, detail="Database service unavailable")
        logger.error(f"Unexpected database error: {e}")
        raise

    if not user or not user.hashed_password:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not user.verify_password(request_body.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Generate tokens
    access_token = create_access_token(str(user.id))
    refresh_token = create_refresh_token(str(user.id))

    logger.info(
        f"User logged in: {request_body.email[:3]}***@{request_body.email.split('@')[1]}"
    )
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(request_body: ForgotPasswordRequest, request: Request):
    """
    Request a password reset email.
    Always returns success (don't reveal whether email exists).
    """
    await _check_rate_limit(request, "forgot_password", 3)

    try:
        user = await User.find_one({"email": request_body.email})
    except Exception as e:
        if CollectionWasNotInitialized and isinstance(e, CollectionWasNotInitialized):
            raise HTTPException(status_code=503, detail="Database service unavailable")
        logger.error(f"Unexpected database error: {e}")
        raise

    if user and user.auth_provider == "local":
        # Generate a signed reset token (1 hour expiry)
        reset_token = create_reset_token(str(user.id))

        # Send reset email via Resend
        try:
            await send_password_reset_email(
                email=request_body.email, reset_token=reset_token
            )
            logger.info(f"Password reset email sent to {request_body.email}")
        except Exception as e:
            logger.error(
                f"Failed to send password reset email to {request_body.email}: {e}"
            )
    else:
        # Don't reveal whether the email exists — log and return same response
        logger.info(
            f"Password reset requested for non-existent/non-local email: {request_body.email}"
        )

    return MessageResponse(
        message="If an account with that email exists, a password reset link has been sent."
    )


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(body: ResetPasswordRequest):
    """
    Reset password using the token from the email link.
    Token is a JWT with type=reset, 1 hour expiry.
    Tokens are single-use (enforced via Redis).
    """
    try:
        if settings.RESET_TOKEN_SECRET:
            payload = jwt.decode(
                body.token,
                settings.RESET_TOKEN_SECRET,
                algorithms=["HS256"],
            )
        else:
            key, algorithm = _get_verification_key()
            payload = jwt.decode(
                body.token,
                key,
                algorithms=[algorithm],
            )
        token_type = payload.get("type")
        user_id = payload.get("sub")

        if token_type != "reset" or not user_id:
            raise HTTPException(
                status_code=400, detail="Invalid or expired reset token"
            )

    except InvalidTokenError:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    # SEC-C4: Check if reset token has already been used
    token_hash = hashlib.sha256(body.token.encode()).hexdigest()
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

    # HF-003: Prevent password reuse
    if user.verify_password(body.new_password):
        raise HTTPException(
            status_code=400,
            detail="New password must be different from current password",
        )

    # Update password
    user.hashed_password = User.hash_password(body.new_password)
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
        key, algorithm = _get_verification_key()
        payload = jwt.decode(body.refresh_token, key, algorithms=[algorithm])
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Invalid token type")

        user_id = payload.get("sub")
        jti = payload.get("jti")

        user = await User.get(user_id)
        if not user:
            raise HTTPException(status_code=401, detail="User not found")

        # Generate new tokens
        new_access_token = create_access_token(str(user.id))
        new_refresh_token = create_refresh_token(str(user.id))

        # Atomically claim the old token (SET NX ensures only one request succeeds)
        if jti:
            try:
                from app.db.redis import get_redis

                redis = get_redis()
                claimed = await redis.set(
                    f"revoked_refresh:{jti}",
                    "1",
                    ex=settings.REFRESH_TOKEN_EXPIRY_DAYS * 86400,
                    nx=True,
                )
                if not claimed:
                    raise HTTPException(
                        status_code=401, detail="Token has been revoked"
                    )
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Redis unavailable for refresh token revocation: {e}")
                raise HTTPException(
                    status_code=503, detail="Token validation service unavailable"
                )

        # Blacklist the old access token if provided (token rotation)
        if body.access_token:
            try:
                old_hash = hashlib.sha256(body.access_token.encode()).hexdigest()
                key_v, alg_v = _get_verification_key()
                old_payload = jwt.decode(body.access_token, key_v, algorithms=[alg_v])
                old_exp = old_payload.get("exp", 0)
                now_ts = int(datetime.now(timezone.utc).timestamp())
                remaining_ttl = max(old_exp - now_ts, 0)
                if remaining_ttl > 0:
                    from app.db.redis import get_redis

                    redis = get_redis()
                    await redis.set(
                        f"blacklisted_token:{old_hash}", "1", ex=remaining_ttl, nx=True
                    )
            except Exception as e:
                logger.warning(
                    f"Failed to blacklist old access token during refresh: {e}"
                )

        return TokenResponse(
            access_token=new_access_token, refresh_token=new_refresh_token
        )
    except HTTPException:
        raise
    except InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    except RuntimeError as e:
        logger.error(f"Authentication service configuration error: {e}")
        raise HTTPException(
            status_code=503, detail="Authentication service misconfigured"
        )
    except Exception as e:
        if CollectionWasNotInitialized and isinstance(e, CollectionWasNotInitialized):
            raise HTTPException(status_code=503, detail="Database service unavailable")
        logger.error(f"Unexpected error during token refresh: {e}")
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")


@router.post("/logout", response_model=MessageResponse)
async def logout(
    request: Request,
    body: LogoutRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    user: User = Depends(get_current_user),
):
    """
    Logout user by blacklisting their access token in Redis.
    Revokes the refresh token as well.
    Raises 503 if Redis is unavailable (fail-closed).
    """
    # The CF Worker replaces Authorization with its own OIDC identity token
    # and puts the original user JWT in X-User-JWT.  Use the same resolution
    # order as get_current_user so we always blacklist the user's actual token.
    user_jwt_header = request.headers.get("X-User-JWT", "")
    if user_jwt_header.startswith("Bearer "):
        user_jwt_header = user_jwt_header[7:]
    token = user_jwt_header or credentials.credentials

    # Decode the access token to get its expiry (get_current_user already
    # validated it, so this should not fail; any exception here is a config
    # problem, not a Redis problem — raise 500, not 503).
    try:
        from app.db.redis import get_redis

        payload = _decode_token_with_fallback(token)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to decode token during logout: {type(e).__name__}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Logout failed: token decode error")

    token_hash = hashlib.sha256(token.encode()).hexdigest()
    exp = payload.get("exp", 0)
    now = int(datetime.now(timezone.utc).timestamp())
    ttl = max(exp - now, 0)

    # Blacklist the access token in Redis.  Fail-open: if Redis is temporarily
    # unavailable the token expires naturally (max TTL = access token lifetime,
    # typically 15 min).  We log a warning so the ops team can investigate.
    try:
        redis = get_redis()

        if ttl > 0:
            await redis.set(f"blacklisted_token:{token_hash}", "1", ex=ttl)

        # Revoke the refresh token (best-effort; client may not supply one)
        if body.refresh_token:
            try:
                refresh_payload = jwt.decode(
                    body.refresh_token,
                    key,
                    algorithms=[algorithm],
                )
                jti = refresh_payload.get("jti")
                if jti:
                    refresh_exp = refresh_payload.get("exp", 0)
                    refresh_ttl = max(refresh_exp - now, 0)
                    if refresh_ttl > 0:
                        await redis.set(f"revoked_refresh:{jti}", "1", ex=refresh_ttl)
            except InvalidTokenError:
                pass  # Invalid refresh token — ignore
    except Exception as e:
        # Redis unavailable or Upstash transient error.  Log with full trace
        # so production logs capture the real cause.  Proceed with logout so
        # the user is not stuck — the access token will expire on its own.
        logger.warning(
            f"Token blacklisting skipped during logout (Redis error): "
            f"{type(e).__name__}: {e}",
            exc_info=True,
        )

    return MessageResponse(message="Logged out successfully")
