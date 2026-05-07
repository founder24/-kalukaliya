"""Tests for Task #656 — review-prompt CTR alert loop in
``routes/admin_review_prompts.py``. Mirrors the patterns in
``test_hydrate_alerting.py``: install the deps stub, patch
``deps.db`` / ``deps.is_mongo_available``, and exercise the pure-ish
helpers (``_gather_review_prompt_alert_window``,
``_evaluate_review_prompt_ctr_alerts``) without spinning up the asyncio
loop in production.
"""
import asyncio
import contextlib
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests._deps_stub import install_deps_stub  # noqa: E402

install_deps_stub()
from routes import admin_review_prompts as arp  # noqa: E402


def _reset_cooldowns():
    arp._REVIEW_PROMPT_ALERT_LAST_FIRED.clear()


def _fake_db(*, shown: int = 0, clicked: int = 0, dismissed: int = 0):
    """Build a fake ``db`` whose ``review_prompt_events.aggregate`` yields
    the same shape the gather helper consumes — one row per event type.
    """
    fake_db = MagicMock()

    class _AggCursor:
        def __init__(self, docs):
            self._docs = list(docs)

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._docs:
                raise StopAsyncIteration
            return self._docs.pop(0)

    def _make_docs():
        return [
            {"_id": "review_prompt_shown", "count": shown},
            {"_id": "review_prompt_clicked", "count": clicked},
            {"_id": "review_prompt_dismissed", "count": dismissed},
        ]

    # Fresh cursor per call so the gather helper can be invoked multiple
    # times in the same test (cooldown / repeat-evaluation cases).
    fake_db.review_prompt_events.aggregate = MagicMock(
        side_effect=lambda *a, **kw: _AggCursor(_make_docs()),
    )
    return fake_db


# ------------- _gather_review_prompt_alert_window -------------

def test_gather_returns_zero_shape_when_mongo_unavailable():
    with patch.object(arp, "is_mongo_available", AsyncMock(return_value=False)):
        snap = asyncio.run(arp._gather_review_prompt_alert_window())
    assert snap["shown"] == 0
    assert snap["clicked"] == 0
    assert snap["dismissed"] == 0
    assert snap["ctr_pct"] is None


def test_gather_computes_ctr_pct():
    fake_db = _fake_db(shown=200, clicked=10, dismissed=40)
    with patch.object(arp, "db", fake_db), \
         patch.object(arp, "is_mongo_available", AsyncMock(return_value=True)):
        snap = asyncio.run(arp._gather_review_prompt_alert_window())
    assert snap["shown"] == 200
    assert snap["clicked"] == 10
    assert snap["dismissed"] == 40
    assert snap["ctr_pct"] == 5.0


def test_gather_ctr_none_when_no_shown():
    fake_db = _fake_db(shown=0, clicked=0)
    with patch.object(arp, "db", fake_db), \
         patch.object(arp, "is_mongo_available", AsyncMock(return_value=True)):
        snap = asyncio.run(arp._gather_review_prompt_alert_window())
    assert snap["ctr_pct"] is None


# ------------- _evaluate_review_prompt_ctr_alerts -------------

def test_no_alert_when_shown_below_minimum():
    """Sparse data (shown < min) must never page — preserves the floor of
    statistical relevance even if CTR is mathematically 0%."""
    _reset_cooldowns()
    fake_db = _fake_db(shown=arp.REVIEW_PROMPT_CTR_MIN_SHOWN - 1, clicked=0)
    with patch.object(arp, "db", fake_db), \
         patch.object(arp, "is_mongo_available", AsyncMock(return_value=True)):
        alerts = asyncio.run(arp._evaluate_review_prompt_ctr_alerts(now_ts=1000.0))
    assert alerts == []


def test_no_alert_when_ctr_at_or_above_floor():
    _reset_cooldowns()
    # CTR == floor → not "below", so no alert.
    shown = arp.REVIEW_PROMPT_CTR_MIN_SHOWN * 2
    clicked = int(shown * arp.REVIEW_PROMPT_CTR_FLOOR_PCT / 100)
    fake_db = _fake_db(shown=shown, clicked=clicked)
    with patch.object(arp, "db", fake_db), \
         patch.object(arp, "is_mongo_available", AsyncMock(return_value=True)):
        alerts = asyncio.run(arp._evaluate_review_prompt_ctr_alerts(now_ts=1000.0))
    assert alerts == []


def test_alert_fires_when_ctr_below_floor_and_enough_shown():
    _reset_cooldowns()
    shown = arp.REVIEW_PROMPT_CTR_MIN_SHOWN * 4
    clicked = 1  # tiny CTR — well below 5% floor
    fake_db = _fake_db(shown=shown, clicked=clicked)
    with patch.object(arp, "db", fake_db), \
         patch.object(arp, "is_mongo_available", AsyncMock(return_value=True)):
        alerts = asyncio.run(arp._evaluate_review_prompt_ctr_alerts(now_ts=1000.0))
    assert len(alerts) == 1
    a = alerts[0]
    assert a["alert_type"] == "review_prompt_ctr_low"
    assert "writeReviewUrl" in a["body"]
    assert "/admin/dashboard" in a["body"]
    snap = a["threshold_snapshot"]
    assert snap["metric"] == "review_prompt_ctr_pct"
    assert snap["value"] == arp.REVIEW_PROMPT_CTR_FLOOR_PCT
    assert snap["actual"] < arp.REVIEW_PROMPT_CTR_FLOOR_PCT
    assert snap["shown"] == shown
    assert snap["clicked"] == clicked
    assert snap["window_days"] == arp.REVIEW_PROMPT_ALERT_WINDOW_DAYS


