"""Task #529 — page on-call when ONE worker is silently failing
while the rest of the fleet is healthy.

Section 6b of ``metrics._alerting_loop`` only watches the
fleet-aggregate failure rate pulled from this worker's local ring
buffer. A single worker pid stuck at 100% failure (revoked Voyage
key, stuck Mongo connection, per-process secret that didn't
refresh) can sit silent indefinitely if the other workers carry
enough healthy traffic to dilute the average below
``memory_brain_failure_rate_pct``. Task #483 already exposes the
per-worker breakdown via ``memory_brain_metrics.get_fleet_workers()``;
this test pins the new alerting behaviour:

  * The ``_alerting_loop`` body iterates the per-worker breakdown
    and pages ``memory_brain_worker_failure_rate:<pid>`` for each
    worker that crosses the threshold + min-sample gate, even
    when the fleet aggregate stays well below the threshold.
  * The offending pid is included in the alert key (so the
    cooldown is per-worker) AND in the alert payload's
    ``threshold_snapshot`` (so on-call knows which worker to
    recycle).
  * Healthy workers in the same fleet do NOT page.
  * A worker that hasn't accumulated enough samples yet does NOT
    page (the min-sample gate also applies per worker).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from tests._deps_stub import install_deps_stub  # noqa: E402

install_deps_stub()

import metrics  # noqa: E402
import memory_brain_metrics as mbm  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_state():
    """Each test starts with no in-memory cooldown for the per-pid
    alert keys we exercise so cooldown bleed-through can't mask a
    regression."""
    mbm.reset()
    for pid in (42, 7, 99, 100, 101):
        metrics._alert_last_fired.pop(
            f"memory_brain_worker_failure_rate:{pid}", None
        )
    yield
    mbm.reset()
    for pid in (42, 7, 99, 100, 101):
        metrics._alert_last_fired.pop(
            f"memory_brain_worker_failure_rate:{pid}", None
        )


def _stub_workers(rows):
    """Return a patcher that makes ``get_fleet_workers`` return ``rows``."""
    return patch.object(mbm, "get_fleet_workers", return_value=rows)


def test_one_bad_worker_pages_even_when_fleet_aggregate_is_healthy():
    """The whole point of this task: pid 42 is at 100% failure but
    the fleet aggregate sits below the threshold because pids 7 and
    99 are carrying healthy traffic. Section 6b would stay silent;
    section 6d (this test) MUST page on pid 42 with the pid in
    both the alert key and the payload snapshot."""
    metrics._ALERT_THRESHOLDS["memory_brain_failure_rate_pct"] = 25.0
    metrics._ALERT_THRESHOLDS["memory_brain_failure_min_sample"] = 20

    workers = [
        # Bad worker — 100% failure, well above min sample.
        {"pid": 42, "writes_ok": 0, "writes_fail": 30,
         "reads_ok": 0, "reads_fail": 20,
         "total": 50, "failures": 50, "failure_rate_pct": 100.0,
         "last_ok_ts": None, "last_fail_ts": 1.7e9},
        # Healthy workers diluting the average.
        {"pid": 7, "writes_ok": 200, "writes_fail": 1,
         "reads_ok": 150, "reads_fail": 0,
         "total": 351, "failures": 1, "failure_rate_pct": 0.28,
         "last_ok_ts": 1.7e9, "last_fail_ts": 1.7e9},
        {"pid": 99, "writes_ok": 180, "writes_fail": 0,
         "reads_ok": 140, "reads_fail": 0,
         "total": 320, "failures": 0, "failure_rate_pct": 0.0,
         "last_ok_ts": 1.7e9, "last_fail_ts": None},
    ]

    captured = []

    async def _fake_dispatch(alert_type, title, body, threshold_snapshot=None, **kw):
        captured.append((alert_type, title, body, threshold_snapshot))
        return {}

    sleep_calls = {"n": 0}

    async def _fast_sleep(_):
        sleep_calls["n"] += 1
        if sleep_calls["n"] >= 2:
            raise asyncio.CancelledError()

    with _stub_workers(workers), \
         patch.object(metrics, "_dispatch_alert", side_effect=_fake_dispatch), \
         patch.object(metrics, "_load_alert_settings", AsyncMock(return_value=None)), \
         patch.object(metrics, "_auto_expire_alerts", AsyncMock(return_value=None)), \
         patch.object(metrics.asyncio, "sleep", side_effect=_fast_sleep):
        loop = asyncio.new_event_loop()
        with pytest.raises(asyncio.CancelledError):
            loop.run_until_complete(metrics._alerting_loop())

    worker_alerts = [c for c in captured if c[0].startswith(
        "memory_brain_worker_failure_rate:"
    )]
    assert worker_alerts, (
        f"expected per-worker alert for pid 42, got dispatched: "
        f"{[c[0] for c in captured]}"
    )
    # Exactly one bad worker — exactly one per-worker alert.
    assert len(worker_alerts) == 1
    alert_type, _title, body, snap = worker_alerts[0]
    assert alert_type == "memory_brain_worker_failure_rate:42"
    assert "pid=42" in body
    assert snap["worker_pid"] == 42
    assert snap["actual"] == 100.0
    assert snap["sample"] == 50
    assert snap["value"] == 25.0
    assert snap["metric"] == "memory_brain_worker_failure_rate_pct"


def test_healthy_workers_do_not_page():
    """All workers below the threshold => no per-worker alert."""
    metrics._ALERT_THRESHOLDS["memory_brain_failure_rate_pct"] = 25.0
    metrics._ALERT_THRESHOLDS["memory_brain_failure_min_sample"] = 20

    workers = [
        {"pid": 7, "writes_ok": 200, "writes_fail": 1,
         "reads_ok": 150, "reads_fail": 0,
         "total": 351, "failures": 1, "failure_rate_pct": 0.28,
         "last_ok_ts": 1.7e9, "last_fail_ts": 1.7e9},
        {"pid": 99, "writes_ok": 180, "writes_fail": 0,
         "reads_ok": 140, "reads_fail": 0,
         "total": 320, "failures": 0, "failure_rate_pct": 0.0,
         "last_ok_ts": 1.7e9, "last_fail_ts": None},
    ]

    captured = []

    async def _fake_dispatch(alert_type, *a, **kw):
        captured.append(alert_type)
        return {}

    sleep_calls = {"n": 0}

    async def _fast_sleep(_):
        sleep_calls["n"] += 1
        if sleep_calls["n"] >= 2:
            raise asyncio.CancelledError()

    with _stub_workers(workers), \
         patch.object(metrics, "_dispatch_alert", side_effect=_fake_dispatch), \
         patch.object(metrics, "_load_alert_settings", AsyncMock(return_value=None)), \
         patch.object(metrics, "_auto_expire_alerts", AsyncMock(return_value=None)), \
         patch.object(metrics.asyncio, "sleep", side_effect=_fast_sleep):
        loop = asyncio.new_event_loop()
        with pytest.raises(asyncio.CancelledError):
            loop.run_until_complete(metrics._alerting_loop())

    assert not [c for c in captured if c.startswith(
        "memory_brain_worker_failure_rate:"
    )], f"healthy fleet must not page, got: {captured}"


def test_low_sample_does_not_page_per_worker():
    """A worker at 100% failure but with only a handful of ops in
    the window must NOT page — bursty hot-path traffic plus a
    momentary blip would otherwise wake on-call constantly. The
    same min-sample gate that protects section 6b applies here."""
    metrics._ALERT_THRESHOLDS["memory_brain_failure_rate_pct"] = 25.0
    metrics._ALERT_THRESHOLDS["memory_brain_failure_min_sample"] = 20

    workers = [
        # Below the 20-sample gate even though the rate is 100%.
        {"pid": 100, "writes_ok": 0, "writes_fail": 3,
         "reads_ok": 0, "reads_fail": 2,
         "total": 5, "failures": 5, "failure_rate_pct": 100.0,
         "last_ok_ts": None, "last_fail_ts": 1.7e9},
    ]

    captured = []

    async def _fake_dispatch(alert_type, *a, **kw):
        captured.append(alert_type)
        return {}

    sleep_calls = {"n": 0}

    async def _fast_sleep(_):
        sleep_calls["n"] += 1
        if sleep_calls["n"] >= 2:
            raise asyncio.CancelledError()

    with _stub_workers(workers), \
         patch.object(metrics, "_dispatch_alert", side_effect=_fake_dispatch), \
         patch.object(metrics, "_load_alert_settings", AsyncMock(return_value=None)), \
         patch.object(metrics, "_auto_expire_alerts", AsyncMock(return_value=None)), \
         patch.object(metrics.asyncio, "sleep", side_effect=_fast_sleep):
        loop = asyncio.new_event_loop()
        with pytest.raises(asyncio.CancelledError):
            loop.run_until_complete(metrics._alerting_loop())

    assert not [c for c in captured if c.startswith(
        "memory_brain_worker_failure_rate:"
    )], f"sub-min-sample worker must not page, got: {captured}"


def test_two_bad_workers_each_page_independently():
    """Per-pid alert key means a second bad worker still pages even
    if pid 42 already paged this tick. Without per-pid cooldown
    scoping, the shared alert type would suppress the second
    worker for 30 minutes."""
    metrics._ALERT_THRESHOLDS["memory_brain_failure_rate_pct"] = 25.0
    metrics._ALERT_THRESHOLDS["memory_brain_failure_min_sample"] = 20

    workers = [
        {"pid": 42, "writes_ok": 0, "writes_fail": 30,
         "reads_ok": 0, "reads_fail": 20,
         "total": 50, "failures": 50, "failure_rate_pct": 100.0,
         "last_ok_ts": None, "last_fail_ts": 1.7e9},
        {"pid": 101, "writes_ok": 5, "writes_fail": 25,
         "reads_ok": 5, "reads_fail": 15,
         "total": 50, "failures": 40, "failure_rate_pct": 80.0,
         "last_ok_ts": 1.7e9, "last_fail_ts": 1.7e9},
    ]

    captured = []

    async def _fake_dispatch(alert_type, *a, **kw):
        captured.append(alert_type)
        return {}

    sleep_calls = {"n": 0}

    async def _fast_sleep(_):
        sleep_calls["n"] += 1
        if sleep_calls["n"] >= 2:
            raise asyncio.CancelledError()

    with _stub_workers(workers), \
         patch.object(metrics, "_dispatch_alert", side_effect=_fake_dispatch), \
         patch.object(metrics, "_load_alert_settings", AsyncMock(return_value=None)), \
         patch.object(metrics, "_auto_expire_alerts", AsyncMock(return_value=None)), \
         patch.object(metrics.asyncio, "sleep", side_effect=_fast_sleep):
        loop = asyncio.new_event_loop()
        with pytest.raises(asyncio.CancelledError):
            loop.run_until_complete(metrics._alerting_loop())

    assert "memory_brain_worker_failure_rate:42" in captured
    assert "memory_brain_worker_failure_rate:101" in captured
