"""
Admin Verification Endpoint
Validates the httponly syrabit_admin_session cookie for admin panel access.
"""
from fastapi import APIRouter, HTTPException, Request
from jose import jwt, JWTError
import logging

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin"])


@router.get("/verify")
async def admin_verify(request: Request):
    """
    Verify admin session via httponly cookie.
    Returns 200 if the session cookie is valid and has admin role.
    The AdminGuard frontend component calls this endpoint on mount.
    """
    session_cookie = request.cookies.get("syrabit_admin_session")
    if not session_cookie:
        raise HTTPException(status_code=401, detail="No admin session")

    try:
        payload = jwt.decode(
            session_cookie,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM]
        )

        if payload.get("type") != "admin":
            raise HTTPException(status_code=401, detail="Not an admin session")

        if payload.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Insufficient permissions")

        return {"status": "ok", "user_id": payload.get("sub")}

    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired session")
