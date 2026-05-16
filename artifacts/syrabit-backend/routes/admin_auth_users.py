"""Syrabit.ai — Admin auth, users, conversations"""
import json, asyncio, uuid, logging
from typing import Optional
from datetime import datetime, timezone, timedelta
from fastapi import (
    APIRouter, HTTPException, Depends, Response, Cookie,
)
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel

import os as _os

from models import (
    AdminLoginReq, UserStatusUpdate, UserPlanUpdate, UserRoleUpdate, UserCreditsUpdate,
)
from config import (
    ADMIN_JWT_SECRET,
    COOKIE_DOMAIN,
    COOKIE_SAMESITE,
    JWT_ACCESS_EXPIRE_MINUTES,
    SECURE_COOKIES,
)
from deps import (
    db,
    is_mongo_available,
    security,
    supa,
)
import deps
from cache import (
    _redis_invalidate_session,
    redis_list_all_anon_conversations,
)
from auth_deps import (
    get_admin_user, create_access_token, create_token, decode_token, get_user_credits, get_current_user_optional, JWTError,
    get_rate_limit_count, reset_rate_limit,
)
from db_ops import (
    _pg_rows,
    _supa,
    supa_count_users,
    supa_get_user,
    supa_get_user_by_id,
    supa_get_users_by_ids,
    supa_insert_activity_log,
    supa_list_users,
    supa_update_user,
)
from analytics_helpers import get_recent_user_events, get_session_metrics
import cloudflare_client
from cf_access import require_cf_access_admin
from turnstile import require_turnstile

# Supabase Auth error class — raised on wrong email/password.
try:
    from supabase_auth.errors import AuthApiError as _SupaAuthApiError
except ImportError:
    class _SupaAuthApiError(Exception):  # noqa: N818
        pass

# SECURITY FIX 2026-01-XX: Removed E2E test backdoor and env-var auth fallback.
# All admin authentication MUST go through Supabase Auth with MFA enabled.
# For E2E tests, use dedicated test accounts provisioned via Supabase CLI.
# See docs/SECURITY_FIXES.md for migration guide.

logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/admin/login", dependencies=[Depends(require_turnstile)])
async def admin_login(
    data: AdminLoginReq,
    response: Response,
    # Task #702 — gate the admin login entry point itself behind
    # Cloudflare Access. `get_admin_user` already protects every other
    # admin route, but `/admin/login` cannot use it (no admin JWT exists
    # yet). Without this dependency, anyone who learns the Railway /
    # Cloud Run URL can hit the credential check directly and brute-
    # force passwords, completely bypassing the Zero Trust IdP +
    # device-posture rules. The dependency is a no-op when
    # CF_ACCESS_ENFORCE is unset (dev / pre-rollout) and 401s the
    # request before the password compare in production.
    #
    # Task #423 — also wrap with `require_turnstile` so unauthenticated
    # bot traffic eats a CF challenge before the credential check, on
    # parity with /auth/signup, /auth/login, /auth/reset-request. The
    # dependency is dormant when TURNSTILE_ON=0 (dev), and 403s with
    # `turnstile_required` / `turnstile_failed` once the flag is on.
    _cf_access_claims: Optional[dict] = Depends(require_cf_access_admin),
):
    submitted_email = (data.email or "").strip().lower()
    submitted_password = (data.password or "").strip()

    # SECURITY FIX 2026-01-XX: All authentication MUST go through Supabase Auth.
    # Removed E2E backdoor and env-var fallback paths.
    
    # ── Verify credentials via Supabase Auth ─────────────────────────────────
    # Admin accounts live in Supabase Auth + the `users` table (is_admin=True).
    # No credentials are stored in environment variables.
    if deps.supa is None:
        logger.critical(
            "admin_login refused — Supabase client not initialized; "
            "check SUPABASE_URL and SUPABASE_SERVICE_KEY. submitted_email=%r",
            submitted_email,
        )
        raise HTTPException(status_code=503, detail="Admin login is not configured")

    try:
        auth_resp = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: deps.supa.auth.sign_in_with_password(
                {"email": submitted_email, "password": submitted_password}
            ),
        )
        auth_user = auth_resp.user if auth_resp else None
    except _SupaAuthApiError:
        correlation_id = str(uuid.uuid4())
        logger.error(
            f"admin_login rejected — Supabase rejected credentials [corr_id={correlation_id}]. "
            f"submitted_email=%r",
            submitted_email,
        )
        raise HTTPException(status_code=401, detail=f"Invalid credentials. Reference ID: {correlation_id}")
    except Exception as _auth_exc:
        correlation_id = str(uuid.uuid4())
        logger.error(
            f"admin_login Supabase auth error [corr_id={correlation_id}]: %s",
            _auth_exc,
            exc_info=True
        )
        raise HTTPException(
            status_code=503,
            detail=f"Authentication service unavailable. Reference ID: {correlation_id}"
        )
    
    if not auth_user:
        raise HTTPException(status_code=401, detail="Invalid admin credentials")

    # ── Confirm is_admin in the users table ──────────────────────────────────
    user_record = await supa_get_user(submitted_email)
    if not user_record or not user_record.get("is_admin"):
        logger.warning(
            "admin_login denied — account not flagged is_admin. submitted_email=%r",
            submitted_email,
        )
        raise HTTPException(status_code=403, detail="Not an admin account")

    matched_email = submitted_email
    matched_name  = user_record.get("name") or submitted_email

    # ── Issue admin JWT (24-hour session) ────────────────────────────────────
    # Token payload includes name so the frontend welcome toast can greet
    # by name. Signed with ADMIN_JWT_SECRET (distinct from JWT_SECRET) so
    # admin tokens are cryptographically segregated from user tokens.
    token = create_token(
        {
            "sub":      matched_email,
            "email":    matched_email,
            "name":     matched_name,
            "is_admin": True,
        },
        secret=ADMIN_JWT_SECRET,
        expires_delta=60 * 24,
    )
    _ck = dict(key="syrabit_admin_session", value=token, httponly=True, secure=SECURE_COOKIES, samesite=COOKIE_SAMESITE, max_age=60 * 24 * 60)
    if COOKIE_DOMAIN:
        _ck["domain"] = COOKIE_DOMAIN
    response.set_cookie(**_ck)
    # Task #707 — record successful admin logins so the silent-lockout
    # watcher can tell whether anyone has reached the admin surface
    # since the last CF_ACCESS_* env change. Best-effort: a Mongo
    # outage must never break login itself, so we swallow errors.
    try:
        if await is_mongo_available():
            await db.admin_login_log.insert_one({
                "email": matched_email,
                "name":  matched_name,
                "success": True,
                "ts": datetime.now(timezone.utc),
                "cf_access_email": (_cf_access_claims or {}).get("email"),
                "cf_access_break_glass": bool((_cf_access_claims or {}).get("break_glass")),
            })
    except Exception as _login_log_err:
        logger.debug(f"admin_login_log insert failed: {_login_log_err}")
    return {
        "access_token": token,
        "token_type":   "bearer",
        "email":        matched_email,
        "name":         matched_name,
    }

@router.post("/auth/refresh")
async def refresh_token(
    response: Response,
    syrabit_refresh: Optional[str] = Cookie(default=None),
    creds: Optional[HTTPAuthorizationCredentials] = Depends(security),
):
    token = creds.credentials if creds else syrabit_refresh
    if not token:
        raise HTTPException(status_code=401, detail="No refresh token provided")
    try:
        payload = decode_token(token)
        if payload.get("type") != "refresh":
            raise HTTPException(status_code=401, detail="Not a refresh token")
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid refresh token")
    user = await supa_get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if user.get("status") in ("banned", "suspended"):
        raise HTTPException(status_code=403, detail=f"Account {user.get('status')}")
    role = "admin" if user.get("is_admin") else "student"
    new_access = create_access_token(user_id, role=role, plan=user.get("plan", "free"))
    _redis_invalidate_session(user_id)
    _ck = dict(key="syrabit_session", value=new_access, httponly=True, secure=SECURE_COOKIES, samesite=COOKIE_SAMESITE, max_age=JWT_ACCESS_EXPIRE_MINUTES * 60)
    if COOKIE_DOMAIN:
        _ck["domain"] = COOKIE_DOMAIN
    response.set_cookie(**_ck)
    return {"access_token": new_access, "token_type": "bearer"}

@router.post("/auth/logout")
async def logout(response: Response, user: dict = Depends(get_current_user_optional)):
    if user:
        _redis_invalidate_session(user.get("id", ""))
    _del_kwargs = dict(samesite=COOKIE_SAMESITE, secure=SECURE_COOKIES)
    if COOKIE_DOMAIN:
        _del_kwargs["domain"] = COOKIE_DOMAIN
    response.delete_cookie(key="syrabit_session", **_del_kwargs)
    response.delete_cookie(key="syrabit_refresh", path="/api/auth/refresh", **_del_kwargs)
    return {"message": "Logged out"}

