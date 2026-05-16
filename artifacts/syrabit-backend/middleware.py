"""Syrabit.ai — ASGI middleware classes."""
import os, re, time as _time_mod, logging, uuid, contextvars, hashlib, asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional
from starlette.types import ASGIApp, Receive, Scope, Send
from starlette.datastructures import MutableHeaders
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from config import SECURE_COOKIES, PLAN_LIMITS
from auth_deps import check_rate_limit, decode_token
from cache import _redis_get_session
from metrics import _metrics

logger = logging.getLogger(__name__)

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")


# ── BaseHTTPMiddleware "No response returned." race guard ───────────────────
#
# Starlette's BaseHTTPMiddleware wraps the inner ASGI app in an anyio
# TaskGroup. When a client (CF Worker, browser, Railway healthcheck) closes
# the connection between when `call_next` is awaited and when the inner
# handler tries to send the response, the inner task is cancelled and exits
# without producing a Response. BaseHTTPMiddleware then raises:
#
#     RuntimeError("No response returned.")
#
# This is a benign race — the client is already gone, so there is nothing
# to send a response to. But Starlette surfaces it as an unhandled
# exception, which pollutes the error log and (on some platforms) trips
# alerts. We wrap every `call_next` site through this helper, which
# downgrades the race to a single INFO line and returns a sentinel 499
# Response. The sentinel is never delivered (no client to deliver it to)
# but lets each middleware's bookkeeping (`response.headers`, status_code
# recording, finally blocks) execute without secondary exceptions.
#
# Real handler errors (anything other than this exact RuntimeError text)
# continue to propagate untouched.
async def _safe_call_next(call_next, request: StarletteRequest):
    from starlette.responses import Response
    try:
        return await call_next(request)
    except RuntimeError as exc:
        if str(exc) == "No response returned.":
            rid = getattr(request.state, "request_id", "") or "-"
            logger.info(
                f"[client-disconnect] {request.method} {request.url.path} "
                f"rid={rid} (BaseHTTPMiddleware race; client closed connection)"
            )
            return Response(status_code=499)
        raise

def _env_bool(key: str, default: bool = True) -> bool:
    val = os.environ.get(key, "").strip().lower()
    if not val:
        return default
    return val in ("1", "true", "yes", "on")

_SEC_HSTS = _env_bool("SEC_HSTS", True)
_SEC_XCTO = _env_bool("SEC_XCTO", True)
_SEC_XFRAME = _env_bool("SEC_XFRAME", True)
_SEC_REFERRER = _env_bool("SEC_REFERRER", True)
_SEC_PERM = _env_bool("SEC_PERM", True)
_SEC_CSP_REPORT_ONLY = _env_bool("SEC_CSP_REPORT_ONLY", False)

# Enhanced CSP with stricter controls for markdown-rendered content
# Implements defense-in-depth against XSS from user-generated content
_CSP_VALUE = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://www.googletagmanager.com https://accounts.google.com https://apis.google.com https://widget.trustpilot.com https://challenges.cloudflare.com; "
    "style-src 'self' 'unsafe-inline' https://accounts.google.com https://widget.trustpilot.com; "
    "img-src 'self' data: https:; "
    "font-src 'self' data:; "
    "connect-src 'self' https:; "
    "frame-src https://accounts.google.com https://widget.trustpilot.com https://challenges.cloudflare.com; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "object-src 'none'; "
    "upgrade-insecure-requests; "
    "report-uri /api/security/csp-report"
)


_ORIGIN_SHARED_SECRET = os.environ.get("ORIGIN_SHARED_SECRET", "").strip()
_ORIGIN_AUTH_HEADER = os.environ.get("ORIGIN_SHARED_SECRET_HEADER", "X-Origin-Auth").strip() or "X-Origin-Auth"
# Paths that must remain reachable without the shared-secret header so Cloud
# Run's own startup/liveness probes keep working. Kept deliberately tiny:
# /docs and /openapi.json used to be open during early bring-up but were
# removed so the *.run.app URL exposes nothing more than health to the
# unauthenticated public — every other endpoint requires the edge-injected
# header when ORIGIN_SHARED_SECRET is set.
_ORIGIN_AUTH_OPEN_PATHS = (
    "/api/health",
    "/api/livez",   # Task #848 — Railway liveness probe, no I/O
    "/api/readyz",  # Task #848 — load-balancer readiness probe
    "/api/ready",   # Legacy readiness — kept open for back-compat with
                    # any external monitor still pointing at the old path
                    # (Task #848 follow-up review).
    "/health",
    # Library bundle is read-only public content (board/class/subject/chapter
    # index). Cloudflare Pages prerender and external CDN revalidation must
    # reach it without the edge-injected secret, because the CF Pages build
    # runner and Replit deploy builder are not behind our edge worker.
    # It is already in _BOT_OPEN_PREFIXES (bot rate-limit bypass), so
    # opening it here is consistent with its existing public-access intent.
    "/api/content/library-bundle",
)


