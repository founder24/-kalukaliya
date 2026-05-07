"""Task #528 — auto-recover the fleet rollup writer when it falls behind.

The writer thread now watches its own queue depth: once the queue has
been ≥ 50% full for more than 30 s the writer flips into a degraded
"essentials only" mode that drops the per-event ``last_*_ts`` HSETs
and pipelines the remaining HINCRBYs so the queue can drain. A
counter (``_fleet_degraded_events``) records how many events were
processed in degraded mode so the admin tile can render a "writer
recovering" badge, and the companion ``memory_brain_fleet_dropped``
alert is suppressed while the writer is actively recovering so on-call
is only paged on a truly-stuck queue.

These tests pin that behaviour against a hand-rolled fake Upstash so
no live Redis is needed.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from tests._deps_stub import install_deps_stub  # noqa: E402

install_deps_stub()


class _FakeRedis:
    """Records every command it receives so tests can assert on the
    exact wire-level call sequence (HSETs vs HINCRBYs)."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.store: dict[str, dict[str, str]] = {}

    def hincrby(self, key, field, n=1):
        self.calls.append(("hincrby", key, field, n))
        d = self.store.setdefault(key, {})
        cur = int(d.get(field, "0"))
        d[field] = str(cur + n)
        return cur + n

    def hset(self, key, field, value):
        self.calls.append(("hset", key, field, value))
        self.store.setdefault(key, {})[field] = str(value)
        return 1

    def expire(self, key, seconds):
        self.calls.append(("expire", key, seconds))
        return 1

    def hgetall(self, key):
        return dict(self.store.get(key, {}))


class _PipelineRedis(_FakeRedis):
    """Adds a ``pipeline()`` factory so tests can assert that
    degraded-mode writes go through ``execute()`` (single round-trip)."""

    def __init__(self):
        super().__init__()
        self.pipelines_executed = 0

    def pipeline(self):
        outer = self

        class _Pipe:
            def __init__(self):
                self.queued: list[tuple] = []

            def hincrby(self, key, field, n=1):
                self.queued.append(("hincrby", key, field, n))

            def hset(self, key, field, value):
                self.queued.append(("hset", key, field, value))

            def expire(self, key, seconds):
                self.queued.append(("expire", key, seconds))

            def execute(self):
                outer.pipelines_executed += 1
                for cmd in self.queued:
                    if cmd[0] == "hincrby":
                        outer.hincrby(cmd[1], cmd[2], cmd[3])
                    elif cmd[0] == "hset":
                        outer.hset(cmd[1], cmd[2], cmd[3])
                    elif cmd[0] == "expire":
                        outer.expire(cmd[1], cmd[2])

        return _Pipe()


@pytest.fixture
def fake_redis(monkeypatch):
    import deps as _deps
    import memory_brain_metrics as _m
    fake = _FakeRedis()
    monkeypatch.setattr(_deps, "redis_client", fake, raising=False)
    monkeypatch.setenv("MEMORY_BRAIN_FLEET_ROLLUP", "1")
    # Block the daemon writer so tests drive ``_process_fleet_event``
    # directly and assert on the resulting wire calls deterministically.
    monkeypatch.setattr(_m, "_fleet_writer_started", True, raising=False)
    _m.reset()
    yield fake
    _m.reset()


@pytest.fixture
def pipeline_redis(monkeypatch):
    import deps as _deps
    import memory_brain_metrics as _m
    fake = _PipelineRedis()
    monkeypatch.setattr(_deps, "redis_client", fake, raising=False)
    monkeypatch.setenv("MEMORY_BRAIN_FLEET_ROLLUP", "1")
    monkeypatch.setattr(_m, "_fleet_writer_started", True, raising=False)
    _m.reset()
    yield fake
    _m.reset()


# ── pressure detection ──────────────────────────────────────────────


def test_short_burst_above_high_water_does_not_flip_degraded():
    """A queue spike that lasts < 30 s must NOT flip the writer into
    degraded mode — that's the whole point of the duration gate."""
    import memory_brain_metrics as _m
    _m.reset()
    high = int(_m._FLEET_QUEUE_MAX * _m._FLEET_PRESSURE_HIGH_RATIO)
    # First sample over the high-water mark just arms the timer.
    _m._update_fleet_pressure(high + 1, now_monotonic=1000.0)
    assert _m.is_fleet_writer_degraded() is False
    # Five seconds later still above — not yet 30 s sustained.
    _m._update_fleet_pressure(high + 1, now_monotonic=1005.0)
    assert _m.is_fleet_writer_degraded() is False
    # And then queue drains before the timer expires.
    _m._update_fleet_pressure(0, now_monotonic=1006.0)
    assert _m.is_fleet_writer_degraded() is False
    assert _m._fleet_pressure_started_at is None


