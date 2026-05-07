"""Task #482 — alert when the memory_brain fleet rollup queue is
dropping events.

Task #446 added a non-blocking ``queue.Queue`` between the chat hot
path and the daemon thread that fans events out to Upstash. When
Upstash hangs the queue fills up and the ``queue.Full`` branch in
``record_event`` silently increments ``_fleet_dropped_events``,
quietly undercounting the admin dashboard. Today the count only
shows up in the admin response under ``fleet_stats.dropped_events_local``;
on-call only notices if they happen to look. This test pins the
new alerting behaviour:

  * ``get_fleet_dropped_events`` exposes the per-worker counter.
  * The ``_alerting_loop`` body fires
    ``memory_brain_fleet_dropped`` when the *delta* of dropped
    events since the previous tick crosses
    ``memory_brain_fleet_dropped_min``.
  * A subsequent tick with no new drops does NOT re-page on the
    same accumulated number (delta-based, not cumulative).
  * The alert payload carries the worker pid and both the
    delta and the cumulative total since boot so on-call can
    identify which gunicorn worker is degraded.
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
    """Each test starts with the drop counter cleared and the
    alerting loop's per-tick high-water mark reset so the delta
    semantics are deterministic."""
    mbm.reset()
    metrics._mb_fleet_dropped_last_seen = 0
    metrics._alert_last_fired.pop("memory_brain_fleet_dropped", None)
    yield
    mbm.reset()
    metrics._mb_fleet_dropped_last_seen = 0
    metrics._alert_last_fired.pop("memory_brain_fleet_dropped", None)


def test_default_threshold_is_well_below_queue_capacity():
    """Runbook promise: page well before the 4096-slot queue saturates
    so on-call can act before the local view diverges meaningfully
    from the fleet aggregate, but high enough that one stray drop
    during a momentary spike doesn't wake anybody up."""
    threshold = metrics._ALERT_THRESHOLDS_DEFAULT["memory_brain_fleet_dropped_min"]
    assert 0 < threshold < mbm._FLEET_QUEUE_MAX
    assert threshold == 10


def test_get_fleet_dropped_events_exposes_counter():
    """The new accessor is what the alerting loop and the admin
    tile read; the per-worker counter is otherwise module-private."""
    assert mbm.get_fleet_dropped_events() == 0
    mbm._fleet_dropped_events = 7
    assert mbm.get_fleet_dropped_events() == 7


def test_alerting_check_fires_on_first_burst_above_threshold():
    """Inline the new check from ``_alerting_loop`` so the test
    isn't coupled to the 60s startup sleep. The payload must
    carry both the delta-since-last-tick and the cumulative
    total-since-boot so on-call can tell a fresh burst from
    long-running degradation."""
    metrics._ALERT_THRESHOLDS["memory_brain_fleet_dropped_min"] = 5
    mbm._fleet_dropped_events = 12

    fired = []

    async def _capture(alert_type, title, body, threshold_snapshot=None, **kw):
        fired.append((alert_type, title, body, threshold_snapshot))
        return {}

    async def _run_one_iteration():
        threshold = int(metrics._ALERT_THRESHOLDS["memory_brain_fleet_dropped_min"])
        current = mbm.get_fleet_dropped_events()
        delta = current - metrics._mb_fleet_dropped_last_seen
        if delta >= threshold:
            await _capture(
                "memory_brain_fleet_dropped",
                "memory_brain fleet rollup queue is dropping events",
                f"delta={delta} total={current} threshold={threshold}",
                {
                    "metric": "memory_brain_fleet_dropped_min",
                    "value": threshold,
                    "actual": delta,
                    "total_since_boot": current,
                },
            )
        metrics._mb_fleet_dropped_last_seen = current

    asyncio.run(_run_one_iteration())
    assert len(fired) == 1
    alert_type, _title, _body, snap = fired[0]
    assert alert_type == "memory_brain_fleet_dropped"
    assert snap["actual"] == 12
    assert snap["total_since_boot"] == 12
    assert snap["value"] == 5
    # High-water mark advanced — the next idle tick must NOT re-page.
    assert metrics._mb_fleet_dropped_last_seen == 12


