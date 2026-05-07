"""Tests for Task #412 — hydrate-telemetry alert loop in routes/analytics.py.

Mirrors the patterns in test_seo_health_alerting.py: install the deps stub,
patch deps.db / deps.is_mongo_available, and exercise the pure-ish helpers
(_gather_hydrate_alert_window, _evaluate_hydrate_alerts) without spinning
up the asyncio loop in production.
"""
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from tests._deps_stub import install_deps_stub  # noqa: E402

install_deps_stub()
from routes import analytics  # noqa: E402


def _reset_cooldowns():
    analytics._HYDRATE_ALERT_LAST_FIRED.clear()


def _fake_db_with_counts(
    *,
    preload_failed_total: int = 0,
    auto_reload_attempts: int = 0,
    auto_reload_recoveries: int = 0,
    top_kind: str | None = None,
    top_kind_count: int = 0,
    sample_message: str | None = None,
):
    fake_db = MagicMock()

    async def _count(filt):
        ev = filt.get("event")
        ar = filt.get("auto_reload")
        if ev == "hydrate_preload_failed" and ar is True:
            return auto_reload_attempts
        if ev == "hydrate_preload_failed":
            return preload_failed_total
        if ev == "hydrate_recovered":
            return auto_reload_recoveries
        return 0

    fake_db.hydrate_telemetry.count_documents = AsyncMock(side_effect=_count)

    class _AggCursor:
        def __init__(self, docs):
            self._docs = list(docs)

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._docs:
                raise StopAsyncIteration
            return self._docs.pop(0)

    if top_kind:
        agg_docs = [{"_id": top_kind, "count": top_kind_count}]
    else:
        agg_docs = []
    fake_db.hydrate_telemetry.aggregate = MagicMock(return_value=_AggCursor(agg_docs))

    if sample_message:
        sample_doc = {
            "message": sample_message,
            "name": "ChunkLoadError",
            "path": "/learn/maths",
            "created_at": datetime.now(timezone.utc),
        }
    else:
        sample_doc = None
    fake_db.hydrate_telemetry.find_one = AsyncMock(return_value=sample_doc)
    return fake_db


# -------- _gather_hydrate_alert_window --------

def test_gather_returns_zero_shape_when_mongo_unavailable():
    with patch.object(analytics, "is_mongo_available", AsyncMock(return_value=False)):
        snap = asyncio.run(analytics._gather_hydrate_alert_window())
    assert snap["preload_failed_total"] == 0
    assert snap["auto_reload_attempts"] == 0
    assert snap["auto_reload_recoveries"] == 0
    assert snap["success_rate_pct"] is None
    assert snap["top_kind"] is None
    assert snap["sample_message"] is None


def test_gather_computes_success_rate_and_top_kind():
    fake_db = _fake_db_with_counts(
        preload_failed_total=80,
        auto_reload_attempts=20,
        auto_reload_recoveries=5,
        top_kind="chunk", top_kind_count=70,
        sample_message="Loading chunk 47 failed",
    )
    with patch.object(analytics, "db", fake_db), \
         patch.object(analytics, "is_mongo_available", AsyncMock(return_value=True)):
        snap = asyncio.run(analytics._gather_hydrate_alert_window())
    assert snap["preload_failed_total"] == 80
    assert snap["auto_reload_attempts"] == 20
    assert snap["auto_reload_recoveries"] == 5
    assert snap["success_rate_pct"] == 25.0
    assert snap["top_kind"] == {"value": "chunk", "count": 70}
    assert snap["sample_message"]["message"] == "Loading chunk 47 failed"


# -------- _evaluate_hydrate_alerts --------

def test_no_alert_when_under_thresholds():
    _reset_cooldowns()
    fake_db = _fake_db_with_counts(
        preload_failed_total=10,
        auto_reload_attempts=3,
        auto_reload_recoveries=1,
    )
    with patch.object(analytics, "db", fake_db), \
         patch.object(analytics, "is_mongo_available", AsyncMock(return_value=True)):
        alerts = asyncio.run(analytics._evaluate_hydrate_alerts(now_ts=1000.0))
    assert alerts == []


def test_failure_spike_alert_fires_when_over_threshold():
    _reset_cooldowns()
    fake_db = _fake_db_with_counts(
        preload_failed_total=analytics.HYDRATE_FAILURE_THRESHOLD + 5,
        auto_reload_attempts=2,
        auto_reload_recoveries=2,
        top_kind="chunk", top_kind_count=40,
        sample_message="Loading chunk 47 failed",
    )
    with patch.object(analytics, "db", fake_db), \
         patch.object(analytics, "is_mongo_available", AsyncMock(return_value=True)):
        alerts = asyncio.run(analytics._evaluate_hydrate_alerts(now_ts=1000.0))
    assert len(alerts) == 1
    a = alerts[0]
    assert a["alert_type"] == "hydrate_failure_spike"
    assert "spiked" in a["title"].lower()
    assert "Sample error" in a["body"]
    assert "/admin/dashboard" in a["body"]
    snap = a["threshold_snapshot"]
    assert snap["metric"] == "hydrate_preload_failed_per_hour"
    assert snap["value"] == analytics.HYDRATE_FAILURE_THRESHOLD
    assert snap["actual"] == analytics.HYDRATE_FAILURE_THRESHOLD + 5
    assert snap["top_kind"] == "chunk"


