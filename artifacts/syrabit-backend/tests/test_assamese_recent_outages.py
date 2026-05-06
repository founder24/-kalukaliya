"""Task #379 — Test the Assamese recent-outages event log.

Pins the contract that ``record_assamese_unavailable()`` persists a small
event document (timestamp + failing leg + error excerpt + conversation
hash) and that ``get_assamese_recent_outages()`` returns those events
newest-first, with a Redis fallback path. Also pins that the
``/admin/dashboard/metrics`` payload exposes a ``recent`` array on the
``assamese_chat_unavailable`` tile.
"""
from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import os
os.environ.setdefault("CF_ACCOUNT_ID", "test-account")
os.environ.setdefault("CF_AI_GATEWAY_TOKEN", "test-token")

from tests._deps_stub import install_deps_stub  # noqa: E402

install_deps_stub()

import llm as llm_mod  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_state():
    llm_mod._ASSAMESE_UNAVAILABLE_WINDOW.clear()
    llm_mod._ASSAMESE_RECENT_OUTAGES.clear()
    yield
    llm_mod._ASSAMESE_UNAVAILABLE_WINDOW.clear()
    llm_mod._ASSAMESE_RECENT_OUTAGES.clear()


# ─── In-memory event log ────────────────────────────────────────────────────

class TestRecordPersistsEvent:
    def test_event_appended_to_inprocess_buffer(self):
        llm_mod.record_assamese_unavailable(
            failing_leg="sarvam_workers_indic_chain",
            error_summary="HTTPException: chain exhausted",
        )
        assert len(llm_mod._ASSAMESE_RECENT_OUTAGES) == 1
        ev = llm_mod._ASSAMESE_RECENT_OUTAGES[0]
        assert ev["failing_leg"] == "sarvam_workers_indic_chain"
        assert "chain exhausted" in ev["error_summary"]
        assert isinstance(ev["ts"], float)

    def test_event_default_failing_leg_when_omitted(self):
        llm_mod.record_assamese_unavailable()
        ev = llm_mod._ASSAMESE_RECENT_OUTAGES[0]
        # Legacy callers (no kwargs) get a stable "unknown" sentinel rather
        # than an empty string the UI would have to guard against.
        assert ev["failing_leg"] == "unknown"
        assert ev["error_summary"] == ""

    def test_event_buffer_caps_at_max(self):
        for i in range(llm_mod._ASSAMESE_RECENT_OUTAGES_MAX + 5):
            llm_mod.record_assamese_unavailable(failing_leg=f"leg_{i}")
        assert len(llm_mod._ASSAMESE_RECENT_OUTAGES) == \
            llm_mod._ASSAMESE_RECENT_OUTAGES_MAX
        # Oldest entries are dropped — the most recent event must be present.
        legs = [e["failing_leg"] for e in llm_mod._ASSAMESE_RECENT_OUTAGES]
        assert legs[-1] == f"leg_{llm_mod._ASSAMESE_RECENT_OUTAGES_MAX + 4}"
        assert "leg_0" not in legs

    def test_error_summary_truncated_to_max(self):
        long_err = "x" * (llm_mod._ASSAMESE_ERROR_SUMMARY_MAX_LEN + 200)
        llm_mod.record_assamese_unavailable(error_summary=long_err)
        ev = llm_mod._ASSAMESE_RECENT_OUTAGES[0]
        # Truncated to MAX_LEN total (last char is the "…" marker).
        assert len(ev["error_summary"]) == llm_mod._ASSAMESE_ERROR_SUMMARY_MAX_LEN
        assert ev["error_summary"].endswith("…")

    def test_conversation_id_is_hashed(self):
        llm_mod.record_assamese_unavailable(conversation_id="conv-abc-123")
        ev = llm_mod._ASSAMESE_RECENT_OUTAGES[0]
        # Hash is short (12 hex chars) and irreversible — the raw id MUST
        # never appear in the stored doc.
        assert ev["conversation_id_hash"]
        assert "conv-abc-123" not in ev["conversation_id_hash"]
        assert len(ev["conversation_id_hash"]) == 12

    def test_conversation_id_hash_is_stable(self):
        llm_mod.record_assamese_unavailable(conversation_id="same")
        llm_mod.record_assamese_unavailable(conversation_id="same")
        h1 = llm_mod._ASSAMESE_RECENT_OUTAGES[0]["conversation_id_hash"]
        h2 = llm_mod._ASSAMESE_RECENT_OUTAGES[1]["conversation_id_hash"]
        # Same id → same hash so operators can spot repeat conversations.
        assert h1 == h2

    def test_empty_conversation_id_yields_empty_hash(self):
        llm_mod.record_assamese_unavailable(conversation_id=None)
        assert llm_mod._ASSAMESE_RECENT_OUTAGES[0]["conversation_id_hash"] == ""