def test_cooldown_suppresses_duplicate_within_window():
    _reset_cooldowns()
    shown = arp.REVIEW_PROMPT_CTR_MIN_SHOWN * 4
    fake_db = _fake_db(shown=shown, clicked=1)
    with patch.object(arp, "db", fake_db), \
         patch.object(arp, "is_mongo_available", AsyncMock(return_value=True)):
        first = asyncio.run(arp._evaluate_review_prompt_ctr_alerts(now_ts=1000.0))
        # Simulate the loop marking cooldown after a successful dispatch.
        arp._REVIEW_PROMPT_ALERT_LAST_FIRED["review_prompt_ctr_low"] = 1000.0
        # Halfway through the cooldown — still suppressed.
        second = asyncio.run(arp._evaluate_review_prompt_ctr_alerts(
            now_ts=1000.0 + arp.REVIEW_PROMPT_ALERT_COOLDOWN_S / 2,
        ))
        # Just past the cooldown — alert may fire again.
        third = asyncio.run(arp._evaluate_review_prompt_ctr_alerts(
            now_ts=1000.0 + arp.REVIEW_PROMPT_ALERT_COOLDOWN_S + 60,
        ))
    assert len(first) == 1
    assert second == []
    assert len(third) == 1


def test_evaluator_does_not_mutate_cooldown_state():
    """Cooldown advancement is the loop's responsibility — evaluator must
    stay pure so a downstream dispatch failure can be retried next tick.
    """
    _reset_cooldowns()
    shown = arp.REVIEW_PROMPT_CTR_MIN_SHOWN * 4
    fake_db = _fake_db(shown=shown, clicked=1)
    with patch.object(arp, "db", fake_db), \
         patch.object(arp, "is_mongo_available", AsyncMock(return_value=True)):
        alerts1 = asyncio.run(arp._evaluate_review_prompt_ctr_alerts(now_ts=1000.0))
        alerts2 = asyncio.run(arp._evaluate_review_prompt_ctr_alerts(now_ts=1000.0 + 60))
    assert len(alerts1) == 1
    assert len(alerts2) == 1
    assert arp._REVIEW_PROMPT_ALERT_LAST_FIRED == {}


# ------------- _evaluate_review_prompt_reason_ctr_drop_alerts (Task #661) ----

def _patch_reason_windows(curr_by_reason, prev_by_reason):
    """Stub ``_aggregate_review_prompt_window`` so the reason-drop
    evaluator sees deterministic curr/prev rollups without needing a
    full Mongo aggregation fake. The first awaited call returns the
    *current* window, the second returns the *previous* window — same
    order the production code requests them.
    """
    # Distinguish curr vs prev by ``until`` — the production code asks
    # for the current window with ``until=now`` and the previous window
    # with ``until=curr_start``. Counter-based fakes break under repeat
    # invocations within the same test (cooldown / mutation cases).
    cutoff = datetime.now(timezone.utc) - timedelta(
        days=arp.REVIEW_PROMPT_ALERT_WINDOW_DAYS - 1,
    )

    async def _fake_agg(since, until):
        if until >= cutoff:
            return {"totals": {}, "by_reason": list(curr_by_reason)}
        return {"totals": {}, "by_reason": list(prev_by_reason)}

    return patch.object(arp, "_aggregate_review_prompt_window", _fake_agg)


def _reason_row(reason, shown, clicked):
    return {"reason": reason, "shown": shown, "clicked": clicked, "dismissed": 0}


def test_reason_drop_no_alert_when_per_reason_sample_too_small():
    """Sample-size gate must protect both windows: a reason that fired
    only a handful of times can't page on noise even if its CTR
    dropped 100 pp."""
    arp._REVIEW_PROMPT_ALERT_LAST_FIRED.clear()
    # 5 shown / 5 clicked → 100% prev; 5 shown / 0 → 0% curr; massive
    # drop but well under the 30-shown gate on both sides.
    curr = [_reason_row("answer_helpful", 5, 0)]
    prev = [_reason_row("answer_helpful", 5, 5)]
    with _patch_reason_windows(curr, prev), \
         patch.object(arp, "is_mongo_available", AsyncMock(return_value=True)):
        alerts = asyncio.run(
            arp._evaluate_review_prompt_reason_ctr_drop_alerts(now_ts=1000.0)
        )
    assert alerts == []


def test_reason_drop_no_alert_when_only_curr_sample_meets_gate():
    """One-sided sample size shouldn't satisfy the gate either — a
    reason that just turned on this week has no comparable baseline."""
    arp._REVIEW_PROMPT_ALERT_LAST_FIRED.clear()
    curr = [_reason_row("answer_helpful", 200, 0)]
    prev = [_reason_row("answer_helpful", 5, 5)]   # prev under min
    with _patch_reason_windows(curr, prev), \
         patch.object(arp, "is_mongo_available", AsyncMock(return_value=True)):
        alerts = asyncio.run(
            arp._evaluate_review_prompt_reason_ctr_drop_alerts(now_ts=1000.0)
        )
    assert alerts == []


def test_reason_drop_no_alert_when_drop_is_below_threshold():
    """A small dip (< drop_pp) on a healthy-volume reason must not page."""
    arp._REVIEW_PROMPT_ALERT_LAST_FIRED.clear()
    # prev: 200/40 = 20%; curr: 200/38 = 19% → -1pp drop, under 5pp default.
    curr = [_reason_row("answer_helpful", 200, 38)]
    prev = [_reason_row("answer_helpful", 200, 40)]
    with _patch_reason_windows(curr, prev), \
         patch.object(arp, "is_mongo_available", AsyncMock(return_value=True)):
        alerts = asyncio.run(
            arp._evaluate_review_prompt_reason_ctr_drop_alerts(now_ts=1000.0)
        )
    assert alerts == []


