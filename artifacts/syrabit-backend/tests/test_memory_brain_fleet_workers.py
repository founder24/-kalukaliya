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
                # Task #530 — mirror the production seen-list write
                # so tests exercise the stale-row code path.
                fake.hset(_m._FLEET_WORKERS_KEY, str(pid), str(ts))
                fake.expire(_m._FLEET_WORKERS_KEY, _m._FLEET_BUCKET_TTL_SECONDS)
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


def test_per_worker_row_includes_last_seen_and_not_stale_when_fresh(fake_redis, monkeypatch):
    """Task #530 — every row must carry ``last_seen_ts`` (max of
    last_ok_ts / last_fail_ts) and an age-derived ``is_stale`` flag.
    Fresh writes must not be flagged stale.
    """
    import memory_brain_metrics as _m
    monkeypatch.setattr(_m, "_current_worker_pid", lambda: 404)
    _m.record_event("write", kind="qa", ok=True)

    workers = _m.get_fleet_workers()
    assert len(workers) == 1
    row = workers[0]
    assert row["pid"] == 404
    assert row["last_seen_ts"] is not None
    assert row["last_seen_ts"] >= row["last_ok_ts"]
    assert row["last_seen_age_seconds"] is not None
    assert row["last_seen_age_seconds"] < 5
    assert row["is_stale"] is False
    assert row["stale_threshold_seconds"] > 0


def test_per_worker_row_marked_stale_when_past_threshold(fake_redis, monkeypatch):
    """A worker whose most-recent event is older than the configured
    stale threshold must render with ``is_stale=True`` so the admin
    tile flags it. Drives the env knob down to 1s and forges an old
    timestamp directly into the seen-list to avoid sleeping.
    """
    import memory_brain_metrics as _m
    import time as _t
    monkeypatch.setenv("MEMORY_BRAIN_WORKER_STALE_SECONDS", "1")
    monkeypatch.setattr(_m, "_current_worker_pid", lambda: 505)

    # Forge an ancient seen-list entry + ancient last_ok_ts so the
    # row's last_seen_ts is comfortably past the 1s threshold.
    ancient = _t.time() - 3600
    fake_redis.hset(_m._FLEET_WORKERS_KEY, "505", str(ancient))
    hour_key = f"{_m._FLEET_KEY_PREFIX}{_m._hour_bucket(ancient)}"
    fake_redis.hset(hour_key, "worker:505:last_ok_ts", str(ancient))
    fake_redis.hincrby(hour_key, "worker:505:op:write:ok", 1)

    workers = _m.get_fleet_workers()
    by_pid = {w["pid"]: w for w in workers}
    assert 505 in by_pid
    assert by_pid[505]["is_stale"] is True
    assert by_pid[505]["last_seen_age_seconds"] >= 3500


def test_silent_worker_with_no_current_hour_events_still_appears(fake_redis, monkeypatch):
    """The whole point of the durable seen-list (Task #530): a worker
    that crashed silently with zero events in the current hour bucket
    must still appear in the table — so the operator can see that
    pid 42 stopped reporting an hour ago instead of it vanishing.
    """
    import memory_brain_metrics as _m
    import time as _t
    monkeypatch.setenv("MEMORY_BRAIN_WORKER_STALE_SECONDS", "60")

    # Simulate a worker that died: only the seen-list hash carries
    # an old timestamp; nothing in the hour-keyed buckets references
    # this pid at all.
    dead_ts = _t.time() - 1800  # 30 minutes ago
    fake_redis.hset(_m._FLEET_WORKERS_KEY, "999", str(dead_ts))

    workers = _m.get_fleet_workers()
    by_pid = {w["pid"]: w for w in workers}
    assert 999 in by_pid, "dead worker must still appear via seen-list"
    dead = by_pid[999]
    assert dead["total"] == 0
    assert dead["is_stale"] is True
    assert dead["last_seen_ts"] is not None
    assert dead["last_seen_age_seconds"] >= 1700


def test_stale_workers_sort_first(fake_redis, monkeypatch):
    """Stale rows must float to the top of the table so the operator's
    eye lands on a silent crash before a noisy-but-alive worker.
    """
    import memory_brain_metrics as _m
    import time as _t
    monkeypatch.setenv("MEMORY_BRAIN_WORKER_STALE_SECONDS", "60")

    # Fresh, healthy worker.
    monkeypatch.setattr(_m, "_current_worker_pid", lambda: 111)
    _m.record_event("write", kind="qa", ok=True)

    # Dead worker, only in the seen-list.
    fake_redis.hset(_m._FLEET_WORKERS_KEY, "222", str(_t.time() - 7200))

    workers = _m.get_fleet_workers()
    assert workers[0]["pid"] == 222
    assert workers[0]["is_stale"] is True


def test_seen_list_prunes_entries_older_than_window(fake_redis, monkeypatch):
    """Task #530 review follow-up — Redis hash fields don't have
    per-field TTLs, so the writer can't auto-evict ancient pids. We
    prune at read time: any seen-list entry older than the bucket TTL
    is dropped from the response and HDEL'd from the hash so a
    long-dead worker doesn't clutter the table forever.
    """
    import memory_brain_metrics as _m
    import time as _t

    ancient = _t.time() - (_m._FLEET_BUCKET_TTL_SECONDS + 3600)
    fake_redis.hset(_m._FLEET_WORKERS_KEY, "888", str(ancient))
    # Also seed a fresh entry so we can prove pruning is selective.
    monkeypatch.setattr(_m, "_current_worker_pid", lambda: 777)
    _m.record_event("write", kind="qa", ok=True)

    pids = {w["pid"] for w in _m.get_fleet_workers()}
    assert 888 not in pids, "ancient seen-list entry must be pruned"
    assert 777 in pids
    # The HDEL side-effect must actually have removed the field so a
    # second read doesn't have to walk it again.
    remaining = fake_redis.hgetall(_m._FLEET_WORKERS_KEY)
    assert "888" not in remaining
    assert "777" in remaining


def test_per_worker_tracks_last_fail_ts(fake_redis, monkeypatch):
    import memory_brain_metrics as _m
    monkeypatch.setattr(_m, "_current_worker_pid", lambda: 303)
    _m.record_event("write", kind="qa", ok=False, reason="timeout")

    workers = _m.get_fleet_workers()
    assert len(workers) == 1
    assert workers[0]["pid"] == 303
    assert workers[0]["last_fail_ts"] is not None
    assert workers[0]["last_ok_ts"] is None
