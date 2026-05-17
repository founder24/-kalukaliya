"""Syrabit.ai — Authentication routes"""
import json, asyncio, uuid, logging
from typing import Optional
from datetime import datetime, timezone, timedelta
from fastapi import (
    APIRouter, HTTPException, Depends, Response, Request, Cookie,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from models import (
    UserCreate, UserLogin, UserOut, TokenOut, PasswordResetReq, PasswordResetConfirm,
)
from config import (
    COOKIE_DOMAIN,
    COOKIE_SAMESITE,
    FRONTEND_URL,
    JWT_ACCESS_EXPIRE_MINUTES,
    JWT_REFRESH_EXPIRE_MINUTES,
    SECURE_COOKIES,
)
from deps import pwd_ctx
from auth_deps import (
    get_current_user, create_access_token, create_refresh_token,
    get_user_credits, get_current_user_optional, _real_client_ip,
)
# Task #383 — Turnstile dependency. Dormant when TURNSTILE_ON is false
# so the dependency can ship in production ahead of flipping the flag.
from turnstile import require_turnstile
from db_ops import (
    supa_create_password_reset,
    supa_delete_password_reset,
    supa_get_password_reset,
    supa_get_settings,
    supa_get_user,
    supa_get_user_for_reset,
    supa_insert_user,
    supa_update_user,
    supa_update_user_password,
)
import email_templates

logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/auth/signup", dependencies=[Depends(require_turnstile)])
async def signup(
    data: UserCreate,
    request: Request,
    response: Response,
    syrabit_device: Optional[str] = Cookie(default=None),
):
    # Task #407 — per-IP signup rate gate routed through the shared
    # ``do_chat.rate_check`` helper. Backed by the global RateLimiter
    # Durable Object when DO_CHAT_ON is set (so a scripted client cannot
    # rotate across regions to reset its counter), with the in-process
    # token-bucket as a safe fallback when the edge is unreachable.
    # Tight window (5 signup attempts / 5 min / IP) — Turnstile already
    # gates this endpoint, so this is the secondary anti-abuse layer.
    _signup_ip = _real_client_ip(request)
    if _signup_ip and _signup_ip != "unknown":
        try:
            from do_chat import rate_check as _do_rate_check
            allowed, _remaining = await _do_rate_check(
                f"signup:ip:{_signup_ip}", limit=5, window_s=300,
            )
            if not allowed:
                raise HTTPException(
                    status_code=429,
                    detail=(
                        "Too many signup attempts from this network. "
                        "Please wait a few minutes and try again."
                    ),
                    headers={"Retry-After": "300", "X-RateLimit-Limit": "5"},
                )
        except HTTPException:
            raise
        except Exception as _rate_err:
            # Never block a real signup because rate_check itself crashed.
            logger.debug("signup rate_check skipped: %s", _rate_err)

    existing = await supa_get_user(data.email.lower())
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    if not data.consent_dpdp:
        raise HTTPException(status_code=400, detail="You must consent to data processing under the DPDP Act to create an account")

    settings = await supa_get_settings()
    if not settings.get("registrations_open", True):
        raise HTTPException(status_code=403, detail="Registrations are currently closed")

    user_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    user = {
        "id": user_id,
        "name": data.name,
        "email": data.email.lower(),
        "password_hash": await asyncio.to_thread(pwd_ctx.hash, data.password),
        "plan": "free",
        "credits_used": 0,
        "credits_limit": 30,
        "document_access": "zero",
        "onboarding_done": False,
        "is_admin": False,
        "status": "active",
        "bio": "",
        "phone": "",
        "saved_subjects": [],
        "has_free_credits_issued": True,
        "consent_dpdp": data.consent_dpdp,
        "consent_dpdp_version": "1.0" if data.consent_dpdp else None,
        "consent_dpdp_at": now if data.consent_dpdp else None,
        "created_at": now,
        "auth_provider": "email",
    }

    # Task #337 — Fraud Detector risk score on signup. Outage policy
    # per runbook §3.9: any RuntimeError is swallowed and treated as
    # ``approve`` so a Fraud Detector outage cannot lock new signups
    # out of the platform. ``block`` is the only verdict that aborts.
    try:
        import os as _os
        _det_id = _os.environ.get("AWS_FRAUD_DETECTOR_ID", "").strip()
        _det_ver = _os.environ.get("AWS_FRAUD_DETECTOR_VERSION", "").strip()
        if _det_id and _det_ver:
            from providers import aws_native as _awsn
            if _awsn.is_enabled("fraud_detector") and _awsn.is_configured():
                _verdict = await asyncio.to_thread(
                    _awsn.get_fraud_score,
                    detector_id=_det_id,
                    detector_version_id=_det_ver,
                    event_id=user_id,
                    event_type="signup",
                    entity_type="user",
                    entity_id=user_id,
                    event_variables={
                        "email":      data.email.lower(),
                        "ip_address": (request.client.host if request.client else "0.0.0.0"),
                        "user_agent": (request.headers.get("user-agent") or "")[:500],
                    },
                )
                if _verdict.outcome == "block":
                    raise HTTPException(
                        status_code=403,
                        detail="Signup blocked by fraud risk policy. Please contact support.",
                    )
    except HTTPException:
        raise
    except Exception as _fd_exc:
        logger.warning("Fraud Detector signup probe skipped: %s", str(_fd_exc)[:200])

    await supa_insert_user(user)
    if data.consent_dpdp:
        try:
            await supa_update_user(user_id, {
                "consent_dpdp": True,
                "consent_dpdp_version": "1.0",
                "consent_dpdp_at": now,
            })
        except Exception:
            pass

    token = create_access_token(user_id, role="student", plan="free")
    refresh = create_refresh_token(user_id)
    # Task #798 — pair this brand-new account with any anonymous-quota
    # exhaustion event from the same device cookie in the prior 48h so
    # the admin chart can compute the "exhausted -> sign-up" funnel.
    # Best-effort: a missing/forged/expired cookie or a metric crash
    # must never block the signup itself.
    try:
        from metrics import record_signup_with_device
        from device_token import device_token_id
        _tid = device_token_id(syrabit_device) if syrabit_device else None
        if _tid:
            record_signup_with_device(_tid)
    except Exception:
        pass
    user_out = UserOut(
        id=user_id, name=data.name, email=data.email.lower(),
        plan="free", credits_used=0, credits_limit=user.get("credits_limit", 30),
        onboarding_done=False, is_admin=False, role="student", created_at=now
    )
    _session_kwargs = dict(key="syrabit_session", value=token, httponly=True, secure=SECURE_COOKIES, samesite=COOKIE_SAMESITE, max_age=JWT_ACCESS_EXPIRE_MINUTES * 60)
    _refresh_kwargs = dict(key="syrabit_refresh", value=refresh, httponly=True, secure=SECURE_COOKIES, samesite=COOKIE_SAMESITE, path="/api/auth/refresh", max_age=JWT_REFRESH_EXPIRE_MINUTES * 60)
    if COOKIE_DOMAIN:
        _session_kwargs["domain"] = COOKIE_DOMAIN
        _refresh_kwargs["domain"] = COOKIE_DOMAIN
    response.set_cookie(**_session_kwargs)
    response.set_cookie(**_refresh_kwargs)
    return {"access_token": token, "token_type": "bearer", "user": user_out.dict()}

@router.post("/auth/login", response_model=TokenOut)
async def login(data: UserLogin, request: Request, response: Response):
    user = await supa_get_user(data.email.lower())
    pw_hash = user.get("password_hash", "") if user else ""
    if not user or not pw_hash or not await asyncio.to_thread(pwd_ctx.verify, data.password, pw_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if user.get("status") == "banned":
        raise HTTPException(status_code=403, detail="Account banned")

    credits_info = await get_user_credits(user)
    db_role = user.get("role", "")
    if user.get("is_admin"):
        role = "admin"
    elif db_role == "staff":
        role = "staff"
    else:
        role = "student"
    token = create_access_token(user["id"], role=role, plan=user.get("plan", "free"))
    refresh = create_refresh_token(user["id"])
    user_out = UserOut(
        id=user["id"], name=user["name"], email=user["email"],
        plan=user.get("plan", "free"),
        credits_used=credits_info["used"],
        credits_limit=credits_info["limit"],
        onboarding_done=user.get("onboarding_done", False),
        is_admin=user.get("is_admin", False),
        board_id=user.get("board_id"),
        class_id=user.get("class_id"),
        stream_id=user.get("stream_id"),
        created_at=user.get("created_at", ""),
        avatar_url=user.get("avatar_url", ""),
        ads_opt_out=bool(user.get("ads_opt_out", False)),
        role=user.get("role", "admin" if user.get("is_admin") else "student"),
    )
    _session_kwargs = dict(key="syrabit_session", value=token, httponly=True, secure=SECURE_COOKIES, samesite=COOKIE_SAMESITE, max_age=JWT_ACCESS_EXPIRE_MINUTES * 60)
    _refresh_kwargs = dict(key="syrabit_refresh", value=refresh, httponly=True, secure=SECURE_COOKIES, samesite=COOKIE_SAMESITE, path="/api/auth/refresh", max_age=JWT_REFRESH_EXPIRE_MINUTES * 60)
    if COOKIE_DOMAIN:
        _session_kwargs["domain"] = COOKIE_DOMAIN
        _refresh_kwargs["domain"] = COOKIE_DOMAIN
    response.set_cookie(**_session_kwargs)
    response.set_cookie(**_refresh_kwargs)
    return TokenOut(access_token=token, user=user_out)

class SupabaseSessionRequest(BaseModel):
    supabase_token: str
    name: Optional[str] = None
    consent_dpdp: Optional[bool] = False


@router.post("/auth/supabase-session")
async def supabase_session(
    data: SupabaseSessionRequest,
    request: Request,
    response: Response,
    syrabit_device: Optional[str] = Cookie(default=None),
):
    """Exchange a Supabase access token for a Syrabit session.

    Called by the frontend immediately after a successful
    supabase.auth.signInWithPassword() or supabase.auth.signUp().
    Verifies the Supabase JWT, finds or auto-creates the matching
    profile row in our users table, and issues our custom httpOnly
    session + refresh cookies so the rest of the app keeps working
    exactly as before.
    """
    from deps import supa as _supa_client
    if not _supa_client:
        raise HTTPException(status_code=503, detail="Supabase not configured on this server")

    try:
        sb_response = await asyncio.to_thread(_supa_client.auth.get_user, data.supabase_token)
        sb_user = sb_response.user
        if not sb_user:
            raise ValueError("no user returned")
    except Exception as exc:
        logger.warning("Supabase token verification failed: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid or expired Supabase token")

    email = (sb_user.email or "").lower()
    if not email:
        raise HTTPException(status_code=400, detail="Supabase account has no email address")

    existing = await supa_get_user(email)

    if existing:
        if existing.get("status") == "banned":
            raise HTTPException(status_code=403, detail="Account banned")
        user = existing
        user_id = user["id"]
    else:
        settings = await supa_get_settings()
        if not settings.get("registrations_open", True):
            raise HTTPException(status_code=403, detail="Registrations are currently closed")

        user_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        display_name = (
            data.name
            or (sb_user.user_metadata or {}).get("full_name")
            or (sb_user.user_metadata or {}).get("name")
            or email.split("@")[0]
        )
        user = {
            "id": user_id,
            "name": display_name,
            "email": email,
            "password_hash": "",
            "plan": "free",
            "credits_used": 0,
            "credits_limit": 30,
            "document_access": "zero",
            "onboarding_done": False,
            "is_admin": False,
            "status": "active",
            "bio": "",
            "phone": "",
            "saved_subjects": [],
            "has_free_credits_issued": True,
            "auth_provider": "supabase",
            "consent_dpdp": bool(data.consent_dpdp),
            "consent_dpdp_version": "1.0" if data.consent_dpdp else None,
            "consent_dpdp_at": now if data.consent_dpdp else None,
            "created_at": now,
        }
        await supa_insert_user(user)
        try:
            from metrics import record_signup_with_device
            from device_token import device_token_id
            _tid = device_token_id(syrabit_device) if syrabit_device else None
            if _tid:
                record_signup_with_device(_tid)
        except Exception:
            pass

    credits_info = await get_user_credits(user)
    db_role = user.get("role", "")
    if user.get("is_admin"):
        role = "admin"
    elif db_role == "staff":
        role = "staff"
    else:
        role = "student"

    token = create_access_token(user_id, role=role, plan=user.get("plan", "free"))
    refresh = create_refresh_token(user_id)

    user_out = UserOut(
        id=user_id,
        name=user.get("name", ""),
        email=email,
        plan=user.get("plan", "free"),
        credits_used=credits_info["used"],
        credits_limit=credits_info["limit"],
        onboarding_done=user.get("onboarding_done", False),
        is_admin=user.get("is_admin", False),
        board_id=user.get("board_id"),
        class_id=user.get("class_id"),
        stream_id=user.get("stream_id"),
        created_at=user.get("created_at", ""),
        avatar_url=user.get("avatar_url", ""),
        ads_opt_out=bool(user.get("ads_opt_out", False)),
        role=user.get("role", "admin" if user.get("is_admin") else "student"),
    )

    _session_kwargs = dict(
        key="syrabit_session", value=token, httponly=True,
        secure=SECURE_COOKIES, samesite=COOKIE_SAMESITE,
        max_age=JWT_ACCESS_EXPIRE_MINUTES * 60,
    )
    _refresh_kwargs = dict(
        key="syrabit_refresh", value=refresh, httponly=True,
        secure=SECURE_COOKIES, samesite=COOKIE_SAMESITE,
        path="/api/auth/refresh", max_age=JWT_REFRESH_EXPIRE_MINUTES * 60,
    )
    if COOKIE_DOMAIN:
        _session_kwargs["domain"] = COOKIE_DOMAIN
        _refresh_kwargs["domain"] = COOKIE_DOMAIN
    response.set_cookie(**_session_kwargs)
    response.set_cookie(**_refresh_kwargs)
    return TokenOut(access_token=token, user=user_out)


async def _send_password_reset_email(email: str, token: str):
    """Send password reset email via email_templates (Amazon SES via boto3)."""
    reset_url = f"{FRONTEND_URL}/reset-password?token={token}"
    await email_templates.send_password_reset(email=email, token=token, reset_url=reset_url)

@router.post("/auth/reset-request", dependencies=[Depends(require_turnstile)])
async def reset_request(data: PasswordResetReq, request: Request):
    # ── Rate-limit guard (Task #407 pattern) ──────────────────────────────────
    # Two complementary buckets, both fail-open so Redis unavailability never
    # blocks a legitimate user.
    #
    # 1) Per-email  — 3 requests / hour:  stops a caller who knows an address
    #    from flooding that inbox.
    # 2) Per-IP     — 5 requests / 10 min: stops enumeration across many
    #    addresses from the same network.
    #
    # Turnstile already sits in front of this route, so these are the secondary
    # anti-abuse layers.
    try:
        from do_chat import rate_check as _rate_check

        _email_key = f"reset:email:{data.email.lower()}"
        _allowed, _ = await _rate_check(_email_key, limit=3, window_s=3600)
        if not _allowed:
            raise HTTPException(
                status_code=429,
                detail="Too many password-reset requests for this email. Please wait an hour and try again.",
                headers={"Retry-After": "3600", "X-RateLimit-Limit": "3"},
            )

        _ip = _real_client_ip(request)
        if _ip and _ip != "unknown":
            _ip_allowed, _ = await _rate_check(
                f"reset:ip:{_ip}", limit=5, window_s=600,
            )
            if not _ip_allowed:
                raise HTTPException(
                    status_code=429,
                    detail="Too many password-reset requests from this network. Please wait a few minutes and try again.",
                    headers={"Retry-After": "600", "X-RateLimit-Limit": "5"},
                )
    except HTTPException:
        raise
    except Exception as _rate_err:
        # Never block a real reset because rate_check itself crashed.
        logger.debug("reset-request rate_check skipped: %s", _rate_err)
    # ── End rate-limit guard ──────────────────────────────────────────────────

    user = await supa_get_user_for_reset(data.email.lower())
    if user:
        token = str(uuid.uuid4())
        expires = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        await supa_create_password_reset(token, data.email.lower(), expires)
        await _send_password_reset_email(data.email.lower(), token)
    return {"message": "If the email exists, a reset link has been sent"}

@router.post("/auth/reset-confirm")
async def reset_confirm(data: PasswordResetConfirm, request: Request):
    record = await supa_get_password_reset(data.token)
    if not record:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    expires = datetime.fromisoformat(record["expires"])
    if datetime.now(timezone.utc) > expires:
        raise HTTPException(status_code=400, detail="Reset token expired")
    await supa_update_user_password(record["email"], await asyncio.to_thread(pwd_ctx.hash, data.new_password))
    await supa_delete_password_reset(data.token)
    return {"message": "Password updated successfully"}

@router.get("/auth/me")
async def get_me(user: Optional[dict] = Depends(get_current_user_optional)):
    if not user:
        return {"user": None}
    credits_info = await get_user_credits(user)
    return UserOut(
        id=user["id"], name=user["name"], email=user["email"],
        plan=user.get("plan", "free"),
        credits_used=credits_info["used"],
        credits_limit=credits_info["limit"],
        onboarding_done=user.get("onboarding_done", False),
        is_admin=user.get("is_admin", False),
        board_id=user.get("board_id"),
        class_id=user.get("class_id"),
        stream_id=user.get("stream_id"),
        created_at=user.get("created_at", ""),
        avatar_url=user.get("avatar_url", ""),
        ads_opt_out=bool(user.get("ads_opt_out", False)),
        role=user.get("role", "admin" if user.get("is_admin") else "student"),
    )


class _ConsentWithdrawReq(BaseModel):
    withdraw: bool = True


@router.get("/privacy/consent")
async def get_consent(user: dict = Depends(get_current_user)):
    return {
        "consent_dpdp": user.get("consent_dpdp", False),
        "consent_dpdp_version": user.get("consent_dpdp_version"),
        "consent_dpdp_at": user.get("consent_dpdp_at"),
    }


@router.post("/privacy/consent")
async def update_consent(body: _ConsentWithdrawReq, user: dict = Depends(get_current_user)):
    now = datetime.now(timezone.utc).isoformat()
    if body.withdraw:
        updates = {
            "consent_dpdp": False,
            "consent_dpdp_version": None,
            "consent_dpdp_at": None,
        }
        await supa_update_user(user["id"], updates)
        logger.info(f"[privacy] User {user['id']} withdrew DPDP consent")
        return {"status": "withdrawn", "consent_dpdp": False, "withdrawn_at": now}
    else:
        updates = {
            "consent_dpdp": True,
            "consent_dpdp_version": "1.0",
            "consent_dpdp_at": now,
        }
        await supa_update_user(user["id"], updates)
        logger.info(f"[privacy] User {user['id']} granted DPDP consent v1.0")
        return {"status": "granted", "consent_dpdp": True, "consent_dpdp_version": "1.0", "consent_dpdp_at": now}


@router.post("/security/csp-report")
async def csp_report(request: Request):
    try:
        body = await request.json()
        report = body.get("csp-report", body)
        logger.warning(f"[CSP-VIOLATION] {json.dumps(report, default=str)[:500]}")
    except Exception:
        pass
    return JSONResponse(status_code=204, content=None)


# ─────────────────────────────────────────────
# CONTENT ROUTES
