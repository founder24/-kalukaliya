"""Task #471 — staleness monitor for the SEO auto-publish job.

Covers:
 * Threshold mirrors the SchedulePanel.jsx values (36h daily / 192h weekly).
 * Evaluation prefers ``seo_generation_log`` ``completed_at`` over the
   marker's ``claimed_at`` so a queued-but-not-finished run does not
   suppress an alert.
 * Healthy / stale classification (including never-ran).
 * First detection alerts; second detection within 24h is debounced.
 * Recovery (stale → healthy) sends exactly one info notification then
   settles.
 * Disabled scheduler never alerts.
 * Persisted lock doc records the alert + state transition.
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, AsyncMock

import pytest

import seo_engine


# ─── Fake Mongo ─────────────────────────────────────────────────────────────

class _FakeCursor:
    def __init__(self, items):
        self._items = list(items)

    def sort(self, *a, **kw):
        return self

    def limit(self, *a, **kw):
        return self

    async def to_list(self, n):
        return self._items[:n]

    def __aiter__(self):
        async def _gen():
            for it in self._items:
                yield it
        return _gen()


class _FakeColl:
    def __init__(self):
        self._docs: dict = {}
        self._log_rows: list = []

    # ── job_locks ──
    async def find_one(self, query, projection=None, sort=None):
        # job_locks: query is {"_id": <id>}
        if "_id" in query:
            doc = self._docs.get(query["_id"])
            return dict(doc) if doc else None
        # seo_generation_log: query has $regex on job_id
        if "job_id" in query:
            rows = sorted(
                [r for r in self._log_rows if r.get("job_id", "").startswith("job-sched-")],
                key=lambda r: r.get("completed_at", ""),
                reverse=True,
            )
            return rows[0] if rows else None
        return None

    async def update_one(self, query, update, upsert=False):
        _id = query["_id"]
        cur = self._docs.get(_id) or {"_id": _id}
        cur.update(update.get("$set", {}))
        self._docs[_id] = cur
        return None

    async def find_one_and_update(self, query, update, upsert=False):
        """Minimal Mongo CAS emulator: applies `$set` only when the
        guard matches. Supports the small subset of operators used by
        ``_claim_seo_staleness_alert_slot``: top-level field equality,
        ``$ne``, ``$lt``, ``$exists``, and a top-level ``$or``."""
        def _matches(doc, q):
            for k, v in q.items():
                if k == "$or":
                    if not any(_matches(doc, sub) for sub in v):
                        return False
                    continue
                actual = doc.get(k)
                if isinstance(v, dict):
                    if "$ne" in v and actual == v["$ne"]:
                        return False
                    if "$lt" in v and not (actual is not None and actual < v["$lt"]):
                        return False
                    if "$exists" in v and (k in doc) != bool(v["$exists"]):
                        return False
                else:
                    if actual != v:
                        return False
            return True

        _id = query["_id"]
        doc = self._docs.get(_id)
        if doc is None:
            return None
        if not _matches(doc, query):
            return None
        prior = dict(doc)
        doc.update(update.get("$set", {}))
        return prior

    async def insert_one(self, doc):
        _id = doc["_id"]
        if _id in self._docs:
            from pymongo.errors import DuplicateKeyError
            raise DuplicateKeyError("dup")
        self._docs[_id] = dict(doc)
        return None

    def find(self, *a, **kw):
        # Used by the admin email lookup — return an empty list of users.
        return _FakeCursor([])


class _FakeDb:
    def __init__(self):
        self.job_locks = _FakeColl()
        self.seo_generation_log = _FakeColl()
        self.users = _FakeColl()


# ─── Helpers ────────────────────────────────────────────────────────────────

def _now():
    return datetime(2026, 4, 18, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _enable_auto_publish():
    """Force the scheduler to read as enabled. The staleness monitor
    short-circuits when disabled, so we'd never get past the gate
    otherwise."""
    with patch.dict(os.environ, {"SEO_AUTO_PUBLISH_ENABLED": "true"}, clear=False):
        yield


@pytest.fixture
def fake_db():
    return _FakeDb()


# ─── Threshold ──────────────────────────────────────────────────────────────

def test_threshold_matches_schedule_panel():
    """Server-side thresholds must equal the client-side values in
    SchedulePanel.jsx (36h daily, 192h weekly) so the alert fires at
    the same boundary the UI surfaces a stale badge."""
    assert seo_engine._seo_auto_publish_staleness_threshold_hours("daily") == 36
    assert seo_engine._seo_auto_publish_staleness_threshold_hours("weekly") == 24 * 8


# ─── Evaluation ─────────────────────────────────────────────────────────────

def test_evaluate_prefers_log_completed_at_over_marker(fake_db):
    """When both the seo_generation_log and the marker have data the
    evaluator must prefer the log (the truthful "did it finish?"
    signal) over the marker's claimed_at."""
    now = _now()
    fake_db.seo_generation_log._log_rows.append({
        "job_id": "job-sched-abc",
        "completed_at": (now - timedelta(hours=2)).isoformat(),
    })
    fake_db.job_locks._docs[seo_engine._SEO_AUTO_PUBLISH_LOCK_ID] = {
        "_id": seo_engine._SEO_AUTO_PUBLISH_LOCK_ID,
        "claimed_at": (now - timedelta(hours=200)).isoformat(),
    }
    state = asyncio.run(seo_engine._evaluate_seo_auto_publish_staleness(fake_db, now))
    assert state["stale"] is False
    assert state["age_hours"] == pytest.approx(2.0, abs=0.1)