@router.post("/admin/logout")
async def admin_logout(response: Response):
    _del_kwargs = dict(samesite=COOKIE_SAMESITE, secure=SECURE_COOKIES)
    if COOKIE_DOMAIN:
        _del_kwargs["domain"] = COOKIE_DOMAIN
    response.delete_cookie(key="syrabit_admin_session", **_del_kwargs)
    return {"message": "Logged out"}

@router.get("/admin/verify")
async def admin_verify(response: Response, admin: dict = Depends(get_admin_user)):
    """Verify admin session and silently slide the cookie expiry forward (keep-alive)."""
    refreshed = create_token(
        {
            "sub":      admin.get("email"),
            "email":    admin.get("email"),
            "name":     admin.get("name", "Admin"),
            "is_admin": True,
        },
        secret=ADMIN_JWT_SECRET,
        expires_delta=60 * 24,
    )
    _ck = dict(key="syrabit_admin_session", value=refreshed, httponly=True, secure=SECURE_COOKIES, samesite=COOKIE_SAMESITE, max_age=60 * 24 * 60)
    if COOKIE_DOMAIN:
        _ck["domain"] = COOKIE_DOMAIN
    response.set_cookie(**_ck)
    return {"valid": True, "email": admin.get("email"), "name": admin.get("name", "Admin"), "access_token": refreshed}

# ─────────────────────────────────────────────
# ADMIN ROUTES
# ─────────────────────────────────────────────
_dashboard_cache: dict = {"data": None, "ts": 0}
_dashboard_lock = asyncio.Lock()
_DASHBOARD_CACHE_TTL = 60

async def _compute_dashboard():
    total_users = await supa_count_users()

    pg_conv_map: dict = {}
    supa_conv_rows: list = []

    if deps.pg_pool:
        try:
            async with deps.pg_pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT id, user_id, messages, created_at FROM conversations ORDER BY created_at ASC"
                )
                for r in _pg_rows(rows):
                    pg_conv_map[r["id"]] = r
        except Exception as e:
            # Audit #7 sweep cleanup: this used to silently swallow pg
            # failures, so if Postgres started returning errors the admin
            # dashboard would just look empty with no clue why. Falling
            # through to the supa branch below is still the right
            # behavior, but we log so the failure mode is observable.
            logger.warning(f"_compute_dashboard pg conversations fetch failed: {e}")

    if supa:
        try:
            all_supa: list = []
            offset = 0
            while True:
                r = await _supa(lambda o=offset: supa.table("conversations")
                    .select("id, messages, created_at, user_id")
                    .order("created_at", desc=False)
                    .range(o, o + 199).execute())
                batch = r.data or []
                if not batch:
                    break
                for row in batch:
                    msgs = row.get("messages")
                    if isinstance(msgs, str):
                        try:
                            row["messages"] = json.loads(msgs)
                        except Exception as e:
                            logger.warning(f"Failed to parse messages JSON for conversation {row.get('id', 'unknown')}: {e}", exc_info=True)
                            row["messages"] = []
                    elif msgs is None:
                        row["messages"] = []
                all_supa.extend(batch)
                offset += 200
                if len(batch) < 200:
                    break
            supa_conv_rows = all_supa
        except Exception as e:
            logger.warning(f"dashboard supa conv fetch: {e}")

    merged_convs: dict = {}
    for row in supa_conv_rows:
        merged_convs[row["id"]] = row
    for cid, row in pg_conv_map.items():
        merged_convs[cid] = row

    total_convs = len(merged_convs)
    pg_conv_count = len(pg_conv_map)
    supa_conv_count = len(supa_conv_rows)

    convs_with_messages = sum(1 for c in merged_convs.values() if len(c.get("messages") or []) > 0)
    total_messages = sum(len(c.get("messages") or []) for c in merged_convs.values())

    all_dates = [c.get("created_at", "") for c in merged_convs.values() if c.get("created_at")]
    oldest_conv = min(all_dates)[:10] if all_dates else None
    newest_conv = max(all_dates)[:10] if all_dates else None

    unique_chatters = len({c.get("user_id") for c in merged_convs.values() if c.get("user_id")})

    try:
        total_subjects = await db.subjects.count_documents({}) if await is_mongo_available() else 0
    except Exception:
        total_subjects = 0
    users = await supa_list_users()
    plan_dist = {}
    for u in users:
        p = u.get("plan", "free")
        plan_dist[p] = plan_dist.get(p, 0) + 1

    # Cloudflare GraphQL is the source of truth for visitor / page-view
    # counts but does not expose bounce rate or session duration. Fan
    # those two metrics out from the Mongo-backed sessions aggregation
    # in parallel so the dashboard's two right-hand traffic tiles
    # ("Bounce Rate", "Avg Session") stop rendering as em-dash
    # placeholders.
    cf_stats, recent_events, sess_metrics = await asyncio.gather(
        cloudflare_client.get_visitor_stats_cf(days=7),
        get_recent_user_events(limit=10),
        get_session_metrics(days=7),
    )
    cf_connected = bool(cf_stats)
    if cf_stats:
        visitor_stats = {
            "total_visitors": cf_stats.get("total_visitors", 0),
            "visitors_today": cf_stats.get("visitors_today", 0),
            "page_views_today": cf_stats.get("page_views_today", 0),
            "total_page_views": cf_stats.get("total_page_views", 0),
            "total_requests": cf_stats.get("total_requests", 0),
            "requests_today": cf_stats.get("requests_today", 0),
            "total_bytes": cf_stats.get("total_bytes", 0),
            "bytes_today": cf_stats.get("bytes_today", 0),
            "daily_visitors": cf_stats.get("daily_visitors", []),
            "bounce_rate": sess_metrics.get("bounce_rate"),
            "avg_session_duration": sess_metrics.get("avg_session_duration"),
            "cloudflare": {**cf_stats, "period_days": 7},
        }
    else:
        visitor_stats = {
            "bounce_rate": sess_metrics.get("bounce_rate"),
            "avg_session_duration": sess_metrics.get("avg_session_duration"),
        }

    return {
        "total_users": total_users,
        "total_conversations": total_convs,
        "conversations_with_messages": convs_with_messages,
        "total_messages": total_messages,
        "unique_chatters": unique_chatters,
        "total_subjects": total_subjects,
        "plan_distribution": plan_dist,
        "visitor_stats": visitor_stats,
        "cf_connected": cf_connected,
        "recent_events": recent_events,
        "conversation_date_range": {
            "oldest": oldest_conv,
            "newest": newest_conv,
        },
        "pg_conversations": pg_conv_count,
        "supa_conversations": supa_conv_count,
    }

