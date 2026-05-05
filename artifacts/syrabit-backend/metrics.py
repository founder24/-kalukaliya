"""Syrabit.ai — Metrics collection, health check infrastructure."""
import time as _time_mod, threading as _threading, logging, asyncio, os, uuid
from typing import Dict
from collections import defaultdict as _defaultdict, deque as _deque
from datetime import datetime, timezone, timedelta
import httpx
import deps as _deps_mod
from deps import db, supa
from config import SUPABASE_URL, SUPABASE_SERVICE_KEY, SUPABASE_ANON_KEY, EMAIL_FROM, LLM_MODEL
import cache as _cache_mod
from cache import _redis_get_search

logger = logging.getLogger(__name__)

__all__ = [
    "_ALERT_COOLDOWN_S", "_ALERT_THRESHOLDS", "_ALERT_THRESHOLDS_DEFAULT",
    "_ALERT_EXPIRATION_DEFAULT", "_alert_expiration",
    "_NOTIFICATION_CHANNELS_DEFAULT", "_notification_channels",
    "_CHANNEL_STATUS_DEFAULT", "_channel_status",
    "_HEALTH_CACHE_TTL_S",
    "_METRICS_HISTORY_MAX", "_MetricsStore", "_alert_last_fired", "_alerting_loop",
    "_bg_health_loop", "_cache_stats_log_counter", "_check_health_deps",
    "_dispatch_alert", "_health_deps_cache", "_health_deps_cache_at",
    "_load_alert_settings", "_auto_expire_alerts",
    "_metrics", "_metrics_history", "_metrics_history_lock",
    "_snapshot_metrics", "_start_metrics_collector", "_startup_time",
    "record_assamese_refresh_success", "get_assamese_refresh_age_seconds",
    "_asm_last_refresh_at",
    "record_credit_fallback", "get_credit_fallback_stats",
    "_credit_fallback_window",
    "record_anon_quota_exhausted", "record_signup_with_device",
    "get_anon_quota_exhausted_stats", "backfill_anon_quota_exhausted_today",
    "get_anon_quota_exhausted_recent", "get_anon_quota_exhausted_top_devices",
    "get_anon_quota_exhausted_weekly_trend",
]

_startup_time = _time_mod.time()

# ── Task #432: Assamese-purity override refresh heartbeat ─────────────────
# Each gunicorn worker runs `_assamese_purity_refresh_loop` (15s cadence)
# to pick up override PATCH/DELETE made on sibling workers. If that loop
# silently dies (mongo auth error, motor exception spiral, etc.) the only
# signal today is sporadic warnings — on-call won't notice until a
# customer complains. We record a per-worker timestamp of the last
# successful refresh tick and the alerting loop pages on-call when it
# falls behind the configured budget (default 60s = 4× the poll cadence).
#
# Initialised to startup time so the staleness window starts ticking from
# boot — that way a worker that crashes the loop on its very first tick
# still trips the alarm after `assamese_refresh_stale_seconds`.
_asm_last_refresh_at: float = _startup_time


def record_assamese_refresh_success() -> None:
    """Called by `_assamese_purity_refresh_loop` after each successful
    mongo poll. Updates this worker's heartbeat timestamp so the
    alerting loop can detect a stalled refresh loop."""
    global _asm_last_refresh_at
    _asm_last_refresh_at = _time_mod.time()


def get_assamese_refresh_age_seconds() -> float:
    """Seconds since this worker last successfully refreshed the
    Assamese-purity override from mongo. Exposed for the admin
    dashboard / health endpoint and consumed by `_alerting_loop`."""
    return max(0.0, _time_mod.time() - _asm_last_refresh_at)

# ── Task #769: credit-deduct fallback observability ───────────────────────
# `db_ops.atomic_deduct_credit` tries Postgres first, then a Redis Lua
# script, then Supabase. Each fallback only logs a warning, so a broken
# Postgres can keep deducting credits via Redis for hours with nobody
# noticing — and the Redis path's daily counter is seeded from a
# possibly stale read, which can drift from the real ledger. We track
# every fallback event in a 10-minute rolling window so the alerting
# loop can page on-call when the rate stays above the configured
# threshold (default 5/min sustained over a 5 min window).
_CREDIT_FALLBACK_WINDOW_SECONDS = 600
_credit_fallback_window: list = []   # list of (ts_float, path: "redis"|"supabase")
_credit_fallback_lock = _threading.Lock()


def record_credit_fallback(path: str) -> None:
    """Record a single credit-deduct fallback event.

    ``path`` is one of:
      - ``"redis"``   — Postgres path was unavailable or raised; the
                        Redis Lua atomic-deduct path was used.
      - ``"supabase"`` — Postgres failed AND Redis failed (or wasn't
                        configured); the last-resort Supabase path
                        was used.

    Consumed by ``_alerting_loop`` to page on-call when the rate stays
    above ``_ALERT_THRESHOLDS["credit_deduct_fallback_per_min"]``."""
    if path not in ("redis", "supabase"):
        return
    now = _time_mod.time()
    cutoff = now - _CREDIT_FALLBACK_WINDOW_SECONDS
    with _credit_fallback_lock:
        _credit_fallback_window.append((now, path))
        # Trim entries older than the rolling window. The list is
        # append-only with monotonically increasing timestamps, so we
        # can drop the leading prefix in one slice instead of scanning.
        keep_from = 0
        for i, (ts, _) in enumerate(_credit_fallback_window):
            if ts > cutoff:
                keep_from = i
                break
        else:
            keep_from = len(_credit_fallback_window)
        if keep_from > 0:
            del _credit_fallback_window[:keep_from]


def get_credit_fallback_stats(window_seconds: int = 300) -> dict:
    """Return rolling-window stats over the last ``window_seconds`` of
    credit-deduct fallback events. Defaults to 5 min so the alerting
    loop can compute "sustained over 5 minutes" cheaply on every tick.
    """
    if window_seconds <= 0:
        window_seconds = 300
    now = _time_mod.time()
    cutoff = now - window_seconds
    by_path = {"redis": 0, "supabase": 0}
    total = 0
    with _credit_fallback_lock:
        for ts, p in _credit_fallback_window:
            if ts > cutoff:
                total += 1
                if p in by_path:
                    by_path[p] += 1
    rate_per_min = round(total / max(1.0, window_seconds / 60.0), 2)
    return {
        "total": total,
        "by_path": by_path,
        "rate_per_min": rate_per_min,
        "window_seconds": window_seconds,
    }

# ── Task #798: anonymous-quota exhaustion observability ───────────────────
# We currently have no signal for how many anonymous students hit the
# 30/day per-device cap each day, nor what fraction of them sign up vs
# bounce after the 429. Without that signal we can't tell whether 30 is
# the right number — too low loses students, too high cannibalises
# sign-up conversions. This block emits a `chat.anon_quota_exhausted`
# counter every time the per-device cap fires (once per device per day,
# so a hammering script doesn't inflate the metric), plus a thin Redis
# join so signups in the next 48h can be matched back to a previously-
# exhausted device cookie. The admin chart in routes/admin_advanced.py
# (`/admin/chat/anon-quota-exhausted`) renders both as a sparkline.
import hashlib as _hashlib_anon

# Keep ~14 days of per-event records so the admin chart's default 7-day
# window has historical context and a single missing day doesn't flatten
# the line. The list is appended-only with monotonically increasing
# timestamps, so trimming is a single slice on each insert.
_ANON_EXHAUST_HISTORY_SECONDS = 14 * 24 * 3600
_anon_exhaust_window: list = []   # list of dicts: ts, plan_target, dow, hour, token_hash, country, asn
_anon_exhaust_lock = _threading.Lock()
# ── Task #808: per-event ring buffer for the admin "Recent" tab ──────────
# Keeps the last N exhaustion events with hashed device id, country/ASN,
# and timestamp so support can answer "I keep getting blocked" tickets in
# seconds (look up the device hash in the support ticket against the
# Recent feed) instead of guessing from a daily aggregate. Bounded so a
# burst can't grow unbounded; ~200 is roughly half a day at our current
# wall-hit volume which keeps the feed useful without hoarding memory.
_ANON_EXHAUST_RECENT_MAX = 200
_anon_exhaust_recent: _deque = _deque(maxlen=_ANON_EXHAUST_RECENT_MAX)
# Dedupe set keyed on (token_id_or_hash, day) so retry hammers from the
# same already-exhausted device don't double-count the metric.
_anon_exhaust_seen: set = set()
# In-memory mirror of devices that exhausted recently. Lets the
# signup-conversion join work even when Redis is offline (single worker
# only — the Redis-backed path is the cross-worker source of truth).
_anon_exhausted_devices: dict = {}   # token_id -> exhausted_at_ts
# Process-local set of *unique* exhausted-then-signed-up devices, keyed
# on the cookie token id so a device that re-attempts signup (e.g. a
# duplicate-email failure followed by a successful retry) only counts
# once toward the conversion ratio. The cross-worker source of truth
# is the Redis set `chat:anon_signup_after_exhaust_devices:<day>` read
# by `get_anon_quota_exhausted_stats`; this in-memory mirror keeps the
# numerator meaningful in single-worker / Redis-down deploys.
_anon_signup_after_exhaust_devices: set = set()
# Strict conversion window. The product question is "of devices that
# hit the wall today, what fraction signed up in the next 24 hours?",
# so we reject signup-conversion matches whose exhaustion event is
# older than this. The Redis sorted set's TTL is sized 2× the window
# (48h) to absorb late signups arriving just past the boundary on a
# different worker, but we still gate them on the zscore here.
_ANON_CONVERSION_WINDOW_SECONDS = 24 * 3600


def _anon_label_hash(s: str) -> str:
    """Stable short hash for log/window labels.

    The cookie token id is itself low-entropy enough that we don't want
    to spray it across structured logs (cf. PII/cookie-leak posture from
    Task #793). 12 hex chars of SHA-256 is enough to cluster repeat
    offenders for forensics without being reversible by an analyst with
    log-read access.
    """
    if not s:
        return ""
    return _hashlib_anon.sha256(s.encode("utf-8", errors="ignore")).hexdigest()[:12]


def _anon_redis_client():
    """Late-binding accessor for the shared redis client.

    `deps.redis_client` is rebound by `tests/_deps_stub.install_deps_stub`
    and by `_install_fake_redis` in the rate-limit test suite, so we have
    to look it up on every call rather than capturing it at import time.
    Returns None when Redis is unavailable so callers can degrade
    gracefully (in-memory fallback).
    """
    try:
        import deps as _d
        return getattr(_d, "redis_client", None)
    except Exception:
        return None


# Task #809 — durable per-day aggregate so the wall-hit chart survives
# gunicorn restarts (deploys, OOM kills, weekly memory recycle). The
# rolling in-memory window above resets to zero on every boot, and the
# `chat:anon_exhausted_devices:<day>` zsets only carry a 14-day TTL,
# so without this writer the dashboard's daily sparkline silently
# truncates after each restart and weekly/monthly trends are
# impossible to compute without scraping logs.
#
# Storage shape: per-day Redis HASH `chat:anon_exhausted_daily_agg:<day>`
# with fields:
#   • events  — INCR'd once per fired metric (== unique devices that
#               day, since record_anon_quota_exhausted dedupes per
#               (token, day); the field name future-proofs the schema
#               for a separate raw-retry counter later);
#   • unique  — snapshot of the same-day zset's ZCARD after each push,
#               so the value is correct even if `events` ever
#               outgrows uniques (e.g. a future patch loosens the
#               dedupe window).
#
# TTL is sized for ~13 months of history (52 weekly buckets × 7 days
# + buffer), comfortably covering the longest charts we expose today
# and giving plenty of headroom for quarterly capacity reviews.
_ANON_EXHAUST_DAILY_AGG_TTL_SECONDS = 400 * 24 * 3600


def _anon_exhaust_daily_agg_key(day: str) -> str:
    return f"chat:anon_exhausted_daily_agg:{day}"


def _persist_anon_exhaust_daily_agg(day: str) -> None:
    """Best-effort write-through for the durable daily aggregate.

    Called from `record_anon_quota_exhausted` after the per-day
    sorted-set push. Failures are swallowed (debug-logged) — the
    in-memory rolling window remains the local fallback and the
    chart endpoint already declares ``data_source='memory_fallback'``
    when Redis is unreachable, so a write here failing only costs
    us cross-restart durability for that one event.
    """
    rc = _anon_redis_client()
    if rc is None:
        return
    try:
        key = _anon_exhaust_daily_agg_key(day)
        rc.hincrby(key, "events", 1)
        try:
            zset_key = f"chat:anon_exhausted_devices:{day}"
            unique_n = int(rc.zcard(zset_key) or 0)
            if unique_n:
                rc.hset(key, "unique", unique_n)
        except Exception:
            # Snapshot is best-effort; `events` alone is still useful.
            pass
        rc.expire(key, _ANON_EXHAUST_DAILY_AGG_TTL_SECONDS)
    except Exception as _e:
        logger.debug("_persist_anon_exhaust_daily_agg failed: %s", _e)


def _read_anon_exhaust_daily_agg(day: str) -> dict | None:
    """Return ``{'events': int, 'unique': int}`` or None if absent.

    Tolerates both ``decode_responses=True`` (str keys, fakeredis
    default) and the bytes-key real-Redis configuration so the
    helper is interchangeable across the test suite and prod.
    """
    rc = _anon_redis_client()
    if rc is None:
        return None
    try:
        raw = rc.hgetall(_anon_exhaust_daily_agg_key(day))
        if not raw:
            return None

        def _g(k: str) -> int:
            v = raw.get(k)
            if v is None:
                v = raw.get(k.encode("ascii"))
            if isinstance(v, bytes):
                v = v.decode("ascii", errors="ignore")
            try:
                return int(v or 0)
            except (TypeError, ValueError):
                return 0

        return {"events": _g("events"), "unique": _g("unique")}
    except Exception as _e:
        logger.debug("_read_anon_exhaust_daily_agg failed: %s", _e)
        return None


