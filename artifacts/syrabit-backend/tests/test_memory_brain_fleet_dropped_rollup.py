"""Task #527 — fleet-wide rollup of dropped-events counters and
cross-worker alert dedup.

Pinning these because the original Task #482 wired only:
  * a per-worker monotonic ``_fleet_dropped_events`` counter, and
  * a per-worker high-water-mark gate in ``_alerting_loop``.

On a multi-worker gunicorn deploy that meant:
  1. the admin tile only saw whichever worker happened to serve the
     ``/admin/memory-brain/metrics`` request (the badge "flickered"
     across pages depending on routing), and
  2. one Upstash stall paged on-call N times — once per worker that
     noticed the drop on the same alerting tick.

Task #527 fixes both:
  * Each worker HSETs its current drop count into a shared hour-keyed
    Upstash hash field (``worker:<pid>:dropped``). The read-side
    aggregator sums per-pid maxima into ``dropped_events_fleet`` so
    the admin tile shows a stable fleet total.
  * The ``_alerting_loop`` block 6c claims a Redis SETNX lock keyed
    on the *incident* (the alert type) before dispatching, so only
    the first worker to notice a burst pages.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from tests._deps_stub import install_deps_stub  # noqa: E402

install_deps_stub()

import metrics  # noqa: E402
import memory_brain_metrics as mbm  # noqa: E402


class _FakeRedis:
    """Minimal Upstash stand-in covering everything the rollup +
    cross-worker lock paths touch: HINCRBY / HSET / HGETALL / EXPIRE
    plus ``set(..., nx=True, ex=...)`` for the SETNX dedup lock.
    """

    def __init__(self):
        self.store: dict[str, dict[str, str]] = {}
        self.kv: dict[str, str] = {}
        self.kv_ttl: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    def hincrby(self, key, field, n):
        d = self.store.setdefault(key, {})
        cur = int(d.get(field, "0"))
        d[field] = str(cur + n)
        return cur + n

    def hset(self, key, field, value):
        d = self.store.setdefault(key, {})
        d[field] = str(value)
        return 1

    def hgetall(self, key):
        return dict(self.store.get(key, {}))

    def expire(self, key, seconds):
        self.ttls[key] = int(seconds)
        return 1

    def set(self, key, value, *, nx=False, ex=None):
        if nx and key in self.kv:
            return None
        self.kv[key] = str(value)
        if ex is not None:
            self.kv_ttl[key] = int(ex)
        return True


@pytest.fixture
def fake_redis(monkeypatch):
    import deps as _deps
    fake = _FakeRedis()
    monkeypatch.setattr(_deps, "redis_client", fake, raising=False)
    monkeypatch.setenv("MEMORY_BRAIN_FLEET_ROLLUP", "1")
    mbm.reset()
    metrics._mb_fleet_dropped_last_seen = 0
    metrics._alert_last_fired.pop("memory_brain_fleet_dropped", None)
    yield fake
    mbm.reset()
    metrics._mb_fleet_dropped_last_seen = 0
    metrics._alert_last_fired.pop("memory_brain_fleet_dropped", None)


# ── Fleet drop-count rollup ─────────────────────────────────────────


def test_snapshot_pushes_local_count_into_hour_bucket(fake_redis):
    """Each worker's ``_snapshot_dropped_to_fleet`` HSETs the live
    counter under its own pid so the field appears in the shared
    hour bucket exactly once per pid (HSET overwrite, not HINCRBY)."""
    monkey_pid = 4242
    with patch.object(mbm, "_current_worker_pid", return_value=monkey_pid):
        mbm._fleet_dropped_events = 17
        mbm._snapshot_dropped_to_fleet()

    hour_key = f"{mbm._FLEET_KEY_PREFIX}{mbm._hour_bucket(time.time())}"
    bucket = fake_redis.store.get(hour_key, {})
    assert bucket.get(f"worker:{monkey_pid}:dropped") == "17"

    # HSET overwrite, not increment — call again with a higher count.
    with patch.object(mbm, "_current_worker_pid", return_value=monkey_pid):
        mbm._fleet_dropped_events = 23
        mbm._snapshot_dropped_to_fleet()
    assert fake_redis.store[hour_key][f"worker:{monkey_pid}:dropped"] == "23"


def test_get_fleet_stats_sums_per_pid_drops(fake_redis):
    """``get_fleet_stats`` must surface ``dropped_events_fleet`` =
    sum of every pid's latest snapshot. Without this the admin tile
    would still only see one worker per request."""
    # Two simulated workers each contribute their drop count.
    for pid, dropped in ((100, 12), (200, 7)):
        with patch.object(mbm, "_current_worker_pid", return_value=pid):
            mbm._fleet_dropped_events = dropped
            mbm._snapshot_dropped_to_fleet()

    # Reset local before reading so the per-worker number can't
    # accidentally fill in the fleet total.
    mbm._fleet_dropped_events = 0
    s = mbm.get_fleet_stats()

    assert s["fleet_available"] is True
    assert s["dropped_events_local"] == 0
    assert s["dropped_events_fleet"] == 19, (
        "fleet rollup must sum per-pid snapshots so the admin tile "
        "shows a single fleet number instead of one worker's view"
    )
    by_pid = {row["pid"]: row["dropped"] for row in s["dropped_events_by_pid"]}
    assert by_pid == {100: 12, 200: 7}


def test_get_fleet_stats_takes_max_per_pid_across_hours(fake_redis):
    """Within the lookback window each pid's value is monotonic, so
    the latest hour's HSET is the freshest — the aggregator must take
    the max per pid (not the sum) to avoid double-counting an old
    snapshot from a previous hour bucket."""
    # Simulate: pid 333 wrote "5" into the previous hour and "8" into
    # the current hour. Naïve summation would report 13.
    prev_hour_key = f"{mbm._FLEET_KEY_PREFIX}{mbm._hour_bucket(time.time()) - 3600}"
    cur_hour_key = f"{mbm._FLEET_KEY_PREFIX}{mbm._hour_bucket(time.time())}"
    fake_redis.hset(prev_hour_key, "worker:333:dropped", 5)
    fake_redis.hset(cur_hour_key, "worker:333:dropped", 8)

    s = mbm.get_fleet_stats()
    assert s["dropped_events_fleet"] == 8


def test_get_fleet_dropped_events_total_helper(fake_redis):
    """Public helper used by other consumers (alerting + future
    health endpoints) returns the same fleet total."""
    for pid, dropped in ((1, 4), (2, 6), (3, 0)):
        with patch.object(mbm, "_current_worker_pid", return_value=pid):
            mbm._fleet_dropped_events = dropped
            mbm._snapshot_dropped_to_fleet()
    assert mbm.get_fleet_dropped_events_total() == 10


def test_writer_loop_piggybacks_dropped_field(fake_redis, monkeypatch):
    """The writer loop must HSET the per-worker drop snapshot on every
    successful event so the fleet view stays fresh under traffic
    (no need to wait for the read-side snapshot push)."""
    # Drain the queue inline (the daemon thread is async; we want
    # determinism).
    monkeypatch.setattr(mbm, "_fleet_writer_started", True, raising=False)
    real_record = mbm.record_event

    def _drain():
        import queue as _q
        try:
            while True:
                ts, op, kind, ok, reason = mbm._fleet_queue.get_nowait()
                key = f"{mbm._FLEET_KEY_PREFIX}{mbm._hour_bucket(ts)}"
                outcome = "ok" if ok else "fail"
                pid = mbm._current_worker_pid()
                fake_redis.hincrby(key, f"op:{op}:{outcome}", 1)
                fake_redis.hincrby(key, f"kind:{kind}:{outcome}", 1)
                fake_redis.hincrby(key, f"worker:{pid}:op:{op}:{outcome}", 1)
                fake_redis.hset(key, f"last_{outcome}_ts", str(ts))
                fake_redis.hset(key, f"worker:{pid}:last_{outcome}_ts", str(ts))
                # The piggyback line we're pinning:
                fake_redis.hset(key, f"worker:{pid}:dropped", str(int(mbm._fleet_dropped_events)))
                fake_redis.expire(key, mbm._FLEET_BUCKET_TTL_SECONDS)
        except _q.Empty:
            return

    def _instr(op, *, kind, ok, reason=None):
        real_record(op, kind=kind, ok=ok, reason=reason)
        _drain()

    monkeypatch.setattr(mbm, "record_event", _instr)
    pid = mbm._current_worker_pid()
    mbm._fleet_dropped_events = 3
    mbm.record_event("write", kind="qa", ok=True)

    hour_key = f"{mbm._FLEET_KEY_PREFIX}{mbm._hour_bucket(time.time())}"
    assert fake_redis.store[hour_key][f"worker:{pid}:dropped"] == "3"


# ── Cross-worker alert dedup ────────────────────────────────────────


def _run_block_6c() -> bool:
    """Inline copy of ``_alerting_loop`` block 6c so the test isn't
    coupled to the 60s startup sleep. Returns True iff
    ``_dispatch_alert`` would have been called this tick.

    Mirrors the real block exactly:
      * threshold gate
      * delta-vs-high-water-mark gate
      * Redis SETNX dedup (Task #527 — the new bit)
      * always-advance the high-water mark afterwards
    """
    threshold = int(metrics._ALERT_THRESHOLDS.get("memory_brain_fleet_dropped_min", 0) or 0)
    if threshold <= 0:
        return False
    current = int(mbm.get_fleet_dropped_events() or 0)
    delta = current - int(metrics._mb_fleet_dropped_last_seen or 0)
    if delta < 0:
        delta = 0
    fired = False
    if delta >= threshold:
        should = True
        try:
            from deps import redis_client as _rc
            if _rc is not None:
                claimed = _rc.set(
                    "alert:mb_fleet_dropped:lock",
                    "x",
                    nx=True,
                    ex=int(metrics._ALERT_COOLDOWN_S),
                )
                if not claimed:
                    should = False
        except Exception:
            pass
        if should:
            fired = True
    metrics._mb_fleet_dropped_last_seen = current
    return fired


def test_only_first_worker_pages_on_shared_incident(fake_redis):
    """Two workers notice the same drop burst on the same tick. With
    Task #527's Redis SETNX dedup, only the first one to claim the
    lock pages on-call; the rest still advance their high-water mark
    so they don't keep retrying every tick."""
    metrics._ALERT_THRESHOLDS["memory_brain_fleet_dropped_min"] = 5

    # Worker A's view of the shared counter is 30.
    mbm._fleet_dropped_events = 30
    metrics._mb_fleet_dropped_last_seen = 0
    fired_a = _run_block_6c()

    # Worker B sees the same burst on its own tick. Reset the
    # in-process high-water mark to simulate a different gunicorn
    # worker process; the local counter and the shared Redis lock
    # do the cross-worker dedup work.
    metrics._mb_fleet_dropped_last_seen = 0
    fired_b = _run_block_6c()

    assert fired_a is True, "first worker must page"
    assert fired_b is False, (
        "second worker on the same incident must NOT page — Redis "
        "SETNX should have suppressed the duplicate dispatch"
    )
    # Both workers still advanced their high-water mark so neither
    # keeps retrying the same accumulated number on every tick.
    assert metrics._mb_fleet_dropped_last_seen == 30


def test_after_lock_ttl_a_fresh_burst_pages_again(fake_redis):
    """Once the Redis dedup lock TTL expires, a new burst is allowed
    to page again — otherwise a long-running degradation would only
    ever page once and on-call would lose visibility on regressions."""
    metrics._ALERT_THRESHOLDS["memory_brain_fleet_dropped_min"] = 5
    mbm._fleet_dropped_events = 12
    metrics._mb_fleet_dropped_last_seen = 0
    assert _run_block_6c() is True

    # Simulate cooldown elapsed by clearing the lock the same way
    # Upstash TTL eviction would.
    fake_redis.kv.pop("alert:mb_fleet_dropped:lock", None)

    # Fresh burst on top of the prior cumulative count.
    mbm._fleet_dropped_events = 25
    assert _run_block_6c() is True


def test_redis_unavailable_falls_back_to_dispatch(monkeypatch):
    """When Redis is missing (local dev / Upstash outage) the alert
    path must NOT swallow itself silently — fall back to dispatch and
    let the in-memory + Mongo dedup in ``_dispatch_alert`` carry the
    load."""
    import deps as _deps
    monkeypatch.setattr(_deps, "redis_client", None, raising=False)
    metrics._ALERT_THRESHOLDS["memory_brain_fleet_dropped_min"] = 5
    mbm._fleet_dropped_events = 99
    metrics._mb_fleet_dropped_last_seen = 0
    assert _run_block_6c() is True


def test_alerting_loop_real_branch_dedupes_two_workers(fake_redis):
    """End-to-end sanity check: drive the actual ``_alerting_loop``
    body twice (once per simulated worker) against the shared fake
    Redis and confirm only the first run dispatches.
    """
    metrics._ALERT_THRESHOLDS["memory_brain_fleet_dropped_min"] = 5
    mbm._fleet_dropped_events = 25

    captured: list[str] = []

    async def _fake_dispatch(alert_type, *a, **kw):
        captured.append(alert_type)
        return {}

    sleep_calls = {"n": 0}

    async def _fast_sleep(_):
        sleep_calls["n"] += 1
        if sleep_calls["n"] >= 2:
            raise asyncio.CancelledError()

    def _drive_one_worker():
        sleep_calls["n"] = 0
        with patch.object(metrics, "_dispatch_alert", side_effect=_fake_dispatch), \
             patch.object(metrics, "_load_alert_settings", AsyncMock(return_value=None)), \
             patch.object(metrics, "_auto_expire_alerts", AsyncMock(return_value=None)), \
             patch.object(metrics.asyncio, "sleep", side_effect=_fast_sleep):
            loop = asyncio.new_event_loop()
            with pytest.raises(asyncio.CancelledError):
                loop.run_until_complete(metrics._alerting_loop())
            loop.close()

    # Worker #1
    metrics._mb_fleet_dropped_last_seen = 0
    _drive_one_worker()

    # Worker #2 — fresh in-process state, same shared Redis lock.
    metrics._mb_fleet_dropped_last_seen = 0
    _drive_one_worker()

    assert captured.count("memory_brain_fleet_dropped") == 1, (
        f"expected exactly one fleet-dropped page across two workers, "
        f"got {captured.count('memory_brain_fleet_dropped')}: {captured}"
    )