class OriginSharedSecretMiddleware:
    """Reject requests that did not flow through the Cloudflare edge worker.

    When ``ORIGIN_SHARED_SECRET`` is set in the environment, every non-open
    request must carry a matching header (default ``X-Origin-Auth``). The
    edge worker injects this header on every backend fetch. This is the
    application-layer equivalent of authenticated origin pull and is what
    keeps the Cloud Run URL from being directly reachable by the public
    internet (in addition to the Cloud Run ingress allowlist).

    Disabled (no enforcement, no overhead) when the env var is empty so the
    backend keeps working unchanged on the legacy Railway origin until the
    cutover is complete.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http" or not _ORIGIN_SHARED_SECRET:
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "GET")
        if method == "OPTIONS":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        # Architect review 2026-05-06: use segment-bounded matching so a
        # future route like ``/api/healthz`` or ``/api/ready-bypass`` does
        # not silently inherit the open-path bypass from ``/api/health`` /
        # ``/api/ready`` just because of a prefix collision.
        if any(path == p or path.startswith(p + "/") for p in _ORIGIN_AUTH_OPEN_PATHS):
            await self.app(scope, receive, send)
            return
        # Header lookup: scope["headers"] is a list of (bytes, bytes) tuples.
        wanted = _ORIGIN_AUTH_HEADER.lower().encode("latin-1")
        provided = b""
        for k, v in scope.get("headers", []):
            if k == wanted:
                provided = v
                break
        if provided and provided.decode("latin-1", "ignore") == _ORIGIN_SHARED_SECRET:
            await self.app(scope, receive, send)
            return
        from fastapi.responses import JSONResponse
        resp = JSONResponse(
            status_code=403,
            content={"detail": "Direct origin access denied — must traverse the edge worker."},
        )
        await resp(scope, receive, send)


_RE_SMAXAGE = re.compile(r"s-maxage=(\d+)")
_RE_SWR      = re.compile(r"stale-while-revalidate=(\d+)")
_RE_SIE      = re.compile(r"stale-if-error=(\d+)")
_RE_UA_VARY  = re.compile(r",?\s*User-Agent\b", re.IGNORECASE)
_COMPRESSIBLE_CTYPES = ("text/", "application/json", "application/javascript", "application/xml")


class CfPerformanceMiddleware:
    """
    Injects Cloudflare-specific performance headers into every HTTP response.

    What it does — without touching your route handlers:

    1. ``stale-if-error=86400`` — appended to every public Cache-Control header
       so CF serves stale content for 24 h if the origin goes down.

    2. ``Cloudflare-CDN-Cache-Control`` — mirrors the public Cache-Control but
       promotes ``s-maxage`` to ``max-age`` so the CF edge obeys a different
       (usually longer) TTL than the browser independently.

    3. ``Vary: Accept-Encoding`` — appended to compressible content-types so
       CF correctly deduplicates gzip / brotli cache buckets.

    4. Bad ``Vary: User-Agent`` — stripped out; it forces CF to store a
       separate cache copy per UA, fragmenting the cache by billions of buckets.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def _send_with_cf_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                cc = headers.get("cache-control", "")
                ct = headers.get("content-type", "")

                if "public" in cc and "no-store" not in cc:
                    # ── 1. stale-if-error ─────────────────────────────────
                    if "stale-if-error" not in cc:
                        cc = cc.rstrip(", ") + ", stale-if-error=86400"
                        headers["cache-control"] = cc

                    # ── 2. Cloudflare-CDN-Cache-Control ───────────────────
                    if not headers.get("cloudflare-cdn-cache-control"):
                        sm = _RE_SMAXAGE.search(cc)
                        swr = _RE_SWR.search(cc)
                        sie = _RE_SIE.search(cc)
                        if sm:
                            edge_ttl = sm.group(1)
                            cf_parts = [f"public, max-age={edge_ttl}"]
                            if swr:
                                cf_parts.append(f"stale-while-revalidate={swr.group(1)}")
                            if sie:
                                cf_parts.append(f"stale-if-error={sie.group(1)}")
                            headers.append(
                                "Cloudflare-CDN-Cache-Control", ", ".join(cf_parts)
                            )

                    # ── 3. Vary: Accept-Encoding ──────────────────────────
                    if any(ct.startswith(t) for t in _COMPRESSIBLE_CTYPES):
                        vary = headers.get("vary", "")
                        if "Accept-Encoding" not in vary:
                            headers["vary"] = (
                                f"{vary}, Accept-Encoding" if vary else "Accept-Encoding"
                            )

                # ── 4. Strip bad Vary: User-Agent ─────────────────────────
                vary = headers.get("vary", "")
                if vary and "User-Agent" in vary:
                    cleaned = _RE_UA_VARY.sub("", vary).strip(", ")
                    if cleaned:
                        headers["vary"] = cleaned
                    else:
                        del headers["vary"]

            await send(message)

        await self.app(scope, receive, _send_with_cf_headers)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        if method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        async def send_with_security_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if _SEC_XCTO:
                    headers.append("X-Content-Type-Options", "nosniff")
                if _SEC_XFRAME:
                    headers.append("X-Frame-Options", "SAMEORIGIN")
                if _SEC_REFERRER:
                    headers.append("Referrer-Policy", "strict-origin-when-cross-origin")
                if _SEC_PERM:
                    headers.append("Permissions-Policy", "camera=(), microphone=(), geolocation=(), identity-credentials-get=(self https://accounts.google.com)")
                headers.append("X-XSS-Protection", "1; mode=block")
                if _SEC_HSTS and SECURE_COOKIES:
                    headers.append("Strict-Transport-Security", "max-age=63072000; includeSubDomains; preload")
                if _SEC_CSP_REPORT_ONLY:
                    headers.append("Content-Security-Policy-Report-Only", _CSP_VALUE)
                else:
                    headers.append("Content-Security-Policy", _CSP_VALUE)
                ct = headers.get("content-type", "")
                if "text/html" in ct:
                    headers.append("Content-Language", "en-IN")
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


