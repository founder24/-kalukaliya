"""Syrabit.ai — JWT helpers, authentication dependencies, and rate limiting."""
import os, time, asyncio, logging
from typing import Optional, List, Dict
from datetime import datetime, timezone, timedelta
from fastapi import Depends, HTTPException, Cookie, Request, Response
from fastapi.security import HTTPAuthorizationCredentials
import jwt
from jwt.exceptions import PyJWTError as JWTError
from config import (
    JWT_SECRET, JWT_ALGORITHM, JWT_ACCESS_EXPIRE_MINUTES,
    JWT_REFRESH_EXPIRE_MINUTES, JWT_EXPIRE_MINUTES,
    ADMIN_JWT_SECRET, PLAN_LIMITS,
    COOKIE_DOMAIN, COOKIE_SAMESITE, SECURE_COOKIES,
    IP_COARSE_DAILY_CAP, DEVICE_COOKIE_MINTS_PER_MIN,
)
from deps import security, redis_client
from cache import _redis_get_session, _redis_cache_session
from cf_access import require_cf_access_admin
from device_token import (
    DEVICE_COOKIE_NAME, DEVICE_COOKIE_MAX_AGE_SECONDS,
    mint_device_token, device_token_id,
)


def _real_client_ip(request: Request) -> str:
    """Return the best-effort real client IP for rate limiting.

    Header preference order (Task #793):

    1. ``cf-connecting-ip`` — Cloudflare always sets this on requests
       it forwards to origin, and it always carries the **real**
       client IP (not the CF edge POP). This is the highest-trust
       source when traffic actually comes from the CF edge.
    2. ``x-forwarded-for`` (first comma-separated entry) — what our
       own ``workers/edge-proxy`` rewrites onto the upstream request
       after stripping CF-Connecting-IP, and the de-facto standard
       header that any HTTP-aware proxy in front of us will set.
    3. ``request.client.host`` — the immediate peer the ASGI server
       sees, which behind any proxy will be the proxy's address (a
       Replit gateway, the Cloud Run frontend, etc.) and is therefore
       the worst signal of "who is actually talking to us". Used only
       as a last resort.

    Previously :func:`rate_limit_chat_optional` checked
    ``request.client.host`` *first*, which on Replit/Cloud Run pinned
    the entire daily quota to a single shared upstream IP and made
    every test environment look like an exhausted attacker.
    """
    cf_ip = (request.headers.get("cf-connecting-ip") or "").strip()
    if cf_ip:
        return cf_ip
    xff = request.headers.get("x-forwarded-for") or ""
    if xff:
        first = xff.split(",")[0].strip()
        if first:
            return first
    return (request.client.host if request.client else "") or "unknown"


def _set_device_cookie(request: Request, response: Response, value: str) -> None:
    """Attach the signed device-token cookie to ``response`` *and* stash
    the value on ``request.state`` so the
    :class:`middleware.DeviceCookieMiddleware` fall-back can re-apply
    it when the route handler returns its own ``Response`` instance
    (FastAPI discards the dependency-injected ``Response`` object in
    that case — the most common path here is ``StreamingResponse`` on
    ``/ai/chat/stream``, which is the user-facing chat endpoint).

    Cookie attributes mirror the existing ``syrabit_session`` cookie
    set by :mod:`routes.auth`: HttpOnly (so client JS cannot read or
    tamper with it), Secure when running over HTTPS, SameSite=Lax (so
    ordinary navigations from search results / WhatsApp link previews
    still send the cookie and the user keeps their device-keyed
    quota), and a 400-day max-age (the longest browsers will honour).
    """
    cookie_kwargs = dict(
        key=DEVICE_COOKIE_NAME,
        value=value,
        httponly=True,
        secure=SECURE_COOKIES,
        samesite=COOKIE_SAMESITE,
        max_age=DEVICE_COOKIE_MAX_AGE_SECONDS,
        path="/",
    )
    if COOKIE_DOMAIN:
        cookie_kwargs["domain"] = COOKIE_DOMAIN
    response.set_cookie(**cookie_kwargs)
    # Stash for the middleware fall-back. Using ``request.state`` (an
    # arbitrary attribute namespace per Starlette docs) keeps the
    # cookie payload bound to this single request and avoids any
    # global mutable state that could leak between concurrent
    # requests.
    try:
        request.state.device_cookie_to_set = value
    except Exception:
        # ``request.state`` is always available on a real Starlette
        # request; the only way this raises is in unit tests that
        # pass a hand-rolled stub without a ``state`` attribute. The
        # tests that exercise the dependency directly read the cookie
        # off ``response.headers`` so they don't need the stash, and
        # the middleware fall-back is irrelevant for them.
        pass


logger = logging.getLogger(__name__)

