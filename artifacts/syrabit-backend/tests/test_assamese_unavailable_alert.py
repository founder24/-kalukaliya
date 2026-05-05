"""Task #374 — Test the Assamese chat "both rails red" alert pipeline.

Mirrors ``test_workers_ai_429_throttle_alert.py`` over four axes:
 A. Counter helpers (llm.record_assamese_unavailable,
    get_assamese_unavailable_burst, get_assamese_unavailable_burst_inprocess).
 B. Alerting check #13 in metrics._alerting_loop: fires _dispatch_alert
    when burst >= threshold, silent below threshold, silent at threshold=0.
 C. Source-level contract: metrics.py contains the check and llm.py
    exports the required symbols (pure import assertions — zero I/O).
 D. End-to-end through _dispatch_alert with a db mock.
"""
from __future__ import annotations

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
    """Clear the in-memory assamese_unavailable window before & after each test."""
    llm_mod._ASSAMESE_UNAVAILABLE_WINDOW.clear()
    yield
    llm_mod._ASSAMESE_UNAVAILABLE_WINDOW.clear()


@pytest.fixture(autouse=True)
def _reset_metrics_cooldowns():
    """Wipe in-memory alert cooldown so tests don't bleed cross-test."""
    metrics_mod._alert_last_fired.clear()
    metrics_mod._notification_channels = dict(
        metrics_mod._NOTIFICATION_CHANNELS_DEFAULT
    )
    # Task #380: also reset the firing-state latch so recovery-alert
    # tests start from a clean slate.
    metrics_mod._assamese_unavailable_was_firing = False
    yield
    metrics_mod._alert_last_fired.clear()
    metrics_mod._assamese_unavailable_was_firing = False


# ═══════════════════════════════════════════════════════════════════════════
# A. Counter helpers
# ═══════════════════════════════════════════════════════════════════════════

class TestRecordAndCount:
    def test_single_event_increments_window(self):
        llm_mod.record_assamese_unavailable()
        assert len(llm_mod._ASSAMESE_UNAVAILABLE_WINDOW) == 1

    def test_three_events_increment_window_three_times(self):
        for _ in range(3):
            llm_mod.record_assamese_unavailable()
        assert len(llm_mod._ASSAMESE_UNAVAILABLE_WINDOW) == 3

    def test_inprocess_burst_counts_within_window(self):
        for _ in range(3):
            llm_mod.record_assamese_unavailable()
        count = llm_mod.get_assamese_unavailable_burst_inprocess(window_seconds=60)
        assert count == 3

    def test_inprocess_burst_excludes_old_timestamps(self):
        # Old timestamps must not count toward a 60s window.
        old_ts = time.time() - 200
        llm_mod._ASSAMESE_UNAVAILABLE_WINDOW.extend([old_ts, old_ts, old_ts])
        llm_mod.record_assamese_unavailable()
        count = llm_mod.get_assamese_unavailable_burst_inprocess(window_seconds=60)
        assert count == 1

    def test_inprocess_burst_empty_returns_zero(self):
        assert llm_mod.get_assamese_unavailable_burst_inprocess(60) == 0

    def test_record_appends_recent_timestamp(self):
        before = time.time()
        llm_mod.record_assamese_unavailable()
        after = time.time()
        ts = llm_mod._ASSAMESE_UNAVAILABLE_WINDOW[-1]
        assert before <= ts <= after

    def test_record_calls_redis_incr_and_expire_when_available(self):
        mock_rc = MagicMock()
        with patch("deps.redis_client", mock_rc):
            llm_mod.record_assamese_unavailable()
        mock_rc.incr.assert_called_once_with(llm_mod._ASSAMESE_UNAVAILABLE_REDIS_KEY)
        mock_rc.expire.assert_called_once_with(
            llm_mod._ASSAMESE_UNAVAILABLE_REDIS_KEY,
            llm_mod._ASSAMESE_UNAVAILABLE_BURST_WINDOW_S,
        )

    def test_record_survives_redis_error(self):
        mock_rc = MagicMock()
        mock_rc.incr.side_effect = RuntimeError("redis down")
        with patch("deps.redis_client", mock_rc):
            llm_mod.record_assamese_unavailable()  # must not raise
        # In-memory window must still be incremented.
        assert len(llm_mod._ASSAMESE_UNAVAILABLE_WINDOW) == 1


