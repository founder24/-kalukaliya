"""Task #446 — fleet rollup tests for memory_brain_metrics.

Pin the contract of ``get_fleet_stats`` / ``get_fleet_hourly_buckets``
against a fake in-memory Redis so multi-worker aggregation is
verifiable without a live Upstash cluster.
"""
from __future__ import annotations

import time

import pytest


class _FakeRedis:
    """Minimal Upstash Redis stand-in — the only commands the fleet
    writer uses are HINCRBY / HSET / EXPIRE / HGETALL.
    """
    def __init__(self):
        self.store: dict[str, dict[str, str]] = {}
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

    def expire(self, key, seconds):
        self.ttls[key] = int(seconds)
        return 1

    def hgetall(self, key):
        return dict(self.store.get(key, {}))


@pytest.fixture
def fake_redis(monkeypatch):
    """Install a fake Upstash client and run the fleet writer
    *synchronously* inline so tests don't race the daemon thread.
    """
    import deps as _deps
    import memory_brain_metrics as _m
    fake = _FakeRedis()
    monkeypatch.setattr(_deps, "redis_client", fake, raising=False)
    monkeypatch.setenv("MEMORY_BRAIN_FLEET_ROLLUP", "1")
    _m.reset()

    # Drain the queue inline after every record_event so we don't
    # need to wait for the daemon thread (which we also disable by
    # marking it already-started).
    monkeypatch.setattr(_m, "_fleet_writer_started", True, raising=False)

    real_record = _m.record_event

    def _drain():
        import queue as _q
        try:
            while True:
                ts, op, kind, ok, reason = _m._fleet_queue.get_nowait()
                key = f"{_m._FLEET_KEY_PREFIX}{_m._hour_bucket(ts)}"
                outcome = "ok" if ok else "fail"
                fake.hincrby(key, f"op:{op}:{outcome}", 1)
                fake.hincrby(key, f"kind:{kind}:{outcome}", 1)
                if not ok and reason:
                    fake.hincrby(key, f"reason:{reason}", 1)
                fake.hset(key, f"last_{outcome}_ts", str(ts))
                fake.expire(key, _m._FLEET_BUCKET_TTL_SECONDS)
        except _q.Empty:
            return

    def _instrumented(op, *, kind, ok, reason=None):
        real_record(op, kind=kind, ok=ok, reason=reason)
        _drain()

    monkeypatch.setattr(_m, "record_event", _instrumented)
    yield fake
    _m.reset()


def test_fleet_rollup_aggregates_into_redis(fake_redis):
    import memory_brain_metrics as _m
    _m.record_event("write", kind="qa", ok=True)
    _m.record_event("write", kind="qa", ok=True)
    _m.record_event("write", kind="fact", ok=False, reason="voyage_error")
    _m.record_event("read",  kind="query", ok=True)
    _m.record_event("read",  kind="query", ok=False, reason="timeout")

    s = _m.get_fleet_stats()
    assert s["scope"] == "fleet"
    assert s["fleet_available"] is True
    assert s["total"] == 5
    assert s["failures"] == 2
    assert s["failure_rate_pct"] == 40.0
    assert s["by_op"]["write"] == {"ok": 2, "fail": 1, "total": 3}
    assert s["by_op"]["read"]  == {"ok": 1, "fail": 1, "total": 2}
    assert s["by_kind"]["qa"]["ok"] == 2
    assert s["by_kind"]["fact"]["fail"] == 1
    reasons = {r["reason"]: r["count"] for r in s["top_failure_reasons"]}
    assert reasons == {"voyage_error": 1, "timeout": 1}


def test_fleet_rollup_simulates_two_workers(fake_redis):
    """Two worker processes writing into the same hour bucket must
    sum, not collide. Simulate by enqueuing+draining twice.
    """
    import memory_brain_metrics as _m
    for _ in range(3):
        _m.record_event("write", kind="qa", ok=True)
    # "Other worker" writes — same fake Redis backs both.
    for _ in range(4):
        _m.record_event("write", kind="qa", ok=True)
    _m.record_event("write", kind="qa", ok=False, reason="voyage_error")

    s = _m.get_fleet_stats()
    assert s["by_op"]["write"]["ok"] == 7
    assert s["by_op"]["write"]["fail"] == 1
    assert s["total"] == 8


