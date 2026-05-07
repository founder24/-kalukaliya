"""Task #417 / #446 — Lightweight in-memory counters for the
memory_brain write/read hot path, plus an opt-in fleet-wide rollup
that mirrors the same counters into Upstash Redis so the admin
dashboard can show a single number across every gunicorn worker.

Why this exists
---------------
Task #401 wired the chat hot path and the flashcard-review hot path
into ``providers.memory_brain`` via the best-effort wrappers in
``memory_brain_chat``. Every wrapper there swallows exceptions and
logs at WARNING / DEBUG so a Voyage outage or a Mongo blip cannot
break the user-visible response. That is the right safety posture,
but the side effect is **silent failure**: if Voyage starts rejecting
embeddings we'll only see scattered WARNING log lines and we'll
notice once a student complains that "Syra doesn't remember me".

This module gives the ``memory_brain_chat`` wrappers a single
zero-dependency place to record outcomes ("write ok", "read failed
because ...") so the admin dashboard can render a sparkline and the
alerting loop in ``metrics._alerting_loop`` can page on-call when the
failure rate crosses a configurable threshold.

Design choices
--------------
* **Per-worker ring buffer (Task #417).** Each gunicorn worker keeps
  its own bounded deque so a metrics bug can never pin a worker.
  ``get_stats`` / ``get_hourly_buckets`` aggregate this local view.
* **Fleet rollup via Upstash Redis (Task #446).** ``record_event``
  also fans out to a background daemon thread that ``HINCRBY``s into
  hour-keyed Redis hashes (``mb:fleet:h:<hour_start_ts>``). Reads
  come back via ``get_fleet_stats`` / ``get_fleet_hourly_buckets``
  so the admin tile can show a single number across every worker —
  the per-worker view stays available behind a toggle for partial-
  outage debugging (one worker's Voyage key revoked, etc).
* **Bounded ring buffer.** We keep events for a fixed rolling window
  (24h by default) and trim on every insert so memory cannot grow
  unboundedly even on a hot path.
* **Best-effort, never blocking.** Recording is wrapped in try/except
  so a metrics bug can never propagate back into the chat hot path
  that called the wrapper. The Redis fan-out goes through a
  ``queue.Queue`` drained by a daemon thread — ``record_event`` itself
  does only an O(1) ``put_nowait`` (drops on overflow) so a slow
  Upstash REST round-trip cannot stall the event loop.
"""
from __future__ import annotations

import logging
import os as _os
import queue as _queue
import threading
import time as _time
from collections import deque
from typing import Any, Optional

logger = logging.getLogger("memory_brain_metrics")

# 24h rolling window. Sized to match the dashboard's default look-back
# and to keep memory bounded even on a busy worker (a few thousand
# events per day is the realistic upper bound).
_WINDOW_SECONDS = 24 * 3600
# Hard cap on the deque length so a runaway hot loop (e.g. an infinite
# retry against Voyage) cannot OOM the worker. With ~50 chat turns/sec
# peak we still fit ~5h of nonstop traffic into 1M slots, far above
# anything we'd realistically see between admin polls.
_MAX_EVENTS = 100_000

# (ts: float, op: "write"|"read", kind: str, ok: bool, reason: str|None)
_events: deque = deque(maxlen=_MAX_EVENTS)
_lock = threading.Lock()

_VALID_OPS = ("write", "read")


# ── Fleet rollup (Task #446) ────────────────────────────────────────
#
# Each worker fan-outs the same (op, kind, ok, reason) tuple it just
# wrote to its local deque into a small thread-local queue, drained
# by ``_fleet_writer_loop`` against Upstash Redis. We use Redis hash
# fields keyed per *wall-clock hour* so:
#   * read-side aggregation is just ``HGETALL`` over the last N
#     hour-keys — no scanning of long event lists;
#   * each hour bucket can be given a TTL (window + 1h slack) so old
#     data evicts on its own and the keyspace stays trivially small;
#   * a partial outage where only some workers fail still shows up,
#     because every worker writes into the same shared keys.
#
# Hash field naming (kept short to minimise Upstash REST payload):
#   op:write:ok / op:write:fail / op:read:ok / op:read:fail   (counters)
#   kind:<k>:ok / kind:<k>:fail                               (counters)
#   reason:<r>                                                (counters)
#   last_ok_ts / last_fail_ts                                 (HSET overwrite)
_FLEET_KEY_PREFIX = "mb:fleet:h:"
_FLEET_BUCKET_TTL_SECONDS = 25 * 3600  # 24h window + 1h slack
_FLEET_QUEUE_MAX = 4096
_fleet_queue: _queue.Queue = _queue.Queue(maxsize=_FLEET_QUEUE_MAX)
_fleet_writer_started = False
_fleet_writer_lock = threading.Lock()
_fleet_dropped_events = 0  # diagnostics-only; surfaced in get_fleet_stats


