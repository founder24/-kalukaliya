"""Task #386 — flag-gated helpers (Speed features, Tiered Cache,
D1 mirror, Durable-Object chat).

These tests cover the rollback story: when a flag is OFF the helper
must report ``applied=False`` / ``enabled=False`` and never reach a
real Cloudflare endpoint. When ON it dispatches to the underlying
implementation. Each helper is exercised in both states without any
Cloudflare credentials, because the snapshot/dispatch contract must
hold even on a CI box with no CF_ZONE_ID.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


# ── cf_speed_smoke ─────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_speed_smoke_no_op_when_flag_off(monkeypatch):
    monkeypatch.setattr("config.CF_SPEED_FEATURES_ON", False, raising=False)
    from cf_speed_smoke import apply_speed_features
    out = await apply_speed_features()
    assert out == {"applied": False, "reason": "flag_off"}


@pytest.mark.asyncio
async def test_speed_smoke_invokes_optimize_when_flag_on(monkeypatch):
    monkeypatch.setattr("config.CF_SPEED_FEATURES_ON", True, raising=False)
    invoked: list[bool] = []

    async def _fake_optimize():
        invoked.append(True)
        return {"polish": {"ok": True}}

    import cf_enterprise
    monkeypatch.setattr(cf_enterprise, "speed_optimize_all", _fake_optimize, raising=False)

    from cf_speed_smoke import apply_speed_features
    out = await apply_speed_features()
    assert out["applied"] is True
    assert out["result"] == {"polish": {"ok": True}}
    assert invoked == [True]


@pytest.mark.asyncio
async def test_polish_smoke_parses_cf_polished_header(monkeypatch):
    """The smoke check must surface cf-polished + cf-bgj from the
    image probe response so the cf-health row knows Polish + Mirage
    are live."""
    import cf_speed_smoke

    class _FakeResp:
        status_code = 200
        headers = {
            "cf-polished": "qual=85",
            "cf-bgj": "imgq=85",
            "cf-cache-status": "HIT",
            "cf-ray": "abc123",
            "content-type": "image/jpeg",
        }

    class _FakeClient:
        def __init__(self, *a, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **kw): return _FakeResp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient, raising=False)
    out = await cf_speed_smoke.polish_smoke("https://example.com/x.jpg")
    assert out["ok"] is True
    assert out["cf_polished"] == "qual=85"
    assert out["polish_active"] is True
    assert out["mirage_active"] is True


# ── cf_tiered_cache ────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_tiered_cache_no_op_when_flag_off(monkeypatch):
    monkeypatch.setattr("config.CF_TIERED_CACHE_ON", False, raising=False)
    from cf_tiered_cache import apply_tiered_cache, purge_by_cache_tags
    assert (await apply_tiered_cache())["applied"] is False
    out = await purge_by_cache_tags(["syrabit-subject-12"])
    assert out == {"purged": False, "reason": "flag_off", "tags": ["syrabit-subject-12"]}


@pytest.mark.asyncio
async def test_tiered_cache_dispatches_when_flag_on(monkeypatch):
    monkeypatch.setattr("config.CF_TIERED_CACHE_ON", True, raising=False)
    import cf_enterprise

    state = {"value": "off"}

    async def _fake_status():
        return dict(state)

    async def _fake_enable():
        state["value"] = "on"
        return dict(state)

    async def _fake_purge(tags):
        return {"id": "purge-123", "tags": tags}

    monkeypatch.setattr(cf_enterprise, "tiered_cache_status", _fake_status, raising=False)
    monkeypatch.setattr(cf_enterprise, "tiered_cache_enable", _fake_enable, raising=False)
    monkeypatch.setattr(cf_enterprise, "purge_by_tags", _fake_purge, raising=False)

    from cf_tiered_cache import apply_tiered_cache, purge_by_cache_tags
    out = await apply_tiered_cache()
    assert out["applied"] is True
    assert out["before"] == "off"
    assert out["after"] == "on"

    purge = await purge_by_cache_tags(["syrabit-chapter-9"])
    assert purge["purged"] is True
    assert purge["tags"] == ["syrabit-chapter-9"]


# ── d1_mirror ──────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_d1_mirror_no_op_when_flag_off(monkeypatch):
    monkeypatch.setattr("config.D1_MIRROR_ON", False, raising=False)
    from d1_mirror import export_extended_payload, sync_extended, lag_snapshot
    assert await export_extended_payload(db=None) == {}
    out = await sync_extended(db=None)
    assert out == {"success": False, "reason": "flag_off"}
    assert lag_snapshot()["enabled"] is False


@pytest.mark.asyncio
async def test_d1_mirror_records_lag_on_success(monkeypatch):
    monkeypatch.setattr("config.D1_MIRROR_ON", True, raising=False)
    from d1_mirror import sync_extended, lag_snapshot, reset_state
    reset_state()

    class _FakeColl:
        def __init__(self, rows): self._rows = rows
        def find(self, *a, **kw): return self
        def sort(self, *a, **kw): return self
        async def to_list(self, _n): return list(self._rows)

    class _FakeDB:
        seo_meta = _FakeColl([{"route": "/x", "meta_title": "X"}])
        audit_log = _FakeColl([{"id": "a1", "action": "edit"}])
        syllabus_map = _FakeColl([{"topic_id": "t1", "topic_slug": "x"}])

    import d1_sync

    async def _fake_trigger(payload):
        return True

    monkeypatch.setattr(d1_sync, "trigger_d1_sync", _fake_trigger, raising=False)

    out = await sync_extended(db=_FakeDB())
    assert out["success"] is True
    assert set(out["tables"]) == {"seo_meta", "audit_log", "syllabus_map"}
    snap = lag_snapshot()
    assert snap["last_sync_ok"] is True
    assert snap["row_counts"] == {"seo_meta": 1, "audit_log": 1, "syllabus_map": 1}
    assert snap["lag_seconds"] is not None and snap["lag_seconds"] < 5
    reset_state()


# ── do_chat ────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_do_chat_falls_back_to_in_process_when_flag_off(monkeypatch):
    monkeypatch.setattr("config.DO_CHAT_ON", False, raising=False)
    from do_chat import (
        get_session, put_session, delete_session, rate_check,
        snapshot, reset,
    )
    reset()

    assert await get_session("s1") is None
    assert await put_session("s1", {"hello": "world"}, ttl=60) is True
    got = await get_session("s1")
    assert got == {"hello": "world"}
    assert await delete_session("s1") is True
    assert await get_session("s1") is None

    # Rate limit fallback — 3 / minute window
    allowed1, rem1 = await rate_check("ip:1.2.3.4", limit=2, window_s=60)
    allowed2, rem2 = await rate_check("ip:1.2.3.4", limit=2, window_s=60)
    allowed3, rem3 = await rate_check("ip:1.2.3.4", limit=2, window_s=60)
    assert allowed1 is True and allowed2 is True
    assert allowed3 is False
    assert rem3 == 0

    snap = snapshot()
    assert snap["enabled"] is False
    assert snap["fallback_requests_total"] >= 1
    assert snap["rate_check_blocked"] >= 1
    reset()


@pytest.mark.asyncio
async def test_do_chat_falls_back_when_edge_unavailable(monkeypatch):
    """When the flag is on but the edge worker is unreachable, the
    helper must fall through to the in-process backend rather than
    losing chat state."""
    monkeypatch.setattr("config.DO_CHAT_ON", True, raising=False)
    import do_chat
    monkeypatch.setattr(do_chat, "_DO_BASE", "https://edge.invalid", raising=False)
    monkeypatch.setattr(do_chat, "_DO_SECRET", "test-secret", raising=False)

    async def _fake_request(method, path, **_kw):
        # Simulate a network error → returns None per do_chat contract.
        return None

    monkeypatch.setattr(do_chat, "_do_request", _fake_request, raising=False)
    do_chat.reset()

    ok = await do_chat.put_session("s2", {"a": 1}, ttl=10)
    assert ok is True
    got = await do_chat.get_session("s2")
    assert got == {"a": 1}
    snap = do_chat.snapshot()
    assert snap["enabled"] is True
    assert snap["fallback_requests_total"] >= 2
    do_chat.reset()


# ── live-path integrations (Task #386 review remediation) ────────────────────
@pytest.mark.asyncio
async def test_d1_sync_full_includes_extended_tables_when_flag_on(monkeypatch):
    """sync_full must fold the extended payload (seo_meta + audit_log
    + syllabus_map) into the same trigger when D1_MIRROR_ON is set,
    and update the d1_mirror lag tracker to reflect the live sync."""
    monkeypatch.setattr("config.D1_MIRROR_ON", True, raising=False)
    import d1_sync, d1_mirror
    d1_mirror.reset_state()

    captured = {}

    async def _fake_export_catalog(_db):
        return {"boards": [{"slug": "seba"}]}

    async def _fake_trigger(payload):
        captured["payload"] = payload
        return True

    async def _fake_seo(_db):
        return [{"route": "/x", "meta_title": "X"}]

    async def _fake_audit(_db, max_rows=5000):
        return [{"id": "a1"}]

    async def _fake_syl(_db):
        return [{"topic_id": "t1"}]

    monkeypatch.setattr(d1_sync, "export_content_catalog", _fake_export_catalog, raising=False)
    monkeypatch.setattr(d1_sync, "trigger_d1_sync", _fake_trigger, raising=False)
    monkeypatch.setattr(d1_mirror, "_export_seo_meta", _fake_seo, raising=False)
    monkeypatch.setattr(d1_mirror, "_export_audit_log", _fake_audit, raising=False)
    monkeypatch.setattr(d1_mirror, "_export_syllabus_map", _fake_syl, raising=False)

    out = await d1_sync.sync_full(db=object())
    assert out["success"] is True
    assert set(captured["payload"].keys()) == {"boards", "seo_meta", "audit_log", "syllabus_map"}
    snap = d1_mirror.lag_snapshot()
    assert snap["last_sync_ok"] is True
    assert snap["row_counts"] == {"seo_meta": 1, "audit_log": 1, "syllabus_map": 1}
    d1_mirror.reset_state()


@pytest.mark.asyncio
async def test_d1_sync_full_skips_extended_when_flag_off(monkeypatch):
    monkeypatch.setattr("config.D1_MIRROR_ON", False, raising=False)
    import d1_sync

    captured = {}

    async def _fake_export_catalog(_db):
        return {"boards": [{"slug": "seba"}]}

    async def _fake_trigger(payload):
        captured["payload"] = payload
        return True

    monkeypatch.setattr(d1_sync, "export_content_catalog", _fake_export_catalog, raising=False)
    monkeypatch.setattr(d1_sync, "trigger_d1_sync", _fake_trigger, raising=False)

    await d1_sync.sync_full(db=object())
    assert "seo_meta" not in captured["payload"]
    assert "audit_log" not in captured["payload"]


@pytest.mark.asyncio
async def test_admin_content_purge_fires_cache_tags_on_subject_event(monkeypatch):
    """_schedule_prerender_refresh must dispatch a cache-tag purge
    keyed on the affected entity when an admin write fires it."""
    monkeypatch.setattr("config.CF_TIERED_CACHE_ON", True, raising=False)
    captured = []

    import cf_tiered_cache

    async def _fake_purge(tags):
        captured.append(list(tags))
        return {"purged": True, "tags": tags}

    monkeypatch.setattr(cf_tiered_cache, "purge_by_cache_tags", _fake_purge, raising=False)

    # Stub out pages_deploy.schedule_refresh so the test doesn't try
    # to fire a real Cloudflare deploy hook.
    import pages_deploy
    monkeypatch.setattr(pages_deploy, "schedule_refresh", lambda *a, **k: None, raising=False)

    from routes import admin_content
    admin_content._schedule_prerender_refresh("subject_updated:physics")
    # Give the asyncio task a tick to run.
    import asyncio
    await asyncio.sleep(0.05)
    assert captured, "purge_by_cache_tags was never called"
    assert "syrabit-html" in captured[0]
    assert "syrabit-subject-physics" in captured[0]


@pytest.mark.asyncio
async def test_chat_rate_limit_consults_do_when_flag_on(monkeypatch):
    """auth_deps.rate_limit_chat_optional must call do_chat.rate_check
    for logged-in users when DO_CHAT_ON is set."""
    monkeypatch.setattr("config.DO_CHAT_ON", True, raising=False)
    calls = []

    async def _fake_rate(key, *, limit, window_s):
        calls.append((key, limit, window_s))
        return True, max(0, limit - 1)

    import do_chat
    monkeypatch.setattr(do_chat, "rate_check", _fake_rate, raising=False)
    # Force is_enabled to return True without the import-time read.
    monkeypatch.setattr(do_chat, "is_enabled", lambda: True, raising=False)

    import auth_deps
    # Bypass real rate-limit math by making the local check pass.
    monkeypatch.setattr(auth_deps, "check_rate_limit", lambda *a, **kw: True, raising=False)

    class _Req:
        headers = {}
        cookies = {}

    user = await auth_deps.rate_limit_chat_optional(
        request=_Req(), response=_Req(), user={"id": "u-1", "plan": "free"},
        syrabit_device=None,
    )
    assert user["id"] == "u-1"
    assert calls, "do_chat.rate_check was never invoked"
    assert calls[0][0] == "chat:u-1"


@pytest.mark.asyncio
async def test_ssr_record_render_updates_snapshot():
    """cf_ssr_health.record_render must move the success_rate so the
    cf-health row reflects live SSR traffic."""
    from cf_ssr_health import record_render, snapshot, reset
    reset()
    record_render(True)
    record_render(True)
    record_render(False)
    snap = await snapshot()
    assert snap["rendered"] == 2
    assert snap["fallback"] == 1
    assert abs(snap["success_rate"] - (2 / 3)) < 1e-9
    reset()


# ── Translator gate hardening (review remediation) ──────────────────────────
@pytest.mark.asyncio
async def test_google_translate_short_circuits_when_workers_indic(monkeypatch):
    """providers.google_translate.translate must return None without
    issuing an HTTP request when TRANSLATE_PROVIDER=workers_indic, so
    any caller that imported the provider directly still obeys the
    flag."""
    monkeypatch.setattr("config.TRANSLATE_PROVIDER", "workers_indic", raising=False)
    called = {"http": False}

    async def _fake_get_token():
        called["http"] = True
        return "tok"

    from providers import google_translate as gt
    monkeypatch.setattr(gt, "_get_access_token", _fake_get_token, raising=False)
    monkeypatch.setattr(gt, "is_configured", lambda: True, raising=False)
    monkeypatch.setattr(gt, "_get_project_id", lambda: "proj-x", raising=False)

    out = await gt.translate("Hello world", target_lang="hi")
    assert out is None
    assert called["http"] is False


def test_translate_provider_default_is_workers_indic_when_unset(monkeypatch):
    """The reload of config.py with no TRANSLATE_PROVIDER env var
    must default to 'workers_indic' (review found the previous default
    of 'auto' did not match the task spec)."""
    monkeypatch.delenv("TRANSLATE_PROVIDER", raising=False)
    import importlib
    import config as _config
    reloaded = importlib.reload(_config)
    try:
        assert reloaded.TRANSLATE_PROVIDER == "workers_indic"
    finally:
        # Reload once more so subsequent tests see the env-controlled
        # value (pytest re-runs may have set TRANSLATE_PROVIDER=auto).
        importlib.reload(_config)


# ── D1 read-prefer (review remediation) ─────────────────────────────────────
@pytest.mark.asyncio
async def test_d1_read_with_fallback_prefers_d1_on_hit(monkeypatch):
    """read_with_fallback must return the D1 row and not invoke the
    Mongo loader when the edge worker reports a hit."""
    monkeypatch.setattr("config.D1_MIRROR_ON", True, raising=False)
    import d1_mirror
    d1_mirror.reset_state()
    monkeypatch.setattr(d1_mirror, "_d1_configured", lambda: True, raising=False)

    async def _fake_get(table, key_field, key_value):
        return {"route": key_value, "from": "d1"}

    monkeypatch.setattr(d1_mirror, "_d1_get", _fake_get, raising=False)

    mongo_calls = {"n": 0}

    async def _mongo_loader():
        mongo_calls["n"] += 1
        return {"route": "/x", "from": "mongo"}

    out = await d1_mirror.read_with_fallback("seo_meta", "route", "/x", _mongo_loader)
    assert out == {"route": "/x", "from": "d1"}
    assert mongo_calls["n"] == 0
    snap = d1_mirror.read_counters_snapshot()
    assert snap["d1_hit"] == 1 and snap["mongo_fallback"] == 0
    d1_mirror.reset_state()


@pytest.mark.asyncio
async def test_d1_read_with_fallback_uses_mongo_on_miss(monkeypatch):
    """read_with_fallback must fall through to the Mongo loader when
    D1 misses, errors, or when the flag is off."""
    monkeypatch.setattr("config.D1_MIRROR_ON", True, raising=False)
    import d1_mirror
    d1_mirror.reset_state()
    monkeypatch.setattr(d1_mirror, "_d1_configured", lambda: True, raising=False)

    async def _fake_get_miss(table, key_field, key_value):
        return None

    monkeypatch.setattr(d1_mirror, "_d1_get", _fake_get_miss, raising=False)

    async def _mongo_loader():
        return {"route": "/y", "from": "mongo"}

    out = await d1_mirror.read_with_fallback("seo_meta", "route", "/y", _mongo_loader)
    assert out == {"route": "/y", "from": "mongo"}
    snap = d1_mirror.read_counters_snapshot()
    assert snap["d1_miss"] == 1 and snap["mongo_fallback"] == 1
    d1_mirror.reset_state()


@pytest.mark.asyncio
async def test_d1_read_with_fallback_noop_when_flag_off(monkeypatch):
    monkeypatch.setattr("config.D1_MIRROR_ON", False, raising=False)
    import d1_mirror
    d1_mirror.reset_state()

    d1_calls = {"n": 0}

    async def _fake_get(*a, **k):
        d1_calls["n"] += 1
        return {"from": "d1"}

    monkeypatch.setattr(d1_mirror, "_d1_get", _fake_get, raising=False)

    async def _mongo_loader():
        return {"from": "mongo"}

    out = await d1_mirror.read_with_fallback("seo_meta", "route", "/z", _mongo_loader)
    assert out == {"from": "mongo"}
    assert d1_calls["n"] == 0
    d1_mirror.reset_state()


# ── DO chat session state (review remediation) ──────────────────────────────
@pytest.mark.asyncio
async def test_do_chat_session_round_trip_local_fallback(monkeypatch):
    """do_chat.get_session/put_session must round-trip via the
    in-process fallback when the edge is unreachable, so the chat
    handler keeps working with DO_CHAT_ON=true even before the
    Worker is deployed."""
    monkeypatch.setattr("config.DO_CHAT_ON", True, raising=False)
    import do_chat
    do_chat.reset()
    # Force the configured-edge check to false so put/get exercise
    # the local branch.
    monkeypatch.setattr(do_chat, "_do_configured", lambda: False, raising=False)

    payload = {
        "conversation_id": "conv-42",
        "user_id": "u-7",
        "last_provider": "workers-ai",
    }
    ok = await do_chat.put_session("conv-42", payload, ttl=60)
    assert ok is True
    got = await do_chat.get_session("conv-42")
    assert got["last_provider"] == "workers-ai"
    assert got["conversation_id"] == "conv-42"
    snap = do_chat.snapshot()
    assert snap["session_put_total"] >= 1
    assert snap["session_get_total"] >= 1
    do_chat.reset()


# ── SSE typing channel (review remediation #2) ─────────────────────────────
@pytest.mark.asyncio
async def test_sse_typing_endpoint_emits_initial_state(monkeypatch):
    """``stream_chat_typing`` must produce a ``StreamingResponse`` with
    ``text/event-stream`` media type whose first data frame carries the
    current typing state from the DO (or its in-process fallback). We
    drive the generator directly to avoid TestClient hanging on the
    long-poll loop."""
    import do_chat
    do_chat.reset()
    monkeypatch.setattr(do_chat, "_do_configured", lambda: False, raising=False)
    await do_chat.put_typing("conv-sse", True, actor="assistant", ttl_ms=10_000)

    from routes.ai_chat import stream_chat_typing

    class _Req:
        async def is_disconnected(self): return False
    resp = await stream_chat_typing("conv-sse", _Req())
    assert resp.media_type == "text/event-stream"
    assert resp.headers.get("Cache-Control") == "no-cache"

    chunks = []
    agen = resp.body_iterator
    for _ in range(3):
        chunks.append(await agen.__anext__())
        joined = b"".join(c if isinstance(c, bytes) else c.encode() for c in chunks)
        if b"data:" in joined and b"\n\n" in joined:
            break
    await agen.aclose()
    text = b"".join(c if isinstance(c, bytes) else c.encode() for c in chunks).decode()
    assert "data:" in text
    assert '"typing": true' in text or '"typing":true' in text
    do_chat.reset()


# ── /admin/audit/recent (D1 read-prefer consumer) ───────────────────────────
@pytest.mark.asyncio
async def test_admin_audit_recent_reads_via_d1_helper(monkeypatch):
    """The new ``/admin/audit/recent`` route must defer to
    ``d1_mirror.read_audit_log_recent`` (the D1-first reader) so the
    mirror has a real production consumer."""
    captured = {}

    async def _fake_read(limit, db):
        captured["limit"] = int(limit)
        captured["db"] = db
        return [{"id": "a1", "actor": "u1", "action": "edit", "ts": 1}]

    import d1_mirror
    monkeypatch.setattr(d1_mirror, "read_audit_log_recent", _fake_read, raising=True)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes import admin_audit_recent
    monkeypatch.setattr(admin_audit_recent, "read_audit_log_recent",
                        _fake_read, raising=True)
    admin_audit_recent.init_admin_audit_recent(object())

    from auth_deps import get_admin_user
    app = FastAPI()
    app.dependency_overrides[get_admin_user] = lambda: {"id": "admin-1"}
    app.include_router(admin_audit_recent.router, prefix="/api")
    client = TestClient(app)
    res = client.get("/api/admin/audit/recent?limit=25")
    assert res.status_code == 200
    body = res.json()
    assert body["count"] == 1
    assert body["rows"][0]["id"] == "a1"
    assert captured["limit"] == 25


# ── seo_meta D1 override on /html/about (real consumer) ─────────────────────
@pytest.mark.asyncio
async def test_about_route_honours_d1_seo_meta_override(monkeypatch):
    """``/html/about`` must consult ``d1_mirror.read_seo_meta`` for a
    per-route override and use the returned title/description in the
    rendered HTML — proving the read-prefer wiring isn't dead code."""
    async def _fake_meta(route, db):
        assert route == "/about"
        return {"meta_title": "OVERRIDE_TITLE_X", "meta_description": "OVERRIDE_DESC_Y"}

    import d1_mirror
    monkeypatch.setattr(d1_mirror, "read_seo_meta", _fake_meta, raising=True)

    import seo_engine
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    cur = MagicMock()
    async def _to_list(_n=None): return []
    cur.to_list = _to_list
    fake_db = SimpleNamespace(
        seo_pages=MagicMock(),
        subjects=MagicMock(),
        chapters=MagicMock(),
    )
    fake_db.seo_pages.count_documents = AsyncMock(return_value=0)
    fake_db.subjects.count_documents = AsyncMock(return_value=0)
    fake_db.chapters.count_documents = AsyncMock(return_value=0)
    fake_db.seo_pages.aggregate = MagicMock(return_value=cur)
    monkeypatch.setattr(seo_engine, "_db", fake_db, raising=False)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(seo_engine.router, prefix="/api")
    res = TestClient(app).get("/api/seo/html/about")
    assert res.status_code == 200
    assert "OVERRIDE_TITLE_X" in res.text
    assert "OVERRIDE_DESC_Y" in res.text


