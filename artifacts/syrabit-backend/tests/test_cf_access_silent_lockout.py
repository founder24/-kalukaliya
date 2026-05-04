"""Task #707 — silent-lockout watcher tests.

Covers:

1. ``cf_access.cf_access_config_fingerprint`` is sensitive to env changes
   (AUD rotation, enforce flip, break-glass activation) — without this
   sensitivity, the watcher's "since change" anchor would never advance
   when an operator silently rotates a tag.
2. ``cf_access.record_cf_access_config_change`` upserts the persisted
   state on first run and writes a fresh ``changed_at`` only when the
   fingerprint actually changes.
3. ``server._cf_access_silent_lockout_check_once`` skips silently before
   the threshold and pages exactly once after the threshold + the
   most-recent admin login is older than the change.
4. The lifespan registers the watcher loop + threshold default exists.
"""
from __future__ import annotations

import ast
import asyncio
import importlib
import pathlib
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest


SERVER_PY = pathlib.Path(__file__).resolve().parent.parent / "server.py"


def _run(coro):
    return asyncio.run(coro)


# ── Fakes ────────────────────────────────────────────────────────────────────

class _FakeCollection:
    def __init__(self):
        self.docs: list[dict] = []
        self.indexes: list[Any] = []

    async def find_one(self, query, projection=None):
        for d in self.docs:
            return dict(d)
        return None

    async def update_one(self, query, update, upsert=False):
        if not self.docs:
            self.docs.append({})
        self.docs[0].update(update.get("$set", {}))

    async def insert_one(self, doc):
        self.docs.append(dict(doc))

    async def create_index(self, *args, **kwargs):
        self.indexes.append((args, kwargs))

    def find(self, query=None, projection=None):
        rows = list(self.docs)
        if query and "success" in query:
            rows = [r for r in rows if r.get("success") == query["success"]]
        return _FakeCursor(rows)


class _FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, key, direction):
        rev = direction < 0
        self.rows = sorted(self.rows, key=lambda r: r.get(key) or datetime.min, reverse=rev)
        return self

    def limit(self, n):
        self.rows = self.rows[:n]
        return self

    async def to_list(self, length=None):
        return list(self.rows[: (length or len(self.rows))])


class _FakeDB:
    def __init__(self):
        self.api_config = _FakeCollection()
        self.admin_login_log = _FakeCollection()


# ── 1. Fingerprint sensitivity ───────────────────────────────────────────────

def test_fingerprint_changes_when_admin_aud_rotates(monkeypatch):
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "syrabit-test")
    monkeypatch.setenv("CF_ACCESS_AUD_ADMIN", "aud-tag-old")
    monkeypatch.setenv("CF_ACCESS_AUD_INTERNAL", "")
    monkeypatch.setenv("CF_ACCESS_ENFORCE", "true")
    monkeypatch.setenv("CF_ACCESS_BREAK_GLASS", "")
    monkeypatch.setenv("CF_ACCESS_BREAK_GLASS_TOKEN", "")

    import cf_access
    importlib.reload(cf_access)
    fp_old = cf_access.cf_access_config_fingerprint()

    monkeypatch.setenv("CF_ACCESS_AUD_ADMIN", "aud-tag-new")
    importlib.reload(cf_access)
    fp_new = cf_access.cf_access_config_fingerprint()

    assert fp_old["admin_aud_hash"] and fp_new["admin_aud_hash"]
    assert fp_old["admin_aud_hash"] != fp_new["admin_aud_hash"], (
        "AUD rotation must change the fingerprint so the watcher arms"
    )


def test_fingerprint_changes_on_break_glass_activation(monkeypatch):
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "syrabit-test")
    monkeypatch.setenv("CF_ACCESS_AUD_ADMIN", "aud")
    monkeypatch.setenv("CF_ACCESS_BREAK_GLASS", "")
    import cf_access
    importlib.reload(cf_access)
    fp_off = cf_access.cf_access_config_fingerprint()
    monkeypatch.setenv("CF_ACCESS_BREAK_GLASS", "true")
    importlib.reload(cf_access)
    fp_on = cf_access.cf_access_config_fingerprint()
    assert fp_off["break_glass_env"] is False
    assert fp_on["break_glass_env"] is True


