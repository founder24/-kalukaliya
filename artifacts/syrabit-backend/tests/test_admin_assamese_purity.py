"""Task #422 — admin runtime override + test-fire route tests."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


@pytest.fixture(autouse=True)
def _reset_runtime_override():
    from lang_sanitizer import clear_runtime_override
    clear_runtime_override()
    yield
    clear_runtime_override()


@pytest.fixture
def mock_admin():
    return {"id": "admin-1", "email": "ops@syrabit.ai", "is_admin": True}


@pytest.fixture
def app_client(mock_admin):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.cms_sarvam_health import router

    app = FastAPI()
    app.include_router(router)
    from auth_deps import get_admin_user
    app.dependency_overrides = {get_admin_user: lambda: mock_admin}
    return TestClient(app)


def _mock_db():
    """Builds a MagicMock(api_config=AsyncMock-collection) usable by the
    routes' `from deps import db as _db` import paths.

    Also wires `db[<collection>]` (used by the audit / runs collections)
    so insert_one is awaitable and won't raise inside the swallowed
    audit recorder."""
    api_cfg = MagicMock()
    api_cfg.find_one = AsyncMock(return_value=None)
    api_cfg.update_one = AsyncMock(return_value=None)
    db = MagicMock()
    db.api_config = api_cfg
    audit_coll = MagicMock()
    audit_coll.insert_one = AsyncMock(return_value=None)
    audit_coll.create_index = AsyncMock(return_value=None)
    audit_coll.count_documents = AsyncMock(return_value=0)
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.skip.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.to_list = AsyncMock(return_value=[])
    audit_coll.find = MagicMock(return_value=cursor)
    db.__getitem__ = MagicMock(return_value=audit_coll)
    return db, api_cfg


def _patch_db(db):
    """Routes do `from deps import db as _db` lazily — patch the source."""
    return patch("deps.db", db, create=True)


class TestGetAssamesePurity:
    def test_get_returns_config_and_test_sample(self, app_client):
        db, _ = _mock_db()
        with _patch_db(db):
            r = app_client.get("/admin/assamese-purity")
        assert r.status_code == 200
        body = r.json()
        assert "config" in body
        cfg = body["config"]
        assert cfg["behaviour"] in cfg["valid_behaviours"]
        assert "behaviour_source" in cfg and "threshold_source" in cfg
        # Default leaky sample is non-empty so admins always have
        # something to fire the test against.
        assert body["test_sample"] and "অসম" in body["test_sample"] or body["test_sample"]


class TestPatchAssamesePurity:
    def test_patch_validates_behaviour(self, app_client):
        db, _ = _mock_db()
        with _patch_db(db):
            r = app_client.patch(
                "/admin/assamese-purity",
                json={"behaviour": "junk-mode"},
            )
        assert r.status_code == 400
        assert "behaviour" in r.json()["detail"]

    def test_patch_validates_threshold_range(self, app_client):
        db, _ = _mock_db()
        with _patch_db(db):
            r = app_client.patch(
                "/admin/assamese-purity",
                json={"threshold": 1.5},
            )
        assert r.status_code == 400

    def test_patch_requires_at_least_one_field(self, app_client):
        db, _ = _mock_db()
        with _patch_db(db):
            r = app_client.patch("/admin/assamese-purity", json={})
        assert r.status_code == 400

    def test_patch_persists_and_applies_override(self, app_client):
        from lang_sanitizer import get_behaviour, get_threshold
        db, api_cfg = _mock_db()
        with _patch_db(db):
            r = app_client.patch(
                "/admin/assamese-purity",
                json={"behaviour": "off", "threshold": 0.07},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        # In-memory layer reflects the override.
        assert get_behaviour() == "off"
        assert get_threshold() == pytest.approx(0.07)
        # Persisted doc carries the audit fields.
        assert body["persisted"]["behaviour"] == "off"
        assert body["persisted"]["threshold"] == pytest.approx(0.07)
        assert body["persisted"]["updated_by"] == "ops@syrabit.ai"
        assert "updated_at" in body["persisted"]
        # Mongo write happened with $set on the override key.
        api_cfg.update_one.assert_awaited()
        call_args = api_cfg.update_one.call_args
        assert "$set" in call_args.args[1]
        assert "assamese_purity_override" in call_args.args[1]["$set"]
        # Source columns are now "override".
        assert body["config"]["behaviour_source"] == "override"
        assert body["config"]["threshold_source"] == "override"

    def test_patch_partial_update_only_changes_one_field(self, app_client):
        from lang_sanitizer import get_behaviour, get_threshold
        # Seed an override first via the in-memory layer directly.
        from lang_sanitizer import apply_runtime_override
        apply_runtime_override(behaviour="strip", threshold=0.10, updated_by="seed")
        db, _ = _mock_db()
        with _patch_db(db):
            r = app_client.patch(
                "/admin/assamese-purity",
                json={"behaviour": "translate"},
            )
        assert r.status_code == 200
        # Threshold preserved from the existing override.
        assert get_behaviour() == "translate"
        assert get_threshold() == pytest.approx(0.10)


class TestDeleteAssamesePurity:
    def test_delete_clears_override_and_unsets_doc(self, app_client):
        from lang_sanitizer import (
            apply_runtime_override, get_runtime_override,
        )
        apply_runtime_override(behaviour="off", threshold=0.5, updated_by="x")
        assert get_runtime_override() is not None
        db, api_cfg = _mock_db()
        with _patch_db(db):
            r = app_client.delete("/admin/assamese-purity")
        assert r.status_code == 200
        body = r.json()
        assert body["cleared"] is True
        assert get_runtime_override() is None
        api_cfg.update_one.assert_awaited()
        call_args = api_cfg.update_one.call_args
        assert "$unset" in call_args.args[1]

    def test_delete_fails_closed_when_persistence_fails(self, app_client):
        """If mongo unset fails, in-memory override must NOT be cleared
        (otherwise the override would silently come back on next worker
        restart and admins would have no idea the clear didn't stick)."""
        from lang_sanitizer import (
            apply_runtime_override, get_runtime_override,
        )
        apply_runtime_override(behaviour="off", threshold=0.5, updated_by="x")
        api_cfg = MagicMock()
        api_cfg.find_one = AsyncMock(return_value=None)
        api_cfg.update_one = AsyncMock(side_effect=RuntimeError("mongo down"))
        db = MagicMock()
        db.api_config = api_cfg
        with _patch_db(db):
            r = app_client.delete("/admin/assamese-purity")
        assert r.status_code == 500
        # In-memory override survived the failed clear.
        ov = get_runtime_override()
        assert ov is not None
        assert ov.get("behaviour") == "off"


class TestTestFireRoute:
    def test_test_fire_runs_sanitiser_against_default_sample(self, app_client):
        db, _ = _mock_db()
        # Force the dispatch translator to return "" so the strip fallback
        # handles cleanup deterministically (no network). Task #492 retired
        # the direct sarvam_client patch — translate now flows through
        # llm.call_translate_with_dispatch.
        async def _empty_translate(*_a, **_kw):
            return ""
        with _patch_db(db), \
             patch("llm.call_translate_with_dispatch", new=_empty_translate):
            r = app_client.post("/admin/assamese-purity/test", json={})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["raw"]
        # Live sanitiser cleaned the leaky sample — the obvious leakage
        # tokens should be gone from the cleaned output.
        assert "me uses" not in body["cleaned"]
        assert "ssible" not in body["cleaned"]
        # Diagnostic block has the required fields the UI renders.
        diag = body["diag"]
        for k in ("action", "ratio", "threshold", "behaviour"):
            assert k in diag

    def test_test_fire_rejects_empty_sample(self, app_client):
        db, _ = _mock_db()
        async def _empty_translate(*_a, **_kw):
            return ""
        with _patch_db(db), \
             patch("llm.call_translate_with_dispatch", new=_empty_translate):
            r = app_client.post("/admin/assamese-purity/test", json={"sample": "   "})
        assert r.status_code == 400


class TestAuditLog:
    """Task #424 — append-only audit log of override edits."""

    def test_patch_writes_audit_row_with_admin_and_diff(self, app_client, mock_admin):
        db, _ = _mock_db()
        # Pre-existing override so PATCH has a meaningful "before" snapshot.
        db.api_config.find_one = AsyncMock(return_value={
            "assamese_purity_override": {
                "behaviour": "strip", "threshold": 0.10, "updated_by": "prev",
            }
        })
        with _patch_db(db):
            r = app_client.patch(
                "/admin/assamese-purity",
                json={"behaviour": "off"},
            )
        assert r.status_code == 200
        audit_coll = db.__getitem__.return_value
        audit_coll.insert_one.assert_awaited()
        doc = audit_coll.insert_one.call_args.args[0]
        assert doc["action"] == "patch"
        assert doc["admin_email"] == mock_admin["email"]
        assert doc["before"]["behaviour"] == "strip"
        assert doc["after"]["behaviour"] == "off"
        assert "ts" in doc

    def test_delete_writes_audit_row_with_before_snapshot(self, app_client, mock_admin):
        from lang_sanitizer import apply_runtime_override
        apply_runtime_override(behaviour="off", threshold=0.5, updated_by="seed")
        db, _ = _mock_db()
        db.api_config.find_one = AsyncMock(return_value={
            "assamese_purity_override": {
                "behaviour": "off", "threshold": 0.5, "updated_by": "seed",
            }
        })
        with _patch_db(db):
            r = app_client.delete("/admin/assamese-purity")
        assert r.status_code == 200
        audit_coll = db.__getitem__.return_value
        audit_coll.insert_one.assert_awaited()
        doc = audit_coll.insert_one.call_args.args[0]
        assert doc["action"] == "delete"
        assert doc["admin_email"] == mock_admin["email"]
        assert doc["before"]["behaviour"] == "off"
        assert doc["after"] is None

    def test_audit_failure_does_not_break_patch(self, app_client):
        """Audit is best-effort — losing a row must NEVER fail the user
        action. The PATCH must still return 200 if the audit insert dies."""
        db, _ = _mock_db()
        audit_coll = db.__getitem__.return_value
        audit_coll.insert_one = AsyncMock(side_effect=RuntimeError("mongo down"))
        with _patch_db(db):
            r = app_client.patch(
                "/admin/assamese-purity",
                json={"behaviour": "off"},
            )
        assert r.status_code == 200

    def test_get_audit_returns_recent_rows(self, app_client):
        from datetime import datetime as _dt, timezone as _tz
        db, _ = _mock_db()
        rows = [
            {"ts": _dt(2026, 4, 17, 10, tzinfo=_tz.utc), "action": "patch",
             "admin_email": "a@b.c", "before": None,
             "after": {"behaviour": "off", "threshold": 0.05}},
            {"ts": _dt(2026, 4, 16, 9, tzinfo=_tz.utc), "action": "delete",
             "admin_email": "x@y.z",
             "before": {"behaviour": "off"}, "after": None},
        ]
        audit_coll = db.__getitem__.return_value
        cursor = audit_coll.find.return_value
        cursor.to_list = AsyncMock(return_value=rows)
        with _patch_db(db):
            r = app_client.get("/admin/assamese-purity/audit")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert len(body["entries"]) == 2
        # ts must be ISO-formatted for the React side.
        assert isinstance(body["entries"][0]["ts"], str)
        assert "2026" in body["entries"][0]["ts"]
        assert body["entries"][0]["action"] == "patch"

    def test_get_audit_clamps_limit(self, app_client):
        db, _ = _mock_db()
        audit_coll = db.__getitem__.return_value
        cursor = audit_coll.find.return_value
        cursor.to_list = AsyncMock(return_value=[])
        with _patch_db(db):
            r = app_client.get("/admin/assamese-purity/audit?limit=9999")
        assert r.status_code == 200
        # Cursor.limit should have been called with the clamped value (100),
        # not the requested 9999.
        cursor.limit.assert_called_with(100)

    def test_get_audit_handles_mongo_failure_gracefully(self, app_client):
        db, _ = _mock_db()
        audit_coll = db.__getitem__.return_value
        audit_coll.find = MagicMock(side_effect=RuntimeError("mongo down"))
        with _patch_db(db):
            r = app_client.get("/admin/assamese-purity/audit")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is False
        assert body["entries"] == []

    def test_get_audit_applies_offset_and_filters(self, app_client):
        """Task #430 — `since`/`until`/`admin_email`/`offset` must
        flow through to the mongo query so admins can bisect history."""
        db, _ = _mock_db()
        audit_coll = db.__getitem__.return_value
        audit_coll.count_documents = AsyncMock(return_value=42)
        cursor = audit_coll.find.return_value
        cursor.to_list = AsyncMock(return_value=[])
        with _patch_db(db):
            r = app_client.get(
                "/admin/assamese-purity/audit"
                "?offset=20&limit=10"
                "&since=2026-01-01T00:00:00Z"
                "&until=2026-02-01T00:00:00Z"
                "&admin_email=ops@syrabit.ai",
            )
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 42
        assert body["offset"] == 20
        assert body["limit"] == 10
        # The mongo query must contain ts bounds and an i-regex on email.
        call_filter = audit_coll.find.call_args.args[0]
        assert "$gte" in call_filter["ts"] and "$lte" in call_filter["ts"]
        assert call_filter["admin_email"]["$options"] == "i"
        assert "ops" in call_filter["admin_email"]["$regex"]
        assert "syrabit" in call_filter["admin_email"]["$regex"]
        # count_documents must use the same filter (not {}).
        count_filter = audit_coll.count_documents.call_args.args[0]
        assert count_filter == call_filter
        # Cursor pipeline is sort → skip(20) → limit(10).
        cursor.skip.assert_called_with(20)
        cursor.limit.assert_called_with(10)

    def test_get_audit_rejects_bad_timestamp(self, app_client):
        db, _ = _mock_db()
        with _patch_db(db):
            r = app_client.get("/admin/assamese-purity/audit?since=not-a-date")
        assert r.status_code == 400
        assert "iso" in r.json()["detail"].lower()


class TestPersistedOverrideRoundTrip:
    def test_apply_persisted_override_seeds_in_memory_layer(self):
        """The lifespan loader must read the persisted doc on api boot
        and seed the in-memory override so behaviour survives restart."""
        import asyncio
        from lang_sanitizer import (
            get_behaviour, get_threshold, get_runtime_override,
        )
        from routes.cms_sarvam_health import (
            apply_persisted_assamese_purity_override,
        )
        api_cfg = MagicMock()
        api_cfg.find_one = AsyncMock(return_value={
            "assamese_purity_override": {
                "behaviour": "off",
                "threshold": 0.09,
                "updated_by": "boot-test",
            }
        })
        db = MagicMock()
        db.api_config = api_cfg
        with _patch_db(db):
            asyncio.run(
                apply_persisted_assamese_purity_override()
            )
        assert get_behaviour() == "off"
        assert get_threshold() == pytest.approx(0.09)
        ov = get_runtime_override()
        assert ov and ov.get("updated_by") == "boot-test"


# ──────────────────────────────────────────────────────────────────────
# Cross-worker propagation (Task #425)
#
# Each gunicorn worker reads the persisted override doc every
# `_ASM_REFRESH_INTERVAL_SECONDS` from a background loop scheduled in
# the lifespan hook. The unit tests above cover the loader function in
# isolation; the tests below additionally pin down the cross-worker
# *timing* contract:
#
#   • A PATCH made on worker A must be observable by worker B within
#     the documented propagation budget (~20s, i.e. one refresh cycle
#     plus jitter).
#   • A DELETE made on worker A must clear worker B's in-memory
#     override within the same budget.
#   • The 15s constant itself is part of the contract — if someone
#     bumps it above 20s, the runbook promise breaks and on-call
#     gets surprised.
# ──────────────────────────────────────────────────────────────────────


class _SharedMongoStub:
    """Tiny stateful stand-in for `db.api_config` shared by both
    simulated workers, so a write from worker A is visible to worker
    B's read. Implements only the minimum subset of motor semantics
    used by the route + loader code: a single doc with `$set` /
    `$unset` updates and `find_one` projection."""

    def __init__(self) -> None:
        self._doc: dict = {}

    async def find_one(self, filter_, projection=None):
        if not self._doc:
            return None
        return dict(self._doc)

    async def update_one(self, filter_, update, upsert=False):
        for k, v in (update.get("$set") or {}).items():
            self._doc[k] = v
        for k in (update.get("$unset") or {}).keys():
            self._doc.pop(k, None)
        return None


def _shared_db():
    api_cfg = _SharedMongoStub()
    db = MagicMock()
    db.api_config = api_cfg
    return db, api_cfg


class TestCrossWorkerPropagation:
    """Two workers, one shared mongo, one persisted-override doc.
    Worker A mutates via the route handlers; worker B picks up the
    change by running the same poll the background loop runs."""

    def test_propagation_budget_constant_is_within_runbook_promise(self):
        """If anyone shortens this loop too aggressively or — far
        worse — bumps it past 20s, the ops runbook lies. Lock it in."""
        from routes.cms_sarvam_health import _ASM_REFRESH_INTERVAL_SECONDS
        assert 0 < _ASM_REFRESH_INTERVAL_SECONDS <= 20, (
            f"Refresh interval {_ASM_REFRESH_INTERVAL_SECONDS}s violates "
            "the documented ~20s cross-worker propagation budget."
        )

    def test_patch_on_worker_a_propagates_to_worker_b_within_budget(
        self, app_client
    ):
        """Worker A serves the PATCH (writes the persisted doc).
        Worker B is a separate process — i.e. its in-memory override
        is empty — and only sees the change when its refresh loop
        ticks. Simulate a single tick by calling the loader directly,
        which is exactly what the loop does each cycle."""
        import asyncio
        from lang_sanitizer import (
            clear_runtime_override,
            get_behaviour,
            get_threshold,
            get_runtime_override,
        )
        from routes.cms_sarvam_health import (
            apply_persisted_assamese_purity_override,
        )

        db, _ = _shared_db()

        # ── Worker A: PATCH writes the persisted doc to shared mongo.
        with _patch_db(db):
            r = app_client.patch(
                "/admin/assamese-purity",
                json={"behaviour": "off", "threshold": 0.07},
            )
        assert r.status_code == 200

        # ── Worker B: simulate a fresh process — no in-memory override.
        clear_runtime_override()
        assert get_runtime_override() is None

        # One refresh-loop tick on worker B reads the persisted doc.
        with _patch_db(db):
            asyncio.run(
                apply_persisted_assamese_purity_override()
            )

        # Worker B now sees worker A's PATCH.
        assert get_behaviour() == "off"
        assert get_threshold() == pytest.approx(0.07)
        ov = get_runtime_override()
        assert ov and ov.get("behaviour") == "off"
        assert ov.get("threshold") == pytest.approx(0.07)

    def test_delete_on_worker_a_propagates_to_worker_b_within_budget(
        self, app_client
    ):
        """Same shape as the PATCH test, but for DELETE: worker A
        clears the persisted doc, worker B starts WITH an in-memory
        override (e.g. seeded from boot or a prior PATCH it served
        itself), and one refresh tick must drop it."""
        import asyncio
        from lang_sanitizer import (
            apply_runtime_override,
            clear_runtime_override,
            get_runtime_override,
        )
        from routes.cms_sarvam_health import (
            apply_persisted_assamese_purity_override,
        )

        db, api_cfg = _shared_db()

        # Pre-seed mongo so worker A has something to delete.
        api_cfg._doc["assamese_purity_override"] = {
            "behaviour": "off",
            "threshold": 0.07,
            "updated_by": "ops@syrabit.ai",
        }

        # ── Worker A: DELETE removes the persisted doc.
        with _patch_db(db):
            r = app_client.delete("/admin/assamese-purity")
        assert r.status_code == 200
        assert "assamese_purity_override" not in api_cfg._doc

        # ── Worker B: starts with a stale in-memory override (it
        # served the PATCH earlier and hasn't ticked yet).
        apply_runtime_override(
            behaviour="off", threshold=0.07, updated_by="worker-b-stale"
        )
        assert get_runtime_override() is not None

        # One refresh-loop tick on worker B reconciles → cleared.
        with _patch_db(db):
            asyncio.run(
                apply_persisted_assamese_purity_override()
            )
        assert get_runtime_override() is None
        clear_runtime_override()

    def test_refresh_loop_picks_up_change_from_a_running_tick(
        self, app_client, monkeypatch
    ):
        """End-to-end timing check: actually run the background loop
        with a tiny interval, mutate the persisted doc mid-flight,
        and assert worker B's in-memory state reflects the change
        within the propagation budget. Guards against someone removing
        the `await apply_persisted_assamese_purity_override()` call
        from the loop body (which the loader-only tests would miss)."""
        import asyncio
        from lang_sanitizer import (
            clear_runtime_override, get_runtime_override,
        )
        from routes import cms_sarvam_health as mod

        db, api_cfg = _shared_db()

        # Compress the loop interval so the test stays fast while
        # still exercising the real loop body. The production budget
        # (15s) is asserted separately above; here we only need to
        # prove the loop calls the loader on every tick.
        monkeypatch.setattr(mod, "_ASM_REFRESH_INTERVAL_SECONDS", 0.05)

        async def _scenario():
            clear_runtime_override()
            with _patch_db(db):
                task = asyncio.create_task(mod._assamese_purity_refresh_loop())
                try:
                    # Mid-flight write from "worker A".
                    api_cfg._doc["assamese_purity_override"] = {
                        "behaviour": "strip",
                        "threshold": 0.11,
                        "updated_by": "live-loop-test",
                    }
                    # Worker B's loop should observe within a few
                    # ticks; cap the wait well under the prod budget.
                    deadline = asyncio.get_event_loop().time() + 2.0
                    while asyncio.get_event_loop().time() < deadline:
                        ov = get_runtime_override()
                        if ov and ov.get("behaviour") == "strip":
                            return ov
                        await asyncio.sleep(0.05)
                    return get_runtime_override()
                finally:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

        ov = asyncio.run(_scenario())
        assert ov is not None, "refresh loop never picked up the change"
        assert ov.get("behaviour") == "strip"
        assert ov.get("threshold") == pytest.approx(0.11)