def test_no_new_drops_does_not_re_page():
    """Once the high-water mark catches up, an idle tick (no new
    drops) must NOT re-fire the alert. Without this, a single
    sustained Upstash stall would page on every 120s tick until
    the worker recycled."""
    metrics._ALERT_THRESHOLDS["memory_brain_fleet_dropped_min"] = 5
    mbm._fleet_dropped_events = 50
    metrics._mb_fleet_dropped_last_seen = 50  # already alerted on this

    fired = []

    async def _capture(alert_type, *a, **kw):
        fired.append(alert_type)
        return {}

    async def _run_one_iteration():
        threshold = int(metrics._ALERT_THRESHOLDS["memory_brain_fleet_dropped_min"])
        current = mbm.get_fleet_dropped_events()
        delta = current - metrics._mb_fleet_dropped_last_seen
        if delta >= threshold:
            await _capture("memory_brain_fleet_dropped")
        metrics._mb_fleet_dropped_last_seen = current

    asyncio.run(_run_one_iteration())
    assert fired == [], "no new drops since last tick must not re-page"


def test_threshold_zero_disables_alert():
    """Operator can mute the alert by setting the threshold to 0."""
    metrics._ALERT_THRESHOLDS["memory_brain_fleet_dropped_min"] = 0
    mbm._fleet_dropped_events = 9999
    fired = []

    async def _capture(*a, **kw):
        fired.append(a[0] if a else None)
        return {}

    async def _run_one_iteration():
        threshold = int(metrics._ALERT_THRESHOLDS.get("memory_brain_fleet_dropped_min", 0) or 0)
        if threshold > 0:
            await _capture("memory_brain_fleet_dropped")

    asyncio.run(_run_one_iteration())
    assert fired == []


def test_alerting_loop_dispatches_via_real_branch():
    """Drive the actual ``_alerting_loop`` body so the test breaks
    loudly if the new check is deleted, moved out of its
    try/except, or accidentally gated behind a different flag."""
    metrics._ALERT_THRESHOLDS["memory_brain_fleet_dropped_min"] = 5
    mbm._fleet_dropped_events = 25
    metrics._mb_fleet_dropped_last_seen = 0

    captured = []

    async def _fake_dispatch(alert_type, *a, **kw):
        captured.append(alert_type)
        return {}

    sleep_calls = {"n": 0}

    async def _fast_sleep(_):
        sleep_calls["n"] += 1
        if sleep_calls["n"] >= 2:
            raise asyncio.CancelledError()

    with patch.object(metrics, "_dispatch_alert", side_effect=_fake_dispatch), \
         patch.object(metrics, "_load_alert_settings", AsyncMock(return_value=None)), \
         patch.object(metrics, "_auto_expire_alerts", AsyncMock(return_value=None)), \
         patch.object(metrics.asyncio, "sleep", side_effect=_fast_sleep):
        loop = asyncio.new_event_loop()
        with pytest.raises(asyncio.CancelledError):
            loop.run_until_complete(metrics._alerting_loop())

    assert "memory_brain_fleet_dropped" in captured, (
        f"expected fleet-dropped alert, got dispatched: {captured}"
    )
    # The loop must also advance the high-water mark so the
    # subsequent tick doesn't re-page.
    assert metrics._mb_fleet_dropped_last_seen == 25


def test_admin_route_exposes_dropped_threshold():
    """The admin tile reads ``alert_threshold.fleet_dropped_min`` to
    decide when to render the "rollup degraded" badge. Without this
    field the badge would either never appear or appear on every
    single dropped event."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.admin_memory_brain_metrics import router
    from auth_deps import get_admin_user

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides = {
        get_admin_user: lambda: {"id": "a", "email": "x@y", "is_admin": True}
    }
    metrics._ALERT_THRESHOLDS["memory_brain_fleet_dropped_min"] = 7

    client = TestClient(app)
    r = client.get("/admin/memory-brain/metrics")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["alert_threshold"]["fleet_dropped_min"] == 7