# ── Cache-Tag emit/purge contract ───────────────────────────────────────────
def test_cache_tag_emit_and_purge_use_same_format():
    """The Cache-Tag tokens emitted by the SSR layer (build_cache_tag,
    hyphen-separated) MUST exactly match what the admin purge sends
    (_cache_tags_for_reason). A mismatch leaves stale HTML at the edge."""
    from cf_enterprise import build_cache_tag
    from routes.admin_content import _cache_tags_for_reason

    emitted = build_cache_tag("subject", "physics", "chapter", "laws")
    emitted_tokens = set(emitted.split())
    purge_subject = set(_cache_tags_for_reason("subject_updated:physics"))
    purge_chapter = set(_cache_tags_for_reason("chapter_updated:laws"))
    purge_topic = set(_cache_tags_for_reason("topic_updated:newton-laws"))

    assert "syrabit-subject-physics" in emitted_tokens
    assert "syrabit-chapter-laws" in emitted_tokens
    assert "syrabit-subject-physics" in purge_subject
    assert "syrabit-chapter-laws" in purge_chapter
    assert "syrabit-topic-newton-laws" in purge_topic
    # Ensure no purge token uses the old colon separator.
    for tag in purge_subject | purge_chapter | purge_topic:
        assert ":" not in tag, f"purge tag {tag!r} uses ':' instead of '-'"