# ── DeviceCookieMiddleware (Task #793) ────────────────────────────────
# The chat rate-limit dependency (``auth_deps.rate_limit_chat_optional``)
# may need to mint a fresh ``syrabit_device`` cookie for first-visit
# anonymous traffic. It tries to do so by calling ``set_cookie`` on
# the ``Response`` parameter FastAPI injects into the dependency.
#
# That works fine when the route handler returns a JSON-serialisable
# value (FastAPI builds the response from the injected ``Response``
# object), but it does **not** work when the route handler returns a
# concrete ``Response`` instance of its own — the most common case
# here is ``StreamingResponse`` on ``/ai/chat/stream``, which is the
# user-facing chat path. In that case FastAPI uses the route's
# response and discards the dependency-injected one, so the freshly
# minted device cookie is silently dropped on the floor and the
# client never persists it. Effective behaviour collapses back to
# coarse per-IP enforcement, which defeats the entire point of
# Task #793.
#
# This middleware is the safety net: when the dependency mints a
# cookie it also stashes the value on ``request.state.device_cookie_to_set``;
# we read that back on the way out and append the ``Set-Cookie``
# header to whichever response the handler ultimately produced — but
# only if no ``syrabit_device`` cookie is already on the response (so
# we don't double-set when FastAPI's normal merge actually did work,
# e.g. on the JSON ``/ai/chat`` path).
class DeviceCookieMiddleware:
    """Re-apply the freshly-minted device cookie to whatever response
    the route handler returned. See module-level comment above for
    the failure mode this guards against.
    """
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # Lazy import so this module stays importable even when
        # device_token / auth_deps haven't been initialised yet (e.g.
        # in test bootstrap).
        from device_token import DEVICE_COOKIE_NAME
        from auth_deps import _set_device_cookie  # type: ignore
        from starlette.responses import Response as _Response

        # Build a real Starlette request once so we can read its
        # ``state`` after the inner app has run. ``state`` is a plain
        # attribute namespace shared between the request and any
        # ASGI-level code that pulls a Request out of the scope.
        request = StarletteRequest(scope, receive)

        async def send_with_device_cookie(message):
            if message["type"] == "http.response.start":
                pending = getattr(request.state, "device_cookie_to_set", None)
                if pending:
                    headers = MutableHeaders(scope=message)
                    # Skip if the route handler already set the same
                    # cookie (e.g. JSON /ai/chat where the injected
                    # Response did get merged in normally) so we
                    # never double-emit Set-Cookie.
                    already = any(
                        v.startswith(f"{DEVICE_COOKIE_NAME}=")
                        for v in headers.getlist("set-cookie")
                    )
                    if not already:
                        # Use a throwaway Response purely to format
                        # the Set-Cookie value with the same flags
                        # (HttpOnly / Secure / SameSite / max-age /
                        # domain) the dependency uses, then transplant
                        # it onto the live outgoing headers.
                        scratch = _Response()
                        try:
                            _set_device_cookie(request, scratch, pending)
                            for cookie_value in scratch.headers.getlist("set-cookie"):
                                headers.append("set-cookie", cookie_value)
                        except Exception as exc:  # pragma: no cover — defensive
                            logger.warning(
                                f"DeviceCookieMiddleware: failed to apply pending cookie: {exc}"
                            )
            await send(message)

        await self.app(scope, receive, send_with_device_cookie)


from utils import _SEARCH_BOT_UA_RE, _ABUSIVE_SCRAPER_UA_RE, _TRAINING_SCRAPER_UA_RE, verify_bot_ip

_BOT_RATE_LIMIT = 1200

_blocked_ip_hashes: set[str] = set()
_blocked_ip_lock = __import__("threading").Lock()


async def _refresh_blocked_ip_cache():
    try:
        from deps import db, is_mongo_available
        if await is_mongo_available():
            docs = await db.blocked_ips.find({}, {"ip_hash": 1, "expires_at": 1, "_id": 0}).to_list(10000)
            from datetime import datetime as _dt, timezone as _tz
            now = _dt.now(_tz.utc)
            new_set = set()
            for d in docs:
                if not d.get("ip_hash"):
                    continue
                ea = d.get("expires_at")
                if ea is not None and ea <= now:
                    continue
                new_set.add(d["ip_hash"])
            with _blocked_ip_lock:
                changed = new_set != _blocked_ip_hashes
                _blocked_ip_hashes.clear()
                _blocked_ip_hashes.update(new_set)
            if changed:
                logger.info(f"Blocked IP cache updated: {len(new_set)} entries")
    except Exception as e:
        logger.debug(f"blocked IP cache refresh failed: {e}")


_BLOCKED_IP_REFRESH_INTERVAL = 10
_EXPIRED_IP_CLEANUP_COUNTER = 0