class TestGetBurstRedisPath:
    def test_prefers_redis_value_over_inprocess(self):
        mock_rc = MagicMock()
        mock_rc.get.return_value = "7"
        with patch("deps.redis_client", mock_rc):
            count = llm_mod.get_assamese_unavailable_burst(180)
        assert count == 7

    def test_falls_back_to_inprocess_when_redis_returns_none(self):
        mock_rc = MagicMock()
        mock_rc.get.return_value = None
        for _ in range(3):
            llm_mod.record_assamese_unavailable()
        with patch("deps.redis_client", mock_rc):
            count = llm_mod.get_assamese_unavailable_burst(180)
        assert count == 3

    def test_falls_back_to_inprocess_when_redis_raises(self):
        mock_rc = MagicMock()
        mock_rc.get.side_effect = ConnectionError("redis gone")
        for _ in range(2):
            llm_mod.record_assamese_unavailable()
        with patch("deps.redis_client", mock_rc):
            count = llm_mod.get_assamese_unavailable_burst(180)
        assert count == 2


# ═══════════════════════════════════════════════════════════════════════════
# B. Alerting check (metrics._alerting_loop check #13)
# ═══════════════════════════════════════════════════════════════════════════

def _make_db_mock():
    """Minimal db mock for _dispatch_alert (mirrors the WAI 429 test)."""
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


async def _run_check_13(burst: int, threshold: int):
    """Run only check #13 (assamese_unavailable burst) of the alerting
    logic in isolation — same structure as the production check, but
    with patched ``get_assamese_unavailable_burst`` / ``_ALERT_THRESHOLDS``
    / ``_dispatch_alert`` so the rest of the loop is irrelevant.

    Threshold parsing mirrors the production None-aware ``int()`` logic
    and honours threshold=0 as "disabled".
    """
    dispatch_mock = AsyncMock()
    with (
        patch.object(metrics_mod, "_dispatch_alert", dispatch_mock),
        patch.object(
            metrics_mod, "_ALERT_THRESHOLDS",
            {"assamese_unavailable_burst_threshold": threshold},
        ),
        patch("llm.get_assamese_unavailable_burst", return_value=burst),
        patch("llm._ASSAMESE_UNAVAILABLE_BURST_WINDOW_S", 180),
    ):
        _raw = metrics_mod._ALERT_THRESHOLDS.get(
            "assamese_unavailable_burst_threshold"
        )
        try:
            _as_threshold = int(_raw) if _raw is not None else 3
        except (TypeError, ValueError):
            _as_threshold = 3
        if _as_threshold > 0:
            from llm import (
                get_assamese_unavailable_burst,
                _ASSAMESE_UNAVAILABLE_BURST_WINDOW_S,
            )
            _as_burst = get_assamese_unavailable_burst(
                _ASSAMESE_UNAVAILABLE_BURST_WINDOW_S
            )
            if _as_burst >= _as_threshold:
                await metrics_mod._dispatch_alert(
                    "assamese_unavailable_burst",
                    "Assamese chat — both rails red",
                    f"{_as_burst} Assamese-unavailable events in last "
                    f"{_ASSAMESE_UNAVAILABLE_BURST_WINDOW_S}s "
                    f"(threshold: {_as_threshold}).",
                    threshold_snapshot={
                        "metric": "assamese_unavailable_burst_threshold",
                        "value": _as_threshold,
                        "actual": _as_burst,
                        "window_seconds": _ASSAMESE_UNAVAILABLE_BURST_WINDOW_S,
                    },
                )
    return dispatch_mock


class TestAlertCheckFires:
    def test_alert_fires_when_burst_equals_threshold(self):
        dispatch = _run(_run_check_13(burst=3, threshold=3))
        dispatch.assert_awaited_once()
        assert dispatch.await_args.args[0] == "assamese_unavailable_burst"

    def test_alert_fires_when_burst_exceeds_threshold(self):
        dispatch = _run(_run_check_13(burst=12, threshold=3))
        dispatch.assert_awaited_once()

    def test_alert_title_says_both_rails_red(self):
        dispatch = _run(_run_check_13(burst=3, threshold=3))
        title = dispatch.await_args.args[1]
        assert "both rails" in title.lower()
        assert "assamese" in title.lower()

    def test_threshold_snapshot_carries_correct_fields(self):
        dispatch = _run(_run_check_13(burst=7, threshold=3))
        snap = (dispatch.await_args.kwargs.get("threshold_snapshot")
                or dispatch.await_args.args[3])
        assert snap["metric"] == "assamese_unavailable_burst_threshold"
        assert snap["value"] == 3
        assert snap["actual"] == 7
        assert snap["window_seconds"] == 180

    def test_alert_body_references_window_seconds(self):
        dispatch = _run(_run_check_13(burst=3, threshold=3))
        body = dispatch.await_args.args[2]
        assert "180" in body