def test_sustained_pressure_flips_writer_into_degraded_mode():
    """≥ 50% full for > 30 s → degraded mode latches on."""
    import memory_brain_metrics as _m
    _m.reset()
    high = int(_m._FLEET_QUEUE_MAX * _m._FLEET_PRESSURE_HIGH_RATIO)
    _m._update_fleet_pressure(high + 10, now_monotonic=1000.0)
    assert _m.is_fleet_writer_degraded() is False
    # Just past the 30 s gate.
    _m._update_fleet_pressure(high + 10, now_monotonic=1031.0)
    assert _m.is_fleet_writer_degraded() is True
    assert _m._fleet_degraded_since is not None


def test_degraded_mode_exits_with_hysteresis_when_queue_drains():
    """Hysteresis: degraded mode only releases once the queue is back
    below the LOW-water mark (25%), not just back below 50%."""
    import memory_brain_metrics as _m
    _m.reset()
    high = int(_m._FLEET_QUEUE_MAX * _m._FLEET_PRESSURE_HIGH_RATIO)
    low = int(_m._FLEET_QUEUE_MAX * _m._FLEET_PRESSURE_LOW_RATIO)
    # Latch degraded mode on.
    _m._update_fleet_pressure(high + 10, now_monotonic=1000.0)
    _m._update_fleet_pressure(high + 10, now_monotonic=1031.0)
    assert _m.is_fleet_writer_degraded() is True
    # Drain to just below the high-water mark — hysteresis says "stay".
    _m._update_fleet_pressure(high - 5, now_monotonic=1032.0)
    assert _m.is_fleet_writer_degraded() is True
    # Drain below the low-water mark — release.
    _m._update_fleet_pressure(low - 1, now_monotonic=1033.0)
    assert _m.is_fleet_writer_degraded() is False
    assert _m._fleet_degraded_since is None


# ── degraded-mode write shape ───────────────────────────────────────


def test_full_mode_writes_last_ts_hsets(fake_redis):
    """Sanity baseline: outside degraded mode the writer still emits
    the per-event last_ok_ts / last_fail_ts HSETs (and the per-worker
    twin) so we know the degraded-mode test below is testing a real
    behavioural change, not a no-op."""
    import memory_brain_metrics as _m
    _m._process_fleet_event(1.7e9, "write", "qa", True, None)
    cmds = [c[0] for c in fake_redis.calls]
    assert "hset" in cmds
    # last_ok_ts (aggregate) + worker:<pid>:last_ok_ts (per-worker)
    # + worker:<pid>:dropped (Task #527 piggyback snapshot)
    # + mb:fleet:workers seen-list (Task #530) = 4.
    hsets = [c for c in fake_redis.calls if c[0] == "hset"]
    assert len(hsets) == 4
    assert any("last_ok_ts" in str(c[2]) for c in hsets)
    assert any("dropped" in str(c[2]) for c in hsets)
    assert any(c[1] == "mb:fleet:workers" for c in hsets)


def test_degraded_mode_skips_last_ts_hsets_and_counts_events(fake_redis):
    """Degraded mode is the actual self-healing path: the per-event
    HSETs that dominate latency are dropped and the degraded-event
    counter advances so the admin tile knows the writer is recovering."""
    import memory_brain_metrics as _m
    _m._fleet_degraded_mode = True
    before = _m.get_fleet_degraded_events()
    _m._process_fleet_event(1.7e9, "write", "qa", True, None)
    cmds = [c[0] for c in fake_redis.calls]
    assert "hset" not in cmds, (
        "degraded mode must drop per-event last_*_ts HSETs to drain the queue"
    )
    # All four counter HINCRBYs (op, kind, worker:op) + the EXPIRE
    # refresh must still run so the dashboard doesn't lose data.
    assert cmds.count("hincrby") == 3  # op, kind, worker:op
    assert cmds.count("expire") == 1
    assert _m.get_fleet_degraded_events() == before + 1


def test_degraded_mode_uses_pipeline_when_available(pipeline_redis):
    """When the redis client supports ``pipeline()`` the degraded path
    must batch the HINCRBYs into a single round-trip — that's how we
    get latency back under the queue-arrival rate."""
    import memory_brain_metrics as _m
    _m._fleet_degraded_mode = True
    _m._process_fleet_event(1.7e9, "read", "query", False, "timeout")
    assert pipeline_redis.pipelines_executed == 1
    # And the per-event HSETs are still skipped via the pipeline.
    hsets = [c for c in pipeline_redis.calls if c[0] == "hset"]
    assert hsets == []
    # Counters still applied (op, kind, reason, worker:op) = 4 HINCRBYs.
    hincrbys = [c for c in pipeline_redis.calls if c[0] == "hincrby"]
    assert len(hincrbys) == 4


# ── admin-tile surface ──────────────────────────────────────────────


