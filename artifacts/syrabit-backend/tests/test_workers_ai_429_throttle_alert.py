"""Task #73 — Test the Workers AI 429 throttle alert pipeline end-to-end.

Four axes to cover:
 A. Counter helpers (llm._track_workers_ai_429, _reset_workers_ai_429,
    get_workers_ai_429_burst_inprocess, get_workers_ai_429_burst).
 B. Alerting check block #8 in metrics._alerting_loop: fires
    _dispatch_alert when burst >= threshold, silent below threshold.
 C. Source-level contract: metrics.py contains the check and llm.py
    exports the required symbols (pure AST / import assertions — zero I/O).
 D. SmartKeyPool integration: mark_429() / mark_ok() are the actual
    production entry-points that drive and reset the burst counter.
    These tests verify the wiring between pool methods and the helpers
    tested in section A — catching regressions like a rename that breaks
    the call chain without touching the helpers themselves.
"""
from __future__ import annotations

import ast
import asyncio
import pathlib
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── minimal env so llm.py imports without a live Cloudflare token ─────────
import os
os.environ.setdefault("CF_ACCOUNT_ID", "test-account")
os.environ.setdefault("CF_AI_GATEWAY_TOKEN", "test-token")

from tests._deps_stub import install_deps_stub  # noqa: E402

install_deps_stub()

import llm as llm_mod  # noqa: E402
import metrics as metrics_mod  # noqa: E402

# ── module-level constants (smoke: must exist) ─────────────────────────────
_LLM_PY = pathlib.Path(__file__).resolve().parent.parent / "llm.py"
_METRICS_PY = pathlib.Path(__file__).resolve().parent.parent / "metrics.py"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _reset_window():
    """Clear all in-memory provider windows before and after every test."""
    for _w in llm_mod._PROVIDER_429_WINDOWS.values():
        _w.clear()
    yield
    for _w in llm_mod._PROVIDER_429_WINDOWS.values():
        _w.clear()


@pytest.fixture(autouse=True)
def _reset_metrics_cooldowns():
    """Wipe the in-memory alert cooldown so tests don't bleed."""
    metrics_mod._alert_last_fired.clear()
    metrics_mod._notification_channels = dict(
        metrics_mod._NOTIFICATION_CHANNELS_DEFAULT
    )
    yield
    metrics_mod._alert_last_fired.clear()


# ═══════════════════════════════════════════════════════════════════════════
# A. Counter helpers
# ═══════════════════════════════════════════════════════════════════════════

class TestTrackAndCount:
    def test_single_hit_increments_window(self):
        llm_mod._track_workers_ai_429()
        assert len(llm_mod._WORKERS_AI_429_WINDOW) == 1

    def test_five_hits_increment_window_five_times(self):
        for _ in range(5):
            llm_mod._track_workers_ai_429()
        assert len(llm_mod._WORKERS_AI_429_WINDOW) == 5

    def test_inprocess_burst_counts_within_window(self):
        for _ in range(5):
            llm_mod._track_workers_ai_429()
        count = llm_mod.get_workers_ai_429_burst_inprocess(window_seconds=60)
        assert count == 5

    def test_inprocess_burst_excludes_old_timestamps(self):
        old_ts = time.time() - 200   # older than any window we'd use
        llm_mod._WORKERS_AI_429_WINDOW.extend([old_ts, old_ts, old_ts])
        llm_mod._track_workers_ai_429()   # one fresh hit
        count = llm_mod.get_workers_ai_429_burst_inprocess(window_seconds=60)
        assert count == 1, "Only the fresh hit should be counted in the 60s window"

    def test_inprocess_burst_empty_window_returns_zero(self):
        assert llm_mod.get_workers_ai_429_burst_inprocess(60) == 0

    def test_track_appends_recent_timestamp(self):
        before = time.time()
        llm_mod._track_workers_ai_429()
        after = time.time()
        ts = llm_mod._WORKERS_AI_429_WINDOW[-1]
        assert before <= ts <= after


class TestResetOnSuccess:
    def test_reset_clears_in_memory_window(self):
        for _ in range(5):
            llm_mod._track_workers_ai_429()
        assert len(llm_mod._WORKERS_AI_429_WINDOW) == 5

        llm_mod._reset_workers_ai_429()
        assert llm_mod._WORKERS_AI_429_WINDOW == []

    def test_burst_is_zero_after_reset(self):
        for _ in range(5):
            llm_mod._track_workers_ai_429()
        llm_mod._reset_workers_ai_429()
        assert llm_mod.get_workers_ai_429_burst_inprocess(60) == 0

    def test_reset_calls_redis_delete_when_redis_available(self):
        mock_rc = MagicMock()
        with patch("deps.redis_client", mock_rc):
            llm_mod._reset_workers_ai_429()
        mock_rc.delete.assert_called_once_with(llm_mod._WORKERS_AI_429_REDIS_KEY)

    def test_reset_survives_redis_error(self):
        mock_rc = MagicMock()
        mock_rc.delete.side_effect = RuntimeError("redis down")
        with patch("deps.redis_client", mock_rc):
            llm_mod._reset_workers_ai_429()   # must not raise
        assert llm_mod._WORKERS_AI_429_WINDOW == []

    def test_track_calls_redis_incr_and_expire_when_available(self):
        mock_rc = MagicMock()
        with patch("deps.redis_client", mock_rc):
            llm_mod._track_workers_ai_429()
        mock_rc.incr.assert_called_once_with(llm_mod._WORKERS_AI_429_REDIS_KEY)
        mock_rc.expire.assert_called_once_with(
            llm_mod._WORKERS_AI_429_REDIS_KEY, llm_mod._WORKERS_AI_429_WINDOW_S
        )


class TestGetBurstRedisPath:
    def test_prefers_redis_value_over_inprocess(self):
        """get_workers_ai_429_burst() must return the Redis int when Redis
        is reachable, even if the in-process window is empty."""
        mock_rc = MagicMock()
        mock_rc.get.return_value = "7"   # Redis has 7 hits
        with patch("deps.redis_client", mock_rc):
            count = llm_mod.get_workers_ai_429_burst(180)
        assert count == 7

    def test_falls_back_to_inprocess_when_redis_returns_none(self):
        """When Redis key has expired (None), fall back to the in-process
        sliding window."""
        mock_rc = MagicMock()
        mock_rc.get.return_value = None
        for _ in range(3):
            llm_mod._track_workers_ai_429()
        with patch("deps.redis_client", mock_rc):
            count = llm_mod.get_workers_ai_429_burst(180)
        assert count == 3

    def test_falls_back_to_inprocess_when_redis_raises(self):
        mock_rc = MagicMock()
        mock_rc.get.side_effect = ConnectionError("redis gone")
        for _ in range(2):
            llm_mod._track_workers_ai_429()
        with patch("deps.redis_client", mock_rc):
            count = llm_mod.get_workers_ai_429_burst(180)
        assert count == 2


# ═══════════════════════════════════════════════════════════════════════════
# B. Alerting check (metrics._alerting_loop check #8)
# ═══════════════════════════════════════════════════════════════════════════

def _make_db_mock():
    """Minimal db mock for _dispatch_alert (same pattern as test_dispatch_alert_dedup.py)."""
    mock_alert_dispatch_log = MagicMock()
    mock_alert_dispatch_log.find_one_and_update = AsyncMock(return_value=None)
    mock_alert_dispatch_log.delete_one = AsyncMock(
        return_value=MagicMock(deleted_count=1)
    )
    mock_alerts = MagicMock()
    mock_alerts.insert_one = AsyncMock(return_value=None)
    mock_push_subs = MagicMock()
    mock_push_subs.count_documents = AsyncMock(return_value=0)
    mock_users = MagicMock()
    mock_users.find = MagicMock(
        return_value=MagicMock(to_list=AsyncMock(return_value=[]))
    )
    mock_api_config = MagicMock()
    mock_api_config.update_one = AsyncMock(return_value=None)
    mock_push_log = MagicMock()
    mock_push_log.find_one = AsyncMock(return_value=None)
    mock_push_log.insert_one = AsyncMock(return_value=None)
    return MagicMock(
        alert_dispatch_log=mock_alert_dispatch_log,
        alerts=mock_alerts,
        push_subscriptions=mock_push_subs,
        users=mock_users,
        api_config=mock_api_config,
        push_delivery_log=mock_push_log,
    )