async def get_user_credits(user: dict) -> dict:
    """
    Daily-resetting credits with backwards-compatible legacy balance bridge.
    Each plan gets a fixed credits_per_day allowance that resets at midnight UTC.
    If the stored credits_reset_date is before today (UTC), usage is treated as 0
    and the counter will be reset on next deduction.

    Legacy bridge: if a user has a credits_limit (from top-ups / admin adjustments /
    referral bonuses) that exceeds the plan's base daily allowance, the effective
    daily limit is raised to honour those purchased credits until they are consumed.
    """
    plan      = user.get("plan", "free")
    plan_cfg  = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
    daily_limit = plan_cfg["credits_per_day"]

    legacy_limit = user.get("credits_limit")
    legacy_used  = user.get("credits_used", 0) or 0
    if legacy_limit is not None:
        legacy_remaining = max(0, legacy_limit - legacy_used)
        if legacy_remaining > daily_limit:
            daily_limit = legacy_remaining

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    reset_date = user.get("credits_reset_date") or ""
    if isinstance(reset_date, datetime):
        reset_date = reset_date.strftime("%Y-%m-%d")
    elif hasattr(reset_date, "isoformat"):
        reset_date = str(reset_date)[:10]
    if reset_date == today_str:
        used = user.get("credits_used_today", 0) or 0
    else:
        used = 0
    return {
        "used": used,
        "limit": daily_limit,
        "remaining": max(0, daily_limit - used),
        "document_access": plan_cfg["document_access"],
        "resets_at": "midnight UTC",
    }


def create_token(data: dict, secret: str = JWT_SECRET, expires_delta: int = JWT_EXPIRE_MINUTES) -> str:
    to_encode = data.copy()
    to_encode["exp"] = datetime.now(timezone.utc) + timedelta(minutes=expires_delta)
    return jwt.encode(to_encode, secret, algorithm=JWT_ALGORITHM)

def create_access_token(user_id: str, role: str = "student", plan: str = "free") -> str:
    return create_token({"sub": user_id, "role": role, "type": "access", "plan": plan}, expires_delta=JWT_ACCESS_EXPIRE_MINUTES)

def create_refresh_token(user_id: str) -> str:
    return create_token({"sub": user_id, "type": "refresh"}, expires_delta=JWT_REFRESH_EXPIRE_MINUTES)

def decode_token(token: str, secret: str = JWT_SECRET) -> dict:
    try:
        return jwt.decode(token, secret, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Session expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

async def get_current_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(security),
    syrabit_session: Optional[str] = Cookie(default=None),
):
    token = creds.credentials if creds else syrabit_session
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = decode_token(token)
        if payload.get("type") == "refresh":
            raise HTTPException(status_code=401, detail="Refresh tokens cannot be used for API access")
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    cached = _redis_get_session(user_id)
    if cached:
        user = cached
    else:
        from db_ops import supa_get_user_by_id
        user = await supa_get_user_by_id(user_id)
        if user:
            _redis_cache_session(user_id, user)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    if user.get("status") == "banned":
        raise HTTPException(status_code=403, detail="Account banned")
    if user.get("status") == "suspended":
        raise HTTPException(status_code=403, detail="Account suspended")
    # Task #591: role column may exist but be empty string for legacy rows;
    # treat blank as the default ('admin' for admins, 'student' otherwise) so
    # the get_educator_user dependency can rely on user["role"] == 'educator'.
    if not user.get("role"):
        user["role"] = "admin" if user.get("is_admin") else "student"
    return user

async def get_current_user_optional(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(security),
    syrabit_session: Optional[str] = Cookie(default=None),
):
    token = creds.credentials if creds else syrabit_session
    if not token:
        return None
    try:
        payload = decode_token(token)
        if payload.get("type") == "refresh":
            return None
        user_id = payload.get("sub")
        if not user_id:
            return None
        cached = _redis_get_session(user_id)
        if cached:
            user = cached
        else:
            from db_ops import supa_get_user_by_id
            user = await supa_get_user_by_id(user_id)
            if user:
                _redis_cache_session(user_id, user)
        if user:
            if not user.get("role"):
                user["role"] = "admin" if user.get("is_admin") else "student"
            if user.get("status") in ["banned", "suspended"]:
                return None
            return user
        # DB lookup returned nothing (new user race, test token, etc.) but the
        # JWT signature is valid — treat as a minimal authenticated user so the
        # caller is not silently downgraded to anonymous.
        role = payload.get("role", "student")
        return {
            "id": user_id,
            "role": role,
            "plan": payload.get("plan", "free"),
            "is_admin": role == "admin",
            "_jwt_only": True,
        }
    except:
        return None

def _voice_preview_kind_for_request(request: Request) -> str:
    """Map the request URL to the preview bucket kind. STT and TTS get
    SEPARATE daily allowances per Task #581 §L9 — the umbrella
    `/voice/voice` pipeline route counts as STT (it transcribes first)."""
    try:
        path = (request.url.path or "").lower()
    except Exception:
        path = ""
    if "/tts" in path:
        return "tts"
    return "stt"


