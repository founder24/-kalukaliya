"""
Admin Verification Endpoint
Validates the httponly syrabit_admin_session cookie for admin panel access.
CSRF Protection: Admin cookies MUST use SameSite=Strict.
Origin validation is enforced on all mutating (non-GET) requests.
"""

from datetime import datetime, timedelta, timezone
import hashlib

from fastapi import APIRouter, Depends, HTTPException, Request
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
    key = (settings.ADMIN_JWT_SECRET or settings.JWT_SECRET or "").strip()
    if not key:
        raise RuntimeError(
            "Admin JWT signing is unavailable: set ADMIN_JWT_SECRET or JWT_SECRET"
        )
    return key, "HS256"


def _get_admin_verification_key() -> tuple:
    """Get the key and algorithm for verifying admin JWTs.

    Admin tokens ALWAYS use HS256 with a dedicated secret for key isolation.
    They intentionally do NOT use the RS256 key pair (which is for user tokens).
    """
    key = (settings.ADMIN_JWT_SECRET or settings.JWT_SECRET or "").strip()
    if not key:
        raise RuntimeError(
            "Admin JWT verification is unavailable: set ADMIN_JWT_SECRET or JWT_SECRET"
        )
    return key, "HS256"


async def _csrf_check(request: Request):
    """Validate Origin/Referer for CSRF protection on admin endpoints."""
    if request.method in ("POST", "PUT", "DELETE"):
        # Skip entirely in test/development — mirrors the global middleware behaviour.
        # In dev the Replit preview domain is the trusted host and blocking it would
        # make the admin panel unusable during local development.
        if settings.APP_ENV in ("test", "development"):
            return

        origin = request.headers.get("origin", "")
        if not origin:
            # Browsers send Referer as a full URL (e.g. https://syrabit.ai/admin/login).
            # Strip to scheme+host so is_origin_allowed() can match it correctly.
            referer = request.headers.get("referer", "")
            if referer:
                from urllib.parse import urlparse
                parsed = urlparse(referer)
                if parsed.scheme and parsed.netloc:
                    origin = f"{parsed.scheme}://{parsed.netloc}"
        # Skip CSRF check if no Origin/Referer header is present.
        # CSRF protection is only meaningful when a browser sends a cross-origin
        # request. API clients and test runners do not send Origin headers.
        if not origin:
            return
        # Use is_origin_allowed() which handles wildcard patterns for Replit dev
        # domains (e.g. https://*.sisko.replit.dev) and CF Pages preview URLs,
        # not just the exact-match list in allowed_origins_list.
        if not settings.is_origin_allowed(origin):
            raise HTTPException(
                status_code=403, detail="CSRF validation failed: origin not allowed"
            )


async def _is_admin_token_blacklisted(token: str) -> bool:
    """Check if an admin session token has been blacklisted (revoked via logout)."""
    try:
        from app.db.redis import get_redis
        redis = get_redis()
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        result = await redis.get(f"blacklisted_admin_token:{token_hash}")
        return result is not None
    except Exception as e:
        logger.warning(f"Redis unavailable for admin token blacklist check: {e}")
        return False  # fail-open: token expires naturally (max 8h)


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
                    if await _is_admin_token_blacklisted(token):
                        raise HTTPException(status_code=401, detail="Session revoked")
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
                # The edge worker always injects its GCP identity token as the
                # Authorization header, so this fallback will fire for every
                # unauthenticated request that went through the worker.  Return
                # the same generic message as the "no cookie" path so callers
                # get consistent, non-misleading errors.
                raise HTTPException(status_code=401, detail="No admin session")
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
        if await _is_admin_token_blacklisted(session_cookie):
            raise HTTPException(status_code=401, detail="Session revoked")
        return payload
    except InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid or expired session")


# ── FastAPI Depends guards ─────────────────────────────────────────────────────
# Use these at the router level so every new endpoint is automatically protected:
#   router = APIRouter(dependencies=[Depends(require_admin_session), Depends(csrf_guard)])


async def require_admin_session(request: Request) -> dict:
    """FastAPI Depends: validates admin session. Raises 401/403 on failure."""
    return await _validate_admin_session(request)


async def csrf_guard(request: Request) -> None:
    """FastAPI Depends: CSRF validation for mutating methods (POST/PUT/DELETE)."""
    await _csrf_check(request)


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
    except HTTPException as e:
        if e.status_code == 429:
            raise  # Real rate limit — enforce it
        # Redis unavailable (503) — fail-open so admins aren't locked out
        logger.warning(f"Admin rate-limit unavailable (fail-open): {e.detail}")
    except Exception as e:
        logger.warning(f"Admin rate-limit check failed (fail-open): {e}")

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
    # In development, cookies must not require HTTPS (secure=False) and use
    # SameSite=Lax so the Vite dev proxy can relay them correctly.
    # In production (Cloud Run ↔ CF Worker), enforce Secure + SameSite=Strict.
    is_prod = settings.APP_ENV == "production"
    response.set_cookie(
        key="syrabit_admin_session",
        value=admin_token,
        httponly=True,
        secure=is_prod,
        samesite="strict" if is_prod else "lax",
        max_age=28800,  # 8 hours
        path="/api/",
    )
    return response


@router.post("/logout")
async def admin_logout(request: Request):
    """Clear admin session cookie and blacklist the session JWT in Redis."""
    await _csrf_check(request)

    # Blacklist the session JWT so it cannot be replayed even if the client
    # keeps a copy of the cookie value (e.g. test scripts, compromised device).
    session_cookie = request.cookies.get("syrabit_admin_session")
    server_revocation = False
    if session_cookie:
        try:
            verify_key, verify_alg = _get_admin_verification_key()
            payload = jwt.decode(session_cookie, verify_key, algorithms=[verify_alg])
            exp = payload.get("exp", 0)
            now = int(datetime.now(timezone.utc).timestamp())
            ttl = max(exp - now, 1)
            from app.db.redis import get_redis
            redis = get_redis()
            token_hash = hashlib.sha256(session_cookie.encode()).hexdigest()
            await redis.set(f"blacklisted_admin_token:{token_hash}", "1", ex=ttl)
            server_revocation = True
            logger.info(f"Admin session blacklisted for user {payload.get('sub')} (ttl={ttl}s)")
        except Exception as e:
            # Redis unavailable — cookie is still cleared client-side (primary mechanism).
            # Return partial-success so callers know server-side revocation failed.
            # The JWT will expire naturally within 8h max.
            logger.warning(f"Admin logout blacklist failed: {type(e).__name__}: {e}")

    response = JSONResponse({
        "status": "ok",
        "message": "Logged out",
        "server_revocation": server_revocation,
    })
    is_prod = settings.APP_ENV == "production"
    response.delete_cookie(
        key="syrabit_admin_session",
        path="/api/",
        httponly=True,
        secure=is_prod,
        samesite="strict" if is_prod else "lax",
    )
    return response
