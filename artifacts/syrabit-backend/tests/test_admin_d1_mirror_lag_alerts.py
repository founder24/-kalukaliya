"""Task #460 — watchdog for the D1 mirror nightly safety net.

Pins:
* classification (breached / healthy / unknown) including the
  ``D1_MIRROR_ON``-off and never-observed branches;
* the admin pill endpoint reduces the snapshot into a status string
  with ``not_enabled`` / ``never_observed`` / ``breached`` / ``healthy``;
* lag is computed as the age of the more-recent of the in-process
  ``last_sync_ts`` and the cross-replica ``d1_sync_nightly_lease.
  last_fired_at`` so a freshly-promoted leader doesn't false-positive;
* required-streak grace window: a single breach observation does NOT
  page until ``D1_MIRROR_LAG_REQUIRED_STREAK`` consecutive checks have
  classified as breached;
* first breach detection past the streak alerts and persists state;
* breached→breached inside the 24h re-page debounce is suppressed;
* breached→breached past the debounce with the SAME sync identity is
  dedup'd as ``same_run`` (sibling-alerter parity);
* breached→breached past the debounce with a NEW sync ts re-pages;
* breached→healthy fires exactly one recovery, then settles;
* healthy→healthy never alerts AND never writes the alert lock doc
  beyond resetting the streak counter on a near-miss;
* in-app notification persists with the lag/threshold/error details.
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, AsyncMock

import pytest

from routes import admin_d1_mirror_lag_alerts as cron


# ─── Fake Mongo (job_locks only) ────────────────────────────────────────────

class _FakeCursor:
    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        async def _gen():
            for it in self._items:
                yield it
        return _gen()


class _FakeColl:
    def __init__(self):
        self._docs: dict = {}

    async def find_one(self, query, projection=None, sort=None):
        if "_id" in query:
            doc = self._docs.get(query["_id"])
            return dict(doc) if doc else None
        return None

    async def find_one_and_update(self, query, update, upsert=False):
        def _matches(doc, q):
            for k, v in q.items():
                if k == "_id":
                    if doc.get("_id") != v:
                        return False
                    continue
                if k == "$or":
                    if not any(_matches(doc, sub) for sub in v):
                        return False
                    continue
                if k == "$and":
                    if not all(_matches(doc, sub) for sub in v):
                        return False
                    continue
                actual = doc.get(k)
                if isinstance(v, dict):
                    if "$ne" in v and actual == v["$ne"]:
                        return False
                    if "$lt" in v and not (
                        actual is not None and actual < v["$lt"]
                    ):
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

    async def update_one(self, query, update, upsert=False):
        _id = query["_id"]
        doc = self._docs.get(_id)
        if doc is None:
            if not upsert:
                return None
            doc = {"_id": _id}
            doc.update(update.get("$setOnInsert", {}))
            doc.update(update.get("$set", {}))
            self._docs[_id] = doc
            return None
        for k, v in (update.get("$set") or {}).items():
            doc[k] = v
        return None

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
        self.users = _FakeColl()


# ─── Helpers ────────────────────────────────────────────────────────────────

def _now():
    return datetime(2026, 5, 6, 12, 0, 0, tzinfo=timezone.utc)


def _health(*, enabled=True, lag_age_s=None, in_process_ts=None,
            lease_ts=None, last_sync_ok=True, last_sync_error=None,
            consecutive_failures=0):
    """Synthetic health snapshot.

    ``lag_age_s=None`` AND both timestamps None → "no sync ever".
    Otherwise lag_age_s is the seconds-since-most-recent-sync that
    the classifier should see; the timestamp fields default to a
    consistent value derived from it.
    """
    now_ts = _now().timestamp()
    if lag_age_s is not None and lease_ts is None and in_process_ts is None:
        # Default: lease drove the last sync.
        lease_ts = now_ts - lag_age_s
    lag = lag_age_s
    if lag is None and (in_process_ts or lease_ts):
        candidates = [t for t in (in_process_ts, lease_ts) if t]
        lag = max(0.0, now_ts - max(candidates))
    return {
        "enabled": enabled,
        "inProcessLastSyncTs": in_process_ts,
        "leaseLastFiredTs": lease_ts,
        "lagSeconds": lag,
        "lastSyncOk": last_sync_ok,
        "lastSyncError": last_sync_error,
        "consecutiveFailures": consecutive_failures,
        "rowCounts": {"seo_meta": 100},
    }


@pytest.fixture
def fake_db():
    return _FakeDb()


def _patch_send():
    return patch.object(cron, "_send_lag_alert", new_callable=AsyncMock)


def _patch_threshold(seconds: int):
    return patch.object(cron, "_lag_threshold_s", lambda: seconds)


def _patch_streak(n: int):
    return patch.object(cron, "_required_streak", lambda: n)


# ─── Classification ─────────────────────────────────────────────────────────

def test_classify_buckets():
    with _patch_threshold(36 * 3600):
        # Mirror disabled → unknown.
        assert cron._classify(_health(enabled=False)) == "unknown"
        # No sync ever → unknown.
        assert cron._classify(_health()) == "unknown"
        # Fresh sync → healthy.
        assert cron._classify(_health(lag_age_s=3600)) == "healthy"
        # Past threshold → breached.
        assert cron._classify(_health(lag_age_s=40 * 3600)) == "breached"
        # Exactly at threshold → breached (>= comparison).
        assert cron._classify(_health(lag_age_s=36 * 3600)) == "breached"


def test_classify_uses_dynamic_threshold():
    """Threshold is read every classification so an operator can bump
    ``D1_MIRROR_LAG_THRESHOLD_S`` without restarting the API."""
    with _patch_threshold(3600):
        assert cron._classify(_health(lag_age_s=2 * 3600)) == "breached"
    with _patch_threshold(48 * 3600):
        assert cron._classify(_health(lag_age_s=2 * 3600)) == "healthy"


def test_classify_uses_more_recent_of_in_process_and_lease():
    """When both timestamps exist, the classifier uses the more-recent
    one — a freshly-promoted leader replica with empty in-process
    state shouldn't false-positive when the global lease is fresh."""
    now_ts = _now().timestamp()
    # Lease shows fresh (1h ago), in-process is stale (40h ago).
    h = _health(
        in_process_ts=now_ts - 40 * 3600,
        lease_ts=now_ts - 3600,
        lag_age_s=3600,  # max(now - candidates) = 1h
    )
    with _patch_threshold(36 * 3600):
        assert cron._classify(h) == "healthy"