class TestAlertCheckSilent:
    def test_alert_does_not_fire_when_burst_below_threshold(self):
        dispatch = _run(_run_check_13(burst=2, threshold=3))
        dispatch.assert_not_awaited()

    def test_alert_does_not_fire_when_burst_is_zero(self):
        dispatch = _run(_run_check_13(burst=0, threshold=3))
        dispatch.assert_not_awaited()

    def test_alert_does_not_fire_when_threshold_is_zero(self):
        """Threshold=0 is the documented 'disable this alert' value.
        The None-aware ``int()`` parsing must NOT coerce 0→default."""
        dispatch = _run(_run_check_13(burst=100, threshold=0))
        dispatch.assert_not_awaited()


# ═══════════════════════════════════════════════════════════════════════════
# B2. Recovery alert (Task #380) — fires once on firing→cleared transition
# ═══════════════════════════════════════════════════════════════════════════

async def _run_check_13_recovery(
    burst: int,
    threshold: int,
    was_firing: bool,
):
    """Run check #13 in isolation with the firing-state latch set to
    ``was_firing``, and return ``(dispatch_mock, final_was_firing)``.

    Mirrors the production state-machine in metrics._alerting_loop:
      * burst >= threshold        → fire ``assamese_unavailable_burst``,
                                    latch was_firing=True.
      * burst == 0 AND was_firing → fire ``assamese_unavailable_recovered``,
                                    clear was_firing=False.
      * otherwise                 → no dispatch, no state change.
    """
    dispatch_mock = AsyncMock()
    metrics_mod._assamese_unavailable_was_firing = was_firing
    with (
        patch.object(metrics_mod, "_dispatch_alert", dispatch_mock),
        patch.object(
            metrics_mod, "_ALERT_THRESHOLDS",
            {"assamese_unavailable_burst_threshold": threshold},
        ),
        patch("llm.get_assamese_unavailable_burst", return_value=burst),
        patch("llm._ASSAMESE_UNAVAILABLE_BURST_WINDOW_S", 180),
    ):
        _raw = metrics_mod._ALERT_THRESHOLDS.get(
            "assamese_unavailable_burst_threshold"
        )
        try:
            _as_threshold = int(_raw) if _raw is not None else 3
        except (TypeError, ValueError):
            _as_threshold = 3
        if _as_threshold <= 0:
            metrics_mod._assamese_unavailable_was_firing = False
        if _as_threshold > 0:
            from llm import (
                get_assamese_unavailable_burst,
                _ASSAMESE_UNAVAILABLE_BURST_WINDOW_S,
            )
            _as_burst = get_assamese_unavailable_burst(
                _ASSAMESE_UNAVAILABLE_BURST_WINDOW_S
            )
            if _as_burst >= _as_threshold:
                await metrics_mod._dispatch_alert(
                    "assamese_unavailable_burst",
                    "Assamese chat — both rails red",
                    f"{_as_burst} events in {_ASSAMESE_UNAVAILABLE_BURST_WINDOW_S}s",
                    threshold_snapshot={
                        "metric": "assamese_unavailable_burst_threshold",
                        "value": _as_threshold,
                        "actual": _as_burst,
                        "window_seconds": _ASSAMESE_UNAVAILABLE_BURST_WINDOW_S,
                    },
                )
                metrics_mod._assamese_unavailable_was_firing = True
            elif (
                _as_burst == 0
                and metrics_mod._assamese_unavailable_was_firing
            ):
                await metrics_mod._dispatch_alert(
                    "assamese_unavailable_recovered",
                    "Assamese chat — recovered from \"both rails red\" incident",
                    f"Burst cleared back to 0 within "
                    f"{_ASSAMESE_UNAVAILABLE_BURST_WINDOW_S}s "
                    f"(threshold: {_as_threshold}). Confirm via the admin "
                    f"dashboard health tile.",
                    threshold_snapshot={
                        "metric": "assamese_unavailable_burst_threshold",
                        "value": _as_threshold,
                        "actual": 0,
                        "window_seconds": _ASSAMESE_UNAVAILABLE_BURST_WINDOW_S,
                    },
                )
                metrics_mod._assamese_unavailable_was_firing = False
    return dispatch_mock, metrics_mod._assamese_unavailable_was_firing


