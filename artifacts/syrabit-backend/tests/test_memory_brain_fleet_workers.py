"""Task #483 — per-worker fan-out tests for memory_brain_metrics.

The fleet rollup hash now also carries ``worker:<pid>:op:<op>:<outcome>``
counters so the admin tile can show which gunicorn worker is the one
with the failing Voyage key during a partial outage. These tests pin
the read-side aggregation contract that ``get_fleet_workers`` exposes
to the admin route.
"""
from __future__ import annotations

import pytest

from tests.test_memory_brain_fleet_rollup import _FakeRedis  # noqa: E402


@pytest.fixture
def fake_redis(monkeypatch):
    """Same inline-drain harness as test_memory_brain_fleet_rollup, but
    written here so we can simulate two distinct worker pids by
    monkeypatching ``_WORKER_PID`` between recordings.
    """
    import deps as _deps
    import memory_brain_metrics as _m
    fake = _FakeRedis()
    monkeypatch.setattr(_deps, "redis_client", fake, raising=False)
    monkeypatch.setenv("MEMORY_BRAIN_FLEET_ROLLUP", "1")
    _m.reset()
    monkeypatch.setattr(_m, "_fleet_writer_started", True, raising=False)

    real_record = _m.record_event

    def _drain():
        import queue as _q
        try:
            while True:
                ts, op, kind, ok, reason = _m._fleet_queue.get_nowait()
                key = f"{_m._FLEET_KEY_PREFIX}{_m._hour_bucket(ts)}"
                outcome = "ok" if ok else "fail"
                # Mirror production: resolve pid at write time via
                # the helper so the gunicorn-preload regression in
                # ``test_pid_resolved_at_write_time_not_import_time``
                # exercises the real code path.
                pid = _m._current_worker_pid()
                fake.hincrby(key, f"op:{op}:{outcome}", 1)
                fake.hincrby(key, f"kind:{kind}:{outcome}", 1)
                if not ok and reason:
                    fake.hincrby(key, f"reason:{reason}", 1)
                fake.hincrby(key, f"worker:{pid}:op:{op}:{outcome}", 1)
                fake.hset(key, f"last_{outcome}_ts", str(ts))
                fake.hset(key, f"worker:{pid}:last_{outcome}_ts", str(ts))
                fake.expire(key, _m._FLEET_BUCKET_TTL_SECONDS)
        except _q.Empty:
            return

    def _instrumented(op, *, kind, ok, reason=None):
        real_record(op, kind=kind, ok=ok, reason=reason)
        _drain()

    monkeypatch.setattr(_m, "record_event", _instrumented)
    yield fake
    _m.reset()


def test_per_worker_breakdown_isolates_pids(fake_redis, monkeypatch):
    """Two workers writing into the same hour bucket must each get
    their own row. The misbehaving worker must sort first.
    """
    import memory_brain_metrics as _m

    # Worker A: healthy.
    monkeypatch.setattr(_m, "_current_worker_pid", lambda: 101)
    for _ in range(50):
        _m.record_event("write", kind="qa", ok=True)
    for _ in range(20):
        _m.record_event("read", kind="query", ok=True)

    # Worker B: Voyage key revoked — every write fails.
    monkeypatch.setattr(_m, "_current_worker_pid", lambda: 202)
    for _ in range(30):
        _m.record_event("write", kind="qa", ok=False, reason="voyage_error")
    for _ in range(5):
        _m.record_event("read", kind="query", ok=True)

    workers = _m.get_fleet_workers()
    assert len(workers) == 2
    by_pid = {w["pid"]: w for w in workers}

    assert by_pid[101]["writes_ok"] == 50
    assert by_pid[101]["writes_fail"] == 0
    assert by_pid[101]["reads_ok"] == 20
    assert by_pid[101]["total"] == 70
    assert by_pid[101]["failures"] == 0
    assert by_pid[101]["failure_rate_pct"] == 0.0

    assert by_pid[202]["writes_ok"] == 0
    assert by_pid[202]["writes_fail"] == 30
    assert by_pid[202]["reads_ok"] == 5
    assert by_pid[202]["total"] == 35
    assert by_pid[202]["failures"] == 30
    # 30 / 35 = 85.71%
    assert by_pid[202]["failure_rate_pct"] > 50.0

    # Misbehaving worker must sort first so the operator's eye lands
    # on it immediately when expanding the disclosure.
    assert workers[0]["pid"] == 202

    # Aggregate roll-up still matches the sum of per-worker counts.
    s = _m.get_fleet_stats()
    assert s["total"] == 105
    assert s["failures"] == 30


def test_pid_resolved_at_write_time_not_import_time(fake_redis, monkeypatch):
    """Regression for the gunicorn preload bug: capturing
    ``os.getpid()`` at module import time would attribute every
    worker's events to the master pid (because ``preload_app=True``
    imports this module pre-fork). The writer MUST resolve the pid
    lazily for each event so per-worker attribution actually works
    in production. We assert that swapping the helper *between*
    ``record_event`` calls produces distinct rows.
    """
    import memory_brain_metrics as _m

    # Sanity: helper exists and is callable (the writer depends on it).
    assert callable(_m._current_worker_pid)

    monkeypatch.setattr(_m, "_current_worker_pid", lambda: 7001)
    _m.record_event("write", kind="qa", ok=True)
    monkeypatch.setattr(_m, "_current_worker_pid", lambda: 7002)
    _m.record_event("write", kind="qa", ok=False, reason="voyage_error")

    pids = {w["pid"] for w in _m.get_fleet_workers()}
    assert pids == {7001, 7002}, (
        "PID must be resolved per-event, not snapshotted at import "
        f"time. Got rows for {pids!r}."
    )


def test_per_worker_returns_empty_when_redis_unwired(monkeypatch):
    import deps as _deps
    import memory_brain_metrics as _m
    monkeypatch.setattr(_deps, "redis_client", None, raising=False)
    _m.reset()
    assert _m.get_fleet_workers() == []


def test_per_worker_returns_empty_when_redis_reads_fail(monkeypatch):
    """A Redis outage must not strand the admin route — return [] so
    the frontend hides the disclosure cleanly.
    """
    import deps as _deps
    import memory_brain_metrics as _m

    class _Broken:
        def hincrby(self, *_a, **_k): return 1
        def hset(self, *_a, **_k): return 1
        def expire(self, *_a, **_k): return 1
        def hgetall(self, *_a, **_k):
            raise RuntimeError("upstash 503")

    monkeypatch.setattr(_deps, "redis_client", _Broken(), raising=False)
    monkeypatch.setenv("MEMORY_BRAIN_FLEET_ROLLUP", "1")
    _m.reset()
    assert _m.get_fleet_workers() == []


def test_per_worker_tracks_last_fail_ts(fake_redis, monkeypatch):
    import memory_brain_metrics as _m
    monkeypatch.setattr(_m, "_current_worker_pid", lambda: 303)
    _m.record_event("write", kind="qa", ok=False, reason="timeout")

    workers = _m.get_fleet_workers()
    assert len(workers) == 1
    assert workers[0]["pid"] == 303
    assert workers[0]["last_fail_ts"] is not None
    assert workers[0]["last_ok_ts"] is None