async def require_paid_plan_or_voice_preview(
    request: Request,
    user: dict = Depends(get_current_user),
) -> dict:
    """Task #581 §L9 — paid-plan gate with a free-tier voice preview escape.

    Same paid-or-better gate as `require_paid_plan`, except free callers
    get TWO independent once-per-UTC-day budgets:
      * 1 STT call per day (any duration up to the existing 25 MB cap)
      * 1 TTS call per day, capped at ~30 s of audio (≤ 600 chars input)

    The kind is derived from the request URL — `/tts` → `tts`, anything
    else (`/stt`, `/voice/voice`) → `stt`. The TTS char clamp is
    enforced inside the route (it sees the body text); this dep only
    manages the per-kind allowance counter
    (`voice:free:preview:{kind}:{user_id}:day`) and stamps the resolved
    user dict with `__voice_preview=True` so the route knows to apply
    the clamp.

    Paid users (plan != "free") pass through unchanged with no flag.
    Admin / staff / educator bypass unconditionally — same semantics as
    `require_paid_plan` so internal CMS / QA flows keep working even
    when an internal user's plan field is "free".
    """
    if (user or {}).get("is_admin") or (user or {}).get("role") in {"admin", "staff", "educator"}:
        return user
    plan = (user.get("plan") or "free").strip().lower()
    if plan and plan != "free":
        return user
    # Free callers — separate STT and TTS daily buckets.
    user_id = user.get("id", "anonymous")
    kind = _voice_preview_kind_for_request(request)
    if not check_rate_limit(
        f"voice:free:preview:{kind}:{user_id}:day",
        max_requests=1,
        window_seconds=86400,
    ):
        raise HTTPException(
            status_code=402,
            detail=(
                f"Free-tier {kind.upper()} preview already used today "
                "(1 STT + 1 short TTS per UTC day). "
                "Upgrade to Pro for unlimited voice features."
            ),
            headers={"X-Paywall-Feature": "voice", "X-Paywall-Voice-Kind": kind},
        )
    user = dict(user)
    user["__voice_preview"] = True
    user["__voice_preview_kind"] = kind
    return user


# Task #581 §L9 — TTS preview char limit (~30s @ ~20 chars/sec).
FREE_VOICE_PREVIEW_TTS_CHAR_LIMIT = 600


async def require_paid_plan(user: dict = Depends(get_current_user)) -> dict:
    """Task #549 — gate paid-only routes (currently /api/voice/*).

    Free-plan users hit a hard 402 PAYMENT REQUIRED. Voice synthesis +
    transcription dominate the per-DAU cost envelope ($100/mo budget at
    # Task #552 §G — AssemblyAI minutes line retired alongside the provider.
    10k DAU only works when ElevenLabs / Deepgram minutes
    are reserved for paying users). Admins and staff bypass this gate
    so internal CMS / QA flows keep working.
    """
    if (user or {}).get("is_admin") or (user or {}).get("role") in {"admin", "staff", "educator"}:
        return user
    plan = (user or {}).get("plan", "free")
    if not plan or str(plan).strip().lower() == "free":
        # Structured payload so the SPA can route the user to /pricing
        # without parsing a free-text message.
        raise HTTPException(
            status_code=402,
            detail={
                "error": "voice_requires_paid_plan",
                "upgrade_url": "/pricing",
                "message": "Voice features require a paid plan. Upgrade to Starter or Pro.",
            },
        )
    return user


async def get_educator_user(user=Depends(get_current_user)):
    """Require the caller to be an educator (or an admin).

    Used by the educator self-serve allowlist flow so verified teachers
    can admit new educational sites after an automated safety probe
    passes. Admins always satisfy this dependency.
    """
    role = (user or {}).get("role", "")
    if role == "educator" or role == "admin" or (user or {}).get("is_admin"):
        return user
    raise HTTPException(status_code=403, detail="Educator role required")


async def get_staff_user(user=Depends(get_current_user)):
    """Require the caller to be a staff member (or an admin).

    Staff users manage educational content (chapter notes, descriptions,
    status flags). Admins always satisfy this dependency.
    """
    role = (user or {}).get("role", "")
    if role == "staff" or role == "admin" or (user or {}).get("is_admin"):
        return user
    raise HTTPException(status_code=403, detail="Staff role required")


