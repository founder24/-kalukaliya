"""Task #378 — Azure OpenAI + Deepgram throttle indicators on /admin/dashboard/metrics.

Pins the contract that the admin dashboard metrics endpoint surfaces
``azure_openai_throttle`` and ``deepgram_throttle`` dicts in the same
shape as the existing Workers AI / Groq / Gemini tiles, so the
AdminHealth burst-tile component can render them without changes.

Each tile dict MUST have exactly these four keys:
  * ``burst_60s``         — in-process count (single worker, exact 60s)
  * ``burst_180s``        — cross-worker count (Redis, ~180s TTL)
  * ``alert_threshold``   — int, mirrors metrics._ALERT_THRESHOLDS
  * ``throttled``         — bool, ``burst_60s >= alert_threshold``

These tests pin:
  1. Both new keys are present in the response.
  2. Tile shape matches the existing tiles exactly.
  3. ``alert_threshold`` is sourced from ``_ALERT_THRESHOLDS`` (not
     hard-coded), so admin-side threshold edits flow through.
  4. ``throttled`` flips correctly at the boundary.
  5. A successful provider call resets the counter (verified by clearing
     the Redis bucket + in-process window and re-reading).
"""
from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest


@pytest.fixture
def stub_admin_dependencies(monkeypatch):
    """Stub out auth, mongo, supabase so we can exercise the route logic."""
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

    # The route calls ``_check_health_deps()`` (with a 5s timeout) to
    # build the deps section.  Stub it so tests don't fan-out to real
    # Mongo / Redis / Supabase health checks.
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

    # bypass the route's 5s in-process cache so each test sees fresh values
    monkeypatch.setattr(
        cms, "_metrics_cache", {"data": None, "ts": 0}, raising=False
    )

    return cms


def _reset_429_windows():
    import llm
    for w in llm._PROVIDER_429_WINDOWS.values():
        w.clear()


# Patch the db.payments.find chain to return [] without hitting Mongo.
class _FakeFindCursor:
    def sort(self, *_a, **_kw): return self
    async def to_list(self, *_a, **_kw): return []


class _FakeCollection:
    def find(self, *_a, **_kw): return _FakeFindCursor()
    async def count_documents(self, *_a, **_kw): return 0


@pytest.mark.asyncio
async def test_azure_openai_and_deepgram_throttle_tiles_present(
    stub_admin_dependencies, monkeypatch,
):
    """Both new tiles must be in the response with the documented keys."""
    cms = stub_admin_dependencies
    # Patch the db handle on the route module so the payments / chapters
    # queries don't hit Mongo.
    fake_db = type("D", (), {"payments": _FakeCollection(), "seo_topics": _FakeCollection(),
                              "seo_pages": _FakeCollection()})()
    monkeypatch.setattr(cms, "db", fake_db, raising=False)
    # Also patch the deps-level redis_client so burst reads return 0.
    import deps
    monkeypatch.setattr(deps, "redis_client", None, raising=False)
    _reset_429_windows()

    result = await cms.admin_dashboard_metrics(admin={"username": "test"})

    assert "azure_openai_throttle" in result, (
        "Task #378: /admin/dashboard/metrics MUST expose azure_openai_throttle"
    )
    assert "deepgram_throttle" in result, (
        "Task #378: /admin/dashboard/metrics MUST expose deepgram_throttle"
    )

    expected_keys = {"burst_60s", "burst_180s", "alert_threshold", "throttled"}
    assert set(result["azure_openai_throttle"].keys()) == expected_keys, (
        f"azure_openai_throttle shape drift: {set(result['azure_openai_throttle'].keys())}"
    )
    assert set(result["deepgram_throttle"].keys()) == expected_keys, (
        f"deepgram_throttle shape drift: {set(result['deepgram_throttle'].keys())}"
    )


@pytest.mark.asyncio
async def test_throttle_tiles_match_workers_ai_shape(
    stub_admin_dependencies, monkeypatch,
):
    """Azure / Deepgram tiles must have the exact same shape as the
    existing Workers AI tile so the AdminHealth.jsx burst-tile component
    (which iterates over an array of {key, label, thr, unit}) renders
    them without code changes."""
    cms = stub_admin_dependencies
    fake_db = type("D", (), {"payments": _FakeCollection(), "seo_topics": _FakeCollection(),
                              "seo_pages": _FakeCollection()})()
    monkeypatch.setattr(cms, "db", fake_db, raising=False)
    import deps
    monkeypatch.setattr(deps, "redis_client", None, raising=False)
    _reset_429_windows()

    result = await cms.admin_dashboard_metrics(admin={"username": "test"})

    wai_keys = set(result["workers_ai_throttle"].keys())
    assert set(result["azure_openai_throttle"].keys()) == wai_keys
    assert set(result["deepgram_throttle"].keys()) == wai_keys
    # And the value types must match.
    for tile in (result["azure_openai_throttle"], result["deepgram_throttle"]):
        assert isinstance(tile["burst_60s"], int)
        assert isinstance(tile["burst_180s"], int)
        assert isinstance(tile["alert_threshold"], int)
        assert isinstance(tile["throttled"], bool)