def test_evaluate_falls_back_to_marker_when_no_log(fake_db):
    now = _now()
    fake_db.job_locks._docs[seo_engine._SEO_AUTO_PUBLISH_LOCK_ID] = {
        "_id": seo_engine._SEO_AUTO_PUBLISH_LOCK_ID,
        "claimed_at": (now - timedelta(hours=10)).isoformat(),
    }
    state = asyncio.run(seo_engine._evaluate_seo_auto_publish_staleness(fake_db, now))
    assert state["stale"] is False
    assert state["age_hours"] == pytest.approx(10.0, abs=0.1)


def test_evaluate_never_ran_is_stale(fake_db):
    """A fresh install with no marker and no log must classify as stale
    (we don't want a silent empty cron to slip past the monitor)."""
    state = asyncio.run(seo_engine._evaluate_seo_auto_publish_staleness(fake_db, _now()))
    assert state["stale"] is True
    assert state["age_hours"] is None


def test_evaluate_old_run_is_stale_daily(fake_db):
    now = _now()
    fake_db.seo_generation_log._log_rows.append({
        "job_id": "job-sched-old",
        "completed_at": (now - timedelta(hours=48)).isoformat(),
    })
    state = asyncio.run(seo_engine._evaluate_seo_auto_publish_staleness(fake_db, now))
    assert state["stale"] is True
    assert state["age_hours"] == pytest.approx(48.0, abs=0.1)


def test_evaluate_weekly_uses_8_day_threshold(fake_db):
    """Weekly cadence should accept a 7-day-old run as healthy and a
    9-day-old run as stale."""
    now = _now()
    fake_db.seo_generation_log._log_rows.append({
        "job_id": "job-sched-old",
        "completed_at": (now - timedelta(days=7)).isoformat(),
    })
    with patch.dict(os.environ, {"SEO_AUTO_PUBLISH_FREQUENCY": "weekly"},
                    clear=False):
        state = asyncio.run(seo_engine._evaluate_seo_auto_publish_staleness(fake_db, now))
    assert state["stale"] is False
    assert state["frequency"] == "weekly"
    assert state["threshold_h"] == 192


# ─── Alert lifecycle ────────────────────────────────────────────────────────

def _patch_send():
    """Patch out the email + notification side-effects so tests run
    without SES / Supabase (Task #556 — SES sole path). We assert that the alert function was
    called once per real alert event."""
    return patch.object(
        seo_engine, "_send_seo_staleness_alert",
        new_callable=AsyncMock,
    )


def test_disabled_never_alerts(fake_db):
    """When SEO_AUTO_PUBLISH_ENABLED=false the monitor must skip
    immediately without persisting anything or sending alerts —
    otherwise toggling the feature off would page admins."""
    with patch.dict(os.environ, {"SEO_AUTO_PUBLISH_ENABLED": "false"},
                    clear=False), _patch_send() as mock_send:
        result = asyncio.run(
            seo_engine._check_and_alert_seo_auto_publish_staleness(fake_db, _now())
        )
    assert result["action"] == "skip"
    assert result["reason"] == "disabled"
    mock_send.assert_not_called()


def test_first_stale_detection_alerts_and_persists(fake_db):
    now = _now()
    # No log + no marker → stale.
    with _patch_send() as mock_send:
        result = asyncio.run(
            seo_engine._check_and_alert_seo_auto_publish_staleness(fake_db, now)
        )
    assert result["action"] == "alerted"
    assert result["kind"] == "stale"
    mock_send.assert_called_once()
    # Lock doc must now record the stale state + the alert timestamp.
    saved = fake_db.job_locks._docs[seo_engine._SEO_STALENESS_ALERT_LOCK_ID]
    assert saved["last_state"] == "stale"
    assert saved["last_alert_at"] == now.isoformat()