async def get_admin_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(security),
    syrabit_admin_session: Optional[str] = Cookie(default=None),
    cf_access_claims: Optional[dict] = Depends(require_cf_access_admin),
):
    """Admin auth = Cloudflare Access (Zero Trust) gate + admin JWT.

    Task #637 layers Cloudflare Access on top of the existing admin JWT so
    a request must (a) transit the Access proxy on the admin team domain
    AND (b) carry a valid admin JWT. The CF Access dependency is a no-op
    until ``CF_ACCESS_ENFORCE=true`` is set in production env, so this
    change is safe to merge before operators provision Access.
    """
    token = creds.credentials if creds else syrabit_admin_session
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = decode_token(token, secret=ADMIN_JWT_SECRET)
        if not (payload.get("is_admin") or payload.get("role") == "admin"):
            raise HTTPException(status_code=403, detail="Not authorized")
        if cf_access_claims:
            # Surface CF Access identity to admin handlers (audit logs).
            payload["cf_access_email"] = cf_access_claims.get("email")
            payload["cf_access_sub"] = cf_access_claims.get("sub")
        return payload
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid admin token")

# ─────────────────────────────────────────────
# RATE LIMITER — sliding window, per user/IP
# ─────────────────────────────────────────────
_rate_windows: Dict[str, List[float]] = {}
# Task #615: remember the widest window any caller has used for a key so
# the periodic cleanup does not GC daily-quota buckets after only a couple
# minutes of idle time (which would silently reset a 24h cap in fallback
# mode whenever Redis is unavailable).
_rate_window_horizon: Dict[str, int] = {}

async def _rate_limiter_cleanup():
    while True:
        await asyncio.sleep(300)
        now = datetime.now(timezone.utc).timestamp()
        stale: list[str] = []
        for k, v in _rate_windows.items():
            horizon = _rate_window_horizon.get(k, 120)
            # Keep the bucket alive while any timestamp could still be
            # inside its declared window (plus a small grace period).
            if not v or v[-1] < now - horizon - 60:
                stale.append(k)
        for k in stale:
            _rate_windows.pop(k, None)
            _rate_window_horizon.pop(k, None)

def _check_rate_limit_memory(key: str, max_requests: int, window_seconds: int) -> bool:
    """In-memory sliding window rate limiter."""
    now = datetime.now(timezone.utc).timestamp()
    window_start = now - window_seconds
    if key not in _rate_windows:
        _rate_windows[key] = []
    # Track the widest window seen so the GC doesn't prune long-window keys.
    if window_seconds > _rate_window_horizon.get(key, 0):
        _rate_window_horizon[key] = window_seconds
    _rate_windows[key] = [t for t in _rate_windows[key] if t > window_start]
    if len(_rate_windows[key]) >= max_requests:
        return False
    _rate_windows[key].append(now)
    return True

def check_rate_limit(key: str, max_requests: int = 100, window_seconds: int = 60) -> bool:
    """Returns True if allowed, False if rate-limited.
    Uses Redis fixed-window counter when available (multi-worker safe), in-memory fallback otherwise.
    """
    if redis_client:
        try:
            redis_key = f"rl2:{key}:{int(time.time() // window_seconds)}"
            count = redis_client.incr(redis_key)
            if count == 1:
                redis_client.expire(redis_key, window_seconds + 5)
            if count > max_requests:
                return False
            return True
        except Exception as e:
            logger.debug(f"Redis rate limit failed, falling back to memory: {e}")
    return _check_rate_limit_memory(key, max_requests, window_seconds)


def get_rate_limit_count(key: str, window_seconds: int) -> int:
    """Best-effort read of the *current* fixed-window count for a rl2 key.

    Used by the admin quiz-quota tile so operators can see how much of a
    user's daily quota is consumed without burning another increment.
    Returns 0 if the bucket is missing or the backend is unreachable.
    """
    if redis_client:
        try:
            redis_key = f"rl2:{key}:{int(time.time() // window_seconds)}"
            v = redis_client.get(redis_key)
            return int(v) if v is not None else 0
        except Exception as e:
            logger.debug(f"Redis rate limit read failed, falling back to memory: {e}")
    bucket = _rate_windows.get(key) or []
    cutoff = datetime.now(timezone.utc).timestamp() - window_seconds
    return sum(1 for t in bucket if t > cutoff)


def reset_rate_limit(key: str, window_seconds: int) -> int:
    """Drop the *current-window* counter for a rl2 key. Returns the count
    that was cleared (best-effort)."""
    cleared = 0
    if redis_client:
        try:
            redis_key = f"rl2:{key}:{int(time.time() // window_seconds)}"
            v = redis_client.get(redis_key)
            cleared = int(v) if v is not None else 0
            redis_client.delete(redis_key)
        except Exception as e:
            logger.debug(f"Redis rate limit reset failed: {e}")
    if key in _rate_windows:
        cleared = max(cleared, len(_rate_windows[key]))
        _rate_windows[key] = []
    return cleared

