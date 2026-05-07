"""Syrabit.ai — Chat speed-up impact tracking (Task #303).

Lightweight per-day counters that measure how often the latency optimizations
introduced in Task #282 (early Redis cache hits, instant casual fast-path,
speculative web search, 6-hour cache pre-warm cycle) actually fire in
production. Designed to be cheap (in-memory dict, lock-protected) so the chat
hot path pays effectively zero cost.

The numbers are surfaced via ``GET /api/admin/chat/speedups`` for a per-day
breakdown of cache hit rate, warm-cache hit %, speculative-web fallback %,
average TTFB, and the most recent cache-warm runs.
"""
from __future__ import annotations

import json
import logging
import threading
from collections import deque
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Deque, Optional

__all__ = [
    "record_chat_started",
    "record_early_cache_hit",
    "record_pre_sse_cache_hit",
    "record_instant_fastpath",
    "record_speculative_web",
    "record_ttfb",
    "record_total_latency",
    "record_lang_ttfb",
    "record_warm_run",
    "record_provider_call",
    "record_provider_fallback",
    "snapshot",
    "load_from_store",
    "flush_to_store",
    "periodic_flush_loop",
]

logger = logging.getLogger(__name__)

# Redis key layout (v2 — atomic, multi-worker safe):
#   chat_speedup:day:{YYYY-MM-DD}  hash of counter -> int / float
#   chat_speedup:days              SET of date strings we've seen
#   chat_speedup:warm_runs         LIST of JSON warm-run entries (newest at head)
# Each chat event bumps an in-memory _delta dict on top of _daily; the
# periodic flush atomically applies _delta to Redis via HINCRBY/HINCRBYFLOAT
# (so multiple workers can flush concurrently without clobbering), then
# resets the delta. On startup every worker rehydrates _daily from Redis so
# the /admin/chat/speedups endpoint shows continuous history regardless of
# which worker handles the request.
_REDIS_PREFIX = "chat_speedup"
_REDIS_DAY_KEY = _REDIS_PREFIX + ":day:{date}"
_REDIS_DAYS_INDEX = _REDIS_PREFIX + ":days"
_REDIS_WARM_RUNS_KEY = _REDIS_PREFIX + ":warm_runs"
# TTL long enough to retain history through a multi-day outage (60 days).
_REDIS_TTL = 60 * 24 * 3600
_FLUSH_INTERVAL_SEC = 30

_MAX_DAYS = 30
_MAX_WARM_RUNS = 50

# Field-type maps for stable serialization/deserialization.
_INT_FIELDS = frozenset((
    "chats_total", "early_cache_hits", "pre_sse_cache_hits", "instant_fastpath",
    "speculative_web_started", "speculative_web_used", "speculative_web_discarded",
    "ttfb_count", "total_count",
))
_FLOAT_FIELDS = frozenset(("ttfb_ms_sum", "total_ms_sum"))

_lock = threading.Lock()
# { "YYYY-MM-DD": {counter_name: int|float, ...} } — current view (loaded from
# Redis at startup + this worker's local events since then).
_daily: Dict[str, Dict[str, float]] = {}
# { "YYYY-MM-DD": {counter_name: int|float, ...} } — delta accumulated since
# the last successful flush. Cleared atomically on flush.
_delta: Dict[str, Dict[str, float]] = {}
_warm_runs: Deque[Dict[str, Any]] = deque(maxlen=_MAX_WARM_RUNS)
# Warm-run entries recorded since the last flush.
_warm_runs_pending: Deque[Dict[str, Any]] = deque(maxlen=_MAX_WARM_RUNS)

# Provider-tagged latency tracking (Task #626):
#   _provider_daily[date][provider] = {
#       ttfb_ms_sum, ttfb_count, total_ms_sum, total_count, calls
#   }
#   _provider_fallbacks[date] = {(from_provider, to_provider): count}
# In-memory only — survives within a worker but resets on restart. We
# rely on the fact that providers are called continuously, so a fresh
# baseline rebuilds within minutes.
_PROVIDER_NAME_MAX = 32
_provider_daily: Dict[str, Dict[str, Dict[str, float]]] = {}
_provider_fallbacks: Dict[str, Dict[str, int]] = {}

# Per-language TTFB tracking (S6 — enables Assamese vs English TTFB comparison):
#   _lang_daily[date][lang_code] = {ttfb_ms_sum, ttfb_count, total_ms_sum, total_count, calls}
# In-memory only; not persisted to Redis (rebuilds quickly from live traffic).
_lang_daily: Dict[str, Dict[str, Dict[str, float]]] = {}