async def _cleanup_expired_blocked_ips():
    try:
        from deps import db, is_mongo_available
        if await is_mongo_available():
            from datetime import datetime as _dt, timezone as _tz
            now = _dt.now(_tz.utc)
            expired_docs = await db.blocked_ips.find(
                {"expires_at": {"$lte": now}, "auto_blocked": True},
                {"ip_hash": 1, "_id": 1},
            ).to_list(500)
            if expired_docs:
                ids = [d["_id"] for d in expired_docs]
                result = await db.blocked_ips.delete_many({"_id": {"$in": ids}})
                count = result.deleted_count
                if count > 0:
                    logger.info(f"Cleaned up {count} expired auto-blocked IP(s)")
                    await _refresh_blocked_ip_cache()
                    try:
                        from metrics import _dispatch_alert
                        hashes = ", ".join(d.get("ip_hash", "?")[:12] + "..." for d in expired_docs[:5])
                        suffix = f" and {count - 5} more" if count > 5 else ""
                        await _dispatch_alert(
                            "auto_block_expired",
                            "Auto-blocked IPs expired",
                            f"{count} auto-blocked IP(s) have been automatically unblocked after their cooldown expired: {hashes}{suffix}.",
                            {"count": count, "ip_hashes": [d.get("ip_hash") for d in expired_docs[:10]]},
                        )
                    except Exception as e:
                        logger.debug(f"auto-block expiry alert dispatch failed: {e}")
    except Exception as e:
        logger.debug(f"expired IP cleanup failed: {e}")

async def _blocked_ip_refresh_loop():
    global _EXPIRED_IP_CLEANUP_COUNTER
    while True:
        try:
            await _refresh_blocked_ip_cache()
            _EXPIRED_IP_CLEANUP_COUNTER += 1
            if _EXPIRED_IP_CLEANUP_COUNTER >= 30:
                _EXPIRED_IP_CLEANUP_COUNTER = 0
                await _cleanup_expired_blocked_ips()
        except Exception as e:
            logger.debug(f"blocked IP refresh loop error: {e}")
        await asyncio.sleep(_BLOCKED_IP_REFRESH_INTERVAL)


async def _init_blocked_ip_cache():
    await _refresh_blocked_ip_cache()
    asyncio.create_task(_blocked_ip_refresh_loop())


def _is_ip_blocked(ip_hash: str) -> bool:
    with _blocked_ip_lock:
        return ip_hash in _blocked_ip_hashes

_spoof_counter_lock = __import__("threading").Lock()
_spoof_minute_counts: dict[str, int] = {}
_SPOOF_ALERT_THRESHOLD = 50

def _get_auto_block_threshold() -> int:
    try:
        from metrics import _ALERT_THRESHOLDS
        return int(_ALERT_THRESHOLDS.get("auto_block_threshold", 100))
    except Exception:
        return 100

async def _check_auto_block(db, ip_hash: str, now: datetime):
    threshold = _get_auto_block_threshold()
    if threshold <= 0:
        return
    if _is_ip_blocked(ip_hash):
        return
    try:
        cutoff = now - timedelta(hours=24)
        count = await db.bot_spoof_attempts.count_documents({
            "ip_hash": ip_hash,
            "timestamp": {"$gte": cutoff},
        })
        if count >= threshold:
            existing = await db.blocked_ips.find_one({"ip_hash": ip_hash})
            if existing:
                return
            doc = {
                "ip_hash": ip_hash,
                "reason": "auto_threshold",
                "blocked_at": now,
                "blocked_by": "system/auto-block",
                "auto_blocked": True,
                "threshold_at_block": threshold,
                "spoof_count_24h": count,
            }
            try:
                from metrics import _ALERT_THRESHOLDS
                expiry_h = float(_ALERT_THRESHOLDS.get("auto_block_expiry_hours", 168))
            except Exception:
                expiry_h = 168
            if expiry_h > 0:
                doc["expires_at"] = now + timedelta(hours=expiry_h)
            await db.blocked_ips.insert_one(doc)
            await _refresh_blocked_ip_cache()
            logger.warning(
                f"AUTO_BLOCK ip_hash={ip_hash} spoof_count_24h={count} threshold={threshold}"
            )
            try:
                from metrics import _dispatch_alert
                await _dispatch_alert(
                    "auto_block_ip",
                    "IP Auto-Blocked",
                    f"IP {ip_hash[:12]}... was automatically blocked after {count} spoofing attempts in 24h (threshold: {threshold}).",
                    {"ip_hash": ip_hash, "spoof_count_24h": count, "threshold": threshold},
                )
            except Exception as e:
                logger.debug(f"auto-block alert dispatch failed: {e}")
    except Exception as e:
        logger.debug(f"auto-block check failed: {e}")


_BOT_OPEN_PREFIXES = (
    "/api/content/library-bundle",
    "/api/content/boards",
    "/api/content/classes",
    "/api/content/streams",
    "/api/content/subjects",
    "/api/content/chapters/",
    "/api/content/chapter-by-slug/",
    "/api/content/chapter/",
    "/api/content/search",
    "/api/seo/",
)


