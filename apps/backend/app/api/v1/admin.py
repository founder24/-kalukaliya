"""
Admin Verification Endpoint
Validates the httponly syrabit_admin_session cookie for admin panel access.
CSRF Protection: Admin cookies MUST use SameSite=Strict.
Origin validation is enforced on all mutating (non-GET) requests.
"""
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from jose import jwt, JWTError
import logging

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin"])


async def _csrf_check(request: Request):
    """Validate Origin/Referer for CSRF protection on admin endpoints."""
    if request.method in ("POST", "PUT", "DELETE"):
        origin = request.headers.get("origin") or request.headers.get("referer", "")
        allowed = settings.allowed_origins_list
        if not any(origin.startswith(o) for o in allowed):
            raise HTTPException(status_code=403, detail="CSRF validation failed: origin not allowed")


def _validate_admin_session(request: Request) -> dict:
    """Validate admin session cookie and return payload. Raises HTTPException on failure."""
    session_cookie = request.cookies.get("syrabit_admin_session")
    if not session_cookie:
        raise HTTPException(status_code=401, detail="No admin session")
    try:
        payload = jwt.decode(session_cookie, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
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
    """Admin login - sets httponly SameSite=Strict cookie for CSRF protection."""
    await _csrf_check(request)

    body = await request.json()
    token = body.get("token")
    if not token:
        raise HTTPException(status_code=400, detail="Token required")

    # Validate the provided token
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        if payload.get("type") != "admin" or payload.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Insufficient permissions")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    response = JSONResponse({"status": "ok", "user_id": payload.get("sub")})
    response.set_cookie(
        key="syrabit_admin_session",
        value=token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=3600,  # 1 hour
        path="/api/v1/admin",
    )
    return response