def _today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


_DEFAULT_BUCKET = {
    "chats_total": 0,
    "early_cache_hits": 0,        # Redis/memory hit before any preprocessing
    "pre_sse_cache_hits": 0,      # cache hit just before SSE stream begins
    "instant_fastpath": 0,        # casual instant-response fast path
    "speculative_web_started": 0, # how often we kicked off the speculative fetch
    "speculative_web_used": 0,    # internal RAG missed → web results used
    "speculative_web_discarded": 0, # internal RAG hit → web results dropped
    "ttfb_ms_sum": 0.0,
    "ttfb_count": 0,
    "total_ms_sum": 0.0,
    "total_count": 0,
}


def _new_bucket() -> Dict[str, float]:
    return dict(_DEFAULT_BUCKET)


def _trim_old_days(target: Dict[str, Dict[str, float]]) -> None:
    if len(target) <= _MAX_DAYS:
        return
    cutoff = (datetime.now(timezone.utc) - timedelta(days=_MAX_DAYS)).strftime("%Y-%m-%d")
    for k in list(target.keys()):
        if k < cutoff:
            target.pop(k, None)


def _bucket() -> Dict[str, float]:
    """Lock-held: return today's _daily bucket, creating it on first access."""
    key = _today_key()
    bucket = _daily.get(key)
    if bucket is None:
        bucket = _new_bucket()
        _daily[key] = bucket
        _trim_old_days(_daily)
    return bucket


def _delta_bucket() -> Dict[str, float]:
    """Lock-held: return today's _delta bucket, creating it on first access."""
    key = _today_key()
    bucket = _delta.get(key)
    if bucket is None:
        bucket = {}
        _delta[key] = bucket
    return bucket


def _bump(name: str, delta: float = 1) -> None:
    with _lock:
        b = _bucket()
        b[name] = (b.get(name, 0) or 0) + delta
        d = _delta_bucket()
        d[name] = (d.get(name, 0) or 0) + delta


def record_chat_started() -> None:
    _bump("chats_total")


def record_early_cache_hit() -> None:
    _bump("early_cache_hits")


def record_pre_sse_cache_hit() -> None:
    _bump("pre_sse_cache_hits")


def record_instant_fastpath() -> None:
    _bump("instant_fastpath")


def record_speculative_web(*, used: bool, discarded: bool) -> None:
    with _lock:
        b = _bucket()
        d = _delta_bucket()
        b["speculative_web_started"] = (b.get("speculative_web_started", 0) or 0) + 1
        d["speculative_web_started"] = (d.get("speculative_web_started", 0) or 0) + 1
        if used:
            b["speculative_web_used"] = (b.get("speculative_web_used", 0) or 0) + 1
            d["speculative_web_used"] = (d.get("speculative_web_used", 0) or 0) + 1
        if discarded:
            b["speculative_web_discarded"] = (b.get("speculative_web_discarded", 0) or 0) + 1
            d["speculative_web_discarded"] = (d.get("speculative_web_discarded", 0) or 0) + 1


def record_ttfb(ms: float) -> None:
    if ms < 0:
        return
    with _lock:
        b = _bucket()
        d = _delta_bucket()
        b["ttfb_ms_sum"] = (b.get("ttfb_ms_sum", 0.0) or 0.0) + float(ms)
        b["ttfb_count"] = (b.get("ttfb_count", 0) or 0) + 1
        d["ttfb_ms_sum"] = (d.get("ttfb_ms_sum", 0.0) or 0.0) + float(ms)
        d["ttfb_count"] = (d.get("ttfb_count", 0) or 0) + 1


def record_total_latency(ms: float) -> None:
    if ms < 0:
        return
    with _lock:
        b = _bucket()
        d = _delta_bucket()
        b["total_ms_sum"] = (b.get("total_ms_sum", 0.0) or 0.0) + float(ms)
        b["total_count"] = (b.get("total_count", 0) or 0) + 1
        d["total_ms_sum"] = (d.get("total_ms_sum", 0.0) or 0.0) + float(ms)
        d["total_count"] = (d.get("total_count", 0) or 0) + 1


def _norm_provider(provider: str) -> str:
    p = (provider or "").strip().lower()[:_PROVIDER_NAME_MAX]
    return p or "unknown"