def record_anon_quota_exhausted(
    token_id: str, ip: str = "", plan_target: str = "free",
    country: str = "", asn: str = "",
) -> bool:
    """Emit the `chat.anon_quota_exhausted` counter.

    Called from `auth_deps.rate_limit_chat_optional` whenever a device
    has used all of its per-day free messages and the next request is
    about to be rejected with HTTP 429. We dedupe on (token_id, day)
    so the metric measures *unique devices that hit the wall*, not
    *post-exhaustion retry hammer rate* — those are very different
    product signals and we want the former for cap-tuning decisions.

    Side effects:
      1. Structured INFO log with plan_target / day-of-week / hour /
         hashed token+ip labels (StatsD-equivalent for log-shipping
         pipelines that consume key=value INFO records).
      2. Append to the in-memory rolling 14-day window so the admin
         chart endpoint can read it without a Redis round-trip.
      3. Append to the bounded "recent events" ring buffer
         (Task #808) so the admin "Recent" tab can show support
         the last N wall-hits with hashed device id, country, ASN
         and timestamp without rescanning the 14-day window.
      4. Push the token_id into a date-bucketed Redis sorted set with
         a 48h TTL so the signup-conversion join in
         `record_signup_with_device` can find it from any worker.

    Parameters
    ----------
    country, asn : str
        Optional Cloudflare-supplied tags (``cf-ipcountry`` and the
        equivalent ASN header). Stored on the event so support can
        spot patterns like "all wall-hits today are from one school's
        NAT range" or "this angry-ticket device hashed XYZ kept
        getting blocked from country=IN, ASN=AS24560". Empty string
        when the request didn't traverse Cloudflare or the headers
        weren't propagated.

    Returns
    -------
    True  — first time this device was recorded today (metric emitted).
    False — already counted today (deduped, no-op).
    """
    if plan_target not in ("free", "pro", "max"):
        plan_target = "free"
    now = _time_mod.time()
    dt = datetime.fromtimestamp(now, tz=timezone.utc)
    day = dt.strftime("%Y-%m-%d")
    dow = dt.strftime("%a")
    hour = dt.hour
    th = _anon_label_hash(token_id or "")
    ih = _anon_label_hash(ip or "")
    # Normalise country/ASN to short, log-safe strings. Cloudflare's
    # ``cf-ipcountry`` is a 2-letter ISO code (or "XX"/"T1"); ASN comes
    # through as either "AS12345" or just "12345" depending on header
    # source. We keep them as-is but truncate to defend against header
    # spoofing inflating our in-memory state.
    country_s = (country or "").strip()[:8]
    asn_s = (asn or "").strip()[:16]

    # Dedupe key: hashed token + day. Falls back to the hashed IP +
    # second-resolution timestamp when no token is available so we
    # don't accidentally collapse all anonymous-IP events into one.
    dedupe_key = f"{th}:{day}" if token_id else f"_:{ih}:{int(now)}"

    with _anon_exhaust_lock:
        if dedupe_key in _anon_exhaust_seen:
            return False
        _anon_exhaust_seen.add(dedupe_key)
        # Trim the dedupe set when it gets large so we don't grow
        # unboundedly across days. We rebuild from the rolling window
        # so a device that exhausts today still won't double-count.
        if len(_anon_exhaust_seen) > 5000:
            cutoff_ts = now - _ANON_EXHAUST_HISTORY_SECONDS
            keep = {
                f"{e['token_hash']}:{datetime.fromtimestamp(e['ts'], tz=timezone.utc).strftime('%Y-%m-%d')}"
                for e in _anon_exhaust_window
                if e["ts"] > cutoff_ts and e.get("token_hash")
            }
            keep.add(dedupe_key)
            _anon_exhaust_seen.clear()
            _anon_exhaust_seen.update(keep)

        _anon_exhaust_window.append({
            "ts": now,
            "plan_target": plan_target,
            "dow": dow,
            "hour": hour,
            "token_hash": th,
            "country": country_s,
            "asn": asn_s,
        })
        # Task #808 — also push onto the bounded recent-events ring
        # buffer. The deque's ``maxlen`` evicts oldest events for
        # us so we never have to scan the rolling window to render
        # the "Recent" tab. The dedupe gate above means a single
        # device contributes at most one entry per day to this
        # buffer, matching the daily-aggregate semantics.
        _anon_exhaust_recent.append({
            "ts": now,
            "plan_target": plan_target,
            "dow": dow,
            "hour": hour,
            "token_hash": th,
            "country": country_s,
            "asn": asn_s,
        })
        # Trim entries older than the configured rolling window in a
        # single slice (the list is monotonically increasing).
        cutoff = now - _ANON_EXHAUST_HISTORY_SECONDS
        keep_from = 0
        for i, e in enumerate(_anon_exhaust_window):
            if e["ts"] > cutoff:
                keep_from = i
                break
        else:
            keep_from = len(_anon_exhaust_window)
        if keep_from > 0:
            del _anon_exhaust_window[:keep_from]

        if token_id:
            _anon_exhausted_devices[token_id] = now
            # Trim entries older than 48h (the signup-conversion window).
            cutoff_48h = now - 48 * 3600
            stale = [t for t, ts in _anon_exhausted_devices.items() if ts < cutoff_48h]
            for t in stale:
                _anon_exhausted_devices.pop(t, None)

    logger.info(
        "chat.anon_quota_exhausted plan_target=%s dow=%s hour=%02d "
        "token_hash=%s ip_hash=%s country=%s asn=%s",
        plan_target, dow, hour, th, ih, country_s or "-", asn_s or "-",
    )

    # Best-effort cross-worker push so signups on a different gunicorn
    # worker can still match this device, AND so the admin chart's
    # denominator (unique devices that hit the wall per day) can be
    # computed from the same Redis source as the numerator (signup-
    # after-exhaust). Without that source-alignment a multi-worker
    # deployment would mix per-worker exhausted counts with cross-
    # worker signup counts and could compute conversion ratios > 100%.
    #
    # TTL is sized for the chart window (14d), not the conversion
    # window (24h). The 24h gate is enforced in code at signup-join
    # time via `_ANON_CONVERSION_WINDOW_SECONDS`, so a 14d TTL here
    # only widens the historical denominator — it doesn't bleed
    # late signups into the conversion ratio.
    rc = _anon_redis_client()
    if rc is not None and token_id:
        # Task #809 — gate the durable aggregate's `events`
        # increment on whether ZADD actually inserted a new
        # member. ZADD NX returns 1 only the first time a given
        # (token, day) lands in the zset, even across workers, so
        # `events` cannot drift above `unique` no matter how many
        # gunicorn workers process the same device's 30th request
        # of the day. Without this, per-process dedupe via
        # `_anon_exhaust_seen` does not protect cross-worker
        # writers, and the daily count would slowly inflate as
        # workers each see the same device.
        added = 0
        try:
            key = f"chat:anon_exhausted_devices:{day}"
            added = int(rc.zadd(key, {token_id: now}, nx=True) or 0)
            rc.expire(key, _ANON_EXHAUST_HISTORY_SECONDS)
        except Exception as _e:
            logger.debug("record_anon_quota_exhausted redis push failed: %s", _e)

        # Only persist when this worker observed a brand-new
        # device-day. The unique-snapshot inside the persist helper
        # still reads ZCARD so the `unique` field self-corrects
        # even if a future caller forgets to gate the increment.
        # Best-effort: failures are debug-logged and never raise.
        if added:
            _persist_anon_exhaust_daily_agg(day)
    return True


def record_signup_with_device(token_id: str) -> bool:
    """Pair a successful new-account signup with a prior exhaustion event.

    Called from `routes.auth.signup` and `routes.auth.google_auth` after
    a brand-new account is created (NOT for returning users logging in).
    Looks up the device cookie's token id in the in-memory mirror and
    in today's + yesterday's Redis sorted sets, but only counts the
    match if the original exhaustion event was within
    ``_ANON_CONVERSION_WINDOW_SECONDS`` (24h). The Redis sorted set's
    48h TTL gives us a 1× safety buffer for late-arriving signups
    written on a different worker, but the 24h gate here is what the
    product question actually asks: "next-24h sign-up conversion among
    exhausted devices".

    On match we add the device hash to a process-local set and to a
    per-day Redis SET (note: SET, not counter — we want unique devices,
    not raw signup events) so the admin chart can read it back across
    workers without double-counting a device that retried signup
    after a duplicate-email error.

    Returns
    -------
    True  — this signup was preceded by a per-device cap exhaustion
            within the last 24h (counts toward the conversion ratio).
    False — no recent exhaustion for this device cookie (organic signup).
    """
    if not token_id:
        return False
    now = _time_mod.time()
    matched = False
    with _anon_exhaust_lock:
        ts = _anon_exhausted_devices.get(token_id)
        if ts is not None and (now - ts) <= _ANON_CONVERSION_WINDOW_SECONDS:
            matched = True

    if not matched:
        rc = _anon_redis_client()
        if rc is not None:
            try:
                today = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")
                yesterday = datetime.fromtimestamp(now - 86400, tz=timezone.utc).strftime("%Y-%m-%d")
                for day in (today, yesterday):
                    key = f"chat:anon_exhausted_devices:{day}"
                    score = rc.zscore(key, token_id)
                    if score is None:
                        continue
                    try:
                        score_f = float(score)
                    except (TypeError, ValueError):
                        continue
                    # Strict 24h join: a signup at T+25h should not be
                    # counted as a wall-hit conversion just because the
                    # zset's 48h TTL hasn't expired the entry yet.
                    if (now - score_f) <= _ANON_CONVERSION_WINDOW_SECONDS:
                        matched = True
                        break
            except Exception as _e:
                logger.debug("record_signup_with_device redis lookup failed: %s", _e)

    if matched:
        th = _anon_label_hash(token_id)
        with _anon_exhaust_lock:
            _anon_signup_after_exhaust_devices.add(th)
        rc = _anon_redis_client()
        if rc is not None:
            try:
                day = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")
                key = f"chat:anon_signup_after_exhaust_devices:{day}"
                rc.sadd(key, th)
                rc.expire(key, 14 * 86400)
            except Exception as _e:
                logger.debug("record_signup_with_device redis sadd failed: %s", _e)
        logger.info(
            "chat.anon_signup_after_exhaust matched token_hash=%s",
            th,
        )
    return matched