# ─── get_assamese_recent_outages ────────────────────────────────────────────

class TestGetRecentOutagesInProcess:
    def test_returns_empty_when_no_events(self):
        assert llm_mod.get_assamese_recent_outages(5) == []

    def test_returns_newest_first(self):
        llm_mod.record_assamese_unavailable(failing_leg="oldest")
        time.sleep(0.001)
        llm_mod.record_assamese_unavailable(failing_leg="middle")
        time.sleep(0.001)
        llm_mod.record_assamese_unavailable(failing_leg="newest")
        with patch("deps.redis_client", None):
            out = llm_mod.get_assamese_recent_outages(5)
        legs = [e["failing_leg"] for e in out]
        assert legs == ["newest", "middle", "oldest"]

    def test_respects_limit(self):
        for i in range(10):
            llm_mod.record_assamese_unavailable(failing_leg=f"leg_{i}")
        with patch("deps.redis_client", None):
            out = llm_mod.get_assamese_recent_outages(3)
        assert len(out) == 3

    def test_negative_or_zero_limit_returns_empty(self):
        llm_mod.record_assamese_unavailable()
        assert llm_mod.get_assamese_recent_outages(0) == []
        assert llm_mod.get_assamese_recent_outages(-5) == []


class TestGetRecentOutagesRedisPath:
    def test_prefers_redis_list_over_inprocess(self):
        mock_rc = MagicMock()
        redis_doc = {
            "ts": 1234567890.0,
            "failing_leg": "redis_leg",
            "error_summary": "from-redis",
            "conversation_id_hash": "abcdef",
        }
        mock_rc.lrange.return_value = [json.dumps(redis_doc)]
        # Pre-seed in-process buffer to prove Redis wins.
        llm_mod.record_assamese_unavailable(failing_leg="inprocess_leg")
        with patch("deps.redis_client", mock_rc):
            out = llm_mod.get_assamese_recent_outages(5)
        assert len(out) == 1
        assert out[0]["failing_leg"] == "redis_leg"
        mock_rc.lrange.assert_called_once_with(
            llm_mod._ASSAMESE_RECENT_OUTAGES_REDIS_KEY, 0, 4
        )

    def test_falls_back_to_inprocess_when_redis_empty(self):
        mock_rc = MagicMock()
        mock_rc.lrange.return_value = []
        llm_mod.record_assamese_unavailable(failing_leg="from_inprocess")
        with patch("deps.redis_client", mock_rc):
            out = llm_mod.get_assamese_recent_outages(5)
        assert len(out) == 1
        assert out[0]["failing_leg"] == "from_inprocess"

    def test_falls_back_to_inprocess_when_redis_raises(self):
        mock_rc = MagicMock()
        mock_rc.lrange.side_effect = ConnectionError("redis gone")
        llm_mod.record_assamese_unavailable(failing_leg="resilient")
        with patch("deps.redis_client", mock_rc):
            out = llm_mod.get_assamese_recent_outages(5)
        assert len(out) == 1
        assert out[0]["failing_leg"] == "resilient"

    def test_skips_malformed_redis_entries(self):
        mock_rc = MagicMock()
        mock_rc.lrange.return_value = [
            "not-json{",
            json.dumps({"ts": 1.0, "failing_leg": "ok", "error_summary": "",
                        "conversation_id_hash": ""}),
            b"\x00\x01",
        ]
        with patch("deps.redis_client", mock_rc):
            out = llm_mod.get_assamese_recent_outages(5)
        # The single valid entry must come through; the malformed ones
        # must NOT crash the call.
        assert len(out) == 1
        assert out[0]["failing_leg"] == "ok"


