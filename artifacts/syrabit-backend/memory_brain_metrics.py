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
#   worker:<pid>:dropped     (Task #527; HSET overwrite of the
#                             monotonic per-worker drop counter, so
#                             the admin tile can sum the per-worker
#                             snapshots into a fleet total instead of
#                             only seeing the worker that happened to
#                             serve the request)
_FLEET_KEY_PREFIX = "mb:fleet:h:"
_FLEET_BUCKET_TTL_SECONDS = 25 * 3600  # 24h window + 1h slack
# Task #530 — sibling Redis HASH that tracks every worker pid we've
# ever seen write into the rolling window (pid -> last_seen_ts as a
# float string). The hour-keyed buckets above only contain pids that
# wrote *during the current hour*, so a worker that crashed silently
# with zero events this hour would otherwise vanish from the admin
# table — defeating the whole point of the per-worker breakdown.
# This hash is the durable "seen list" so a dead worker still shows
# up as a stale row instead of silently disappearing.
_FLEET_WORKERS_KEY = "mb:fleet:workers"
# Default stale threshold for the per-worker table (Task #530). A
# worker that hasn't reported either a success or a failure within
# this many seconds renders with a "stale" badge in the admin tile
# so the operator can tell "pid 42 stopped reporting an hour ago"
# from "pid 42 was never alive". Configurable via env so an operator
# can dial it down during a noisy incident without a code change.
_DEFAULT_WORKER_STALE_SECONDS = 600


def _worker_stale_threshold_seconds() -> int:
    """Read the per-worker stale threshold from the environment with
    a safe fallback. Capped at the rolling-window length because a
    threshold longer than the window is meaningless (the seen-list
    hash itself only carries `_FLEET_BUCKET_TTL_SECONDS` of history).
    """
    raw = (_os.environ.get("MEMORY_BRAIN_WORKER_STALE_SECONDS") or "").strip()
    if not raw:
        return _DEFAULT_WORKER_STALE_SECONDS
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_WORKER_STALE_SECONDS
    if v <= 0:
        return _DEFAULT_WORKER_STALE_SECONDS
    return min(v, _FLEET_BUCKET_TTL_SECONDS)
# Task #483 — per-worker fan-out fields live in the same hour-keyed
# hash as the aggregate counters so we don't double the Upstash key
# count. Field naming:
#   worker:<pid>:op:<op>:<outcome>   counters per (pid, op, outcome)
#   worker:<pid>:last_<outcome>_ts   most-recent ok/fail timestamp
# Co-locating the per-worker fields in the same hash means a single
# HGETALL still returns everything the dashboard needs — no extra
# round-trip and no risk of the aggregate and per-worker views going
# out of sync between two reads.
#
# IMPORTANT: gunicorn runs with ``preload_app = True`` so this module
# is imported in the *master* process before forking. Capturing
# ``os.getpid()`` at import time would attribute every worker's
# events to the master's pid, defeating the entire breakdown. We
# resolve the pid lazily at write time via ``_current_worker_pid()``
# so each forked worker stamps its own pid into Upstash. Tests
# monkeypatch this helper to simulate multiple workers.
def _current_worker_pid() -> int:
    return _os.getpid()
_FLEET_QUEUE_MAX = 4096
_fleet_queue: _queue.Queue = _queue.Queue(maxsize=_FLEET_QUEUE_MAX)
_fleet_writer_started = False
_fleet_writer_lock = threading.Lock()
_fleet_dropped_events = 0  # diagnostics-only; surfaced in get_fleet_stats

# ── Auto-recovery / degraded mode (Task #528) ───────────────────────
#
# When Upstash slows down, the per-event HSET round-trips (last_ok_ts /
# last_fail_ts, both aggregate and per-worker) start dominating writer
# latency. The queue then backs up, ``record_event`` starts dropping on
# overflow, and the admin tile silently undercounts. To absorb a
# transient Upstash slowdown without operator intervention we watch the
# queue depth from inside the writer loop:
#
#   * Once ``_fleet_queue`` has been ≥ ``_FLEET_PRESSURE_HIGH_RATIO``
#     full for at least ``_FLEET_PRESSURE_DURATION_SEC`` we flip into
#     "essentials only" mode: drop the per-event ``last_*_ts`` HSETs
#     (pure latency — they're convenience timestamps, not counters)
#     and pipeline the remaining HINCRBYs into a single round-trip
#     when the client supports it.
#   * When the queue has drained back below
#     ``_FLEET_PRESSURE_LOW_RATIO`` we exit degraded mode and resume
#     full HSET writes on the very next event.
#   * ``_fleet_degraded_events`` counts how many events were processed
#     in essentials-only mode, surfaced via ``get_fleet_stats`` so the
#     admin tile can render a "writer recovering" badge instead of a
#     misleading green tile.
#   * The companion alert in ``metrics._alerting_loop`` reads
#     ``is_fleet_writer_recovering`` and skips paging while the writer
#     is in degraded mode AND the queue is actively draining — only a
#     truly stuck queue (degraded mode unable to recover) reaches
#     on-call.
_FLEET_PRESSURE_HIGH_RATIO = 0.5
_FLEET_PRESSURE_LOW_RATIO = 0.25
_FLEET_PRESSURE_DURATION_SEC = 30.0