def test_get_fleet_stats_exposes_degraded_fields(fake_redis):
    import memory_brain_metrics as _m
    _m._fleet_degraded_mode = True
    _m._fleet_degraded_since = 1700000000.0
    _m._fleet_degraded_events = 17
    body = _m.get_fleet_stats()
    assert body["writer_degraded"] is True
    assert body["writer_degraded_since"] == 1700000000.0
    assert body["degraded_events_local"] == 17
    assert body["writer_queue_capacity"] == _m._FLEET_QUEUE_MAX
    assert "writer_queue_size" in body


# ── alerting-loop suppression ───────────────────────────────────────


def test_alert_suppressed_while_writer_is_actively_recovering():
    """Drops crossed the threshold but the writer is in degraded mode
    AND the queue has drained below the high-water mark — that's the
    auto-recovery success path. Page would be noise, so suppress it
    but still advance the high-water mark so the *next* genuine stall
    alerts on its own delta."""
    import metrics
    import memory_brain_metrics as mbm

    metrics._ALERT_THRESHOLDS["memory_brain_fleet_dropped_min"] = 5
    metrics._mb_fleet_dropped_last_seen = 0
    mbm._fleet_dropped_events = 25
    mbm._fleet_degraded_mode = True  # writer is healing itself

    captured: list[str] = []

    async def _fake_dispatch(alert_type, *a, **kw):
        captured.append(alert_type)
        return {}

    sleep_calls = {"n": 0}

    async def _fast_sleep(_):
        sleep_calls["n"] += 1
        if sleep_calls["n"] >= 2:
            raise asyncio.CancelledError()

    try:
        with patch.object(metrics, "_dispatch_alert", side_effect=_fake_dispatch), \
             patch.object(metrics, "_load_alert_settings", AsyncMock(return_value=None)), \
             patch.object(metrics, "_auto_expire_alerts", AsyncMock(return_value=None)), \
             patch.object(metrics.asyncio, "sleep", side_effect=_fast_sleep):
            loop = asyncio.new_event_loop()
            with pytest.raises(asyncio.CancelledError):
                loop.run_until_complete(metrics._alerting_loop())

        assert "memory_brain_fleet_dropped" not in captured, (
            f"alert must be suppressed while recovering, got: {captured}"
        )
        # High-water mark still advances so the next real stall is
        # measured against an honest baseline.
        assert metrics._mb_fleet_dropped_last_seen == 25
    finally:
        mbm.reset()
        metrics._mb_fleet_dropped_last_seen = 0
        metrics._alert_last_fired.pop("memory_brain_fleet_dropped", None)


def test_alert_still_fires_when_writer_is_stuck_not_recovering(monkeypatch):
    """Queue is still saturated (≥ high-water) → recovery is *failing*
    → on-call MUST be paged. This is the only condition that should
    reach the operator now that auto-recovery handles the transient
    case."""
    import metrics
    import memory_brain_metrics as mbm

    metrics._ALERT_THRESHOLDS["memory_brain_fleet_dropped_min"] = 5
    metrics._mb_fleet_dropped_last_seen = 0
    mbm._fleet_dropped_events = 25
    mbm._fleet_degraded_mode = True

    # Force ``qsize`` to report the queue still pinned above the
    # high-water mark (truly stuck — degraded mode hasn't drained).
    high = int(mbm._FLEET_QUEUE_MAX * mbm._FLEET_PRESSURE_HIGH_RATIO)
    monkeypatch.setattr(mbm._fleet_queue, "qsize", lambda: high + 1)

    captured: list[str] = []

    async def _fake_dispatch(alert_type, *a, **kw):
        captured.append(alert_type)
        return {}

    sleep_calls = {"n": 0}

    async def _fast_sleep(_):
        sleep_calls["n"] += 1
        if sleep_calls["n"] >= 2:
            raise asyncio.CancelledError()

    try:
        with patch.object(metrics, "_dispatch_alert", side_effect=_fake_dispatch), \
             patch.object(metrics, "_load_alert_settings", AsyncMock(return_value=None)), \
             patch.object(metrics, "_auto_expire_alerts", AsyncMock(return_value=None)), \
             patch.object(metrics.asyncio, "sleep", side_effect=_fast_sleep):
            loop = asyncio.new_event_loop()
            with pytest.raises(asyncio.CancelledError):
                loop.run_until_complete(metrics._alerting_loop())
        assert "memory_brain_fleet_dropped" in captured, (
            f"truly-stuck queue must page on-call, got: {captured}"
        )
    finally:
        mbm.reset()
        metrics._mb_fleet_dropped_last_seen = 0
        metrics._alert_last_fired.pop("memory_brain_fleet_dropped", None)


def test_is_fleet_writer_recovering_false_when_not_degraded():
    """Sanity: outside degraded mode the recovery flag is False (so
    the alerting loop doesn't start suppressing pages on healthy
    workers just because someone bumped the dropped counter directly
    in a test)."""
    import memory_brain_metrics as mbm
    mbm.reset()
    assert mbm.is_fleet_writer_degraded() is False
    assert mbm.is_fleet_writer_recovering() is False