def test_cache_tag_helper_emits_entity_scoped_tags():
    """cf_enterprise.build_cache_tag must emit the syrabit-<entity>-<id>
    tokens that the SSR Cache-Tag header advertises, so the matching
    purge in admin_content can target a single subject/chapter/topic."""
    from cf_enterprise import build_cache_tag
    out = build_cache_tag("subject", "physics", "chapter", "ch1", "topic", "t1")
    assert "syrabit-subject-physics" in out
    assert "syrabit-chapter-ch1" in out
    assert "syrabit-topic-t1" in out


# ── Review remediation #2 — D1 typed read helpers ───────────────────────────
@pytest.mark.asyncio
async def test_d1_read_seo_meta_prefers_d1(monkeypatch):
    """``read_seo_meta`` must hit D1 first and not touch Mongo when D1
    serves the row."""
    monkeypatch.setattr("config.D1_MIRROR_ON", True, raising=False)
    import d1_mirror
    d1_mirror.reset_state()
    monkeypatch.setattr(d1_mirror, "_d1_configured", lambda: True, raising=False)

    async def _fake_get(table, key_field, key_value):
        assert table == "seo_meta"
        assert key_field == "route"
        return {"route": key_value, "meta_title": "T", "from": "d1"}

    monkeypatch.setattr(d1_mirror, "_d1_get", _fake_get, raising=False)

    db = SimpleNamespace(seo_meta=SimpleNamespace(
        find_one=lambda *a, **kw: (_ for _ in ()).throw(AssertionError("Mongo should not be hit")),
    ))
    out = await d1_mirror.read_seo_meta("/x", db)
    assert out["from"] == "d1"
    assert d1_mirror.read_counters_snapshot()["d1_hit"] == 1
    d1_mirror.reset_state()


