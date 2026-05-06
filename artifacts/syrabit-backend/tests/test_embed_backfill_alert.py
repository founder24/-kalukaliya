"""Task #468 — Tests for the embed-backfill alert watcher.

The watcher in ``aca_jobs.embed_backfill`` (Task #434) pages on-call when
the legacy → workers_ai_custom backfill job stalls (``running=True`` but
``state.updated_at`` hasn't advanced) or starts failing
(``last_run.failed`` crosses ``EMBED_BACKFILL_ALERT_FAILED_THRESHOLD``).
Without these tests a future refactor could silently break the page —
the admin pill would simply stop moving and on-call would never know.

Coverage:
  1. No state doc          → no alert (skipped="no_state")
  2. Healthy state         → no alert (skipped="healthy")
  3. failed >= threshold   → ALERT_TYPE_FAILING, includes counts in body
  4. running + stale ts    → ALERT_TYPE_STALLED, threshold snapshot set
  5. failing wins over stalled when both true
  6. running=False + stale → no stall alert (job finished cleanly)
  7. failed_threshold=0    → failing-alert path disabled
  8. updated_at malformed  → graceful skip (no false stall page)
  9. alert_loop dedups     → only ONE _dispatch_alert call per failure
                             run, then a recovery alert on the next
                             healthy iteration
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _state_db(state: dict | None):
    """Build a Mongo-shaped mock that returns ``state`` from ``find_one``.

    Only ``embed_backfill_state`` is needed by the alert watcher — the
    chunks collection is never read on the alert path. ``state=None``
    simulates "job has never run" (no document persisted yet)."""
    state_state: dict = dict(state) if state else {}

    async def _state_find_one(q):
        if not state_state:
            return None
        if state_state.get("_id") == q.get("_id"):
            return dict(state_state)
        return None

    async def _state_update_one(q, update, upsert=False):
        return MagicMock(modified_count=1)

    state_coll = MagicMock(name="embed_backfill_state")
    state_coll.find_one = _state_find_one
    state_coll.update_one = _state_update_one

    db = MagicMock()
    db.__getitem__ = lambda self, name: state_coll
    return db


@pytest.mark.asyncio
async def test_no_state_doc_skips_alert():
    """Job has never run → watcher must NOT page (would be a false alarm)."""
    from aca_jobs import embed_backfill

    db = _state_db(None)
    decision = await embed_backfill._evaluate_alert_state(db)
    assert decision["alert_type"] is None
    assert decision["skipped"] == "no_state"
    assert decision["body"] is None


@pytest.mark.asyncio
async def test_healthy_state_skips_alert():
    """running=False, no failures, fresh updated_at → no alert."""
    from aca_jobs import embed_backfill

    db = _state_db({
        "_id": embed_backfill.STATE_DOC_ID,
        "running": False,
        "last_run": {"processed": 100, "succeeded": 100, "failed": 0,
                     "skipped": 0, "remaining": 0},
        "updated_at": _dt.datetime.utcnow(),
    })
    decision = await embed_backfill._evaluate_alert_state(db)
    assert decision["alert_type"] is None
    assert decision["skipped"] == "healthy"


@pytest.mark.asyncio
async def test_failing_threshold_pages_oncall(monkeypatch):
    """``last_run.failed`` >= threshold MUST emit ALERT_TYPE_FAILING with a
    body that names the failing leg and counts so on-call has actionable
    detail."""
    from aca_jobs import embed_backfill

    monkeypatch.setattr(embed_backfill, "ALERT_FAILED_THRESHOLD", 50, raising=False)

    db = _state_db({
        "_id": embed_backfill.STATE_DOC_ID,
        "running": False,
        "last_run": {"processed": 100, "succeeded": 40, "failed": 60,
                     "skipped": 0, "remaining": 1234},
        "updated_at": _dt.datetime.utcnow(),
    })
    decision = await embed_backfill._evaluate_alert_state(db)
    assert decision["alert_type"] == embed_backfill.ALERT_TYPE_FAILING
    assert decision["title"] == "Embedding backfill failing"
    assert "failed=60" in decision["body"]
    assert "threshold=50" in decision["body"]
    assert "remaining=1234" in decision["body"]
    snap = decision["snapshot"]
    assert snap == {
        "metric": "embed_backfill_last_run_failed",
        "value": 50,
        "actual": 60,
    }


@pytest.mark.asyncio
async def test_stalled_run_pages_oncall(monkeypatch):
    """running=True with ``updated_at`` older than ALERT_STALL_MINUTES MUST
    emit ALERT_TYPE_STALLED — this catches the loop-crashed-mid-run case
    that the failing-counter path doesn't see."""
    from aca_jobs import embed_backfill

    monkeypatch.setattr(embed_backfill, "ALERT_STALL_MINUTES", 30, raising=False)

    stale_ts = _dt.datetime.utcnow() - _dt.timedelta(minutes=45)
    db = _state_db({
        "_id": embed_backfill.STATE_DOC_ID,
        "running": True,
        "last_run": {"processed": 0, "succeeded": 0, "failed": 0},
        "last_processed_id": "abc-123",
        "started_at": stale_ts,
        "updated_at": stale_ts,
    })
    decision = await embed_backfill._evaluate_alert_state(db)
    assert decision["alert_type"] == embed_backfill.ALERT_TYPE_STALLED
    assert decision["title"] == "Embedding backfill stalled"
    assert "min old" in decision["body"]
    assert "abc-123" in decision["body"]
    snap = decision["snapshot"]
    assert snap["metric"] == "embed_backfill_updated_at_age_min"
    assert snap["value"] == 30
    assert snap["actual"] >= 45