@router.get("/admin/dashboard")
async def admin_dashboard(admin: dict = Depends(get_admin_user)):
    import time as _t
    now = _t.time()
    if _dashboard_cache["data"] and (now - _dashboard_cache["ts"]) < _DASHBOARD_CACHE_TTL:
        return _dashboard_cache["data"]
    async with _dashboard_lock:
        now = _t.time()
        if _dashboard_cache["data"] and (now - _dashboard_cache["ts"]) < _DASHBOARD_CACHE_TTL:
            return _dashboard_cache["data"]
        result = await _compute_dashboard()
        _dashboard_cache["data"] = result
        _dashboard_cache["ts"] = _t.time()
        return result

@router.get("/admin/users")
async def admin_get_users(
    search: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    admin: dict = Depends(get_admin_user),
):
    users = await supa_list_users()
    if search:
        q = search.lower()
        users = [u for u in users if q in u.get("email", "").lower() or q in u.get("name", "").lower()]
    total = len(users)
    users = users[offset: offset + limit]
    result = []
    for u in users:
        u.pop("password_hash", None)
        credits_info = await get_user_credits(u)
        # Task #591: surface effective role so the admin UI can show / toggle 'educator'.
        effective_role = u.get("role") or ("admin" if u.get("is_admin") else "student")
        result.append({**u, "role": effective_role, "credits_used": credits_info["used"], "credits_limit": credits_info["limit"]})
    return {"users": result, "total": total, "limit": limit, "offset": offset}

async def _sync_push_subscription_role(user_id: str, is_admin: bool):
    try:
        role = "admin" if is_admin else "student"
        result = await db.push_subscriptions.update_many(
            {"user_id": user_id},
            {"$set": {"role": role}},
        )
        if result.modified_count > 0:
            logging.getLogger(__name__).info(
                f"Synced {result.modified_count} push subscription(s) role to '{role}' for user {user_id}"
            )
    except Exception as e:
        logging.getLogger(__name__).debug(f"push subscription role sync failed for {user_id}: {e}")


@router.patch("/admin/users/{user_id}/status")
async def admin_update_user_status(user_id: str, data: UserStatusUpdate, admin: dict = Depends(get_admin_user)):
    user = await supa_get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    await supa_update_user(user_id, {"status": data.status})
    _redis_invalidate_session(user_id)
    return {"message": "Updated"}