_fleet_pressure_started_at: Optional[float] = None  # monotonic
_fleet_degraded_mode = False
_fleet_degraded_since: Optional[float] = None  # wall-clock, for tile
_fleet_degraded_events = 0  # monotonic per worker


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


def _update_fleet_pressure(qsize: int, now_monotonic: float) -> None:
    """Sample queue depth and toggle ``_fleet_degraded_mode`` (Task #528).

    Called from the writer loop after each event. Uses a hysteresis
    band so a queue oscillating around the high-water mark doesn't
    flap in and out of degraded mode every iteration.
    """
    global _fleet_pressure_started_at, _fleet_degraded_mode, _fleet_degraded_since
    high = _FLEET_QUEUE_MAX * _FLEET_PRESSURE_HIGH_RATIO
    low = _FLEET_QUEUE_MAX * _FLEET_PRESSURE_LOW_RATIO
    if not _fleet_degraded_mode:
        if qsize >= high:
            if _fleet_pressure_started_at is None:
                _fleet_pressure_started_at = now_monotonic
            elif now_monotonic - _fleet_pressure_started_at >= _FLEET_PRESSURE_DURATION_SEC:
                _fleet_degraded_mode = True
                _fleet_degraded_since = _time.time()
                logger.warning(
                    "memory_brain fleet writer entering degraded mode "
                    "(qsize=%d/%d, sustained for %.1fs) — dropping per-event "
                    "last_*_ts HSETs and pipelining HINCRBYs to drain the queue",
                    qsize, _FLEET_QUEUE_MAX,
                    now_monotonic - _fleet_pressure_started_at,
                )
        else:
            _fleet_pressure_started_at = None
    else:
        if qsize <= low:
            _fleet_degraded_mode = False
            _fleet_pressure_started_at = None
            _fleet_degraded_since = None
            logger.info(
                "memory_brain fleet writer recovered (qsize=%d/%d) — "
                "resuming full last_*_ts HSETs",
                qsize, _FLEET_QUEUE_MAX,
            )