def get_anon_quota_exhausted_stats(days: int = 7) -> dict:
    """Compute the admin-chart payload for `chat.anon_quota_exhausted`.

    The chart's headline KPIs (``daily``, ``unique_devices_exhausted``,
    ``signup_after_exhaust``, ``conversion_pct``) are read from Redis
    so that numerator and denominator come from the **same**
    cross-worker source. Mixing per-worker counts with cross-worker
    counts in a multi-gunicorn deployment can otherwise produce
    inflated and unstable conversion rates (including >100%) depending
    on which worker serves this endpoint.

    Source of truth:
      • denominator (``unique_devices_exhausted``, ``daily``) — sum of
        ``ZCARD chat:anon_exhausted_devices:<day>`` across the window
      • numerator (``signup_after_exhaust``)               — sum of
        ``SCARD chat:anon_signup_after_exhaust_devices:<day>``

    Conversion semantics: this is **window-level** conversion, not
    cohort-by-exhaustion-day conversion. The numerator buckets the
    signup by *signup day*, so a device that exhausts on day N at
    23:00 and signs up on day N+1 at 02:00 (still inside the 24h
    code-level join) is counted in day N+1's signup bucket but day
    N's exhaustion bucket. For the headline window-aggregated
    ratio this is exactly what we want; if the dashboard later
    needs per-day cohort fidelity (`% of day-N cohort that
    converted within 24h`) the numerator key would have to switch
    to exhaustion-day. Documented here so the chart copy stays
    honest and the next iteration knows what to change.

    The in-memory rolling window is still used for the by-hour /
    by-day-of-week distributions (which are sub-views and acceptable
    as per-worker samples), and as a fallback when Redis is offline.
    The ``data_source`` field in the payload tells the dashboard
    whether it is showing fully cross-worker data or the degraded
    single-worker view.
    """
    days = max(1, int(days))
    now = _time_mod.time()
    cutoff = now - days * 86400

    # ── Local memory (always computed; used for label histograms and
    # as the fallback when Redis is unavailable). ──────────────────
    by_day_local: dict = {}
    by_hour: dict = {h: 0 for h in range(24)}
    by_dow: dict = {d: 0 for d in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")}
    local_total = 0
    local_unique_tokens: set = set()
    with _anon_exhaust_lock:
        for e in _anon_exhaust_window:
            if e["ts"] < cutoff:
                continue
            d = datetime.fromtimestamp(e["ts"], tz=timezone.utc).strftime("%Y-%m-%d")
            by_day_local[d] = by_day_local.get(d, 0) + 1
            by_hour[e["hour"]] = by_hour.get(e["hour"], 0) + 1
            by_dow[e["dow"]] = by_dow.get(e["dow"], 0) + 1
            local_total += 1
            if e.get("token_hash"):
                local_unique_tokens.add(e["token_hash"])
        local_signup_devices = set(_anon_signup_after_exhaust_devices)

    # ── Redis (cross-worker headline KPIs). ──────────────────────────
    # We pull denominator AND numerator from the same source so the
    # ratio can never compute above 100% due to source mismatch.
    rc = _anon_redis_client()
    redis_ok = False
    by_day_redis: dict = {}
    redis_unique_total = 0
    redis_signup_total = 0
    if rc is not None:
        try:
            for n in range(days):
                day = datetime.fromtimestamp(now - n * 86400, tz=timezone.utc).strftime("%Y-%m-%d")
                exh_key = f"chat:anon_exhausted_devices:{day}"
                sup_key = f"chat:anon_signup_after_exhaust_devices:{day}"
                # Task #809 — prefer the durable per-day aggregate
                # (HASH `chat:anon_exhausted_daily_agg:<day>`) which
                # survives gunicorn restarts and outlives the
                # 14-day TTL on the zset above. Fall back to ZCARD
                # for the (rare) window where the writer raced the
                # reader and the aggregate hasn't been written yet,
                # OR for legacy days that pre-date this task.
                exh_count = 0
                agg = _read_anon_exhaust_daily_agg(day)
                if agg:
                    # Prefer `unique` (canonical: ZCARD-derived,
                    # always reflects true distinct devices). Use
                    # `events` only as a fallback for the rare
                    # window where the writer hasn't taken the
                    # `unique` snapshot yet — never `max(...)`,
                    # because in multi-worker setups `events` can
                    # transiently exceed `unique` if the
                    # ZADD-NX gate is bypassed by a future caller.
                    exh_count = int(agg.get("unique") or 0)
                    if not exh_count:
                        exh_count = int(agg.get("events") or 0)
                if not exh_count:
                    try:
                        exh_count = int(rc.zcard(exh_key) or 0)
                    except (TypeError, ValueError):
                        exh_count = 0
                try:
                    sup_count = int(rc.scard(sup_key) or 0)
                except (TypeError, ValueError):
                    sup_count = 0
                if exh_count:
                    by_day_redis[day] = exh_count
                redis_unique_total += exh_count
                redis_signup_total += sup_count
            redis_ok = True
        except Exception as _e:
            logger.debug("get_anon_quota_exhausted_stats redis read failed: %s", _e)

    if redis_ok:
        data_source = "redis"
        by_day = by_day_redis
        unique_count = redis_unique_total
        signup_after_exhaust = redis_signup_total
        # Total events (sum of per-day uniques) — a device that hits
        # the wall on day N and again on day N+1 is correctly counted
        # twice here since the per-day zsets are independent.
        total = redis_unique_total
    else:
        data_source = "memory_fallback"
        by_day = by_day_local
        unique_count = len(local_unique_tokens)
        signup_after_exhaust = len(local_signup_devices)
        total = local_total

    # Defensive cap: even with source-aligned data, a freshly-pushed
    # exhaustion event whose corresponding signup landed in a
    # previous-day's set could in theory tip the ratio. Clamping at
    # 100% means the chart stays in [0, 100] no matter what.
    raw_pct = (signup_after_exhaust / max(1, unique_count) * 100) if unique_count else 0.0
    conversion_pct = round(min(100.0, raw_pct), 2)

    daily = [{"date": d, "exhausted": by_day[d]} for d in sorted(by_day.keys())]
    return {
        "period_days": days,
        "total_exhausted": total,
        "unique_devices_exhausted": unique_count,
        "signup_after_exhaust": signup_after_exhaust,
        "conversion_pct": conversion_pct,
        "daily": daily,
        "by_hour": by_hour,
        "by_day_of_week": by_dow,
        "has_data": total > 0,
        "data_source": data_source,
    }


def get_anon_quota_exhausted_recent(limit: int = 200) -> list:
    """Return the most-recent per-device exhaustion events (Task #808).

    Powers the admin chart's "Recent" tab so support can investigate
    angry "I keep getting blocked" tickets in seconds — look the
    user's hashed device id up against this feed and see whether
    they actually hit the cap, what country/ASN they came from, and
    when. The feed is fed by `record_anon_quota_exhausted`'s ring
    buffer, so it is naturally bounded (oldest evicted) and ordered.

    Each event has the same shape used internally:
      ``ts``         (float, unix seconds, UTC)
      ``token_hash`` (12-hex sha256 prefix of the device cookie id)
      ``country``    (cf-ipcountry, e.g. "IN" / "US"; "" if absent)
      ``asn``        (e.g. "AS24560"; "" if not surfaced)
      ``hour``       (UTC hour-of-day at the moment of the event)
      ``dow``        (UTC day-of-week, "Mon".."Sun")
      ``plan_target``("free" today; reserved for future tiers)

    The returned list is sorted **newest-first** and capped at
    ``limit`` (which itself is clamped at the buffer's max so a
    careless caller can't ask for more than we keep).
    """
    if limit <= 0:
        return []
    cap = min(limit, _ANON_EXHAUST_RECENT_MAX)
    with _anon_exhaust_lock:
        # Snapshot under the lock so a concurrent recorder can't
        # mutate the deque while we're reversing it. ``list(...)``
        # on a deque is O(n) and avoids holding the lock during the
        # JSON serialisation downstream.
        snapshot = list(_anon_exhaust_recent)
    snapshot.reverse()
    return snapshot[:cap]


def get_anon_quota_exhausted_top_devices(days: int = 7, top_n: int = 10) -> list:
    """Top device hashes by hit count over the last ``days`` (Task #808).

    Built from the in-memory rolling window. Per-device dedupe inside
    `record_anon_quota_exhausted` already collapses repeat-retry
    hammers to one event per device per day, so an entry of "5 hits
    over 7 days" here actually means "this device hit the daily cap
    on 5 distinct days" — the most useful signal for spotting a
    chronic offender vs. a one-time student.

    Returned list (sorted by hits desc, then most-recent first) of:
      ``token_hash``, ``hits`` (int), ``last_seen`` (unix seconds),
      ``country`` (most-recent country tag seen for this device),
      ``asn`` (likewise).

    Gated behind a query flag in the admin endpoint so we don't pay
    the O(N_window) scan on every dashboard load.
    """
    days = max(1, int(days))
    top_n = max(1, int(top_n))
    cutoff = _time_mod.time() - days * 86400
    counts: Dict[str, dict] = {}
    with _anon_exhaust_lock:
        for e in _anon_exhaust_window:
            if e["ts"] < cutoff:
                continue
            th = e.get("token_hash") or ""
            if not th:
                continue
            row = counts.get(th)
            if row is None:
                counts[th] = {
                    "token_hash": th,
                    "hits": 1,
                    "last_seen": e["ts"],
                    "country": e.get("country", ""),
                    "asn": e.get("asn", ""),
                }
            else:
                row["hits"] += 1
                # Always remember the *most recent* country/ASN so a
                # device that roamed (mobile -> wifi) is shown by its
                # current location rather than its first.
                if e["ts"] >= row["last_seen"]:
                    row["last_seen"] = e["ts"]
                    if e.get("country"):
                        row["country"] = e["country"]
                    if e.get("asn"):
                        row["asn"] = e["asn"]
    # Hits desc, then most-recent first as a stable tiebreaker.
    ranked = sorted(
        counts.values(),
        key=lambda r: (-r["hits"], -r["last_seen"]),
    )
    return ranked[:top_n]


def get_anon_quota_exhausted_weekly_trend(weeks: int = 12) -> list:
    """Return wall-hits grouped by ISO week for the last ``weeks`` weeks.

    Reads the durable per-day aggregate written by
    `_persist_anon_exhaust_daily_agg` (TTL ~13 months), so this
    survives gunicorn restarts and goes back well beyond the 14-day
    in-memory rolling window. Powers the second sparkline added in
    Task #809.

    Bucketing is by **ISO week-Monday (UTC)** so weeks are consistent
    across timezones — `week_start` is the Monday's date in
    ``YYYY-MM-DD``. Weeks with zero data are returned with
    ``exhausted=0`` so the chart's x-axis is regularly spaced rather
    than jumping over silent weeks.

    The aggregate stores deduplicated per-device counts per day, so
    ``exhausted`` here is "device-days that hit the wall this week"
    — a single device that hit on Mon, Wed and Fri counts as 3.
    Computing strict per-week unique devices would need the full
    per-day device sets which we don't durably persist; the
    device-days metric is the right one for cap-tuning trends and is
    documented honestly in the field name.

    Falls back to the (still-alive) zset ``ZCARD`` for days that
    pre-date the persistence writer, keeping the chart non-empty
    on first deploy.
    """
    weeks = max(1, min(int(weeks), 52))
    rc = _anon_redis_client()
    today = datetime.now(timezone.utc).date()
    # Monday of the current ISO week, then walk back `weeks - 1` weeks
    # to get the oldest bucket's Monday. Iterating forward by day
    # (rather than week) keeps the bucketing logic uniform with the
    # daily aggregate's storage shape.
    cur_monday = today - timedelta(days=today.weekday())
    oldest_monday = cur_monday - timedelta(days=(weeks - 1) * 7)
    # Pre-seed every week with zero so the chart x-axis never has
    # gaps that look like missing data.
    buckets: dict = {
        (oldest_monday + timedelta(days=7 * i)).strftime("%Y-%m-%d"):
            {"week_start": (oldest_monday + timedelta(days=7 * i)).strftime("%Y-%m-%d"), "exhausted": 0, "days_with_data": 0}
        for i in range(weeks)
    }

    if rc is not None:
        total_days = (today - oldest_monday).days + 1
        for n in range(total_days):
            d = oldest_monday + timedelta(days=n)
            day_str = d.strftime("%Y-%m-%d")
            count = 0
            agg = _read_anon_exhaust_daily_agg(day_str)
            if agg:
                # Prefer `unique` (canonical, ZCARD-derived). Fall
                # back to `events` only if `unique` is missing —
                # never `max(...)`, see explanation in
                # `get_anon_quota_exhausted_stats`.
                count = int(agg.get("unique") or 0)
                if not count:
                    count = int(agg.get("events") or 0)
            if not count:
                # Fallback: still-alive zset for legacy days.
                try:
                    count = int(rc.zcard(f"chat:anon_exhausted_devices:{day_str}") or 0)
                except Exception:
                    count = 0
            if count <= 0:
                continue
            week_monday = d - timedelta(days=d.weekday())
            wk = week_monday.strftime("%Y-%m-%d")
            row = buckets.get(wk)
            if row is None:
                # Defensive: a `d` outside the seeded range shouldn't
                # happen but we don't want to drop a real datapoint.
                row = {"week_start": wk, "exhausted": 0, "days_with_data": 0}
                buckets[wk] = row
            row["exhausted"] += count
            row["days_with_data"] += 1

    return sorted(buckets.values(), key=lambda r: r["week_start"])


def backfill_anon_quota_exhausted_today() -> int:
    """One-shot baseline scan for today's chart.

    The metric only starts collecting from the moment this code ships,
    so today's chart would otherwise be empty until the first device
    exhausts. This scans Redis for `device_daily_credits:*:<today>`
    keys (the same keys `atomic_deduct_device_credit` writes) and
    replays `record_anon_quota_exhausted` for any whose counter has
    already reached the per-day cap. The dedupe inside
    `record_anon_quota_exhausted` ensures repeated calls are safe.

    Returns the number of at-cap devices found (0 when Redis is
    unavailable). Intended for one-off invocation from the admin
    endpoint via `?backfill=1` so we don't run a full keyspace scan
    on every page load.
    """
    rc = _anon_redis_client()
    if rc is None:
        return 0
    try:
        from config import PLAN_LIMITS
        cap = int(PLAN_LIMITS.get("free", {}).get("credits_per_day") or 30)
    except Exception:
        cap = 30
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    suffix = f":{today}"
    found = 0
    try:
        for key in rc.scan_iter(f"device_daily_credits:*{suffix}"):
            try:
                key_str = key.decode("ascii", errors="ignore") if isinstance(key, bytes) else key
                if not key_str.endswith(suffix):
                    continue
                raw = rc.get(key_str)
                if isinstance(raw, bytes):
                    raw = raw.decode("ascii", errors="ignore")
                used = int(raw or 0)
                if used < cap:
                    continue
                # Strip the `device_daily_credits:` prefix and the
                # trailing `:<today>` suffix to recover the token id.
                middle = key_str[len("device_daily_credits:"):-len(suffix)]
                if not middle:
                    continue
                if record_anon_quota_exhausted(middle, ip="", plan_target="free"):
                    found += 1
            except (TypeError, ValueError, AttributeError):
                continue
    except Exception as _e:
        logger.debug("backfill_anon_quota_exhausted_today scan failed: %s", _e)
        return 0
    return found

# ── Background health-check cache ─────────────────────────────────────────────
# _check_health_deps() costs ~500 ms per call (Supabase round-trip).
# A background task runs it every 25 s and stores the result here so the
# admin dashboard always reads from cache (~0 ms).
_health_deps_cache: dict = {}
_health_deps_cache_at: float = 0.0
_HEALTH_CACHE_TTL_S: float = 30.0      # max age before falling back to live call

class _MetricsStore:
    def __init__(self):
        self._lock = _threading.Lock()
        self.request_count = 0
        self.error_count = 0
        self.active_requests = 0
        self.active_users: Dict[str, float] = {}
        self.chat_count = 0
        self.endpoint_counts: Dict[str, int] = _defaultdict(int)
        self.status_counts: Dict[int, int] = _defaultdict(int)
        self._rps_window: list = []
        self.spoof_count = 0
        self.spoof_by_bot: Dict[str, int] = _defaultdict(int)
        self._spoof_window: list = []

    def record_request(self, path: str, status: int, user_id: str = None):
        now = _time_mod.time()
        with self._lock:
            self.request_count += 1
            self.status_counts[status] += 1
            # Only count *server* failures as errors. Bucketing every 4xx
            # as an error caused the alerting loop to fire constant
            # "Error rate spike" alerts (40-67% rates were typical)
            # because:
            #   - 401 / 403  →  auth working as intended (failed login,
            #                   missing token, bot probing /admin)
            #   - 404        →  bot scans for /wp-admin, /.env, /xmlrpc.php
            #                   etc. — totally normal noise on the open web
            #   - 422        →  client sent invalid form data
            #   - 429        →  the rate limiter doing its job
            # None of those indicate the backend is broken; they indicate
            # the backend correctly *rejected* a bad request. We now only
            # increment error_count on:
            #   - 5xx (server fault: code bug, dep down, OOM, etc.)
            #   - 408 (request timeout — backend was too slow)
            #   - 499 (client closed during slow response — same signal,
            #          surfaced by some proxies / NGINX-style middleware)
            if status >= 500 or status in (408, 499):
                self.error_count += 1
            bucket = path.split("?")[0]
            if bucket.startswith("/api/"):
                self.endpoint_counts[bucket] += 1
            if path.startswith("/api/chat"):
                self.chat_count += 1
            if user_id:
                self.active_users[user_id] = now
            self._rps_window.append(now)

    def inc_active(self):
        with self._lock:
            self.active_requests += 1

    def dec_active(self):
        with self._lock:
            self.active_requests -= 1

    def get_rps(self) -> float:
        now = _time_mod.time()
        cutoff = now - 60
        with self._lock:
            self._rps_window = [t for t in self._rps_window if t > cutoff]
            count = len(self._rps_window)
        return round(count / 60.0, 2) if count else 0.0

    def get_active_users(self, window_seconds: int = 300) -> int:
        cutoff = _time_mod.time() - window_seconds
        with self._lock:
            self.active_users = {uid: ts for uid, ts in self.active_users.items() if ts > cutoff}
            return len(self.active_users)

    def get_top_endpoints(self, n: int = 10) -> list:
        with self._lock:
            return sorted(self.endpoint_counts.items(), key=lambda x: -x[1])[:n]

    def record_spoof(self, claimed_bot: str = "unknown"):
        now = _time_mod.time()
        with self._lock:
            self.spoof_count += 1
            self.spoof_by_bot[claimed_bot] += 1
            self._spoof_window.append(now)

    def get_spoof_rpm(self) -> float:
        now = _time_mod.time()
        cutoff = now - 60
        with self._lock:
            self._spoof_window = [t for t in self._spoof_window if t > cutoff]
            return float(len(self._spoof_window))

    def get_spoof_stats(self) -> dict:
        now = _time_mod.time()
        cutoff = now - 60
        with self._lock:
            self._spoof_window = [t for t in self._spoof_window if t > cutoff]
            return {
                "total": self.spoof_count,
                "by_bot": dict(self.spoof_by_bot),
                "rpm": float(len(self._spoof_window)),
            }

_metrics = _MetricsStore()

_METRICS_HISTORY_MAX = 1440
_metrics_history: list = []
_metrics_history_lock = _threading.Lock()

def _snapshot_metrics():
    """Take a point-in-time snapshot of key metrics for graphing."""
    import datetime
    from llm import _llm_batcher
    now = datetime.datetime.now(datetime.timezone.utc)
    batch_s = _llm_batcher.stats
    spoof_stats = _metrics.get_spoof_stats()
    snap = {
        "t": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ts": int(_time_mod.time()),
        "active_5m": _metrics.get_active_users(300),
        "active_15m": _metrics.get_active_users(900),
        "active_60m": _metrics.get_active_users(3600),
        "rps": _metrics.get_rps(),
        "requests": _metrics.request_count,
        "errors": _metrics.error_count,
        "chats": _metrics.chat_count,
        "in_flight": _metrics.active_requests,
        "llm_batched": batch_s["batched"],
        "llm_deduped": batch_s["deduped"],
        "llm_pending": batch_s["pending"],
        "spoof_total": spoof_stats["total"],
        "spoof_rpm": spoof_stats["rpm"],
    }
    with _metrics_history_lock:
        _metrics_history.append(snap)
        if len(_metrics_history) > _METRICS_HISTORY_MAX:
            del _metrics_history[:len(_metrics_history) - _METRICS_HISTORY_MAX]
    return snap

def _start_metrics_collector():
    """Background thread that snapshots metrics every 60 seconds."""
    def _run():
        while True:
            try:
                _snapshot_metrics()
            except Exception:
                pass
            _time_mod.sleep(60)
    t = _threading.Thread(target=_run, daemon=True)
    t.start()

_start_metrics_collector()


async def _check_health_deps():
    result = {}
    try:
        t0 = _time_mod.time()
        await db.command("ping")
        result["mongodb"] = {"status": "ok", "latencyMs": round((_time_mod.time() - t0) * 1000, 1)}
    except Exception:
        result["mongodb"] = {"status": "error", "latencyMs": 0}
    try:
        if _deps_mod.pg_pool:
            t0 = _time_mod.time()
            async with _deps_mod.pg_pool.acquire() as conn:
                await conn.execute("SELECT 1")
            result["postgresql"] = {"status": "ok", "latencyMs": round((_time_mod.time() - t0) * 1000, 1)}
        else:
            result["postgresql"] = {"status": "not_configured", "latencyMs": 0}
    except Exception:
        result["postgresql"] = {"status": "error", "latencyMs": 0}
    # Cloudflare AI Gateway / cache reachability — replaced the previous
    # Redis probe because ``deps.redis_client`` is permanently None in
    # this codebase, so the dashboard "Redis Connected" tile was always
    # showing a misleading status. The Cloudflare AI Gateway is the
    # actual durable cache layer the chat path leans on, so probe it
    # instead. HEAD with a tight timeout — we don't care about the HTTP
    # status (gateway returns 4xx for an empty HEAD) only that we can
    # reach the edge at all.
    try:
        from config import CF_GATEWAY_ENABLED, CF_GATEWAY_BASE
        if not CF_GATEWAY_ENABLED or not CF_GATEWAY_BASE:
            result["cloudflare_cache"] = {"status": "not_configured", "latencyMs": 0}
        else:
            t0 = _time_mod.time()
            async with httpx.AsyncClient(timeout=2.5) as _hc:
                await _hc.head(CF_GATEWAY_BASE)
            result["cloudflare_cache"] = {"status": "ok", "latencyMs": round((_time_mod.time() - t0) * 1000, 1)}
    except Exception:
        result["cloudflare_cache"] = {"status": "error", "latencyMs": 0}
    try:
        if supa and SUPABASE_URL:
            # Use the best available key: service key → anon key.
            # Direct HTTP GET to /rest/v1/ — no SQL round-trip, just TLS keep-alive.
            _supa_key        = SUPABASE_SERVICE_KEY or SUPABASE_ANON_KEY
            _supa_health_url = SUPABASE_URL.rstrip("/") + "/rest/v1/"
            _supa_headers    = {"apikey": _supa_key, "Authorization": f"Bearer {_supa_key}"}
            t0 = _time_mod.time()
            async with httpx.AsyncClient(
                http2=True,
                timeout=httpx.Timeout(connect=2.0, read=4.0, write=2.0, pool=1.0),
                limits=httpx.Limits(max_keepalive_connections=5, max_connections=10, keepalive_expiry=60),
            ) as _hc:
                _r = await _hc.get(_supa_health_url, headers=_supa_headers)
                _r.raise_for_status()
            result["supabase"] = {"status": "ok", "latencyMs": round((_time_mod.time() - t0) * 1000, 1)}
        else:
            result["supabase"] = {"status": "not_configured", "latencyMs": 0}
    except Exception as _se:
        logger.debug(f"Supabase health check failed: {_se}")
        result["supabase"] = {"status": "error", "latencyMs": 0}
    return result


_cache_stats_log_counter = 0   # increments each 25 s cycle; log every 12 cycles = 5 min

async def _bg_health_loop():
    """Warm the health-deps cache every 25 s so dashboard reads are near-instant.
    Also emits a structured cache_stats log every 5 minutes."""
    global _health_deps_cache, _health_deps_cache_at, _cache_stats_log_counter
    await asyncio.sleep(8)                  # let startup settle first
    while True:
        try:
            fresh = await asyncio.wait_for(_check_health_deps(), timeout=10)
            _health_deps_cache    = fresh
            _health_deps_cache_at = _time_mod.time()
        except Exception as _e:
            logger.debug(f"Health bg loop: {_e}")

        # Emit cache hit-rate log every 5 minutes
        _cache_stats_log_counter += 1
        if _cache_stats_log_counter % 12 == 0:
            total = _cache_mod._redis_hit_count + _cache_mod._redis_miss_count
            hit_rate = round(_cache_mod._redis_hit_count / max(1, total), 3)
            logger.info(
                f"cache_stats hit_rate={hit_rate} "
                f"hits={_cache_mod._redis_hit_count} misses={_cache_mod._redis_miss_count} total={total}"
            )
            try:
                from neural_mesh import get_mesh_stats as _gms
                _ms = _gms()
                agg = _ms["aggregate"]
                mesh_parts = " ".join(
                    f"{m['name']}={m['hits']}h/{m['misses']}m/{m['l1_size']}sz"
                    for m in _ms["meshes"]
                )
                logger.info(
                    "neural_mesh_stats hit_rate=%.3f hits=%d misses=%d "
                    "inflight_saves=%d | %s",
                    agg["hit_rate"], agg["hits"], agg["misses"],
                    agg["inflight_saves"], mesh_parts,
                )
            except Exception:
                pass

        await asyncio.sleep(25)


# ─────────────────────────────────────────────
# PRODUCTION ALERTING SYSTEM
# ─────────────────────────────────────────────

_ALERT_COOLDOWN_S = 1800   # 30 min between same alert type
_alert_last_fired: dict = {}   # { "alert_key": timestamp }

# Cross-worker / cross-restart dedup window. The in-memory ``_alert_last_fired``
# above resets every time a gunicorn worker recycles (max_requests=5000) and
# isn't shared between workers, so the same alert can fire 3-N× per real
# incident. This persistent backstop is keyed by ``(alert_type, dedup_key)``
# where ``dedup_key`` is derived from threshold_snapshot fields like
# ``endpoint`` / ``url`` / ``service`` so per-target alerts (e.g. one alert per
# IndexNow endpoint) still fire independently. Default window is 6h — long
# enough to suppress repeated firings within the same incident, short enough
# that a recurrent issue still pages on-call after a working day.
_PERSISTENT_ALERT_COOLDOWN_S = 6 * 3600
_PERSISTENT_DEDUP_KEYS = ("endpoint", "url", "page_id", "service", "host", "domain")
# Task #453: per-alert-type debounce for the inline "no working browser
# push endpoints" warning that gets attached to email/webhook bodies when
# Task #452's pre-check finds zero active admin push subs. Without this,
# every alert burst would re-warn on the still-healthy channels.
_PUSH_SILENT_WARN_COOLDOWN_S = 24 * 3600
_push_silent_warning_last_at: dict = {}   # { "alert_key": timestamp }
_ALERT_THRESHOLDS_DEFAULT = {
    "latency_p95_ms": 2000,
    "error_rate_pct": 5.0,
    "fallback_rate_pct": 50.0,
    "spoof_rpm": 50,
    "auto_block_threshold": 100,
    "auto_block_expiry_hours": 168,
    "endpoint_down_minutes": 60,
    "endpoint_down_check_minutes": 15,
    "collection_growth_per_day": 500,
    "url_404_spike_pct": 20.0,
    "hydrate_failure_per_hour": 50,
    "hydrate_recovery_min_rate_pct": 50.0,
    "hydrate_recovery_min_attempts": 10,
    # Task #656: review-prompt CTR floor alert. Fires when, over the last
    # 7d, ``review_prompt_shown`` >= ``review_prompt_ctr_min_shown`` AND
    # ``ctr_pct`` < ``review_prompt_ctr_floor_pct``. Modeled on the
    # hydrate_recovery_low pair above so admins can tune both knobs from
    # the existing Alert Settings panel without a deploy.
    "review_prompt_ctr_min_shown": 50,
    "review_prompt_ctr_floor_pct": 5.0,
    # Task #661: per-trigger-reason CTR collapse alert. Fires when, over
    # the last 7d, an individual trigger reason's CTR drops by ≥
    # ``review_prompt_reason_ctr_drop_pp`` percentage points vs the
    # prior 7d AND both windows have at least
    # ``review_prompt_reason_ctr_min_shown`` shown events for that
    # reason (so a low-volume reason can't trip the alert on noise).
    # Catches regressions confined to one surface (e.g. answer_helpful)
    # before they wash out the aggregate ``review_prompt_ctr_low``.
    "review_prompt_reason_ctr_drop_pp": 5.0,
    "review_prompt_reason_ctr_min_shown": 30,
    # Task #670: auto-tune the per-reason CTR collapse threshold from
    # baseline noise. The evaluator computes the per-reason CTR mean +
    # sample stddev across the last
    # ``review_prompt_reason_ctr_baseline_weeks`` weeks (excluding the
    # current week) and additionally requires the WoW drop to exceed
    # ``review_prompt_reason_ctr_drop_sigma`` × stddev before paging.
    # A volatile reason whose CTR routinely swings ±10 pp won't trip on
    # an ordinary 6 pp dip; a rock-steady reason will page on a much
    # smaller absolute move once it clears the absolute pp floor. When
    # stddev is 0 or < 2 weekly samples are available, the sigma gate
    # is skipped so behaviour matches the original absolute-only check.
    "review_prompt_reason_ctr_drop_sigma": 2.0,
    "review_prompt_reason_ctr_baseline_weeks": 4,
    # Task #432: page on-call when this worker's Assamese-purity override
    # refresh loop hasn't ticked successfully in this many seconds. The
    # poll cadence is 15s so 60s == 4 missed ticks before paging.
    "assamese_refresh_stale_seconds": 60,
    # Task #707: silent-lockout watcher. Fires
    # `cf_access_admin_silent_lockout` when the CF_ACCESS_* env state has
    # changed but no admin login has succeeded for this many hours since
    # the change. Operator-tunable from the existing Alert Settings table
    # so a noisy / urgent rollout can shorten the window without a deploy.
    "cf_access_silent_lockout_hours": 24,
    # Task #769 (audit B1 follow-up): page on-call when
    # `atomic_deduct_credit` falls back away from Postgres at a
    # sustained rate over the last 5 min. Default 5/min — i.e. >25
    # fallbacks in a 5 min window — is small enough to catch a real
    # PG outage almost immediately and large enough to stay quiet
    # during routine connection blips. Set to 0 to disable the alert.
    "credit_deduct_fallback_per_min": 5,
    # Task #70: Workers AI 429 burst alert. Fires when >= this many
    # Workers AI 429 rate-limit responses are recorded within the 180s
    # counting window without any successful call resetting the counter.
    # The 180s window (llm._PROVIDER_429_BURST_WINDOW_S) is intentionally
    # larger than the 120s alerting loop so a burst near a tick boundary
    # is never silently missed. The Redis counter uses TTL-consecutive
    # semantics: it counts hits since the first 429 in the current burst
    # and auto-expires 180s after the last hit (not a strict sliding window).
    # Default 5 — enough to distinguish a real throttle burst from a
    # transient single-request spike. Set to 0 to disable.
    "workers_ai_429_burst_threshold": 5,
    # Task #75: Groq 429 burst alert.  Same semantics as Workers AI above.
    # Groq operates at 30 RPM on the free tier so 5 hits in 180 s means
    # it is fully throttled.  Redis key: groq_429_burst.  Set to 0 to disable.
    "groq_429_burst_threshold": 5,
    # Task #75: Gemini 429 burst alert.  Same semantics.  Redis key:
    # gemini_429_burst.  Gemini's paid quota is much higher so a burst here
    # usually signals an account-level quota exhaustion, not normal traffic.
    # Set to 0 to disable.
    "gemini_429_burst_threshold": 5,
    # Task #373: Azure OpenAI 429 burst alert.  llm._track_provider_429 has
    # been counting Azure 429s into _PROVIDER_429_WINDOWS["azure_openai"]
    # (Redis key: azure_429_burst) since the provider was added, but the
    # alerting loop never dispatched on them — bursts were silently logged
    # and never paged on-call.  Same TTL-consecutive semantics as the
    # other providers above.  Default 5 — enough to distinguish a real
    # throttle burst from a transient single-request spike.  Set to 0 to
    # disable.
    "azure_openai_429_burst_threshold": 5,
    # Task #373: Deepgram 429 burst alert.  Same semantics; Redis key:
    # deepgram_429_burst.  Deepgram is the primary STT/TTS provider, so a
    # sustained burst means voice features are degrading even when chat
    # completions look healthy — well worth a page.  Set to 0 to disable.
    "deepgram_429_burst_threshold": 5,
}
_ALERT_EXPIRATION_DEFAULT = {
    "enabled": False,
    "days": 7,
}
_ALERT_THRESHOLDS = dict(_ALERT_THRESHOLDS_DEFAULT)
_alert_expiration = dict(_ALERT_EXPIRATION_DEFAULT)
_NOTIFICATION_CHANNELS_DEFAULT = {
    "email": "",
    "webhook_url": "",
    # Per-alert-type webhook toggles. When False, the Slack/Discord webhook
    # is suppressed for that alert type even if a webhook URL is configured.
    # (Email, persisted alerts, and browser push are unaffected.)
    "seo_slack_enabled": True,
    # Task #414: per-category webhook toggle for the new hydrate /
    # stale-build alerts. Email + persisted alerts + browser push are
    # unaffected when False — only the Slack/Discord webhook is muted.
    "hydrate_slack_enabled": True,
    # Task #660: separate recipient list for the Monday review-prompt
    # weekly digest (distinct from the incident-alert ``email`` channel).
    # Stored as a list of trimmed lowercase emails. Empty list → fall
    # back to ``email`` then ``ALERT_EMAIL`` so behaviour is unchanged
    # for existing installs that haven't configured the new field.
    "review_prompt_digest_emails": [],
}
# Alert types treated as "SEO incidents" for the Slack webhook toggle.
_SEO_WEBHOOK_ALERT_TYPES = ("seo_health_degraded", "seo_url_spike", "seo_health_recovered")
_SEO_DASHBOARD_URL = "https://syrabit.ai/admin/seo"
# Task #414: alert types that get a custom hydrate Slack card.
_HYDRATE_WEBHOOK_ALERT_TYPES = ("hydrate_failure_spike", "hydrate_recovery_low")
_HYDRATE_DASHBOARD_URL = "https://syrabit.ai/admin/dashboard?tab=overview#hydrate-health"
_notification_channels: dict = dict(_NOTIFICATION_CHANNELS_DEFAULT)

# Task #418: per-channel delivery status surfaced on the Alert Settings page so
# admins can confirm their Slack/email/push integrations actually work without
# having to wait for a real incident. Updated by ``_dispatch_alert`` after each
# attempt and persisted to ``db.api_config["alert_channel_status"]`` so it
# survives process restarts.
_CHANNEL_STATUS_KEYS = ("email", "webhook", "persisted", "push")
_CHANNEL_STATUS_DEFAULT = {
    k: {
        "last_attempt_at": None,
        "last_success_at": None,
        "last_error": None,
        "last_alert_type": None,
    } for k in _CHANNEL_STATUS_KEYS
}
_channel_status: dict = {k: dict(v) for k, v in _CHANNEL_STATUS_DEFAULT.items()}

async def _load_alert_settings():
    """Load alert thresholds, expiration, and notification channel settings from db.api_config, falling back to defaults."""
    global _ALERT_THRESHOLDS, _alert_expiration, _notification_channels, _channel_status
    try:
        new_thresholds = dict(_ALERT_THRESHOLDS_DEFAULT)
        new_expiration = dict(_ALERT_EXPIRATION_DEFAULT)
        new_channels = dict(_NOTIFICATION_CHANNELS_DEFAULT)
        cfg = await db.api_config.find_one({}, {"_id": 0})
        if cfg and "alert_channel_status" in cfg and isinstance(cfg["alert_channel_status"], dict):
            saved_status = cfg["alert_channel_status"]
            for k in _CHANNEL_STATUS_KEYS:
                entry = saved_status.get(k)
                if isinstance(entry, dict):
                    _channel_status[k] = {
                        "last_attempt_at": entry.get("last_attempt_at"),
                        "last_success_at": entry.get("last_success_at"),
                        "last_error": entry.get("last_error"),
                        "last_alert_type": entry.get("last_alert_type"),
                    }
        if cfg and "alert_settings" in cfg:
            s = cfg["alert_settings"]
            thresholds = s.get("thresholds", {})
            for k in _ALERT_THRESHOLDS_DEFAULT:
                if k in thresholds:
                    try:
                        new_thresholds[k] = float(thresholds[k])
                    except (ValueError, TypeError):
                        pass
            exp = s.get("expiration", {})
            if "enabled" in exp and isinstance(exp["enabled"], bool):
                new_expiration["enabled"] = exp["enabled"]
            if "days" in exp:
                try:
                    new_expiration["days"] = max(1, int(exp["days"]))
                except (ValueError, TypeError):
                    pass
            channels = s.get("notification_channels", {})
            if isinstance(channels.get("email"), str):
                new_channels["email"] = channels["email"].strip()
            if isinstance(channels.get("webhook_url"), str):
                new_channels["webhook_url"] = channels["webhook_url"].strip()
            if isinstance(channels.get("seo_slack_enabled"), bool):
                new_channels["seo_slack_enabled"] = channels["seo_slack_enabled"]
            if isinstance(channels.get("hydrate_slack_enabled"), bool):
                new_channels["hydrate_slack_enabled"] = channels["hydrate_slack_enabled"]
            # Task #660: review-prompt digest recipient list. Accept a
            # list (preferred) or a comma-separated string for tolerance
            # with older configs / hand-edited DB rows. Filter out blanks
            # and dedupe while preserving order so the saved list stays
            # stable across reloads.
            raw_digest = channels.get("review_prompt_digest_emails")
            if isinstance(raw_digest, str):
                raw_digest = [p for p in raw_digest.split(",")]
            if isinstance(raw_digest, list):
                seen: set = set()
                cleaned: list = []
                for entry in raw_digest:
                    if not isinstance(entry, str):
                        continue
                    e = entry.strip()
                    if not e or "@" not in e:
                        continue
                    key = e.lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    cleaned.append(e)
                new_channels["review_prompt_digest_emails"] = cleaned
        _ALERT_THRESHOLDS = new_thresholds
        _alert_expiration = new_expiration
        _notification_channels = new_channels
    except Exception as e:
        logger.debug(f"Failed to load alert settings from db: {e}")

async def _auto_expire_alerts():
    """Auto-acknowledge alerts older than the configured expiration period."""
    if not _alert_expiration.get("enabled"):
        return
    days = _alert_expiration.get("days", 7)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        result = await db.alerts.update_many(
            {"acknowledged": False, "fired_at": {"$lt": cutoff}},
            {"$set": {"acknowledged": True, "acknowledged_at": datetime.now(timezone.utc).isoformat(), "acknowledged_by": "auto-expiration"}},
        )
        if result.modified_count > 0:
            logger.info(f"Auto-expired {result.modified_count} alerts older than {days} days")
    except Exception as e:
        logger.debug(f"Alert auto-expiration error: {e}")

def _build_seo_slack_payload(alert_type: str, title: str, body: str, snap: dict) -> dict:
    """Build a Slack-friendly message for SEO health alerts.

    Uses Slack Block Kit so the message shows severity, sitemap counts, and a
    "Open SEO Manager" button. Slack, Discord (via `text` fallback), and
    generic webhooks all accept the `text` field, while Slack additionally
    renders `blocks` for the rich layout.

    Task #821: handle the new ``seo_health_recovered`` alert type with a
    green check icon and a "RESOLVED" label so the all-clear message is
    visually distinct from the initial / digest alerts. The header emoji
    and section icon also switch from red to green for recoveries.
    """
    is_recovery = (alert_type == "seo_health_recovered")
    status = str(snap.get("actual", "")).lower() or ("ok" if is_recovery else "degraded")
    if is_recovery:
        severity_label = ":white_check_mark: RESOLVED"
        header_emoji = "✅"
        fallback_emoji = ":white_check_mark:"
        button_style = "primary"
    else:
        severity_label = {
            "critical": ":rotating_light: CRITICAL",
            "degraded": ":warning: DEGRADED",
        }.get(status, f":warning: {status.upper() or 'DEGRADED'}")
        header_emoji = "🚨"
        fallback_emoji = ":rotating_light:"
        button_style = "primary"

    valid_sm = snap.get("valid_sitemaps", "N/A")
    total_sm = snap.get("total_sitemaps", "N/A")
    url_rate = snap.get("url_check_success_rate", "N/A")
    sitemap_line = f"Sitemaps valid: *{valid_sm} / {total_sm}*"
    url_line = f"URL spot-check success: *{url_rate}%*"

    text_fallback = (
        f"{fallback_emoji} *{title}*\n"
        f"{severity_label}\n"
        f"{body}\n"
        f"{sitemap_line} · {url_line}\n"
        f"Dashboard: {_SEO_DASHBOARD_URL}"
    )

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"{header_emoji} {title}", "emoji": True}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Severity*\n{severity_label}"},
            {"type": "mrkdwn", "text": f"*Alert type*\n`{alert_type}`"},
            {"type": "mrkdwn", "text": f"*{sitemap_line.split(':',1)[0]}*\n{valid_sm} / {total_sm}"},
            {"type": "mrkdwn", "text": f"*URL spot-checks*\n{url_rate}%"},
        ]},
        {"type": "section", "text": {"type": "mrkdwn", "text": body or ("SEO health back to OK." if is_recovery else "SEO health degraded.")}},
        {"type": "actions", "elements": [
            {"type": "button",
             "text": {"type": "plain_text", "text": "Open SEO Manager", "emoji": True},
             "url": _SEO_DASHBOARD_URL,
             "style": button_style},
        ]},
    ]

    return {
        "text": text_fallback,
        "blocks": blocks,
        "alert_type": alert_type,
        "service": "syrabit-api",
        "threshold_snapshot": snap,
    }