@pytest.mark.asyncio
async def test_d1_read_audit_log_recent_falls_back_and_truncates(monkeypatch):
    """``read_audit_log_recent`` falls back to Mongo on D1 miss and
    truncates the result to ``limit`` rows so a misbehaving D1 mirror
    can't overwhelm the caller."""
    monkeypatch.setattr("config.D1_MIRROR_ON", True, raising=False)
    import d1_mirror
    d1_mirror.reset_state()
    monkeypatch.setattr(d1_mirror, "_d1_configured", lambda: True, raising=False)

    async def _fake_get(*a, **kw):
        return None  # miss

    monkeypatch.setattr(d1_mirror, "_d1_get", _fake_get, raising=False)

    rows = [{"id": f"a{i}", "ts": i} for i in range(20)]
    cursor = MagicMock()

    async def _to_list(_n=None):
        return rows

    cursor.to_list = _to_list

    def _sort(*a, **kw):
        return cursor

    cursor.sort = _sort
    db = SimpleNamespace(audit_log=SimpleNamespace(
        find=lambda *a, **kw: cursor,
    ))
    out = await d1_mirror.read_audit_log_recent(5, db)
    assert isinstance(out, list)
    assert len(out) == 5
    d1_mirror.reset_state()


@pytest.mark.asyncio
async def test_d1_read_syllabus_chain_hits_d1(monkeypatch):
    """``read_syllabus_chain`` returns the breadcrumb chain from D1
    so SSR can render breadcrumbs without joining."""
    monkeypatch.setattr("config.D1_MIRROR_ON", True, raising=False)
    import d1_mirror
    d1_mirror.reset_state()
    monkeypatch.setattr(d1_mirror, "_d1_configured", lambda: True, raising=False)

    async def _fake_get(table, key_field, key_value):
        assert table == "syllabus_map"
        assert key_field == "topic_id"
        return {
            "topic_id": key_value, "topic_slug": "newton-laws",
            "chapter_slug": "laws", "subject_slug": "physics",
            "class_slug": "class-12", "board_slug": "ahsec",
        }

    monkeypatch.setattr(d1_mirror, "_d1_get", _fake_get, raising=False)
    out = await d1_mirror.read_syllabus_chain("top-1", db=None)
    assert out["board_slug"] == "ahsec"
    assert out["topic_slug"] == "newton-laws"
    d1_mirror.reset_state()