# ── 2. record_cf_access_config_change persistence ────────────────────────────

def test_record_change_writes_initial_state(monkeypatch):
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "t")
    monkeypatch.setenv("CF_ACCESS_AUD_ADMIN", "a")
    import cf_access
    importlib.reload(cf_access)
    db = _FakeDB()
    state = _run(cf_access.record_cf_access_config_change(db))
    assert state["fingerprint"]["team_domain"] == "t"
    # changed_at recorded
    assert state.get("changed_at")
    assert db.api_config.docs[0]["cf_access_config_state"]["changed_at"]


def test_record_change_no_op_when_fingerprint_matches(monkeypatch):
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "t")
    monkeypatch.setenv("CF_ACCESS_AUD_ADMIN", "a")
    import cf_access
    importlib.reload(cf_access)
    db = _FakeDB()
    first = _run(cf_access.record_cf_access_config_change(db))
    second = _run(cf_access.record_cf_access_config_change(db))
    assert first.get("changed_at") == second.get("changed_at"), (
        "Identical fingerprint must not advance changed_at"
    )


def test_record_change_advances_on_aud_rotation(monkeypatch):
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", "t")
    monkeypatch.setenv("CF_ACCESS_AUD_ADMIN", "old")
    import cf_access
    importlib.reload(cf_access)
    db = _FakeDB()
    first = _run(cf_access.record_cf_access_config_change(db))
    monkeypatch.setenv("CF_ACCESS_AUD_ADMIN", "new")
    importlib.reload(cf_access)
    second = _run(cf_access.record_cf_access_config_change(db))
    assert second.get("previous_fingerprint") == first["fingerprint"]
    assert second.get("changed_at") != first.get("changed_at")


# ── 3. Watcher iteration logic (ast-extracted to avoid importing server.py) ──

def _server_source() -> str:
    return SERVER_PY.read_text(encoding="utf-8")


def _find_function(name: str) -> ast.AsyncFunctionDef:
    tree = ast.parse(_server_source())
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(name)


def _extract_check_once(monkeypatch, fake_db, fake_dispatch, threshold_hours=24.0,
                        team_domain="t", admin_aud="a"):
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", team_domain)
    monkeypatch.setenv("CF_ACCESS_AUD_ADMIN", admin_aud)
    import cf_access as _cf_mod
    importlib.reload(_cf_mod)

    import sys
    import types
    # Stub metrics module so the function can `import metrics as _metrics`.
    mmod = types.ModuleType("metrics")
    mmod._ALERT_THRESHOLDS = {"cf_access_silent_lockout_hours": threshold_hours}
    mmod._ALERT_THRESHOLDS_DEFAULT = {"cf_access_silent_lockout_hours": 24}
    mmod._dispatch_alert = fake_dispatch  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "metrics", mmod)

    fn_node = _find_function("_cf_access_silent_lockout_check_once")
    # Also extract the _parse_iso_dt helper used by the function.
    tree = ast.parse(_server_source())
    helper_src = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_parse_iso_dt":
            helper_src = ast.get_source_segment(_server_source(), node)
            break
    assert helper_src, "_parse_iso_dt helper missing from server.py"

    src = ast.get_source_segment(_server_source(), fn_node)

    namespace: dict[str, Any] = {
        "db": fake_db,
        "datetime": datetime,
        "timedelta": timedelta,
        "timezone": timezone,
        "logger": __import__("logging").getLogger("test.silent_lockout"),
        "_CF_ACCESS_SILENT_LOCKOUT_ALERT_TYPE": "cf_access_admin_silent_lockout",
    }
    exec(compile(helper_src, "<parse_helper>", "exec"), namespace)
    exec(compile(src, "<check_once>", "exec"), namespace)
    return namespace["_cf_access_silent_lockout_check_once"]


def test_check_once_skips_when_no_recorded_change(monkeypatch):
    db = _FakeDB()
    calls: list[dict] = []

    async def _dispatch(*a, **kw):
        calls.append({"args": a, "kw": kw})

    fn = _extract_check_once(monkeypatch, db, _dispatch)
    out = _run(fn())
    assert out.get("skipped") == "no_recorded_change"
    assert calls == []