def _provider_bucket(date: str, provider: str) -> Dict[str, float]:
    day = _provider_daily.setdefault(date, {})
    b = day.get(provider)
    if b is None:
        b = {"ttfb_ms_sum": 0.0, "ttfb_count": 0,
             "total_ms_sum": 0.0, "total_count": 0,
             "calls": 0}
        day[provider] = b
    return b


def record_provider_call(provider: str, *, ttfb_ms: float = 0.0, total_ms: float = 0.0) -> None:
    """Record a per-provider chat completion. Either ttfb_ms, total_ms, or
    both may be supplied — call once with whichever metrics are available
    at the call site. ``provider`` should be the same tag emitted as
    ``__provider`` in the SSE stream (e.g. 'azure_openai', 'workers_ai')."""
    p = _norm_provider(provider)
    with _lock:
        b = _provider_bucket(_today_key(), p)
        b["calls"] = (b.get("calls", 0) or 0) + 1
        if ttfb_ms and ttfb_ms > 0:
            b["ttfb_ms_sum"] += float(ttfb_ms)
            b["ttfb_count"] += 1
        if total_ms and total_ms > 0:
            b["total_ms_sum"] += float(total_ms)
            b["total_count"] += 1
    # Mirror the un-tagged latency counters so existing dashboards keep working.
    if ttfb_ms and ttfb_ms > 0:
        record_ttfb(ttfb_ms)
    if total_ms and total_ms > 0:
        record_total_latency(total_ms)


def record_lang_ttfb(lang: str, ttfb_ms: float = 0.0, total_ms: float = 0.0) -> None:
    """Record TTFB/total latency keyed by response language (e.g. 'en', 'as').
    Enables per-language P95 monitoring so Assamese regressions surface
    independently from English in the /api/admin/chat/speedups dashboard."""
    lang_key = (lang or "en")[:8].lower()
    with _lock:
        day = _lang_daily.setdefault(_today_key(), {})
        b = day.setdefault(lang_key, {
            "ttfb_ms_sum": 0.0, "ttfb_count": 0,
            "total_ms_sum": 0.0, "total_count": 0,
            "calls": 0,
        })
        b["calls"] = b.get("calls", 0) + 1
        if ttfb_ms > 0:
            b["ttfb_ms_sum"] = b.get("ttfb_ms_sum", 0.0) + float(ttfb_ms)
            b["ttfb_count"] = b.get("ttfb_count", 0) + 1
        if total_ms > 0:
            b["total_ms_sum"] = b.get("total_ms_sum", 0.0) + float(total_ms)
            b["total_count"] = b.get("total_count", 0) + 1


def record_provider_fallback(from_provider: str, to_provider: str) -> None:
    """Record a fallback transition (e.g. vertex_gemini → openai/gpt-oss-20b
    when Vertex fails before first token). The from→to pair is the bucket
    key so each transition surfaces independently in the dashboard."""
    src = _norm_provider(from_provider)
    dst = _norm_provider(to_provider)
    key = f"{src}->{dst}"
    with _lock:
        day = _provider_fallbacks.setdefault(_today_key(), {})
        day[key] = (day.get(key, 0) or 0) + 1