# ─── Health endpoint ───────────────────────────────────────────────────────

def test_admin_health_endpoint_status_branches():
    async def _call(health):
        async def _fake(_db):
            return health
        with patch.object(cron, "get_d1_mirror_lag_health", new=_fake):
            with _patch_threshold(36 * 3600):
                with _patch_streak(2):
                    return await cron.admin_d1_mirror_lag_health(admin={})

    not_enabled = asyncio.run(_call(_health(enabled=False)))
    assert not_enabled["status"] == "not_enabled"
    assert not_enabled["healthUrl"] == "/admin/cf-health"
    assert not_enabled["lagThresholdSeconds"] == 36 * 3600
    assert not_enabled["requiredStreak"] == 2

    never = asyncio.run(_call(_health()))
    assert never["status"] == "never_observed"

    healthy = asyncio.run(_call(_health(lag_age_s=3600)))
    assert healthy["status"] == "healthy"

    breached = asyncio.run(_call(_health(lag_age_s=40 * 3600)))
    assert breached["status"] == "breached"


# ─── Streak / grace window ─────────────────────────────────────────────────

def test_first_breach_under_streak_does_not_page(fake_db):
    """With required streak=2, a single breach observation must NOT
    page; it just persists the streak counter so the next poll knows
    where we are."""
    now = _now()
    health = _health(lag_age_s=40 * 3600)
    with _patch_threshold(36 * 3600), _patch_streak(2), _patch_send() as ms:
        result = asyncio.run(
            cron._check_and_alert_d1_mirror_lag(fake_db, now, health)
        )
    assert result["action"] == "skip"
    assert result["reason"] == "streak_pending"
    assert result["streak"] == 1
    assert result["required"] == 2
    ms.assert_not_called()
    saved = fake_db.job_locks._docs[cron._LOCK_ID]
    assert saved["consecutive_breach_count"] == 1
    assert saved.get("last_state") in (None, "")