def test_check_once_skips_within_threshold(monkeypatch):
    db = _FakeDB()
    db.api_config.docs.append({
        "cf_access_config_state": {
            "changed_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            "fingerprint": {},
        }
    })
    calls: list[dict] = []

    async def _dispatch(*a, **kw):
        calls.append({"args": a, "kw": kw})

    fn = _extract_check_once(monkeypatch, db, _dispatch, threshold_hours=24.0)
    out = _run(fn())
    assert out.get("skipped") == "within_threshold"
    assert calls == []


def test_check_once_skips_when_login_after_change(monkeypatch):
    db = _FakeDB()
    changed = datetime.now(timezone.utc) - timedelta(hours=30)
    db.api_config.docs.append({
        "cf_access_config_state": {
            "changed_at": changed.isoformat(),
            "fingerprint": {},
        }
    })
    db.admin_login_log.docs.append({
        "success": True,
        "ts": datetime.now(timezone.utc) - timedelta(hours=2),
    })
    calls: list[dict] = []

    async def _dispatch(*a, **kw):
        calls.append({"args": a, "kw": kw})

    fn = _extract_check_once(monkeypatch, db, _dispatch, threshold_hours=24.0)
    out = _run(fn())
    assert out.get("skipped") == "login_seen_after_change"
    assert calls == []


def test_check_once_pages_when_silent_past_threshold(monkeypatch):
    db = _FakeDB()
    changed = datetime.now(timezone.utc) - timedelta(hours=30)
    db.api_config.docs.append({
        "cf_access_config_state": {
            "changed_at": changed.isoformat(),
            "fingerprint": {},
        }
    })
    # Either no logins, or only stale ones predating the change.
    db.admin_login_log.docs.append({
        "success": True,
        "ts": changed - timedelta(hours=5),
        "email": "ops@syrabit.ai",
    })
    calls: list[dict] = []

    async def _dispatch(alert_type, title, body, threshold_snapshot=None,
                        force=False, mark_synthetic=False):
        calls.append({
            "alert_type": alert_type,
            "title": title,
            "body": body,
            "threshold_snapshot": threshold_snapshot,
        })

    fn = _extract_check_once(monkeypatch, db, _dispatch, threshold_hours=24.0)
    out = _run(fn())
    assert out.get("alerted") is True
    assert len(calls) == 1
    assert calls[0]["alert_type"] == "cf_access_admin_silent_lockout"
    snap = calls[0]["threshold_snapshot"]
    assert snap["metric"] == "cf_access.hours_since_change_without_login"
    assert snap["value"] == 24.0
    assert snap["actual"] >= 24.0


# ── 4. Lifespan + threshold default ──────────────────────────────────────────

def test_loop_re_arms_state_when_missing():
    """The watcher loop must re-call ``record_cf_access_config_change``
    on every tick so a transient boot-time Mongo outage does not leave
    the watcher permanently stuck in ``no_recorded_change``.
    """
    src = _server_source()
    tree = ast.parse(src)
    loop_fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_cf_access_silent_lockout_loop":
            loop_fn = node
            break
    assert loop_fn is not None
    body_src = ast.get_source_segment(src, loop_fn)
    assert "record_cf_access_config_change" in body_src, (
        "Loop must re-arm the persisted change anchor each iteration"
    )


def test_lifespan_schedules_silent_lockout_loop():
    src = _server_source()
    # Task #332 — accept either the legacy `asyncio.create_task` form
    # or the takeover-aware `_aca_create_task(..., key="cf-access-silent-lockout")`
    # wrapper that gates the loop on `_aca_jobs_takeover()`.
    assert (
        "asyncio.create_task(_cf_access_silent_lockout_loop())" in src
        or '_aca_create_task(_cf_access_silent_lockout_loop(), key="cf-access-silent-lockout")' in src
        or "_aca_create_task(_cf_access_silent_lockout_loop(), key='cf-access-silent-lockout')" in src
    ), "lifespan must register the silent-lockout watcher"


def test_lifespan_calls_record_cf_access_config_change():
    src = _server_source()
    assert "record_cf_access_config_change" in src


def test_alert_thresholds_default_includes_silent_lockout():
    import importlib as _i
    import metrics
    _i.reload(metrics)
    assert "cf_access_silent_lockout_hours" in metrics._ALERT_THRESHOLDS_DEFAULT