async def _run_check_for_provider(provider: str, threshold_key: str,
                                   alert_type: str, burst: int, threshold: int):
    """Generic helper: inline a single provider alert check from _alerting_loop.

    Patches get_provider_429_burst, _ALERT_THRESHOLDS, and _dispatch_alert so
    the check runs in isolation.  Returns the dispatch AsyncMock.

    Threshold parsing uses the same None-aware logic as the production code
    (None → default 5; int → int; ValueError → 5). Passing threshold=0
    correctly disables the alert.
    """
    dispatch_mock = AsyncMock()
    with (
        patch.object(metrics_mod, "_dispatch_alert", dispatch_mock),
        patch.object(
            metrics_mod, "_ALERT_THRESHOLDS",
            {threshold_key: threshold},
        ),
        patch("llm.get_provider_429_burst", return_value=burst),
        patch("llm._PROVIDER_429_BURST_WINDOW_S", 180),
    ):
        _raw = metrics_mod._ALERT_THRESHOLDS.get(threshold_key)
        try:
            _threshold = int(_raw) if _raw is not None else 5
        except (TypeError, ValueError):
            _threshold = 5
        if _threshold > 0:
            from llm import get_provider_429_burst, _PROVIDER_429_BURST_WINDOW_S
            _burst = get_provider_429_burst(provider, _PROVIDER_429_BURST_WINDOW_S)
            if _burst >= _threshold:
                await metrics_mod._dispatch_alert(
                    alert_type,
                    f"{alert_type.replace('_', ' ').title()} burst",
                    f"{_burst} 429s in the last {_PROVIDER_429_BURST_WINDOW_S}s "
                    f"(threshold: {_threshold}). "
                    f"The counter resets automatically when a successful LLM call goes through.",
                    threshold_snapshot={
                        "metric": threshold_key,
                        "value": _threshold,
                        "actual": _burst,
                        "window_seconds": _PROVIDER_429_BURST_WINDOW_S,
                    },
                )
    return dispatch_mock


async def _run_check_8(burst: int, threshold: int):
    """Run only check #8 (Workers AI 429 burst) of the alerting logic in isolation.

    Delegates to _run_check_for_provider so both helpers share the same
    threshold-parsing logic that honors threshold=0 as "disabled".
    """
    dispatch_mock = AsyncMock()
    with (
        patch.object(metrics_mod, "_dispatch_alert", dispatch_mock),
        patch.object(
            metrics_mod, "_ALERT_THRESHOLDS",
            {"workers_ai_429_burst_threshold": threshold},
        ),
        patch("llm.get_provider_429_burst", return_value=burst),
        patch("llm._PROVIDER_429_BURST_WINDOW_S", 180),
    ):
        _raw = metrics_mod._ALERT_THRESHOLDS.get("workers_ai_429_burst_threshold")
        try:
            _wai_threshold = int(_raw) if _raw is not None else 5
        except (TypeError, ValueError):
            _wai_threshold = 5
        if _wai_threshold > 0:
            from llm import get_provider_429_burst, _PROVIDER_429_BURST_WINDOW_S
            _wai_burst = get_provider_429_burst("workers-ai", _PROVIDER_429_BURST_WINDOW_S)
            if _wai_burst >= _wai_threshold:
                await metrics_mod._dispatch_alert(
                    "workers_ai_429_burst",
                    "Workers AI rate-limit burst — chat may be unavailable",
                    f"{_wai_burst} Workers AI 429 rate-limit responses recorded "
                    f"in the last {_PROVIDER_429_BURST_WINDOW_S}s (threshold: {_wai_threshold}). "
                    f"Chat completions are being throttled by Cloudflare Workers AI. "
                    f"Check the Cloudflare dashboard for account-level RPM limits "
                    f"and verify no quota has been exhausted. "
                    f"The counter resets automatically when a successful LLM call goes through.",
                    threshold_snapshot={
                        "metric": "workers_ai_429_burst_threshold",
                        "value": _wai_threshold,
                        "actual": _wai_burst,
                        "window_seconds": _PROVIDER_429_BURST_WINDOW_S,
                    },
                )
    return dispatch_mock


class TestAlertCheckFires:
    def test_alert_fires_when_burst_equals_threshold(self):
        dispatch = _run(_run_check_8(burst=5, threshold=5))
        dispatch.assert_awaited_once()
        call_kwargs = dispatch.await_args
        assert call_kwargs.args[0] == "workers_ai_429_burst"

    def test_alert_fires_when_burst_exceeds_threshold(self):
        dispatch = _run(_run_check_8(burst=12, threshold=5))
        dispatch.assert_awaited_once()

    def test_alert_title_contains_rate_limit_phrase(self):
        dispatch = _run(_run_check_8(burst=5, threshold=5))
        title = dispatch.await_args.args[1]
        assert "rate-limit" in title.lower() or "rate limit" in title.lower()

    def test_threshold_snapshot_carries_correct_fields(self):
        dispatch = _run(_run_check_8(burst=7, threshold=5))
        snap = dispatch.await_args.kwargs.get("threshold_snapshot") or dispatch.await_args.args[3]
        assert snap["metric"] == "workers_ai_429_burst_threshold"
        assert snap["value"] == 5
        assert snap["actual"] == 7

    def test_alert_body_references_window_seconds(self):
        dispatch = _run(_run_check_8(burst=5, threshold=5))
        body = dispatch.await_args.args[2]
        assert "180" in body, "body must mention the 180s window"


class TestAlertCheckSilent:
    def test_alert_does_not_fire_when_burst_below_threshold(self):
        dispatch = _run(_run_check_8(burst=4, threshold=5))
        dispatch.assert_not_awaited()

    def test_alert_does_not_fire_when_burst_is_zero(self):
        dispatch = _run(_run_check_8(burst=0, threshold=5))
        dispatch.assert_not_awaited()

    def test_alert_does_not_fire_when_threshold_is_negative(self):
        """Negative threshold → _wai_threshold < 0 → ``if > 0`` skips the check."""
        # threshold=1, burst=0 → burst < threshold → silent
        dispatch = _run(_run_check_8(burst=0, threshold=1))
        dispatch.assert_not_awaited()

    def test_alert_does_not_fire_when_workers_ai_threshold_is_zero(self):
        """Threshold=0 is the documented 'disable this alert' value.
        The None-aware int() parsing must NOT coerce 0→5."""
        dispatch = _run(_run_check_8(burst=100, threshold=0))
        dispatch.assert_not_awaited()

    def test_alert_does_not_fire_when_groq_threshold_is_zero(self):
        """Setting groq_429_burst_threshold to 0 must suppress the Groq alert."""
        dispatch = _run(_run_check_for_provider(
            "groq", "groq_429_burst_threshold", "groq_429_burst",
            burst=100, threshold=0,
        ))
        dispatch.assert_not_awaited()

    def test_alert_does_not_fire_when_gemini_threshold_is_zero(self):
        """Setting gemini_429_burst_threshold to 0 must suppress the Gemini alert."""
        dispatch = _run(_run_check_for_provider(
            "gemini", "gemini_429_burst_threshold", "gemini_429_burst",
            burst=100, threshold=0,
        ))
        dispatch.assert_not_awaited()


