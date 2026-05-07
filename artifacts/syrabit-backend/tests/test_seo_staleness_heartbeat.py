"""Task #491 — liveness heartbeat for the staleness monitor itself.

Task #485 surfaced the monitor's ``updated_at`` in the Schedule panel so
admins who happen to look can confirm the monitor is alive. This module
automates that check: every 6h, verify the lock doc bumped its heartbeat
within the last ~3h (2x the monitor's 1h loop interval) and page admins
exactly once if it hasn't. Same CAS + debounce + recovery shape as the
parent staleness alert (Task #471) and the CI alert (Task #484).

Covers:
 * never-ran (no doc) → silent (we can't distinguish "monitor down" from
   "monitor never started"; bootstrap silence is the safer default).
 * recently-updated (~10 min ago) → silent.
 * significantly-behind (>3h) → alert + persist down state.
 * repeated-behind inside the debounce window → silent (single page).
 * recovery (heartbeat catches up) → exactly one info notification.
 * the monitor's own iteration bumps ``updated_at`` so the heartbeat
   watcher has a real liveness signal even during steady-state healthy.
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, AsyncMock

import pytest

import seo_engine


# ─── Fake Mongo (mirrors test_seo_auto_publish_staleness.py) ────────────────

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

    async def find_one(self, query, projection=None, sort=None):
        if "_id" in query:
            doc = self._docs.get(query["_id"])
            return dict(doc) if doc else None
        if "job_id" in query:
            rows = sorted(
                [r for r in self._log_rows
                 if r.get("job_id", "").startswith("job-sched-")],
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
        """Minimal Mongo CAS emulator — same operator subset as the
        parent test module: top-level equality, ``$ne``, ``$lt``,
        ``$exists``, top-level ``$or``."""
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
                    if "$lt" in v and not (actual is not None
                                           and actual < v["$lt"]):
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
        return _FakeCursor([])


class _FakeDb:
    def __init__(self):
        self.job_locks = _FakeColl()
        self.seo_generation_log = _FakeColl()
        self.users = _FakeColl()


def _now():
    return datetime(2026, 4, 18, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def fake_db():
    return _FakeDb()


def _patch_send():
    """Stub out the alert side-effects (notification + email + audit
    log) so tests don't need SES / Supabase (Task #556). We assert call counts
    on the stub to verify exactly-once paging."""
    return patch.object(
        seo_engine, "_send_staleness_heartbeat_alert",
        new_callable=AsyncMock,
    )


# ─── never-ran (no monitor doc) ─────────────────────────────────────────────

def test_never_ran_no_monitor_doc_is_silent(fake_db):
    """A fresh install has no staleness lock doc yet — could mean the
    monitor is down OR the monitor simply hasn't completed its first
    iteration after warmup. We must NOT page on cold start; the parent
    monitor's first iteration will create the doc and unblock us."""
    with _patch_send() as mock_send:
        result = asyncio.run(
            seo_engine._check_and_alert_staleness_heartbeat(fake_db, _now())
        )
    assert result["action"] == "skip"
    assert result["reason"] == "monitor_never_ran"
    mock_send.assert_not_called()
    # No heartbeat-alert lock doc should be created either — we have
    # nothing to remember yet.
    assert seo_engine._SEO_STALENESS_HEARTBEAT_ALERT_LOCK_ID not in \
        fake_db.job_locks._docs


# ─── recently-updated (silent) ──────────────────────────────────────────────