def test_reason_drop_alert_fires_for_collapsed_reason():
    arp._REVIEW_PROMPT_ALERT_LAST_FIRED.clear()
    # prev: 200/40 = 20%; curr: 200/4 = 2% → -18pp drop, well past 5pp.
    # Second reason stays healthy and must NOT appear in the alert.
    curr = [
        _reason_row("answer_helpful", 200, 4),
        _reason_row("session_end", 200, 50),
    ]
    prev = [
        _reason_row("answer_helpful", 200, 40),
        _reason_row("session_end", 200, 48),
    ]
    with _patch_reason_windows(curr, prev), \
         patch.object(arp, "is_mongo_available", AsyncMock(return_value=True)):
        alerts = asyncio.run(
            arp._evaluate_review_prompt_reason_ctr_drop_alerts(now_ts=1000.0)
        )
    assert len(alerts) == 1
    a = alerts[0]
    assert a["alert_type"] == "review_prompt_reason_ctr_drop"
    assert "answer_helpful" in a["title"]
    assert "answer_helpful" in a["body"]
    assert "session_end" not in a["body"]
    assert "/admin/dashboard" in a["body"]
    snap = a["threshold_snapshot"]
    assert snap["metric"] == "review_prompt_reason_ctr_delta_pp"
    assert len(snap["reasons"]) == 1
    assert snap["reasons"][0]["reason"] == "answer_helpful"
    assert snap["reasons"][0]["delta_pp"] <= -5.0


def test_reason_drop_alert_batches_multiple_reasons_worst_first():
    arp._REVIEW_PROMPT_ALERT_LAST_FIRED.clear()
    # answer_helpful: 20% → 2% (-18pp);  session_end: 15% → 5% (-10pp).
    # answer_helpful collapsed harder so it must lead the title + body.
    curr = [
        _reason_row("answer_helpful", 200, 4),
        _reason_row("session_end", 200, 10),
    ]
    prev = [
        _reason_row("answer_helpful", 200, 40),
        _reason_row("session_end", 200, 30),
    ]
    with _patch_reason_windows(curr, prev), \
         patch.object(arp, "is_mongo_available", AsyncMock(return_value=True)):
        alerts = asyncio.run(
            arp._evaluate_review_prompt_reason_ctr_drop_alerts(now_ts=1000.0)
        )
    assert len(alerts) == 1
    a = alerts[0]
    snap_reasons = a["threshold_snapshot"]["reasons"]
    assert [r["reason"] for r in snap_reasons] == ["answer_helpful", "session_end"]
    # Worst reason mentioned in the title.
    assert "answer_helpful" in a["title"]
    assert "2 reasons" in a["title"] or "2 trigger" in a["title"] or "2 " in a["title"]


def test_reason_drop_cooldown_suppresses_repeats_then_releases():
    arp._REVIEW_PROMPT_ALERT_LAST_FIRED.clear()
    curr = [_reason_row("answer_helpful", 200, 4)]
    prev = [_reason_row("answer_helpful", 200, 40)]
    with _patch_reason_windows(curr, prev), \
         patch.object(arp, "is_mongo_available", AsyncMock(return_value=True)):
        first = asyncio.run(
            arp._evaluate_review_prompt_reason_ctr_drop_alerts(now_ts=1000.0)
        )
    assert len(first) == 1
    # Simulate a successful dispatch marking the cooldown.
    arp._REVIEW_PROMPT_ALERT_LAST_FIRED["review_prompt_reason_ctr_drop"] = 1000.0
    with _patch_reason_windows(curr, prev), \
         patch.object(arp, "is_mongo_available", AsyncMock(return_value=True)):
        mid = asyncio.run(arp._evaluate_review_prompt_reason_ctr_drop_alerts(
            now_ts=1000.0 + arp.REVIEW_PROMPT_ALERT_COOLDOWN_S / 2,
        ))
        after = asyncio.run(arp._evaluate_review_prompt_reason_ctr_drop_alerts(
            now_ts=1000.0 + arp.REVIEW_PROMPT_ALERT_COOLDOWN_S + 60,
        ))
    assert mid == []
    assert len(after) == 1


def test_reason_drop_evaluator_does_not_mutate_cooldown_state():
    """Pure helper — the loop is responsible for cooldown bookkeeping
    so a downstream SES failure can be retried next tick (Task #556)."""
    arp._REVIEW_PROMPT_ALERT_LAST_FIRED.clear()
    curr = [_reason_row("answer_helpful", 200, 4)]
    prev = [_reason_row("answer_helpful", 200, 40)]
    with _patch_reason_windows(curr, prev), \
         patch.object(arp, "is_mongo_available", AsyncMock(return_value=True)):
        a1 = asyncio.run(
            arp._evaluate_review_prompt_reason_ctr_drop_alerts(now_ts=1000.0)
        )
        a2 = asyncio.run(
            arp._evaluate_review_prompt_reason_ctr_drop_alerts(now_ts=1060.0)
        )
    assert len(a1) == 1
    assert len(a2) == 1
    assert arp._REVIEW_PROMPT_ALERT_LAST_FIRED == {}