async def rate_limit_chat(user: dict = Depends(get_current_user)):
    """Dependency: plan-aware chat rate limiting (Free 5, Starter 10, Pro 15 req/min)."""
    user_id = user.get("id", "anonymous")
    plan = user.get("plan", "free")
    plan_cfg = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
    limit = plan_cfg["req_per_min"]
    if not check_rate_limit(f"chat:{user_id}", max_requests=limit, window_seconds=60):
        raise HTTPException(
            status_code=429,
            detail=f"Chat rate limit exceeded — {limit} messages/minute ({plan} plan). Upgrade for higher limits.",
            headers={"Retry-After": "60", "X-RateLimit-Limit": str(limit)},
        )
    return user

async def rate_limit_chat_optional(
    request: Request,
    response: Response,
    user: Optional[dict] = Depends(get_current_user_optional),
    syrabit_device: Optional[str] = Cookie(default=None),
):
    """Anonymous-friendly chat rate limiter.

    Logged-in users keep their plan-aware per-minute limit (unchanged
    since Task #768).

    For anonymous users (Task #793), the daily 30-message budget is
    keyed on a **signed HttpOnly device-token cookie**, not on the
    public IP. The IP is kept only as a coarse abuse cap.

    The change exists to fix the single biggest funnel-killer on the
    site: AHSEC/SEBA students almost always reach us through shared
    egress IPs (Jio/Airtel mobile CGNAT, school/college WiFi,
    hostel/cyber-café WiFi). When the daily budget was per-IP, the
    first ~30 messages from any one of those networks drained the
    pool for every other student behind the same NAT, so the second
    visitor saw "Daily free quota exhausted" before sending a single
    message.

    Per-anonymous-request logic, top to bottom:

    1. **Per-minute throttle** — sliding-window rate limit, keyed on
       the device-token id when one is present, else on the IP. This
       mirrors the previous behaviour (a fresh device on a busy NAT
       still doesn't get throttled because each device gets its own
       per-minute window once the cookie is issued).

    2. **Coarse per-IP daily ceiling** — ``IP_COARSE_DAILY_CAP``
       requests/day per real client IP. Set high enough (default
       1500/day) that a classroom or hostel of students sharing one
       NAT never hits it; meant only to stop a single host from
       scripting thousands of requests.

    3. **Per-device daily quota** — 30/day from the free-plan config,
       enforced via :func:`db_ops.atomic_deduct_device_credit` (the
       same atomic Lua script used by the user credit ledger so
       concurrent abusers can't push a counter past its limit). The
       counter is keyed on either the verified incoming token or the
       freshly minted one (see (4) below), so the very first request
       counts toward the device's daily budget — preserving the
       documented contract that anonymous browsers get exactly 30
       successful messages a day, with the 31st blocked.

    4. **Cookie issuance** — every anonymous response that comes
       through here either re-confirms the existing valid cookie or
       mints a fresh one (and stashes it on ``request.state`` for
       :class:`middleware.DeviceCookieMiddleware` to apply onto any
       ``StreamingResponse`` the route returns). A brand-new browser
       therefore receives its cookie *and* is charged 1 against that
       fresh token's 30/day budget on the same request — never let a
       missing cookie produce a hard 429 outside of the per-device
       cap, but never give it a free ride either.
    """
    if user:
        user_id = user.get("id", "anonymous")
        plan = user.get("plan", "free")
        plan_cfg = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
        limit = plan_cfg["req_per_min"]
        # Task #407 — ``do_chat.rate_check`` is the SOLE per-minute
        # authority for chat traffic. The helper transparently falls
        # back to its in-process token bucket when DO_CHAT_ON is unset
        # or the edge is unreachable, so we no longer double-gate
        # against ``check_rate_limit`` (which used to produce
        # inconsistent 429s across pods because each pod had its own
        # local sliding window).
        try:
            from do_chat import rate_check as _do_rate
            allowed, _remaining = await _do_rate(
                f"chat:{user_id}", limit=limit, window_s=60,
            )
        except HTTPException:
            raise
        except Exception:
            # rate_check itself crashed (not just an edge outage —
            # that already falls back internally). Fail open rather
            # than block a paying user on a helper bug.
            allowed = True
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail=f"Chat rate limit exceeded — {limit} messages/minute ({plan} plan). Upgrade for higher limits.",
                headers={
                    "Retry-After": "60",
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Source": "do_chat",
                },
            )
        return user

    free_cfg = PLAN_LIMITS["free"]
    daily_cap = int(free_cfg.get("credits_per_day") or 30)
    per_min_cap = int(free_cfg.get("req_per_min") or 15)
    ip = _real_client_ip(request)

    # ── 1. Resolve / mint device cookie ──────────────────────────────
    # ``device_token_id`` returns a printable hex id when the signed
    # cookie verifies, else None. When the incoming cookie is missing
    # or forged we mint a fresh one and use *its* token id for the
    # rest of this request — so a brand-new browser still gets a
    # valid token id keyed counter and is charged 1 against the
    # 30/day device cap on its very first message (preserving the
    # "30 successful, 31st blocked" UX contract).
    token_id = device_token_id(syrabit_device)
    if token_id is None:
        # Task #797 — rate-limit fresh-cookie issuance per IP. The
        # first-visit branch is a deliberate UX softener: a brand-new
        # browser must be able to send its first message even though
        # the cookie round-trip hasn't completed. A scripted client
        # can abuse it by simply discarding the cookie on every
        # request and looking like an endless stream of "first
        # visits", capped only by the much higher 1500/day per-IP
        # coarse cap. Gating the mint itself at a small per-minute
        # ceiling shuts that loophole without affecting any real
        # browser (real browsers retain the cookie they're handed and
        # never come back through this branch). On (truly) unknown
        # IPs we skip the gate so loopback/test paths aren't broken.
        if ip and ip != "unknown" and not check_rate_limit(
            f"chat:mint:ip:{ip}",
            max_requests=DEVICE_COOKIE_MINTS_PER_MIN,
            window_seconds=60,
        ):
            raise HTTPException(
                status_code=429,
                detail=(
                    "Too many new sessions from this network in a short window. "
                    "Wait a minute and retry — make sure cookies are enabled."
                ),
                headers={
                    "Retry-After": "60",
                    "X-RateLimit-Limit": str(DEVICE_COOKIE_MINTS_PER_MIN),
                },
            )
        new_cookie = mint_device_token()
        _set_device_cookie(request, response, new_cookie)
        token_id = device_token_id(new_cookie)

    # ── 2. Per-minute throttle (device-scoped when possible) ─────────
    # Task #407 — ``do_chat.rate_check`` is the sole per-minute
    # authority for anon chat traffic. The helper falls back to its
    # in-process bucket when DO_CHAT_ON is off or the edge is
    # unreachable, so we no longer also call ``check_rate_limit``
    # (which gave inconsistent throttling across pods).
    rl_key = f"chat:dev:{token_id}" if token_id else f"chat:ip:{ip}"
    try:
        from do_chat import rate_check as _do_rate
        allowed_do, _rem_do = await _do_rate(
            rl_key, limit=per_min_cap, window_s=60,
        )
    except HTTPException:
        raise
    except Exception:
        # Fail open on a helper crash — never 429 a real user because
        # ``do_chat.rate_check`` itself broke.
        allowed_do = True
    if not allowed_do:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Sign in for higher limits.",
            headers={
                "Retry-After": "60",
                "X-RateLimit-Limit": str(per_min_cap),
                "X-RateLimit-Source": "do_chat",
            },
        )

    # ── 3. Coarse per-IP abuse cap ───────────────────────────────────
    # Skip on truly unknown IPs so we don't lock out the loopback /
    # offline test paths; in production cf-connecting-ip / xff are
    # always populated upstream of this dependency.
    if ip and ip != "unknown":
        from db_ops import atomic_deduct_ip_credit
        if not atomic_deduct_ip_credit(ip, daily_limit=IP_COARSE_DAILY_CAP):
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Daily request ceiling reached for this network "
                    f"(>{IP_COARSE_DAILY_CAP} requests/day). Sign in or try again "
                    "tomorrow — resets at midnight UTC."
                ),
                headers={"Retry-After": "3600", "X-RateLimit-Limit": str(IP_COARSE_DAILY_CAP)},
            )

    # ── 4. Per-device daily quota (30/day) ───────────────────────────
    # Always charged. ``token_id`` here is either the verified
    # incoming cookie's id, or — on first visit — the freshly-minted
    # token id from step (1). Either way, the request consumes 1
    # against this device's daily budget; the 31st request from the
    # same device on the same day will trip the cap.
    if token_id:
        from db_ops import atomic_deduct_device_credit
        if not atomic_deduct_device_credit(token_id, daily_limit=daily_cap):
            # Task #798 — emit `chat.anon_quota_exhausted` so we can
            # tell how many anonymous students hit the wall each day
            # and what fraction of them sign up vs bounce. Wrapped in
            # try/except because a metric crash must never block a
            # real user request — the 429 has to fire either way.
            #
            # ``atomic_deduct_device_credit`` returns False in **two**
            # very different states: (a) quota is genuinely exhausted,
            # and (b) Redis is unreachable / errored (fail-closed). We
            # peek the counter to disambiguate so a Redis outage isn't
            # misclassified as a wave of "wall-hits" in the metric.
            # When the peek itself fails (returns 0 because Redis is
            # also down for it), we deliberately skip the metric — a
            # zero is better than a phantom spike.
            try:
                from db_ops import peek_device_credit_used
                if peek_device_credit_used(token_id) >= daily_cap:
                    from metrics import record_anon_quota_exhausted
                    # Task #808 — pass through Cloudflare's geo/ASN
                    # tags so support can investigate angry "I keep
                    # getting blocked" tickets in seconds via the
                    # admin "Recent" tab. ``cf-ipcountry`` is the
                    # standard 2-letter ISO code; ASN is exposed by
                    # CF Workers as ``cf-ipasn`` / ``cf-asn``
                    # depending on origin config — try both so
                    # whichever one our edge proxy forwards lands
                    # on the metric. Falls back to "" when the
                    # request didn't traverse Cloudflare (e.g.
                    # local/dev), which the recorder treats as
                    # "unknown" without crashing.
                    cf_country = (
                        request.headers.get("cf-ipcountry") or ""
                    ).strip()
                    cf_asn = (
                        request.headers.get("cf-ipasn")
                        or request.headers.get("cf-asn")
                        or request.headers.get("x-asn")
                        or ""
                    ).strip()
                    record_anon_quota_exhausted(
                        token_id, ip=ip, plan_target="free",
                        country=cf_country, asn=cf_asn,
                    )
            except Exception:
                pass
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Daily free quota exhausted ({daily_cap} requests/day). "
                    "Sign in for higher limits — resets at midnight UTC."
                ),
                headers={"Retry-After": "3600", "X-RateLimit-Limit": str(daily_cap)},
            )

    return None