class GlobalRateLimitMiddleware(BaseHTTPMiddleware):
    """Plan-aware IP rate limiting for all /api routes + request tracking.
    Plan is read from JWT claim (refreshed on login, plan change invalidates session).
    JWT is decoded once and reused for both rate-limiting and metrics."""
    _RATE_LIMIT_EXEMPT_PREFIXES = (
        "/api/auth/me",
        "/api/analytics/",
        "/api/health",
        "/api/livez",   # Task #848 — never rate-limit the Railway liveness probe
        "/api/readyz",  # Task #848 — never rate-limit the readiness probe
        "/api/ready",   # Legacy readiness — same treatment as /api/readyz
    )

    async def dispatch(self, request: StarletteRequest, call_next):
        path = request.url.path
        if not path.startswith("/api/"):
            return await _safe_call_next(call_next, request)

        rid = uuid.uuid4().hex[:12]
        request_id_var.set(rid)
        request.state.request_id = rid
        request.state.start_time = _time_mod.time()

        ua = request.headers.get("user-agent", "")
        ua_claims_bot = bool(ua and _SEARCH_BOT_UA_RE.search(ua) and not _ABUSIVE_SCRAPER_UA_RE.search(ua) and not _TRAINING_SCRAPER_UA_RE.search(ua))
        request.state.is_search_bot = False

        cf_ip = request.headers.get("cf-connecting-ip", "")
        xff = request.headers.get("x-forwarded-for", "")
        client_ip = cf_ip or (xff.split(",")[0].strip() if xff else "") or (request.client.host if request.client else "unknown")

        ip_hash_check = _hash_ip_stable(client_ip)
        if _is_ip_blocked(ip_hash_check):
            from fastapi.responses import JSONResponse
            _metrics.record_request(path, 403)
            logger.warning(f"BLOCKED_IP ip_hash={ip_hash_check} path={path} rid={rid}")
            return JSONResponse(
                status_code=403,
                content={"detail": "Access denied."},
                headers={"X-Request-Id": rid},
            )

        exempt = any(path.startswith(p) for p in self._RATE_LIMIT_EXEMPT_PREFIXES)
        if exempt:
            _metrics.inc_active()
            try:
                response = await _safe_call_next(call_next, request)
                _metrics.record_request(path, response.status_code)
                response.headers["X-Request-Id"] = rid
                # Task #944 — feed the unified-log explorer with this
                # request even though it bypassed the rate limiter; a
                # silent /api/health flap is exactly the kind of thing
                # the explorer must surface.
                _record_unified_log(request, response, rid)
                return response
            finally:
                _metrics.dec_active()

        user_id = None
        ip_limit = PLAN_LIMITS["free"]["req_per_min_ip"]
        is_admin_request = False
        try:
            token = None
            auth = request.headers.get("authorization", "")
            if auth.startswith("Bearer "):
                token = auth[7:]
            else:
                token = request.cookies.get("syrabit_session")
            if token:
                payload = decode_token(token)
                user_id = payload.get("sub") or payload.get("user_id")
                user_plan = payload.get("plan", "free")
                if user_id:
                    cached_user = _redis_get_session(user_id)
                    if cached_user:
                        user_plan = cached_user.get("plan", user_plan)
                plan_cfg = PLAN_LIMITS.get(user_plan, PLAN_LIMITS["free"])
                ip_limit = plan_cfg["req_per_min_ip"]
                # Admin requests bypass the IP-based rate limit. The admin
                # dashboard fan-outs (~30+ panels in parallel on first
                # load, plus the BreakGlassBanner's /admin/diagnostics
                # poll every 60s) routinely exceed the per-IP free-plan
                # cap of 60/min from a single browser session, which used
                # to 429-storm the whole UI for the first ~minute after
                # login and silently mask break-glass-mode visibility
                # during a real Cloudflare Access incident — the exact
                # moment the diagnostics endpoint must remain reachable.
                # The admin endpoints themselves are gated by
                # ``Depends(get_admin_user)`` (which re-validates the
                # admin role against the DB / cache), so the IP cap was
                # never the real authorization boundary for them.
                if payload.get("is_admin") or payload.get("role") == "admin":
                    is_admin_request = True
        except Exception:
            pass

        is_legit_bot = False
        if ua_claims_bot:
            try:
                is_legit_bot = await asyncio.to_thread(verify_bot_ip, client_ip, ua)
            except Exception:
                is_legit_bot = False
        request.state.is_search_bot = is_legit_bot
        if ua_claims_bot and not is_legit_bot:
            ip_hash = _hash_ip_stable(client_ip)
            bot_match = _SEARCH_BOT_UA_RE.search(ua)
            claimed_bot = bot_match.group(0).lower() if bot_match else "unknown"
            logger.warning(
                f"SPOOFED_BOT ip_hash={ip_hash} claimed={claimed_bot} "
                f"ua=\"{ua[:150]}\" path={path} rid={rid}"
            )
            _metrics.record_spoof(claimed_bot)
            minute_key = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
            with _spoof_counter_lock:
                _spoof_minute_counts[minute_key] = _spoof_minute_counts.get(minute_key, 0) + 1
                count = _spoof_minute_counts[minute_key]
                if len(_spoof_minute_counts) > 10:
                    stale = [k for k in _spoof_minute_counts if k < minute_key]
                    for k in stale:
                        _spoof_minute_counts.pop(k, None)
            if count == _SPOOF_ALERT_THRESHOLD:
                logger.error(
                    f"SPOOF_ALERT threshold={_SPOOF_ALERT_THRESHOLD}/min reached | "
                    f"minute={minute_key}"
                )
            async def _bg_persist_spoof():
                try:
                    from deps import db, is_mongo_available
                    if await is_mongo_available():
                        _now = datetime.now(timezone.utc)
                        await db.bot_spoof_attempts.insert_one({
                            "ip_hash": ip_hash,
                            "claimed_bot": claimed_bot,
                            "user_agent": ua[:500],
                            "path": path,
                            "timestamp": _now,
                            "date": _now.strftime("%Y-%m-%d"),
                            "request_id": rid,
                        })
                        await _check_auto_block(db, ip_hash, _now)
                except Exception as e:
                    logger.debug(f"spoof persist failed: {e}")
            asyncio.create_task(_bg_persist_spoof())
        if not is_legit_bot and not is_admin_request:
            if not check_rate_limit(f"ip:{client_ip}", max_requests=ip_limit, window_seconds=60):
                from fastapi.responses import JSONResponse
                _metrics.record_request(path, 429)
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Too many requests — please slow down."},
                    headers={"Retry-After": "60", "X-RateLimit-Limit": str(ip_limit), "X-Request-Id": rid}
                )

        _metrics.inc_active()
        try:
            response = await _safe_call_next(call_next, request)
            _metrics.record_request(path, response.status_code, user_id)
            response.headers["X-Request-Id"] = rid
            if is_legit_bot and any(path.startswith(p) for p in _BOT_OPEN_PREFIXES):
                response.headers["X-Robots-Tag"] = "noarchive"
                response.headers["X-Content-Source"] = "Syrabit Browser"
                response.headers["X-Attribution"] = "Source: Syrabit Browser - https://syrabit.ai"
                if "Cache-Control" not in response.headers:
                    response.headers["Cache-Control"] = "public, max-age=3600, s-maxage=86400"
            if is_legit_bot:
                response.headers["X-Bot-Served"] = "true"
            elapsed = _time_mod.time() - request.state.start_time
            if elapsed > 1.0:
                logger.info(f"[SLOW] {path} took {elapsed*1000:.0f}ms | rid={rid} uid={user_id or 'anon'}")
            # Task #944 — sample this request into the unified-log
            # explorer; sampling + 4xx-keep handled by the shipper.
            _record_unified_log(request, response, rid, user_id=user_id,
                                is_admin=is_admin_request)
            return response
        finally:
            _metrics.dec_active()