class AdminToggleUpdate(BaseModel):
    is_admin: bool


@router.patch("/admin/users/{user_id}/admin")
async def admin_toggle_user_admin(
    user_id: str,
    data: AdminToggleUpdate,
    admin: dict = Depends(get_admin_user),
):
    user = await supa_get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    is_admin = data.is_admin
    await supa_update_user(user_id, {"is_admin": is_admin})
    _redis_invalidate_session(user_id)
    await _sync_push_subscription_role(user_id, is_admin)
    return {"message": f"User {'promoted to admin' if is_admin else 'demoted to student'}"}


@router.patch("/admin/users/{user_id}/role")
async def admin_update_user_role(
    user_id: str,
    data: UserRoleUpdate,
    admin: dict = Depends(get_admin_user),
):
    """Task #591: promote a trusted user to the 'educator' role (or revert to 'student').

    The educator role unlocks the self-serve allowlist endpoints in routes/edu_browser.py
    via get_educator_user. Admin role flips remain on /admin/users/{id}/admin.
    """
    allowed = {"student", "educator"}
    new_role = (data.role or "").strip().lower()
    if new_role not in allowed:
        raise HTTPException(status_code=400, detail=f"role must be one of: {sorted(allowed)}")
    user = await supa_get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    prev_role = user.get("role") or ("admin" if user.get("is_admin") else "student")
    await supa_update_user(user_id, {"role": new_role})
    _redis_invalidate_session(user_id)
    try:
        await supa_insert_activity_log({
            "id": str(uuid.uuid4()),
            "action": "admin.user.role_change",
            "details": json.dumps({
                "user_id": user_id,
                "user_email": user.get("email", ""),
                "previous_role": prev_role,
                "new_role": new_role,
                "reason": (data.reason or "").strip()[:500],
            }),
            "level": "info",
            "admin_name": admin.get("name", ""),
            "admin_email": admin.get("email", ""),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        logger.warning(f"role audit log failed for {user_id}: {e}")
    return {"message": f"Role set to {new_role}", "role": new_role, "previous_role": prev_role}


@router.patch("/admin/users/{user_id}/plan")
async def admin_update_user_plan(user_id: str, data: UserPlanUpdate, admin: dict = Depends(get_admin_user)):
    user = await supa_get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    update = {"plan": data.plan}
    if data.credits_used is not None:
        update["credits_used"] = data.credits_used
    await supa_update_user(user_id, update)
    _redis_invalidate_session(user_id)
    return {"message": "Updated"}

@router.patch("/admin/users/{user_id}/credits")
async def admin_update_user_credits(user_id: str, data: UserCreditsUpdate, admin: dict = Depends(get_admin_user)):
    user = await supa_get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if data.action not in ("add", "deduct", "reset"):
        raise HTTPException(status_code=400, detail="action must be one of: add, deduct, reset")
    if data.action != "reset" and (data.amount is None or data.amount < 0):
        raise HTTPException(status_code=400, detail="amount must be a non-negative integer for add/deduct actions")
    from datetime import datetime, timezone
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    reset_date = user.get("credits_reset_date") or ""
    if isinstance(reset_date, datetime):
        reset_date = reset_date.strftime("%Y-%m-%d")
    elif hasattr(reset_date, "isoformat"):
        reset_date = str(reset_date)[:10]
    credits_used_today = user.get("credits_used_today", 0) if reset_date == today_str else 0
    action = data.action
    amount = data.amount if data.amount is not None else 0
    update = {"credits_reset_date": today_str}
    if action == "reset":
        update["credits_used_today"] = 0
    elif action == "deduct":
        update["credits_used_today"] = credits_used_today + amount
    else:
        update["credits_used_today"] = max(0, credits_used_today - amount)
    await supa_update_user(user_id, update)
    return {"message": "Credits updated", **update}

# ───────────────── Task #615: per-user quiz quota ─────────────────
# Mirror the constants & key shape from routes/edu_study.py so the admin
# read/reset surface targets the exact same rate-limit bucket the
# /edu/quiz/generate endpoint enforces. Imported lazily to avoid a
# circular import through the FastAPI router registry.

def _quiz_quota_meta(user: dict) -> tuple[str, int, int]:
    """Returns (rl_key, daily_cap, window_seconds) for the given user row."""
    from routes.edu_study import (
        QUIZ_DAILY_CAP, QUIZ_DAY_WINDOW_SEC, _quiz_daily_key,
    )
    # Signed-in users always count under kind='user' regardless of which
    # device they used, matching what _actor() returns when a JWT is present.
    return (
        _quiz_daily_key("user", user["id"]),
        QUIZ_DAILY_CAP,
        QUIZ_DAY_WINDOW_SEC,
    )


def _seconds_to_utc_midnight() -> int:
    now = datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((tomorrow - now).total_seconds())


@router.get("/admin/users/{user_id}/quiz-quota")
async def admin_get_user_quiz_quota(user_id: str, admin: dict = Depends(get_admin_user)):
    """Read the current UTC-day quiz quota counter for a single user.

    Lets operators see whether a complaining student is genuinely capped
    (vs. hitting a different error) without having to inspect Redis directly.
    """
    user = await supa_get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    rl_key, cap, window = _quiz_quota_meta(user)
    used = get_rate_limit_count(rl_key, window)
    return {
        "user_id": user_id,
        "used": used,
        "limit": cap,
        "remaining": max(0, cap - used),
        "scope": "day",
        "resets_in_seconds": _seconds_to_utc_midnight(),
        "resets_at": "midnight UTC",
    }


@router.post("/admin/users/{user_id}/quiz-quota/reset")
async def admin_reset_user_quiz_quota(user_id: str, admin: dict = Depends(get_admin_user)):
    """Clear today's quiz-quota counter for a user. Audited."""
    user = await supa_get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    rl_key, cap, window = _quiz_quota_meta(user)
    cleared = reset_rate_limit(rl_key, window)
    try:
        await supa_insert_activity_log({
            "id": str(uuid.uuid4()),
            "action": "admin.user.quiz_quota_reset",
            "details": json.dumps({
                "user_id": user_id,
                "user_email": user.get("email", ""),
                "cleared_count": cleared,
                "limit": cap,
            }),
            "level": "info",
            "admin_name": admin.get("name", ""),
            "admin_email": admin.get("email", ""),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        logger.warning(f"quiz quota reset audit log failed for {user_id}: {e}")
    return {"message": "Quiz quota reset", "cleared": cleared, "limit": cap}


@router.post("/admin/sync-conversations")
async def admin_sync_conversations(admin: dict = Depends(get_admin_user)):
    """Sync all Supabase conversations → PostgreSQL (upsert, PG wins on message count tie)."""
    if not supa or not deps.pg_pool:
        raise HTTPException(status_code=503, detail="Both Supabase and PostgreSQL required for sync")

    # ── 1. Fetch all Supabase conversations (paginated) ──────────────────────
    all_supa: list = []
    offset = 0
    while True:
        r = await _supa(lambda o=offset: supa.table("conversations")
            .select("id, user_id, title, preview, subject_id, subject_name, starred, archived, messages, tokens, created_at, updated_at")
            .order("created_at", desc=False)
            .range(o, o + 199).execute())
        batch = r.data or []
        if not batch:
            break
        for row in batch:
            msgs = row.get("messages")
            if isinstance(msgs, str) and msgs.strip():
                try:
                    parsed = json.loads(msgs)
                    row["_parsed_messages"] = parsed if isinstance(parsed, list) else []
                    row["_raw_messages"] = msgs
                except Exception:
                    row["_parsed_messages"] = []
                    row["_raw_messages"] = "[]"
            elif isinstance(msgs, list):
                row["_parsed_messages"] = msgs
                row["_raw_messages"] = json.dumps(msgs)
            else:
                row["_parsed_messages"] = []
                row["_raw_messages"] = "[]"
        all_supa.extend(batch)
        offset += 200
        if len(batch) < 200:
            break

    total_supa = len(all_supa)

    # ── 2. Fetch existing PG ids + message lengths ────────────────────────────
    async with deps.pg_pool.acquire() as conn:
        pg_rows = await conn.fetch("SELECT id, octet_length(messages) AS msg_len FROM conversations")
    pg_map = {r["id"]: (r["msg_len"] or 0) for r in pg_rows}

    # ── 3. Upsert each Supabase row into PG ───────────────────────────────────
    inserted = 0
    updated = 0
    skipped = 0
    errors = 0

    UPSERT_SQL = """
        INSERT INTO conversations
            (id, user_id, title, preview, subject_id, subject_name, starred, archived,
             messages, tokens, created_at, updated_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
        ON CONFLICT (id) DO UPDATE SET
            messages   = CASE
                WHEN octet_length(EXCLUDED.messages) > octet_length(conversations.messages)
                THEN EXCLUDED.messages
                ELSE conversations.messages
            END,
            title      = COALESCE(EXCLUDED.title, conversations.title),
            preview    = COALESCE(EXCLUDED.preview, conversations.preview),
            updated_at = GREATEST(EXCLUDED.updated_at, conversations.updated_at)
    """

    async with deps.pg_pool.acquire() as conn:
        for row in all_supa:
            try:
                raw_msgs = row["_raw_messages"]
                supa_msg_len = len(raw_msgs.encode())
                pg_msg_len = pg_map.get(row["id"], -1)

                if pg_msg_len == -1:
                    # Not in PG at all — insert
                    action = "insert"
                elif supa_msg_len > pg_msg_len:
                    # Supabase has more data — update
                    action = "update"
                else:
                    skipped += 1
                    continue

                await conn.execute(
                    UPSERT_SQL,
                    row.get("id"),
                    row.get("user_id"),
                    row.get("title") or "Untitled",
                    row.get("preview") or "",
                    row.get("subject_id") or "",
                    row.get("subject_name") or "",
                    bool(row.get("starred", False)),
                    bool(row.get("archived", False)),
                    raw_msgs,
                    int(row.get("tokens") or 0),
                    str(row.get("created_at") or ""),
                    str(row.get("updated_at") or ""),
                )
                if action == "insert":
                    inserted += 1
                else:
                    updated += 1
            except Exception as e:
                logger.warning(f"sync conv {row.get('id')}: {e}")
                errors += 1

    # ── 4. Final PG counts ────────────────────────────────────────────────────
    async with deps.pg_pool.acquire() as conn:
        pg_total = await conn.fetchval("SELECT COUNT(*) FROM conversations") or 0
        pg_with_msgs = await conn.fetchval(
            "SELECT COUNT(*) FROM conversations WHERE messages IS NOT NULL AND messages != '[]' AND length(messages) > 2"
        ) or 0
        pg_total_msgs = await conn.fetchval(
            "SELECT SUM(jsonb_array_length(messages::jsonb)) FROM conversations "
            "WHERE messages IS NOT NULL AND messages != '[]' AND length(messages) > 2"
        ) or 0

    return {
        "ok": True,
        "supa_total": total_supa,
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "pg_total_after": pg_total,
        "pg_with_messages_after": pg_with_msgs,
        "pg_total_messages_after": pg_total_msgs,
    }


@router.get("/admin/conversations")
async def admin_get_conversations(admin: dict = Depends(get_admin_user)):
    # Fetch from both PostgreSQL and Supabase and merge (PG takes precedence for messages)
    pg_convs: list = []
    supa_convs: list = []

    if deps.pg_pool:
        try:
            async with deps.pg_pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT * FROM conversations ORDER BY updated_at DESC LIMIT 1000"
                )
                pg_convs = _pg_rows(rows)
        except Exception as e:
            logger.warning(f"admin_get_conversations pg fetch: {e}")

    if supa:
        try:
            r = await _supa(lambda: supa.table("conversations").select("*").order("updated_at", desc=True).limit(1000).execute())
            for row in (r.data or []):
                if isinstance(row.get("messages"), str):
                    try:
                        row["messages"] = json.loads(row["messages"])
                    except Exception as e:
                        logger.warning(f"Failed to parse messages JSON for conversation {row.get('id', 'unknown')}: {e}", exc_info=True)
                        row["messages"] = []
                elif row.get("messages") is None:
                    row["messages"] = []
            supa_convs = r.data or []
        except Exception as e:
            logger.warning(f"admin_get_conversations supa fetch: {e}")

    # Merge: use PG row if available (has real messages), otherwise use Supabase row
    pg_ids = {c.get("id") for c in pg_convs}
    merged = list(pg_convs)
    for sc in supa_convs:
        if sc.get("id") not in pg_ids:
            if isinstance(sc.get("messages"), list):
                pass  # already parsed
            sc["messages"] = sc.get("messages") or []
            merged.append(sc)

    merged_ids = {c.get("id") for c in merged}

    anon_convs: list = []
    try:
        anon_convs = redis_list_all_anon_conversations()
    except Exception as e:
        logger.warning(f"admin_get_conversations anon fetch: {e}")

    for ac in anon_convs:
        raw_id = ac.get("id")
        if raw_id in merged_ids:
            for mc in merged:
                if mc.get("id") == raw_id:
                    redis_updated = ac.get("updated_at", "")
                    pg_updated = mc.get("updated_at", "")
                    if redis_updated > pg_updated:
                        mc["messages"] = ac.get("messages") or mc.get("messages") or []
                        mc["updated_at"] = redis_updated
                    if not mc.get("is_anonymous"):
                        mc["is_anonymous"] = True
                        mc["anon_id"] = ac.get("anon_id")
                    break
        else:
            ac["is_anonymous"] = True
            ac["user_id"] = ac.get("anon_id") or ac.get("user_id")
            merged.append(ac)
            merged_ids.add(raw_id)

    # Sort by updated_at desc
    def _conv_sort_key(c):
        ts = c.get("updated_at") or c.get("created_at") or ""
        return str(ts)

    merged.sort(key=_conv_sort_key, reverse=True)

    # Enrich with user info
    user_ids = list({c.get("user_id") for c in merged if c.get("user_id")})
    users_map = {}
    if user_ids:
        try:
            users = await supa_get_users_by_ids(user_ids)
            users_map = {u["id"]: u for u in users}
        except Exception:
            pass
    for c in merged:
        if c.get("is_anonymous"):
            c["user_name"] = "Anonymous User"
            c["user_email"] = ""
            c["user_plan"] = "anonymous"
            c["user_avatar"] = ""
            c["user_board"] = ""
            c["user_class"] = ""
            c["user_stream"] = ""
        else:
            uid = c.get("user_id")
            u = users_map.get(uid, {})
            c["user_name"] = u.get("name", "")
            c["user_email"] = u.get("email", c.get("user_email", ""))
            c["user_plan"] = u.get("plan", "free")
            c["user_avatar"] = u.get("avatar_url", "")
            c["user_board"] = u.get("board_name", "")
            c["user_class"] = u.get("class_name", "")
            c["user_stream"] = u.get("stream_name", "")
        c["has_messages"] = len(c.get("messages") or []) > 0

    return merged


# ── Supabase Auth migration ────────────────────────────────────────────────────

class SupabaseSyncResult(BaseModel):
    created: int
    skipped_exists: int
    skipped_google: int
    error: int
    email_sent: int
    dry_run: bool


@router.post("/admin/sync-users-to-supabase", response_model=SupabaseSyncResult)
async def admin_sync_users_to_supabase(
    dry_run: bool = False,
    skip_email: bool = False,
    admin: dict = Depends(get_admin_user),
):
    """
    Create Supabase Auth accounts for every local-DB user who doesn't already
    have one, then send each a password-reset email so they can regain access.

    Google OAuth users are skipped — Supabase creates their accounts on first login.

    Query params:
      dry_run=true    — preview only, make no changes
      skip_email=true — create accounts but don't send reset emails
    """
    if not supa:
        raise HTTPException(status_code=503, detail="Supabase not configured on this server")
    if not deps.pg_pool:
        raise HTTPException(status_code=503, detail="PostgreSQL not available")

    from scripts.sync_users_to_supabase import (
        _fetch_all_local_users,
        _fetch_all_supabase_auth_emails,
        _create_supabase_user,
        _generate_recovery_link,
        _send_reset_email,
    )

    _log = logging.getLogger(__name__)

    local_users = await _fetch_all_local_users(deps.pg_pool)
    existing_emails = await asyncio.to_thread(_fetch_all_supabase_auth_emails, supa)

    stats = {"created": 0, "skipped_google": 0, "skipped_exists": 0, "error": 0, "email_sent": 0}

    for user in local_users:
        email    = (user.get("email") or "").lower().strip()
        name     = user.get("name") or ""
        provider = user.get("auth_provider") or "email"
        status   = user.get("status") or "active"

        if not email or status == "banned":
            continue

        if provider == "google":
            stats["skipped_google"] += 1
            continue

        if email in existing_emails:
            stats["skipped_exists"] += 1
            continue

        if dry_run:
            stats["created"] += 1
            continue

        ok, err = await asyncio.to_thread(_create_supabase_user, supa, email, name)
        if not ok:
            _log.error("sync_users_to_supabase: error creating %s: %s", email, err)
            stats["error"] += 1
            continue

        if err == "already_exists":
            stats["skipped_exists"] += 1
            continue

        stats["created"] += 1
        existing_emails.add(email)

        if skip_email:
            continue

        link = await asyncio.to_thread(_generate_recovery_link, supa, email)
        if link:
            await asyncio.to_thread(_send_reset_email, email, name, link)
            stats["email_sent"] += 1
        else:
            _log.warning("sync_users_to_supabase: no recovery link for %s", email)

    return {**stats, "dry_run": dry_run}