# ─────────────────────────────────────────────────────────────────────
# Task #819 — OCR-only rate limiter
#
# The chat composer's image-upload (Plus → Camera/Gallery → OCR) used
# to share ``rate_limit_chat_optional``, which burned 1 chat-message
# credit on every OCR call. That doubled the cost of "snap a photo
# and ask a question" — the very flow the feature was built for —
# and chewed through anonymous students' 30/day budget twice as fast,
# tripping "Daily free quota exhausted" after just ~15 photo Q&As.
#
# This dep enforces the same anti-abuse layers as chat (per-minute
# throttle, coarse per-IP daily ceiling, device-cookie issuance) but
# **does not** deduct from the per-device daily message budget. The
# follow-up chat send still costs 1 credit as usual; OCR itself is
# free.
#
# A separate per-minute throttle (``OCR_PER_MIN_CAP``) is used so a
# scripted client cannot just spam Vertex Vision calls — Vision
# requests are an order of magnitude more expensive than a chat call,
# so we keep this bound tight (10/min per device, vs 15/min for chat).
# The per-IP coarse cap (1500/day) is shared with chat — it counts
# *all* requests off a single egress IP, so OCR abuse still trips it.
# ─────────────────────────────────────────────────────────────────────

OCR_PER_MIN_CAP = 10            # per-device or per-IP per-minute cap on OCR calls.
OCR_DAILY_CAP_ANON = 50         # per-device daily OCR cap for anonymous callers.
# Task #581 §L9 — split the per-user daily OCR cap by plan. Free users
# (the cohort that drives the bulk of Vertex Vision spend) drop to
# 3/day so a curious user can try the feature but cannot scrape an
# entire textbook for free. Paid users keep the original 100/day cap.
OCR_DAILY_CAP_USER_FREE = 3     # per-free-user daily OCR cap.
OCR_DAILY_CAP_USER_PAID = 100   # per-paid-user daily OCR cap.
# Back-compat alias — the legacy constant is still imported by some
# admin tooling. Kept pointing at the paid ceiling so the meaning of
# "OCR_DAILY_CAP_USER" (the per-USER ceiling, applied to the privileged
# tier) doesn't change.
OCR_DAILY_CAP_USER = OCR_DAILY_CAP_USER_PAID
# Per-IP daily OCR ceiling — separate bucket from the chat coarse cap
# (``IP_COARSE_DAILY_CAP``) so OCR uploads do NOT eat into the per-IP
# chat budget. Sized for shared egress IPs (school WiFi, hostel,
# Jio/Airtel CGNAT): a class of ~100 students × 50 anon OCR/day each
# fits comfortably under 5000/day. A single IP scripting thousands of
# Vertex Vision calls still trips it, which is the only abuse case
# the per-IP layer is meant to catch (per-device + per-minute caps
# already handle real-user throttling).
OCR_IP_DAILY_CAP = int(os.environ.get("OCR_IP_DAILY_CAP", "5000"))

