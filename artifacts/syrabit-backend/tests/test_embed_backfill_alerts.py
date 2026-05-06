"""Task #468 — Tests for the embed-backfill alert watcher.

Locks the contract of:

* ``_evaluate_alert_state`` — the pure decision helper that classifies
  the current ``embed_backfill_state`` doc as ``failing`` /
  ``stalled`` / healthy / no-state. We pin this so a future refactor
  of the state-doc field names or value types (datetime vs ISO
  string, missing ``last_run``, etc.) cannot silently break the
  watcher and let an outage go unpaged.

* ``alert_loop`` — the long-running dispatcher around the helper. We
  pin two specific behaviours: (1) a paged failure followed by a
  healthy iteration must fire the recovery alert exactly once, and
  (2) consecutive failing iterations must not re-page until a
  healthy iteration resets the latch.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _make_state_db(state_doc):
    """Tiny Motor-shaped mock that only services the state collection."""
    state_coll = MagicMock(name="embed_backfill_state")

    async def _find_one(q):
        if state_doc is None:
            return None
        return dict(state_doc)

    state_coll.find_one = _find_one

    db = MagicMock()

    def _getitem(self, name):
        if name == "embed_backfill_state":
            return state_coll
        return MagicMock()

    db.__getitem__ = _getitem
    return db


# ── _evaluate_alert_state ────────────────────────────────────────────────────


async def test_evaluate_no_state_doc_skips():
    """A fresh DB with no state doc must not page — the job has never
    run so there is nothing to alert on yet."""
    from aca_jobs import embed_backfill

    db = _make_state_db(None)
    decision = await embed_backfill._evaluate_alert_state(db)
    assert decision["alert_type"] is None
    assert decision["skipped"] == "no_state"


async def test_evaluate_healthy_run_skips():
    """``running=False``, recent ``updated_at``, and ``last_run.failed``
    well below threshold ⇒ no alert."""
    from aca_jobs import embed_backfill

    db = _make_state_db({
        "_id": "global",
        "running": False,
        "updated_at": _dt.datetime.utcnow(),
        "last_run": {"processed": 100, "succeeded": 100,
                     "failed": 0, "skipped": 0, "remaining": 50},
    })
    decision = await embed_backfill._evaluate_alert_state(db)
    assert decision["alert_type"] is None
    assert decision["skipped"] == "healthy"


async def test_evaluate_failed_at_threshold_pages():
    """``last_run.failed`` exactly at the threshold must page —
    a strict ``>`` would let a sustained failure mode sit unwatched
    when it lands right on the boundary."""
    from aca_jobs import embed_backfill

    db = _make_state_db({
        "_id": "global",
        "running": False,
        "updated_at": _dt.datetime.utcnow(),
        "last_run": {
            "processed": 100, "succeeded": 50,
            "failed": embed_backfill.ALERT_FAILED_THRESHOLD,
            "skipped": 0, "remaining": 200,
        },
    })
    decision = await embed_backfill._evaluate_alert_state(db)
    assert decision["alert_type"] == embed_backfill.ALERT_TYPE_FAILING
    assert decision["title"] == "Embedding backfill failing"
    snap = decision["snapshot"]
    assert snap["metric"] == "embed_backfill_last_run_failed"
    assert snap["actual"] == embed_backfill.ALERT_FAILED_THRESHOLD


async def test_evaluate_failed_over_threshold_pages():
    from aca_jobs import embed_backfill

    over = embed_backfill.ALERT_FAILED_THRESHOLD + 25
    db = _make_state_db({
        "_id": "global",
        "running": False,
        "updated_at": _dt.datetime.utcnow(),
        "last_run": {"processed": 200, "succeeded": 100,
                     "failed": over, "skipped": 0, "remaining": 99},
    })
    decision = await embed_backfill._evaluate_alert_state(db)
    assert decision["alert_type"] == embed_backfill.ALERT_TYPE_FAILING
    assert decision["snapshot"]["actual"] == over
    # Body should carry actionable counts.
    assert "failed=" in decision["body"]
    assert "remaining=99" in decision["body"]


async def test_evaluate_running_with_stale_updated_at_pages_stalled():
    """``running=True`` but ``updated_at`` older than the stall
    threshold ⇒ the autostart loop probably crashed mid-run; on-call
    must be paged with the stall message."""
    from aca_jobs import embed_backfill

    stale = _dt.datetime.utcnow() - _dt.timedelta(
        minutes=embed_backfill.ALERT_STALL_MINUTES + 5
    )
    db = _make_state_db({
        "_id": "global",
        "running": True,
        "updated_at": stale,
        "last_processed_id": "chunk-42",
        "started_at": stale - _dt.timedelta(minutes=10),
        "last_run": {"failed": 0},
    })
    decision = await embed_backfill._evaluate_alert_state(db)
    assert decision["alert_type"] == embed_backfill.ALERT_TYPE_STALLED
    assert decision["title"] == "Embedding backfill stalled"
    snap = decision["snapshot"]
    assert snap["metric"] == "embed_backfill_updated_at_age_min"
    assert snap["actual"] >= embed_backfill.ALERT_STALL_MINUTES


async def test_evaluate_not_running_with_stale_updated_at_skips():
    """``running=False`` ⇒ the job is just idle between passes, not
    stalled. A stale ``updated_at`` here is normal and must not page."""
    from aca_jobs import embed_backfill

    stale = _dt.datetime.utcnow() - _dt.timedelta(
        minutes=embed_backfill.ALERT_STALL_MINUTES + 60
    )
    db = _make_state_db({
        "_id": "global",
        "running": False,
        "updated_at": stale,
        "last_run": {"failed": 0},
    })
    decision = await embed_backfill._evaluate_alert_state(db)
    assert decision["alert_type"] is None
    assert decision["skipped"] == "healthy"


async def test_evaluate_updated_at_naive_datetime_ages_correctly():
    """Naive UTC datetime — what ``_write_state`` actually writes — must
    age out exactly as expected so the stall detector triggers on real
    state docs."""
    from aca_jobs import embed_backfill

    stale_naive = _dt.datetime.utcnow() - _dt.timedelta(
        minutes=embed_backfill.ALERT_STALL_MINUTES + 1
    )
    assert stale_naive.tzinfo is None
    db = _make_state_db({
        "_id": "global",
        "running": True,
        "updated_at": stale_naive,
        "last_run": {"failed": 0},
    })
    decision = await embed_backfill._evaluate_alert_state(db)
    assert decision["alert_type"] == embed_backfill.ALERT_TYPE_STALLED


async def test_evaluate_updated_at_iso_string_ages_correctly():
    """Some Mongo drivers / serialisers round-trip the timestamp as an
    ISO-8601 string. The watcher must coerce it back to a datetime so a
    serialiser change doesn't silently disable stall detection."""
    from aca_jobs import embed_backfill

    stale = _dt.datetime.utcnow() - _dt.timedelta(
        minutes=embed_backfill.ALERT_STALL_MINUTES + 2
    )
    iso = stale.isoformat() + "Z"
    db = _make_state_db({
        "_id": "global",
        "running": True,
        "updated_at": iso,
        "last_run": {"failed": 0},
    })
    decision = await embed_backfill._evaluate_alert_state(db)
    assert decision["alert_type"] == embed_backfill.ALERT_TYPE_STALLED
    assert decision["snapshot"]["actual"] >= embed_backfill.ALERT_STALL_MINUTES


