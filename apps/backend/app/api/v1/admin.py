"""
Admin Verification Endpoint
Validates the httponly syrabit_admin_session cookie for admin panel access.
CSRF Protection: Admin cookies MUST use SameSite=Strict.
Origin validation is enforced on all mutating (non-GET) requests.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from jose import jwt, JWTError
import logging

from app.config import settings
from app.models.user import User
from app.api.v1.auth import _check_rate_limit

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin"])


async def _csrf_check(request: Request):
    """Validate Origin/Referer for CSRF protection on admin endpoints."""
    if request.method in ("POST", "PUT", "DELETE"):
        origin = request.headers.get("origin") or request.headers.get("referer", "")
        allowed = settings.allowed_origins_list
        if not any(origin.startswith(o) for o in allowed):
            raise HTTPException(
                status_code=403, detail="CSRF validation failed: origin not allowed"
            )


def _validate_admin_session(request: Request) -> dict:
    """Validate admin session cookie and return payload. Raises HTTPException on failure."""
    session_cookie = request.cookies.get("syrabit_admin_session")
    if not session_cookie:
        raise HTTPException(status_code=401, detail="No admin session")
    try:
        payload = jwt.decode(
            session_cookie, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
        if payload.get("type") != "admin" or payload.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired session")


@router.get("/verify")
async def admin_verify(request: Request):
    """
    Verify admin session via httponly cookie.
    Returns 200 if the session cookie is valid and has admin role.
    The AdminGuard frontend component calls this endpoint on mount.
    """
    payload = _validate_admin_session(request)
    return {"status": "ok", "user_id": payload.get("sub")}


@router.post("/login")
async def admin_login(request: Request):
    """Admin login - accepts email/password, verifies admin role, sets httponly cookie."""
    await _csrf_check(request)
    await _check_rate_limit(request, "admin_login", 5)

    body = await request.json()
    email = body.get("email")
    password = body.get("password")

    if not email or not password:
        raise HTTPException(status_code=400, detail="Email and password required")

    # Find user and verify credentials
    user = await User.find_one({"email": email})
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
    admin_token = jwt.encode(
        token_payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM
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
        path="/api/v1/admin",
    )
    return response


@router.post("/logout")
async def admin_logout(request: Request):
    """Clear admin session cookie."""
    await _csrf_check(request)
    response = JSONResponse({"status": "ok", "message": "Logged out"})
    response.delete_cookie(
        key="syrabit_admin_session",
        path="/api/v1/admin",
        secure=True,
        samesite="strict",
    )
    return response