# Task #944 — unified-log shipper hook. Imported lazily so a circular
# import or a missing module never bricks the request path. Any failure
# inside the shipper is swallowed.
def _record_unified_log(request, response, rid: str, *,
                        user_id: Optional[str] = None,
                        is_admin: bool = False) -> None:
    try:
        from unified_logs_dao import get_backend_shipper
        start = getattr(request.state, "start_time", None)
        duration_ms = None
        if start is not None:
            try:
                duration_ms = int((_time_mod.time() - float(start)) * 1000)
            except Exception:
                duration_ms = None
        get_backend_shipper().record_request(
            method=request.method,
            route=request.url.path,
            status=getattr(response, "status_code", None),
            duration_ms=duration_ms,
            request_id=rid,
            user_agent=(request.headers.get("user-agent") or "")[:200] or None,
            extra={"uid": user_id, "admin": bool(is_admin)} if (user_id or is_admin) else None,
        )
    except Exception:
        pass


_STATIC_ASSET_RE = re.compile(
    r"\.(js|css|png|jpg|jpeg|gif|svg|ico|woff2?|ttf|eot|map|webp|avif|mp4|webm)$",
    re.IGNORECASE,
)

_SKIP_TRACKING_PREFIXES = (
    "/api/auth/", "/api/admin/", "/api/ai/", "/api/analytics/",
    "/api/health", "/api/livez", "/api/readyz", "/api/ready",  # Task #848 + legacy
    "/api/billing/",
    "/static/", "/assets/", "/icons/", "/fonts/",
    "/health", "/docs", "/openapi.json", "/robots.txt", "/sitemap",
    "/__mockup", "/favicon",
)

_SERVER_BOT_RE = re.compile(
    _SEARCH_BOT_UA_RE.pattern + r"|" + _TRAINING_SCRAPER_UA_RE.pattern + r"|" + _ABUSIVE_SCRAPER_UA_RE.pattern,
    re.IGNORECASE,
)

_IP_HASH_SALT = os.environ.get("IP_HASH_SALT", "syrabit-ss-tracking-2026")


def _hash_ip_daily(ip: str, date: str) -> str:
    raw = f"{_IP_HASH_SALT}:{ip}:{date}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _hash_ip_stable(ip: str) -> str:
    raw = f"{_IP_HASH_SALT}:{ip}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class ServerSideTrackingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next):
        path = request.url.path

        if (
            any(path.startswith(p) for p in _SKIP_TRACKING_PREFIXES)
            or _STATIC_ASSET_RE.search(path)
        ):
            return await _safe_call_next(call_next, request)

        ua = request.headers.get("user-agent", "")
        bot_match = _SERVER_BOT_RE.search(ua) if ua else None
        is_bot = bool(bot_match)
        # Use the canonical classifier so e.g. "Googlebot-Image/1.0" is
        # stored as "Googlebot-Image" (not just "googlebot"), and so AI
        # crawlers (GPTBot, PerplexityBot, ClaudeBot, OAI-SearchBot,
        # Google-Extended, Applebot-Extended, …) get readable names in
        # the admin dashboard's top_bots / per_bot_pages aggregations.
        # Fall back to the raw regex match for UAs that match the bot
        # regex but aren't in the canonical patterns list.
        if bot_match:
            try:
                from cf_bot_report import _classify_ua as _classify_bot_ua
                _canonical = _classify_bot_ua(ua)
            except Exception:
                _canonical = None
            bot_name = _canonical or bot_match.group(0)
        else:
            bot_name = ""
        cf_connecting_ip = request.headers.get("cf-connecting-ip", "")
        x_forwarded = request.headers.get("x-forwarded-for", "")
        client_ip = cf_connecting_ip or (x_forwarded.split(",")[0].strip() if x_forwarded else "") or (request.client.host if request.client else "unknown")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        now_iso = datetime.now(timezone.utc).isoformat()
        ip_hash_daily = _hash_ip_daily(client_ip, today)
        ip_hash_stable = _hash_ip_stable(client_ip)
        cf_country = request.headers.get("cf-ipcountry", "")

        response = await _safe_call_next(call_next, request)

        import asyncio
        async def _bg_track():
            try:
                from deps import db, is_mongo_available
                if await is_mongo_available():
                    await db.server_hits.insert_one({
                        "path": path,
                        "ip_hash": ip_hash_daily,
                        "ip_hash_stable": ip_hash_stable,
                        "user_agent": ua[:500],
                        "is_bot": is_bot,
                        "bot_name": bot_name,
                        "date": today,
                        "timestamp": now_iso,
                        "status_code": response.status_code,
                        "country": cf_country,
                    })
            except Exception as e:
                logger.debug(f"server-side tracking failed: {e}")
        asyncio.create_task(_bg_track())

        return response


