"""
Task #490 — Option-D embed-failover automatic controller.

Owns the trip/reset state machine for the cache-only degraded mode that
replaced the legacy Vertex multilingual-embed fallback. Records the
outcome of each primary embed probe (success/failure + latency_ms) and
flips an in-process degraded flag according to the locked thresholds:

    Trip   : >= 3 failures within the last 5 probes
             OR p95 latency_ms over the last 5 probes > 2000 ms
    Reset  : 5 consecutive successful probes with p95 <= 2000 ms

The controller is intentionally process-local (not Redis-backed) — every
ACA replica runs its own state so a partial regional outage trips that
replica without false-tripping the rest of the fleet. The shared signal
is `EMBED_DEGRADED_MODE=true` (operator override, manual cutover); this
controller layers an automatic signal on top so an outage trips before
on-call has to flip the env var by hand.

`is_degraded()` is the single read API used by `llm.call_embed_with_dispatch`
to gate the SQS enqueue path. It returns True when EITHER the env var is
set OR the in-process controller has tripped.

A hook (`on_state_change`) is exposed so admin alerting / health surfaces
can subscribe without this module taking a hard dependency on them.
"""
from __future__ import annotations

import collections
import logging
import os
import threading
import time
from typing import Callable, Deque, Optional

logger = logging.getLogger(__name__)

# Locked thresholds (Task #490 V4 §15 amendment). Override only via
# operator-facing env vars; do not change defaults without a follow-up
# task because they pin the contract reviewed in PR #490.
_PROBE_WINDOW: int = int(os.environ.get("EMBED_DEGRADED_PROBE_WINDOW", "5"))
_TRIP_FAILURES: int = int(os.environ.get("EMBED_DEGRADED_TRIP_FAILURES", "3"))
_TRIP_P95_MS: float = float(os.environ.get("EMBED_DEGRADED_TRIP_P95_MS", "2000"))
_RESET_STREAK: int = int(os.environ.get("EMBED_DEGRADED_RESET_STREAK", "5"))


class _ProbeRecord:
    __slots__ = ("ok", "latency_ms", "ts")

    def __init__(self, ok: bool, latency_ms: float, ts: float) -> None:
        self.ok = ok
        self.latency_ms = latency_ms
        self.ts = ts


_lock = threading.Lock()
_window: Deque[_ProbeRecord] = collections.deque(maxlen=_PROBE_WINDOW)
_consecutive_ok: int = 0
_tripped: bool = False
_tripped_since: Optional[float] = None
_trip_reason: Optional[str] = None
_last_state_change: Optional[float] = None
_listener: Optional[Callable[[bool, str], None]] = None


def on_state_change(cb: Callable[[bool, str], None]) -> None:
    """Register a single listener invoked as ``cb(is_tripped, reason)`` on
    every trip/reset edge. Replaces any prior listener."""
    global _listener
    _listener = cb


def _emit(state: bool, reason: str) -> None:
    if _listener is None:
        return
    try:
        _listener(state, reason)
    except Exception:
        logger.exception("embed_degraded_controller listener raised (ignored)")


def _p95(samples: list[float]) -> float:
    if not samples:
        return 0.0
    s = sorted(samples)
    # Nearest-rank p95 — for windows of 5 this is index 4 (the max).
    idx = max(0, min(len(s) - 1, int(round(0.95 * (len(s) - 1)))))
    return s[idx]


def record_probe(ok: bool, latency_ms: float) -> None:
    """Record one primary-embed probe outcome.

    Called from the embed dispatcher after each primary call attempt
    (success path AND failure path). Latency is the wall-clock duration
    of the attempt in milliseconds.
    """
    global _consecutive_ok, _tripped, _tripped_since, _trip_reason, _last_state_change

    with _lock:
        _window.append(_ProbeRecord(ok=bool(ok), latency_ms=float(latency_ms), ts=time.time()))
        if ok:
            _consecutive_ok += 1
        else:
            _consecutive_ok = 0

        if not _tripped:
            failures = sum(1 for r in _window if not r.ok)
            p95_ms = _p95([r.latency_ms for r in _window])
            trip = False
            reason = ""
            if len(_window) >= _PROBE_WINDOW and failures >= _TRIP_FAILURES:
                trip = True
                reason = f"failures={failures}/{len(_window)} >= {_TRIP_FAILURES}"
            elif len(_window) >= _PROBE_WINDOW and p95_ms > _TRIP_P95_MS:
                trip = True
                reason = f"p95={p95_ms:.0f}ms > {_TRIP_P95_MS:.0f}ms"
            if trip:
                _tripped = True
                _tripped_since = time.time()
                _trip_reason = reason
                _last_state_change = _tripped_since
                logger.warning(
                    "embed_degraded_controller TRIPPED — entering Option-D "
                    "cache-only mode (%s). SQS reembed queue is now the "
                    "deferred replay path; clear by sustained recovery "
                    "(%d consecutive ok probes with p95 <= %.0fms).",
                    reason, _RESET_STREAK, _TRIP_P95_MS,
                )
                _emit(True, reason)
                return

        if _tripped and _consecutive_ok >= _RESET_STREAK:
            recent_p95 = _p95([r.latency_ms for r in list(_window)[-_RESET_STREAK:]])
            if recent_p95 <= _TRIP_P95_MS:
                _tripped = False
                _tripped_since = None
                prev_reason = _trip_reason or ""
                _trip_reason = None
                _last_state_change = time.time()
                logger.info(
                    "embed_degraded_controller RESET — primary embed recovered "
                    "(%d consecutive ok probes, p95=%.0fms <= %.0fms). Prior "
                    "trip reason: %s.",
                    _RESET_STREAK, recent_p95, _TRIP_P95_MS, prev_reason,
                )
                _emit(False, f"recovered after {_RESET_STREAK} ok probes")


def is_degraded() -> bool:
    """True iff Option-D cache-only mode is active.

    Combines the operator-override env var (`EMBED_DEGRADED_MODE`) with
    the in-process auto-trip controller. Either is sufficient to gate
    `llm.call_embed_with_dispatch` into the SQS deferred-replay path.
    """
    if os.environ.get("EMBED_DEGRADED_MODE", "").strip().lower() in {"1", "true", "yes"}:
        return True
    with _lock:
        return _tripped


def snapshot() -> dict:
    """Read-only state for admin health surfaces."""
    with _lock:
        failures = sum(1 for r in _window if not r.ok)
        p95_ms = _p95([r.latency_ms for r in _window])
        return {
            "tripped": _tripped,
            "tripped_since": _tripped_since,
            "trip_reason": _trip_reason,
            "consecutive_ok": _consecutive_ok,
            "window_size": len(_window),
            "window_max": _PROBE_WINDOW,
            "window_failures": failures,
            "window_p95_ms": p95_ms,
            "trip_failures_threshold": _TRIP_FAILURES,
            "trip_p95_ms_threshold": _TRIP_P95_MS,
            "reset_streak_required": _RESET_STREAK,
            "env_override_active": (
                os.environ.get("EMBED_DEGRADED_MODE", "").strip().lower()
                in {"1", "true", "yes"}
            ),
            "last_state_change": _last_state_change,
        }


def reset_for_tests() -> None:
    """Test helper — wipe state so each test starts from a clean slate."""
    global _consecutive_ok, _tripped, _tripped_since, _trip_reason, _last_state_change, _listener
    with _lock:
        _window.clear()
        _consecutive_ok = 0
        _tripped = False
        _tripped_since = None
        _trip_reason = None
        _last_state_change = None
        _listener = None