def _build_hydrate_slack_payload(alert_type: str, title: str, body: str, snap: dict) -> dict:
    """Build a Slack-friendly message for hydrate / stale-build alerts
    (Task #414). Mirrors ``_build_seo_slack_payload`` so admins on Slack
    get the same depth of context as the dashboard email — failure count
    vs threshold, top failing chunk kind, sample error message, and a
    one-click button to the admin Analytics tile.
    """
    metric = snap.get("metric") or alert_type
    configured = snap.get("value", "N/A")
    actual = snap.get("actual", "N/A")
    top_kind = snap.get("top_kind") or "n/a"
    attempts = snap.get("auto_reload_attempts")
    recoveries = snap.get("auto_reload_recoveries")

    # Pretty units per alert type so the Slack card reads naturally.
    if alert_type == "hydrate_failure_spike":
        actual_str = f"{actual} events/hr"
        threshold_str = f"> {configured}/hr"
        severity_label = ":rotating_light: SPIKE"
    elif alert_type == "hydrate_recovery_low":
        actual_str = f"{actual}%"
        threshold_str = f"< {configured}%"
        severity_label = ":warning: RECOVERY LOW"
    else:  # defensive — should not happen; routing gates on the tuple.
        actual_str = str(actual)
        threshold_str = str(configured)
        severity_label = ":warning: HYDRATE"

    recovery_line = ""
    if attempts is not None and recoveries is not None:
        recovery_line = f"Auto-reload: *{recoveries}/{attempts}* recovered in last hour"

    text_fallback = (
        f":rotating_light: *{title}*\n"
        f"{severity_label} · `{metric}` actual *{actual_str}* (threshold {threshold_str})\n"
        f"{body}\n"
        f"Dashboard: {_HYDRATE_DASHBOARD_URL}"
    )

    fields = [
        {"type": "mrkdwn", "text": f"*Severity*\n{severity_label}"},
        {"type": "mrkdwn", "text": f"*Alert type*\n`{alert_type}`"},
        {"type": "mrkdwn", "text": f"*Threshold*\n{threshold_str}"},
        {"type": "mrkdwn", "text": f"*Actual*\n{actual_str}"},
        {"type": "mrkdwn", "text": f"*Top failing kind*\n{top_kind}"},
    ]
    if recovery_line:
        fields.append({"type": "mrkdwn", "text": f"*Recovery*\n{recovery_line.split(': ',1)[1]}"})

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"🚨 {title}", "emoji": True}},
        {"type": "section", "fields": fields},
        {"type": "section", "text": {"type": "mrkdwn", "text": body or "Hydration regression detected."}},
        {"type": "actions", "elements": [
            {"type": "button",
             "text": {"type": "plain_text", "text": "Open Analytics tile", "emoji": True},
             "url": _HYDRATE_DASHBOARD_URL,
             "style": "primary"},
        ]},
    ]

    return {
        "text": text_fallback,
        "blocks": blocks,
        "alert_type": alert_type,
        "service": "syrabit-api",
        "threshold_snapshot": snap,
    }