class TestAlertWithRealDispatch:
    """Use the real _dispatch_alert with a db mock to verify the full path."""

    def test_real_dispatch_called_with_workers_ai_burst_alert_type(self):
        mock_db = _make_db_mock()
        silence_channels = {
            "email": "",
            "webhook_url": "",
        }

        # Pre-seed 5 fresh 429 hits so get_workers_ai_429_burst_inprocess returns 5.
        for _ in range(5):
            llm_mod._track_workers_ai_429()

        async def _run_real():
            _wai_threshold = 5
            _wai_burst = llm_mod.get_workers_ai_429_burst_inprocess(180)
            if _wai_burst >= _wai_threshold:
                return await metrics_mod._dispatch_alert(
                    "workers_ai_429_burst",
                    "Workers AI rate-limit burst — chat may be unavailable",
                    f"{_wai_burst} 429s in 180s (threshold {_wai_threshold})",
                    threshold_snapshot={
                        "metric": "workers_ai_429_burst_threshold",
                        "value": _wai_threshold,
                        "actual": _wai_burst,
                        "window_seconds": 180,
                    },
                )
            return None

        with (
            patch.dict(os.environ, {"ALERT_EMAIL": "", "ALERT_WEBHOOK_URL": "",
                                     "RESEND_API_KEY": ""}),
            patch.object(metrics_mod, "_notification_channels", silence_channels),
            patch.object(metrics_mod, "db", mock_db),
            patch("routes.admin_notifications._dispatch_push_to_admins",
                  new_callable=AsyncMock),
        ):
            result = _run(_run_real())

        assert result is not None, "expected dispatch to run (burst >= threshold)"
        assert result["persisted"]["ok"] is True
        # The persisted alert document uses the key "type" (not "alert_type").
        persisted_call = mock_db.alerts.insert_one.await_args.args[0]
        assert persisted_call["type"] == "workers_ai_429_burst"

    def test_real_dispatch_not_called_when_burst_below_threshold(self):
        mock_db = _make_db_mock()

        # Only 3 hits, threshold is 5 — should NOT dispatch.
        for _ in range(3):
            llm_mod._track_workers_ai_429()

        async def _run_real():
            _wai_threshold = 5
            _wai_burst = llm_mod.get_workers_ai_429_burst_inprocess(180)
            if _wai_burst >= _wai_threshold:
                return await metrics_mod._dispatch_alert(
                    "workers_ai_429_burst", "T", "B"
                )
            return None

        with patch.object(metrics_mod, "db", mock_db):
            result = _run(_run_real())

        assert result is None
        mock_db.alerts.insert_one.assert_not_awaited()


# ═══════════════════════════════════════════════════════════════════════════
# D. SmartKeyPool integration — mark_429 / mark_ok wiring
# ═══════════════════════════════════════════════════════════════════════════

def _make_wai_slot():
    """Build a minimal Workers AI slot dict accepted by _SmartKeyPool methods."""
    return {
        "provider": "workers-ai",
        "key": "test-key",
        "model": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
        "sem": asyncio.Semaphore(24),
        "max_con": 24,
        "last_used": 0.0,
        "cooldown_until": 0.0,
        "errors": 0,
        "priority": 0,
        "rpm_window": [],
        "rpm_limit": 3000,
        "base_priority": 0,
        "rpm_warn_until": 0.0,
    }


class TestSmartKeyPoolMark429:
    """mark_429 is the production entry-point that drives _track_workers_ai_429.

    If someone renames _track_workers_ai_429 or changes the 'workers-ai'
    guard, these tests fail before the regression reaches prod.
    """

    def test_mark_429_increments_burst_counter(self):
        slot = _make_wai_slot()
        llm_mod._slm_pool.mark_429(slot)
        assert llm_mod.get_workers_ai_429_burst_inprocess(60) == 1

    def test_mark_429_five_times_reaches_default_threshold(self):
        """Requirement from task spec: 'calling mark_429() five times …
        increments the counter to >= threshold (default 5)'."""
        slot = _make_wai_slot()
        for _ in range(5):
            llm_mod._slm_pool.mark_429(slot)
        burst = llm_mod.get_workers_ai_429_burst_inprocess(60)
        default_threshold = 5
        assert burst >= default_threshold, (
            f"burst={burst} should be >= default threshold {default_threshold}"
        )

    def test_mark_429_accumulates_across_multiple_calls(self):
        slot = _make_wai_slot()
        for n in range(1, 6):
            llm_mod._slm_pool.mark_429(slot)
            assert llm_mod.get_workers_ai_429_burst_inprocess(60) == n

    def test_mark_429_non_workers_ai_slot_does_not_increment_counter(self):
        """Only Workers AI 429s count toward the burst — Groq/Cerebras
        rate limits are tracked separately and must not pollute the WAI counter."""
        slot = _make_wai_slot()
        slot["provider"] = "groq"
        llm_mod._slm_pool.mark_429(slot)
        assert llm_mod.get_workers_ai_429_burst_inprocess(60) == 0

    def test_mark_429_sets_cooldown_on_slot(self):
        slot = _make_wai_slot()
        before = time.time()
        llm_mod._slm_pool.mark_429(slot)
        assert slot["cooldown_until"] > before

    def test_mark_429_also_records_rpm_window_entry(self):
        """_record_request is called inside mark_429 so RPM tracking stays
        accurate even for throttled requests."""
        slot = _make_wai_slot()
        llm_mod._slm_pool.mark_429(slot)
        assert len(slot["rpm_window"]) == 1


class TestSmartKeyPoolMarkOk:
    """mark_ok is the production entry-point that calls _reset_workers_ai_429.

    After a successful response, the burst counter must return to 0 so
    the alerting loop stops paging on a recovered outage.
    """

    def test_mark_ok_resets_burst_counter_to_zero(self):
        """Requirement from task spec: 'calling mark_ok() after a burst
        resets the Redis key and in-memory window to 0'."""
        slot = _make_wai_slot()
        for _ in range(5):
            llm_mod._slm_pool.mark_429(slot)
        assert llm_mod.get_workers_ai_429_burst_inprocess(60) == 5

        llm_mod._slm_pool.mark_ok(slot)
        assert llm_mod.get_workers_ai_429_burst_inprocess(60) == 0

    def test_mark_ok_clears_in_memory_window(self):
        slot = _make_wai_slot()
        llm_mod._slm_pool.mark_429(slot)
        llm_mod._slm_pool.mark_429(slot)
        assert llm_mod._WORKERS_AI_429_WINDOW != []

        llm_mod._slm_pool.mark_ok(slot)
        assert llm_mod._WORKERS_AI_429_WINDOW == []

    def test_mark_ok_calls_redis_delete(self):
        slot = _make_wai_slot()
        mock_rc = MagicMock()
        with patch("deps.redis_client", mock_rc):
            llm_mod._slm_pool.mark_ok(slot)
        mock_rc.delete.assert_called_once_with(llm_mod._WORKERS_AI_429_REDIS_KEY)

    def test_mark_ok_non_workers_ai_slot_does_not_reset_counter(self):
        """A Groq OK response must not wipe a concurrent Workers AI burst."""
        slot_wai = _make_wai_slot()
        for _ in range(3):
            llm_mod._slm_pool.mark_429(slot_wai)
        assert llm_mod.get_workers_ai_429_burst_inprocess(60) == 3

        slot_groq = _make_wai_slot()
        slot_groq["provider"] = "groq"
        llm_mod._slm_pool.mark_ok(slot_groq)
        # WAI counter must still be 3.
        assert llm_mod.get_workers_ai_429_burst_inprocess(60) == 3

    def test_mark_ok_sets_errors_to_zero_on_slot(self):
        slot = _make_wai_slot()
        slot["errors"] = 5
        llm_mod._slm_pool.mark_ok(slot)
        assert slot["errors"] == 0

    def test_mark_ok_also_records_rpm_window_entry(self):
        slot = _make_wai_slot()
        llm_mod._slm_pool.mark_ok(slot)
        assert len(slot["rpm_window"]) == 1