def _fleet_enabled() -> bool:
    """Fleet rollup is on iff Upstash Redis is configured and the
    operator hasn't explicitly turned the rollup off via env flag.
    Disabling the flag falls back to per-worker-only (Task #417)
    behaviour — useful for local dev and for the test suite, which
    doesn't talk to Upstash.
    """
    flag = (_os.environ.get("MEMORY_BRAIN_FLEET_ROLLUP", "1") or "").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    try:
        from deps import redis_client as _rc
        return _rc is not None
    except Exception:
        return False


def _hour_bucket(ts: float) -> int:
    """Wall-clock hour-aligned epoch seconds for ``ts``."""
    return (int(ts) // 3600) * 3600


def _fleet_writer_loop() -> None:
    """Daemon thread: drain ``_fleet_queue`` into Upstash Redis.

    Each event is one HINCRBY per affected counter (3 counters per
    event: op, kind, reason-or-last-ts). Upstash REST round-trips are
    ~30–80ms; queueing keeps the hot path off them entirely. On a
    transient Upstash outage we just log + continue — the per-worker
    deque still has the truth.
    """
    while True:
        try:
            ts, op, kind, ok, reason = _fleet_queue.get()
        except Exception:
            continue
        try:
            from deps import redis_client as _rc
            if _rc is None:
                continue
            key = f"{_FLEET_KEY_PREFIX}{_hour_bucket(ts)}"
            outcome = "ok" if ok else "fail"
            try:
                _rc.hincrby(key, f"op:{op}:{outcome}", 1)
                _rc.hincrby(key, f"kind:{kind}:{outcome}", 1)
                if not ok and reason:
                    _rc.hincrby(key, f"reason:{reason}", 1)
                # Track most-recent timestamps so the tile can show
                # "last ok / last fail" across the whole fleet.
                _rc.hset(key, f"last_{outcome}_ts", str(ts))
                # Refresh TTL on every write so an actively-used hour
                # bucket can't expire mid-window.
                _rc.expire(key, _FLEET_BUCKET_TTL_SECONDS)
            except Exception as exc:
                # Don't spam — Upstash hiccups are common and the
                # per-worker view is still authoritative.
                logger.debug("fleet rollup write failed: %s", exc)
        except Exception as exc:  # pragma: no cover — defensive
            logger.debug("fleet writer loop error: %s", exc)


def _ensure_fleet_writer() -> None:
    """Lazily start the daemon writer thread on first use so import
    of this module never side-effects a thread (test isolation).
    """
    global _fleet_writer_started
    if _fleet_writer_started:
        return
    with _fleet_writer_lock:
        if _fleet_writer_started:
            return
        t = threading.Thread(
            target=_fleet_writer_loop,
            name="memory_brain_fleet_writer",
            daemon=True,
        )
        t.start()
        _fleet_writer_started = True


def record_event(
    op: str,
    *,
    kind: str,
    ok: bool,
    reason: Optional[str] = None,
) -> None:
    """Record one memory_brain operation outcome.

    Parameters
    ----------
    op : "write" | "read"
        Which side of the memory_brain API was exercised. ``read`` is
        a vector query (``query_user_memories``); ``write`` is one of
        ``write_chat_turn_memory`` / ``write_flashcard_recall_memory``.
    kind : str
        The event kind. For writes this is the ``kind`` parameter
        passed through to ``providers.memory_brain.write_memory``
        (``"qa"`` for chat turns, ``"fact"`` for flashcards). For
        reads we use ``"query"`` as a single bucket because the read
        surface doesn't filter by kind today.
    ok : bool
        Whether the operation succeeded end-to-end. A timed-out read
        counts as a failure; a swallowed Voyage exception counts as
        a failure; an upstream "feature disabled" no-op should NOT
        be recorded at all (see callers).
    reason : str, optional
        Short failure label (e.g. ``"timeout"``, ``"voyage_error"``,
        ``"mongo_unavailable"``). Truncated to 80 chars so a stack
        trace dumped here can't blow up the response payload.
    """
    if op not in _VALID_OPS:
        return
    try:
        ts = _time.time()
        k = (kind or "")[:32] or "unknown"
        r = None if ok else ((reason or "error")[:80])
        with _lock:
            _events.append((ts, op, k, bool(ok), r))
            # Time-based trim: deque.maxlen already caps the absolute
            # length, but we also want to drop events older than the
            # rolling window so get_stats() can make zero-cost
            # assumptions about freshness. The deque is monotonic in
            # ts so a single popleft loop is sufficient.
            cutoff = ts - _WINDOW_SECONDS
            while _events and _events[0][0] < cutoff:
                _events.popleft()
        # Fleet rollup (Task #446): non-blocking enqueue, drops on
        # overflow so the hot path is *never* coupled to Upstash.
        try:
            if _fleet_enabled():
                _ensure_fleet_writer()
                try:
                    _fleet_queue.put_nowait((ts, op, k, bool(ok), r))
                except _queue.Full:
                    global _fleet_dropped_events
                    _fleet_dropped_events += 1
        except Exception as exc:
            logger.debug("memory_brain fleet enqueue swallowed: %s", exc)
    except Exception as exc:
        # Metrics must never propagate back into the chat hot path.
        logger.debug("memory_brain_metrics.record_event swallowed: %s", exc)


def get_stats(window_seconds: int = _WINDOW_SECONDS) -> dict[str, Any]:
    """Aggregate the rolling event ring into a dashboard payload.

    Returns counts split by ``op`` (write / read) and by event kind,
    plus the most recent failure reasons (top-N) so the operator can
    immediately see *why* writes are failing without grepping logs.
    """
    if window_seconds <= 0 or window_seconds > _WINDOW_SECONDS:
        window_seconds = _WINDOW_SECONDS
    now = _time.time()
    cutoff = now - window_seconds

    by_op: dict[str, dict[str, int]] = {
        "write": {"ok": 0, "fail": 0, "total": 0},
        "read":  {"ok": 0, "fail": 0, "total": 0},
    }
    by_kind: dict[str, dict[str, int]] = {}
    reasons: dict[str, int] = {}
    last_failure_ts: Optional[float] = None
    last_success_ts: Optional[float] = None
    total = 0
    failures = 0

    with _lock:
        snapshot = list(_events)

    for ts, op, kind, ok, reason in snapshot:
        if ts < cutoff:
            continue
        total += 1
        bucket = by_op.setdefault(op, {"ok": 0, "fail": 0, "total": 0})
        bucket["total"] += 1
        kind_bucket = by_kind.setdefault(kind, {"ok": 0, "fail": 0, "total": 0})
        kind_bucket["total"] += 1
        if ok:
            bucket["ok"] += 1
            kind_bucket["ok"] += 1
            if last_success_ts is None or ts > last_success_ts:
                last_success_ts = ts
        else:
            bucket["fail"] += 1
            kind_bucket["fail"] += 1
            failures += 1
            if reason:
                reasons[reason] = reasons.get(reason, 0) + 1
            if last_failure_ts is None or ts > last_failure_ts:
                last_failure_ts = ts

    failure_rate_pct = round((failures / total) * 100.0, 2) if total else 0.0
    top_reasons = sorted(reasons.items(), key=lambda kv: -kv[1])[:5]

    return {
        "window_seconds": window_seconds,
        "total": total,
        "failures": failures,
        "failure_rate_pct": failure_rate_pct,
        "by_op": by_op,
        "by_kind": by_kind,
        "top_failure_reasons": [{"reason": r, "count": c} for r, c in top_reasons],
        "last_failure_ts": last_failure_ts,
        "last_success_ts": last_success_ts,
        "scope": "per_worker",
    }


def get_hourly_buckets(hours: int = 24) -> list[dict[str, Any]]:
    """Bucketise the rolling window into hourly slots for the chart.

    Returns a list of ``hours`` entries, oldest first, each shaped::

        {"hour_start_ts": float, "writes_ok": int, "writes_fail": int,
         "reads_ok": int, "reads_fail": int}

    Slots with no events still appear (with all zeros) so the
    sparkline has a stable x-axis even on a quiet day.
    """
    if hours <= 0:
        hours = 24
    if hours > 24:
        hours = 24
    now = _time.time()
    # Align to the wall-clock hour so two browser tabs polling within
    # the same minute see identical buckets.
    bucket_size = 3600
    end_align = (int(now) // bucket_size) * bucket_size + bucket_size
    start = end_align - hours * bucket_size

    buckets = [
        {
            "hour_start_ts": start + i * bucket_size,
            "writes_ok": 0, "writes_fail": 0,
            "reads_ok": 0, "reads_fail": 0,
        }
        for i in range(hours)
    ]

    with _lock:
        snapshot = list(_events)

    for ts, op, _kind, ok, _reason in snapshot:
        if ts < start or ts >= end_align:
            continue
        idx = int((ts - start) // bucket_size)
        if idx < 0 or idx >= hours:
            continue
        b = buckets[idx]
        if op == "write":
            b["writes_ok" if ok else "writes_fail"] += 1
        elif op == "read":
            b["reads_ok" if ok else "reads_fail"] += 1
    return buckets


# ── Fleet read-side (Task #446) ─────────────────────────────────────


def _fleet_hour_keys(hours: int, now: Optional[float] = None) -> list[int]:
    """Return the last ``hours`` wall-clock-hour starts, oldest first."""
    if hours <= 0:
        hours = 24
    if hours > 24:
        hours = 24
    if now is None:
        now = _time.time()
    cur = _hour_bucket(now)
    return [cur - (hours - 1 - i) * 3600 for i in range(hours)]


def _fleet_fetch_buckets(hours: int) -> tuple[list[dict[str, Any]], bool]:
    """HGETALL each hour bucket and return raw {field: int} dicts.

    Returns a ``(buckets, read_ok)`` tuple. ``read_ok`` is True when
    every HGETALL succeeded — used to distinguish "Upstash configured
    but currently failing" (fleet view should fall back to per-worker
    so the operator isn't shown a zeroed-out dashboard during a Redis
    outage) from "Upstash unconfigured" and from "configured + healthy
    + just empty so far this hour".
    """
    out: list[dict[str, Any]] = []
    read_ok = True
    try:
        from deps import redis_client as _rc
        if _rc is None:
            return [], False
        for hk in _fleet_hour_keys(hours):
            key = f"{_FLEET_KEY_PREFIX}{hk}"
            try:
                raw = _rc.hgetall(key) or {}
            except Exception as exc:
                logger.debug("fleet hgetall failed for %s: %s", key, exc)
                raw = {}
                read_ok = False
            out.append({"hour_start_ts": hk, "fields": raw})
    except Exception as exc:
        logger.debug("fleet fetch failed: %s", exc)
        return [], False
    return out, read_ok


def _coerce_int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _coerce_float(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def get_fleet_stats(window_seconds: int = _WINDOW_SECONDS) -> dict[str, Any]:
    """Aggregate the Upstash hour-keyed hashes into the same shape as
    :func:`get_stats` so the admin tile can swap views with no schema
    branching on the frontend.

    Adds a ``scope: "fleet"`` marker plus a ``fleet_available`` flag
    so the frontend can disable the toggle when Upstash isn't wired
    (local dev / test) instead of rendering a misleading zero state.
    """
    if window_seconds <= 0 or window_seconds > _WINDOW_SECONDS:
        window_seconds = _WINDOW_SECONDS
    hours = max(1, min(24, (window_seconds + 3599) // 3600))

    fleet_configured = _fleet_enabled()
    if fleet_configured:
        raw_buckets, fleet_read_ok = _fleet_fetch_buckets(hours)
    else:
        raw_buckets, fleet_read_ok = [], False
    # ``fleet_available`` keeps backwards-compat semantics for the
    # tile: "true iff this view is trustworthy *right now*". Configured
    # + read-failing collapses to false so the UI auto-falls back to
    # the per-worker view instead of showing a misleading zero state
    # during an Upstash outage.
    fleet_available = fleet_configured and fleet_read_ok

    by_op: dict[str, dict[str, int]] = {
        "write": {"ok": 0, "fail": 0, "total": 0},
        "read":  {"ok": 0, "fail": 0, "total": 0},
    }
    by_kind: dict[str, dict[str, int]] = {}
    reasons: dict[str, int] = {}
    last_failure_ts: Optional[float] = None
    last_success_ts: Optional[float] = None
    total = 0
    failures = 0

    for entry in raw_buckets:
        for field, val in (entry.get("fields") or {}).items():
            f = field if isinstance(field, str) else field.decode("utf-8", "ignore")
            if f == "last_ok_ts":
                ts = _coerce_float(val)
                if ts is not None and (last_success_ts is None or ts > last_success_ts):
                    last_success_ts = ts
                continue
            if f == "last_fail_ts":
                ts = _coerce_float(val)
                if ts is not None and (last_failure_ts is None or ts > last_failure_ts):
                    last_failure_ts = ts
                continue
            n = _coerce_int(val)
            if n <= 0:
                continue
            parts = f.split(":")
            if len(parts) == 3 and parts[0] == "op":
                _, op, outcome = parts
                bucket = by_op.setdefault(op, {"ok": 0, "fail": 0, "total": 0})
                bucket[outcome] = bucket.get(outcome, 0) + n
                bucket["total"] = bucket.get("total", 0) + n
                total += n
                if outcome == "fail":
                    failures += n
            elif len(parts) == 3 and parts[0] == "kind":
                _, kind, outcome = parts
                kb = by_kind.setdefault(kind, {"ok": 0, "fail": 0, "total": 0})
                kb[outcome] = kb.get(outcome, 0) + n
                kb["total"] = kb.get("total", 0) + n
            elif len(parts) >= 2 and parts[0] == "reason":
                # reason can contain ':' so re-join the tail
                reason = ":".join(parts[1:])
                reasons[reason] = reasons.get(reason, 0) + n

    failure_rate_pct = round((failures / total) * 100.0, 2) if total else 0.0
    top_reasons = sorted(reasons.items(), key=lambda kv: -kv[1])[:5]

    # ``fleet_status`` gives the operator a single string explaining
    # *why* the fleet view is in its current state — easier to debug
    # than three booleans.
    if not fleet_configured:
        fleet_status = "unconfigured"
    elif not fleet_read_ok:
        fleet_status = "read_failed"
    else:
        fleet_status = "ok"

    return {
        "window_seconds": window_seconds,
        # NOTE: ``window_seconds`` is approximated to whole-hour
        # buckets in the fleet view (we read N hour-keyed Redis
        # hashes). Sub-hour windows therefore include up to one extra
        # hour of data — operators tuning sub-hour windows should
        # rely on the per-worker scope, which uses event-level
        # timestamps.
        "total": total,
        "failures": failures,
        "failure_rate_pct": failure_rate_pct,
        "by_op": by_op,
        "by_kind": by_kind,
        "top_failure_reasons": [{"reason": r, "count": c} for r, c in top_reasons],
        "last_failure_ts": last_failure_ts,
        "last_success_ts": last_success_ts,
        "scope": "fleet",
        "fleet_available": fleet_available,
        "fleet_configured": fleet_configured,
        "fleet_read_ok": fleet_read_ok,
        "fleet_status": fleet_status,
        "dropped_events_local": _fleet_dropped_events,
    }


def get_fleet_hourly_buckets(hours: int = 24) -> list[dict[str, Any]]:
    """Bucketise fleet counters into the same chart shape as
    :func:`get_hourly_buckets` so the sparkline component is reusable.
    Returns all-zero buckets (with a stable x-axis) when Upstash is
    unavailable.
    """
    if hours <= 0 or hours > 24:
        hours = 24
    keys = _fleet_hour_keys(hours)
    out = [
        {"hour_start_ts": hk,
         "writes_ok": 0, "writes_fail": 0,
         "reads_ok": 0, "reads_fail": 0}
        for hk in keys
    ]
    if not _fleet_enabled():
        return out
    raw, _read_ok = _fleet_fetch_buckets(hours)
    by_hour = {entry["hour_start_ts"]: (entry.get("fields") or {}) for entry in raw}
    for b in out:
        fields = by_hour.get(b["hour_start_ts"]) or {}
        for field, val in fields.items():
            f = field if isinstance(field, str) else field.decode("utf-8", "ignore")
            n = _coerce_int(val)
            if n <= 0:
                continue
            if f == "op:write:ok":
                b["writes_ok"] += n
            elif f == "op:write:fail":
                b["writes_fail"] += n
            elif f == "op:read:ok":
                b["reads_ok"] += n
            elif f == "op:read:fail":
                b["reads_fail"] += n
    return out


def get_fleet_dropped_events() -> int:
    """Return this worker's monotonic count of fleet-rollup queue
    drops (Task #482).

    ``record_event`` enqueues the event tuple onto ``_fleet_queue``
    via ``put_nowait`` so the chat hot path is never coupled to
    Upstash. When Upstash hangs and the queue fills up, the
    ``queue.Full`` branch increments ``_fleet_dropped_events`` and
    silently drops the event. This counter is exposed for the
    alerting loop in ``metrics._alerting_loop`` to page on-call when
    drops start accumulating, and surfaced in the admin tile so the
    operator can see *which* worker is dropping.
    """
    return int(_fleet_dropped_events)


def reset() -> None:
    """Test-only — drop all recorded events. Also clears the fleet
    queue + drop counter so per-test isolation is total. Does NOT
    touch Upstash (tests don't hit it; production has TTL eviction).
    """
    global _fleet_dropped_events
    with _lock:
        _events.clear()
    _fleet_dropped_events = 0
    try:
        while True:
            _fleet_queue.get_nowait()
    except _queue.Empty:
        pass


__all__ = [
    "record_event", "get_stats", "get_hourly_buckets",
    "get_fleet_stats", "get_fleet_hourly_buckets",
    "get_fleet_dropped_events",
    "reset",
]
