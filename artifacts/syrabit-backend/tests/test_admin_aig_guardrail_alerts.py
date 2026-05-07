"""Task #485 — per-model AI Gateway guardrail block-ratio alerter.

Covers:
* row classification (spike / healthy / unknown);
* low-sample rows never page even when the ratio is high;
* first spike detection alerts and persists per-model state;
* spike→spike inside the 24h debounce is suppressed;
* spike→spike past the 24h debounce re-pages;
* spike→healthy fires exactly one recovery, then settles;
* healthy→healthy never alerts AND never writes the lock doc;
* a separate model in the same iteration is independent;
* obs-disabled snapshots short-circuit the iteration;
* the admin alert-state endpoint surfaces every per-model lock doc.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, AsyncMock

import pytest

from routes import admin_aig_guardrail_alerts as alerter


# ─── Fake Mongo (job_locks only — copy of the trustpilot-alerter test fake) ─

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

    def find(self, query=None, projection=None, sort=None):
        # Honour the regex-prefix lookup the alert-state endpoint uses.
        items = list(self._docs.values())
        if query and "_id" in query and isinstance(query["_id"], dict):
            regex = query["_id"].get("$regex")
            if regex:
                import re
                pat = re.compile(regex)
                items = [d for d in items if pat.search(str(d.get("_id")))]
        return _FakeCursor(items)


class _FakeDb:
    def __init__(self):
        self.job_locks = _FakeColl()
        self.users = _FakeColl()


# ─── Helpers ────────────────────────────────────────────────────────────────

def _now():
    return datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)


def _row(model="m1", provider="workers_ai", *, blocks=10, total=20, ratio=0.5):
    return {
        "provider": provider,
        "model": model,
        "samples": total,
        "allows": max(0, total - blocks),
        "rewrites": 0,
        "blocks": blocks,
        "guardrail_total": total,
        "block_ratio": ratio,
    }


@pytest.fixture
def fake_db():
    return _FakeDb()


def _patch_send():
    return patch.object(
        alerter, "_send_aig_guardrail_alert", new_callable=AsyncMock,
    )


# ─── Classification ─────────────────────────────────────────────────────────

def test_classify_buckets():
    # Below the min-sample floor → unknown even at a high ratio.
    assert alerter._classify_row(_row(total=5, blocks=4, ratio=0.8)) == "unknown"
    # block_ratio missing → unknown (e.g. cache-only telemetry row).
    r = _row(total=50, blocks=0, ratio=None)
    assert alerter._classify_row(r) == "unknown"
    # At or above threshold past the sample floor → spike.
    assert alerter._classify_row(
        _row(total=50, blocks=15, ratio=0.30)
    ) == "spike"
    assert alerter._classify_row(
        _row(total=50, blocks=40, ratio=0.80)
    ) == "spike"
    # Past the floor, ratio under threshold → healthy.
    assert alerter._classify_row(
        _row(total=50, blocks=2, ratio=0.04)
    ) == "healthy"


def test_lock_id_sanitises_provider_and_model():
    # Mongo `_id` must not embed regex/operator metacharacters.
    bad = alerter._lock_id_for("vertex/$ai", "model:foo$bar")
    assert bad.startswith(alerter._LOCK_ID_PREFIX)
    for ch in ("$", "/", ":"):
        # Sanitised form replaces them with `_`.
        assert ch not in bad


# ─── Alert lifecycle ────────────────────────────────────────────────────────

def test_low_sample_high_ratio_never_pages(fake_db):
    """A single ``block`` event on a quiet model would read as 100%
    blocked. The min-sample floor must suppress that — without it
    every cold-start would page on its first guardrail rejection."""
    now = _now()
    rows = [_row(total=3, blocks=3, ratio=1.0)]
    snap = {"enabled": True, "guardrail_by_model": rows}
    with _patch_send() as mock_send:
        result = asyncio.run(
            alerter._check_and_alert_aig_guardrail(fake_db, now, snap)
        )
    assert result["action"] == "checked"
    only = next(iter(result["results"].values()))
    assert only["action"] == "skip"
    assert only["reason"] == "inconclusive"
    mock_send.assert_not_called()
    # No lock doc written.
    assert fake_db.job_locks._docs == {}


def test_first_spike_detection_alerts_and_persists(fake_db):
    now = _now()
    rows = [_row(model="llama-guard-victim", total=50, blocks=20, ratio=0.4)]
    snap = {"enabled": True, "guardrail_by_model": rows}
    with _patch_send() as mock_send:
        result = asyncio.run(
            alerter._check_and_alert_aig_guardrail(fake_db, now, snap)
        )
    only = next(iter(result["results"].values()))
    assert only == {
        "action": "alerted", "kind": "spike",
        "model": "llama-guard-victim", "provider": "workers_ai",
    }
    mock_send.assert_called_once()
    lock_id = alerter._lock_id_for("workers_ai", "llama-guard-victim")
    saved = fake_db.job_locks._docs[lock_id]
    assert saved["last_state"] == "spike"
    assert saved["last_alert_at"] == now.isoformat()
    assert saved["last_block_ratio"] == 0.4


def test_spike_within_debounce_is_suppressed(fake_db):
    now = _now()
    lock_id = alerter._lock_id_for("workers_ai", "m1")
    fake_db.job_locks._docs[lock_id] = {
        "_id": lock_id, "last_state": "spike",
        "last_alert_at": (now - timedelta(hours=2)).isoformat(),
        "model": "m1", "provider": "workers_ai",
    }
    rows = [_row(total=50, blocks=25, ratio=0.5)]
    snap = {"enabled": True, "guardrail_by_model": rows}
    with _patch_send() as mock_send:
        result = asyncio.run(
            alerter._check_and_alert_aig_guardrail(fake_db, now, snap)
        )
    only = next(iter(result["results"].values()))
    assert only["action"] == "skip"
    assert only["reason"] == "debounced"
    mock_send.assert_not_called()


def test_spike_outside_debounce_re_pages(fake_db):
    now = _now()
    lock_id = alerter._lock_id_for("workers_ai", "m1")
    fake_db.job_locks._docs[lock_id] = {
        "_id": lock_id, "last_state": "spike",
        "last_alert_at": (now - timedelta(hours=25)).isoformat(),
        "model": "m1", "provider": "workers_ai",
    }
    rows = [_row(total=50, blocks=25, ratio=0.5)]
    snap = {"enabled": True, "guardrail_by_model": rows}
    with _patch_send() as mock_send:
        result = asyncio.run(
            alerter._check_and_alert_aig_guardrail(fake_db, now, snap)
        )
    only = next(iter(result["results"].values()))
    assert only["action"] == "alerted"
    assert only["kind"] == "spike"
    mock_send.assert_called_once()


def test_spike_to_healthy_fires_recovery_then_settles(fake_db):
    now = _now()
    lock_id = alerter._lock_id_for("workers_ai", "m1")
    fake_db.job_locks._docs[lock_id] = {
        "_id": lock_id, "last_state": "spike",
        "last_alert_at": (now - timedelta(hours=1)).isoformat(),
        "model": "m1", "provider": "workers_ai",
    }
    healthy = [_row(total=50, blocks=1, ratio=0.02)]
    snap = {"enabled": True, "guardrail_by_model": healthy}
    with _patch_send() as mock_send:
        first = asyncio.run(
            alerter._check_and_alert_aig_guardrail(fake_db, now, snap)
        )
    only_first = next(iter(first["results"].values()))
    assert only_first["action"] == "alerted"
    assert only_first["kind"] == "recovered"
    mock_send.assert_called_once()
    assert fake_db.job_locks._docs[lock_id]["last_state"] == "healthy"

    with _patch_send() as mock_send2:
        second = asyncio.run(
            alerter._check_and_alert_aig_guardrail(
                fake_db, now + timedelta(minutes=15), snap,
            )
        )
    only_second = next(iter(second["results"].values()))
    assert only_second["action"] == "skip"
    assert only_second["reason"] == "healthy"
    mock_send2.assert_not_called()


def test_healthy_to_healthy_never_alerts_or_writes(fake_db):
    now = _now()
    healthy = [_row(total=50, blocks=1, ratio=0.02)]
    snap = {"enabled": True, "guardrail_by_model": healthy}
    with _patch_send() as mock_send:
        result = asyncio.run(
            alerter._check_and_alert_aig_guardrail(fake_db, now, snap)
        )
    only = next(iter(result["results"].values()))
    assert only["action"] == "skip"
    assert only["reason"] == "healthy"
    mock_send.assert_not_called()
    # No lock doc written — would race a peer's `spike` claim and
    # silently bypass the 24h debounce on the next iteration.
    assert fake_db.job_locks._docs == {}


def test_two_models_are_alerted_independently(fake_db):
    """A page on model A must not suppress (or be debounced by) a
    sibling spike on model B inside the same iteration."""
    now = _now()
    rows = [
        _row(model="m1", total=50, blocks=25, ratio=0.5),
        _row(model="m2", total=50, blocks=20, ratio=0.4),
    ]
    snap = {"enabled": True, "guardrail_by_model": rows}
    with _patch_send() as mock_send:
        result = asyncio.run(
            alerter._check_and_alert_aig_guardrail(fake_db, now, snap)
        )
    actions = {k: v["action"] for k, v in result["results"].items()}
    assert actions == {"workers_ai::m1": "alerted",
                       "workers_ai::m2": "alerted"}
    assert mock_send.call_count == 2
    assert (alerter._lock_id_for("workers_ai", "m1")
            in fake_db.job_locks._docs)
    assert (alerter._lock_id_for("workers_ai", "m2")
            in fake_db.job_locks._docs)


def test_obs_disabled_short_circuits(fake_db):
    """When `CF_AIGW_OBS_ON` is off the snapshot reports `enabled: False`
    and the rows are stale/empty — never page on that."""
    snap = {"enabled": False, "guardrail_by_model": [
        _row(total=999, blocks=999, ratio=1.0),
    ]}
    with _patch_send() as mock_send:
        result = asyncio.run(
            alerter._check_and_alert_aig_guardrail(fake_db, _now(), snap)
        )
    assert result["action"] == "skip"
    assert result["reason"] == "obs_disabled"
    mock_send.assert_not_called()
    assert fake_db.job_locks._docs == {}


# ─── Admin alert-state endpoint ────────────────────────────────────────────

def test_alert_state_endpoint_lists_every_per_model_lock(fake_db):
    """The endpoint must return the per-model alerter state so the
    AdminHealth tile can decorate each row with `paged Xh ago`."""
    now = _now()
    a = alerter._lock_id_for("workers_ai", "m1")
    b = alerter._lock_id_for("vertex", "gemini-2.5-flash")
    fake_db.job_locks._docs[a] = {
        "_id": a, "last_state": "spike",
        "last_alert_at": (now - timedelta(hours=2)).isoformat(),
        "provider": "workers_ai", "model": "m1",
        "last_block_ratio": 0.42,
    }
    fake_db.job_locks._docs[b] = {
        "_id": b, "last_state": "healthy",
        "last_alert_at": (now - timedelta(hours=30)).isoformat(),
        "provider": "vertex", "model": "gemini-2.5-flash",
        "last_block_ratio": 0.05,
    }
    # Unrelated lock doc must not leak through the prefix scan.
    fake_db.job_locks._docs["something_else"] = {"_id": "something_else"}

    async def _fake_mongo_available():
        return True

    with patch.object(alerter, "datetime") as mock_dt:
        mock_dt.now.return_value = now
        # The shaping helper uses datetime internally — the patch on
        # the module-level alias pins "now".
        mock_dt.fromisoformat = datetime.fromisoformat
        # Patch the deps imports inside the endpoint.
        import sys
        fake_deps = type(sys)("deps")
        fake_deps.db = fake_db
        fake_deps.is_mongo_available = _fake_mongo_available
        with patch.dict(sys.modules, {"deps": fake_deps}):
            result = asyncio.run(
                alerter.admin_aig_guardrail_alert_state(admin={})
            )

    assert result["alerter"]["blockRatioThreshold"] == (
        alerter._AIG_BLOCK_RATIO_THRESHOLD
    )
    assert result["alerter"]["minSamples"] == alerter._AIG_MIN_SAMPLES
    models = {(m["provider"], m["model"]): m for m in result["models"]}
    assert ("workers_ai", "m1") in models
    assert ("vertex", "gemini-2.5-flash") in models
    spike_row = models[("workers_ai", "m1")]
    assert spike_row["lastState"] == "spike"
    assert spike_row["inDebounce"] is True
    assert spike_row["debounceRemainingSeconds"] is not None
    assert 0 < spike_row["debounceRemainingSeconds"] <= 24 * 3600
    healthy_row = models[("vertex", "gemini-2.5-flash")]
    assert healthy_row["lastState"] == "healthy"
    assert healthy_row["inDebounce"] is False


def test_alert_state_endpoint_returns_empty_when_alerter_never_fired(fake_db):
    async def _fake_mongo_available():
        return True

    import sys
    fake_deps = type(sys)("deps")
    fake_deps.db = fake_db
    fake_deps.is_mongo_available = _fake_mongo_available
    with patch.dict(sys.modules, {"deps": fake_deps}):
        result = asyncio.run(
            alerter.admin_aig_guardrail_alert_state(admin={})
        )
    assert result["models"] == []
    # Tunables still surface so the tile can render the threshold copy.
    assert "blockRatioThreshold" in result["alerter"]