def test_reason_drop_no_alert_when_mongo_unavailable():
    arp._REVIEW_PROMPT_ALERT_LAST_FIRED.clear()
    with patch.object(arp, "is_mongo_available", AsyncMock(return_value=False)):
        alerts = asyncio.run(
            arp._evaluate_review_prompt_reason_ctr_drop_alerts(now_ts=1000.0)
        )
    assert alerts == []


def test_reason_drop_admin_threshold_overrides_take_effect():
    """Lowering ``review_prompt_reason_ctr_drop_pp`` from the Alert
    Settings panel must let smaller dips page; lowering ``min_shown``
    must let lower-volume reasons through the sample gate."""
    arp._REVIEW_PROMPT_ALERT_LAST_FIRED.clear()
    # Volume is below the default 30-shown gate, dip is below the
    # default 5pp threshold — neither would alert under defaults.
    curr = [_reason_row("answer_helpful", 20, 2)]   # 10%
    prev = [_reason_row("answer_helpful", 20, 4)]   # 20%, -10pp
    import metrics as _m
    saved = dict(_m._ALERT_THRESHOLDS)
    try:
        _m._ALERT_THRESHOLDS["review_prompt_reason_ctr_min_shown"] = 10
        _m._ALERT_THRESHOLDS["review_prompt_reason_ctr_drop_pp"] = 3.0
        with _patch_reason_windows(curr, prev), \
             patch.object(arp, "is_mongo_available", AsyncMock(return_value=True)):
            alerts = asyncio.run(
                arp._evaluate_review_prompt_reason_ctr_drop_alerts(now_ts=1000.0)
            )
    finally:
        _m._ALERT_THRESHOLDS.clear()
        _m._ALERT_THRESHOLDS.update(saved)
    assert len(alerts) == 1
    snap = alerts[0]["threshold_snapshot"]
    assert snap["min_shown"] == 10
    assert snap["value"] == -3.0


# ------------- Task #670: auto-tuned per-reason sigma gate -------------------