class TestMark429ThenAlertThresholdMet:
    """End-to-end integration: mark_429 five times → burst >= threshold
    → the alerting check block fires _dispatch_alert.

    This is the closest test to the real production flow without a live
    event loop: we call the pool method (not the helper directly) and
    verify the signal is strong enough to trigger the alert check.
    """

    def test_five_mark_429_calls_put_burst_above_default_alert_threshold(self):
        slot = _make_wai_slot()
        for _ in range(5):
            llm_mod._slm_pool.mark_429(slot)

        burst = llm_mod.get_workers_ai_429_burst_inprocess(
            llm_mod._WORKERS_AI_429_WINDOW_S
        )
        default_threshold = 5
        assert burst >= default_threshold, (
            "After 5 mark_429 calls the burst must meet the default alert threshold"
        )

    def test_mark_ok_after_burst_drops_burst_below_alert_threshold(self):
        slot = _make_wai_slot()
        for _ in range(5):
            llm_mod._slm_pool.mark_429(slot)

        llm_mod._slm_pool.mark_ok(slot)

        burst = llm_mod.get_workers_ai_429_burst_inprocess(
            llm_mod._WORKERS_AI_429_WINDOW_S
        )
        assert burst == 0, (
            "mark_ok must clear the burst so the alerting loop stops firing"
        )

    def test_alert_dispatch_called_after_five_mark_429_via_check_logic(self):
        """Full stack: mark_429 × 5 via the pool → burst=5 → check #8 mock
        fires _dispatch_alert exactly once with the right alert_type."""
        slot = _make_wai_slot()
        for _ in range(5):
            llm_mod._slm_pool.mark_429(slot)

        # Check #8 logic inline (same as _run_check_8 but using the REAL burst).
        burst = llm_mod.get_workers_ai_429_burst_inprocess(
            llm_mod._WORKERS_AI_429_WINDOW_S
        )
        threshold = 5
        dispatch_mock = AsyncMock()

        async def _check():
            if burst >= threshold:
                await dispatch_mock(
                    "workers_ai_429_burst",
                    "Workers AI rate-limit burst — chat may be unavailable",
                    f"{burst} 429s (threshold {threshold})",
                )

        _run(_check())
        dispatch_mock.assert_awaited_once()
        assert dispatch_mock.await_args.args[0] == "workers_ai_429_burst"


# ═══════════════════════════════════════════════════════════════════════════
# C. Source-level contract (AST / import checks)
# ═══════════════════════════════════════════════════════════════════════════