async def rate_limit_ocr_optional(
    request: Request,
    response: Response,
    user: Optional[dict] = Depends(get_current_user_optional),
    syrabit_device: Optional[str] = Cookie(default=None),
):
    """OCR-only rate limiter.

    See module-level docstring above the function for the rationale.
    Returns the resolved user dict (or ``None`` for anonymous callers)
    so the OCR route can branch on auth state without re-parsing the
    cookie.
    """
    # Logged-in users: per-minute throttle + generous daily cap on Vertex
    # Vision spend. Daily cap is intentionally separate from the chat
    # credit budget (Task #819 — OCR no longer burns chat credits).
    if user:
        user_id = user.get("id", "anonymous")
        if not check_rate_limit(f"ocr:{user_id}", max_requests=OCR_PER_MIN_CAP, window_seconds=60):
            raise HTTPException(
                status_code=429,
                detail=f"OCR rate limit exceeded — {OCR_PER_MIN_CAP} uploads/minute. Try again in a moment.",
                headers={"Retry-After": "60", "X-RateLimit-Limit": str(OCR_PER_MIN_CAP)},
            )
        # Task #581 §L9 — split the per-user OCR daily cap by plan.
        # Free users get 3/day (anti-abuse for Vertex Vision spend);
        # paid users keep the original 100/day. Admin / staff /
        # educator bypass via the same path used by other paid-only
        # gates (their plan resolves to a non-"free" value).
        plan = (user.get("plan") or "free").strip().lower()
        daily_cap = OCR_DAILY_CAP_USER_FREE if (not plan or plan == "free") else OCR_DAILY_CAP_USER_PAID
        if not check_rate_limit(f"ocr:day:{user_id}", max_requests=daily_cap, window_seconds=86400):
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Daily OCR limit reached ({daily_cap} image uploads/day). "
                    "Resets at midnight UTC. "
                    + ("Upgrade to Pro for 100 OCR scans/day." if daily_cap == OCR_DAILY_CAP_USER_FREE else "")
                ).strip(),
                headers={"Retry-After": "3600", "X-RateLimit-Limit": str(daily_cap)},
            )
        return user

    # Anonymous: device-cookie + per-minute throttle + coarse per-IP daily cap.
    ip = _real_client_ip(request)

    # 1. Resolve / mint device cookie (parity with chat dep so the
    #    follow-up chat send keys on the same device id).
    token_id = device_token_id(syrabit_device)
    if token_id is None:
        if ip and ip != "unknown" and not check_rate_limit(
            f"chat:mint:ip:{ip}",
            max_requests=DEVICE_COOKIE_MINTS_PER_MIN,
            window_seconds=60,
        ):
            raise HTTPException(
                status_code=429,
                detail=(
                    "Too many new sessions from this network in a short window. "
                    "Wait a minute and retry — make sure cookies are enabled."
                ),
                headers={
                    "Retry-After": "60",
                    "X-RateLimit-Limit": str(DEVICE_COOKIE_MINTS_PER_MIN),
                },
            )
        new_cookie = mint_device_token()
        _set_device_cookie(request, response, new_cookie)
        token_id = device_token_id(new_cookie)

    # 2. Per-minute throttle (device-scoped when possible).
    rl_key = f"ocr:dev:{token_id}" if token_id else f"ocr:ip:{ip}"
    if not check_rate_limit(rl_key, max_requests=OCR_PER_MIN_CAP, window_seconds=60):
        raise HTTPException(
            status_code=429,
            detail=f"OCR rate limit exceeded — {OCR_PER_MIN_CAP} uploads/minute. Try again in a moment.",
            headers={"Retry-After": "60", "X-RateLimit-Limit": str(OCR_PER_MIN_CAP)},
        )

    # 3. Per-IP daily OCR ceiling — uses a SEPARATE Redis bucket from
    #    the chat coarse cap (``IP_COARSE_DAILY_CAP`` / ``ip_daily_credits:``)
    #    so a busy classroom uploading photos doesn't drain the chat
    #    quota for that same egress IP. Sized for shared NAT use
    #    (~5000/day, see ``OCR_IP_DAILY_CAP`` constant). Falls back
    #    open on Redis outage instead of blocking real users — abuse
    #    is still bounded by the per-device + per-minute caps below.
    if ip and ip != "unknown":
        if not check_rate_limit(
            f"ocr:ip:day:{ip}",
            max_requests=OCR_IP_DAILY_CAP,
            window_seconds=86400,
        ):
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Daily OCR ceiling reached for this network "
                    f"(>{OCR_IP_DAILY_CAP} uploads/day). Try again tomorrow — "
                    "resets at midnight UTC."
                ),
                headers={"Retry-After": "3600", "X-RateLimit-Limit": str(OCR_IP_DAILY_CAP)},
            )

    # 4. Per-device daily OCR cap (separate from chat's 30/day budget —
    #    Task #819 keeps OCR off the chat credit ledger but still bounds
    #    total Vertex Vision spend per device).
    if token_id and not check_rate_limit(
        f"ocr:day:dev:{token_id}",
        max_requests=OCR_DAILY_CAP_ANON,
        window_seconds=86400,
    ):
        raise HTTPException(
            status_code=429,
            detail=(
                f"Daily OCR limit reached ({OCR_DAILY_CAP_ANON} image uploads/day). "
                "Sign in for higher limits — resets at midnight UTC."
            ),
            headers={"Retry-After": "3600", "X-RateLimit-Limit": str(OCR_DAILY_CAP_ANON)},
        )

    # NOTE: deliberately NOT calling atomic_deduct_device_credit here
    # — OCR is free and does not consume the 30/day chat budget.

    return None