class TestRecordWritesRedisList:
    def test_lpush_and_ltrim_called_when_redis_available(self):
        mock_rc = MagicMock()
        with patch("deps.redis_client", mock_rc):
            llm_mod.record_assamese_unavailable(failing_leg="sarvam_workers_indic_chain")
        # Burst counter still incremented (back-compat with Task #374).
        mock_rc.incr.assert_called_once_with(llm_mod._ASSAMESE_UNAVAILABLE_REDIS_KEY)
        # Event log persisted via LPUSH + LTRIM + EXPIRE (Task #379).
        mock_rc.lpush.assert_called_once()
        args = mock_rc.lpush.call_args.args
        assert args[0] == llm_mod._ASSAMESE_RECENT_OUTAGES_REDIS_KEY
        # Payload is JSON-encoded with the failing_leg.
        assert "sarvam_workers_indic_chain" in args[1]
        mock_rc.ltrim.assert_called_once_with(
            llm_mod._ASSAMESE_RECENT_OUTAGES_REDIS_KEY,
            0,
            llm_mod._ASSAMESE_RECENT_OUTAGES_MAX - 1,
        )
        mock_rc.expire.assert_any_call(
            llm_mod._ASSAMESE_RECENT_OUTAGES_REDIS_KEY,
            llm_mod._ASSAMESE_RECENT_OUTAGES_TTL_S,
        )

    def test_record_survives_redis_list_error(self):
        mock_rc = MagicMock()
        mock_rc.lpush.side_effect = RuntimeError("redis list down")
        with patch("deps.redis_client", mock_rc):
            llm_mod.record_assamese_unavailable(failing_leg="x")  # must not raise
        # In-memory buffer must still reflect the event.
        assert len(llm_mod._ASSAMESE_RECENT_OUTAGES) == 1


# ─── Source-level contract ──────────────────────────────────────────────────

class TestSourceContract:
    def test_llm_exports_recent_outages_helper(self):
        assert hasattr(llm_mod, "get_assamese_recent_outages")
        assert callable(llm_mod.get_assamese_recent_outages)

    def test_llm_exports_constants(self):
        assert hasattr(llm_mod, "_ASSAMESE_RECENT_OUTAGES_MAX")
        assert hasattr(llm_mod, "_ASSAMESE_RECENT_OUTAGES_REDIS_KEY")
        assert llm_mod._ASSAMESE_RECENT_OUTAGES_REDIS_KEY == "assamese_unavailable_events"
        assert llm_mod._ASSAMESE_RECENT_OUTAGES_MAX >= 5

    def test_record_signature_accepts_event_kwargs(self):
        """The new kwargs MUST be accepted as keyword-only-friendly args
        so future callers can supply context without positional-arg drift.
        """
        import inspect
        sig = inspect.signature(llm_mod.record_assamese_unavailable)
        params = sig.parameters
        assert "failing_leg" in params
        assert "error_summary" in params
        assert "conversation_id" in params
        # All kwargs default to a no-op so legacy callers keep working.
        assert params["failing_leg"].default == ""
        assert params["error_summary"].default == ""
        assert params["conversation_id"].default is None


# ─── /admin/dashboard/metrics — recent[] surfacing ──────────────────────────