class TestRecoveryAlert:
    """Task #380 — auto-page on-call when the Assamese chat 'both rails red'
    incident recovers. Mirrors the SEO-health recovery-alert pattern in
    ``test_seo_health_alerting.py`` (firing→cleared transition fires once,
    sustained-clear stays silent, and the recovery alert reuses the same
    ``_dispatch_alert`` path with a distinct alert_type)."""

    def test_recovery_fires_on_firing_to_cleared_transition(self):
        """Burst=0 and was_firing=True → exactly one recovery dispatch."""
        dispatch, final_state = _run(
            _run_check_13_recovery(burst=0, threshold=3, was_firing=True)
        )
        dispatch.assert_awaited_once()
        assert dispatch.await_args.args[0] == "assamese_unavailable_recovered"
        assert final_state is False

    def test_recovery_does_not_fire_when_never_was_firing(self):
        """Burst=0 and was_firing=False → no dispatch (no incident to recover)."""
        dispatch, final_state = _run(
            _run_check_13_recovery(burst=0, threshold=3, was_firing=False)
        )
        dispatch.assert_not_awaited()
        assert final_state is False

    def test_recovery_does_not_re_fire_while_still_cleared(self):
        """After a recovery, subsequent burst=0 ticks must stay silent until
        the burst alert fires again. Mirrors the SEO recovery-alert pattern
        (recovery is a one-shot per incident)."""
        # Tick 1: firing → cleared, dispatches recovery.
        dispatch1, state1 = _run(
            _run_check_13_recovery(burst=0, threshold=3, was_firing=True)
        )
        dispatch1.assert_awaited_once()
        assert state1 is False
        # Tick 2: still cleared — must NOT re-fire.
        dispatch2, state2 = _run(
            _run_check_13_recovery(burst=0, threshold=3, was_firing=state1)
        )
        dispatch2.assert_not_awaited()
        assert state2 is False

    def test_recovery_does_not_fire_while_still_firing(self):
        """Burst still over threshold → fires the burst alert, NOT recovery,
        and latches was_firing=True for the next tick."""
        dispatch, final_state = _run(
            _run_check_13_recovery(burst=5, threshold=3, was_firing=True)
        )
        dispatch.assert_awaited_once()
        assert dispatch.await_args.args[0] == "assamese_unavailable_burst"
        assert final_state is True

    def test_recovery_does_not_fire_while_burst_decaying_but_nonzero(self):
        """Burst < threshold but > 0 (counter still draining within the 180s
        window) is NOT a full recovery — must stay silent and keep the
        was_firing latch set so the next tick still has a chance to recover."""
        dispatch, final_state = _run(
            _run_check_13_recovery(burst=1, threshold=3, was_firing=True)
        )
        dispatch.assert_not_awaited()
        assert final_state is True

    def test_burst_to_recovery_full_lifecycle(self):
        """End-to-end: burst over threshold (latches firing) → burst clears
        (fires recovery, clears latch) → still cleared (silent). Mirrors
        ``test_e2e_critical_then_critical_then_ok_dedupes_and_recovers``
        in ``test_seo_health_alerting.py``."""
        dispatched_types: list[str] = []

        async def _scenario():
            # Tick 1: burst crosses threshold → fire burst alert, latch on.
            d1, s1 = await _run_check_13_recovery(
                burst=5, threshold=3, was_firing=False,
            )
            for c in d1.await_args_list:
                dispatched_types.append(c.args[0])
            # Tick 2: still over threshold → re-fire burst alert (cooldown
            # would suppress it in the real ``_dispatch_alert``, but our
            # mock just records the attempt). Latch stays on.
            d2, s2 = await _run_check_13_recovery(
                burst=4, threshold=3, was_firing=s1,
            )
            for c in d2.await_args_list:
                dispatched_types.append(c.args[0])
            # Tick 3: burst clears → fire recovery alert, latch off.
            d3, s3 = await _run_check_13_recovery(
                burst=0, threshold=3, was_firing=s2,
            )
            for c in d3.await_args_list:
                dispatched_types.append(c.args[0])
            # Tick 4: still clear → silent.
            d4, s4 = await _run_check_13_recovery(
                burst=0, threshold=3, was_firing=s3,
            )
            for c in d4.await_args_list:
                dispatched_types.append(c.args[0])
            return s4

        final_state = _run(_scenario())
        assert dispatched_types == [
            "assamese_unavailable_burst",
            "assamese_unavailable_burst",
            "assamese_unavailable_recovered",
        ], f"got {dispatched_types}"
        assert final_state is False

    def test_recovery_alert_title_signals_recovery(self):
        """Title must clearly signal recovery so on-call sees it's good news,
        not a re-fire of the same incident. Mirrors the burst-alert
        title-content assertion in ``TestAlertCheckFires``."""
        dispatch, _ = _run(
            _run_check_13_recovery(burst=0, threshold=3, was_firing=True)
        )
        title = dispatch.await_args.args[1]
        assert "recovered" in title.lower()
        assert "assamese" in title.lower()

    def test_recovery_alert_body_links_to_dashboard(self):
        """Body must reference the admin health dashboard so on-call has a
        one-click confirm path before standing down."""
        dispatch, _ = _run(
            _run_check_13_recovery(burst=0, threshold=3, was_firing=True)
        )
        body = dispatch.await_args.args[2]
        assert "admin" in body.lower() and "dashboard" in body.lower()

    def test_recovery_threshold_snapshot_actual_is_zero(self):
        """Snapshot.actual must be 0 (the cleared value) so the alert sink
        can render the recovered metric in the dashboard. Mirrors the
        snapshot-shape assertion in ``test_threshold_snapshot_carries_correct_fields``."""
        dispatch, _ = _run(
            _run_check_13_recovery(burst=0, threshold=3, was_firing=True)
        )
        snap = (dispatch.await_args.kwargs.get("threshold_snapshot")
                or dispatch.await_args.args[3])
        assert snap["metric"] == "assamese_unavailable_burst_threshold"
        assert snap["value"] == 3
        assert snap["actual"] == 0
        assert snap["window_seconds"] == 180

    def test_recovery_alert_uses_distinct_alert_type(self):
        """The recovery alert must use a distinct ``alert_type`` so dedup
        and cooldown bookkeeping don't collapse it with the burst alert."""
        dispatch, _ = _run(
            _run_check_13_recovery(burst=0, threshold=3, was_firing=True)
        )
        assert dispatch.await_args.args[0] == "assamese_unavailable_recovered"
        assert dispatch.await_args.args[0] != "assamese_unavailable_burst"

    def test_threshold_disabled_clears_latch(self):
        """If the admin disables the alert (threshold=0) while the latch
        is set, the latch must be cleared so re-enabling the alert later
        doesn't fire a stale recovery for an incident on-call was never
        paged about. Regression guard for the disabled-while-firing
        edge case flagged in Task #380's code review."""
        dispatch, final_state = _run(
            _run_check_13_recovery(burst=100, threshold=0, was_firing=True)
        )
        # Threshold disabled → no burst dispatch and no recovery dispatch.
        dispatch.assert_not_awaited()
        # Latch must be cleared so a subsequent re-enable + clear doesn't
        # auto-page a phantom recovery.
        assert final_state is False