@pytest.mark.asyncio
async def test_failing_takes_priority_over_stalled(monkeypatch):
    """When BOTH conditions are true the watcher must emit FAILING (it
    carries actionable leg-level detail; STALLED is a generic timeout)."""
    from aca_jobs import embed_backfill

    monkeypatch.setattr(embed_backfill, "ALERT_FAILED_THRESHOLD", 50, raising=False)
    monkeypatch.setattr(embed_backfill, "ALERT_STALL_MINUTES", 30, raising=False)

    stale_ts = _dt.datetime.utcnow() - _dt.timedelta(minutes=60)
    db = _state_db({
        "_id": embed_backfill.STATE_DOC_ID,
        "running": True,
        "last_run": {"processed": 100, "succeeded": 0, "failed": 100},
        "updated_at": stale_ts,
    })
    decision = await embed_backfill._evaluate_alert_state(db)
    assert decision["alert_type"] == embed_backfill.ALERT_TYPE_FAILING


@pytest.mark.asyncio
async def test_finished_run_with_stale_timestamp_does_not_page(monkeypatch):
    """``running=False`` + stale ``updated_at`` is the steady state when the
    job has finished and is waiting for the next pass — it must NOT page,
    only ``running=True`` with stale ts is a stall."""
    from aca_jobs import embed_backfill

    monkeypatch.setattr(embed_backfill, "ALERT_STALL_MINUTES", 30, raising=False)

    stale_ts = _dt.datetime.utcnow() - _dt.timedelta(hours=12)
    db = _state_db({
        "_id": embed_backfill.STATE_DOC_ID,
        "running": False,
        "last_run": {"processed": 5000, "succeeded": 5000, "failed": 0},
        "updated_at": stale_ts,
    })
    decision = await embed_backfill._evaluate_alert_state(db)
    assert decision["alert_type"] is None
    assert decision["skipped"] == "healthy"


@pytest.mark.asyncio
async def test_failed_threshold_zero_disables_failing_alert(monkeypatch):
    """``EMBED_BACKFILL_ALERT_FAILED_THRESHOLD=0`` disables the failing-leg
    page so ops can quiet a known-bad run without a deploy."""
    from aca_jobs import embed_backfill

    monkeypatch.setattr(embed_backfill, "ALERT_FAILED_THRESHOLD", 0, raising=False)

    db = _state_db({
        "_id": embed_backfill.STATE_DOC_ID,
        "running": False,
        "last_run": {"processed": 100, "succeeded": 0, "failed": 100},
        "updated_at": _dt.datetime.utcnow(),
    })
    decision = await embed_backfill._evaluate_alert_state(db)
    assert decision["alert_type"] is None


@pytest.mark.asyncio
async def test_malformed_updated_at_does_not_false_page(monkeypatch):
    """A non-datetime, non-iso ``updated_at`` MUST be treated as "age
    unknown" and skip the stall page rather than firing a false alarm."""
    from aca_jobs import embed_backfill

    monkeypatch.setattr(embed_backfill, "ALERT_STALL_MINUTES", 30, raising=False)

    db = _state_db({
        "_id": embed_backfill.STATE_DOC_ID,
        "running": True,
        "last_run": {"processed": 0, "failed": 0},
        "updated_at": "not-a-real-timestamp",
    })
    decision = await embed_backfill._evaluate_alert_state(db)
    assert decision["alert_type"] is None


