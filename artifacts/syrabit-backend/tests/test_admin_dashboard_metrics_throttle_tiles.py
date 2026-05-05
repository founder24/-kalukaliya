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


def test_throttle_tile_keys_match_helper_output():
    """Task #388 drift guard: ``_THROTTLE_TILE_KEYS`` declares the
    canonical set of tile keys the cache-hit branch overlays on top of
    the cached payload. ``_build_throttle_tiles()`` MUST return exactly
    those keys (no more, no less). If a future change adds a new
    throttle tile but forgets to register it here, the cache-hit path
    would silently keep serving the stale value for up to 60 s — the
    exact regression Task #388 was filed to fix.

    Pure data-shape test, no async / no mocks.
    """
    import routes.cms_sarvam_health as cms

    produced = set(cms._build_throttle_tiles().keys())
    declared = set(cms._THROTTLE_TILE_KEYS)
    assert produced == declared, (
        f"Task #388 drift: _build_throttle_tiles() and _THROTTLE_TILE_KEYS "
        f"are out of sync. produced={produced} declared={declared}. "
        f"Update _THROTTLE_TILE_KEYS so the cache-hit refresh covers all tiles."
    )


@pytest.mark.asyncio
async def test_throttle_dict_is_fresh_even_when_rest_of_response_is_cached(
    stub_admin_dependencies, monkeypatch,
):
    """Task #388: when /admin/dashboard/metrics serves a cached payload
    (the heavy users / payments / SEO / deps queries are kept on the
    60s cache to protect Mongo + Redis), the throttle-tile dicts MUST
    still be recomputed on every call so a 429 storm that has cleared
    is reflected on the dashboard within ~5 s instead of waiting up to
    60 s for the cache to expire.

    Pins:
      1. After a cache HIT, the cached non-throttle fields are reused
         verbatim (proves the cache is doing its job — the heavy
         queries did NOT re-run).
      2. The 6 throttle-tile keys are recomputed against the live
         underlying counters (proves recovery shows up immediately).
      3. The original cached entry is left in place (no premature
         eviction; subsequent callers within the TTL also see the
         heavy fields cached + fresh throttle).
    """
    cms = stub_admin_dependencies
    fake_db = type("D", (), {"payments": _FakeCollection(), "seo_topics": _FakeCollection(),
                              "seo_pages": _FakeCollection()})()
    monkeypatch.setattr(cms, "db", fake_db, raising=False)
    import deps
    monkeypatch.setattr(deps, "redis_client", None, raising=False)
    _reset_429_windows()

    import llm
    import time as _time
    now = _time.time()
    # Seed an active throttle storm: 6 azure_openai 429s and 6 deepgram
    # 429s in the last 60s (threshold is 5 → both throttled).
    for _ in range(6):
        llm._PROVIDER_429_WINDOWS["azure_openai"].append(now)
    for _ in range(6):
        llm._PROVIDER_429_WINDOWS["deepgram"].append(now)

    # First call → cache MISS, populates the cache with throttled=True
    # for both providers and a (real but stubbed) ``users`` block.
    first = await cms.admin_dashboard_metrics(admin={"username": "test"})
    assert first["azure_openai_throttle"]["burst_60s"] == 6
    assert first["azure_openai_throttle"]["throttled"] is True
    assert first["deepgram_throttle"]["burst_60s"] == 6
    assert first["deepgram_throttle"]["throttled"] is True
    cached_users_block = first["users"]
    cached_response_time = first["response_time_ms"]

    # Storm clears: provider counters reset to 0.  Critically we do NOT
    # touch ``cms._metrics_cache`` here — the cache is still fresh
    # (well within the 60s TTL), so the second call must hit the
    # cache-HIT branch.
    llm._reset_provider_429("azure_openai")
    llm._reset_provider_429("deepgram")

    # Second call within the cache TTL.
    second = await cms.admin_dashboard_metrics(admin={"username": "test"})

    # Throttle tiles MUST be fresh: counters now zero, throttled false.
    assert second["azure_openai_throttle"]["burst_60s"] == 0, (
        "Task #388: throttle dict MUST bypass the metrics cache so "
        "recovery shows on the AdminHealth panel within seconds"
    )
    assert second["azure_openai_throttle"]["throttled"] is False
    assert second["deepgram_throttle"]["burst_60s"] == 0, (
        "Task #388: throttle dict MUST bypass the metrics cache so "
        "recovery shows on the AdminHealth panel within seconds"
    )
    assert second["deepgram_throttle"]["throttled"] is False
    # All six throttle-tile keys must be present and live (not just
    # the two we mutated) — proves the helper rebuilds the full set.
    for key in (
        "workers_ai_throttle", "groq_throttle", "gemini_throttle",
        "azure_openai_throttle", "deepgram_throttle",
        "assamese_chat_unavailable",
    ):
        assert key in second, f"throttle tile {key} dropped on cache hit"

    # Heavy fields MUST be served from cache (proves we didn't blow up
    # the cache to get fresh throttle data — that's the whole point).
    assert second["users"] is cached_users_block, (
        "Task #388: heavy users block must come from cache (identity "
        "preserved) — only the throttle tiles bypass the cache"
    )
    assert second["response_time_ms"] == cached_response_time, (
        "Task #388: cached response_time_ms must NOT be recomputed on "
        "a cache hit (otherwise the heavy queries ran again)"
    )

    # The cache entry itself must still hold the original throttled=True
    # snapshot — the cache-hit branch returns a SHALLOW COPY with the
    # throttle tiles overwritten, but does not mutate the cached dict.
    cached_dict = cms._metrics_cache["data"]
    assert cached_dict["azure_openai_throttle"]["burst_60s"] == 6, (
        "Task #388: cache-hit refresh must NOT mutate the cached dict; "
        "the next caller still gets a consistent view of the original "
        "snapshot (with throttle tiles overlayed fresh on read)"
    )


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