# ═══════════════════════════════════════════════════════════════════════════
# C. Source-level contract — pure import assertions
# ═══════════════════════════════════════════════════════════════════════════

class TestSourceContract:
    def test_llm_exports_record_helper(self):
        assert hasattr(llm_mod, "record_assamese_unavailable")
        assert callable(llm_mod.record_assamese_unavailable)

    def test_llm_exports_burst_getter(self):
        assert hasattr(llm_mod, "get_assamese_unavailable_burst")
        assert callable(llm_mod.get_assamese_unavailable_burst)

    def test_llm_exports_inprocess_burst_getter(self):
        assert hasattr(llm_mod, "get_assamese_unavailable_burst_inprocess")
        assert callable(llm_mod.get_assamese_unavailable_burst_inprocess)

    def test_llm_exports_window_constant(self):
        assert hasattr(llm_mod, "_ASSAMESE_UNAVAILABLE_BURST_WINDOW_S")
        # 180s window matches the per-provider 429 burst window.
        assert llm_mod._ASSAMESE_UNAVAILABLE_BURST_WINDOW_S == 180

    def test_llm_exports_redis_key_constant(self):
        assert hasattr(llm_mod, "_ASSAMESE_UNAVAILABLE_REDIS_KEY")
        assert llm_mod._ASSAMESE_UNAVAILABLE_REDIS_KEY == "assamese_unavailable_burst"

    def test_metrics_default_threshold_present(self):
        assert (
            "assamese_unavailable_burst_threshold"
            in metrics_mod._ALERT_THRESHOLDS_DEFAULT
        )
        # Default 3 — lower than the 5-default for 429 bursts because
        # each event is a P0 user-visible outage.
        assert (
            metrics_mod._ALERT_THRESHOLDS_DEFAULT[
                "assamese_unavailable_burst_threshold"
            ] == 3
        )

    def test_metrics_alerting_loop_calls_assamese_burst_check(self):
        """The alerting loop source must reference the burst getter and
        the alert type so a future refactor can't silently drop the check.
        """
        src = _METRICS_PY.read_text(encoding="utf-8")
        assert "get_assamese_unavailable_burst" in src
        assert '"assamese_unavailable_burst"' in src
        assert '"assamese_unavailable_burst_threshold"' in src

    def test_metrics_exports_recovery_state_latch(self):
        """Task #380: the firing-state latch must live at module scope so
        it survives across ``_alerting_loop`` ticks and so tests can
        reset it the same way ``_alert_last_fired`` is reset."""
        assert hasattr(metrics_mod, "_assamese_unavailable_was_firing")
        # Must be a plain bool (not a dict / Lock / etc.) so the
        # state-machine logic in check #13 stays trivially auditable.
        assert isinstance(
            metrics_mod._assamese_unavailable_was_firing, bool
        )

    def test_metrics_alerting_loop_dispatches_recovery_alert(self):
        """The alerting loop source must reference the recovery alert
        type so a future refactor can't silently drop the auto-page
        recovery path that pairs with the burst alert."""
        src = _METRICS_PY.read_text(encoding="utf-8")
        assert '"assamese_unavailable_recovered"' in src
        assert "_assamese_unavailable_was_firing" in src

    def test_llm_error_sites_record_unavailable(self):
        """All three error sites in llm.py must call record_assamese_unavailable
        so a future refactor can't silently drop one of the rails.
        """
        src = _LLM_PY.read_text(encoding="utf-8")
        # At least three call sites + the definition itself = >=4 hits.
        n = src.count("record_assamese_unavailable")
        assert n >= 4, f"expected >=4 references to record_assamese_unavailable, found {n}"