def _process_fleet_event(ts: float, op: str, kind: str, ok: bool, reason: Optional[str]) -> None:
    """Write one event into Upstash. Splits on ``_fleet_degraded_mode``:

    * Full mode (default): one HINCRBY per counter + per-event HSET
      for ``last_ok_ts`` / ``last_fail_ts`` (aggregate + per-worker).
    * Degraded mode (Task #528): essentials only — pipelined HINCRBYs,
      no ``last_*_ts`` HSETs. The dashboard's "last ok / last fail"
      timestamps will lag by however long the writer stayed degraded
      but every counter remains accurate.
    """
    global _fleet_degraded_events
    try:
        from deps import redis_client as _rc
        if _rc is None:
            return
        key = f"{_FLEET_KEY_PREFIX}{_hour_bucket(ts)}"
        outcome = "ok" if ok else "fail"
        # Resolve pid at write time, NOT module import time —
        # gunicorn preloads this module in the master before
        # forking (see ``_current_worker_pid`` docstring).
        pid = _current_worker_pid()
        try:
            if _fleet_degraded_mode:
                # Pipeline the HINCRBYs into a single round-trip when
                # the client supports it (Upstash REST + redis-py both
                # do); fall through to per-call writes otherwise.
                pipe_factory = getattr(_rc, "pipeline", None)
                pipe = None
                if callable(pipe_factory):
                    try:
                        pipe = pipe_factory()
                    except Exception:
                        pipe = None
                target = pipe if pipe is not None else _rc
                target.hincrby(key, f"op:{op}:{outcome}", 1)
                target.hincrby(key, f"kind:{kind}:{outcome}", 1)
                if not ok and reason:
                    target.hincrby(key, f"reason:{reason}", 1)
                target.hincrby(key, f"worker:{pid}:op:{op}:{outcome}", 1)
                target.expire(key, _FLEET_BUCKET_TTL_SECONDS)
                if pipe is not None:
                    exec_fn = getattr(pipe, "execute", None)
                    if callable(exec_fn):
                        exec_fn()
                _fleet_degraded_events += 1
            else:
                _rc.hincrby(key, f"op:{op}:{outcome}", 1)
                _rc.hincrby(key, f"kind:{kind}:{outcome}", 1)
                if not ok and reason:
                    _rc.hincrby(key, f"reason:{reason}", 1)
                # Task #483 — per-worker fan-out: same (op, outcome)
                # counter scoped to this gunicorn worker pid so the
                # admin tile can render a "pid 42 is the one failing"
                # breakdown without standing up a second key.
                _rc.hincrby(key, f"worker:{pid}:op:{op}:{outcome}", 1)
                # Track most-recent timestamps so the tile can show
                # "last ok / last fail" across the whole fleet.
                _rc.hset(key, f"last_{outcome}_ts", str(ts))
                _rc.hset(key, f"worker:{pid}:last_{outcome}_ts", str(ts))
                # Task #527: piggyback the per-worker dropped-events
                # counter onto every successful event so the admin
                # tile can render a *fleet* drop total. HSET (not
                # HINCRBY) because the counter is monotonic on the
                # worker — the latest snapshot wins.
                try:
                    _rc.hset(key, f"worker:{pid}:dropped", str(int(_fleet_dropped_events)))
                except Exception:
                    pass
                # Refresh TTL on every write so an actively-used hour
                # bucket can't expire mid-window.
                _rc.expire(key, _FLEET_BUCKET_TTL_SECONDS)
                # Task #530 — durable per-worker seen-list. The
                # hour-keyed hash above only carries pids that wrote
                # *this hour*, so a worker that crashed silently with
                # zero events would otherwise vanish from the admin
                # table. Recording into a sibling hash here means a
                # dead worker still surfaces as a stale row instead.
                try:
                    _rc.hset(_FLEET_WORKERS_KEY, str(pid), str(ts))
                    _rc.expire(_FLEET_WORKERS_KEY, _FLEET_BUCKET_TTL_SECONDS)
                except Exception as exc:
                    logger.debug("fleet seen-list write failed: %s", exc)
        except Exception as exc:
            # Don't spam — Upstash hiccups are common and the
            # per-worker view is still authoritative.
            logger.debug("fleet rollup write failed: %s", exc)
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("fleet writer loop error: %s", exc)


def _fleet_writer_loop() -> None:
    """Daemon thread: drain ``_fleet_queue`` into Upstash Redis.

    Each event is one HINCRBY per affected counter (3 counters per
    event: op, kind, reason-or-last-ts). Upstash REST round-trips are
    ~30–80ms; queueing keeps the hot path off them entirely. On a
    transient Upstash outage we just log + continue — the per-worker
    deque still has the truth.

    Task #528: after each event we sample ``_fleet_queue.qsize()`` and
    flip into degraded "essentials only" mode if the queue has been
    sustained-full so the writer can drain instead of dropping.
    """
    while True:
        try:
            ts, op, kind, ok, reason = _fleet_queue.get()
        except Exception:
            continue
        try:
            _update_fleet_pressure(_fleet_queue.qsize(), _time.monotonic())
        except Exception as exc:  # pragma: no cover — defensive
            logger.debug("fleet pressure sample error: %s", exc)
        _process_fleet_event(ts, op, kind, ok, reason)