# ── Review remediation #2 — cache_rules contract ────────────────────────────
def test_cache_rules_payload_covers_all_required_groups():
    """cf_cache_rules.policy_payload must include rules for every
    route group the reviewer asked for: SSR HTML, static, images,
    sitemap, robots, public JSON."""
    from cf_cache_rules import policy_payload
    p = policy_payload()
    families = {r["family"] for r in p["rules"]}
    assert {"ssr_html", "static_assets", "images", "sitemap", "robots", "public_json"}.issubset(families)
    assert p["rule_count"] == len(p["rules"])


def test_cache_rules_classifier_returns_expected_family():
    """rule_for_path is the cheap classifier the admin panel uses to
    show which rule a sample path lands in."""
    from cf_cache_rules import rule_for_path
    assert rule_for_path("/assets/main.css").family == "static_assets"
    assert rule_for_path("/sitemap.xml").family == "sitemap"
    assert rule_for_path("/robots.txt").family == "robots"
    assert rule_for_path("/api/seo/routes").family == "public_json"
    assert rule_for_path("/photo.png").family == "images"
    assert rule_for_path("/ahsec/class-12/physics").family == "ssr_html"
    assert rule_for_path("/random") is None


@pytest.mark.asyncio
async def test_cache_rules_apply_noop_when_flag_off(monkeypatch):
    monkeypatch.setattr("config.CF_TIERED_CACHE_ON", False, raising=False)
    from cf_cache_rules import apply_rules_via_api
    result = await apply_rules_via_api()
    assert result["applied"] is False
    assert result["reason"] == "flag_off"