def test_recently_updated_heartbeat_is_silent(fake_db):
    """Heartbeat bumped 10 min ago — well inside the 3h threshold.
    Watcher must skip silently and NOT touch the heartbeat-alert doc."""
    now = _now()
    fake_db.job_locks._docs[seo_engine._SEO_STALENESS_ALERT_LOCK_ID] = {
        "_id": seo_engine._SEO_STALENESS_ALERT_LOCK_ID,
        "last_state": "healthy",
        "updated_at": (now - timedelta(minutes=10)).isoformat(),
    }
    with _patch_send() as mock_send:
        result = asyncio.run(
            seo_engine._check_and_alert_staleness_heartbeat(fake_db, now)
        )
    assert result["action"] == "skip"
    assert result["reason"] == "healthy"
    assert result["age_h"] == pytest.approx(10 / 60.0, abs=0.01)
    mock_send.assert_not_called()
    assert seo_engine._SEO_STALENESS_HEARTBEAT_ALERT_LOCK_ID not in \
        fake_db.job_locks._docs


# ─── significantly-behind (alert) ───────────────────────────────────────────

def test_significantly_behind_heartbeat_alerts_and_persists(fake_db):
    """Heartbeat last bumped 5h ago — past the 3h threshold. First
    detection must page admins exactly once and persist a ``down``
    state on the heartbeat-alert lock doc."""
    now = _now()
    fake_db.job_locks._docs[seo_engine._SEO_STALENESS_ALERT_LOCK_ID] = {
        "_id": seo_engine._SEO_STALENESS_ALERT_LOCK_ID,
        "last_state": "healthy",
        "updated_at": (now - timedelta(hours=5)).isoformat(),
    }
    with _patch_send() as mock_send:
        result = asyncio.run(
            seo_engine._check_and_alert_staleness_heartbeat(fake_db, now)
        )
    assert result["action"] == "alerted"
    assert result["kind"] == "down"
    assert result["age_h"] == pytest.approx(5.0, abs=0.01)
    mock_send.assert_called_once()
    # Heartbeat-alert lock doc records the down state + alert time so
    # subsequent iterations can debounce against it.
    saved = fake_db.job_locks._docs[
        seo_engine._SEO_STALENESS_HEARTBEAT_ALERT_LOCK_ID]
    assert saved["last_state"] == "down"
    assert saved["last_alert_at"] == now.isoformat()


# ─── repeated-behind (debounced) ────────────────────────────────────────────

def test_repeated_behind_inside_window_is_debounced(fake_db):
    """Heartbeat is still behind on the next iteration (4h after the
    last alert) — must NOT re-page. Outside the 12h debounce window a
    follow-up page is allowed (we want to nag but not spam)."""
    now = _now()
    fake_db.job_locks._docs[seo_engine._SEO_STALENESS_ALERT_LOCK_ID] = {
        "_id": seo_engine._SEO_STALENESS_ALERT_LOCK_ID,
        "last_state": "healthy",
        "updated_at": (now - timedelta(hours=8)).isoformat(),
    }
    fake_db.job_locks._docs[
        seo_engine._SEO_STALENESS_HEARTBEAT_ALERT_LOCK_ID] = {
        "_id": seo_engine._SEO_STALENESS_HEARTBEAT_ALERT_LOCK_ID,
        "last_state": "down",
        "last_alert_at": (now - timedelta(hours=4)).isoformat(),
    }
    with _patch_send() as mock_send:
        result = asyncio.run(
            seo_engine._check_and_alert_staleness_heartbeat(fake_db, now)
        )
    assert result["action"] == "skip"
    assert result["reason"] == "debounced"
    mock_send.assert_not_called()

    # Now jump past the 12h debounce window — a re-page is allowed.
    fake_db.job_locks._docs[
        seo_engine._SEO_STALENESS_HEARTBEAT_ALERT_LOCK_ID][
        "last_alert_at"] = (now - timedelta(hours=13)).isoformat()
    with _patch_send() as mock_send2:
        result2 = asyncio.run(
            seo_engine._check_and_alert_staleness_heartbeat(fake_db, now)
        )
    assert result2["action"] == "alerted"
    assert result2["kind"] == "down"
    mock_send2.assert_called_once()


# ─── recovery ───────────────────────────────────────────────────────────────