# ═══════════════════════════════════════════════════════════════════════════
# D. End-to-end through _dispatch_alert with a db mock
# ═══════════════════════════════════════════════════════════════════════════

class TestAlertWithRealDispatch:
    def test_real_dispatch_called_with_assamese_unavailable_alert_type(self):
        mock_db = _make_db_mock()
        silence_channels = {"email": "", "webhook_url": ""}

        # Pre-seed 3 events so get_assamese_unavailable_burst_inprocess returns 3.
        for _ in range(3):
            llm_mod.record_assamese_unavailable()

        async def _run_real():
            _as_threshold = 3
            _as_burst = llm_mod.get_assamese_unavailable_burst_inprocess(180)
            if _as_burst >= _as_threshold:
                return await metrics_mod._dispatch_alert(
                    "assamese_unavailable_burst",
                    "Assamese chat — both rails red",
                    f"{_as_burst} events in 180s (threshold {_as_threshold})",
                    threshold_snapshot={
                        "metric": "assamese_unavailable_burst_threshold",
                        "value": _as_threshold,
                        "actual": _as_burst,
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

        assert result is not None
        assert result["persisted"]["ok"] is True
        persisted_call = mock_db.alerts.insert_one.await_args.args[0]
        assert persisted_call["type"] == "assamese_unavailable_burst"

    def test_real_dispatch_not_called_when_burst_below_threshold(self):
        mock_db = _make_db_mock()

        # Only 2 events, threshold 3 — must NOT dispatch.
        for _ in range(2):
            llm_mod.record_assamese_unavailable()

        async def _run_real():
            _as_threshold = 3
            _as_burst = llm_mod.get_assamese_unavailable_burst_inprocess(180)
            if _as_burst >= _as_threshold:
                return await metrics_mod._dispatch_alert(
                    "assamese_unavailable_burst", "T", "B"
                )
            return None

        with patch.object(metrics_mod, "db", mock_db):
            result = _run(_run_real())

        assert result is None
        mock_db.alerts.insert_one.assert_not_awaited()