def _snapshot_dropped_to_fleet() -> None:
    """Task #527 — push this worker's current ``_fleet_dropped_events``
    value into the live hour bucket via a single HSET.

    The writer loop also piggybacks the same field onto every
    successful event (cheap, no extra round-trip), but a worker that
    has been quiet for a few minutes — or whose queue is so backed up
    that no event has drained recently — would otherwise leave the
    fleet view stale. Calling this from the read-side
    (``get_fleet_stats``) guarantees the worker handling the admin
    request always contributes a fresh snapshot before HGETALL.

    Best-effort: any Redis error is swallowed so a transient Upstash
    blip never breaks the dashboard render.
    """
    try:
        from deps import redis_client as _rc
        if _rc is None:
            return
        ts = _time.time()
        key = f"{_FLEET_KEY_PREFIX}{_hour_bucket(ts)}"
        pid = _current_worker_pid()
        _rc.hset(key, f"worker:{pid}:dropped", str(int(_fleet_dropped_events)))
        _rc.expire(key, _FLEET_BUCKET_TTL_SECONDS)
    except Exception as exc:
        logger.debug("dropped-events snapshot failed: %s", exc)


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
        # Task #527: contribute this worker's latest dropped-events
        # snapshot BEFORE the HGETALL so the fleet aggregate is never
        # stale by more than one read tick — even when this worker has
        # been quiet long enough for the writer-loop piggyback to lag.
        _snapshot_dropped_to_fleet()
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
    # Task #527 — per-pid latest dropped snapshot. The writer loop and
    # the read-side both HSET ``worker:<pid>:dropped`` to the worker's
    # current monotonic counter, so for each pid we take the MAX value
    # seen across the hour buckets in the window and sum across pids.
    dropped_by_pid: dict[str, int] = {}

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
            elif len(parts) == 3 and parts[0] == "worker" and parts[2] == "dropped":
                # Task #527 — ``worker:<pid>:dropped``. Monotonic per
                # pid, HSET overwrite, so the freshest value within the
                # window for that pid is the max we've seen.
                pid = parts[1]
                cur = dropped_by_pid.get(pid, 0)
                if n > cur:
                    dropped_by_pid[pid] = n

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
        # Task #527 — sum of every worker's latest monotonic drop
        # snapshot in the window. This is the number the admin tile
        # surfaces in fleet scope so the badge no longer flickers
        # depending on which worker happened to serve the request.
        "dropped_events_fleet": sum(dropped_by_pid.values()),
        "dropped_events_by_pid": [
            {"pid": (int(p) if p.isdigit() else p), "dropped": v}
            for p, v in sorted(
                dropped_by_pid.items(),
                key=lambda kv: -kv[1],
            )
        ],
        # Task #528 — degraded-mode visibility for the admin tile.
        # ``writer_degraded`` lets the frontend render an amber
        # "writer recovering" badge that distinguishes a healthy
        # green fleet from a writer silently dropping per-event
        # last_*_ts HSETs to drain the queue.
        "writer_degraded": bool(_fleet_degraded_mode),
        "writer_degraded_since": _fleet_degraded_since,
        "writer_queue_size": _fleet_queue.qsize(),
        "writer_queue_capacity": _FLEET_QUEUE_MAX,
        "degraded_events_local": int(_fleet_degraded_events),
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