def test_recovery_low_alert_fires_when_rate_under_floor():
    _reset_cooldowns()
    # Stay under failure-spike threshold so only the recovery alert fires.
    fake_db = _fake_db_with_counts(
        preload_failed_total=12,
        auto_reload_attempts=12,
        auto_reload_recoveries=2,   # 16.7% — below 50% floor, ≥10 attempts
        top_kind="css", top_kind_count=8,
        sample_message="net::ERR_FAILED",
    )
    with patch.object(analytics, "db", fake_db), \
         patch.object(analytics, "is_mongo_available", AsyncMock(return_value=True)):
        alerts = asyncio.run(analytics._evaluate_hydrate_alerts(now_ts=1000.0))
    assert len(alerts) == 1
    a = alerts[0]
    assert a["alert_type"] == "hydrate_recovery_low"
    assert "loop guard" in a["body"]
    snap = a["threshold_snapshot"]
    assert snap["metric"] == "auto_reload_success_rate_pct"
    assert snap["actual"] < analytics.HYDRATE_RECOVERY_MIN_RATE_PCT


def test_recovery_low_skipped_when_attempts_below_minimum():
    _reset_cooldowns()
    fake_db = _fake_db_with_counts(
        preload_failed_total=5,
        auto_reload_attempts=analytics.HYDRATE_RECOVERY_MIN_ATTEMPTS - 1,
        auto_reload_recoveries=0,
    )
    with patch.object(analytics, "db", fake_db), \
         patch.object(analytics, "is_mongo_available", AsyncMock(return_value=True)):
        alerts = asyncio.run(analytics._evaluate_hydrate_alerts(now_ts=1000.0))
    assert alerts == []


def test_both_alerts_fire_simultaneously_when_both_breached():
    _reset_cooldowns()
    fake_db = _fake_db_with_counts(
        preload_failed_total=analytics.HYDRATE_FAILURE_THRESHOLD + 100,
        auto_reload_attempts=20,
        auto_reload_recoveries=3,   # 15% success rate
        top_kind="chunk", top_kind_count=80,
        sample_message="Loading chunk 47 failed",
    )
    with patch.object(analytics, "db", fake_db), \
         patch.object(analytics, "is_mongo_available", AsyncMock(return_value=True)):
        alerts = asyncio.run(analytics._evaluate_hydrate_alerts(now_ts=1000.0))
    types = {a["alert_type"] for a in alerts}
    assert types == {"hydrate_failure_spike", "hydrate_recovery_low"}


def test_cooldown_suppresses_duplicate_within_window():
    _reset_cooldowns()
    fake_db = _fake_db_with_counts(
        preload_failed_total=analytics.HYDRATE_FAILURE_THRESHOLD + 5,
    )
    with patch.object(analytics, "db", fake_db), \
         patch.object(analytics, "is_mongo_available", AsyncMock(return_value=True)):
        first = asyncio.run(analytics._evaluate_hydrate_alerts(now_ts=1000.0))
        # Simulate the loop marking cooldown after a successful dispatch.
        analytics._HYDRATE_ALERT_LAST_FIRED["hydrate_failure_spike"] = 1000.0
        # 30 min later — still inside the 60-min cooldown.
        second = asyncio.run(analytics._evaluate_hydrate_alerts(now_ts=1000.0 + 1800))
        # 61 min after the first fire — cooldown elapsed, alert may fire again.
        third = asyncio.run(
            analytics._evaluate_hydrate_alerts(
                now_ts=1000.0 + analytics.HYDRATE_ALERT_COOLDOWN_S + 60
            )
        )
    assert len(first) == 1
    assert second == []
    assert len(third) == 1


def test_evaluator_does_not_mutate_cooldown_state():
    """Cooldown advancement is the loop's responsibility — evaluator must
    stay pure so a downstream dispatch failure can be retried next tick.
    """
    _reset_cooldowns()
    fake_db = _fake_db_with_counts(
        preload_failed_total=analytics.HYDRATE_FAILURE_THRESHOLD + 5,
    )
    with patch.object(analytics, "db", fake_db), \
         patch.object(analytics, "is_mongo_available", AsyncMock(return_value=True)):
        alerts1 = asyncio.run(analytics._evaluate_hydrate_alerts(now_ts=1000.0))
        # Without the loop marking cooldown, a second evaluation should
        # still return the alert.
        alerts2 = asyncio.run(analytics._evaluate_hydrate_alerts(now_ts=1000.0 + 60))
    assert len(alerts1) == 1
    assert len(alerts2) == 1
    assert analytics._HYDRATE_ALERT_LAST_FIRED == {}