def test_streak_threshold_reached_pages(fake_db):
    """Once the breach has been seen twice in a row, the second
    iteration alerts."""
    now = _now()
    health = _health(lag_age_s=40 * 3600)
    with _patch_threshold(36 * 3600), _patch_streak(2), _patch_send() as ms:
        first = asyncio.run(
            cron._check_and_alert_d1_mirror_lag(fake_db, now, health)
        )
        assert first["reason"] == "streak_pending"
        ms.assert_not_called()
        later = now + timedelta(hours=1)
        second = asyncio.run(
            cron._check_and_alert_d1_mirror_lag(fake_db, later, health)
        )
    assert second == {"action": "alerted", "kind": "breached", "streak": 2}
    ms.assert_called_once()
    saved = fake_db.job_locks._docs[cron._LOCK_ID]
    assert saved["last_state"] == "breached"
    assert saved["consecutive_breach_count"] == 2


def test_streak_one_pages_immediately(fake_db):
    """An operator who sets the streak to 1 (no grace beyond the
    threshold itself) gets paged on the first breach detection."""
    now = _now()
    health = _health(lag_age_s=40 * 3600)
    with _patch_threshold(36 * 3600), _patch_streak(1), _patch_send() as ms:
        result = asyncio.run(
            cron._check_and_alert_d1_mirror_lag(fake_db, now, health)
        )
    assert result == {"action": "alerted", "kind": "breached", "streak": 1}
    ms.assert_called_once()


def test_healthy_resets_pending_streak(fake_db):
    """A near-miss that bumped the streak counter without ever paging
    must reset to 0 once the lag drops back under threshold."""
    now = _now()
    fake_db.job_locks._docs[cron._LOCK_ID] = {
        "_id": cron._LOCK_ID,
        "consecutive_breach_count": 1,
    }
    healthy = _health(lag_age_s=3600)
    with _patch_threshold(36 * 3600), _patch_streak(2), _patch_send() as ms:
        result = asyncio.run(
            cron._check_and_alert_d1_mirror_lag(fake_db, now, healthy)
        )
    assert result == {"action": "skip", "reason": "healthy"}
    ms.assert_not_called()
    assert (
        fake_db.job_locks._docs[cron._LOCK_ID]["consecutive_breach_count"]
        == 0
    )


# ─── Debounce / dedup ──────────────────────────────────────────────────────

def test_breached_within_debounce_is_suppressed(fake_db):
    now = _now()
    fake_db.job_locks._docs[cron._LOCK_ID] = {
        "_id": cron._LOCK_ID,
        "last_state": "breached",
        "last_alert_at": (now - timedelta(hours=2)).isoformat(),
        "last_lease_ts": _now().timestamp() - 40 * 3600,
        "last_in_process_ts": None,
        "consecutive_breach_count": 5,
    }
    health = _health(lag_age_s=42 * 3600)
    with _patch_threshold(36 * 3600), _patch_streak(2), _patch_send() as ms:
        result = asyncio.run(
            cron._check_and_alert_d1_mirror_lag(fake_db, now, health)
        )
    assert result["action"] == "skip"
    assert result["reason"] == "debounced"
    ms.assert_not_called()


def test_same_sync_ts_does_not_re_page_after_debounce(fake_db):
    """Past the 24h debounce, same lease + in-process ts means the
    same already-acknowledged breach episode — don't re-page."""
    now = _now()
    lease_ts = _now().timestamp() - 50 * 3600
    fake_db.job_locks._docs[cron._LOCK_ID] = {
        "_id": cron._LOCK_ID,
        "last_state": "breached",
        "last_alert_at": (now - timedelta(hours=25)).isoformat(),
        "last_lease_ts": lease_ts,
        "last_in_process_ts": None,
        "consecutive_breach_count": 24,
    }
    health = _health(in_process_ts=None, lease_ts=lease_ts)
    health["lagSeconds"] = 50 * 3600
    with _patch_threshold(36 * 3600), _patch_streak(2), _patch_send() as ms:
        result = asyncio.run(
            cron._check_and_alert_d1_mirror_lag(fake_db, now, health)
        )
    assert result["action"] == "skip"
    assert result["reason"] == "same_run"
    ms.assert_not_called()