@pytest.mark.asyncio
async def test_alert_threshold_sourced_from_metrics_module(
    stub_admin_dependencies, monkeypatch,
):
    """``alert_threshold`` must read from metrics._ALERT_THRESHOLDS so
    runtime threshold edits flow into the dashboard, not hard-coded 5."""
    cms = stub_admin_dependencies
    fake_db = type("D", (), {"payments": _FakeCollection(), "seo_topics": _FakeCollection(),
                              "seo_pages": _FakeCollection()})()
    monkeypatch.setattr(cms, "db", fake_db, raising=False)
    import deps
    monkeypatch.setattr(deps, "redis_client", None, raising=False)
    _reset_429_windows()

    import metrics
    monkeypatch.setitem(
        metrics._ALERT_THRESHOLDS, "azure_openai_429_burst_threshold", 12
    )
    monkeypatch.setitem(
        metrics._ALERT_THRESHOLDS, "deepgram_429_burst_threshold", 7
    )

    result = await cms.admin_dashboard_metrics(admin={"username": "test"})

    assert result["azure_openai_throttle"]["alert_threshold"] == 12, (
        "azure_openai_throttle.alert_threshold must reflect runtime override"
    )
    assert result["deepgram_throttle"]["alert_threshold"] == 7, (
        "deepgram_throttle.alert_threshold must reflect runtime override"
    )


@pytest.mark.asyncio
async def test_throttled_flag_flips_at_threshold(
    stub_admin_dependencies, monkeypatch,
):
    """``throttled`` must be ``burst_60s >= alert_threshold``."""
    cms = stub_admin_dependencies
    fake_db = type("D", (), {"payments": _FakeCollection(), "seo_topics": _FakeCollection(),
                              "seo_pages": _FakeCollection()})()
    monkeypatch.setattr(cms, "db", fake_db, raising=False)
    import deps
    monkeypatch.setattr(deps, "redis_client", None, raising=False)
    _reset_429_windows()

    import llm
    import time
    now = time.time()
    # 5 azure_openai 429s in the last 60s, threshold is 5 → throttled.
    for _ in range(5):
        llm._PROVIDER_429_WINDOWS["azure_openai"].append(now)
    # 4 deepgram 429s, threshold is 5 → NOT throttled.
    for _ in range(4):
        llm._PROVIDER_429_WINDOWS["deepgram"].append(now)

    result = await cms.admin_dashboard_metrics(admin={"username": "test"})

    assert result["azure_openai_throttle"]["burst_60s"] == 5
    assert result["azure_openai_throttle"]["throttled"] is True, (
        "azure_openai_throttle.throttled must be True when burst >= threshold"
    )
    assert result["deepgram_throttle"]["burst_60s"] == 4
    assert result["deepgram_throttle"]["throttled"] is False, (
        "deepgram_throttle.throttled must be False when burst < threshold"
    )


@pytest.mark.asyncio
async def test_successful_call_resets_throttle_counter(
    stub_admin_dependencies, monkeypatch,
):
    """A successful provider call clears the in-process counter via
    _reset_provider_429 — the dashboard must surface 0 immediately."""
    cms = stub_admin_dependencies
    fake_db = type("D", (), {"payments": _FakeCollection(), "seo_topics": _FakeCollection(),
                              "seo_pages": _FakeCollection()})()
    monkeypatch.setattr(cms, "db", fake_db, raising=False)
    import deps
    monkeypatch.setattr(deps, "redis_client", None, raising=False)
    _reset_429_windows()

    import llm
    import time
    now = time.time()
    for _ in range(7):
        llm._PROVIDER_429_WINDOWS["azure_openai"].append(now)
    for _ in range(7):
        llm._PROVIDER_429_WINDOWS["deepgram"].append(now)

    # Hit the dashboard once to confirm the burst is visible.
    pre = await cms.admin_dashboard_metrics(admin={"username": "test"})
    assert pre["azure_openai_throttle"]["burst_60s"] == 7
    assert pre["deepgram_throttle"]["burst_60s"] == 7

    # Simulate a successful provider call clearing the counter.
    llm._reset_provider_429("azure_openai")
    llm._reset_provider_429("deepgram")
    # Bypass the 5s endpoint cache so the second read recomputes.
    monkeypatch.setattr(
        cms, "_metrics_cache", {"data": None, "ts": 0}, raising=False
    )

    post = await cms.admin_dashboard_metrics(admin={"username": "test"})
    assert post["azure_openai_throttle"]["burst_60s"] == 0, (
        "successful Azure call must reset the counter visible on the dashboard"
    )
    assert post["azure_openai_throttle"]["throttled"] is False
    assert post["deepgram_throttle"]["burst_60s"] == 0, (
        "successful Deepgram call must reset the counter visible on the dashboard"
    )
    assert post["deepgram_throttle"]["throttled"] is False


@pytest.mark.asyncio
async def test_dashboard_still_succeeds_when_llm_imports_fail(
    stub_admin_dependencies, monkeypatch,
):
    """The 429-burst block is wrapped in try/except — if the llm module
    raises (transient import error, etc.) the dashboard MUST still
    render, with both new tiles defaulted to a safe zero state."""
    cms = stub_admin_dependencies
    fake_db = type("D", (), {"payments": _FakeCollection(), "seo_topics": _FakeCollection(),
                              "seo_pages": _FakeCollection()})()
    monkeypatch.setattr(cms, "db", fake_db, raising=False)
    import deps
    monkeypatch.setattr(deps, "redis_client", None, raising=False)
    _reset_429_windows()

    import llm
    def _boom(*_a, **_kw):
        raise RuntimeError("simulated llm outage")
    monkeypatch.setattr(llm, "get_provider_429_burst", _boom, raising=False)
    monkeypatch.setattr(llm, "get_provider_429_burst_inprocess", _boom, raising=False)
    monkeypatch.setattr(llm, "get_workers_ai_429_burst", _boom, raising=False)
    monkeypatch.setattr(llm, "get_workers_ai_429_burst_inprocess", _boom, raising=False)

    result = await cms.admin_dashboard_metrics(admin={"username": "test"})

    assert result["azure_openai_throttle"]["burst_60s"] == 0
    assert result["azure_openai_throttle"]["throttled"] is False
    assert result["deepgram_throttle"]["burst_60s"] == 0
    assert result["deepgram_throttle"]["throttled"] is False