def get_fleet_workers(hours: int = 24) -> list[dict[str, Any]]:
    """Task #483 — per-worker fan-out breakdown over the last ``hours``.

    Walks the same hour-keyed Upstash hashes used by
    :func:`get_fleet_stats` and pulls out the ``worker:<pid>:...``
    fields, returning one row per worker pid seen in the window::

        [{"pid": 42, "writes_ok": 100, "writes_fail": 3,
          "reads_ok": 80, "reads_fail": 0,
          "total": 183, "failures": 3, "failure_rate_pct": 1.64,
          "last_ok_ts": 1.7e9, "last_fail_ts": 1.7e9}, ...]

    Sorted by failure rate descending then by total descending so the
    misbehaving workers float to the top of the operator's table —
    that is the whole point of the breakdown (a partial outage where
    pid 42 has a revoked Voyage key while the rest are healthy).
    Returns an empty list when Upstash is unwired or unreadable so
    the frontend can hide the disclosure section cleanly.
    """
    if hours <= 0 or hours > 24:
        hours = 24
    if not _fleet_enabled():
        return []
    raw, read_ok = _fleet_fetch_buckets(hours)
    if not read_ok:
        return []

    # pid -> {writes_ok, writes_fail, reads_ok, reads_fail,
    #         last_ok_ts, last_fail_ts}
    by_pid: dict[str, dict[str, Any]] = {}

    def _row(pid: str) -> dict[str, Any]:
        return by_pid.setdefault(pid, {
            "writes_ok": 0, "writes_fail": 0,
            "reads_ok": 0, "reads_fail": 0,
            "last_ok_ts": None, "last_fail_ts": None,
        })

    for entry in raw:
        for field, val in (entry.get("fields") or {}).items():
            f = field if isinstance(field, str) else field.decode("utf-8", "ignore")
            if not f.startswith("worker:"):
                continue
            parts = f.split(":")
            # worker:<pid>:op:<op>:<outcome>           (5 parts)
            # worker:<pid>:last_ok_ts / last_fail_ts   (3 parts)
            if len(parts) == 5 and parts[2] == "op":
                pid, op, outcome = parts[1], parts[3], parts[4]
                n = _coerce_int(val)
                if n <= 0:
                    continue
                row = _row(pid)
                if op == "write" and outcome == "ok":
                    row["writes_ok"] += n
                elif op == "write" and outcome == "fail":
                    row["writes_fail"] += n
                elif op == "read" and outcome == "ok":
                    row["reads_ok"] += n
                elif op == "read" and outcome == "fail":
                    row["reads_fail"] += n
            elif len(parts) == 3 and parts[2] in ("last_ok_ts", "last_fail_ts"):
                pid = parts[1]
                ts = _coerce_float(val)
                if ts is None:
                    continue
                row = _row(pid)
                key = "last_ok_ts" if parts[2] == "last_ok_ts" else "last_fail_ts"
                cur = row[key]
                if cur is None or ts > cur:
                    row[key] = ts

    # Task #530 — pull the durable seen-list so workers that crashed
    # silently with zero events this hour still surface as stale rows.
    # Failure to read this is non-fatal: we just lose the "still
    # remembered across hour boundaries" property and fall back to the
    # in-bucket pid set.
    seen_list: dict[str, float] = {}
    # Hash fields don't have per-field TTLs in Redis — only the key as
    # a whole — and the writer refreshes the key TTL on every event.
    # An always-busy fleet would therefore keep very old PID fields
    # alive forever and clutter the table with long-dead workers. We
    # prune at read-time (and HDEL the stragglers) so the seen-list
    # respects the same rolling-window semantics as the hour buckets.
    seen_cutoff = _time.time() - _FLEET_BUCKET_TTL_SECONDS
    try:
        from deps import redis_client as _rc
        if _rc is not None:
            raw_seen = _rc.hgetall(_FLEET_WORKERS_KEY) or {}
            stale_fields: list[str] = []
            for pid_field, ts_val in raw_seen.items():
                pid_str = pid_field if isinstance(pid_field, str) else pid_field.decode("utf-8", "ignore")
                ts = _coerce_float(ts_val)
                if ts is None:
                    stale_fields.append(pid_str)
                    continue
                if ts < seen_cutoff:
                    stale_fields.append(pid_str)
                    continue
                seen_list[pid_str] = ts
            if stale_fields:
                try:
                    _rc.hdel(_FLEET_WORKERS_KEY, *stale_fields)
                except Exception as exc:
                    logger.debug("fleet seen-list prune hdel failed: %s", exc)
    except Exception as exc:
        logger.debug("fleet seen-list read failed: %s", exc)

    # Bring in pids that exist only in the seen-list (zero events in
    # the current hour buckets) so the operator can still see them.
    for pid_str in seen_list.keys():
        if pid_str not in by_pid:
            _row(pid_str)

    now = _time.time()
    stale_threshold = _worker_stale_threshold_seconds()

    out: list[dict[str, Any]] = []
    for pid, row in by_pid.items():
        total = row["writes_ok"] + row["writes_fail"] + row["reads_ok"] + row["reads_fail"]
        failures = row["writes_fail"] + row["reads_fail"]
        failure_rate_pct = round((failures / total) * 100.0, 2) if total else 0.0
        try:
            pid_int: Any = int(pid)
        except (TypeError, ValueError):
            pid_int = pid

        # last_seen_ts = most recent of last_ok_ts / last_fail_ts /
        # the seen-list entry. The seen-list catches workers that
        # crashed silently with zero events in the current hour
        # buckets — without it they'd render with a None last_seen.
        candidates = [
            t for t in (row["last_ok_ts"], row["last_fail_ts"], seen_list.get(pid))
            if t is not None
        ]
        last_seen_ts: Optional[float] = max(candidates) if candidates else None
        if last_seen_ts is None:
            # No timestamp anywhere — treat as stale so the operator
            # notices instead of seeing a silent "—".
            is_stale = True
            last_seen_age_seconds: Optional[float] = None
        else:
            age = max(0.0, now - last_seen_ts)
            last_seen_age_seconds = age
            is_stale = age > stale_threshold

        out.append({
            "pid": pid_int,
            "writes_ok": row["writes_ok"],
            "writes_fail": row["writes_fail"],
            "reads_ok": row["reads_ok"],
            "reads_fail": row["reads_fail"],
            "total": total,
            "failures": failures,
            "failure_rate_pct": failure_rate_pct,
            "last_ok_ts": row["last_ok_ts"],
            "last_fail_ts": row["last_fail_ts"],
            "last_seen_ts": last_seen_ts,
            "last_seen_age_seconds": last_seen_age_seconds,
            "is_stale": is_stale,
            "stale_threshold_seconds": stale_threshold,
        })
    # Sort: stale rows first (silent worker death is the most urgent
    # signal), then by highest failure rate, then by highest volume,
    # so the operator's eye lands on whichever pid is misbehaving.
    out.sort(key=lambda r: (
        0 if r["is_stale"] else 1,
        -r["failure_rate_pct"],
        -r["total"],
    ))
    return out