def test_new_sync_ts_after_debounce_re_pages(fake_db):
    """If a fresh sync DID land in between (lease_ts rolled forward)
    and the lag then crossed the threshold again past the 24h window,
    that's a genuinely new breach episode worth paging on."""
    now = _now()
    fake_db.job_locks._docs[cron._LOCK_ID] = {
        "_id": cron._LOCK_ID,
        "last_state": "breached",
        "last_alert_at": (now - timedelta(hours=25)).isoformat(),
        "last_lease_ts": _now().timestamp() - 80 * 3600,
        "last_in_process_ts": None,
        "consecutive_breach_count": 24,
    }
    # Different lease ts (a sync DID land at -45h before silence).
    health = _health(
        lease_ts=_now().timestamp() - 45 * 3600,
        lag_age_s=45 * 3600,
    )
    with _patch_threshold(36 * 3600), _patch_streak(2), _patch_send() as ms:
        result = asyncio.run(
            cron._check_and_alert_d1_mirror_lag(fake_db, now, health)
        )
    assert result["action"] == "alerted"
    assert result["kind"] == "breached"
    ms.assert_called_once()


# ─── Recovery ──────────────────────────────────────────────────────────────

def test_breached_to_healthy_fires_recovery_then_settles(fake_db):
    now = _now()
    fake_db.job_locks._docs[cron._LOCK_ID] = {
        "_id": cron._LOCK_ID,
        "last_state": "breached",
        "last_alert_at": (now - timedelta(hours=1)).isoformat(),
        "consecutive_breach_count": 5,
    }
    healthy = _health(lag_age_s=3600)
    with _patch_threshold(36 * 3600), _patch_streak(2), _patch_send() as ms:
        first = asyncio.run(
            cron._check_and_alert_d1_mirror_lag(fake_db, now, healthy)
        )
    assert first == {"action": "alerted", "kind": "recovered"}
    ms.assert_called_once()
    saved = fake_db.job_locks._docs[cron._LOCK_ID]
    assert saved["last_state"] == "healthy"
    assert saved["consecutive_breach_count"] == 0

    later = now + timedelta(minutes=15)
    healthy_later = _health(lag_age_s=3600 + 15 * 60)
    with _patch_threshold(36 * 3600), _patch_streak(2), _patch_send() as ms2:
        second = asyncio.run(
            cron._check_and_alert_d1_mirror_lag(fake_db, later, healthy_later)
        )
    assert second == {"action": "skip", "reason": "healthy"}
    ms2.assert_not_called()


# ─── Negative paths ────────────────────────────────────────────────────────

def test_healthy_to_healthy_never_alerts_or_creates_state(fake_db):
    now = _now()
    healthy = _health(lag_age_s=3600)
    with _patch_threshold(36 * 3600), _patch_streak(2), _patch_send() as ms:
        result = asyncio.run(
            cron._check_and_alert_d1_mirror_lag(fake_db, now, healthy)
        )
    assert result == {"action": "skip", "reason": "healthy"}
    ms.assert_not_called()
    # No prior streak → no need to write the doc at all.
    assert cron._LOCK_ID not in fake_db.job_locks._docs


def test_unknown_does_not_touch_existing_breached_state(fake_db):
    """A transient inconclusive snapshot (e.g. mirror flag off mid-deploy)
    must NOT clobber an existing breached lock doc — that would
    bypass the recovery alert."""
    now = _now()
    fake_db.job_locks._docs[cron._LOCK_ID] = {
        "_id": cron._LOCK_ID,
        "last_state": "breached",
        "last_alert_at": (now - timedelta(hours=1)).isoformat(),
    }
    with _patch_threshold(36 * 3600), _patch_streak(2), _patch_send() as ms:
        result = asyncio.run(
            cron._check_and_alert_d1_mirror_lag(
                fake_db, now, _health(enabled=False),
            )
        )
    assert result["action"] == "skip"
    assert result["reason"] == "inconclusive"
    ms.assert_not_called()
    assert (
        fake_db.job_locks._docs[cron._LOCK_ID]["last_state"] == "breached"
    )


def test_never_observed_does_not_page(fake_db):
    """A freshly-deployed backend whose mirror has never synced yet
    should classify as ``unknown`` and never page — paging on a
    cold-start would be a deploy-misconfiguration bug, not a real
    incident."""
    now = _now()
    h = _health()  # enabled, no sync ts anywhere
    with _patch_threshold(36 * 3600), _patch_streak(2), _patch_send() as ms:
        result = asyncio.run(
            cron._check_and_alert_d1_mirror_lag(fake_db, now, h)
        )
    assert result["action"] == "skip"
    assert result["reason"] == "inconclusive"
    ms.assert_not_called()
    assert cron._LOCK_ID not in fake_db.job_locks._docs


# ─── Notification body ─────────────────────────────────────────────────────