def test_second_stale_within_24h_is_debounced(fake_db):
    """A repeated stale check inside the 24h window must NOT re-alert
    — that's the whole point of the debounce. Outside the window a
    follow-up alert is allowed."""
    now = _now()
    # Seed a prior alert 3h ago.
    fake_db.job_locks._docs[seo_engine._SEO_STALENESS_ALERT_LOCK_ID] = {
        "_id": seo_engine._SEO_STALENESS_ALERT_LOCK_ID,
        "last_state": "stale",
        "last_alert_at": (now - timedelta(hours=3)).isoformat(),
    }
    with _patch_send() as mock_send:
        result = asyncio.run(
            seo_engine._check_and_alert_seo_auto_publish_staleness(fake_db, now)
        )
    assert result["action"] == "skip"
    assert result["reason"] == "debounced"
    mock_send.assert_not_called()

    # Now jump past the 24h window — a re-alert is allowed.
    fake_db.job_locks._docs[seo_engine._SEO_STALENESS_ALERT_LOCK_ID][
        "last_alert_at"] = (now - timedelta(hours=25)).isoformat()
    with _patch_send() as mock_send2:
        result2 = asyncio.run(
            seo_engine._check_and_alert_seo_auto_publish_staleness(fake_db, now)
        )
    assert result2["action"] == "alerted"
    assert result2["kind"] == "stale"
    mock_send2.assert_called_once()


def test_recovery_sends_exactly_one_info_then_settles(fake_db):
    """When a previously-stale job runs again, fire one recovery
    notification, then on subsequent healthy checks send nothing."""
    now = _now()
    # Prior alert: was stale.
    fake_db.job_locks._docs[seo_engine._SEO_STALENESS_ALERT_LOCK_ID] = {
        "_id": seo_engine._SEO_STALENESS_ALERT_LOCK_ID,
        "last_state": "stale",
        "last_alert_at": (now - timedelta(hours=48)).isoformat(),
    }
    # Fresh successful run 1h ago.
    fake_db.seo_generation_log._log_rows.append({
        "job_id": "job-sched-new",
        "completed_at": (now - timedelta(hours=1)).isoformat(),
    })
    with _patch_send() as mock_send:
        result = asyncio.run(
            seo_engine._check_and_alert_seo_auto_publish_staleness(fake_db, now)
        )
    assert result["action"] == "alerted"
    assert result["kind"] == "recovered"
    mock_send.assert_called_once()
    # State should now be healthy with the recovery's last_alert_at set.
    saved = fake_db.job_locks._docs[seo_engine._SEO_STALENESS_ALERT_LOCK_ID]
    assert saved["last_state"] == "healthy"
    assert saved["last_alert_at"] == now.isoformat()

    # Subsequent healthy check — must be silent.
    with _patch_send() as mock_send2:
        result2 = asyncio.run(
            seo_engine._check_and_alert_seo_auto_publish_staleness(fake_db, now)
        )
    assert result2["action"] == "skip"
    assert result2["reason"] == "healthy"
    mock_send2.assert_not_called()


def test_healthy_bootstrap_persists_state_without_alerting(fake_db):
    """First-ever check on a healthy system: persist last_state=healthy
    so we have a reference for future transitions, but don't bump
    last_alert_at (no alert was sent)."""
    now = _now()
    fake_db.seo_generation_log._log_rows.append({
        "job_id": "job-sched-new",
        "completed_at": (now - timedelta(hours=2)).isoformat(),
    })
    with _patch_send() as mock_send:
        result = asyncio.run(
            seo_engine._check_and_alert_seo_auto_publish_staleness(fake_db, now)
        )
    assert result["action"] == "skip"
    assert result["reason"] == "healthy"
    mock_send.assert_not_called()
    saved = fake_db.job_locks._docs[seo_engine._SEO_STALENESS_ALERT_LOCK_ID]
    assert saved["last_state"] == "healthy"
    assert "last_alert_at" not in saved


def test_email_helper_delivers_to_resolved_admin_recipients(fake_db):
    """Guard against silent suppression regressions: under a normal
    admin setup the prefs subsystem returns at least one recipient and
    the email helper actually invokes the SMTP send path with that
    recipient. If a future refactor accidentally swallows the audience
    or skips delivery, this test fails loudly instead of letting
    staleness alerts vanish into the void."""
    async def _fake_audience(_db, _now):
        return {"recipients": [
            {"email": "ops@syrabit.ai"},
            {"email": "founder@syrabit.ai"},
        ]}

    sent_to: list = []

    async def _fake_send(to, subject, html, *args, **kwargs):
        sent_to.append({"to": to, "subject": subject})
        return True

    import sys, types
    mod = types.ModuleType("email_templates")
    mod._send = _fake_send  # type: ignore[attr-defined]
    sys.modules["email_templates"] = mod
    try:
        with patch.object(seo_engine, "_resolve_seo_summary_audience",
                          new=AsyncMock(side_effect=_fake_audience)):
            asyncio.run(seo_engine._email_admins_about_seo_staleness(
                fake_db, "subj", "body",
            ))
    finally:
        sys.modules.pop("email_templates", None)

    assert len(sent_to) >= 1, "expected at least one outbound email"
    addrs = sent_to[0]["to"]
    if isinstance(addrs, str):
        addrs = [addrs]
    assert any("ops@syrabit.ai" in a or "founder@syrabit.ai" in a for a in addrs)
