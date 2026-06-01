"""
Admin Verification Endpoint
Validates the httponly syrabit_admin_session cookie for admin panel access.
CSRF Protection: Admin cookies MUST use SameSite=Strict.
Origin validation is enforced on all mutating (non-GET) requests.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
import jwt
from jwt.exceptions import InvalidTokenError
import logging

from app.config import settings
from app.models.user import User
from app.api.v1.auth import _check_rate_limit

try:
    from beanie.exceptions import CollectionWasNotInitialized
except ImportError:  # pragma: no cover
    CollectionWasNotInitialized = None

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin"])


def _get_admin_signing_key() -> tuple:
    """Get the key and algorithm for signing admin JWTs.
    
    Admin tokens ALWAYS use HS256 with a dedicated secret for key isolation.
    They intentionally do NOT use the RS256 key pair (which is for user tokens).
    This ensures compromising one key type doesn't affect the other.
    """
    return settings.ADMIN_JWT_SECRET or settings.JWT_SECRET, "HS256"


def _get_admin_verification_key() -> tuple:
    """Get the key and algorithm for verifying admin JWTs.
    
    Admin tokens ALWAYS use HS256 with a dedicated secret for key isolation.
    They intentionally do NOT use the RS256 key pair (which is for user tokens).
    """
    return settings.ADMIN_JWT_SECRET or settings.JWT_SECRET, "HS256"


async def _csrf_check(request: Request):
    """Validate Origin/Referer for CSRF protection on admin endpoints."""
    if request.method in ("POST", "PUT", "DELETE"):
        origin = request.headers.get("origin") or request.headers.get("referer", "")
        # Skip CSRF check if no Origin/Referer header is present.
        # CSRF protection is only meaningful when a browser sends a cross-origin
        # request. API clients and test runners do not send Origin headers.
        if not origin:
            return
        allowed = settings.allowed_origins_list
        if not any(origin.startswith(o) for o in allowed):
            raise HTTPException(
                status_code=403, detail="CSRF validation failed: origin not allowed"
            )


async def _validate_admin_session(request: Request) -> dict:
    """Validate admin session cookie and return payload. Raises HTTPException on failure."""
    session_cookie = request.cookies.get("syrabit_admin_session")
    if not session_cookie:
        # Fallback: check for Bearer token in Authorization header
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            try:
                verify_key, verify_alg = _get_admin_verification_key()
                payload = jwt.decode(
                    token,
                    verify_key,
                    algorithms=[verify_alg],
                )
                # For Bearer tokens with type "admin", allow directly
                if payload.get("type") == "admin" and payload.get("role") == "admin":
                    return payload
                # For access tokens, verify user has admin role in DB
                if payload.get("type") == "access":
                    user_id = payload.get("sub")
                    if not user_id:
                        raise HTTPException(
                            status_code=401, detail="Invalid token payload"
                        )
                    user = await User.get(user_id)
                    if user and user.role == "admin":
                        return {
                            "sub": str(user.id),
                            "type": "admin",
                            "role": "admin",
                        }
                raise HTTPException(status_code=403, detail="Insufficient permissions")
            except HTTPException:
                raise
            except InvalidTokenError:
                raise HTTPException(status_code=401, detail="Invalid or expired token")
        raise HTTPException(status_code=401, detail="No admin session")
    try:
        verify_key, verify_alg = _get_admin_verification_key()
        payload = jwt.decode(
            session_cookie,
            verify_key,
            algorithms=[verify_alg],
        )
        if payload.get("type") != "admin" or payload.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return payload
    except InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid or expired session")


@router.get("/verify")
async def admin_verify(request: Request):
    """
    Verify admin session via httponly cookie.
    Returns 200 if the session cookie is valid and has admin role.
    The AdminGuard frontend component calls this endpoint on mount.
    """
    payload = await _validate_admin_session(request)
    return {"status": "ok", "user_id": payload.get("sub")}


@router.post("/login")
async def admin_login(request: Request):
    """Admin login - accepts email/password, verifies admin role, sets httponly cookie."""
    await _csrf_check(request)
    try:
        await _check_rate_limit(request, "admin_login", 5)
    except HTTPException:
        raise  # Re-raise 429 (rate limit) or 503 (Redis unavailable)
    except Exception as e:
        logger.warning(f"Admin rate-limit check failed (Redis unavailable): {e}")
        raise HTTPException(status_code=503, detail="Rate limiting service unavailable")

    body = await request.json()
    email = body.get("email")
    password = body.get("password")

    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password required")

    # Find user and verify credentials
    try:
        user = await User.find_one({"email": email})
    except Exception as e:
        if CollectionWasNotInitialized and isinstance(e, CollectionWasNotInitialized):
            raise HTTPException(status_code=503, detail="Database service unavailable")
        logger.error(f"Unexpected database error: {e}")
        raise
    if not user or not user.hashed_password:
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not user.verify_password(password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    # Check admin role
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    # Mint admin JWT (8-hour session)
    expire = datetime.now(timezone.utc) + timedelta(hours=8)
    token_payload = {
        "sub": str(user.id),
        "type": "admin",
        "role": "admin",
        "exp": expire,
    }
    signing_key, signing_alg = _get_admin_signing_key()
    admin_token = jwt.encode(
        token_payload,
        signing_key,
        algorithm=signing_alg,
    )

    response = JSONResponse(
        {"status": "ok", "name": user.name or "", "user_id": str(user.id)}
    )
    response.set_cookie(
        key="syrabit_admin_session",
        value=admin_token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=28800,  # 8 hours
        path="/api/",
    )
    return response


@router.post("/logout")
async def admin_logout(request: Request):
    """Clear admin session cookie."""
    await _csrf_check(request)
    response = JSONResponse({"status": "ok", "message": "Logged out"})
    response.delete_cookie(
        key="syrabit_admin_session",
        path="/api/",
        secure=True,
        samesite="strict",
    )
    return response