def get_fleet_dropped_events_total(hours: int = 24) -> int:
    """Task #527 — fleet-wide sum of per-worker drop snapshots.

    Walks the same hour-keyed Upstash hashes used by
    :func:`get_fleet_stats` and sums the latest ``worker:<pid>:dropped``
    snapshot per pid. Returns 0 when Upstash isn't wired or every read
    failed (the alerting / admin caller should fall back to the local
    per-worker counter in that case).
    """
    if hours <= 0 or hours > 24:
        hours = 24
    if not _fleet_enabled():
        return 0
    raw, read_ok = _fleet_fetch_buckets(hours)
    if not read_ok:
        return 0
    by_pid: dict[str, int] = {}
    for entry in raw:
        for field, val in (entry.get("fields") or {}).items():
            f = field if isinstance(field, str) else field.decode("utf-8", "ignore")
            parts = f.split(":")
            if len(parts) == 3 and parts[0] == "worker" and parts[2] == "dropped":
                n = _coerce_int(val)
                pid = parts[1]
                if n > by_pid.get(pid, 0):
                    by_pid[pid] = n
    return sum(by_pid.values())


def get_fleet_degraded_events() -> int:
    """Monotonic per-worker count of events processed in degraded
    "essentials only" mode (Task #528).

    The admin tile reads this to render a "writer recovering" badge:
    a non-zero delta since the last poll means the writer is actively
    self-healing through an Upstash slowdown rather than silently
    dropping events.
    """
    return int(_fleet_degraded_events)


def is_fleet_writer_degraded() -> bool:
    """True iff the writer is currently in essentials-only mode
    (Task #528). Used by the alerting loop to downgrade the
    ``memory_brain_fleet_dropped`` page while auto-recovery is
    actively draining the queue.
    """
    return bool(_fleet_degraded_mode)


def is_fleet_writer_recovering() -> bool:
    """True iff the writer is in degraded mode AND the queue is
    actively draining (i.e. below the high-water mark).

    Returning True signals the alerting loop to skip the
    ``memory_brain_fleet_dropped`` page: drops happened, but the
    self-healing path is working — on-call only needs to be paged
    when degraded mode can't catch up (queue still ≥ high-water).
    """
    if not _fleet_degraded_mode:
        return False
    high = _FLEET_QUEUE_MAX * _FLEET_PRESSURE_HIGH_RATIO
    try:
        return _fleet_queue.qsize() < high
    except Exception:
        # ``qsize`` is documented as not-reliable on some platforms;
        # if it raises we conservatively treat the writer as
        # recovering so we don't over-page during degraded mode.
        return True


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
    global _fleet_degraded_mode, _fleet_degraded_since
    global _fleet_degraded_events, _fleet_pressure_started_at
    with _lock:
        _events.clear()
    _fleet_dropped_events = 0
    _fleet_degraded_mode = False
    _fleet_degraded_since = None
    _fleet_degraded_events = 0
    _fleet_pressure_started_at = None
    try:
        while True:
            _fleet_queue.get_nowait()
    except _queue.Empty:
        pass


__all__ = [
    "record_event", "get_stats", "get_hourly_buckets",
    "get_fleet_stats", "get_fleet_hourly_buckets",
    "get_fleet_workers",
    "get_fleet_dropped_events",
    "get_fleet_dropped_events_total",
    "get_fleet_degraded_events",
    "is_fleet_writer_degraded",
    "is_fleet_writer_recovering",
    "reset",
]