def _make_outcome():
    return {"attempted": False, "ok": False, "error": None, "skipped_reason": None}


def _summarize_push_failure(doc: dict) -> str:
    """Build a human-readable error string from a push_delivery_log entry."""
    if not doc:
        return "unknown error"
    if doc.get("error"):
        return str(doc["error"])[:200]
    failed = int(doc.get("failed") or 0)
    expired = int(doc.get("expired") or 0)
    total = int(doc.get("total") or 0)
    if total == 0:
        return "no subscribers received the push"
    parts = []
    if failed:
        parts.append(f"{failed} failed")
    if expired:
        parts.append(f"{expired} expired")
    return ", ".join(parts) or "delivery failed"


async def _recompute_push_channel_status() -> None:
    """Refresh _channel_status['push'] from db.push_delivery_log so the Alert
    Settings UI shows the truth (per Task #427) instead of the optimistic
    queued-task signal that just confirms the dispatch coroutine started.

    Scoped to ``target="admin-only"`` because the Alert Settings panel reports
    on admin alert delivery health. Broadcast pushes (``target="all"``) sent
    via /admin/notifications or the exam-reminder loop go to general users
    and must not mask a broken admin push pipeline.
    """
    try:
        admin_filter = {"target": "admin-only"}
        latest = await db.push_delivery_log.find_one(
            admin_filter, {"_id": 0}, sort=[("dispatched_at", -1)]
        )
        latest_success = await db.push_delivery_log.find_one(
            {**admin_filter, "sent": {"$gt": 0}},
            {"_id": 0},
            sort=[("dispatched_at", -1)],
        )
        latest_failure = await db.push_delivery_log.find_one(
            {
                **admin_filter,
                "$or": [
                    {"skipped": True},
                    {"error": {"$exists": True, "$ne": None}},
                    {"$and": [{"sent": 0}, {"$or": [
                        {"failed": {"$gt": 0}},
                        {"expired": {"$gt": 0}},
                        {"total": 0},
                    ]}]},
                ],
            },
            {"_id": 0},
            sort=[("dispatched_at", -1)],
        )
        entry = _channel_status.setdefault("push", dict(_CHANNEL_STATUS_DEFAULT["push"]))
        if latest:
            entry["last_attempt_at"] = latest.get("dispatched_at") or entry.get("last_attempt_at")
            entry["last_alert_type"] = latest.get("alert_type") or entry.get("last_alert_type")
        entry["last_success_at"] = latest_success.get("dispatched_at") if latest_success else None
        if latest_failure and (
            not latest_success
            or (latest_failure.get("dispatched_at") or "") > (latest_success.get("dispatched_at") or "")
        ):
            entry["last_error"] = _summarize_push_failure(latest_failure)
        else:
            entry["last_error"] = None
    except Exception as exc:
        logger.debug(f"Failed to recompute push channel status: {exc}")