def test_send_lag_alert_persists_in_app_notification(fake_db):
    """End-to-end on the breached side: ``_send_lag_alert`` must persist
    an in-app admin notification carrying the breach kind + the
    lag/threshold/error fields the dashboard renders."""
    now = _now()
    health = _health(
        lag_age_s=42 * 3600,
        last_sync_ok=False,
        last_sync_error="primary_target_failed",
        consecutive_failures=3,
    )
    captured: dict = {}

    async def _fake_persist(payload):
        captured.update(payload)

    async def _run():
        with patch("db_ops.supa_insert_notification", new=_fake_persist):
            with patch.object(
                cron, "_email_admins", new=AsyncMock(),
            ):
                await cron._send_lag_alert(
                    fake_db, "breached", health, now,
                )
                await asyncio.sleep(0)

    with _patch_threshold(36 * 3600):
        asyncio.run(_run())
    assert captured["channel"] == "in_app"
    assert captured["audience"] == "admins"
    assert captured["type"] == "error"
    assert "breached" in captured["title"].lower()
    assert captured["meta"]["state"] == "breached"
    assert captured["meta"]["kind"] == "d1_mirror_lag_alert"
    assert captured["meta"]["lag_seconds"] == 42 * 3600
    assert captured["meta"]["lag_threshold_seconds"] == 36 * 3600
    assert captured["meta"]["last_sync_error"] == "primary_target_failed"
    assert captured["meta"]["consecutive_failures"] == 3
    # The body should reference the lag and threshold so on-call has
    # immediate context without round-tripping to the dashboard.
    assert "42.0h" in captured["message"]
    assert "primary_target_failed" in captured["message"]


def test_send_recovery_alert_uses_info_level(fake_db):
    now = _now()
    healthy = _health(lag_age_s=3600)
    captured: dict = {}

    async def _fake_persist(payload):
        captured.update(payload)

    async def _run():
        with patch("db_ops.supa_insert_notification", new=_fake_persist):
            with patch.object(
                cron, "_email_admins", new=AsyncMock(),
            ):
                await cron._send_lag_alert(
                    fake_db, "recovered", healthy, now,
                )
                await asyncio.sleep(0)

    with _patch_threshold(36 * 3600):
        asyncio.run(_run())
    assert captured["type"] == "info"
    assert "recovered" in captured["title"].lower()
    assert captured["meta"]["state"] == "recovered"


# ─── Threshold / streak env overrides ─────────────────────────────────────

def test_lag_threshold_reads_env(monkeypatch):
    monkeypatch.setenv("D1_MIRROR_LAG_THRESHOLD_S", "7200")
    assert cron._lag_threshold_s() == 7200
    # Whitespace / invalid → fall through to default.
    monkeypatch.setenv("D1_MIRROR_LAG_THRESHOLD_S", "   ")
    assert cron._lag_threshold_s() == 36 * 3600
    monkeypatch.setenv("D1_MIRROR_LAG_THRESHOLD_S", "not-a-number")
    assert cron._lag_threshold_s() == 36 * 3600
    # Defensive floor at 60s.
    monkeypatch.setenv("D1_MIRROR_LAG_THRESHOLD_S", "10")
    assert cron._lag_threshold_s() == 60


def test_required_streak_reads_env(monkeypatch):
    monkeypatch.setenv("D1_MIRROR_LAG_REQUIRED_STREAK", "5")
    assert cron._required_streak() == 5
    monkeypatch.setenv("D1_MIRROR_LAG_REQUIRED_STREAK", "0")
    # Floor at 1 — a streak of 0 would mean "page even when healthy".
    assert cron._required_streak() == 1
    monkeypatch.setenv("D1_MIRROR_LAG_REQUIRED_STREAK", "")
    assert cron._required_streak() == 2


# ─── Task #509 — Slack fan-out ─────────────────────────────────────────────

def test_slack_payload_breached_carries_lag_threshold_error_and_link():
    """The Slack body for a breach page must carry the four elements
    the task spec calls out: lag, threshold, last error, and a link
    to the admin pill endpoint."""
    health = _health(
        lag_age_s=40 * 3600,
        last_sync_ok=False,
        last_sync_error="cf 401 unauthorized",
        consecutive_failures=3,
    )
    with _patch_threshold(36 * 3600):
        payload = cron._slack_payload_for_lag_alert(
            "D1 mirror lag breached: no sync in 40.0h",
            "body",
            "breached",
            health,
        )
    assert payload["text"].startswith(":rotating_light:")
    blocks_text = "\n".join(
        b["text"]["text"] for b in payload["blocks"]
    )
    # Lag, threshold, last error, and the admin pill URL must all
    # appear so on-call has the same triage context as the in-app
    # body without context-switching.
    assert "40.0h" in blocks_text
    assert "36.0h" in blocks_text
    assert "cf 401 unauthorized" in blocks_text
    assert cron._HEALTH_URL in blocks_text


