"""Google Indexing API + sitemap-ping client (SEO Phase C).

Why this module exists
----------------------
Google does not consume IndexNow, so every Phase A content-time signal
(IndexNow + Cloudflare cache purge + synthetic Googlebot prewarm) misses
the search engine that drives ~96% of Syrabit's bot traffic. Google
instead relies on natural sitemap polling, which typically takes 3–7 days
before a freshly-generated URL gets indexed.

Phase C adds two Google-side notifications that run alongside the Phase A
fan-out:

1. `notify_url_updated(url)` — POSTs to `urlNotifications:publish` with
   `type=URL_UPDATED`. Authenticated via a service-account JWT minted by
   `google-auth` (already pinned in `requirements.txt`). Token is cached
   for ~50 min to stay well within the 60 min lifetime.

2. `ping_sitemap(sitemap_url)` — GET
   `https://www.google.com/ping?sitemap=<sitemap>`. Free, unauthenticated,
   kept alive by Google for decades.

Safety rails
------------
- 5 s timeout on every outbound call; exceptions are logged but NEVER
  re-raised, so the content generator can't block or fail because of us.
- Daily quota cap (default 200/day, configurable via env
  `GOOGLE_INDEXING_DAILY_LIMIT`). The cap is enforced cross-worker via
  an atomic Mongo `update_one` with filter `{day, sent: {$lt: limit}}`
  + `$inc: {sent: 1}` so concurrent gunicorn workers cannot collectively
  blow past the limit. A process-local mirror of the counter provides a
  fast-path short-circuit and the Mongo-unavailable fallback. When the
  quota is reached the client short-circuits further `notify_url_updated`
  calls for the rest of the UTC day.
- Missing `GOOGLE_INDEXING_SERVICE_ACCOUNT` secret is treated as
  "disabled" — we log a one-time warning and every `notify_url_updated`
  call returns a structured "skipped" result. Sitemap-ping still works.
- Killswitch via env `GOOGLE_INDEXING_ENABLED=false`.

Stats exposed via `get_stats()` and surfaced through the admin endpoint
`GET /admin/seo/google-indexing-stats`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Constants / configuration
# -----------------------------------------------------------------------------

INDEXING_API_URL = "https://indexing.googleapis.com/v3/urlNotifications:publish"
OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
SITEMAP_PING_URL_TPL = "https://www.google.com/ping?sitemap={sitemap}"
SCOPE = "https://www.googleapis.com/auth/indexing"

# Token lifetime is 3600 s; refresh at 3000 s to leave a safety margin.
_TOKEN_CACHE_TTL_S = 3000
# Outbound network timeout. The task spec mandates 5 s so the content
# generator can't be held up by Google's side.
_HTTP_TIMEOUT_S = 5.0

_DEFAULT_DAILY_LIMIT = 200


def _daily_limit() -> int:
    raw = os.getenv("GOOGLE_INDEXING_DAILY_LIMIT")
    if not raw:
        return _DEFAULT_DAILY_LIMIT
    try:
        v = int(raw)
        return v if v >= 0 else _DEFAULT_DAILY_LIMIT
    except (TypeError, ValueError):
        return _DEFAULT_DAILY_LIMIT


def _enabled() -> bool:
    """Killswitch. Defaults to enabled in prod, disabled under pytest so
    existing test suites don't start making outbound calls."""
    raw = os.getenv("GOOGLE_INDEXING_ENABLED")
    if raw is not None:
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if os.getenv("PYTEST_CURRENT_TEST"):
        return False
    return True


# -----------------------------------------------------------------------------
# Task #246 — Priority tier system for Google Indexing API quota.
#
# URLs are classified into 3 tiers so high-value fresh content always gets
# notified, even when the daily quota is nearly exhausted by lower-value URLs.
#
#   Tier 1 — notes + MCQs for top-20 subjects: full daily quota
#   Tier 2 — definitions + examples: up to 75% of daily quota
#   Tier 3 — FAQs / older / non-priority: up to 25% of daily quota
#
# The per-tier cap is enforced in-memory only (not persisted to Mongo).
# A worker restart resets the tier counters — which is intentional, since a
# restart typically happens at deploy time (a natural quota boundary) and the
# Mongo-authoritative global quota remains intact.
#
# `GOOGLE_INDEXING_TIER1_SUBJECTS` env var: comma-separated subject slugs that
# should be treated as Tier-1 (in addition to the top-20 defaults below).
# -----------------------------------------------------------------------------

_TIER1_DEFAULT_SUBJECTS = frozenset([
    "physics", "chemistry", "mathematics", "biology", "english",
    "assamese", "history", "political-science", "economics", "geography",
    "hindi", "alternative-english", "logic-philosophy", "sociology",
    "education", "computer-science", "business-studies", "accountancy",
    "environmental-studies", "statistics",
])

_tier_sent: Dict[int, int] = {1: 0, 2: 0, 3: 0}
_tier_day: str = ""
_tier_lock = threading.Lock()


def _tier1_subjects() -> frozenset:
    extra = os.getenv("GOOGLE_INDEXING_TIER1_SUBJECTS", "").strip()
    if not extra:
        return _TIER1_DEFAULT_SUBJECTS
    return _TIER1_DEFAULT_SUBJECTS | frozenset(
        s.strip().lower() for s in extra.split(",") if s.strip()
    )