# ── MtlsClientCertMiddleware (Task #120) ──────────────────────────────────────
#
# Application-layer defence-in-depth complement to Railway's TLS-level mTLS
# enforcement.  When Railway requires the Cloudflare-issued client certificate
# at the TLS handshake (configured via Railway dashboard → Service → Settings →
# mTLS), connections without a valid cert are rejected before HTTP is reached.
# This middleware is the belt-and-suspenders layer: if the TLS-level check ever
# lapses (misconfiguration, cert rotation gap, Railway plan downgrade), the
# backend still rejects requests that are missing a cryptographic proof that
# the CF Worker sent this request WITH the mTLS cert bound.
#
# How it works:
#   1. cloudflare-phase6-apply.js issues an mTLS client certificate.
#   2. The CF Worker's addMtlsActiveHeader() computes
#        HMAC-SHA256("mtls-active", BACKEND_ORIGIN_SECRET)
#      and injects it as X-Cf-Mtls-Active header, ONLY when env.MTLS_CERT is
#      bound (i.e. the cert has been provisioned and wrangler deploy has run).
#   3. This middleware validates the HMAC using ORIGIN_SHARED_SECRET (the same
#      secret used by OriginSharedSecretMiddleware).
#
# Security properties:
#   • Non-spoofable: BACKEND_ORIGIN_SECRET must be known to forge the HMAC.
#   • Bound to cert deployment: X-Cf-Mtls-Active is set ONLY when MTLS_CERT is
#     bound in the CF Worker, so requests succeed only when the cert is active.
#   • BACKEND_ORIGIN_SECRET rotation simultaneously invalidates both headers
#     (X-Origin-Auth and X-Cf-Mtls-Active), so there is no extra key to manage.
#
# Activation:
#   Set ENFORCE_MTLS=true in the Railway service environment.
#   The middleware is a no-op when ENFORCE_MTLS is absent or ORIGIN_SHARED_SECRET
#   is not set, so it cannot break deployments during the rollout window.
#
# Exempt paths:
#   /api/livez, /api/readyz, /api/ready — Railway's own infrastructure health
#   probes reach these from Railway's internal subnet without the CF HMAC.
#   /api/health is intentionally NOT exempt: it is the path used by external
#   bypass probes (nightly-smoke.js 6a-iv) so the enforcement has real teeth.

import hmac as _hmac_mod
import hashlib as _hashlib_mod

_ENFORCE_MTLS = _env_bool("ENFORCE_MTLS", False)
_MTLS_ACTIVE_HEADER = b"x-cf-mtls-active"

# Railway internal liveness/readiness probes — these never carry the CF HMAC
# because they originate from Railway's own infrastructure, not from the CF edge.
# /api/health is intentionally excluded so the bypass-probe assertion works.
_MTLS_PROBE_PATHS = (
    "/api/livez",
    "/api/readyz",
    "/api/ready",
)

def _compute_mtls_hmac(secret: str) -> str:
    """Compute HMAC-SHA256("mtls-active", secret) — same algorithm as the CF Worker."""
    return _hmac_mod.new(secret.encode("utf-8"), b"mtls-active", _hashlib_mod.sha256).hexdigest()


# Task #135 — module-level misconfiguration flag.
# Set to True at import time when ENFORCE_MTLS=true but ORIGIN_SHARED_SECRET is
# absent.  Exported so /api/readyz can include it in its critical-dependency
# check and return 503 rather than silently advertising "ready" while the
# origin is actually unprotected.
MTLS_MISCONFIGURED: bool = _ENFORCE_MTLS and not bool(_ORIGIN_SHARED_SECRET)