# ── Typing-indicator channel ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_do_chat_typing_round_trip_local(monkeypatch):
    """put_typing → get_typing must round-trip via the in-process
    fallback so the SPA can render the indicator with DO_CHAT_ON
    set even before the Worker is deployed."""
    monkeypatch.setattr("config.DO_CHAT_ON", True, raising=False)
    import do_chat
    do_chat.reset()
    monkeypatch.setattr(do_chat, "_do_configured", lambda: False, raising=False)

    ok = await do_chat.put_typing("conv-9", True, actor="assistant", ttl_ms=2000)
    assert ok is True
    state = await do_chat.get_typing("conv-9")
    assert state["typing"] is True
    assert state["actor"] == "assistant"
    # Stop typing.
    await do_chat.put_typing("conv-9", False)
    state2 = await do_chat.get_typing("conv-9")
    assert state2["typing"] is False
    do_chat.reset()


@pytest.mark.asyncio
async def test_admin_cf_tier2_apply_dispatches_three_helpers(monkeypatch):
    """POST /admin/cf-tier2/apply must call apply_speed_features +
    apply_tiered_cache + apply_rules_via_api so 'enabled' really means
    'applied at the edge' (not just helper-callable)."""
    import cf_speed_smoke, cf_tiered_cache, cf_cache_rules

    calls: list[str] = []

    async def _speed():
        calls.append("speed")
        return {"applied": True, "settings": ["polish"]}

    async def _tiered():
        calls.append("tiered")
        return {"applied": True}

    async def _rules():
        calls.append("rules")
        return {"applied": True, "rule_count": 6}

    monkeypatch.setattr(cf_speed_smoke, "apply_speed_features", _speed, raising=False)
    monkeypatch.setattr(cf_tiered_cache, "apply_tiered_cache", _tiered, raising=False)
    monkeypatch.setattr(cf_cache_rules, "apply_rules_via_api", _rules, raising=False)

    from routes.admin_cf_health import admin_cf_tier2_apply
    out = await admin_cf_tier2_apply(admin={"id": "admin-test"})
    assert calls == ["speed", "tiered", "rules"]
    assert out["speed_features"]["applied"] is True
    assert out["tiered_cache"]["applied"] is True
    assert out["cache_rules"]["applied"] is True


@pytest.mark.asyncio
async def test_admin_cf_tier2_apply_isolates_helper_failures(monkeypatch):
    """A helper crash must not break the other two — each step is
    reported in its own block with an error string."""
    import cf_speed_smoke, cf_tiered_cache, cf_cache_rules

    async def _boom():
        raise RuntimeError("kaboom")

    async def _ok():
        return {"applied": True}

    monkeypatch.setattr(cf_speed_smoke, "apply_speed_features", _boom, raising=False)
    monkeypatch.setattr(cf_tiered_cache, "apply_tiered_cache", _ok, raising=False)
    monkeypatch.setattr(cf_cache_rules, "apply_rules_via_api", _ok, raising=False)

    from routes.admin_cf_health import admin_cf_tier2_apply
    out = await admin_cf_tier2_apply(admin={"id": "admin-test"})
    assert out["speed_features"]["applied"] is False
    assert "kaboom" in out["speed_features"]["error"]
    assert out["tiered_cache"]["applied"] is True
    assert out["cache_rules"]["applied"] is True