# ── alert_loop ───────────────────────────────────────────────────────────────


class _StopLoop(Exception):
    """Sentinel raised inside the patched ``asyncio.sleep`` to break the
    otherwise-infinite ``alert_loop`` after a known number of
    iterations. The loop's outer ``except Exception`` swallows this,
    so we re-raise via a wrapper task instead."""


def _patch_loop_runtime(monkeypatch, decisions, *, max_sleeps):
    """Wire ``alert_loop`` so it executes ``len(decisions)`` iterations
    against a scripted ``_evaluate_alert_state`` and then exits via a
    ``CancelledError`` from the inter-iteration sleep."""
    from aca_jobs import embed_backfill

    # Skip the 5-min startup delay outright.
    monkeypatch.setattr(embed_backfill, "ALERT_STARTUP_DELAY_S", 0)

    # Scripted evaluator.
    decisions_iter = iter(decisions)

    async def _fake_eval(db):
        try:
            return next(decisions_iter)
        except StopIteration:
            # Out of script — keep returning healthy until the sleep
            # patch tears the loop down.
            return {"alert_type": None, "title": None, "body": None,
                    "snapshot": None, "skipped": "healthy", "state": {}}

    monkeypatch.setattr(embed_backfill, "_evaluate_alert_state", _fake_eval)

    # Patched sleep: count calls, raise CancelledError once we've
    # serviced enough iterations to cover the script.
    sleep_calls = {"n": 0}

    async def _fake_sleep(secs):
        sleep_calls["n"] += 1
        if sleep_calls["n"] >= max_sleeps:
            raise asyncio.CancelledError()
        return None

    monkeypatch.setattr(embed_backfill.asyncio, "sleep", _fake_sleep)

    # Capture every dispatched alert so the test can assert on the
    # exact sequence (which alert_type, fired how many times).
    dispatched: list[dict] = []

    async def _fake_dispatch(alert_type, title, body,
                             threshold_snapshot=None, force=False):
        dispatched.append({
            "alert_type": alert_type, "title": title, "body": body,
            "snapshot": threshold_snapshot, "force": force,
        })

    import metrics as _metrics
    monkeypatch.setattr(_metrics, "_dispatch_alert", _fake_dispatch)

    return embed_backfill, dispatched