def _patch_baseline_weeks(curr_by_reason, weekly_by_reason):
    """Stub ``_aggregate_review_prompt_window`` so each baseline week
    returns a DIFFERENT ``by_reason`` rollup — needed to exercise the
    rolling-stddev path. ``weekly_by_reason`` is indexed week-back:
    index 0 == most recent baseline week (the WoW "prev" window),
    index 1 == one week before that, etc.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(
        days=arp.REVIEW_PROMPT_ALERT_WINDOW_DAYS - 1,
    )
    week = timedelta(days=arp.REVIEW_PROMPT_ALERT_WINDOW_DAYS)
    curr_start = datetime.now(timezone.utc) - week

    async def _fake_agg(since, until):
        if until >= cutoff:
            return {"totals": {}, "by_reason": list(curr_by_reason)}
        # Map ``until`` back to which baseline week it represents.
        # Week 0 has until ≈ curr_start, week 1 has until ≈ curr_start - 7d, ...
        delta_days = (curr_start - until).total_seconds() / 86400.0
        idx = int(round(delta_days / arp.REVIEW_PROMPT_ALERT_WINDOW_DAYS))
        idx = max(0, min(idx, len(weekly_by_reason) - 1))
        return {"totals": {}, "by_reason": list(weekly_by_reason[idx])}

    return patch.object(arp, "_aggregate_review_prompt_window", _fake_agg)


def test_reason_drop_sigma_gate_suppresses_within_noise():
    """A 6 pp WoW drop on a reason whose baseline routinely swings
    ±10 pp must NOT page once the auto-tuned sigma gate is in effect —
    the absolute floor alone would have fired."""
    arp._REVIEW_PROMPT_ALERT_LAST_FIRED.clear()
    # curr: 200/30 = 15%; prev (week 0): 200/42 = 21% → -6 pp delta,
    # past the 5 pp absolute floor.
    curr = [_reason_row("answer_helpful", 200, 30)]
    # Baseline weeks: 21%, 8%, 28%, 12% — mean ≈ 17.25%, stddev ≈ 9 pp.
    # 2× stddev ≈ 18 pp, so -6 pp drop is well within noise.
    weekly = [
        [_reason_row("answer_helpful", 200, 42)],   # 21%
        [_reason_row("answer_helpful", 200, 16)],   #  8%
        [_reason_row("answer_helpful", 200, 56)],   # 28%
        [_reason_row("answer_helpful", 200, 24)],   # 12%
    ]
    with _patch_baseline_weeks(curr, weekly), \
         patch.object(arp, "is_mongo_available", AsyncMock(return_value=True)):
        alerts = asyncio.run(
            arp._evaluate_review_prompt_reason_ctr_drop_alerts(now_ts=1000.0)
        )
    assert alerts == []


def test_reason_drop_sigma_gate_allows_when_drop_exceeds_noise():
    """When the WoW drop dwarfs the baseline stddev, the sigma gate
    must still let the alert through."""
    arp._REVIEW_PROMPT_ALERT_LAST_FIRED.clear()
    # curr: 200/4 = 2%; prev (week 0): 200/40 = 20% → -18 pp delta.
    curr = [_reason_row("answer_helpful", 200, 4)]
    # Tight baseline: 20%, 19%, 21%, 20% — stddev < 1 pp, so 2× stddev
    # is tiny and a -18 pp drop blows right through.
    weekly = [
        [_reason_row("answer_helpful", 200, 40)],   # 20%
        [_reason_row("answer_helpful", 200, 38)],   # 19%
        [_reason_row("answer_helpful", 200, 42)],   # 21%
        [_reason_row("answer_helpful", 200, 40)],   # 20%
    ]
    with _patch_baseline_weeks(curr, weekly), \
         patch.object(arp, "is_mongo_available", AsyncMock(return_value=True)):
        alerts = asyncio.run(
            arp._evaluate_review_prompt_reason_ctr_drop_alerts(now_ts=1000.0)
        )
    assert len(alerts) == 1
    snap_reason = alerts[0]["threshold_snapshot"]["reasons"][0]
    # Baseline stats are surfaced for the on-call engineer.
    assert snap_reason["baseline_weeks_used"] == 4
    assert snap_reason["baseline_mean_ctr_pct"] is not None
    assert snap_reason["baseline_stddev_pp"] is not None
    assert snap_reason["sigma_threshold_pp"] is not None
    snap = alerts[0]["threshold_snapshot"]
    assert snap["sigma_mult"] == arp.REVIEW_PROMPT_REASON_CTR_DROP_SIGMA
    assert snap["baseline_weeks"] == arp.REVIEW_PROMPT_REASON_CTR_BASELINE_WEEKS


def test_reason_drop_sigma_disabled_via_admin_override():
    """Setting ``review_prompt_reason_ctr_drop_sigma`` to 0 from the
    Alert Settings panel must disable the noise gate — the alert then
    reverts to the original absolute-pp-only behaviour."""
    arp._REVIEW_PROMPT_ALERT_LAST_FIRED.clear()
    # Same noisy baseline as the suppression test, but admin disabled
    # the sigma gate so the -6 pp drop should now page.
    curr = [_reason_row("answer_helpful", 200, 30)]
    weekly = [
        [_reason_row("answer_helpful", 200, 42)],
        [_reason_row("answer_helpful", 200, 16)],
        [_reason_row("answer_helpful", 200, 56)],
        [_reason_row("answer_helpful", 200, 24)],
    ]
    import metrics as _m
    saved = dict(_m._ALERT_THRESHOLDS)
    try:
        _m._ALERT_THRESHOLDS["review_prompt_reason_ctr_drop_sigma"] = 0.0
        with _patch_baseline_weeks(curr, weekly), \
             patch.object(arp, "is_mongo_available", AsyncMock(return_value=True)):
            alerts = asyncio.run(
                arp._evaluate_review_prompt_reason_ctr_drop_alerts(now_ts=1000.0)
            )
    finally:
        _m._ALERT_THRESHOLDS.clear()
        _m._ALERT_THRESHOLDS.update(saved)
    assert len(alerts) == 1
    assert alerts[0]["threshold_snapshot"]["sigma_mult"] == 0.0


def test_reason_drop_sigma_gate_skipped_when_baseline_too_thin():
    """If too few baseline weeks have enough volume to compute a
    stddev, the alert must still be allowed to fire on the absolute
    floor alone — otherwise newly-popular reasons would never page."""
    arp._REVIEW_PROMPT_ALERT_LAST_FIRED.clear()
    curr = [_reason_row("answer_helpful", 200, 4)]   # 2%
    # Only the most recent baseline week clears min_shown — every
    # earlier week is below the gate, so we can't compute a stddev.
    weekly = [
        [_reason_row("answer_helpful", 200, 40)],   # 20%, qualifies
        [_reason_row("answer_helpful", 5, 1)],      # too small
        [_reason_row("answer_helpful", 5, 1)],
        [_reason_row("answer_helpful", 5, 1)],
    ]
    with _patch_baseline_weeks(curr, weekly), \
         patch.object(arp, "is_mongo_available", AsyncMock(return_value=True)):
        alerts = asyncio.run(
            arp._evaluate_review_prompt_reason_ctr_drop_alerts(now_ts=1000.0)
        )
    assert len(alerts) == 1
    snap_reason = alerts[0]["threshold_snapshot"]["reasons"][0]
    assert snap_reason["baseline_stddev_pp"] is None
    assert snap_reason["sigma_threshold_pp"] is None


def test_admin_threshold_overrides_take_effect():
    """Admin-tuned thresholds in metrics._ALERT_THRESHOLDS must override
    the module-level defaults (the whole point of the editable knobs)."""
    _reset_cooldowns()
    # 30 shown / 0 clicked → would NOT fire under default min_shown=50,
    # but should fire when admin lowers min_shown to 20.
    fake_db = _fake_db(shown=30, clicked=0)
    import metrics as _m
    saved = dict(_m._ALERT_THRESHOLDS)
    try:
        _m._ALERT_THRESHOLDS["review_prompt_ctr_min_shown"] = 20
        _m._ALERT_THRESHOLDS["review_prompt_ctr_floor_pct"] = 5.0
        with patch.object(arp, "db", fake_db), \
             patch.object(arp, "is_mongo_available", AsyncMock(return_value=True)):
            alerts = asyncio.run(arp._evaluate_review_prompt_ctr_alerts(now_ts=1000.0))
    finally:
        _m._ALERT_THRESHOLDS.clear()
        _m._ALERT_THRESHOLDS.update(saved)
    assert len(alerts) == 1
    snap = alerts[0]["threshold_snapshot"]
    assert snap["min_shown"] == 20
    assert snap["value"] == 5.0


# ------------- Task #681: per-reason baseline noise snapshot -----------------

def test_baseline_noise_snapshot_returns_empty_when_mongo_down():
    """Helper must return the (empty-but-shaped) snapshot when Mongo is
    unavailable so the admin tile can still render with sensible
    defaults."""
    with patch.object(arp, "is_mongo_available", AsyncMock(return_value=False)):
        snap = asyncio.run(arp._compute_review_prompt_reason_baseline(window_days=7))
    assert snap["by_reason"] == {}
    assert snap["window_days"] == 7
    assert snap["baseline_weeks"] == arp.REVIEW_PROMPT_REASON_CTR_BASELINE_WEEKS


def test_baseline_noise_snapshot_computes_mean_stddev_and_zscore():
    """Happy path: snapshot exposes per-reason μ, σ (Bessel-corrected),
    current CTR, and z-score using the SAME baseline aggregation the
    auto-tuned alert uses — so the tile and alert can never drift."""
    # Tight baseline (20%, 19%, 21%, 20%) → μ ≈ 20%, σ < 1 pp.
    # Current week CTR = 200/2 = 1% → z ≈ -19σ-ish, very cold.
    curr = [_reason_row("answer_helpful", 200, 2)]
    weekly = [
        [_reason_row("answer_helpful", 200, 40)],   # 20%
        [_reason_row("answer_helpful", 200, 38)],   # 19%
        [_reason_row("answer_helpful", 200, 42)],   # 21%
        [_reason_row("answer_helpful", 200, 40)],   # 20%
    ]
    with _patch_baseline_weeks(curr, weekly), \
         patch.object(arp, "is_mongo_available", AsyncMock(return_value=True)):
        snap = asyncio.run(
            arp._compute_review_prompt_reason_baseline(window_days=7)
        )
    rec = snap["by_reason"]["answer_helpful"]
    assert rec["baseline_weeks_used"] == 4
    assert abs(rec["baseline_mean_ctr_pct"] - 20.0) < 0.5
    assert rec["baseline_stddev_pp"] is not None
    assert rec["baseline_stddev_pp"] > 0
    assert rec["current_shown"] == 200
    assert rec["current_ctr_pct"] == 1.0
    # z must be strongly negative because the current week is far below μ.
    assert rec["current_z_score"] is not None
    assert rec["current_z_score"] < -5
    assert rec["sigma_threshold_pp"] is not None
    # Snapshot also surfaces the active config so the tile legend can
    # explain the noise band without a second round-trip.
    assert snap["baseline_weeks"] == arp.REVIEW_PROMPT_REASON_CTR_BASELINE_WEEKS
    assert snap["sigma_mult"] == arp.REVIEW_PROMPT_REASON_CTR_DROP_SIGMA
    assert snap["min_shown"] == arp.REVIEW_PROMPT_REASON_CTR_MIN_SHOWN


def test_baseline_noise_snapshot_marks_thin_baseline_as_none():
    """When fewer than 2 baseline weeks clear the min-shown gate the
    helper must report μ / σ / z as None (so the UI shows 'n/a')
    instead of a misleading point estimate."""
    curr = [_reason_row("answer_helpful", 200, 30)]
    # Only one week clears the 30-shown gate; the rest are below it.
    weekly = [
        [_reason_row("answer_helpful", 200, 40)],
        [_reason_row("answer_helpful", 5, 1)],
        [_reason_row("answer_helpful", 5, 1)],
        [_reason_row("answer_helpful", 5, 1)],
    ]
    with _patch_baseline_weeks(curr, weekly), \
         patch.object(arp, "is_mongo_available", AsyncMock(return_value=True)):
        snap = asyncio.run(
            arp._compute_review_prompt_reason_baseline(window_days=7)
        )
    rec = snap["by_reason"]["answer_helpful"]
    assert rec["baseline_weeks_used"] == 1
    assert rec["baseline_mean_ctr_pct"] is None
    assert rec["baseline_stddev_pp"] is None
    assert rec["current_z_score"] is None
    # Current CTR is still surfaced even with a thin baseline.
    assert rec["current_ctr_pct"] == 15.0


# ------------- Task #682: integration test against real Mongo aggregation -----

# These tests stand up an in-memory Motor-compatible client
# (``mongomock_motor``) and seed ``review_prompt_events`` so the noise
# gate exercises the *actual* ``$match`` / ``$group`` pipelines in
# ``_aggregate_review_prompt_window``. The unit tests above stub
# ``_aggregate_review_prompt_window`` directly — a regression in the
# real aggregation (e.g. timezone slippage at week boundaries, wrong
# ``$gte`` / ``$lt`` semantics, by-reason / by-event grouping bugs)
# would silently pass them while breaking production. This file fills
# that gap.

mongomock_motor = pytest.importorskip("mongomock_motor")


def _seed_event(reason, event, when):
    """Shape one ``review_prompt_events`` document. ``when`` should be
    a tz-aware ``datetime`` so it round-trips through Mongo's BSON
    layer with the same UTC instant the production writer emits."""
    return {"event": event, "reason": reason, "created_at": when}


def _seed_bucket(reason, when, *, shown, clicked, dismissed=0):
    """Generate ``shown + clicked + dismissed`` event docs for a single
    (reason, week) cell. All events are stamped at ``when`` — exact
    timestamp doesn't matter to the rollup as long as it lands in the
    correct ``[since, until)`` window, which is the whole point.
    """
    docs = []
    for _ in range(shown):
        docs.append(_seed_event(reason, "review_prompt_shown", when))
    for _ in range(clicked):
        docs.append(_seed_event(reason, "review_prompt_clicked", when))
    for _ in range(dismissed):
        docs.append(_seed_event(reason, "review_prompt_dismissed", when))
    return docs


@contextlib.contextmanager
def _frozen_now(now_dt):
    """Pin ``arp.datetime.now(timezone.utc)`` to ``now_dt`` so the
    week-boundary math in ``_evaluate_review_prompt_reason_ctr_drop_alerts``
    is reproducible. We subclass ``datetime`` and patch the symbol the
    module imported (``arp.datetime``); the real
    ``_aggregate_review_prompt_window`` doesn't call ``datetime.now``
    itself, so the patch only affects the evaluator.
    """
    real_dt = arp.datetime

    class _FrozenDateTime(real_dt):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return now_dt.replace(tzinfo=None)
            return now_dt.astimezone(tz)

    with patch.object(arp, "datetime", _FrozenDateTime):
        yield


async def _seed_collection(coll, docs):
    if docs:
        await coll.insert_many(docs)


def _run_with_real_mongo(now_dt, docs, body):
    """Wire up an in-memory mongomock_motor db, seed ``docs`` into
    ``review_prompt_events``, install it as ``arp.db``, and run the
    async ``body(db)`` coroutine factory under a frozen clock.
    """
    client = mongomock_motor.AsyncMongoMockClient()
    db_obj = client["syrabit_test"]

    async def _go():
        await _seed_collection(db_obj.review_prompt_events, docs)
        with patch.object(arp, "db", db_obj), \
             patch.object(arp, "is_mongo_available",
                          AsyncMock(return_value=True)), \
             _frozen_now(now_dt):
            return await body(db_obj)

    return asyncio.run(_go())


def _five_bucket_seed(now_dt, *, window_days=7):
    """Seed five contiguous weekly buckets (curr + 4 baselines) plus
    boundary-straddling events on either side of ``curr_start``.

    Layout (CTR shown):
      curr   (now-7d, now)        answer_helpful 50/1  = 2%
      week 0 (now-14d, now-7d)    answer_helpful 50/10 = 20%
      week 1 (now-21d, now-14d)   answer_helpful 50/10 = 20%
      week 2 (now-28d, now-21d)   answer_helpful 50/10 = 20%
      week 3 (now-35d, now-28d)   answer_helpful 50/10 = 20%
      session_end stays flat at 20% in every bucket — must NOT alert.
      first_visit only appears in curr — must fail the per-reason
      sample-size gate on the baseline side and stay out of the alert.

    Boundary docs:
      • One ``answer_helpful`` shown stamped at exactly ``curr_start``
        (the ``$gte`` edge of the curr window) — must land in curr.
      • One ``answer_helpful`` shown stamped at ``curr_start - 1µs``
        (the ``$lt`` edge of week 0) — must land in week 0, never in
        curr. These two extra events are reflected in the expected
        per-bucket totals below so an off-by-one in week-boundary
        math would change the asserted CTRs.

    Outside the 35-day window we drop a stale event that must be
    excluded from every aggregation; if the ``$gte since`` clause
    leaks, week 3 will pick it up.
    """
    week = timedelta(days=window_days)
    curr_start = now_dt - week
    docs = []

    # Curr week (mid-bucket timestamp = now - 1 day).
    curr_mid = now_dt - timedelta(days=1)
    docs += _seed_bucket("answer_helpful", curr_mid, shown=49, clicked=1)
    docs += _seed_bucket("session_end", curr_mid, shown=50, clicked=10)
    docs += _seed_bucket("first_visit", curr_mid, shown=50, clicked=1)

    # Boundary: exactly at curr_start → curr ($gte).
    docs.append(_seed_event(
        "answer_helpful", "review_prompt_shown", curr_start,
    ))
    # Boundary: 1µs before curr_start → week 0 ($lt curr_start).
    docs.append(_seed_event(
        "answer_helpful",
        "review_prompt_shown",
        curr_start - timedelta(microseconds=1),
    ))

    # Baseline weeks. Each week's mid-bucket ts is week_start + 3.5d so
    # we're nowhere near the edges except for the explicit boundary
    # docs above.
    # Slightly varied baseline clicks (20/18/22/20%) so stddev > 0 and
    # the production code emits a real sigma_threshold_pp instead of
    # skipping the noise gate. Curr collapses to 2% which still blows
    # past 2σ comfortably.
    baseline_clicks_for_helpful = [10, 9, 11, 10]
    for i in range(4):
        w_until = curr_start - timedelta(days=window_days * i)
        w_since = w_until - week
        w_mid = w_since + timedelta(days=window_days // 2)
        clk = baseline_clicks_for_helpful[i]
        # answer_helpful: shown=49 (week 0 gets +1 from the boundary
        # doc above for a clean 50), clicked varies per week.
        if i == 0:
            docs += _seed_bucket("answer_helpful", w_mid,
                                 shown=49, clicked=clk)
        else:
            docs += _seed_bucket("answer_helpful", w_mid,
                                 shown=50, clicked=clk)
        docs += _seed_bucket("session_end", w_mid, shown=50, clicked=10)
        # first_visit deliberately absent from baselines.

    # Stale event well outside the 5-week window — must NEVER show up.
    docs.append(_seed_event(
        "answer_helpful",
        "review_prompt_shown",
        now_dt - timedelta(days=window_days * 10),
    ))
    return docs


def test_aggregation_respects_week_boundaries_via_real_pipeline():
    """Drive ``_aggregate_review_prompt_window`` through mongomock so
    the real ``$match`` / ``$group`` pipelines decide bucket
    membership. Two events sit at ±1µs of ``curr_start``; an off-by-one
    in ``$gte`` / ``$lt`` would flip both into the wrong bucket and
    move totals by ±1 in opposite directions, which the asserts catch.
    """
    now_dt = datetime(2026, 4, 22, 12, 0, 0, tzinfo=timezone.utc)
    window = timedelta(days=arp.REVIEW_PROMPT_ALERT_WINDOW_DAYS)
    curr_start = now_dt - window
    week0_since = curr_start - window

    async def _body(_db):
        curr = await arp._aggregate_review_prompt_window(curr_start, now_dt)
        week0 = await arp._aggregate_review_prompt_window(
            week0_since, curr_start,
        )
        return curr, week0

    curr, week0 = _run_with_real_mongo(
        now_dt, _five_bucket_seed(now_dt), _body,
    )

    by_reason_curr = {r["reason"]: r for r in curr["by_reason"]}
    by_reason_week0 = {r["reason"]: r for r in week0["by_reason"]}

    # Curr window: 49 in-bucket shown + 1 boundary-at-curr_start = 50.
    # The 1µs-before-curr_start boundary doc must NOT leak in here.
    assert by_reason_curr["answer_helpful"]["shown"] == 50
    assert by_reason_curr["answer_helpful"]["clicked"] == 1
    # Week 0: 49 in-bucket + 1 boundary-at-(curr_start - 1µs) = 50.
    assert by_reason_week0["answer_helpful"]["shown"] == 50
    # Week 0 click count is the first entry of baseline_clicks_for_helpful.
    assert by_reason_week0["answer_helpful"]["clicked"] == 10
    # Reason isolation: session_end / first_visit don't leak into the
    # answer_helpful row; first_visit is curr-only.
    assert by_reason_curr["session_end"]["shown"] == 50
    assert by_reason_curr["first_visit"]["shown"] == 50
    assert "first_visit" not in by_reason_week0
    # Totals roll up across reasons — sanity check vs. the by-reason
    # rows so a divergence between the two $group stages is caught.
    assert curr["totals"]["shown"] == sum(
        r["shown"] for r in curr["by_reason"]
    )
    assert curr["totals"]["clicked"] == sum(
        r["clicked"] for r in curr["by_reason"]
    )


def test_noise_gate_fires_via_real_aggregation_when_curr_collapses():
    """End-to-end: real Mongo aggregation feeds the sigma gate. The
    answer_helpful CTR collapses from a tight ~20% baseline to 2% in
    the current week — the WoW drop blows past both the absolute floor
    and the auto-tuned 2σ band, so the alert MUST fire. session_end
    stays flat, first_visit lacks a baseline; neither may appear in
    the alert payload.
    """
    arp._REVIEW_PROMPT_ALERT_LAST_FIRED.clear()
    now_dt = datetime(2026, 4, 22, 12, 0, 0, tzinfo=timezone.utc)

    async def _body(_db):
        return await arp._evaluate_review_prompt_reason_ctr_drop_alerts(
            now_ts=now_dt.timestamp(),
        )

    alerts = _run_with_real_mongo(
        now_dt, _five_bucket_seed(now_dt), _body,
    )

    assert len(alerts) == 1
    a = alerts[0]
    assert a["alert_type"] == "review_prompt_reason_ctr_drop"
    snap_reasons = a["threshold_snapshot"]["reasons"]
    assert [r["reason"] for r in snap_reasons] == ["answer_helpful"]
    bad = snap_reasons[0]
    # 2% vs ~20% → ~-18 pp delta. Round-trip through Mongo must not
    # smear the value.
    assert bad["delta_pp"] <= -15.0
    # Sigma gate surfaced its inputs.
    assert bad["baseline_weeks_used"] == 4
    assert bad["baseline_mean_ctr_pct"] is not None
    assert bad["baseline_stddev_pp"] is not None
    assert bad["sigma_threshold_pp"] is not None
    # session_end / first_visit must not have been flagged.
    assert "session_end" not in a["body"]
    assert "first_visit" not in a["body"]


def test_noise_gate_quiet_via_real_aggregation_when_within_band():
    """Same scaffolding, opposite outcome. Curr CTR for answer_helpful
    dips by only ~3 pp against a baseline that routinely swings ~10
    pp; the absolute floor is satisfied (3 < 5 pp drop_pp default
    means floor NOT crossed) AND the noise gate would also stop it.
    Either way, no alert. session_end stays flat; first_visit lacks a
    baseline.
    """
    arp._REVIEW_PROMPT_ALERT_LAST_FIRED.clear()
    now_dt = datetime(2026, 4, 22, 12, 0, 0, tzinfo=timezone.utc)
    window = timedelta(days=arp.REVIEW_PROMPT_ALERT_WINDOW_DAYS)
    curr_start = now_dt - window
    docs = []

    # Curr: 50 / 9 = 18% (vs baseline mean ≈ 17.25% — well within band)
    docs += _seed_bucket("answer_helpful", now_dt - timedelta(days=1),
                         shown=50, clicked=9)
    docs += _seed_bucket("session_end", now_dt - timedelta(days=1),
                         shown=50, clicked=10)

    # Noisy baselines: 21%, 8%, 28%, 12% — μ ≈ 17.25%, σ ≈ 9 pp,
    # 2σ ≈ 18 pp. -3 pp falls comfortably inside.
    baseline_clicks = [10, 4, 14, 6]   # /50 → 20%, 8%, 28%, 12%
    for i, clk in enumerate(baseline_clicks):
        w_until = curr_start - timedelta(days=arp.REVIEW_PROMPT_ALERT_WINDOW_DAYS * i)
        w_mid = w_until - timedelta(days=arp.REVIEW_PROMPT_ALERT_WINDOW_DAYS // 2)
        docs += _seed_bucket("answer_helpful", w_mid, shown=50, clicked=clk)
        docs += _seed_bucket("session_end", w_mid, shown=50, clicked=10)

    async def _body(_db):
        return await arp._evaluate_review_prompt_reason_ctr_drop_alerts(
            now_ts=now_dt.timestamp(),
        )

    alerts = _run_with_real_mongo(now_dt, docs, _body)
    assert alerts == []