async def _persist_channel_status():
    """Best-effort write of in-memory _channel_status to db.api_config."""
    try:
        await db.api_config.update_one(
            {},
            {"$set": {"alert_channel_status": _channel_status}},
            upsert=True,
        )
    except Exception as exc:
        logger.debug(f"Failed to persist channel status: {exc}")


def _record_outcome(channel: str, outcome: dict, alert_type: str, now_iso: str):
    """Update in-memory _channel_status from a single channel outcome."""
    if channel not in _channel_status:
        return
    if not outcome.get("attempted"):
        return
    entry = _channel_status[channel]
    entry["last_attempt_at"] = now_iso
    entry["last_alert_type"] = alert_type
    if outcome.get("ok"):
        entry["last_success_at"] = now_iso
        entry["last_error"] = None
    else:
        entry["last_error"] = outcome.get("error") or outcome.get("skipped_reason") or "unknown error"


async def _dispatch_alert(alert_type: str, title: str, body: str, threshold_snapshot: dict = None,
                          force: bool = False, mark_synthetic: bool = False):
    """Send alert via email (Resend), webhook, persisted alert, and browser push.

    Respects cooldown unless ``force=True`` (test deliveries from the admin
    dashboard bypass cooldown so admins can re-test on demand).

    When ``mark_synthetic=True`` the persisted alert and push notification are
    tagged as test traffic so they can be filtered out and don't pollute the
    real alert feed.

    Returns a dict of per-channel outcomes::

        {
            "email":     {"attempted": bool, "ok": bool, "error": str|None, "skipped_reason": str|None},
            "webhook":   {...},
            "persisted": {...},
            "push":      {...},
            "skipped_cooldown": bool,
        }

    Also updates the in-memory ``_channel_status`` and persists it to
    ``db.api_config["alert_channel_status"]`` so the Alert Settings UI can
    surface per-channel last-success timestamps (Task #418).
    """
    outcomes = {k: _make_outcome() for k in _CHANNEL_STATUS_KEYS}
    outcomes["skipped_cooldown"] = False

    now = _time_mod.time()
    if not force and now - _alert_last_fired.get(alert_type, 0) < _ALERT_COOLDOWN_S:
        outcomes["skipped_cooldown"] = True
        return outcomes

    # ── Persistent cross-worker dedup (atomic claim) ──────────────────────
    # Build a dedup key that's stable per real incident. Generic alerts
    # (no per-target context) collapse to just ``alert_type``; per-target
    # alerts (e.g. one per IndexNow endpoint) include the target so different
    # endpoints still alert independently. Forced dispatches bypass both the
    # in-memory and persistent backstops the same way (synthetic test
    # deliveries from the admin dashboard always use ``force=True``).
    dedup_key = alert_type
    if threshold_snapshot:
        for _k in _PERSISTENT_DEDUP_KEYS:
            _v = threshold_snapshot.get(_k)
            if _v:
                dedup_key = f"{alert_type}|{_k}={_v}"
                break
    persistent_claimed = False
    if not force:
        # Atomic claim: a single conditional upsert that wins iff the existing
        # row's ts is older than the cooldown cutoff (or no row exists). Using
        # find_one_and_update with the cutoff predicate + upsert leverages the
        # unique index on dedup_key — a racing worker that tries to insert
        # against an already-fresh row trips DuplicateKeyError, which we catch
        # and treat as "lost the race → skip". This is the cross-worker
        # equivalent of compare-and-swap.
        cutoff = now - _PERSISTENT_ALERT_COOLDOWN_S
        try:
            from pymongo.errors import DuplicateKeyError
            try:
                await db.alert_dispatch_log.find_one_and_update(
                    {
                        "dedup_key": dedup_key,
                        "$or": [
                            {"ts": {"$lt": cutoff}},
                            {"ts": {"$exists": False}},
                        ],
                    },
                    {"$set": {
                        "dedup_key": dedup_key,
                        "alert_type": alert_type,
                        "ts": now,
                        "fired_at": datetime.now(timezone.utc),
                    }},
                    upsert=True,
                )
                persistent_claimed = True
            except DuplicateKeyError:
                # Another worker (or a recent fire) already holds the slot
                # within the cooldown window. Drop this dispatch.
                outcomes["skipped_cooldown"] = True
                _alert_last_fired[alert_type] = now  # keep in-memory mirror in sync
                return outcomes
        except Exception as _dedup_exc:
            # Mongo unavailable or some other failure: fall back to the
            # in-memory cooldown (defense-in-depth). Don't drop a real alert
            # just because the dedup backstop is sick.
            logger.debug(f"persistent cooldown claim failed for {dedup_key}: {_dedup_exc}")

    _alert_last_fired[alert_type] = now

    logger.warning(f"ALERT [{alert_type}] {title}: {body}")

    # Task #453: detect zero active admin push endpoints up-front so the
    # email/webhook channels can carry an inline "browser push is silent"
    # warning. The push step (#4 below) reuses ``active_admin_subs`` to
    # short-circuit. Inline warning is debounced per-alert-type
    # (_PUSH_SILENT_WARN_COOLDOWN_S = 24h) so an alert burst doesn't spam
    # every still-healthy channel. -1 means the check itself errored — we
    # then fall through to the legacy dispatch path so we never silently
    # drop a real alert.
    active_admin_subs = -1
    try:
        active_admin_subs = await db.push_subscriptions.count_documents({
            "$or": [{"role": "admin"}, {"is_admin": True}],
            "active": {"$ne": False},
        })
        if active_admin_subs == 0:
            admin_docs = await db.users.find(
                {"is_admin": True}, {"_id": 0, "id": 1}
            ).to_list(500)
            legacy_admin_ids = [str(d["id"]) for d in admin_docs if d.get("id")]
            if legacy_admin_ids:
                active_admin_subs = await db.push_subscriptions.count_documents({
                    "user_id": {"$in": legacy_admin_ids},
                    "active": {"$ne": False},
                })
    except Exception as exc:
        logger.debug(f"Push pre-check (active admin subs) failed: {exc}")

    push_silent_warn_text = ""
    push_silent_warn_html = ""
    if active_admin_subs == 0:
        last_warn = _push_silent_warning_last_at.get(alert_type, 0)
        if force or now - last_warn >= _PUSH_SILENT_WARN_COOLDOWN_S:
            _push_silent_warning_last_at[alert_type] = now
            push_silent_warn_text = (
                "\n\n⚠️ No working browser push endpoints — "
                "re-enable notifications at /admin/notifications"
            )
            push_silent_warn_html = (
                "<p style=\"margin:14px 0;padding:12px 14px;border-left:4px solid #f59e0b;"
                "background:#fff7ed;color:#92400e;font-weight:600;border-radius:4px\">"
                "&#9888;&#65039; No working browser push endpoints &mdash; "
                "re-enable notifications at "
                "<a href=\"/admin/notifications\" style=\"color:#92400e;text-decoration:underline\">"
                "/admin/notifications</a>"
                "</p>"
            )

    # 1) Email alert via SendGrid (to admin) — Task #347 migrated from Resend.
    try:
        admin_email = (_notification_channels.get("email") or os.environ.get("ALERT_EMAIL", "")).strip()
        sendgrid_key = os.environ.get("SENDGRID_API_KEY", "").strip()
        if not admin_email:
            outcomes["email"]["skipped_reason"] = "no admin email configured"
        elif not sendgrid_key:
            outcomes["email"]["skipped_reason"] = "SENDGRID_API_KEY not set"
        if admin_email and sendgrid_key:
            outcomes["email"]["attempted"] = True
            from email_templates import send_admin_email as _send_admin_email
            threshold_html = ""
            if threshold_snapshot:
                metric = threshold_snapshot.get("metric", "N/A")
                configured = threshold_snapshot.get("value", "N/A")
                actual = threshold_snapshot.get("actual", "N/A")
                threshold_html = (
                    "<table style='border-collapse:collapse;margin:12px 0;width:100%;max-width:480px'>"
                    "<tr style='background:#f8d7da'>"
                    "<th style='text-align:left;padding:8px;border:1px solid #ddd'>Metric</th>"
                    "<th style='text-align:left;padding:8px;border:1px solid #ddd'>Threshold</th>"
                    "<th style='text-align:left;padding:8px;border:1px solid #ddd'>Actual</th>"
                    "</tr>"
                    f"<tr>"
                    f"<td style='padding:8px;border:1px solid #ddd'><code>{metric}</code></td>"
                    f"<td style='padding:8px;border:1px solid #ddd'>{configured}</td>"
                    f"<td style='padding:8px;border:1px solid #ddd;color:#c0392b;font-weight:bold'>{actual}</td>"
                    f"</tr></table>"
                )
            # Optional rich extra HTML block (e.g. per-sitemap breakdown for
            # the seo_url_spike alert). Callers may attach pre-rendered HTML
            # via threshold_snapshot["extra_html"] or the older
            # threshold_snapshot["by_sitemap_html"] alias.
            extra_html = ""
            if threshold_snapshot:
                extra_html = (
                    threshold_snapshot.get("extra_html")
                    or threshold_snapshot.get("by_sitemap_html")
                    or ""
                )
            # Render newlines in the body as <br> so multi-line bodies (e.g.
            # the seo_url_spike text fallback) read cleanly in HTML email.
            body_html = (body or "").replace("\n", "<br>")
            _send_admin_email(
                to=admin_email,
                subject=f"🚨 Syrabit Alert: {title}",
                html=f"<h2>{title}</h2><p>{body_html}</p>{push_silent_warn_html}{threshold_html}{extra_html}<p style='color:#888'>Alert type: {alert_type}<br>Cooldown: {_ALERT_COOLDOWN_S // 60} min</p>",
                sender=EMAIL_FROM,
            )
            outcomes["email"]["ok"] = True
    except Exception as e:
        outcomes["email"]["error"] = str(e)
        logger.debug(f"Alert email failed: {e}")

    # 2) Webhook alert (Slack / Discord / generic)
    try:
        webhook_url = (_notification_channels.get("webhook_url") or os.environ.get("ALERT_WEBHOOK_URL", "")).strip()
        # Per-category opt-out: admins can silence SEO alerts on Slack
        # without affecting email or push delivery.
        seo_slack_enabled = bool(_notification_channels.get("seo_slack_enabled", True))
        hydrate_slack_enabled = bool(_notification_channels.get("hydrate_slack_enabled", True))
        if alert_type in _SEO_WEBHOOK_ALERT_TYPES and not seo_slack_enabled:
            outcomes["webhook"]["skipped_reason"] = "seo_slack_enabled disabled"
            webhook_url = ""
        elif alert_type in _HYDRATE_WEBHOOK_ALERT_TYPES and not hydrate_slack_enabled:
            outcomes["webhook"]["skipped_reason"] = "hydrate_slack_enabled disabled"
            webhook_url = ""
        elif not webhook_url:
            outcomes["webhook"]["skipped_reason"] = "no webhook URL configured"
        if webhook_url:
            outcomes["webhook"]["attempted"] = True
            if alert_type in _SEO_WEBHOOK_ALERT_TYPES:
                webhook_payload = _build_seo_slack_payload(
                    alert_type, title, body, threshold_snapshot or {}
                )
            elif alert_type in _HYDRATE_WEBHOOK_ALERT_TYPES:
                webhook_payload = _build_hydrate_slack_payload(
                    alert_type, title, body, threshold_snapshot or {}
                )
            else:
                webhook_payload = {
                    "text": f"🚨 *{title}*\n{body}",
                    "alert_type": alert_type,
                    "service": "syrabit-api",
                }
                if threshold_snapshot:
                    webhook_payload["threshold_snapshot"] = threshold_snapshot
                    webhook_payload["text"] += (
                        f"\n📊 Metric: `{threshold_snapshot.get('metric', 'N/A')}` "
                        f"| Threshold: {threshold_snapshot.get('value', 'N/A')} "
                        f"| Actual: *{threshold_snapshot.get('actual', 'N/A')}*"
                    )
            # Task #453: append the "browser push is silent" advisory to
            # generic and SEO/hydrate Slack payloads alike. The branded
            # _build_*_slack_payload helpers also expose a top-level
            # ``text`` field, so this works uniformly.
            if push_silent_warn_text and isinstance(webhook_payload.get("text"), str):
                webhook_payload["text"] = webhook_payload["text"] + push_silent_warn_text
                webhook_payload["push_silent"] = True
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(webhook_url, json=webhook_payload)
                if 200 <= resp.status_code < 300:
                    outcomes["webhook"]["ok"] = True
                else:
                    outcomes["webhook"]["error"] = f"HTTP {resp.status_code}"
                    logger.debug(f"Alert webhook returned {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        outcomes["webhook"]["error"] = str(e)
        logger.debug(f"Alert webhook failed: {e}")

    # 3) Persist to db.alerts for admin dashboard visibility
    try:
        outcomes["persisted"]["attempted"] = True
        _now_dt = datetime.now(timezone.utc)
        doc = {
            "type": alert_type,
            "title": title,
            "body": body,
            "fired_at": _now_dt.isoformat(),
            "acknowledged": False,
        }
        if threshold_snapshot:
            doc["threshold_snapshot"] = threshold_snapshot
        if mark_synthetic:
            doc["synthetic"] = True
            # Task #433: stamp a BSON Date `expires_at` so the partial TTL
            # index on db.alerts can prune synthetic test alerts ~7 days
            # after they fire. The string `fired_at` is kept as-is for
            # back-compat with the dashboard feed.
            from routes.admin_notifications import _SYNTHETIC_ALERT_TTL_SECONDS
            doc["expires_at"] = _now_dt + timedelta(seconds=_SYNTHETIC_ALERT_TTL_SECONDS)
        await db.alerts.insert_one(doc)
        outcomes["persisted"]["ok"] = True
    except Exception as e:
        outcomes["persisted"]["error"] = str(e)

    # 4) Browser push notification — filtered by per-admin prefs (push_enabled + push_severities)
    #
    # Task #427: Real per-subscriber delivery health is sourced from
    # ``db.push_delivery_log`` after the dispatch completes (or, for queued
    # fire-and-forget alerts, from the most recent prior dispatch). The
    # outcomes["push"] entry below is only used as the immediate response
    # signal for the test-delivery flow — the persisted _channel_status["push"]
    # is recomputed from the log via ``_recompute_push_channel_status``.
    try:
        outcomes["push"]["attempted"] = True
        from routes.admin_notifications import _dispatch_push_to_admins
        push_body = body
        if threshold_snapshot:
            metric = threshold_snapshot.get("metric", "N/A")
            configured = threshold_snapshot.get("value", "N/A")
            actual = threshold_snapshot.get("actual", "N/A")
            push_body = f"{body}\n📊 {metric}: {actual} (threshold: {configured})"
        push_payload = {
            "title": f"\u26a0\ufe0f {title}",
            "body": push_body,
            "icon": "/icons/icon-192.png",
            "url": "/admin",
            "tag": f"{'test' if mark_synthetic else 'critical'}-alert-{alert_type}-{int(now)}",
            "severity": "critical",
            "alert_type": alert_type,
        }
        if mark_synthetic:
            push_payload["synthetic"] = True

        # Task #452 / #453: ``active_admin_subs`` was already counted at the
        # top of _dispatch_alert (so the email/webhook bodies can carry the
        # "browser push is silent" warning). Reuse that result here to
        # short-circuit the push step.
        if active_admin_subs == 0:
            skip_reason = "no active push subscribers"
            try:
                await db.push_delivery_log.insert_one({
                    "dispatch_id": str(uuid.uuid4()),
                    "dispatched_at": datetime.now(timezone.utc).isoformat(),
                    "target": "admin-only",
                    "payload_title": push_payload.get("title", ""),
                    "payload_body": push_payload.get("body", "")[:500],
                    "alert_type": alert_type,
                    "total": 0,
                    "sent": 0,
                    "failed": 0,
                    "expired": 0,
                    "results": [],
                    "skipped": True,
                    "error": skip_reason,
                })
            except Exception as log_exc:
                logger.warning(f"Failed to persist push skip log: {log_exc}")
            outcomes["push"]["skipped_reason"] = skip_reason
        elif force:
            # Test deliveries: await so we can surface failures synchronously.
            try:
                await _dispatch_push_to_admins(push_payload)
                outcomes["push"]["ok"] = True
            except Exception as e:
                outcomes["push"]["error"] = str(e)
        else:
            # Real alerts dispatch fire-and-forget — we cannot await without
            # blocking the alerting loop. The queued-task signal is no longer
            # used for _channel_status["push"]; truth is read from
            # db.push_delivery_log via _recompute_push_channel_status (Task
            # #427). The outcomes["push"] entry below stays unset (ok=False)
            # because no synchronous result is available for the immediate
            # response.
            asyncio.create_task(_dispatch_push_to_admins(push_payload))
            outcomes["push"]["skipped_reason"] = "queued — see push delivery log for result"
    except Exception as e:
        outcomes["push"]["error"] = str(e)
        logger.debug(f"Alert push dispatch failed: {e}")

    # Record per-channel outcomes to in-memory + persisted status for the
    # Alert Settings UI (Task #418). The push channel is sourced from
    # db.push_delivery_log instead of the optimistic queued-task signal so
    # admins see real delivery health (Task #427).
    now_iso = datetime.now(timezone.utc).isoformat()
    for ch in _CHANNEL_STATUS_KEYS:
        if ch == "push":
            continue
        _record_outcome(ch, outcomes[ch], alert_type, now_iso)
    await _recompute_push_channel_status()
    await _persist_channel_status()

    # ── Roll back the persistent claim if every delivery channel failed ──
    # The atomic claim above prevents racing workers from double-firing the
    # same alert, but it would also lock out retries for the full 6h window
    # if all delivery channels happened to be down at claim time. When we
    # observe that nothing was attempted-and-succeeded (push is fire-and-
    # forget, so we ignore it for this check; the persisted alert doc and
    # email/webhook are the synchronous truth), drop the claim so the next
    # alerter tick is free to re-fire.
    if persistent_claimed:
        synchronous_delivered = (
            outcomes.get("persisted", {}).get("ok")
            or outcomes.get("email", {}).get("ok")
            or outcomes.get("webhook", {}).get("ok")
        )
        if not synchronous_delivered:
            try:
                await db.alert_dispatch_log.delete_one(
                    {"dedup_key": dedup_key, "ts": now}
                )
                _alert_last_fired.pop(alert_type, None)
            except Exception as _rb_exc:
                logger.debug(f"persistent cooldown rollback failed for {dedup_key}: {_rb_exc}")

    return outcomes


async def _alerting_loop():
    """Background loop: checks metrics every 2 minutes for alert conditions."""
    await asyncio.sleep(60)   # let startup + first metrics settle
    _prev_errors = 0
    _prev_requests = 0
    _prev_fallbacks = 0
    _prev_llm_calls = 0
    _expire_counter = 0
    while True:
        try:
            await _load_alert_settings()

            _expire_counter += 1
            if _expire_counter >= 15:
                await _auto_expire_alerts()
                _expire_counter = 0
            # ── 1. Error rate in last window ──
            curr_errors = _metrics.error_count
            curr_requests = _metrics.request_count
            delta_err = curr_errors - _prev_errors
            delta_req = curr_requests - _prev_requests
            _prev_errors = curr_errors
            _prev_requests = curr_requests
            if delta_req > 20:   # need minimum sample
                err_rate = (delta_err / delta_req) * 100
                if err_rate > _ALERT_THRESHOLDS["error_rate_pct"]:
                    await _dispatch_alert(
                        "high_error_rate",
                        "Error rate spike",
                        f"{err_rate:.1f}% errors in last 2 min ({delta_err}/{delta_req} requests)",
                        threshold_snapshot={"metric": "error_rate_pct", "value": _ALERT_THRESHOLDS["error_rate_pct"], "actual": round(err_rate, 1)},
                    )

            # ── 2. LLM latency (p95 from _chat_latencies ring buffer) ──
            try:
                from rag import _chat_latencies
                recent_lats = [e["latency_ms"] for e in _chat_latencies[-100:]]
                if len(recent_lats) >= 5:
                    lats_sorted = sorted(recent_lats)
                    p95 = lats_sorted[int(len(lats_sorted) * 0.95)]
                    if p95 > _ALERT_THRESHOLDS["latency_p95_ms"]:
                        await _dispatch_alert(
                            "high_latency",
                            "LLM latency spike",
                            f"p95={int(p95)}ms (threshold: {_ALERT_THRESHOLDS['latency_p95_ms']}ms, sample={len(recent_lats)})",
                            threshold_snapshot={"metric": "latency_p95_ms", "value": _ALERT_THRESHOLDS["latency_p95_ms"], "actual": int(p95)},
                        )
            except Exception:
                pass

            # ── 3. Spoofed bot UA rate ──
            spoof_rpm = _metrics.get_spoof_rpm()
            if spoof_rpm >= _ALERT_THRESHOLDS["spoof_rpm"]:
                spoof_stats = _metrics.get_spoof_stats()
                top_bots = sorted(spoof_stats["by_bot"].items(), key=lambda x: -x[1])[:5]
                top_str = ", ".join(f"{b}={c}" for b, c in top_bots)
                await _dispatch_alert(
                    "spoofed_bot_surge",
                    "Spoofed bot UA surge detected",
                    f"{spoof_rpm:.0f} spoofed requests/min (threshold: {_ALERT_THRESHOLDS['spoof_rpm']}). "
                    f"Total lifetime: {spoof_stats['total']}. Top claimed bots: {top_str}",
                    threshold_snapshot={"metric": "spoof_rpm", "value": _ALERT_THRESHOLDS["spoof_rpm"], "actual": round(spoof_rpm)},
                )

            # ── 4. Fallback rate (from cost log provider != primary) ──
            from routes.admin_advanced import _llm_cost_log
            recent_cost = _llm_cost_log[-100:]
            if len(recent_cost) >= 10:
                primary_model = LLM_MODEL
                fallbacks = sum(1 for e in recent_cost if e.get("model") != primary_model)
                fb_rate = (fallbacks / len(recent_cost)) * 100
                if fb_rate > _ALERT_THRESHOLDS["fallback_rate_pct"]:
                    await _dispatch_alert(
                        "high_fallback_rate",
                        "LLM fallback rate high",
                        f"{fb_rate:.0f}% of last {len(recent_cost)} calls used fallback models "
                        f"(primary: {primary_model})",
                        threshold_snapshot={"metric": "fallback_rate_pct", "value": _ALERT_THRESHOLDS["fallback_rate_pct"], "actual": round(fb_rate, 1)},
                    )

            # ── 5. Collection size growth rate ──
            try:
                _growth_threshold = _ALERT_THRESHOLDS.get("collection_growth_per_day", 500)
                if _growth_threshold > 0:
                    _now_growth = datetime.now(timezone.utc)
                    _yesterday = (_now_growth - timedelta(days=1)).strftime("%Y-%m-%d")
                    _today_str = _now_growth.strftime("%Y-%m-%d")
                    _snapshots = await db.collection_size_history.find(
                        {"collection": "bot_spoof_attempts", "date": {"$in": [_yesterday, _today_str]}},
                        {"_id": 0, "date": 1, "size": 1},
                    ).to_list(2)
                    if len(_snapshots) == 2:
                        _snap_map = {s["date"]: s["size"] for s in _snapshots}
                        if _yesterday in _snap_map and _today_str in _snap_map:
                            _daily_growth = _snap_map[_today_str] - _snap_map[_yesterday]
                            if _daily_growth > _growth_threshold:
                                await _dispatch_alert(
                                    "collection_growth_spike",
                                    "Collection size growing fast",
                                    f"bot_spoof_attempts grew by {_daily_growth:,} docs in 1 day "
                                    f"(threshold: {_growth_threshold:,}/day)",
                                    threshold_snapshot={
                                        "metric": "collection_growth_per_day",
                                        "value": _growth_threshold,
                                        "actual": _daily_growth,
                                    },
                                )
            except Exception:
                pass

            # ── 6. Assamese-purity override refresh staleness (Task #432) ──
            # Each gunicorn worker polls mongo every ~15s; a stalled loop
            # means PATCH/DELETE on the override never propagates to
            # this worker. Page on-call once we're 4× past the budget.
            try:
                _stale_threshold = float(_ALERT_THRESHOLDS.get("assamese_refresh_stale_seconds", 60) or 0)
                if _stale_threshold > 0:
                    _age = get_assamese_refresh_age_seconds()
                    if _age > _stale_threshold:
                        _worker_pid = os.getpid()
                        await _dispatch_alert(
                            "assamese_override_refresh_stalled",
                            "Assamese override refresh loop stalled",
                            f"Worker pid={_worker_pid} has not refreshed the Assamese-purity override "
                            f"from mongo for {int(_age)}s (threshold: {int(_stale_threshold)}s, "
                            f"poll cadence: 15s). PATCH/DELETE on /admin/assamese-purity will not "
                            f"propagate to this worker until the loop recovers. Check api logs for "
                            f"'[INDIC-SANITIZE] refresh loop tick failed' warnings and verify mongo "
                            f"connectivity. See RUNBOOK.md › Assamese purity override propagation.",
                            threshold_snapshot={
                                "metric": "assamese_refresh_stale_seconds",
                                "value": _stale_threshold,
                                "actual": int(_age),
                                "worker_pid": _worker_pid,
                            },
                        )
            except Exception:
                pass

            # ── 7. Credit-deduct fallback rate (Task #769) ────────────
            # `db_ops.atomic_deduct_credit` records every fall-through
            # past the Postgres path. If the rolling 5-min rate stays
            # above the configured per-min threshold, page on-call —
            # we're silently charging credits via Redis (or worse,
            # Supabase) while the canonical PG ledger is untouched.
            try:
                _cf_threshold = float(_ALERT_THRESHOLDS.get("credit_deduct_fallback_per_min", 5) or 0)
                if _cf_threshold > 0:
                    _cf_stats = get_credit_fallback_stats(300)
                    if _cf_stats["rate_per_min"] > _cf_threshold:
                        await _dispatch_alert(
                            "credit_deduct_fallback_high",
                            "Credit deduction silently falling back to Redis/Supabase",
                            f"{_cf_stats['total']} credit-deduct fallbacks in last 5 min "
                            f"(redis={_cf_stats['by_path']['redis']}, "
                            f"supabase={_cf_stats['by_path']['supabase']}, "
                            f"rate={_cf_stats['rate_per_min']:.1f}/min, "
                            f"threshold: {_cf_threshold:.0f}/min). "
                            f"Postgres credit path is degraded — verify pg_pool health "
                            f"and DATABASE_URL connectivity. Until PG recovers, the "
                            f"daily counter is seeded from possibly-stale reads, which "
                            f"may drift from the real ledger.",
                            threshold_snapshot={
                                "metric": "credit_deduct_fallback_per_min",
                                "value": _cf_threshold,
                                "actual": _cf_stats["rate_per_min"],
                                "by_path": _cf_stats["by_path"],
                                "window_seconds": _cf_stats["window_seconds"],
                            },
                        )
            except Exception:
                pass

            # ── 8. Workers AI 429 burst (Task #70) ──────────────────────
            # Fires when Workers AI returns >= threshold 429s in the last
            # 180s without any successful call resetting the counter.
            # 180s window > 120s loop interval so a burst near a tick
            # boundary is never silently missed.  Redis TTL is refreshed
            # on every 429 hit, so an ongoing outage stays counted.
            # Counter is cleared by mark_ok() on the next successful call.
            try:
                _wai_raw = _ALERT_THRESHOLDS.get("workers_ai_429_burst_threshold")
                try:
                    _wai_threshold = int(_wai_raw) if _wai_raw is not None else 5
                except (TypeError, ValueError):
                    _wai_threshold = 5
                if _wai_threshold > 0:
                    from llm import get_provider_429_burst, _PROVIDER_429_BURST_WINDOW_S
                    _wai_burst = get_provider_429_burst("workers-ai", _PROVIDER_429_BURST_WINDOW_S)
                    if _wai_burst >= _wai_threshold:
                        await _dispatch_alert(
                            "workers_ai_429_burst",
                            "Workers AI rate-limit burst — chat may be unavailable",
                            f"{_wai_burst} Workers AI 429 rate-limit responses recorded "
                            f"in the last {_PROVIDER_429_BURST_WINDOW_S}s (threshold: {_wai_threshold}). "
                            f"Chat completions are being throttled by Cloudflare Workers AI. "
                            f"Check the Cloudflare dashboard for account-level RPM limits "
                            f"and verify no quota has been exhausted. "
                            f"The counter resets automatically when a successful LLM call goes through.",
                            threshold_snapshot={
                                "metric": "workers_ai_429_burst_threshold",
                                "value": _wai_threshold,
                                "actual": _wai_burst,
                                "window_seconds": _PROVIDER_429_BURST_WINDOW_S,
                            },
                        )
            except Exception:
                pass

            # ── 9. Groq 429 burst (Task #75) ─────────────────────────────
            # Same semantics as check #8.  Groq has a 30 RPM free-tier cap
            # so 5 hits in 180 s means it is fully throttled.
            try:
                _groq_raw = _ALERT_THRESHOLDS.get("groq_429_burst_threshold")
                try:
                    _groq_threshold = int(_groq_raw) if _groq_raw is not None else 5
                except (TypeError, ValueError):
                    _groq_threshold = 5
                if _groq_threshold > 0:
                    from llm import get_provider_429_burst, _PROVIDER_429_BURST_WINDOW_S
                    _groq_burst = get_provider_429_burst("groq", _PROVIDER_429_BURST_WINDOW_S)
                    if _groq_burst >= _groq_threshold:
                        await _dispatch_alert(
                            "groq_429_burst",
                            "Groq rate-limit burst — fallback LLM throttled",
                            f"{_groq_burst} Groq 429 rate-limit responses recorded "
                            f"in the last {_PROVIDER_429_BURST_WINDOW_S}s (threshold: {_groq_threshold}). "
                            f"Groq is being throttled, which may affect chat fallback availability. "
                            f"Check your Groq account RPM limits and key usage. "
                            f"The counter resets automatically when a successful Groq call goes through.",
                            threshold_snapshot={
                                "metric": "groq_429_burst_threshold",
                                "value": _groq_threshold,
                                "actual": _groq_burst,
                                "window_seconds": _PROVIDER_429_BURST_WINDOW_S,
                            },
                        )
            except Exception:
                pass

            # ── 10. Gemini 429 burst (Task #75) ──────────────────────────
            # Same semantics.  Gemini's paid quota is high so a burst here
            # usually indicates account-level quota exhaustion.
            try:
                _gemini_raw = _ALERT_THRESHOLDS.get("gemini_429_burst_threshold")
                try:
                    _gemini_threshold = int(_gemini_raw) if _gemini_raw is not None else 5
                except (TypeError, ValueError):
                    _gemini_threshold = 5
                if _gemini_threshold > 0:
                    from llm import get_provider_429_burst, _PROVIDER_429_BURST_WINDOW_S
                    _gemini_burst = get_provider_429_burst("gemini", _PROVIDER_429_BURST_WINDOW_S)
                    if _gemini_burst >= _gemini_threshold:
                        await _dispatch_alert(
                            "gemini_429_burst",
                            "Gemini rate-limit burst — AI provider quota may be exhausted",
                            f"{_gemini_burst} Gemini 429 rate-limit responses recorded "
                            f"in the last {_PROVIDER_429_BURST_WINDOW_S}s (threshold: {_gemini_threshold}). "
                            f"Gemini is being throttled, which may affect chat availability. "
                            f"Check your Google AI Studio / Vertex AI quota in the Google Cloud Console. "
                            f"The counter resets automatically when a successful Gemini call goes through.",
                            threshold_snapshot={
                                "metric": "gemini_429_burst_threshold",
                                "value": _gemini_threshold,
                                "actual": _gemini_burst,
                                "window_seconds": _PROVIDER_429_BURST_WINDOW_S,
                            },
                        )
            except Exception:
                pass

            # ── 11. Azure OpenAI 429 burst (Task #373) ───────────────────
            # Same TTL-consecutive semantics as checks #8–#10.  Azure OpenAI
            # is the primary path for the gpt-4o-mini / gpt-4.1-mini chat
            # branch (and the failover chain for several feature pools), so
            # a sustained 429 burst means we're being throttled at the
            # deployment quota — chat will degrade even though Workers AI
            # itself looks fine.  Redis key: azure_429_burst.
            try:
                _az_raw = _ALERT_THRESHOLDS.get("azure_openai_429_burst_threshold")
                try:
                    _az_threshold = int(_az_raw) if _az_raw is not None else 5
                except (TypeError, ValueError):
                    _az_threshold = 5
                if _az_threshold > 0:
                    from llm import get_provider_429_burst, _PROVIDER_429_BURST_WINDOW_S
                    _az_burst = get_provider_429_burst("azure_openai", _PROVIDER_429_BURST_WINDOW_S)
                    if _az_burst >= _az_threshold:
                        await _dispatch_alert(
                            "azure_openai_429_burst",
                            "Azure OpenAI rate-limit burst — chat fallback throttled",
                            f"{_az_burst} Azure OpenAI 429 rate-limit responses recorded "
                            f"in the last {_PROVIDER_429_BURST_WINDOW_S}s (threshold: {_az_threshold}). "
                            f"Azure OpenAI is being throttled, which may affect chat completions "
                            f"and any feature pool that fails over to Azure. "
                            f"Check the Azure OpenAI deployment's TPM/RPM quota in the Azure portal "
                            f"and verify no quota has been exhausted. "
                            f"The counter resets automatically when a successful Azure call goes through.",
                            threshold_snapshot={
                                "metric": "azure_openai_429_burst_threshold",
                                "value": _az_threshold,
                                "actual": _az_burst,
                                "window_seconds": _PROVIDER_429_BURST_WINDOW_S,
                            },
                        )
            except Exception:
                pass

            # ── 12. Deepgram 429 burst (Task #373) ───────────────────────
            # Deepgram is our primary STT (nova-3) and TTS (Aura-2) provider.
            # A sustained 429 burst means voice features (transcription,
            # text-to-speech) are degrading — that's invisible from a
            # chat-completions health check, so it gets its own alert.
            # Redis key: deepgram_429_burst.
            try:
                _dg_raw = _ALERT_THRESHOLDS.get("deepgram_429_burst_threshold")
                try:
                    _dg_threshold = int(_dg_raw) if _dg_raw is not None else 5
                except (TypeError, ValueError):
                    _dg_threshold = 5
                if _dg_threshold > 0:
                    from llm import get_provider_429_burst, _PROVIDER_429_BURST_WINDOW_S
                    _dg_burst = get_provider_429_burst("deepgram", _PROVIDER_429_BURST_WINDOW_S)
                    if _dg_burst >= _dg_threshold:
                        await _dispatch_alert(
                            "deepgram_429_burst",
                            "Deepgram rate-limit burst — voice features throttled",
                            f"{_dg_burst} Deepgram 429 rate-limit responses recorded "
                            f"in the last {_PROVIDER_429_BURST_WINDOW_S}s (threshold: {_dg_threshold}). "
                            f"Deepgram is being throttled, which will degrade speech-to-text and "
                            f"text-to-speech features (chat voice replies, transcription). "
                            f"Check your Deepgram account concurrency / RPM limits and key usage. "
                            f"The counter resets automatically when a successful Deepgram call goes through.",
                            threshold_snapshot={
                                "metric": "deepgram_429_burst_threshold",
                                "value": _dg_threshold,
                                "actual": _dg_burst,
                                "window_seconds": _PROVIDER_429_BURST_WINDOW_S,
                            },
                        )
            except Exception:
                pass

        except Exception as exc:
            logger.debug(f"Alerting loop error: {exc}")

        await asyncio.sleep(120)   # check every 2 minutes


# Admin endpoints for alert management