async def _drive_loop(embed_backfill):
    """Run ``alert_loop`` until the patched sleep cancels it."""
    try:
        await embed_backfill.alert_loop(MagicMock())
    except asyncio.CancelledError:
        return


async def test_alert_loop_recovery_fires_once_after_failure_clears(monkeypatch):
    """A failing iteration must page once, then a follow-up healthy
    iteration must dispatch the recovery alert exactly once — closing
    the loop so the next failure can re-page."""
    failing_decision = {
        "alert_type": "embed_backfill_failing",
        "title": "Embedding backfill failing",
        "body": "failed=99",
        "snapshot": {"metric": "embed_backfill_last_run_failed",
                     "value": 50, "actual": 99},
        "skipped": None, "state": {},
    }
    healthy_decision = {
        "alert_type": None, "title": None, "body": None,
        "snapshot": None, "skipped": "healthy", "state": {},
    }
    embed_backfill, dispatched = _patch_loop_runtime(
        monkeypatch,
        decisions=[failing_decision, healthy_decision, healthy_decision],
        # startup-delay sleep + one inter-iter sleep per scripted
        # decision = 4 sleeps. Stop on the 4th so all three
        # iterations get to run their dispatch logic.
        max_sleeps=4,
    )

    await _drive_loop(embed_backfill)

    types = [d["alert_type"] for d in dispatched]
    assert types == [
        embed_backfill.ALERT_TYPE_FAILING,
        embed_backfill.ALERT_TYPE_RECOVERED,
    ], f"unexpected dispatch sequence: {types}"
    # The recovery alert must be ``force=True`` so the cooldown that
    # the failing alert just consumed doesn't silence it.
    recovery = dispatched[1]
    assert recovery["force"] is True
    assert recovery["title"] == "Embedding backfill recovered"


async def test_alert_loop_does_not_repage_on_consecutive_failures(monkeypatch):
    """Two consecutive failing iterations must result in exactly one
    paged dispatch — the per-run latch protects on-call from a flood
    while the underlying problem persists. A subsequent healthy run
    clears the latch (recovery), and then a fresh failure re-pages."""
    failing_decision = {
        "alert_type": "embed_backfill_failing",
        "title": "Embedding backfill failing",
        "body": "failed=99",
        "snapshot": {"metric": "embed_backfill_last_run_failed",
                     "value": 50, "actual": 99},
        "skipped": None, "state": {},
    }
    healthy_decision = {
        "alert_type": None, "title": None, "body": None,
        "snapshot": None, "skipped": "healthy", "state": {},
    }
    embed_backfill, dispatched = _patch_loop_runtime(
        monkeypatch,
        decisions=[
            failing_decision,   # paged
            failing_decision,   # latched — no re-page
            failing_decision,   # latched — no re-page
            healthy_decision,   # recovery
            failing_decision,   # latch reset → paged again
        ],
        # 1 startup + 5 inter-iter sleeps = 6 sleeps to service all
        # five scripted iterations.
        max_sleeps=6,
    )

    await _drive_loop(embed_backfill)

    types = [d["alert_type"] for d in dispatched]
    assert types == [
        embed_backfill.ALERT_TYPE_FAILING,
        embed_backfill.ALERT_TYPE_RECOVERED,
        embed_backfill.ALERT_TYPE_FAILING,
    ], (
        "consecutive failures should not re-page; recovery must "
        f"reset the latch. got: {types}"
    )