def _tier_daily_limit(tier: int) -> int:
    """Per-tier sub-cap derived from the global daily limit.

    Tier-1 → 100% of daily limit (full access, highest priority)
    Tier-2 → 75%  of daily limit
    Tier-3 → 25%  of daily limit  (cannot crowd out higher tiers)
    """
    limit = _daily_limit()
    if tier == 3:
        return max(1, limit // 4)
    if tier == 2:
        return max(1, (limit * 3) // 4)
    return limit  # tier == 1


def _roll_tier_if_needed() -> None:
    """Reset per-tier counters on UTC-day rollover. Must be called under
    `_tier_lock`."""
    global _tier_day, _tier_sent
    today = _today_key()
    if _tier_day != today:
        _tier_day = today
        _tier_sent = {1: 0, 2: 0, 3: 0}


def _under_tier_quota_and_reserve(tier: int) -> bool:
    """Atomically check and increment the per-tier counter. Returns True
    iff the submission should proceed under the tier sub-cap.

    Priority protection semantics (Task #246):
    - A floor of 30% of the daily quota is reserved exclusively for Tier-1 by
      capping Tier-2+Tier-3 combined at 70% of the global limit. This guarantees
      Tier-1 capacity even if lower-priority pages are published early in the day.
    - Tier-3 is additionally blocked once 75% of the global quota is spent,
      providing a second layer of protection so Tier-1/Tier-2 can always get
      through late in the day.
    - Tier-1 itself is gated only by the global daily limit.
    """
    with _tier_lock:
        _roll_tier_if_needed()
        limit = _daily_limit()
        cap = _tier_daily_limit(tier)
        if _tier_sent.get(tier, 0) >= cap:
            return False
        # Floor protection: Tier-2 + Tier-3 combined may not consume more than
        # 70% of the daily limit, so Tier-1 always retains ≥30% of capacity.
        if tier in (2, 3):
            t23_total = _tier_sent.get(2, 0) + _tier_sent.get(3, 0)
            if t23_total >= (limit * 7) // 10:
                return False
        # Second-layer back-pressure for Tier-3: block once global usage
        # exceeds 75% so Tier-1/Tier-2 can still squeeze in before day-end.
        if tier == 3:
            total_sent = sum(_tier_sent.values())
            if total_sent >= (limit * 3) // 4:
                return False
        _tier_sent[tier] = _tier_sent.get(tier, 0) + 1
        return True


def classify_url_tier(url: str) -> int:
    """Classify a Syrabit URL into a priority tier (1 = highest, 3 = lowest).

    Tier-1: notes + MCQs for top-20 subjects (drive 90%+ of search traffic).
    Tier-2: definitions + examples (useful secondary content).
    Tier-3: FAQs, older or non-priority page types.

    URL format (path segments after the domain):
        /{board}/{class}/{subject}/{topic}            → notes page (default)
        /{board}/{class}/{subject}/{topic}/{page_type} → typed page

    Segment indices (0-based path segments with empties removed):
        0=board, 1=class, 2=subject, 3=topic, 4=page_type (optional)
    """
    if not url:
        return 3
    try:
        from urllib.parse import urlparse
        path = urlparse(url).path
        segments = [s for s in path.split("/") if s]
        # Minimum: board + class + subject + topic = 4 segments
        if len(segments) < 4:
            return 3
        subject_slug = segments[2].lower()
        page_type = segments[4].lower() if len(segments) > 4 else "notes"

        # Tier-3: FAQ and anything not in notes/mcqs/definition/examples
        if page_type in ("faq", "important-questions"):
            return 3

        # Tier-1: notes or MCQs for a top-20 subject
        if page_type in ("notes", "mcqs", ""):
            if subject_slug in _tier1_subjects():
                return 1
            return 2

        # Tier-2: definitions + examples (all subjects)
        if page_type in ("definition", "examples"):
            return 2

        return 3
    except Exception:
        return 3


# -----------------------------------------------------------------------------
# Service-account loading (lazy, cached, never raises)
# -----------------------------------------------------------------------------

_sa_lock = threading.Lock()
_sa_info: Optional[Dict[str, Any]] = None
_sa_load_attempted = False
_sa_load_error: Optional[str] = None


def _load_service_account() -> Optional[Dict[str, Any]]:
    """Parse `GOOGLE_INDEXING_SERVICE_ACCOUNT` once. The secret may be raw
    JSON or a base64-encoded JSON string (some operators paste it
    that way to avoid newlines-in-env issues)."""
    global _sa_info, _sa_load_attempted, _sa_load_error
    with _sa_lock:
        if _sa_load_attempted:
            return _sa_info
        _sa_load_attempted = True
        raw = os.getenv("GOOGLE_INDEXING_SERVICE_ACCOUNT", "").strip()
        if not raw:
            _sa_load_error = "missing_secret"
            logger.warning(
                "google_indexing_client: GOOGLE_INDEXING_SERVICE_ACCOUNT is "
                "not set — Indexing API calls will be skipped. Sitemap-ping "
                "still works."
            )
            return None
        # Support base64-encoded JSON as a fallback.
        candidate = raw
        if not candidate.lstrip().startswith("{"):
            try:
                import base64
                candidate = base64.b64decode(raw).decode("utf-8")
            except Exception as e:
                _sa_load_error = f"b64_decode_failed:{type(e).__name__}"
                logger.error(
                    "google_indexing_client: secret is neither JSON nor valid "
                    "base64: %s", e,
                )
                return None
        try:
            info = json.loads(candidate)
        except json.JSONDecodeError as e:
            _sa_load_error = f"json_decode_failed:{e.msg}"
            logger.error(
                "google_indexing_client: secret is not valid JSON: %s", e,
            )
            return None
        required = {"client_email", "private_key", "token_uri"}
        missing = required - set(info.keys())
        if missing:
            _sa_load_error = f"missing_fields:{sorted(missing)}"
            logger.error(
                "google_indexing_client: service-account JSON missing fields: %s",
                sorted(missing),
            )
            return None
        _sa_info = info
        return _sa_info


def _reset_state_for_tests() -> None:
    """Reset every bit of module-level cache. Tests use this to get a clean
    slate (fresh counters + fresh service-account cache)."""
    global _sa_info, _sa_load_attempted, _sa_load_error
    global _cached_token, _cached_token_expires_at
    with _sa_lock:
        _sa_info = None
        _sa_load_attempted = False
        _sa_load_error = None
    with _token_lock:
        _cached_token = None
        _cached_token_expires_at = 0.0
    with _stats_lock:
        _stats.clear()
        _stats.update(_fresh_stats())
    _loaded_days.clear()
    _load_locks.clear()
    global _last_flushed_day, _quota_alert_fired_day
    for k in _COUNTER_KEYS:
        _last_flushed_stats[k] = 0
    _last_flushed_day = ""
    _quota_alert_fired_day = ""
    # Task #246 — reset per-tier counters.
    global _tier_day, _tier_sent
    with _tier_lock:
        _tier_day = ""
        _tier_sent = {1: 0, 2: 0, 3: 0}


# -----------------------------------------------------------------------------
# OAuth token cache (JWT-bearer → access_token)
# -----------------------------------------------------------------------------

_token_lock = threading.Lock()
_cached_token: Optional[str] = None
_cached_token_expires_at: float = 0.0  # monotonic epoch-seconds
# Async single-flight lock: during a burst of Phase A fan-outs several
# coroutines may all see an empty cache and race to mint a token. Without
# this guard each one hits oauth2.googleapis.com, which both wastes quota
# and can produce mild thundering-herd latency spikes. We hold this lock
# only around the mint call; the read-side fast path is still lock-free.
_token_refresh_lock: Optional[asyncio.Lock] = None


def _get_refresh_lock() -> asyncio.Lock:
    global _token_refresh_lock
    if _token_refresh_lock is None:
        _token_refresh_lock = asyncio.Lock()
    return _token_refresh_lock


async def _mint_access_token() -> Optional[str]:
    """Mint an OAuth2 access token using the service-account JWT-bearer
    flow. `google.oauth2.service_account.Credentials` builds the signed
    JWT for us; we then exchange it with Google's OAuth token endpoint
    over an async httpx call so we never block the event loop."""
    info = _load_service_account()
    if not info:
        return None
    try:
        # google-auth is pinned in requirements.txt as google-auth==2.49.1.
        from google.oauth2 import service_account
        import google.auth.crypt
        import google.auth.jwt
    except Exception as e:  # pragma: no cover — lib is always installed in prod
        logger.error("google_indexing_client: google-auth import failed: %s", e)
        return None

    try:
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=[SCOPE],
        )
    except Exception as e:
        logger.error("google_indexing_client: bad service-account key: %s", e)
        return None

    # Build the assertion JWT manually so we can do the token exchange
    # over httpx (google-auth's default transport is sync urllib3).
    try:
        signer = creds._signer  # pylint: disable=protected-access
        now = int(datetime.now(timezone.utc).timestamp())
        payload = {
            "iss": info["client_email"],
            "scope": SCOPE,
            "aud": info.get("token_uri", OAUTH_TOKEN_URL),
            "iat": now,
            "exp": now + 3600,
        }
        assertion = google.auth.jwt.encode(signer, payload).decode("utf-8")
    except Exception as e:
        logger.error("google_indexing_client: JWT signing failed: %s", e)
        return None

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
            resp = await client.post(
                info.get("token_uri", OAUTH_TOKEN_URL),
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": assertion,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except Exception as e:
        logger.warning("google_indexing_client: token endpoint error: %s", e)
        return None

    if resp.status_code != 200:
        logger.warning(
            "google_indexing_client: token exchange failed: %s %s",
            resp.status_code, resp.text[:200],
        )
        return None
    try:
        data = resp.json()
    except Exception as e:
        logger.warning("google_indexing_client: token body parse failed: %s", e)
        return None
    token = data.get("access_token")
    if not token:
        logger.warning("google_indexing_client: token response missing access_token")
        return None
    return token


async def _get_cached_token() -> Optional[str]:
    global _cached_token, _cached_token_expires_at
    loop = asyncio.get_event_loop()
    now = loop.time()
    # Fast path: cache hit, no lock needed — the threading.Lock only guards
    # the pointer write below. A stale read here is harmless; the worst case
    # is that we re-enter the refresh lock and see the fresh token there.
    with _token_lock:
        if _cached_token and now < _cached_token_expires_at:
            return _cached_token

    # Slow path: single-flight refresh. The first coroutine to enter mints
    # a token; subsequent coroutines wait, re-check the cache, and return
    # the freshly-minted token instead of firing their own mint request.
    async with _get_refresh_lock():
        with _token_lock:
            if _cached_token and loop.time() < _cached_token_expires_at:
                return _cached_token
        new_token = await _mint_access_token()
        if not new_token:
            return None
        with _token_lock:
            _cached_token = new_token
            _cached_token_expires_at = loop.time() + _TOKEN_CACHE_TTL_S
        return new_token


# -----------------------------------------------------------------------------
# Daily-quota + stats counters
# -----------------------------------------------------------------------------

_stats_lock = threading.Lock()

# Every counter field in `_stats` that should survive a restart.
# Persistence uses `$inc` with delta tracking (see `_last_flushed_stats`) so
# concurrent gunicorn workers accumulate into a shared per-day total instead
# of clobbering one another. On restart, a worker hydrates by reading the
# shared total and seeding both `_stats` and `_last_flushed_stats` to it, so
# its first flush sends a zero delta and no double-counts occur.
_COUNTER_KEYS = (
    "sent", "status_2xx", "status_4xx", "status_5xx",
    "errors", "quota_blocks", "skipped_disabled",
    "sitemap_ping_sent", "sitemap_ping_2xx", "sitemap_ping_errors",
)

_STORE_COLLECTION = "google_indexing_daily"

# Days we've successfully hydrated from the Mongo store. A rollover to a
# new day clears this so the next touch re-hydrates. Only populated AFTER
# a successful load so a transient Mongo outage on first touch doesn't
# permanently strand a worker with stale zeroed counters.
_loaded_days: set = set()

# Per-day async locks to single-flight the hydrate path: concurrent first
# requests on a fresh process all await the same load instead of racing.
_load_locks: Dict[str, "asyncio.Lock"] = {}
_load_locks_guard = threading.Lock()

# Mirror of `_stats` as of the last successful flush. Used to compute the
# delta for $inc-based persistence so multi-worker totals aggregate
# correctly. Seeded on hydrate with the stored values so the next flush
# sends a zero delta.
_last_flushed_stats: Dict[str, int] = {k: 0 for k in _COUNTER_KEYS}
_last_flushed_day: str = ""

# Daily-quota-exhausted alert dedupe. We fire AT MOST ONCE per UTC day so a
# busy fan-out batch that hits the cap doesn't spam ops with hundreds of
# identical alerts. Stored as the YYYY-MM-DD key of the day we last fired
# for; the day-rollover logic in `_roll_day_if_needed` resets it.
_quota_alert_fired_day: str = ""
_QUOTA_ALERT_TYPE = "google_indexing_quota_exhausted"
_QUOTA_ALERT_DASHBOARD_URL = (
    "https://syrabit.ai/admin/seo/google-indexing-stats"
)
# Mongo-backed cross-worker claim collection. The doc `_id` encodes the
# alert type + UTC day, so an `insert_one` with a duplicate key means a
# sibling worker already won the claim for today and we must NOT re-send.
_QUOTA_ALERT_CLAIM_COLLECTION = "google_indexing_alert_claims"


def _today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _fresh_stats() -> Dict[str, Any]:
    return {
        "day": _today_key(),
        "sent": 0,
        "status_2xx": 0,
        "status_4xx": 0,
        "status_5xx": 0,
        "errors": 0,
        "quota_blocks": 0,
        "skipped_disabled": 0,
        "sitemap_ping_sent": 0,
        "sitemap_ping_2xx": 0,
        "sitemap_ping_errors": 0,
    }


_stats: Dict[str, Any] = _fresh_stats()


def _roll_day_if_needed() -> None:
    today = _today_key()
    if _stats.get("day") != today:
        # New UTC day: reset counters and force a re-hydrate on next read.
        # The previous day's totals stay in Mongo for historical queries.
        _stats.clear()
        _stats.update(_fresh_stats())
        _loaded_days.clear()
        # Allow the quota-exhausted alert to fire once again for the new day.
        global _quota_alert_fired_day
        _quota_alert_fired_day = ""


def _bump(key: str, amount: int = 1) -> None:
    with _stats_lock:
        _roll_day_if_needed()
        _stats[key] = _stats.get(key, 0) + amount
    _schedule_flush()


def _record_status_code(status_code: int) -> None:
    with _stats_lock:
        _roll_day_if_needed()
        if 200 <= status_code < 300:
            _stats["status_2xx"] = _stats.get("status_2xx", 0) + 1
        elif 400 <= status_code < 500:
            _stats["status_4xx"] = _stats.get("status_4xx", 0) + 1
        elif 500 <= status_code < 600:
            _stats["status_5xx"] = _stats.get("status_5xx", 0) + 1
    _schedule_flush()


def _under_quota_and_reserve() -> bool:
    """Process-local atomic check-and-increment used as the in-memory
    fast-path AND as the Mongo-unavailable fallback for `_reserve_quota`.
    Returns True if a slot was reserved, False if the local mirror of the
    daily cap has been hit. A failed POST still counts against the quota
    so a misconfigured service account can't hammer Google.

    On the first quota_block of the UTC day this also schedules an ops
    alert (de-duped per day per process; cross-worker dedupe via Mongo
    claim doc inside `_dispatch_quota_alert`)."""
    blocked = False
    sent_now = 0
    limit = 0
    day_now = ""
    with _stats_lock:
        _roll_day_if_needed()
        limit = _daily_limit()
        day_now = _stats.get("day", "")
        if _stats.get("sent", 0) >= limit:
            _stats["quota_blocks"] = _stats.get("quota_blocks", 0) + 1
            blocked = True
            sent_now = int(_stats.get("sent", 0))
        else:
            _stats["sent"] = _stats.get("sent", 0) + 1
            sent_now = int(_stats.get("sent", 0))
    _schedule_flush()
    if blocked:
        # Notify ops the very first time we hit the cap today. De-duped
        # under the stats lock so concurrent quota_blocks across coroutines
        # only schedule one alert per UTC day per process. Worker-level
        # de-dup is provided by `metrics._dispatch_alert`'s cooldown.
        _schedule_quota_alert(day_now, sent_now, limit)
    return not blocked


def _schedule_quota_alert(day: str, sent: int, limit: int) -> None:
    """Fire-and-forget the ops alert when today's Google Indexing quota is
    exhausted. At-most-once per UTC day per process. Killswitch-aware so
    the test suite doesn't dispatch alerts."""
    global _quota_alert_fired_day
    if not day:
        return
    if os.getenv("GOOGLE_INDEXING_QUOTA_ALERT_DISABLED", "").strip().lower() in (
        "1", "true", "yes", "on"
    ):
        return
    if os.getenv("PYTEST_CURRENT_TEST") and not os.getenv(
        "GOOGLE_INDEXING_QUOTA_ALERT_IN_TESTS"
    ):
        return
    with _stats_lock:
        if _quota_alert_fired_day == day:
            return
        # Mark fired BEFORE scheduling so a sibling coroutine racing in on
        # the next quota_block doesn't queue a duplicate task.
        _quota_alert_fired_day = day
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Sync context (module imported standalone, tests). Nothing to do —
        # the next async quota_block on a real event loop will catch up if
        # we reset the fired-day below.
        with _stats_lock:
            _quota_alert_fired_day = ""
        return
    loop.create_task(_dispatch_quota_alert(day, sent, limit))


async def _claim_quota_alert_day(day: str) -> bool:
    """Atomic, cross-worker claim for "I will send today's quota alert".

    The first worker to insert `_id="<alert_type>:<day>"` into the claims
    collection wins; siblings hit `DuplicateKeyError` and bail. This is
    the same pattern used by the SEO weekly digest (`_acquire_weekly_digest_claim`).
    Returns True iff this worker should dispatch.

    If Mongo is unavailable we fall back to letting the alert fire — a
    duplicate alert during an outage is far better than silently swallowing
    a real quota-exhaustion event."""
    try:
        from deps import db, is_mongo_available
        if not await is_mongo_available():
            logger.info(
                "google_indexing quota alert: Mongo unavailable, "
                "falling back to in-process dedupe only"
            )
            return True
    except Exception as e:
        logger.debug(
            "google_indexing quota alert: deps import failed (%s); "
            "falling back to in-process dedupe only", e,
        )
        return True
    claim_id = f"{_QUOTA_ALERT_TYPE}:{day}"
    try:
        from pymongo.errors import DuplicateKeyError
    except Exception as e:
        logger.debug("google_indexing quota alert: pymongo import failed: %s", e)
        return True
    try:
        await db[_QUOTA_ALERT_CLAIM_COLLECTION].insert_one({
            "_id": claim_id,
            "alert_type": _QUOTA_ALERT_TYPE,
            "day": day,
            "claimed_at": datetime.now(timezone.utc),
        })
        return True
    except DuplicateKeyError:
        logger.info(
            "google_indexing quota alert: another worker already claimed %s",
            claim_id,
        )
        return False
    except Exception as e:
        # Mongo hiccup mid-insert: don't lose the alert.
        logger.debug(
            "google_indexing quota alert: claim insert failed: %s; "
            "falling back to in-process dedupe only", e,
        )
        return True


async def _dispatch_quota_alert(day: str, sent: int, limit: int) -> None:
    """Send the quota-exhausted alert through the shared metrics alert
    pipeline (SES email + webhook + admin-dashboard banner). Never
    raises — the content generator must not be impacted by alert failures.

    Cross-worker dedupe: before dispatching, race on a Mongo claim doc
    keyed by today's UTC date. Only the first worker to insert wins; the
    rest log and return without sending."""
    won_claim = await _claim_quota_alert_day(day)
    if not won_claim:
        return
    try:
        from metrics import _dispatch_alert
    except Exception as e:
        logger.debug("google_indexing quota alert: metrics import failed: %s", e)
        return
    # `metrics._dispatch_alert` enforces a global per-alert-type cooldown
    # (`_ALERT_COOLDOWN_S`, currently 30 min). Our own dedupe is already
    # day-scoped via the Mongo claim above, so a fresh quota exhaustion on
    # day N+1 just past UTC midnight could otherwise be silently swallowed
    # if day N's alert fired within the cooldown window. Clear the cooldown
    # entry for this alert type so day-boundary alerts always go through —
    # same trick `bot_discovery._seo_health_alert_loop` uses for
    # `seo_health_degraded` and `seo_url_spike`. Best-effort: imported
    # separately so a metrics module without the cooldown table (e.g. test
    # stubs) doesn't block the dispatch.
    try:
        from metrics import _alert_last_fired as _ml
        _ml.pop(_QUOTA_ALERT_TYPE, None)
    except Exception:
        pass
    title = "Google Indexing daily quota exhausted"
    body = (
        f"Google Indexing API submissions reached today's cap of "
        f"{sent}/{limit} for {day} (UTC). Additional fresh URLs will skip "
        f"Google notification until the UTC midnight rollover. To raise "
        f"the cap, set the env var GOOGLE_INDEXING_DAILY_LIMIT and restart "
        f"the API. Dashboard: {_QUOTA_ALERT_DASHBOARD_URL}"
    )
    try:
        await _dispatch_alert(
            _QUOTA_ALERT_TYPE,
            title,
            body,
            threshold_snapshot={
                "metric": "google_indexing_daily_limit",
                "value": limit,
                "actual": sent,
                "day": day,
                "dashboard_url": _QUOTA_ALERT_DASHBOARD_URL,
            },
        )
    except Exception as e:
        logger.debug("google_indexing quota alert dispatch failed: %s", e)
        # Roll back the dedupe so the next quota_block can retry the alert.
        global _quota_alert_fired_day
        with _stats_lock:
            if _quota_alert_fired_day == day:
                _quota_alert_fired_day = ""


async def _reserve_quota_in_store(limit: int) -> Optional[bool]:
    """Atomically reserve a slot in the shared `google_indexing_daily`
    Mongo doc. Returns:

    - True  → reservation succeeded (`$inc: {sent: 1}` applied under the
              filter `{day, sent: {$lt: limit}}`).
    - False → daily cap reached on the DB side. The doc exists and its
              `sent` is already >= `limit`.
    - None  → Mongo unavailable / transient error. Caller should fall
              back to the in-memory reservation.

    This is the cross-worker authoritative cap. With multiple gunicorn
    workers each holding their own in-memory `_stats`, the only way to
    keep the aggregate Google Indexing API submissions per UTC day at or
    below the configured cap is to gate every send on this DB-side
    reservation.
    """
    try:
        from deps import db, is_mongo_available
        if not await is_mongo_available():
            return None
        coll = db[_STORE_COLLECTION]
        day = _today_key()
        now = datetime.now(timezone.utc)
        # Atomic conditional reserve. Mongo serialises this update at the
        # document level, so concurrent workers cannot both observe
        # sent < limit and both increment past the cap.
        updated = await coll.update_one(
            {"day": day, "sent": {"$lt": limit}},
            {
                "$inc": {"sent": 1},
                "$set": {"updated_at": now},
            },
        )
        if getattr(updated, "matched_count", 0) >= 1:
            return True
        # No match: either the day's doc doesn't exist yet OR the cap was
        # already reached. Distinguish with a probe.
        existing = await coll.find_one({"day": day}, {"sent": 1, "_id": 0})
        if existing is None:
            # Race-safe bootstrap: create the day's doc with sent=0 only if
            # nobody else got there first.
            try:
                await coll.update_one(
                    {"day": day},
                    {"$setOnInsert": {
                        "day": day, "sent": 0,
                        "created_at": now, "updated_at": now,
                    }},
                    upsert=True,
                )
            except Exception as e:
                logger.debug(
                    "google_indexing reserve bootstrap failed: %s", e,
                )
                return None
            # Retry the conditional reserve now that the doc exists.
            updated = await coll.update_one(
                {"day": day, "sent": {"$lt": limit}},
                {
                    "$inc": {"sent": 1},
                    "$set": {"updated_at": now},
                },
            )
            if getattr(updated, "matched_count", 0) >= 1:
                return True
            # Still nothing — another worker raced us past the cap.
        return False
    except Exception as e:
        logger.debug("google_indexing reserve failed: %s", e)
        return None


async def _reserve_quota(limit: int) -> bool:
    """Reserve a submission slot honouring BOTH a process-local fast-path
    AND a Mongo-authoritative cross-worker cap. Single source of truth for
    "may we submit this URL right now?". Never raises.

    Order of operations:
      1. Local atomic reserve under `_stats_lock`. Serialises within the
         worker; if local mirror already shows we are at the cap, short-
         circuit without hitting Mongo.
      2. Cross-worker reserve via `_reserve_quota_in_store`. The DB is
         authoritative — if it says cap reached, we roll back the local
         increment and report quota_blocked. If Mongo is unavailable, we
         trust the local reserve so a Mongo outage does not stop sends.
    """
    # Step 1: local fast-path (atomic in-process).
    if not _under_quota_and_reserve():
        return False
    # Step 2: cross-worker DB-authoritative reserve.
    db_result = await _reserve_quota_in_store(limit)
    if db_result is True:
        # Mirror baseline so the next `_flush_to_store` does NOT $inc
        # `sent` again — the reservation already incremented the shared
        # doc's `sent` field directly.
        global _last_flushed_day
        with _stats_lock:
            day = _stats.get("day")
            if _last_flushed_day != day:
                for k in _COUNTER_KEYS:
                    _last_flushed_stats[k] = 0
                _last_flushed_day = day
            _last_flushed_stats["sent"] = (
                _last_flushed_stats.get("sent", 0) + 1
            )
        return True
    if db_result is False:
        # DB cap reached. Roll back the optimistic local reserve and sync
        # the local mirror to the cap so subsequent fast-path checks
        # short-circuit without re-hitting Mongo.
        with _stats_lock:
            current = _stats.get("sent", 0)
            _stats["sent"] = max(current - 1, limit)
            _stats["quota_blocks"] = _stats.get("quota_blocks", 0) + 1
            day = _stats.get("day")
            if _last_flushed_day != day:
                for k in _COUNTER_KEYS:
                    _last_flushed_stats[k] = 0
                _last_flushed_day = day
            # Pin the baseline at the cap so flush won't re-$inc sent.
            _last_flushed_stats["sent"] = max(
                _last_flushed_stats.get("sent", 0), _stats["sent"],
            )
        _schedule_flush()
        return False
    # db_result is None → Mongo unavailable. Trust the local reserve.
    return True


# -----------------------------------------------------------------------------
# Persistence: load-on-first-use + $inc-delta upsert per mutation.
# Every helper here is async and never raises back to the caller; Mongo
# being down should NEVER stop the content generator from running.
# -----------------------------------------------------------------------------

async def _load_day_from_store(day: str) -> bool:
    """Hydrate `_stats` with the persisted counters for `day`. Returns
    True if the load reached Mongo (regardless of whether a doc existed
    for the day), False on Mongo-unavailable / error — caller uses this
    to decide whether to mark the day loaded."""
    try:
        from deps import db, is_mongo_available
        if not await is_mongo_available():
            return False
        doc = await db[_STORE_COLLECTION].find_one(
            {"day": day}, {"_id": 0},
        )
    except Exception as e:
        logger.debug("google_indexing store load failed: %s", e)
        return False
    global _last_flushed_day
    with _stats_lock:
        # Guard against rollover racing with the load.
        if _stats.get("day") != day:
            return True
        if doc:
            for k in _COUNTER_KEYS:
                v = doc.get(k)
                if isinstance(v, int) and v > _stats.get(k, 0):
                    _stats[k] = v
        # Seed `_last_flushed_stats` to the current in-memory view so the
        # next `_flush_to_store` sends a zero delta for anything already
        # persisted (no double-counting after restart).
        for k in _COUNTER_KEYS:
            _last_flushed_stats[k] = int(_stats.get(k, 0))
        _last_flushed_day = day
    return True


def _get_load_lock(day: str) -> "asyncio.Lock":
    with _load_locks_guard:
        lock = _load_locks.get(day)
        if lock is None:
            lock = asyncio.Lock()
            _load_locks[day] = lock
        # Opportunistic cleanup: keep only the last 3 days' locks.
        if len(_load_locks) > 3:
            stale = sorted(_load_locks.keys())[:-3]
            for s in stale:
                _load_locks.pop(s, None)
        return lock


async def _ensure_loaded() -> None:
    """Load today's counters from the store exactly once per day per
    process. Concurrent callers single-flight through a per-day lock so
    none proceed with stale zeros while hydration is in flight. If the
    load fails (Mongo unavailable or transient error) the day is NOT
    marked loaded — the next call retries."""
    with _stats_lock:
        _roll_day_if_needed()
        day = _stats.get("day")
    if not day or day in _loaded_days:
        return
    lock = _get_load_lock(day)
    async with lock:
        # Re-check under the lock in case a sibling coroutine loaded it
        # while we were waiting.
        if day in _loaded_days:
            return
        ok = await _load_day_from_store(day)
        if ok:
            _loaded_days.add(day)


async def _flush_to_store() -> None:
    """Upsert today's counters into Mongo using `$inc` on per-field
    deltas so concurrent gunicorn workers sum into a shared total
    instead of overwriting each other. Safe to call concurrently — each
    worker only reports its own uncommitted delta, and `_last_flushed_stats`
    is updated under `_stats_lock` right after the Mongo call succeeds."""
    global _last_flushed_day
    try:
        from deps import db, is_mongo_available
        if not await is_mongo_available():
            return
    except Exception as e:
        logger.debug("google_indexing store flush: deps import failed: %s", e)
        return
    with _stats_lock:
        day = _stats.get("day")
        if not day:
            return
        # Day rolled over under us: start a fresh delta baseline.
        if _last_flushed_day != day:
            for k in _COUNTER_KEYS:
                _last_flushed_stats[k] = 0
            _last_flushed_day = day
        current = {k: int(_stats.get(k, 0)) for k in _COUNTER_KEYS}
        delta = {
            k: current[k] - _last_flushed_stats.get(k, 0)
            for k in _COUNTER_KEYS
        }
        # Filter to positive deltas only; counters are monotonic within a
        # day so a negative delta means a reset we don't want to propagate.
        inc = {k: v for k, v in delta.items() if v > 0}
        if not inc:
            return
        # Optimistically advance the baseline so concurrent flushers on
        # the same worker don't double-report the same delta. If the
        # Mongo write fails below, the counters we lose are bounded to
        # one flush window (sub-second) which is acceptable for stats.
        for k in inc:
            _last_flushed_stats[k] = current[k]
    try:
        await db[_STORE_COLLECTION].update_one(
            {"day": day},
            {
                "$inc": inc,
                "$set": {
                    "day": day,
                    "updated_at": datetime.now(timezone.utc),
                },
                "$setOnInsert": {"created_at": datetime.now(timezone.utc)},
            },
            upsert=True,
        )
    except Exception as e:
        logger.debug("google_indexing store flush failed: %s", e)
        # Roll the baseline back so the next flush retries this delta.
        with _stats_lock:
            if _last_flushed_day == day:
                for k, v in inc.items():
                    _last_flushed_stats[k] = max(
                        0, _last_flushed_stats.get(k, 0) - v
                    )


def _schedule_flush() -> None:
    """Fire-and-forget the persistence write. If there's no running event
    loop (module imported from a sync context, or tests) we skip silently —
    the next async mutation will catch up. Killswitch-aware."""
    if os.getenv("GOOGLE_INDEXING_PERSIST_DISABLED", "").strip().lower() in (
        "1", "true", "yes", "on"
    ):
        return
    if os.getenv("PYTEST_CURRENT_TEST") and not os.getenv(
        "GOOGLE_INDEXING_PERSIST_IN_TESTS"
    ):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_flush_to_store())


def get_stats() -> Dict[str, Any]:
    """Synchronous snapshot of today's in-memory counters + config. Does
    NOT include yesterday's totals — for the admin dashboard history view,
    use the async `get_stats_with_history` instead."""
    with _stats_lock:
        _roll_day_if_needed()
        snapshot = dict(_stats)
    limit = _daily_limit()
    snapshot["daily_limit"] = limit
    snapshot["quota_remaining"] = max(0, limit - snapshot.get("sent", 0))
    snapshot["enabled"] = _enabled()
    snapshot["service_account_loaded"] = _load_service_account() is not None
    snapshot["service_account_error"] = _sa_load_error
    # Task #246 — per-tier quota snapshot for the admin dashboard.
    with _tier_lock:
        _roll_tier_if_needed()
        tier_snapshot = dict(_tier_sent)
    snapshot["tier_sent"] = tier_snapshot
    snapshot["tier_limits"] = {t: _tier_daily_limit(t) for t in (1, 2, 3)}
    return snapshot


async def get_stats_with_history() -> Dict[str, Any]:
    """Async variant that first hydrates today's counters from the store
    (so a fresh process shows correct values immediately), then pulls
    yesterday's persisted totals for the admin dashboard history panel."""
    await _ensure_loaded()
    snapshot = get_stats()
    # Fetch yesterday's row. Best-effort; Mongo unavailability returns None.
    from datetime import timedelta
    yesterday_key = (
        datetime.now(timezone.utc) - timedelta(days=1)
    ).strftime("%Y-%m-%d")
    yesterday: Optional[Dict[str, Any]] = None
    try:
        from deps import db, is_mongo_available
        if await is_mongo_available():
            doc = await db[_STORE_COLLECTION].find_one(
                {"day": yesterday_key}, {"_id": 0},
            )
            if doc:
                yesterday = {k: int(doc.get(k, 0)) for k in _COUNTER_KEYS}
                yesterday["day"] = yesterday_key
    except Exception as e:
        logger.debug("google_indexing yesterday fetch failed: %s", e)
    snapshot["yesterday"] = yesterday
    return snapshot


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------

async def notify_url_updated(
    url: str,
    source: str = "content_fanout",
    tier: int = 0,
) -> Dict[str, Any]:
    """Send `type=URL_UPDATED` to the Indexing API for `url`. Returns a
    small result dict so callers (and tests) can verify what happened.
    Never raises.

    `tier` controls which priority bucket this submission draws from:
      0 (default) → auto-classify via `classify_url_tier(url)`
      1           → Tier-1 (notes + MCQs, top subjects — full daily quota)
      2           → Tier-2 (definitions + examples — up to 75% of quota)
      3           → Tier-3 (FAQs, older content — up to 25% of quota)

    Task #246: per-tier sub-caps prevent low-priority URLs from exhausting
    the daily quota before high-priority content is notified.
    """
    result: Dict[str, Any] = {"url": url, "status": "skipped", "reason": ""}
    if not url or not isinstance(url, str):
        result["reason"] = "empty_url"
        return result
    # Hydrate today's counters from Mongo on first call per day per process
    # so the quota cap survives a restart. Never raises.
    await _ensure_loaded()
    if not _enabled():
        result["reason"] = "disabled"
        _bump("skipped_disabled")
        return result
    info = _load_service_account()
    if not info:
        result["reason"] = "no_service_account"
        _bump("skipped_disabled")
        return result

    # Task #246: classify tier if not provided, then check per-tier sub-cap.
    effective_tier = tier if tier in (1, 2, 3) else classify_url_tier(url)
    result["tier"] = effective_tier
    if not _under_tier_quota_and_reserve(effective_tier):
        result["status"] = "quota_blocked"
        result["reason"] = f"tier_{effective_tier}_limit_reached"
        # Tier-cap blocks count toward `quota_blocks` so admin dashboards
        # don't undercount blocked submissions whenever the tier sub-cap
        # tripped before (or together with) the global daily-limit cap.
        with _stats_lock:
            _roll_day_if_needed()
            _stats["quota_blocks"] = _stats.get("quota_blocks", 0) + 1
            _day_now = _stats.get("day", "")
            _sent_now = int(_stats.get("sent", 0))
        _schedule_flush()
        # Only escalate to the daily-quota ops alert when the tier block
        # *coincides* with the global cap being exhausted (sent >= limit).
        # That covers Tier-1 (whose sub-cap equals the daily limit) and
        # any tier whose cumulative submissions caught up to the global
        # cap — both cases the "Google Indexing daily quota exhausted"
        # alert message describes accurately. A pure sub-cap-only block
        # (e.g. Tier-3 25% cap hit while Tier-1 still has capacity) is
        # observable on the dashboard via `quota_blocks` but must NOT
        # consume the once-per-day alert slot, otherwise an early sub-cap
        # event could suppress a later true daily-cap alert via dedupe.
        _limit = _daily_limit()
        if _sent_now >= _limit:
            _schedule_quota_alert(_day_now, _sent_now, _limit)
        return result

    if not await _reserve_quota(_daily_limit()):
        result["status"] = "quota_blocked"
        result["reason"] = "daily_limit_reached"
        # Roll back the tier reservation since the global quota denied us.
        with _tier_lock:
            _roll_tier_if_needed()
            _tier_sent[effective_tier] = max(0, _tier_sent.get(effective_tier, 0) - 1)
        return result

    token = await _get_cached_token()
    if not token:
        result["status"] = "error"
        result["reason"] = "token_mint_failed"
        _bump("errors")
        return result

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
            resp = await client.post(
                INDEXING_API_URL,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={"url": url, "type": "URL_UPDATED"},
            )
    except Exception as e:
        logger.warning(
            "google_indexing_client: publish error url=%s err=%s", url, e,
        )
        result["status"] = "error"
        result["reason"] = f"{type(e).__name__}"
        _bump("errors")
        return result

    _record_status_code(resp.status_code)
    result["http_status"] = resp.status_code
    result["source"] = source
    if 200 <= resp.status_code < 300:
        result["status"] = "ok"
    elif resp.status_code in (401, 403):
        # Token likely expired mid-call; force a mint on next submission.
        with _token_lock:
            global _cached_token_expires_at
            _cached_token_expires_at = 0.0
        result["status"] = "auth_error"
        result["reason"] = resp.text[:200]
        logger.warning(
            "google_indexing_client: auth error %s for %s: %s",
            resp.status_code, url, resp.text[:200],
        )
    elif resp.status_code == 429:
        result["status"] = "quota_error"
        result["reason"] = "google_quota_exceeded"
        logger.warning("google_indexing_client: Google quota 429 for %s", url)
    else:
        result["status"] = "error"
        result["reason"] = f"http_{resp.status_code}"
        logger.warning(
            "google_indexing_client: publish non-2xx %s url=%s body=%s",
            resp.status_code, url, resp.text[:200],
        )
    return result


async def ping_sitemap(sitemap_url: str = "https://syrabit.ai/sitemap-index.xml") -> Dict[str, Any]:
    """Ping Google's legacy sitemap-ping endpoint. Unauthenticated, free,
    documented at developers.google.com/search/docs. Never raises."""
    result: Dict[str, Any] = {
        "sitemap": sitemap_url,
        "status": "skipped",
        "reason": "",
    }
    await _ensure_loaded()
    if not _enabled():
        result["reason"] = "disabled"
        return result
    ping_url = SITEMAP_PING_URL_TPL.format(sitemap=quote(sitemap_url, safe=""))
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
            resp = await client.get(ping_url)
    except Exception as e:
        logger.warning("google_indexing_client: sitemap ping error: %s", e)
        _bump("sitemap_ping_errors")
        result["status"] = "error"
        result["reason"] = f"{type(e).__name__}"
        return result
    _bump("sitemap_ping_sent")
    result["http_status"] = resp.status_code
    if 200 <= resp.status_code < 300:
        _bump("sitemap_ping_2xx")
        result["status"] = "ok"
    else:
        result["status"] = "error"
        result["reason"] = f"http_{resp.status_code}"
        logger.info(
            "google_indexing_client: sitemap ping non-2xx %s for %s",
            resp.status_code, sitemap_url,
        )
    return result
