"""Task #360 — Credit-burn meters (A, B, C).

Three independent meters that enforce the v3 fallback rules from
``infra/credit-burn-runbook.md`` §4. They are deliberately built as
small, side-effect-isolated classes so the whole matrix is unit-testable
without Redis / DynamoDB / CloudWatch.

- **Meter A — daily-call ceiling (auto-flip).**
  Per-UTC-day RAG-call counter. ``warning_threshold`` (default 8 000)
  posts a warning alert; ``trip_threshold`` (default 10 000) flips
  ``chat:fallback=1`` and posts a high-priority alert. Auto-clears at
  00:00 UTC unless ``chat:fallback:pin=1``.

- **Meter B — RPM-headroom (auto-flip).**
  Rolling 1-minute counter against ``WORKERS_AI_RPM_LIMIT``. Trips at
  ``trip_pct`` (default 0.70) of the limit; auto-clears when usage
  stays below ``clear_pct`` (default 0.50) for ``sustain_min`` (default
  5) consecutive minutes, unless ``chat:fallback:pin=1``.

- **Meter C — cumulative cost (notify-only).**
  365-day rolling cost counter. ``warning_pct`` (default 0.50) and
  ``alert_pct`` (default 0.70) of ``budget_usd`` (default $5 000) emit
  alerts. Never flips any flag — on-call decides.

The Redis client and the alert sink are passed in by the caller so the
class stays free of import-time side effects. The shared flag semantics
(A and B both write ``chat:fallback``; pin survives auto-clear) are
enforced by ``FallbackFlag``.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Protocol


CHAT_FALLBACK_KEY = "chat:fallback"
CHAT_FALLBACK_PIN_KEY = "chat:fallback:pin"
EMAIL_FALLBACK_KEY = "email:fallback"


class _RedisLike(Protocol):
    def get(self, key: str) -> Optional[bytes]: ...
    def set(self, key: str, value: str, ex: Optional[int] = None) -> Any: ...
    def delete(self, key: str) -> Any: ...
    def incr(self, key: str) -> int: ...
    def expire(self, key: str, ttl: int) -> Any: ...


AlertSink = Callable[[str, str, dict], None]
"""(severity, message, context) — severity is 'warning' | 'critical'."""


def _utc_day_key(now: Optional[float] = None) -> str:
    ts = now if now is not None else time.time()
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


# ── Shared fallback flag (Meters A + B share `chat:fallback`) ────────────────


class FallbackFlag:
    """Wraps the Redis hot-flag with pin + multi-source coordination.

    Meters A and B share ``chat:fallback``. The flag stays set as long
    as **any** source is active — auto-clear from Meter A does not
    clear a flag that Meter B is still tripping (and vice-versa). This
    is enforced via a Redis-backed source registry stored alongside
    the flag (``chat:fallback:sources`` — a string of comma-separated
    active source names; stored as a string so the contract works on
    any minimal Redis stub).

    ``chat:fallback:pin=1`` keeps the flag set across any auto-clear.
    The env var ``CHAT_FALLBACK`` on the ACA app is the durable cold-
    start default only — never the propagation path; flipping the
    Redis key is sub-ms.
    """

    def __init__(self, redis: _RedisLike, key: str = CHAT_FALLBACK_KEY,
                 pin_key: str = CHAT_FALLBACK_PIN_KEY,
                 sources_key: Optional[str] = None) -> None:
        self.redis = redis
        self.key = key
        self.pin_key = pin_key
        self.sources_key = sources_key or f"{key}:sources"

    # ── source-set helpers ────────────────────────────────────────
    def _read_sources(self) -> set[str]:
        raw = self.redis.get(self.sources_key)
        if raw is None:
            return set()
        s = _decode(raw).strip()
        return {p for p in s.split(",") if p}

    def _write_sources(self, sources: set[str]) -> None:
        if sources:
            self.redis.set(self.sources_key, ",".join(sorted(sources)))
        else:
            self.redis.delete(self.sources_key)

    def active_sources(self) -> set[str]:
        return self._read_sources()

    # ── flag state ────────────────────────────────────────────────
    def is_set(self) -> bool:
        v = self.redis.get(self.key)
        return v is not None and str(_decode(v)) not in ("", "0", "false")

    def is_pinned(self) -> bool:
        v = self.redis.get(self.pin_key)
        return v is not None and str(_decode(v)) not in ("", "0", "false")

    def trip(self, *, source: str) -> None:
        """Register ``source`` as active; set the flag if not already set."""
        s = self._read_sources()
        s.add(source)
        self._write_sources(s)
        self.redis.set(self.key, "1")

    def release(self, *, source: str) -> bool:
        """Remove ``source`` from the active set.

        Returns True if the flag was actually cleared (no other source
        is active and not pinned), False otherwise.
        """
        s = self._read_sources()
        s.discard(source)
        self._write_sources(s)
        if s:
            return False  # another meter still tripping
        if self.is_pinned():
            return False
        self.redis.delete(self.key)
        return True

    def auto_clear(self, *, source: Optional[str] = None) -> bool:
        """Backwards-compatible single-source clear.

        If ``source`` is provided, only that source is released. If not,
        all sources are released (callers must really intend to wipe
        everyone — used for ops-driven manual clears via runbook §10).
        """
        if source is not None:
            return self.release(source=source)
        if self.is_pinned():
            return False
        self._write_sources(set())
        self.redis.delete(self.key)
        return True


def _decode(v: Any) -> str:
    if isinstance(v, bytes):
        return v.decode("utf-8", "ignore")
    return str(v)


# ── Meter A — daily-call ceiling ──────────────────────────────────────────────


@dataclass
class MeterAConfig:
    warning_threshold: int = 8_000
    trip_threshold: int = 10_000
    ttl_seconds: int = 48 * 3600  # 48 h, covers UTC-day rollover gap
    redis_key_prefix: str = "meterA:"


class MeterA:
    """Daily-call meter (auto-flip).

    On every RAG call the dispatcher invokes ``increment()``; the meter
    bumps a per-UTC-day Redis counter (sub-ms hot-path), then evaluates
    the threshold ladder. The day rollover is handled by the natural
    key change (``meterA:YYYY-MM-DD``) plus the 48 h TTL so a stale
    counter cannot resurrect a yesterday-set flag tomorrow.
    """

    def __init__(self, redis: _RedisLike, flag: FallbackFlag,
                 alert_sink: AlertSink, config: Optional[MeterAConfig] = None) -> None:
        self.redis = redis
        self.flag = flag
        self.alert = alert_sink
        self.cfg = config or MeterAConfig()
        self._warned_for_day: Optional[str] = None
        self._tripped_for_day: Optional[str] = None

    def _key(self, now: Optional[float] = None) -> str:
        return f"{self.cfg.redis_key_prefix}{_utc_day_key(now)}"

    def increment(self, now: Optional[float] = None, *, n: int = 1) -> int:
        key = self._key(now)
        # incr + expire is the canonical Upstash pattern; expire is a no-op
        # if the key already has a TTL set.
        count = 0
        for _ in range(n):
            count = int(self.redis.incr(key))
        self.redis.expire(key, self.cfg.ttl_seconds)
        self._evaluate(count, now)
        return count

    def current(self, now: Optional[float] = None) -> int:
        v = self.redis.get(self._key(now))
        return int(_decode(v)) if v is not None else 0

    def _evaluate(self, count: int, now: Optional[float]) -> None:
        day = _utc_day_key(now)
        if count >= self.cfg.trip_threshold and self._tripped_for_day != day:
            self._tripped_for_day = day
            self.flag.trip(source="meterA")
            self.alert("critical",
                       f"Meter A tripped: {count} RAG calls today (>= "
                       f"{self.cfg.trip_threshold}); chat:fallback=1",
                       {"meter": "A", "count": count, "day": day})
        elif count >= self.cfg.warning_threshold and self._warned_for_day != day:
            self._warned_for_day = day
            self.alert("warning",
                       f"Meter A warning: {count} RAG calls today "
                       f"(>= {self.cfg.warning_threshold})",
                       {"meter": "A", "count": count, "day": day})

    def maybe_rollover(self, now: Optional[float] = None) -> bool:
        """Auto-clear the shared flag at 00:00 UTC unless pinned.

        Called by the meter background tick. Returns True if the flag
        was actually cleared (only happens when no other meter is also
        tripping). Idempotent — safe to call every minute.
        """
        day = _utc_day_key(now)
        if self._tripped_for_day and self._tripped_for_day != day:
            self._tripped_for_day = None
            self._warned_for_day = None
            return self.flag.release(source="meterA")
        return False


# ── Meter B — RPM-headroom (sliding window) ───────────────────────────────────


@dataclass
class MeterBConfig:
    rpm_limit: int = 300
    trip_pct: float = 0.70
    clear_pct: float = 0.50
    sustain_min: int = 5
    window_s: int = 60
    redis_key_prefix: str = "meterB:"
    bucket_seconds: int = 1  # 1-second granularity for the sliding window


class MeterB:
    """RPM-headroom meter (auto-flip), Redis-backed sliding window.

    The window is kept in Redis (per-second bucket counters with the
    same TTL as ``window_s + 5``) so every replica observes the same
    global RPM and the trip / clear decisions are consistent across
    the fleet — a single hot replica cannot mask saturation. Falls
    back to a tiny in-process window when Redis is unreachable so a
    Redis outage does not make the meter silent.
    """

    def __init__(self, redis: _RedisLike, flag: FallbackFlag,
                 alert_sink: AlertSink,
                 config: Optional[MeterBConfig] = None,
                 *, time_fn: Callable[[], float] = time.time) -> None:
        self.redis = redis
        self.flag = flag
        self.alert = alert_sink
        self.cfg = config or MeterBConfig()
        self._time = time_fn
        self._below_clear_since: Optional[float] = None
        self._tripped: bool = False
        # local-fallback window — used only when Redis raises
        self._local_window: list[float] = []

    @property
    def trip_count(self) -> int:
        return int(self.cfg.rpm_limit * self.cfg.trip_pct)

    @property
    def clear_count(self) -> int:
        return int(self.cfg.rpm_limit * self.cfg.clear_pct)

    # ── Redis-backed bucket helpers ──────────────────────────────
    def _bucket_key(self, ts: float) -> str:
        bucket = int(ts // self.cfg.bucket_seconds)
        return f"{self.cfg.redis_key_prefix}{bucket}"

    def _redis_incr(self, ts: float) -> bool:
        try:
            key = self._bucket_key(ts)
            self.redis.incr(key)
            self.redis.expire(key, self.cfg.window_s + 5)
            return True
        except Exception:
            return False

    def _redis_count(self, now: float) -> Optional[int]:
        try:
            cutoff = now - self.cfg.window_s
            start_b = int(cutoff // self.cfg.bucket_seconds)
            end_b = int(now // self.cfg.bucket_seconds)
            total = 0
            for b in range(start_b, end_b + 1):
                v = self.redis.get(f"{self.cfg.redis_key_prefix}{b}")
                if v is not None:
                    try:
                        total += int(_decode(v))
                    except ValueError:
                        pass
            return total
        except Exception:
            return None

    def _local_count(self, now: float) -> int:
        cutoff = now - self.cfg.window_s
        self._local_window = [t for t in self._local_window if t >= cutoff]
        return len(self._local_window)

    # ── public API ───────────────────────────────────────────────
    def record(self) -> int:
        now = self._time()
        if not self._redis_incr(now):
            self._local_window.append(now)
        count = self._redis_count(now)
        if count is None:
            count = self._local_count(now)
        self._evaluate(now, count)
        return count

    def tick(self) -> None:
        now = self._time()
        count = self._redis_count(now)
        if count is None:
            count = self._local_count(now)
        self._evaluate(now, count)

    def current(self) -> int:
        now = self._time()
        c = self._redis_count(now)
        return c if c is not None else self._local_count(now)

    def _evaluate(self, now: float, count: int) -> None:
        if count >= self.trip_count and not self._tripped:
            self._tripped = True
            self._below_clear_since = None
            self.flag.trip(source="meterB")
            self.alert("critical",
                       f"Meter B tripped: {count} RPM "
                       f"(>= {self.trip_count} = {int(self.cfg.trip_pct*100)}%"
                       f" of {self.cfg.rpm_limit}); chat:fallback=1",
                       {"meter": "B", "rpm": count})
        elif self._tripped:
            if count <= self.clear_count:
                if self._below_clear_since is None:
                    self._below_clear_since = now
                if (now - self._below_clear_since) >= self.cfg.sustain_min * 60:
                    self._tripped = False
                    self._below_clear_since = None
                    cleared = self.flag.release(source="meterB")
                    if cleared:
                        self.alert("warning",
                                   f"Meter B cleared: {count} RPM "
                                   f"sustained < {self.clear_count} for "
                                   f"{self.cfg.sustain_min}m",
                                   {"meter": "B", "rpm": count})
            else:
                self._below_clear_since = None


# ── Meter C — cumulative cost (notify-only) ───────────────────────────────────


@dataclass
class MeterCConfig:
    budget_usd: float = 5_000.0
    warning_pct: float = 0.50
    alert_pct: float = 0.70
    window_days: int = 365


class MeterC:
    """Cumulative-cost meter — notify-only, never flips a flag.

    The 365-day rolling window is implemented as an in-memory list of
    (ts, usd) records that the daily Lambda backstops to DynamoDB. Old
    entries are evicted on every ``record()``. ``current()`` returns
    the cumulative USD spent in the window.
    """

    def __init__(self, alert_sink: AlertSink,
                 config: Optional[MeterCConfig] = None,
                 *, time_fn: Callable[[], float] = time.time) -> None:
        self.alert = alert_sink
        self.cfg = config or MeterCConfig()
        self._time = time_fn
        self._events: list[tuple[float, float]] = []
        self._warned: bool = False
        self._alerted: bool = False

    def record(self, usd: float) -> float:
        now = self._time()
        self._events.append((now, usd))
        self._prune(now)
        self._evaluate()
        return self.current()

    def current(self) -> float:
        self._prune(self._time())
        return sum(u for _, u in self._events)

    def _prune(self, now: float) -> None:
        cutoff = now - self.cfg.window_days * 86_400
        self._events = [(t, u) for (t, u) in self._events if t >= cutoff]

    def _evaluate(self) -> None:
        total = sum(u for _, u in self._events)
        warn_at = self.cfg.budget_usd * self.cfg.warning_pct
        alert_at = self.cfg.budget_usd * self.cfg.alert_pct
        if total >= alert_at and not self._alerted:
            self._alerted = True
            self.alert("critical",
                       f"Meter C: ${total:.0f} cumulative cost "
                       f"(>= {int(self.cfg.alert_pct*100)}% of "
                       f"${self.cfg.budget_usd:.0f}); on-call decides "
                       f"whether to flip CHAT_FALLBACK manually",
                       {"meter": "C", "usd": total, "notify_only": True})
        elif total >= warn_at and not self._warned:
            self._warned = True
            self.alert("warning",
                       f"Meter C warning: ${total:.0f} cumulative cost "
                       f"(>= {int(self.cfg.warning_pct*100)}% of "
                       f"${self.cfg.budget_usd:.0f})",
                       {"meter": "C", "usd": total, "notify_only": True})


__all__ = [
    "AlertSink",
    "CHAT_FALLBACK_KEY", "CHAT_FALLBACK_PIN_KEY", "EMAIL_FALLBACK_KEY",
    "FallbackFlag",
    "MeterA", "MeterAConfig",
    "MeterB", "MeterBConfig",
    "MeterC", "MeterCConfig",
]