@pytest.mark.asyncio
async def test_updated_at_iso_string_is_parsed(monkeypatch):
    """``updated_at`` written as an ISO-8601 string (Mongo round-trip via
    JSON) must still be parsed for age — otherwise stalls go unnoticed
    after a serializer change."""
    from aca_jobs import embed_backfill

    monkeypatch.setattr(embed_backfill, "ALERT_STALL_MINUTES", 30, raising=False)

    stale_iso = (_dt.datetime.utcnow() - _dt.timedelta(minutes=45)).isoformat()
    db = _state_db({
        "_id": embed_backfill.STATE_DOC_ID,
        "running": True,
        "last_run": {"failed": 0},
        "updated_at": stale_iso,
    })
    decision = await embed_backfill._evaluate_alert_state(db)
    assert decision["alert_type"] == embed_backfill.ALERT_TYPE_STALLED


@pytest.mark.asyncio
async def test_alert_loop_dedups_and_emits_recovery(monkeypatch):
    """Loop-level contract:
      1. Page exactly ONCE per failure run (no spam during a sustained
         outage) — the second iteration with the same alert_type must
         NOT call ``_dispatch_alert`` again.
      2. After a healthy iteration, fire ALERT_TYPE_RECOVERED with
         ``force=True`` so the all-clear isn't silenced by the cooldown
         the failure alert just consumed.
    """
    from aca_jobs import embed_backfill

    monkeypatch.setattr(embed_backfill, "ALERT_LOOP_INTERVAL_S", 0, raising=False)
    monkeypatch.setattr(embed_backfill, "ALERT_STARTUP_DELAY_S", 0, raising=False)
    monkeypatch.setattr(embed_backfill, "ALERT_FAILED_THRESHOLD", 50, raising=False)
    monkeypatch.setattr(embed_backfill, "ALERT_STALL_MINUTES", 30, raising=False)

    failing_state = {
        "_id": embed_backfill.STATE_DOC_ID,
        "running": False,
        "last_run": {"processed": 100, "succeeded": 0, "failed": 100,
                     "skipped": 0, "remaining": 0},
        "updated_at": _dt.datetime.utcnow(),
    }
    healthy_state = {
        "_id": embed_backfill.STATE_DOC_ID,
        "running": False,
        "last_run": {"processed": 100, "succeeded": 100, "failed": 0,
                     "skipped": 0, "remaining": 0},
        "updated_at": _dt.datetime.utcnow(),
    }

    iter_states = [failing_state, failing_state, healthy_state]
    _original_eval = embed_backfill._evaluate_alert_state

    async def _eval_stub(_db):
        # Walk through the scripted timeline; halt the loop after the
        # last scripted iteration so the test doesn't hang.
        if not iter_states:
            raise asyncio.CancelledError()
        s = iter_states.pop(0)
        return await _original_eval(_state_db(s))

    monkeypatch.setattr(embed_backfill, "_evaluate_alert_state", _eval_stub, raising=True)

    dispatches: list[dict] = []

    async def _fake_dispatch(alert_type, title, body, threshold_snapshot=None, force=False):
        dispatches.append({
            "alert_type": alert_type, "title": title, "body": body,
            "snapshot": threshold_snapshot, "force": force,
        })

    import metrics as _metrics
    monkeypatch.setattr(_metrics, "_dispatch_alert", _fake_dispatch, raising=False)

    # Use a no-sleep so the three scripted iterations finish synchronously.
    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(embed_backfill.asyncio, "sleep", _no_sleep, raising=True)

    with pytest.raises(asyncio.CancelledError):
        await embed_backfill.alert_loop(MagicMock())

    # Iteration 1 → FAILING dispatch.
    # Iteration 2 → SAME failing alert_type + alerted_for_run set; MUST be deduped.
    # Iteration 3 → healthy → RECOVERED dispatch (force=True).
    types = [d["alert_type"] for d in dispatches]
    assert types == [
        embed_backfill.ALERT_TYPE_FAILING,
        embed_backfill.ALERT_TYPE_RECOVERED,
    ], f"expected [FAILING, RECOVERED] but got {types!r}"
    recovery = dispatches[-1]
    assert recovery["force"] is True, (
        "recovery dispatch must use force=True to bypass the failure-alert "
        "cooldown that just consumed the same on-call channel"
    )
