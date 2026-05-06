"""Task #417 — Lightweight in-memory counters for the memory_brain
write/read hot path.

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
* **In-process only.** A multi-worker gunicorn deployment will see
  per-worker counts; aggregating across workers is intentionally out
  of scope for this task — the failure-mode we care about (Voyage /
  Mongo outage) hits every worker simultaneously, so per-worker stats
  are perfectly representative for an alert. The admin route surfaces
  this caveat in the response so the dashboard can label it.
* **Bounded ring buffer.** We keep events for a fixed rolling window
  (24h by default) and trim on every insert so memory cannot grow
  unboundedly even on a hot path.
* **Best-effort.** Recording is wrapped in try/except so a metrics
  bug can never propagate back into the chat hot path that called
  the wrapper. The whole point of the wrappers is non-fatality —
  we must not regress that.
"""
from __future__ import annotations

import logging
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


def reset() -> None:
    """Test-only — drop all recorded events."""
    with _lock:
        _events.clear()


__all__ = ["record_event", "get_stats", "get_hourly_buckets", "reset"]
