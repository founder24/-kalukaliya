"""Task #940 — drift detector + alerter contract tests.

Two surfaces locked down:

  1. ``compute_drift(prev, cur)`` — must surface signal regressions,
     improvements, claim-level Wikidata removals, and per-summary
     numeric WoW deltas; must NOT page on the very first snapshot.

  2. ``_maybe_dispatch_drift_alert(db, snapshot, drift)`` — must:
       * page when there are regressions and no debounce is active,
       * skip when the same fingerprint pages within the 24 h window,
       * page again when the fingerprint *changes* even inside debounce,
       * clear the lock-doc on recovery (no regressions).
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List, Optional
from unittest.mock import patch, AsyncMock

import pytest

import entity_seo_health as esh


def _run(coro):
    # Task #284 — fresh loop per call (Python 3.11+ no longer auto-creates
    # a main-thread loop on demand).
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ─── compute_drift ────────────────────────────────────────────────────


def _snap(*, status="ok", wikidata_status="ok", claims=None, missing=0,
           sameas_broken=0):
    return {
        "iso_week": "2026-W17",
        "aggregate_status": status,
        "signals": {
            "wikidata": {"status": wikidata_status, "summary": "wd",
                         "fields": {"present_claims": claims or ["P31", "P17"]}},
            "wikipedia": {"status": "ok", "summary": "wp", "fields": {}},
            "crunchbase": {"status": "ok", "summary": "cb", "fields": {}},
            "sameas": {"status": "ok", "summary": "sa", "fields": {}},
            "google_kg": {"status": "ok", "summary": "kg", "fields": {}},
        },
        "summary": {
            "wikidata_claims": len(claims or ["P31", "P17"]),
            "wikidata_missing": missing,
            "sameas_broken": sameas_broken,
        },
    }


def test_compute_drift_no_baseline_yields_no_regressions():
    drift = esh.compute_drift(None, _snap())
    assert drift["had_baseline"] is False
    assert drift["regressions"] == []
    assert drift["improvements"] == []


def test_compute_drift_signal_regression_recorded():
    prev = _snap(wikidata_status="ok")
    cur = _snap(wikidata_status="missing")
    drift = esh.compute_drift(prev, cur)
    names = [r["name"] for r in drift["regressions"]]
    assert "wikidata" in names
    # Improvement list must stay empty when nothing improved.
    assert drift["improvements"] == []


def test_compute_drift_recovery_recorded_as_improvement():
    prev = _snap(wikidata_status="missing")
    cur = _snap(wikidata_status="ok")
    drift = esh.compute_drift(prev, cur)
    names = [i["name"] for i in drift["improvements"]]
    assert "wikidata" in names
    assert drift["regressions"] == []


def test_compute_drift_claim_removed_surfaces_subordinate_regression():
    prev = _snap(claims=["P31", "P17", "P856"])
    cur = _snap(claims=["P31", "P17"])  # P856 removed by another editor
    drift = esh.compute_drift(prev, cur)
    rems = [r for r in drift["regressions"] if r["name"] == "wikidata_claims_removed"]
    assert rems and "P856" in rems[0]["removed_props"]


def test_compute_drift_suppresses_claim_removed_when_wikidata_unhealthy():
    """A transient outage (Wikidata 404) must not be misreported as
    every-claim-removed. Headline ``wikidata`` regression is enough."""
    prev = _snap(wikidata_status="ok",      claims=["P31", "P17", "P856"])
    cur  = _snap(wikidata_status="missing", claims=[])  # outage emptied list
    drift = esh.compute_drift(prev, cur)
    names = [r["name"] for r in drift["regressions"]]
    # Headline regression on the signal itself is fine.
    assert "wikidata" in names
    # But the per-claim subordinate regression must NOT fire.
    assert "wikidata_claims_removed" not in names


def test_compute_drift_suppresses_claim_removed_when_wikidata_errored():
    prev = _snap(wikidata_status="ok",    claims=["P31", "P17"])
    cur  = _snap(wikidata_status="error", claims=[])
    drift = esh.compute_drift(prev, cur)
    names = [r["name"] for r in drift["regressions"]]
    assert "wikidata_claims_removed" not in names


def test_compute_drift_summary_deltas():
    prev = _snap(missing=2, sameas_broken=0)
    cur = _snap(missing=4, sameas_broken=1)
    drift = esh.compute_drift(prev, cur)
    deltas = drift["summary_deltas"]
    assert deltas["wikidata_missing"]["delta"] == 2
    assert deltas["sameas_broken"]["delta"] == 1


# ─── alerter (mock db + metrics) ──────────────────────────────────────


class _FakeJobLocks:
    def __init__(self):
        self.docs: Dict[str, Dict[str, Any]] = {}

    async def find_one(self, q):
        _id = q.get("_id")
        return dict(self.docs.get(_id) or {}) or None

    async def update_one(self, q, update, upsert=False):
        _id = q.get("_id")
        cur = self.docs.get(_id) or {}
        if "$set" in update:
            cur.update(update["$set"])
        if upsert or _id in self.docs:
            self.docs[_id] = cur
        return None

    async def delete_one(self, q):
        self.docs.pop(q.get("_id"), None)


class _FakeDb:
    def __init__(self):
        self.job_locks = _FakeJobLocks()


@pytest.fixture
def db():
    return _FakeDb()


@pytest.fixture(autouse=True)
def _reset_alert_cooldown():
    # Make sure the in-process per-type cooldown from metrics doesn't
    # leak between cases — each test is its own scenario.
    try:
        from metrics import _alert_last_fired
        _alert_last_fired.pop(esh._ALERT_TYPE, None)
        yield
        _alert_last_fired.pop(esh._ALERT_TYPE, None)
    except Exception:
        yield


def _drift(regressions: List[Dict[str, Any]]):
    return {"had_baseline": True, "regressions": regressions,
            "improvements": [], "summary_deltas": {}}


def _snapshot():
    return {"aggregate_status": "degraded",
            "summary": {"wikidata_claims": 6}, "signals": {}}


def test_alert_pages_when_regressions_and_no_prior_lock(db):
    drift = _drift([{"name": "wikidata", "from": "ok", "to": "missing", "summary": "s"}])
    sent = []
    async def _fake_dispatch(*args, **kwargs):
        sent.append((args, kwargs))
    with patch("metrics._dispatch_alert", new=_fake_dispatch):
        paged = _run(esh._maybe_dispatch_drift_alert(db, _snapshot(), drift))
    assert paged is True
    assert len(sent) == 1
    # Lock-doc records the fingerprint so the next call within debounce skips.
    lock = db.job_locks.docs[esh._ALERT_LOCK_ID]
    assert lock["fingerprint"] == "wikidata"
    assert lock["regression_count"] == 1


def test_alert_skipped_when_same_fingerprint_inside_debounce(db):
    db.job_locks.docs[esh._ALERT_LOCK_ID] = {
        "fingerprint": "wikidata",
        "last_paged_at_epoch": time.time() - 60,  # 1 min ago
        "regression_count": 1,
    }
    drift = _drift([{"name": "wikidata", "from": "ok", "to": "missing", "summary": "s"}])
    sent = []
    async def _fake_dispatch(*args, **kwargs):
        sent.append((args, kwargs))
    with patch("metrics._dispatch_alert", new=_fake_dispatch):
        paged = _run(esh._maybe_dispatch_drift_alert(db, _snapshot(), drift))
    assert paged is False
    assert sent == []


def test_alert_repages_when_fingerprint_changes_even_inside_debounce(db):
    db.job_locks.docs[esh._ALERT_LOCK_ID] = {
        "fingerprint": "wikidata",
        "last_paged_at_epoch": time.time() - 60,
        "regression_count": 1,
    }
    drift = _drift([
        {"name": "wikidata", "from": "ok", "to": "missing", "summary": "s"},
        {"name": "sameas",   "from": "ok", "to": "missing", "summary": "s"},
    ])
    sent = []
    async def _fake_dispatch(*args, **kwargs):
        sent.append((args, kwargs))
    with patch("metrics._dispatch_alert", new=_fake_dispatch):
        paged = _run(esh._maybe_dispatch_drift_alert(db, _snapshot(), drift))
    assert paged is True
    assert sent and len(sent) == 1
    # New fingerprint persisted so subsequent same-set calls debounce.
    assert db.job_locks.docs[esh._ALERT_LOCK_ID]["fingerprint"] == "sameas,wikidata"


def test_alert_clears_lock_on_recovery(db):
    db.job_locks.docs[esh._ALERT_LOCK_ID] = {
        "fingerprint": "wikidata",
        "last_paged_at_epoch": time.time() - 60,
    }
    drift = _drift([])  # No regressions = recovery
    sent = []
    async def _fake_dispatch(*args, **kwargs):
        sent.append((args, kwargs))
    with patch("metrics._dispatch_alert", new=_fake_dispatch):
        paged = _run(esh._maybe_dispatch_drift_alert(db, _snapshot(), drift))
    assert paged is False
    assert sent == []
    # Lock cleared so the next regression is treated as a fresh page.
    assert esh._ALERT_LOCK_ID not in db.job_locks.docs


# ─── window-gate ──────────────────────────────────────────────────────


def test_should_run_entity_seo_now_inside_window():
    from datetime import datetime, timezone
    now = datetime(2026, 4, 27, 4, 30, tzinfo=timezone.utc)  # Mon 04:30 UTC
    assert esh._should_run_entity_seo_now(now, "") is True
    # Same week → dedup.
    assert esh._should_run_entity_seo_now(now, esh._iso_week_tag(now)) is False


def test_should_run_entity_seo_now_outside_window():
    from datetime import datetime, timezone
    # Tuesday 04:30 UTC
    now = datetime(2026, 4, 28, 4, 30, tzinfo=timezone.utc)
    assert esh._should_run_entity_seo_now(now, "") is False
    # Monday 03:00 UTC — too early
    early = datetime(2026, 4, 27, 3, 0, tzinfo=timezone.utc)
    assert esh._should_run_entity_seo_now(early, "") is False


# ─── catch-up gate: window must have already passed ────────────────────


def test_window_has_passed_this_week():
    from datetime import datetime, timezone
    # Monday 03:00 UTC → window 04:30±15 has not passed yet.
    early = datetime(2026, 4, 27, 3, 0, tzinfo=timezone.utc)
    assert esh._window_has_passed_this_week(early) is False
    # Monday 04:50 UTC → window (closes 04:45) has passed.
    after = datetime(2026, 4, 27, 4, 50, tzinfo=timezone.utc)
    assert esh._window_has_passed_this_week(after) is True
    # Wednesday 12:00 UTC → window obviously passed.
    later = datetime(2026, 4, 29, 12, 0, tzinfo=timezone.utc)
    assert esh._window_has_passed_this_week(later) is True


def test_catchup_refuses_to_run_before_the_window_passes(db):
    """Belt-and-braces: if a pod boots Sun 23:00 UTC or Mon 03:00 UTC,
    catch-up must NOT pre-empt the contracted Mon 04:30 run."""
    from datetime import datetime, timezone
    early = datetime(2026, 4, 27, 3, 0, tzinfo=timezone.utc)

    class _DbWithCollection:
        def __init__(self):
            self.job_locks = _FakeJobLocks()
            self._coll_calls = []

        def __getitem__(self, name):
            # Spy on collection access so we can assert no Mongo lookup
            # happened — catch-up should bail before doing any I/O.
            self._coll_calls.append(name)
            class _Stub:
                async def find_one(self, *_a, **_kw):
                    return None
            return _Stub()

    spy_db = _DbWithCollection()
    res = _run(esh._entity_seo_catchup_if_missed(spy_db, early))
    assert res == {"ran": False, "reason": "window_not_yet_passed"}
    # The collection lookup must not have happened.
    assert spy_db._coll_calls == []