def record_warm_run(result: Dict[str, Any]) -> None:
    """Record a cache-warm run result (from _perform_cache_warm)."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "warmed": int(result.get("warmed", 0) or 0),
        "already_cached": int(result.get("already_cached", 0) or 0),
        "failed": int(result.get("failed", 0) or 0),
        "total_queries": int(result.get("total_queries", 0) or 0),
        "source": str(result.get("source", "unknown")),
    }
    with _lock:
        _warm_runs.append(entry)
        _warm_runs_pending.append(entry)


def _pct(numer: float, denom: float) -> float:
    if denom <= 0:
        return 0.0
    return round((numer / denom) * 100.0, 2)


def _avg(total: float, count: float) -> float:
    if count <= 0:
        return 0.0
    return round(total / count, 1)


def snapshot(days: int = 7) -> Dict[str, Any]:
    """Return a per-day breakdown plus rolled-up totals across the window."""
    days = max(1, min(int(days), _MAX_DAYS))
    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=days - 1)
    cutoff = cutoff_dt.strftime("%Y-%m-%d")

    with _lock:
        items = sorted(
            ((d, dict(b)) for d, b in _daily.items() if d >= cutoff),
            key=lambda x: x[0],
        )
        warm_runs_snapshot = [dict(r) for r in _warm_runs
                              if r["ts"][:10] >= cutoff]

    daily = []
    totals: Dict[str, float] = {}
    for d, b in items:
        chats = b.get("chats_total", 0) or 0
        early = b.get("early_cache_hits", 0) or 0
        pre_sse = b.get("pre_sse_cache_hits", 0) or 0
        instant = b.get("instant_fastpath", 0) or 0
        spec_started = b.get("speculative_web_started", 0) or 0
        spec_used = b.get("speculative_web_used", 0) or 0
        spec_disc = b.get("speculative_web_discarded", 0) or 0
        cache_hits = early + pre_sse
        daily.append({
            "date": d,
            "chats_total": chats,
            "early_cache_hits": early,
            "pre_sse_cache_hits": pre_sse,
            "instant_fastpath": instant,
            "cache_hit_pct": _pct(cache_hits, chats),
            "warmed_cache_hit_pct": _pct(early, chats),
            "speculative_web_started": spec_started,
            "speculative_web_used": spec_used,
            "speculative_web_discarded": spec_disc,
            "speculative_web_used_pct": _pct(spec_used, chats),
            "speculative_web_useful_pct": _pct(spec_used, spec_started),
            "avg_ttfb_ms": _avg(b.get("ttfb_ms_sum", 0.0), b.get("ttfb_count", 0)),
            "avg_total_ms": _avg(b.get("total_ms_sum", 0.0), b.get("total_count", 0)),
            "ttfb_samples": int(b.get("ttfb_count", 0) or 0),
        })
        for k in (
            "chats_total", "early_cache_hits", "pre_sse_cache_hits",
            "instant_fastpath", "speculative_web_started",
            "speculative_web_used", "speculative_web_discarded",
            "ttfb_ms_sum", "ttfb_count", "total_ms_sum", "total_count",
        ):
            totals[k] = totals.get(k, 0) + (b.get(k, 0) or 0)

    chats = totals.get("chats_total", 0)
    early = totals.get("early_cache_hits", 0)
    pre_sse = totals.get("pre_sse_cache_hits", 0)
    spec_started = totals.get("speculative_web_started", 0)
    spec_used = totals.get("speculative_web_used", 0)
    cache_hits = early + pre_sse

    # ── Per-provider breakdown (Task #626) ─────────────────────────────
    by_provider: Dict[str, Dict[str, Any]] = {}
    fallbacks_total: Dict[str, int] = {}
    with _lock:
        for d, providers in _provider_daily.items():
            if d < cutoff:
                continue
            for prov, b in providers.items():
                tgt = by_provider.setdefault(prov, {
                    "provider": prov,
                    "calls": 0,
                    "ttfb_ms_sum": 0.0, "ttfb_count": 0,
                    "total_ms_sum": 0.0, "total_count": 0,
                })
                tgt["calls"] += int(b.get("calls", 0) or 0)
                tgt["ttfb_ms_sum"] += float(b.get("ttfb_ms_sum", 0.0) or 0.0)
                tgt["ttfb_count"] += int(b.get("ttfb_count", 0) or 0)
                tgt["total_ms_sum"] += float(b.get("total_ms_sum", 0.0) or 0.0)
                tgt["total_count"] += int(b.get("total_count", 0) or 0)
        for d, transitions in _provider_fallbacks.items():
            if d < cutoff:
                continue
            for k, n in transitions.items():
                fallbacks_total[k] = fallbacks_total.get(k, 0) + int(n)

    providers_list = []
    for prov, agg in sorted(by_provider.items()):
        providers_list.append({
            "provider": prov,
            "calls": int(agg["calls"]),
            "avg_ttfb_ms": _avg(agg["ttfb_ms_sum"], agg["ttfb_count"]),
            "avg_total_ms": _avg(agg["total_ms_sum"], agg["total_count"]),
            "ttfb_samples": int(agg["ttfb_count"]),
            "total_samples": int(agg["total_count"]),
            "tokens_per_sec": (round(agg["total_count"] / (agg["total_ms_sum"] / 1000.0), 2)
                               if agg["total_ms_sum"] > 0 else 0.0),
        })
    fallbacks_list = [
        {"transition": k, "count": int(v)}
        for k, v in sorted(fallbacks_total.items(), key=lambda x: -x[1])
    ]

    # ── Per-language TTFB breakdown (S6) ───────────────────────────────────────
    by_lang_agg: Dict[str, Dict[str, Any]] = {}
    with _lock:
        for d, langs in _lang_daily.items():
            if d < cutoff:
                continue
            for lk, b in langs.items():
                tgt = by_lang_agg.setdefault(lk, {
                    "calls": 0,
                    "ttfb_ms_sum": 0.0, "ttfb_count": 0,
                    "total_ms_sum": 0.0, "total_count": 0,
                })
                tgt["calls"] += int(b.get("calls", 0) or 0)
                tgt["ttfb_ms_sum"] += float(b.get("ttfb_ms_sum", 0.0) or 0.0)
                tgt["ttfb_count"] += int(b.get("ttfb_count", 0) or 0)
                tgt["total_ms_sum"] += float(b.get("total_ms_sum", 0.0) or 0.0)
                tgt["total_count"] += int(b.get("total_count", 0) or 0)
    lang_list = [
        {
            "lang": lk,
            "calls": int(agg["calls"]),
            "avg_ttfb_ms": _avg(agg["ttfb_ms_sum"], agg["ttfb_count"]),
            "avg_total_ms": _avg(agg["total_ms_sum"], agg["total_count"]),
            "ttfb_samples": int(agg["ttfb_count"]),
        }
        for lk, agg in sorted(by_lang_agg.items())
    ]

    return {
        "period_days": days,
        "totals": {
            "chats_total": int(chats),
            "early_cache_hits": int(early),
            "pre_sse_cache_hits": int(pre_sse),
            "instant_fastpath": int(totals.get("instant_fastpath", 0)),
            "cache_hit_pct": _pct(cache_hits, chats),
            "warmed_cache_hit_pct": _pct(early, chats),
            "speculative_web_started": int(spec_started),
            "speculative_web_used": int(spec_used),
            "speculative_web_discarded": int(totals.get("speculative_web_discarded", 0)),
            "speculative_web_used_pct": _pct(spec_used, chats),
            "speculative_web_useful_pct": _pct(spec_used, spec_started),
            "avg_ttfb_ms": _avg(totals.get("ttfb_ms_sum", 0.0), totals.get("ttfb_count", 0)),
            "avg_total_ms": _avg(totals.get("total_ms_sum", 0.0), totals.get("total_count", 0)),
            "ttfb_samples": int(totals.get("ttfb_count", 0)),
        },
        "daily": daily,
        "warm_runs": list(reversed(warm_runs_snapshot))[:20],
        "by_provider": providers_list,
        "provider_fallbacks": fallbacks_list,
        "by_lang": lang_list,
        "has_data": chats > 0,
    }


# ── Persistence (Task #310) ────────────────────────────────────────────────────
# Daily counters and warm-run history are flushed to Redis on a short interval
# and on shutdown, then rehydrated from Redis on startup so historical days
# survive deploys/restarts.
#
# Multi-worker safety:
# Counters are stored as Redis hashes (one per day) and updated with atomic
# HINCRBY / HINCRBYFLOAT against a per-worker delta of unflushed events. This
# means N gunicorn workers can flush concurrently and their increments add
# correctly instead of overwriting each other.

def _coerce_field(field: str, raw: Any) -> Optional[float]:
    """Decode a Redis hash value back to int or float based on field type."""
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    try:
        if field in _FLOAT_FIELDS:
            return float(raw)
        if field in _INT_FIELDS:
            return int(float(raw))
        # Unknown field: prefer int, fall back to float
        try:
            return int(float(raw))
        except Exception:
            return float(raw)
    except (TypeError, ValueError):
        return None


def load_from_store() -> bool:
    """Rehydrate _daily/_warm_runs from Redis. Safe to call on every worker
    at startup; downstream flushes from each worker only push that worker's
    own delta on top of the shared aggregate."""
    try:
        from deps import redis_client
    except Exception:
        return False
    if not redis_client:
        return False
    try:
        raw_dates = redis_client.smembers(_REDIS_DAYS_INDEX) or []
        cutoff = (datetime.now(timezone.utc) - timedelta(days=_MAX_DAYS)).strftime("%Y-%m-%d")
        loaded_daily: Dict[str, Dict[str, float]] = {}
        for raw_date in raw_dates:
            d = raw_date.decode() if isinstance(raw_date, bytes) else raw_date
            if not isinstance(d, str) or d < cutoff:
                continue
            h = redis_client.hgetall(_REDIS_DAY_KEY.format(date=d)) or {}
            if not h:
                continue
            bucket = _new_bucket()
            for field, raw_val in h.items():
                f = field.decode() if isinstance(field, bytes) else field
                coerced = _coerce_field(f, raw_val)
                if coerced is not None:
                    bucket[f] = coerced
            loaded_daily[d] = bucket
        raw_warm = redis_client.lrange(_REDIS_WARM_RUNS_KEY, 0, _MAX_WARM_RUNS - 1) or []
        loaded_warm = []
        seen_ts = set()
        for raw in raw_warm:
            r_str = raw.decode() if isinstance(raw, bytes) else raw
            try:
                entry = json.loads(r_str)
            except Exception:
                continue
            if isinstance(entry, dict) and entry.get("ts") and entry["ts"] not in seen_ts:
                seen_ts.add(entry["ts"])
                loaded_warm.append(entry)
        # Redis LIST is newest-first (we LPUSH); reverse so our deque ends up
        # with newest at the right (matching local append() semantics).
        loaded_warm.reverse()
        with _lock:
            # Replace in-place so concurrent counters added since startup
            # (during the brief load window) are not stomped.
            for d, bucket in loaded_daily.items():
                if d not in _daily:
                    _daily[d] = bucket
                else:
                    # Take the larger of each value — the in-memory bucket may
                    # already contain a few local events recorded between the
                    # smembers/hgetall calls and this point.
                    existing = _daily[d]
                    for k, v in bucket.items():
                        existing[k] = max(existing.get(k, 0) or 0, v)
            _trim_old_days(_daily)
            existing_ts = {r.get("ts") for r in _warm_runs}
            for r in loaded_warm:
                if r.get("ts") not in existing_ts:
                    _warm_runs.append(r)
                    existing_ts.add(r.get("ts"))
            day_count = len(_daily)
            warm_count = len(_warm_runs)
        logger.info(
            "chat_speedup_metrics: rehydrated %d days, %d warm runs from Redis",
            day_count, warm_count,
        )
        return True
    except Exception as exc:
        logger.warning("chat_speedup_metrics load_from_store failed: %s", exc)
        return False


def flush_to_store() -> bool:
    """Apply this worker's pending delta to Redis using atomic HINCRBY /
    HINCRBYFLOAT operations, then reset the delta. Safe to call concurrently
    from multiple workers — deltas add instead of overwriting."""
    try:
        from deps import redis_client
    except Exception:
        return False
    if not redis_client:
        return False
    # Snapshot + clear delta under the lock so further writes accumulate fresh.
    with _lock:
        delta_copy = {d: dict(b) for d, b in _delta.items() if b}
        _delta.clear()
        warm_copy = list(_warm_runs_pending)
        _warm_runs_pending.clear()
    if not delta_copy and not warm_copy:
        return True
    try:
        for date, fields in delta_copy.items():
            day_key = _REDIS_DAY_KEY.format(date=date)
            for field, value in fields.items():
                if not value:
                    continue
                if field in _FLOAT_FIELDS:
                    redis_client.hincrbyfloat(day_key, field, float(value))
                else:
                    redis_client.hincrby(day_key, field, int(value))
            redis_client.expire(day_key, _REDIS_TTL)
            redis_client.sadd(_REDIS_DAYS_INDEX, date)
        redis_client.expire(_REDIS_DAYS_INDEX, _REDIS_TTL)
        if warm_copy:
            for entry in warm_copy:
                redis_client.lpush(_REDIS_WARM_RUNS_KEY, json.dumps(entry, default=str))
            redis_client.ltrim(_REDIS_WARM_RUNS_KEY, 0, _MAX_WARM_RUNS - 1)
            redis_client.expire(_REDIS_WARM_RUNS_KEY, _REDIS_TTL)
        return True
    except Exception as exc:
        # Restore the delta so we don't lose increments on a transient failure.
        with _lock:
            for date, fields in delta_copy.items():
                d = _delta.setdefault(date, {})
                for field, value in fields.items():
                    d[field] = (d.get(field, 0) or 0) + value
            for entry in warm_copy:
                _warm_runs_pending.appendleft(entry)
        logger.debug("chat_speedup_metrics flush_to_store failed: %s", exc)
        return False


async def periodic_flush_loop(interval_sec: int = _FLUSH_INTERVAL_SEC) -> None:
    """Background task: flush in-memory delta to Redis every ``interval_sec``."""
    import asyncio
    while True:
        try:
            await asyncio.sleep(max(5, int(interval_sec)))
            await asyncio.to_thread(flush_to_store)
        except asyncio.CancelledError:
            # Final flush on shutdown
            try:
                await asyncio.to_thread(flush_to_store)
            except Exception:
                pass
            raise
        except Exception as exc:
            logger.debug("chat_speedup_metrics periodic_flush_loop tick failed: %s", exc)