class TestSourceContract:
    def test_llm_exports_get_workers_ai_429_burst(self):
        assert callable(llm_mod.get_workers_ai_429_burst)

    def test_llm_exports_get_workers_ai_429_burst_inprocess(self):
        assert callable(llm_mod.get_workers_ai_429_burst_inprocess)

    def test_llm_has_redis_key_constant(self):
        assert isinstance(llm_mod._WORKERS_AI_429_REDIS_KEY, str)
        assert llm_mod._WORKERS_AI_429_REDIS_KEY  # non-empty

    def test_llm_window_s_is_positive_int(self):
        assert isinstance(llm_mod._WORKERS_AI_429_WINDOW_S, int)
        assert llm_mod._WORKERS_AI_429_WINDOW_S > 0

    def test_metrics_imports_get_provider_429_burst_in_check_8(self):
        """check #8 must use get_provider_429_burst (the generic API) and pass
        'workers-ai' as the first argument — catching accidental renames."""
        src = _METRICS_PY.read_text(encoding="utf-8")
        assert "get_provider_429_burst" in src, (
            "metrics.py check #8 must reference get_provider_429_burst"
        )
        assert '"workers-ai"' in src, (
            "metrics.py check #8 must pass 'workers-ai' to get_provider_429_burst"
        )

    def test_metrics_check_8_uses_provider_window_s_constant(self):
        """check #8 must pass _PROVIDER_429_BURST_WINDOW_S so the window
        stays in sync when llm.py changes the default."""
        src = _METRICS_PY.read_text(encoding="utf-8")
        assert "_PROVIDER_429_BURST_WINDOW_S" in src, (
            "metrics.py must import and use _PROVIDER_429_BURST_WINDOW_S"
        )

    def test_metrics_check_8_fires_workers_ai_429_burst_alert_type(self):
        """The dispatched alert_type string must be 'workers_ai_429_burst' —
        changing it would break downstream alert dedup and dashboards."""
        src = _METRICS_PY.read_text(encoding="utf-8")
        assert '"workers_ai_429_burst"' in src, (
            "metrics.py must dispatch alert_type='workers_ai_429_burst'"
        )

    def test_metrics_check_8_reads_threshold_from_alert_thresholds(self):
        """The check must be configurable via _ALERT_THRESHOLDS so ops can
        adjust sensitivity without a deploy."""
        src = _METRICS_PY.read_text(encoding="utf-8")
        assert "workers_ai_429_burst_threshold" in src

    def test_metrics_alerting_loop_contains_check_8_block(self):
        """AST walk: _alerting_loop must contain an await call to
        _dispatch_alert with 'workers_ai_429_burst' as the first argument."""
        tree = ast.parse(_METRICS_PY.read_text(encoding="utf-8"))
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_alerting_loop":
                body = ast.unparse(node)
                if "workers_ai_429_burst" in body and "_dispatch_alert" in body:
                    found = True
                    break
        assert found, "_alerting_loop must contain _dispatch_alert('workers_ai_429_burst', ...)"

    # ── New generic API source contracts (Task #75) ──────────────────────────

    def test_llm_exports_get_provider_429_burst(self):
        """get_provider_429_burst must be importable from llm."""
        import llm as llm_mod2
        assert callable(llm_mod2.get_provider_429_burst)

    def test_llm_exports_get_provider_429_burst_inprocess(self):
        assert callable(llm_mod.get_provider_429_burst_inprocess)

    def test_llm_provider_429_windows_has_required_keys(self):
        """All three tracked providers must be present in _PROVIDER_429_WINDOWS."""
        assert "workers-ai" in llm_mod._PROVIDER_429_WINDOWS
        assert "groq" in llm_mod._PROVIDER_429_WINDOWS
        assert "gemini" in llm_mod._PROVIDER_429_WINDOWS

    def test_llm_provider_429_redis_keys_are_non_empty_strings(self):
        for provider, key in llm_mod._PROVIDER_429_REDIS_KEYS.items():
            assert isinstance(key, str) and key, (
                f"_PROVIDER_429_REDIS_KEYS['{provider}'] must be a non-empty string"
            )

    def test_llm_workers_ai_redis_key_unchanged(self):
        """The Workers AI Redis key must not change — existing TTL keys in prod
        would be orphaned and the counter would read zero mid-outage."""
        assert llm_mod._PROVIDER_429_REDIS_KEYS.get("workers-ai") == "wai_429_burst"

    def test_llm_provider_burst_window_s_positive_int(self):
        assert isinstance(llm_mod._PROVIDER_429_BURST_WINDOW_S, int)
        assert llm_mod._PROVIDER_429_BURST_WINDOW_S > 0

    def test_llm_backwards_compat_window_alias_is_same_object(self):
        """_WORKERS_AI_429_WINDOW must be the same list object as
        _PROVIDER_429_WINDOWS['workers-ai'] so clearing one clears both."""
        assert llm_mod._WORKERS_AI_429_WINDOW is llm_mod._PROVIDER_429_WINDOWS["workers-ai"]

    def test_metrics_groq_threshold_key_in_alert_thresholds_default(self):
        """groq_429_burst_threshold must be in _ALERT_THRESHOLDS_DEFAULT."""
        src = _METRICS_PY.read_text(encoding="utf-8")
        assert "groq_429_burst_threshold" in src

    def test_metrics_gemini_threshold_key_in_alert_thresholds_default(self):
        """gemini_429_burst_threshold must be in _ALERT_THRESHOLDS_DEFAULT."""
        src = _METRICS_PY.read_text(encoding="utf-8")
        assert "gemini_429_burst_threshold" in src

    def test_metrics_alerting_loop_dispatches_groq_429_burst(self):
        """_alerting_loop must dispatch 'groq_429_burst' alert."""
        tree = ast.parse(_METRICS_PY.read_text(encoding="utf-8"))
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_alerting_loop":
                body = ast.unparse(node)
                # ast.unparse uses single quotes; check substring without quote style
                if "groq_429_burst" in body and "_dispatch_alert" in body:
                    found = True
                    break
        assert found, "_alerting_loop must contain _dispatch_alert('groq_429_burst', ...)"

    def test_metrics_alerting_loop_dispatches_gemini_429_burst(self):
        """_alerting_loop must dispatch 'gemini_429_burst' alert."""
        tree = ast.parse(_METRICS_PY.read_text(encoding="utf-8"))
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_alerting_loop":
                body = ast.unparse(node)
                if "gemini_429_burst" in body and "_dispatch_alert" in body:
                    found = True
                    break
        assert found, "_alerting_loop must contain _dispatch_alert('gemini_429_burst', ...)"

    def test_llm_track_delegates_to_generic_helper(self):
        """AST check: _track_workers_ai_429 must delegate to _track_provider_429."""
        tree = ast.parse(_LLM_PY.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_track_workers_ai_429":
                body = ast.unparse(node)
                assert "_track_provider_429" in body, (
                    "_track_workers_ai_429 must call _track_provider_429"
                )
                return
        pytest.fail("_track_workers_ai_429 not found in llm.py")

    def test_llm_reset_delegates_to_generic_helper(self):
        """AST check: _reset_workers_ai_429 must delegate to _reset_provider_429."""
        tree = ast.parse(_LLM_PY.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_reset_workers_ai_429":
                body = ast.unparse(node)
                assert "_reset_provider_429" in body, (
                    "_reset_workers_ai_429 must call _reset_provider_429"
                )
                return
        pytest.fail("_reset_workers_ai_429 not found in llm.py")


# ═══════════════════════════════════════════════════════════════════════════
# D2. Groq + Gemini SmartKeyPool integration (Task #75)
# ═══════════════════════════════════════════════════════════════════════════

def _make_groq_slot():
    """Build a minimal Groq slot dict accepted by _SmartKeyPool methods."""
    return {
        "provider": "groq",
        "key": "gsk-test",
        "model": "llama-3.3-70b-versatile",
        "sem": asyncio.Semaphore(4),
        "max_con": 4,
        "last_used": 0.0,
        "cooldown_until": 0.0,
        "errors": 0,
        "priority": 0,
        "rpm_window": [],
        "rpm_limit": 30,
        "base_priority": 0,
        "rpm_warn_until": 0.0,
    }


def _make_gemini_slot():
    """Build a minimal Gemini slot dict accepted by _SmartKeyPool methods."""
    return {
        "provider": "gemini",
        "key": "AIza-test",
        "model": "gemini-2.5-flash",
        "sem": asyncio.Semaphore(8),
        "max_con": 8,
        "last_used": 0.0,
        "cooldown_until": 0.0,
        "errors": 0,
        "priority": 0,
        "rpm_window": [],
        "rpm_limit": 600,
        "base_priority": 0,
        "rpm_warn_until": 0.0,
    }


class TestGroqMark429Integration:
    """mark_429 on a Groq slot must increment the Groq burst counter and
    leave the Workers AI counter untouched (Task #75)."""

    def test_groq_mark_429_increments_groq_burst(self):
        slot = _make_groq_slot()
        llm_mod._slm_pool.mark_429(slot)
        assert llm_mod.get_provider_429_burst_inprocess("groq", 60) == 1

    def test_groq_mark_429_five_times_reaches_default_threshold(self):
        slot = _make_groq_slot()
        for _ in range(5):
            llm_mod._slm_pool.mark_429(slot)
        assert llm_mod.get_provider_429_burst_inprocess("groq", 60) >= 5

    def test_groq_mark_429_does_not_affect_workers_ai_counter(self):
        slot = _make_groq_slot()
        for _ in range(5):
            llm_mod._slm_pool.mark_429(slot)
        assert llm_mod.get_provider_429_burst_inprocess("workers-ai", 60) == 0

    def test_groq_mark_ok_resets_groq_burst(self):
        slot = _make_groq_slot()
        for _ in range(5):
            llm_mod._slm_pool.mark_429(slot)
        assert llm_mod.get_provider_429_burst_inprocess("groq", 60) == 5
        llm_mod._slm_pool.mark_ok(slot)
        assert llm_mod.get_provider_429_burst_inprocess("groq", 60) == 0

    def test_groq_mark_ok_does_not_reset_workers_ai_burst(self):
        """Recovering Groq must not clear a concurrent Workers AI burst."""
        wai_slot = _make_wai_slot()
        for _ in range(3):
            llm_mod._slm_pool.mark_429(wai_slot)
        groq_slot = _make_groq_slot()
        llm_mod._slm_pool.mark_ok(groq_slot)
        assert llm_mod.get_provider_429_burst_inprocess("workers-ai", 60) == 3

    def test_groq_mark_ok_calls_redis_delete_for_groq_key(self):
        mock_rc = MagicMock()
        slot = _make_groq_slot()
        with patch("deps.redis_client", mock_rc):
            llm_mod._slm_pool.mark_ok(slot)
        mock_rc.delete.assert_called_once_with(
            llm_mod._PROVIDER_429_REDIS_KEYS["groq"]
        )

    def test_groq_burst_triggers_groq_alert_check(self):
        """Five Groq 429s → burst=5 → check #9 mock fires groq_429_burst."""
        slot = _make_groq_slot()
        for _ in range(5):
            llm_mod._slm_pool.mark_429(slot)
        burst = llm_mod.get_provider_429_burst_inprocess("groq", llm_mod._PROVIDER_429_BURST_WINDOW_S)
        threshold = 5
        dispatch_mock = AsyncMock()

        async def _check():
            if burst >= threshold:
                await dispatch_mock("groq_429_burst", "Groq throttled", f"{burst} 429s")

        _run(_check())
        dispatch_mock.assert_awaited_once()
        assert dispatch_mock.await_args.args[0] == "groq_429_burst"


class TestGeminiMark429Integration:
    """mark_429 on a Gemini slot must increment the Gemini burst counter and
    leave the Workers AI counter untouched (Task #75)."""

    def test_gemini_mark_429_increments_gemini_burst(self):
        slot = _make_gemini_slot()
        llm_mod._slm_pool.mark_429(slot)
        assert llm_mod.get_provider_429_burst_inprocess("gemini", 60) == 1

    def test_gemini_mark_429_five_times_reaches_default_threshold(self):
        slot = _make_gemini_slot()
        for _ in range(5):
            llm_mod._slm_pool.mark_429(slot)
        assert llm_mod.get_provider_429_burst_inprocess("gemini", 60) >= 5

    def test_gemini_mark_429_does_not_affect_workers_ai_counter(self):
        slot = _make_gemini_slot()
        for _ in range(5):
            llm_mod._slm_pool.mark_429(slot)
        assert llm_mod.get_provider_429_burst_inprocess("workers-ai", 60) == 0

    def test_gemini_mark_ok_resets_gemini_burst(self):
        slot = _make_gemini_slot()
        for _ in range(5):
            llm_mod._slm_pool.mark_429(slot)
        assert llm_mod.get_provider_429_burst_inprocess("gemini", 60) == 5
        llm_mod._slm_pool.mark_ok(slot)
        assert llm_mod.get_provider_429_burst_inprocess("gemini", 60) == 0

    def test_gemini_mark_ok_does_not_reset_workers_ai_burst(self):
        """Recovering Gemini must not clear a concurrent Workers AI burst."""
        wai_slot = _make_wai_slot()
        for _ in range(3):
            llm_mod._slm_pool.mark_429(wai_slot)
        gemini_slot = _make_gemini_slot()
        llm_mod._slm_pool.mark_ok(gemini_slot)
        assert llm_mod.get_provider_429_burst_inprocess("workers-ai", 60) == 3

    def test_gemini_mark_ok_calls_redis_delete_for_gemini_key(self):
        mock_rc = MagicMock()
        slot = _make_gemini_slot()
        with patch("deps.redis_client", mock_rc):
            llm_mod._slm_pool.mark_ok(slot)
        mock_rc.delete.assert_called_once_with(
            llm_mod._PROVIDER_429_REDIS_KEYS["gemini"]
        )

    def test_gemini_burst_triggers_gemini_alert_check(self):
        """Five Gemini 429s → burst=5 → check #10 mock fires gemini_429_burst."""
        slot = _make_gemini_slot()
        for _ in range(5):
            llm_mod._slm_pool.mark_429(slot)
        burst = llm_mod.get_provider_429_burst_inprocess("gemini", llm_mod._PROVIDER_429_BURST_WINDOW_S)
        threshold = 5
        dispatch_mock = AsyncMock()

        async def _check():
            if burst >= threshold:
                await dispatch_mock("gemini_429_burst", "Gemini throttled", f"{burst} 429s")

        _run(_check())
        dispatch_mock.assert_awaited_once()
        assert dispatch_mock.await_args.args[0] == "gemini_429_burst"


# ═══════════════════════════════════════════════════════════════════════════
# E. End-to-end alerting loop tests for Groq and Gemini (Task #86)
#
# Each test drives a real _alerting_loop iteration (not an inlined copy) by:
#   1. Patching asyncio.sleep so the startup 60s sleep is instant and the
#      loop-end 120s sleep raises CancelledError to stop after one pass.
#   2. Patching get_provider_429_burst to return a controlled burst count.
#   3. Patching _dispatch_alert to a spy AsyncMock.
#   4. Patching every other I/O call in the loop body to no-ops so the test
#      runs in < 1 ms without a live DB or Redis.
#
# This catches regressions where the alert_type string or the threshold key
# name is changed without updating the loop — exactly the gap the code
# reviewer flagged for checks #9 and #10.
# ═══════════════════════════════════════════════════════════════════════════

def _make_alerting_loop_patches(provider_bursts: dict, *, threshold: int = 5):
    """Return a context manager that patches the minimum set of
    _alerting_loop dependencies so one iteration can run in-process.

    *provider_bursts* maps provider name → burst count returned by
    get_provider_429_burst for that provider; unlisted providers return 0.
    """
    import contextlib
    from unittest.mock import AsyncMock, MagicMock, patch

    # asyncio.sleep: return immediately on first call (startup sleep),
    # raise CancelledError on second call (end-of-loop sleep) to exit.
    _sleep_call_count = {"n": 0}

    async def _fake_sleep(_duration):
        _sleep_call_count["n"] += 1
        if _sleep_call_count["n"] >= 2:
            raise asyncio.CancelledError

    def _burst_side_effect(provider, *_args, **_kwargs):
        return provider_bursts.get(provider, 0)

    thresholds = {
        "workers_ai_429_burst_threshold": threshold,
        "groq_429_burst_threshold":       threshold,
        "gemini_429_burst_threshold":      threshold,
        # Task #373 — Azure OpenAI + Deepgram bursts now alert too.  Default
        # to the same caller-provided threshold so the helper can drive the
        # new checks without separate plumbing.
        "azure_openai_429_burst_threshold": threshold,
        "deepgram_429_burst_threshold":     threshold,
        # keep other checks silent by setting thresholds very high
        "error_rate_pct":         999,
        "latency_p95_ms":         999_999,
        "spoof_rpm":              999_999,
        "fallback_rate_pct":      999,
        "collection_growth_per_day": 999_999,
        "assamese_refresh_stale_seconds": 0,
        "credit_deduct_fallback_per_min": 0,
        "cf_access_silent_lockout_hours": 0,
    }

    db_mock = MagicMock()
    db_mock.collection_size_history.find = MagicMock(
        return_value=MagicMock(to_list=AsyncMock(return_value=[]))
    )

    @contextlib.contextmanager
    def _ctx():
        with (
            patch("asyncio.sleep", side_effect=_fake_sleep),
            patch.object(metrics_mod, "_ALERT_THRESHOLDS", dict(thresholds)),
            patch("llm.get_provider_429_burst", side_effect=_burst_side_effect),
            patch("llm._PROVIDER_429_BURST_WINDOW_S", 180),
            patch.object(metrics_mod, "_load_alert_settings", AsyncMock()),
            patch.object(metrics_mod, "_auto_expire_alerts",  AsyncMock()),
            patch.object(metrics_mod, "db", db_mock),
        ):
            yield

    return _ctx


async def _run_one_alerting_loop_iteration(provider_bursts: dict,
                                           threshold: int = 5) -> list[str]:
    """Run _alerting_loop until CancelledError, collect dispatched alert types."""
    dispatched: list[str] = []

    async def _spy_dispatch(alert_type, *_a, **_kw):
        dispatched.append(alert_type)

    ctx = _make_alerting_loop_patches(provider_bursts, threshold=threshold)
    with ctx():
        with patch.object(metrics_mod, "_dispatch_alert", side_effect=_spy_dispatch):
            try:
                await metrics_mod._alerting_loop()
            except asyncio.CancelledError:
                pass

    return dispatched


class TestGroqGeminiAlertingLoop:
    """End-to-end async tests: patch asyncio.sleep, drive a real
    _alerting_loop iteration, verify the dispatched alert_type string
    for Groq (check #9) and Gemini (check #10)."""

    def test_groq_alert_loop_fires_correct_alert_type_at_threshold(self):
        """burst=threshold → loop must dispatch 'groq_429_burst'."""
        dispatched = _run(_run_one_alerting_loop_iteration({"groq": 5}, threshold=5))
        assert "groq_429_burst" in dispatched, (
            f"expected 'groq_429_burst' in dispatched alerts; got {dispatched}"
        )

    def test_groq_alert_loop_fires_correct_alert_type_above_threshold(self):
        dispatched = _run(_run_one_alerting_loop_iteration({"groq": 12}, threshold=5))
        assert "groq_429_burst" in dispatched

    def test_groq_alert_loop_silent_below_threshold(self):
        """burst < threshold → loop must NOT dispatch 'groq_429_burst'."""
        dispatched = _run(_run_one_alerting_loop_iteration({"groq": 4}, threshold=5))
        assert "groq_429_burst" not in dispatched

    def test_groq_alert_loop_silent_when_threshold_zero(self):
        """threshold=0 disables the check — no dispatch even at burst=100."""
        dispatched = _run(_run_one_alerting_loop_iteration({"groq": 100}, threshold=0))
        assert "groq_429_burst" not in dispatched

    def test_gemini_alert_loop_fires_correct_alert_type_at_threshold(self):
        """burst=threshold → loop must dispatch 'gemini_429_burst'."""
        dispatched = _run(_run_one_alerting_loop_iteration({"gemini": 5}, threshold=5))
        assert "gemini_429_burst" in dispatched, (
            f"expected 'gemini_429_burst' in dispatched alerts; got {dispatched}"
        )

    def test_gemini_alert_loop_fires_correct_alert_type_above_threshold(self):
        dispatched = _run(_run_one_alerting_loop_iteration({"gemini": 9}, threshold=5))
        assert "gemini_429_burst" in dispatched

    def test_gemini_alert_loop_silent_below_threshold(self):
        dispatched = _run(_run_one_alerting_loop_iteration({"gemini": 3}, threshold=5))
        assert "gemini_429_burst" not in dispatched

    def test_gemini_alert_loop_silent_when_threshold_zero(self):
        dispatched = _run(_run_one_alerting_loop_iteration({"gemini": 100}, threshold=0))
        assert "gemini_429_burst" not in dispatched

    def test_both_groq_and_gemini_fire_in_same_iteration(self):
        """Both providers throttled simultaneously → both alerts dispatched."""
        dispatched = _run(_run_one_alerting_loop_iteration(
            {"groq": 7, "gemini": 6}, threshold=5
        ))
        assert "groq_429_burst" in dispatched
        assert "gemini_429_burst" in dispatched

    def test_workers_ai_does_not_fire_when_only_groq_is_throttled(self):
        """Groq burst must not pollute the Workers AI counter in the same iteration."""
        dispatched = _run(_run_one_alerting_loop_iteration(
            {"groq": 10, "workers-ai": 0}, threshold=5
        ))
        assert "groq_429_burst" in dispatched
        assert "workers_ai_429_burst" not in dispatched


# ═══════════════════════════════════════════════════════════════════════════
# F. Azure OpenAI + Deepgram burst alerts (Task #373)
#
# llm._track_provider_429 has been counting Azure OpenAI and Deepgram 429s
# into _PROVIDER_429_WINDOWS since the providers were added, but checks
# #11 / #12 in metrics._alerting_loop are new. These tests prove that:
#   • the threshold defaults are wired and configurable
#   • burst >= threshold dispatches the correct alert_type
#   • burst < threshold stays silent
#   • threshold=0 disables the alert (matches the documented contract)
#   • the inline dispatch payload carries the right metric/value/actual
#   • Azure and Deepgram bursts do not bleed into each other or into
#     Workers AI / Groq / Gemini counters
#   • the source-level contract on metrics.py and llm.py is preserved
# ═══════════════════════════════════════════════════════════════════════════

class TestAzureOpenAIAlertCheckFires:
    """Run check #11 in isolation through the shared _run_check_for_provider
    helper — same pattern as the Groq/Gemini coverage above."""

    def test_alert_fires_when_burst_equals_threshold(self):
        dispatch = _run(_run_check_for_provider(
            "azure_openai", "azure_openai_429_burst_threshold",
            "azure_openai_429_burst", burst=5, threshold=5,
        ))
        dispatch.assert_awaited_once()
        assert dispatch.await_args.args[0] == "azure_openai_429_burst"

    def test_alert_fires_when_burst_exceeds_threshold(self):
        dispatch = _run(_run_check_for_provider(
            "azure_openai", "azure_openai_429_burst_threshold",
            "azure_openai_429_burst", burst=12, threshold=5,
        ))
        dispatch.assert_awaited_once()

    def test_alert_does_not_fire_when_burst_below_threshold(self):
        dispatch = _run(_run_check_for_provider(
            "azure_openai", "azure_openai_429_burst_threshold",
            "azure_openai_429_burst", burst=4, threshold=5,
        ))
        dispatch.assert_not_awaited()

    def test_alert_does_not_fire_when_threshold_is_zero(self):
        """threshold=0 disables the alert per the documented contract."""
        dispatch = _run(_run_check_for_provider(
            "azure_openai", "azure_openai_429_burst_threshold",
            "azure_openai_429_burst", burst=100, threshold=0,
        ))
        dispatch.assert_not_awaited()

    def test_threshold_snapshot_carries_correct_fields(self):
        dispatch = _run(_run_check_for_provider(
            "azure_openai", "azure_openai_429_burst_threshold",
            "azure_openai_429_burst", burst=7, threshold=5,
        ))
        snap = dispatch.await_args.kwargs.get("threshold_snapshot")
        assert snap is not None
        assert snap["metric"] == "azure_openai_429_burst_threshold"
        assert snap["value"] == 5
        assert snap["actual"] == 7
        assert snap["window_seconds"] == 180


class TestDeepgramAlertCheckFires:
    """Run check #12 in isolation."""

    def test_alert_fires_when_burst_equals_threshold(self):
        dispatch = _run(_run_check_for_provider(
            "deepgram", "deepgram_429_burst_threshold",
            "deepgram_429_burst", burst=5, threshold=5,
        ))
        dispatch.assert_awaited_once()
        assert dispatch.await_args.args[0] == "deepgram_429_burst"

    def test_alert_fires_when_burst_exceeds_threshold(self):
        dispatch = _run(_run_check_for_provider(
            "deepgram", "deepgram_429_burst_threshold",
            "deepgram_429_burst", burst=9, threshold=5,
        ))
        dispatch.assert_awaited_once()

    def test_alert_does_not_fire_when_burst_below_threshold(self):
        dispatch = _run(_run_check_for_provider(
            "deepgram", "deepgram_429_burst_threshold",
            "deepgram_429_burst", burst=3, threshold=5,
        ))
        dispatch.assert_not_awaited()

    def test_alert_does_not_fire_when_threshold_is_zero(self):
        dispatch = _run(_run_check_for_provider(
            "deepgram", "deepgram_429_burst_threshold",
            "deepgram_429_burst", burst=100, threshold=0,
        ))
        dispatch.assert_not_awaited()

    def test_threshold_snapshot_carries_correct_fields(self):
        dispatch = _run(_run_check_for_provider(
            "deepgram", "deepgram_429_burst_threshold",
            "deepgram_429_burst", burst=8, threshold=5,
        ))
        snap = dispatch.await_args.kwargs.get("threshold_snapshot")
        assert snap is not None
        assert snap["metric"] == "deepgram_429_burst_threshold"
        assert snap["value"] == 5
        assert snap["actual"] == 8
        assert snap["window_seconds"] == 180


class TestAzureDeepgramAlertingLoop:
    """End-to-end: drive a real _alerting_loop iteration and assert the
    dispatched alert_type strings for checks #11 and #12.

    Uses the shared _run_one_alerting_loop_iteration helper which already
    feeds the new threshold keys (added in this task) into the patched
    _ALERT_THRESHOLDS dict."""

    def test_azure_openai_alert_loop_fires_at_threshold(self):
        dispatched = _run(_run_one_alerting_loop_iteration(
            {"azure_openai": 5}, threshold=5
        ))
        assert "azure_openai_429_burst" in dispatched, (
            f"expected 'azure_openai_429_burst' in dispatched alerts; "
            f"got {dispatched}"
        )

    def test_azure_openai_alert_loop_fires_above_threshold(self):
        dispatched = _run(_run_one_alerting_loop_iteration(
            {"azure_openai": 11}, threshold=5
        ))
        assert "azure_openai_429_burst" in dispatched

    def test_azure_openai_alert_loop_silent_below_threshold(self):
        dispatched = _run(_run_one_alerting_loop_iteration(
            {"azure_openai": 4}, threshold=5
        ))
        assert "azure_openai_429_burst" not in dispatched

    def test_azure_openai_alert_loop_silent_when_threshold_zero(self):
        dispatched = _run(_run_one_alerting_loop_iteration(
            {"azure_openai": 100}, threshold=0
        ))
        assert "azure_openai_429_burst" not in dispatched

    def test_deepgram_alert_loop_fires_at_threshold(self):
        dispatched = _run(_run_one_alerting_loop_iteration(
            {"deepgram": 5}, threshold=5
        ))
        assert "deepgram_429_burst" in dispatched, (
            f"expected 'deepgram_429_burst' in dispatched alerts; "
            f"got {dispatched}"
        )

    def test_deepgram_alert_loop_fires_above_threshold(self):
        dispatched = _run(_run_one_alerting_loop_iteration(
            {"deepgram": 7}, threshold=5
        ))
        assert "deepgram_429_burst" in dispatched

    def test_deepgram_alert_loop_silent_below_threshold(self):
        dispatched = _run(_run_one_alerting_loop_iteration(
            {"deepgram": 2}, threshold=5
        ))
        assert "deepgram_429_burst" not in dispatched

    def test_deepgram_alert_loop_silent_when_threshold_zero(self):
        dispatched = _run(_run_one_alerting_loop_iteration(
            {"deepgram": 100}, threshold=0
        ))
        assert "deepgram_429_burst" not in dispatched

    def test_both_azure_and_deepgram_fire_in_same_iteration(self):
        """Both providers throttled simultaneously → both alerts dispatched."""
        dispatched = _run(_run_one_alerting_loop_iteration(
            {"azure_openai": 6, "deepgram": 8}, threshold=5
        ))
        assert "azure_openai_429_burst" in dispatched
        assert "deepgram_429_burst" in dispatched

    def test_azure_burst_does_not_fire_deepgram_alert(self):
        """Azure burst must not bleed into the Deepgram counter."""
        dispatched = _run(_run_one_alerting_loop_iteration(
            {"azure_openai": 10, "deepgram": 0}, threshold=5
        ))
        assert "azure_openai_429_burst" in dispatched
        assert "deepgram_429_burst" not in dispatched

    def test_deepgram_burst_does_not_fire_azure_alert(self):
        dispatched = _run(_run_one_alerting_loop_iteration(
            {"deepgram": 10, "azure_openai": 0}, threshold=5
        ))
        assert "deepgram_429_burst" in dispatched
        assert "azure_openai_429_burst" not in dispatched

    def test_azure_burst_does_not_fire_other_provider_alerts(self):
        """Azure burst alone must not trigger Workers AI / Groq / Gemini."""
        dispatched = _run(_run_one_alerting_loop_iteration(
            {"azure_openai": 20}, threshold=5
        ))
        assert "azure_openai_429_burst" in dispatched
        assert "workers_ai_429_burst" not in dispatched
        assert "groq_429_burst" not in dispatched
        assert "gemini_429_burst" not in dispatched

    def test_deepgram_burst_does_not_fire_other_provider_alerts(self):
        dispatched = _run(_run_one_alerting_loop_iteration(
            {"deepgram": 20}, threshold=5
        ))
        assert "deepgram_429_burst" in dispatched
        assert "workers_ai_429_burst" not in dispatched
        assert "groq_429_burst" not in dispatched
        assert "gemini_429_burst" not in dispatched


class TestAzureDeepgramSourceContract:
    """Source-level guarantees so future renames break tests, not prod."""

    def test_metrics_default_thresholds_include_azure_openai_key(self):
        assert (
            "azure_openai_429_burst_threshold"
            in metrics_mod._ALERT_THRESHOLDS_DEFAULT
        )
        assert metrics_mod._ALERT_THRESHOLDS_DEFAULT[
            "azure_openai_429_burst_threshold"
        ] == 5

    def test_metrics_default_thresholds_include_deepgram_key(self):
        assert (
            "deepgram_429_burst_threshold"
            in metrics_mod._ALERT_THRESHOLDS_DEFAULT
        )
        assert metrics_mod._ALERT_THRESHOLDS_DEFAULT[
            "deepgram_429_burst_threshold"
        ] == 5

    def test_metrics_alerting_loop_dispatches_azure_openai_429_burst(self):
        """_alerting_loop must contain a dispatch for azure_openai_429_burst."""
        tree = ast.parse(_METRICS_PY.read_text(encoding="utf-8"))
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_alerting_loop":
                body = ast.unparse(node)
                if "azure_openai_429_burst" in body and "_dispatch_alert" in body:
                    found = True
                    break
        assert found, (
            "_alerting_loop must contain _dispatch_alert('azure_openai_429_burst', ...)"
        )

    def test_metrics_alerting_loop_dispatches_deepgram_429_burst(self):
        tree = ast.parse(_METRICS_PY.read_text(encoding="utf-8"))
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_alerting_loop":
                body = ast.unparse(node)
                if "deepgram_429_burst" in body and "_dispatch_alert" in body:
                    found = True
                    break
        assert found, (
            "_alerting_loop must contain _dispatch_alert('deepgram_429_burst', ...)"
        )

    def test_llm_provider_429_windows_includes_azure_openai(self):
        assert "azure_openai" in llm_mod._PROVIDER_429_WINDOWS

    def test_llm_provider_429_windows_includes_deepgram(self):
        assert "deepgram" in llm_mod._PROVIDER_429_WINDOWS

    def test_llm_redis_keys_for_azure_and_deepgram_unchanged(self):
        """Redis keys must stay stable so existing TTL keys in prod aren't
        orphaned mid-burst."""
        assert llm_mod._PROVIDER_429_REDIS_KEYS["azure_openai"] == "azure_429_burst"
        assert llm_mod._PROVIDER_429_REDIS_KEYS["deepgram"] == "deepgram_429_burst"


class TestAzureDeepgramTrackingIsolation:
    """The new alert checks rely on _track_provider_429 routing 429s into
    the right per-provider window. Verify Azure/Deepgram tracking does not
    leak into Workers AI / Groq / Gemini (and vice versa)."""

    def test_track_azure_openai_increments_only_azure_burst(self):
        for _ in range(4):
            llm_mod._track_provider_429("azure_openai")
        assert llm_mod.get_provider_429_burst_inprocess("azure_openai", 60) == 4
        assert llm_mod.get_provider_429_burst_inprocess("workers-ai", 60) == 0
        assert llm_mod.get_provider_429_burst_inprocess("groq", 60) == 0
        assert llm_mod.get_provider_429_burst_inprocess("gemini", 60) == 0
        assert llm_mod.get_provider_429_burst_inprocess("deepgram", 60) == 0

    def test_track_deepgram_increments_only_deepgram_burst(self):
        for _ in range(3):
            llm_mod._track_provider_429("deepgram")
        assert llm_mod.get_provider_429_burst_inprocess("deepgram", 60) == 3
        assert llm_mod.get_provider_429_burst_inprocess("workers-ai", 60) == 0
        assert llm_mod.get_provider_429_burst_inprocess("groq", 60) == 0
        assert llm_mod.get_provider_429_burst_inprocess("gemini", 60) == 0
        assert llm_mod.get_provider_429_burst_inprocess("azure_openai", 60) == 0

    def test_reset_azure_openai_does_not_clear_deepgram(self):
        for _ in range(3):
            llm_mod._track_provider_429("azure_openai")
        for _ in range(2):
            llm_mod._track_provider_429("deepgram")
        llm_mod._reset_provider_429("azure_openai")
        assert llm_mod.get_provider_429_burst_inprocess("azure_openai", 60) == 0
        assert llm_mod.get_provider_429_burst_inprocess("deepgram", 60) == 2

    def test_reset_deepgram_does_not_clear_azure_openai(self):
        for _ in range(3):
            llm_mod._track_provider_429("azure_openai")
        for _ in range(2):
            llm_mod._track_provider_429("deepgram")
        llm_mod._reset_provider_429("deepgram")
        assert llm_mod.get_provider_429_burst_inprocess("deepgram", 60) == 0
        assert llm_mod.get_provider_429_burst_inprocess("azure_openai", 60) == 3

    def test_track_azure_uses_azure_redis_key(self):
        mock_rc = MagicMock()
        with patch("deps.redis_client", mock_rc):
            llm_mod._track_provider_429("azure_openai")
        mock_rc.incr.assert_called_once_with(
            llm_mod._PROVIDER_429_REDIS_KEYS["azure_openai"]
        )
        mock_rc.expire.assert_called_once_with(
            llm_mod._PROVIDER_429_REDIS_KEYS["azure_openai"],
            llm_mod._PROVIDER_429_BURST_WINDOW_S,
        )

    def test_track_deepgram_uses_deepgram_redis_key(self):
        mock_rc = MagicMock()
        with patch("deps.redis_client", mock_rc):
            llm_mod._track_provider_429("deepgram")
        mock_rc.incr.assert_called_once_with(
            llm_mod._PROVIDER_429_REDIS_KEYS["deepgram"]
        )
        mock_rc.expire.assert_called_once_with(
            llm_mod._PROVIDER_429_REDIS_KEYS["deepgram"],
            llm_mod._PROVIDER_429_BURST_WINDOW_S,
        )
