"""Task #557 — FCM → VAPID migration runner.

Asserts the state machine in `scripts/migrate_fcm_to_vapid.py`:

  * A complete W3C subscription is bucketed as `migrated` and stamped
    on first `--apply` sweep.
  * A legacy FCM-shaped doc (or one missing the W3C keys blob) is
    classified as `pending` and gets `migration_first_seen_at` stamped.
  * A `pending` doc whose `first_seen` is older than the migration
    window is tombstoned (`active=False`,
    `deactivation_reason="fcm_migration_window_expired"`,
    `migration_state="tombstoned"`) but NOT deleted.
  * `--purge` deletes tombstoned docs older than the grace period and
    leaves fresh tombstones in place.
  * Dry-run (default) writes nothing.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from tests._deps_stub import install_deps_stub

install_deps_stub(force=True)

import deps  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def __aiter__(self):
        async def gen():
            for d in self._docs:
                yield d
        return gen()


class _FakePushColl:
    def __init__(self, docs):
        # store a clone so the sweep's writes are isolated from the
        # caller's original list
        self.docs = [dict(d) for d in docs]
        self.updates: list[tuple[dict, dict]] = []
        self.deletes: list[dict] = []

    def find(self, *_args, **_kw):
        return _FakeCursor(self.docs)

    @staticmethod
    def _matches(d, flt):
        for k, v in flt.items():
            if d.get(k) != v:
                return False
        return True

    async def update_one(self, flt, upd):
        self.updates.append((flt, upd))
        for d in self.docs:
            if self._matches(d, flt):
                d.update(upd.get("$set", {}))
                return

    async def delete_one(self, flt):
        self.deletes.append(flt)
        self.docs = [d for d in self.docs if not self._matches(d, flt)]


def _install_db(docs) -> _FakePushColl:
    coll = _FakePushColl(docs)
    deps.db.push_subscriptions = coll
    # Force re-import of the script module each time so MIGRATION_WINDOW
    # is computed against the current env.
    import importlib
    import sys
    sys.modules.pop("scripts.migrate_fcm_to_vapid", None)
    importlib.import_module("scripts.migrate_fcm_to_vapid")
    return coll


def test_complete_w3c_doc_is_marked_migrated():
    coll = _install_db([{
        "endpoint": "https://fcm.googleapis.com/wp/abc",
        "subscription_info": {
            "endpoint": "https://fcm.googleapis.com/wp/abc",
            "keys": {"p256dh": "p", "auth": "a"},
        },
    }])
    from scripts.migrate_fcm_to_vapid import run_sweep

    summary = _run(run_sweep(apply=True, purge=False))
    assert summary["marked_migrated"] == 1
    assert summary["classified"] == 0
    assert summary["tombstoned"] == 0
    assert coll.docs[0]["migration_state"] == "migrated"


def test_legacy_fcm_doc_is_classified_pending():
    coll = _install_db([{
        "endpoint": "legacy://fcm/xyz",
        "provider": "fcm",
        "fcm_token": "ya29.legacy",
        # no subscription_info
    }])
    from scripts.migrate_fcm_to_vapid import run_sweep

    summary = _run(run_sweep(apply=True, purge=False))
    assert summary["classified"] == 1
    assert summary["tombstoned"] == 0
    assert coll.docs[0]["migration_state"] == "pending"
    assert isinstance(coll.docs[0]["migration_first_seen_at"], datetime)


def test_old_pending_doc_is_tombstoned_not_deleted():
    long_ago = datetime.now(timezone.utc) - timedelta(days=45)
    coll = _install_db([{
        "endpoint": "legacy://fcm/old",
        "provider": "fcm",
        "fcm_token": "ya29.old",
        "migration_state": "pending",
        "migration_first_seen_at": long_ago,
    }])
    from scripts.migrate_fcm_to_vapid import run_sweep

    summary = _run(run_sweep(apply=True, purge=False))
    assert summary["tombstoned"] == 1
    assert summary["purged"] == 0
    assert len(coll.docs) == 1
    doc = coll.docs[0]
    assert doc["active"] is False
    assert doc["migration_state"] == "tombstoned"
    assert doc["deactivation_reason"] == "fcm_migration_window_expired"
    assert isinstance(doc["deactivated_at"], datetime)


def test_purge_deletes_tombstones_past_grace_only():
    long_ago = datetime.now(timezone.utc) - timedelta(days=20)
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    coll = _install_db([
        {  # past grace → purged
            "endpoint": "tomb://old",
            "migration_state": "tombstoned",
            "deactivated_at": long_ago,
            "active": False,
        },
        {  # fresh tombstone → kept
            "endpoint": "tomb://fresh",
            "migration_state": "tombstoned",
            "deactivated_at": yesterday,
            "active": False,
        },
    ])
    from scripts.migrate_fcm_to_vapid import run_sweep

    summary = _run(run_sweep(apply=True, purge=True))
    assert summary["purged"] == 1
    remaining = [d["endpoint"] for d in coll.docs]
    assert "tomb://old" not in remaining
    assert "tomb://fresh" in remaining


def test_dry_run_makes_no_writes():
    coll = _install_db([{
        "endpoint": "legacy://fcm/dry",
        "provider": "fcm",
        "fcm_token": "ya29.dry",
    }])
    from scripts.migrate_fcm_to_vapid import run_sweep

    _run(run_sweep(apply=False, purge=False))
    assert coll.updates == []
    assert coll.deletes == []
    assert "migration_state" not in coll.docs[0]


def test_token_only_legacy_doc_is_classified_via_id():
    """A pre-W3C FCM doc has no `endpoint` field at all — only `_id`
    and `fcm_token`. The sweep must address it via `_id` and tombstone
    it on the next pass, otherwise legacy users would never roll off."""
    coll = _install_db([{
        "_id": "leg-token-1",
        "provider": "fcm",
        "fcm_token": "ya29.endpointless",
    }])
    from scripts.migrate_fcm_to_vapid import run_sweep

    summary = _run(run_sweep(apply=True, purge=False))
    assert summary["classified"] == 1
    doc = coll.docs[0]
    assert doc["migration_state"] == "pending"
    assert isinstance(doc["migration_first_seen_at"], datetime)
    # Re-sweep with first_seen pushed back past the window → tombstoned.
    doc["migration_first_seen_at"] = datetime.now(timezone.utc) - timedelta(days=45)
    summary2 = _run(run_sweep(apply=True, purge=False))
    assert summary2["tombstoned"] == 1
    assert coll.docs[0]["active"] is False
    assert coll.docs[0]["migration_state"] == "tombstoned"


def test_collect_status_buckets_correctly():
    long_ago = datetime.now(timezone.utc) - timedelta(days=45)
    _install_db([
        {  # migrated
            "endpoint": "ok://1",
            "subscription_info": {"endpoint": "ok://1", "keys": {"p256dh": "p", "auth": "a"}},
        },
        {  # pending
            "endpoint": "legacy://1",
            "provider": "fcm",
            "fcm_token": "t",
        },
        {  # tombstoned
            "endpoint": "tomb://1",
            "migration_state": "tombstoned",
            "active": False,
            "deactivated_at": long_ago,
        },
    ])
    from scripts.migrate_fcm_to_vapid import collect_status

    status = _run(collect_status())
    assert status["total"] == 3
    assert status["migrated"] == 1
    assert status["pending"] == 1
    assert status["tombstoned"] == 1
    assert status["window_days"] >= 1