class _FakeFindCursor:
    def sort(self, *_a, **_kw): return self
    async def to_list(self, *_a, **_kw): return []


class _FakeCollection:
    def find(self, *_a, **_kw): return _FakeFindCursor()
    async def count_documents(self, *_a, **_kw): return 0


@pytest.fixture
def stub_admin_dependencies(monkeypatch):
    import routes.cms_sarvam_health as cms

    async def _fake_admin(*_a, **_kw):
        return {"username": "test-admin"}
    monkeypatch.setattr(cms, "get_admin_user", _fake_admin, raising=False)

    async def _fake_supa_list_users():
        return []
    monkeypatch.setattr(cms, "supa_list_users", _fake_supa_list_users, raising=False)

    async def _fake_is_mongo_available():
        return False
    monkeypatch.setattr(cms, "is_mongo_available", _fake_is_mongo_available, raising=False)

    async def _fake_check_health_deps():
        return {}
    monkeypatch.setattr(
        cms, "_check_health_deps", _fake_check_health_deps, raising=False
    )

    async def _fake_bot_render():
        return {}
    monkeypatch.setattr(
        cms, "get_bot_render_metrics_async", _fake_bot_render, raising=False
    )

    monkeypatch.setattr(
        cms, "_metrics_cache", {"data": None, "ts": 0}, raising=False
    )
    fake_db = type("D", (), {"payments": _FakeCollection(),
                              "seo_topics": _FakeCollection(),
                              "seo_pages": _FakeCollection()})()
    monkeypatch.setattr(cms, "db", fake_db, raising=False)

    import deps
    monkeypatch.setattr(deps, "redis_client", None, raising=False)

    return cms


@pytest.mark.asyncio
async def test_metrics_payload_includes_recent_array_when_empty(
    stub_admin_dependencies,
):
    cms = stub_admin_dependencies
    result = await cms.admin_dashboard_metrics(admin={"username": "test"})
    tile = result["assamese_chat_unavailable"]
    assert "recent" in tile, (
        "Task #379: assamese_chat_unavailable MUST expose a `recent` array"
    )
    assert tile["recent"] == []


@pytest.mark.asyncio
async def test_metrics_payload_recent_array_lists_recorded_events(
    stub_admin_dependencies,
):
    cms = stub_admin_dependencies
    # Record three events end-to-end through the production helper.
    llm_mod.record_assamese_unavailable(
        failing_leg="sarvam_workers_indic_chain",
        error_summary="HTTPException: chain exhausted",
    )
    llm_mod.record_assamese_unavailable(
        failing_leg="workers_ai_unavailable",
        error_summary="Workers AI Phase-2 fallback not configured",
    )
    llm_mod.record_assamese_unavailable(
        failing_leg="workers_ai_phase2",
        error_summary="TimeoutError: stream stalled",
    )
    result = await cms.admin_dashboard_metrics(admin={"username": "test"})
    recent = result["assamese_chat_unavailable"]["recent"]
    assert len(recent) == 3
    legs = [e["failing_leg"] for e in recent]
    # Newest first (workers_ai_phase2 was recorded last).
    assert legs[0] == "workers_ai_phase2"
    assert legs[-1] == "sarvam_workers_indic_chain"
    for ev in recent:
        assert "ts" in ev
        assert "failing_leg" in ev
        assert "error_summary" in ev
        assert "conversation_id_hash" in ev


@pytest.mark.asyncio
async def test_metrics_payload_caps_recent_at_five(
    stub_admin_dependencies,
):
    cms = stub_admin_dependencies
    for i in range(10):
        llm_mod.record_assamese_unavailable(failing_leg=f"leg_{i}")
    result = await cms.admin_dashboard_metrics(admin={"username": "test"})
    recent = result["assamese_chat_unavailable"]["recent"]
    # Endpoint contract: at most 5 entries even when buffer is fuller.
    assert len(recent) == 5