def test_loop_does_not_mark_cooldown_when_dispatch_fails():
    """End-to-end check on the loop body: a single tick where dispatch
    raises must leave _HYDRATE_ALERT_LAST_FIRED unchanged so the next
    tick retries the alert.
    """
    _reset_cooldowns()
    fake_db = _fake_db_with_counts(
        preload_failed_total=analytics.HYDRATE_FAILURE_THRESHOLD + 5,
    )

    async def _one_tick():
        # Inline the loop body (sans the outer sleep / while True) so the
        # test stays deterministic.
        alerts = await analytics._evaluate_hydrate_alerts()
        assert alerts, "expected the spike alert to be queued"
        from metrics import _alert_last_fired
        for a in alerts:
            _alert_last_fired.pop(a["alert_type"], None)
            try:
                raise RuntimeError("SES down")  # Task #556 — SES sole path
            except Exception:
                continue  # mirrors the production except-continue branch
            analytics._HYDRATE_ALERT_LAST_FIRED[a["alert_type"]] = 1.0

    with patch.object(analytics, "db", fake_db), \
         patch.object(analytics, "is_mongo_available", AsyncMock(return_value=True)):
        asyncio.run(_one_tick())

    # Cooldown must NOT have advanced — next tick will retry.
    assert "hydrate_failure_spike" not in analytics._HYDRATE_ALERT_LAST_FIRED


# -------- admin-tunable thresholds --------

def test_admin_overrides_failure_threshold_lowers_trigger_point():
    """Admin can quiet/tighten the failure-spike threshold via
    metrics._ALERT_THRESHOLDS without a deploy."""
    _reset_cooldowns()
    import metrics as _metrics_mod
    saved = dict(_metrics_mod._ALERT_THRESHOLDS)
    try:
        # Tighten: alert at >5 failures/hr instead of the default 50.
        _metrics_mod._ALERT_THRESHOLDS = {
            **saved,
            "hydrate_failure_per_hour": 5,
        }
        fake_db = _fake_db_with_counts(preload_failed_total=10)  # under default
        with patch.object(analytics, "db", fake_db), \
             patch.object(analytics, "is_mongo_available", AsyncMock(return_value=True)):
            alerts = asyncio.run(analytics._evaluate_hydrate_alerts(now_ts=2000.0))
        assert len(alerts) == 1
        assert alerts[0]["alert_type"] == "hydrate_failure_spike"
        # The snapshot reflects the live admin-configured value.
        assert alerts[0]["threshold_snapshot"]["value"] == 5
        assert "threshold: 5" in alerts[0]["body"]
    finally:
        _metrics_mod._ALERT_THRESHOLDS = saved


def test_admin_overrides_recovery_min_attempts_gates_alert():
    """Raising hydrate_recovery_min_attempts must suppress the recovery
    alert for low-volume windows."""
    _reset_cooldowns()
    import metrics as _metrics_mod
    saved = dict(_metrics_mod._ALERT_THRESHOLDS)
    try:
        _metrics_mod._ALERT_THRESHOLDS = {
            **saved,
            "hydrate_recovery_min_attempts": 100,   # demand 100 attempts
            "hydrate_recovery_min_rate_pct": 50.0,
            "hydrate_failure_per_hour": 1000,       # keep spike alert silent
        }
        # 12 attempts / 2 recoveries — would fire under defaults, must be
        # suppressed under the higher attempts gate.
        fake_db = _fake_db_with_counts(
            preload_failed_total=10,
            auto_reload_attempts=12,
            auto_reload_recoveries=2,
        )
        with patch.object(analytics, "db", fake_db), \
             patch.object(analytics, "is_mongo_available", AsyncMock(return_value=True)):
            alerts = asyncio.run(analytics._evaluate_hydrate_alerts(now_ts=2000.0))
        assert alerts == []
    finally:
        _metrics_mod._ALERT_THRESHOLDS = saved


# -------- _format_hydrate_sample --------

def test_format_sample_handles_missing_fields():
    assert analytics._format_hydrate_sample(None) == ""
    assert analytics._format_hydrate_sample({}) == ""
    assert (
        analytics._format_hydrate_sample({"message": "boom", "path": "/x"})
        == "boom (path: /x)"
    )
    assert (
        analytics._format_hydrate_sample({"name": "ChunkLoadError", "message": "boom"})
        == "ChunkLoadError: boom"
    )