def test_slack_payload_recovered_uses_check_emoji():
    health = _health(lag_age_s=60)
    with _patch_threshold(36 * 3600):
        payload = cron._slack_payload_for_lag_alert(
            "D1 mirror lag recovered", "body", "recovered", health,
        )
    assert payload["text"].startswith(":white_check_mark:")
    header_text = payload["blocks"][0]["text"]["text"]
    assert "recovered" in header_text.lower()


def test_slack_payload_truncates_long_message_body():
    """Defensively cap the free-form message section under Slack's
    3000-char section limit, matching sibling alerters."""
    health = _health(lag_age_s=40 * 3600)
    huge = "x" * 5000
    with _patch_threshold(36 * 3600):
        payload = cron._slack_payload_for_lag_alert(
            "t", huge, "breached", health,
        )
    msg_block = payload["blocks"][-1]["text"]["text"]
    assert len(msg_block) <= 2900


def test_post_slack_lag_alert_noop_when_env_unset(monkeypatch):
    """No env var → no network call and the helper never raises."""
    monkeypatch.delenv("D1_MIRROR_LAG_SLACK_WEBHOOK", raising=False)
    captured = {"called": False}

    class _SentinelClient:
        def __init__(self, *a, **kw):
            captured["called"] = True

    with patch("httpx.AsyncClient", _SentinelClient):
        with _patch_threshold(36 * 3600):
            asyncio.run(cron._post_slack_lag_alert(
                "t", "m", "breached",
                _health(lag_age_s=40 * 3600),
            ))
    assert captured["called"] is False


def test_post_slack_lag_alert_posts_when_env_set(monkeypatch):
    """When the env var is set the helper POSTs the rendered payload
    to that URL with a JSON body."""
    monkeypatch.setenv(
        "D1_MIRROR_LAG_SLACK_WEBHOOK", "https://hooks.slack.test/abc",
    )
    posted: dict = {}

    class _Resp:
        status_code = 200
        text = "ok"

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            posted["url"] = url
            posted["json"] = json
            return _Resp()

    with patch("httpx.AsyncClient", _Client):
        with _patch_threshold(36 * 3600):
            asyncio.run(cron._post_slack_lag_alert(
                "t", "m", "breached",
                _health(lag_age_s=40 * 3600),
            ))
    assert posted["url"] == "https://hooks.slack.test/abc"
    assert posted["json"]["text"].startswith(":rotating_light:")
    assert posted["json"]["blocks"]


def test_post_slack_lag_alert_swallows_transport_failures(monkeypatch):
    """A network error from the webhook must NOT propagate — the
    Slack channel is best-effort, the email + in-app channels have
    already succeeded by the time the background task runs."""
    monkeypatch.setenv(
        "D1_MIRROR_LAG_SLACK_WEBHOOK", "https://hooks.slack.test/abc",
    )

    class _BoomClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            raise RuntimeError("network dead")

    with patch("httpx.AsyncClient", _BoomClient):
        with _patch_threshold(36 * 3600):
            # Must not raise.
            asyncio.run(cron._post_slack_lag_alert(
                "t", "m", "breached",
                _health(lag_age_s=40 * 3600),
            ))


def test_post_slack_lag_alert_swallows_4xx_responses(monkeypatch):
    """A 4xx response from the webhook is logged at WARNING but never
    raised — same discipline as the cf-waf-drift sibling."""
    monkeypatch.setenv(
        "D1_MIRROR_LAG_SLACK_WEBHOOK", "https://hooks.slack.test/abc",
    )

    class _Resp:
        status_code = 404
        text = "no_team"

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            return _Resp()

    with patch("httpx.AsyncClient", _Client):
        with _patch_threshold(36 * 3600):
            asyncio.run(cron._post_slack_lag_alert(
                "t", "m", "breached",
                _health(lag_age_s=40 * 3600),
            ))