def test_fleet_buckets_have_stable_axis(fake_redis):
    import memory_brain_metrics as _m
    _m.record_event("write", kind="qa", ok=True)
    _m.record_event("read",  kind="query", ok=False, reason="timeout")

    buckets = _m.get_fleet_hourly_buckets(hours=12)
    assert len(buckets) == 12
    for b in buckets:
        for k in ("writes_ok", "writes_fail", "reads_ok", "reads_fail"):
            assert k in b
    total_writes = sum(b["writes_ok"] + b["writes_fail"] for b in buckets)
    total_reads  = sum(b["reads_ok"]  + b["reads_fail"]  for b in buckets)
    assert total_writes == 1
    assert total_reads == 1


def test_fleet_disabled_when_redis_missing(monkeypatch):
    import deps as _deps
    import memory_brain_metrics as _m
    monkeypatch.setattr(_deps, "redis_client", None, raising=False)
    _m.reset()

    s = _m.get_fleet_stats()
    assert s["fleet_available"] is False
    assert s["total"] == 0
    # Buckets still have a stable shape so the chart never crashes.
    buckets = _m.get_fleet_hourly_buckets(hours=24)
    assert len(buckets) == 24


def test_fleet_disabled_via_env_flag(monkeypatch):
    import deps as _deps
    import memory_brain_metrics as _m
    monkeypatch.setattr(_deps, "redis_client", _FakeRedis(), raising=False)
    monkeypatch.setenv("MEMORY_BRAIN_FLEET_ROLLUP", "0")
    _m.reset()

    # record_event should NOT enqueue when the flag is off — even
    # though Redis is technically available.
    _m.record_event("write", kind="qa", ok=True)
    assert _m._fleet_queue.empty()
    s = _m.get_fleet_stats()
    assert s["fleet_available"] is False


def test_fleet_unavailable_when_redis_reads_fail(monkeypatch):
    """If Upstash is configured but every HGETALL raises, the payload
    must report ``fleet_status="read_failed"`` and ``fleet_available
    =False`` so the UI auto-falls back to the per-worker view instead
    of showing a misleading zero state during a Redis outage.
    """
    import deps as _deps
    import memory_brain_metrics as _m

    class _BrokenRedis:
        def hincrby(self, *_a, **_k): return 1
        def hset(self, *_a, **_k): return 1
        def expire(self, *_a, **_k): return 1
        def hgetall(self, *_a, **_k):
            raise RuntimeError("upstash 503")

    monkeypatch.setattr(_deps, "redis_client", _BrokenRedis(), raising=False)
    monkeypatch.setenv("MEMORY_BRAIN_FLEET_ROLLUP", "1")
    _m.reset()

    s = _m.get_fleet_stats()
    assert s["fleet_configured"] is True
    assert s["fleet_read_ok"] is False
    assert s["fleet_available"] is False
    assert s["fleet_status"] == "read_failed"


def test_record_event_does_not_block_when_queue_full(monkeypatch):
    """A backed-up writer thread (Upstash hung) must NOT stall the
    chat hot path — overflowing events get counted and dropped.
    """
    import deps as _deps
    import memory_brain_metrics as _m
    monkeypatch.setattr(_deps, "redis_client", _FakeRedis(), raising=False)
    monkeypatch.setenv("MEMORY_BRAIN_FLEET_ROLLUP", "1")
    _m.reset()
    # Pretend the writer is alive but stalled — fill the queue to cap.
    monkeypatch.setattr(_m, "_fleet_writer_started", True, raising=False)
    while not _m._fleet_queue.full():
        _m._fleet_queue.put_nowait((time.time(), "write", "qa", True, None))

    before = _m._fleet_dropped_events
    # This must return immediately, not raise, not block.
    _m.record_event("write", kind="qa", ok=True)
    assert _m._fleet_dropped_events == before + 1