class MtlsClientCertMiddleware:
    """Reject requests that did not flow through the Cloudflare edge with the mTLS cert.

    When ``ENFORCE_MTLS=true`` is set AND ``ORIGIN_SHARED_SECRET`` is configured,
    every non-probe request must carry a matching ``X-Cf-Mtls-Active`` header
    containing HMAC-SHA256("mtls-active", ORIGIN_SHARED_SECRET).  The CF Worker
    computes and injects this header automatically when ``MTLS_CERT`` is bound.

    The HMAC is non-spoofable without ORIGIN_SHARED_SECRET and is only emitted
    by the CF Worker when the mTLS cert binding (env.MTLS_CERT) is present,
    ensuring that both the cert is provisioned AND the request came from the
    CF edge.

    Disabled (no enforcement) when ENFORCE_MTLS is not set or ORIGIN_SHARED_SECRET
    is empty, so existing deployments keep working during the rollout window.

    Misconfiguration (ENFORCE_MTLS=true but ORIGIN_SHARED_SECRET absent) sets
    the module-level ``MTLS_MISCONFIGURED`` flag and logs at ERROR level so
    log-based alerting catches it; /api/readyz also returns 503 in this state.
    """

    def __init__(self, app: ASGIApp):
        self.app = app
        self._expected_hmac: str | None = None
        if _ENFORCE_MTLS and _ORIGIN_SHARED_SECRET:
            self._expected_hmac = _compute_mtls_hmac(_ORIGIN_SHARED_SECRET)
            logger.info(
                "MtlsClientCertMiddleware: ACTIVE — rejecting requests without "
                "X-Cf-Mtls-Active HMAC proof from the CF Worker"
            )
        elif _ENFORCE_MTLS and not _ORIGIN_SHARED_SECRET:
            # Use ERROR (not WARNING) so log-based alerting rules fire.
            # MTLS_MISCONFIGURED is also True — /api/readyz will return 503.
            logger.error(
                "MtlsClientCertMiddleware: MISCONFIGURED — ENFORCE_MTLS=true but "
                "ORIGIN_SHARED_SECRET is not set. Enforcement is INACTIVE and the "
                "origin is UNPROTECTED. Set ORIGIN_SHARED_SECRET or unset "
                "ENFORCE_MTLS to suppress this alert."
            )
        else:
            logger.info("MtlsClientCertMiddleware: inactive (ENFORCE_MTLS not set)")

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http" or not self._expected_hmac:
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "GET")
        if method == "OPTIONS":
            # CORS preflight bypass — intentional and safe.
            #
            # Browsers always send an OPTIONS preflight before a cross-origin
            # request (e.g. the Syrabit React SPA calling /api/* from a
            # different origin).  The Cloudflare edge worker does NOT attach
            # the X-Cf-Mtls-Active HMAC on OPTIONS requests because the mTLS
            # handshake completes before the preflight is forwarded; injecting
            # the HMAC into the preflight would require a second Worker round-
            # trip and is not standard practice.
            #
            # Safety: OPTIONS responses are controlled by CORSMiddleware (or
            # Starlette's default 405 handler when CORS is not configured).
            # Neither path returns application data, so bypassing the HMAC
            # check here does not expose any sensitive route payload.  An
            # attacker who sends OPTIONS to a data endpoint receives only CORS
            # headers or a 405 Method Not Allowed — not the response body.
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if any(path == p or path.startswith(p) for p in _MTLS_PROBE_PATHS):
            await self.app(scope, receive, send)
            return
        # Look up the HMAC header injected by the CF Worker (only when cert is bound).
        provided = b""
        for k, v in scope.get("headers", []):
            if k == _MTLS_ACTIVE_HEADER:
                provided = v
                break
        provided_str = provided.decode("latin-1", "ignore").lower()
        if provided_str and _hmac_mod.compare_digest(provided_str, self._expected_hmac):
            await self.app(scope, receive, send)
            return
        # Missing or wrong HMAC — reject with 403.
        logger.warning(
            f"MtlsClientCertMiddleware: rejected {method} {path} "
            f"(X-Cf-Mtls-Active={'present but wrong' if provided_str else 'absent'})"
        )
        from fastapi.responses import JSONResponse
        resp = JSONResponse(
            status_code=403,
            content={
                "detail": (
                    "mTLS client certificate required — "
                    "request must originate from the Cloudflare edge worker "
                    "with the mTLS certificate bound."
                )
            },
        )
        await resp(scope, receive, send)


# ── /learn/ slug normalisation redirect (Task #3 — SEO) ─────────────────────
#
# Any request to ``/learn/<dirty-slug>`` where the slug contains paper-code
# noise (parenthesised codes like ``(2025)`` or ``(bcm0200304)``) is 301-
# redirected to the clean form so that any URLs Google may have already
# crawled are normalised before a user or bot follows them.
#
# Only fires for paths that actually need cleaning — clean slugs pass
# through to the SPA/CF Pages handler unchanged.

from slug_utils import clean_learn_slug as _normalize_learn_slug  # Task #3 SEO

# Fast-path guard: only enter slug-cleaning logic when the path contains
# one of these markers.  Catches both parenthesised codes '(' and the
# double-hyphen separator '--' used to append paper-code tokens.
_DIRTY_SLUG_MARKERS = ("(", "--")


class DirtyLearnSlugRedirectMiddleware:
    """301-redirect ``/learn/<noisy-slug>`` → ``/learn/<clean-slug>``.

    Fires when the slug after ``/learn/`` contains ``(`` (parenthesised
    paper code or year) OR ``--`` (double-hyphen paper-code separator).
    Clean slugs contain neither and pass through at zero cost.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            path: str = scope.get("path", "")
            if path.startswith("/learn/") and any(
                m in path for m in _DIRTY_SLUG_MARKERS
            ):
                prefix = "/learn/"
                raw_slug = path[len(prefix):]
                clean_slug = _normalize_learn_slug(raw_slug)
                if clean_slug != raw_slug and clean_slug:
                    qs = scope.get("query_string", b"")
                    location = f"{prefix}{clean_slug}"
                    if qs:
                        location = f"{location}?{qs.decode('latin-1')}"
                    from starlette.responses import Response as _R
                    resp = _R(
                        status_code=301,
                        headers={"Location": location,
                                 "Cache-Control": "public, max-age=31536000"},
                    )
                    await resp(scope, receive, send)
                    return
        await self.app(scope, receive, send)