def test_recovery_sends_one_info_then_settles(fake_db):
    """When a previously-down monitor heartbeats again, fire exactly
    one recovery notification and then stay silent on subsequent
    healthy iterations."""
    now = _now()
    fake_db.job_locks._docs[seo_engine._SEO_STALENESS_ALERT_LOCK_ID] = {
        "_id": seo_engine._SEO_STALENESS_ALERT_LOCK_ID,
        "last_state": "healthy",
        "updated_at": (now - timedelta(minutes=5)).isoformat(),
    }
    fake_db.job_locks._docs[
        seo_engine._SEO_STALENESS_HEARTBEAT_ALERT_LOCK_ID] = {
        "_id": seo_engine._SEO_STALENESS_HEARTBEAT_ALERT_LOCK_ID,
        "last_state": "down",
        "last_alert_at": (now - timedelta(hours=20)).isoformat(),
    }
    with _patch_send() as mock_send:
        result = asyncio.run(
            seo_engine._check_and_alert_staleness_heartbeat(fake_db, now)
        )
    assert result["action"] == "alerted"
    assert result["kind"] == "recovered"
    mock_send.assert_called_once()
    saved = fake_db.job_locks._docs[
        seo_engine._SEO_STALENESS_HEARTBEAT_ALERT_LOCK_ID]
    assert saved["last_state"] == "healthy"
    assert saved["last_alert_at"] == now.isoformat()

    # Subsequent healthy check — must be silent.
    with _patch_send() as mock_send2:
        result2 = asyncio.run(
            seo_engine._check_and_alert_staleness_heartbeat(fake_db, now)
        )
    assert result2["action"] == "skip"
    assert result2["reason"] == "healthy"
    mock_send2.assert_not_called()


# ─── monitor iteration bumps the heartbeat ──────────────────────────────────

def test_monitor_iteration_bumps_heartbeat_even_when_disabled(fake_db):
    """The bump must run on EVERY monitor iteration (including the
    disabled branch). Otherwise toggling SEO_AUTO_PUBLISH_ENABLED off
    would freeze ``updated_at`` and trigger a false heartbeat alert
    a few hours later."""
    now = _now()
    with patch.dict(os.environ, {"SEO_AUTO_PUBLISH_ENABLED": "false"},
                    clear=False):
        result = asyncio.run(
            seo_engine._check_and_alert_seo_auto_publish_staleness(
                fake_db, now)
        )
    assert result["reason"] == "disabled"
    saved = fake_db.job_locks._docs[seo_engine._SEO_STALENESS_ALERT_LOCK_ID]
    assert saved["updated_at"] == now.isoformat()


def test_monitor_iteration_bump_does_not_clobber_state(fake_db):
    """The bump uses ``$set`` only on ``updated_at`` so a steady-state
    healthy iteration cannot accidentally erase a prior ``stale`` /
    ``last_alert_at`` written by the alert path. (Critical: a clobber
    here would break the parent monitor's debounce.)"""
    now = _now()
    fake_db.job_locks._docs[seo_engine._SEO_STALENESS_ALERT_LOCK_ID] = {
        "_id": seo_engine._SEO_STALENESS_ALERT_LOCK_ID,
        "last_state": "stale",
        "last_alert_at": (now - timedelta(hours=2)).isoformat(),
        "last_run_at_observed": (now - timedelta(hours=50)).isoformat(),
        "updated_at": (now - timedelta(hours=2)).isoformat(),
    }
    asyncio.run(seo_engine._bump_staleness_monitor_heartbeat(fake_db, now))
    saved = fake_db.job_locks._docs[seo_engine._SEO_STALENESS_ALERT_LOCK_ID]
    assert saved["last_state"] == "stale"
    assert saved["last_alert_at"] == (now - timedelta(hours=2)).isoformat()
    assert saved["last_run_at_observed"] == \
        (now - timedelta(hours=50)).isoformat()
    assert saved["updated_at"] == now.isoformat()