def test_send_lag_alert_schedules_slack_fan_out(fake_db):
    """End-to-end: ``_send_lag_alert`` must schedule a Slack POST on
    the breach transition alongside the email + in-app channels."""
    now = _now()
    health = _health(
        lag_age_s=40 * 3600,
        last_sync_ok=False,
        last_sync_error="cf 401 unauthorized",
    )
    captured: dict = {}

    async def _fake_slack(title, msg, kind, h):
        captured["title"] = title
        captured["kind"] = kind
        captured["health"] = h

    async def _run():
        with patch("db_ops.supa_insert_notification", new=AsyncMock()):
            with patch.object(cron, "_post_slack_lag_alert",
                              new=_fake_slack):
                with patch.object(cron, "_email_admins",
                                  new=AsyncMock()):
                    await cron._send_lag_alert(
                        fake_db, "breached", health, now,
                    )
                    # Background tasks scheduled via asyncio.create_task —
                    # yield once so they run before the patches tear down.
                    await asyncio.sleep(0)

    with _patch_threshold(36 * 3600):
        asyncio.run(_run())
    assert captured.get("kind") == "breached"
    assert "breached" in captured.get("title", "").lower()
    assert captured.get("health") is health


def test_send_recovery_alert_schedules_slack_fan_out(fake_db):
    """The recovery transition must also fan out to Slack so on-call
    sees the all-clear in the same channel as the original page."""
    now = _now()
    healthy = _health(lag_age_s=60)
    captured: dict = {}

    async def _fake_slack(title, msg, kind, h):
        captured["kind"] = kind
        captured["title"] = title

    async def _run():
        with patch("db_ops.supa_insert_notification", new=AsyncMock()):
            with patch.object(cron, "_post_slack_lag_alert",
                              new=_fake_slack):
                with patch.object(cron, "_email_admins",
                                  new=AsyncMock()):
                    await cron._send_lag_alert(
                        fake_db, "recovered", healthy, now,
                    )
                    await asyncio.sleep(0)

    with _patch_threshold(36 * 3600):
        asyncio.run(_run())
    assert captured.get("kind") == "recovered"
    assert "recovered" in captured.get("title", "").lower()


def test_send_lag_alert_in_app_succeeds_when_slack_post_fails(fake_db):
    """A failing Slack POST must NOT undo the in-app notification
    persist that already ran — Slack is purely best-effort."""
    now = _now()
    health = _health(lag_age_s=40 * 3600)
    persisted: dict = {}

    async def _fake_persist(payload):
        persisted.update(payload)

    async def _boom_slack(*a, **kw):
        raise RuntimeError("slack down")

    async def _run():
        with patch("db_ops.supa_insert_notification", new=_fake_persist):
            with patch.object(cron, "_post_slack_lag_alert",
                              new=_boom_slack):
                with patch.object(cron, "_email_admins",
                                  new=AsyncMock()):
                    await cron._send_lag_alert(
                        fake_db, "breached", health, now,
                    )
                    # Yield so the background Slack task runs and
                    # raises (its raise must not surface here).
                    await asyncio.sleep(0)

    with _patch_threshold(36 * 3600):
        asyncio.run(_run())
    assert persisted.get("channel") == "in_app"
    assert persisted.get("meta", {}).get("state") == "breached"


def test_admin_health_endpoint_surfaces_slack_configured(monkeypatch):
    """The pill endpoint must surface ``slackConfigured`` /
    ``slackWebhookEnv`` so the AdminHealth dashboard can render the
    "Slack ✓ / ✗" badge alongside its sibling cron pills."""
    async def _call():
        async def _fake(_db):
            return _health(lag_age_s=40 * 3600)
        with patch.object(cron, "get_d1_mirror_lag_health", new=_fake):
            with _patch_threshold(36 * 3600):
                with _patch_streak(2):
                    return await cron.admin_d1_mirror_lag_health(admin={})

    monkeypatch.setenv(
        "D1_MIRROR_LAG_SLACK_WEBHOOK",
        "https://hooks.slack.example.com/services/T0/B0/secret",
    )
    payload = asyncio.run(_call())
    assert payload["slackConfigured"] is True
    assert payload["slackWebhookEnv"] == "D1_MIRROR_LAG_SLACK_WEBHOOK"

    monkeypatch.delenv("D1_MIRROR_LAG_SLACK_WEBHOOK", raising=False)
    payload_unset = asyncio.run(_call())
    assert payload_unset["slackConfigured"] is False
    assert payload_unset["slackWebhookEnv"] == "D1_MIRROR_LAG_SLACK_WEBHOOK"
