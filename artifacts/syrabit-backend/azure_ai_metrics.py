"""In-process throttle / latency / error counters for Azure AI features.

Phase 5b — Task #338.

The wrappers in ``artifacts/syrabit/services/backend/azure_ai/`` and
the legacy ``providers/azure_openai.py`` path call ``record_*`` on
every data-plane round-trip. ``SNAPSHOT`` is then read by
``routes/admin_azure_ai.py`` so the admin panel renders even when
the App Insights pull cron is degraded — App Insights remains the
authoritative source for cross-replica aggregation, but this in-
process counter is the always-on safety net.

Counters are intentionally simple (no Prometheus client dependency)
so this module imports cleanly inside the cron-job runtime which
strips most observability extras to keep the image small.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

# 15-minute rolling window — matches the panel header.
_WINDOW_SECONDS = 15 * 60
_LATENCY_KEEP = 256  # ring of recent latencies per feature for p50/p95

_lock = threading.Lock()
_throttle_events: dict[str, deque[float]] = {}
_latencies: dict[str, deque[float]] = {}
_last_error: dict[str, dict[str, Any]] = {}

FEATURE_KEYS = (
    "openai",
    "speech",
    "translator",
    "document_intel",
    "vision",
    "content_safety",
    "language",
    "search",
    "anomaly_detector",
    "personalizer",
)


def _normalize(feature: str) -> str:
    if feature not in FEATURE_KEYS:
        # Caller passed an unknown feature; record under "_unknown"
        # rather than raise so a typo never crashes a request path.
        return "_unknown"
    return feature


def record_throttle(feature: str) -> None:
    key = _normalize(feature)
    now = time.time()
    with _lock:
        bucket = _throttle_events.setdefault(key, deque())
        bucket.append(now)
        _trim(bucket, now)


def record_latency(feature: str, latency_ms: float) -> None:
    key = _normalize(feature)
    with _lock:
        bucket = _latencies.setdefault(key, deque(maxlen=_LATENCY_KEEP))
        bucket.append(float(latency_ms))


def record_error(feature: str, message: str) -> None:
    key = _normalize(feature)
    with _lock:
        _last_error[key] = {
            "lastErrorAt": _iso_now(),
            "lastErrorMessage": message[:280],
        }


def _trim(bucket: deque[float], now: float) -> None:
    cutoff = now - _WINDOW_SECONDS
    while bucket and bucket[0] < cutoff:
        bucket.popleft()


def _iso_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = max(0, min(len(ordered) - 1, int(round(pct * (len(ordered) - 1)))))
    return ordered[rank]


def _snapshot_locked() -> dict[str, dict[str, Any]]:
    now = time.time()
    out: dict[str, dict[str, Any]] = {}
    for key in FEATURE_KEYS:
        bucket = _throttle_events.get(key)
        if bucket is not None:
            _trim(bucket, now)
        latencies = list(_latencies.get(key, ()))
        out[key] = {
            "throttle15m": len(bucket) if bucket is not None else 0,
            "latencyP50Ms": _percentile(latencies, 0.50),
            "latencyP95Ms": _percentile(latencies, 0.95),
        }
        out[key].update(_last_error.get(key, {}))
    return out


class _SnapshotProxy:
    """``SNAPSHOT[key]`` returns a fresh dict on every access.

    Implemented as a proxy rather than a plain dict so callers don't
    need to know to call a method. The admin route iterates with
    ``for key, snap in SNAPSHOT.items()`` which works as expected.
    """

    def __getitem__(self, key: str) -> dict[str, Any]:
        with _lock:
            return _snapshot_locked()[key]

    def get(self, key: str, default: Any = None) -> Any:
        with _lock:
            snap = _snapshot_locked()
        return snap.get(key, default)

    def items(self):
        with _lock:
            return list(_snapshot_locked().items())

    def keys(self):
        return list(FEATURE_KEYS)

    def values(self):
        with _lock:
            return list(_snapshot_locked().values())

    def __iter__(self):
        return iter(FEATURE_KEYS)

    def __len__(self) -> int:
        return len(FEATURE_KEYS)

    def __bool__(self) -> bool:
        return True


SNAPSHOT = _SnapshotProxy()


def reset_for_tests() -> None:
    with _lock:
        _throttle_events.clear()
        _latencies.clear()
        _last_error.clear()
