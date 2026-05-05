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


@pytest.mark.asyncio
async def test_heavy_query_cache_expires_after_ttl(
    stub_admin_dependencies, monkeypatch,
):
    """Task #394: complement the Task #388 throttle-bypass test by pinning
    the OTHER half of the cache contract — the heavy users / payments /
    SEO / deps queries MUST be re-run after ``_METRICS_CACHE_TTL``
    seconds, not served stale forever.

    Without this guard, a future refactor that accidentally extends the
    TTL (or breaks the ``ts`` write-back) would silently leave admins
    looking at hours-old revenue / user-count numbers, and the existing
    throttle-only test from #388 would NOT catch it.

    Pin three things in one flow:
      1. Cache HIT path: a second call within the TTL serves the same
         heavy snapshot (proves the cache is wired up — without this,
         step 3 below would be vacuously true).
      2. Cache EXPIRY: after fast-forwarding ``_metrics_cache['ts']``
         back by more than ``_METRICS_CACHE_TTL`` seconds, the heavy
         fields are recomputed and reflect the new fake state.
      3. Write-back: the freshly computed payload is re-cached (the
         ``ts`` advances forward, not stuck at the rewound value), so
         the cache continues to function on subsequent calls.
    """
    cms = stub_admin_dependencies

    # Mutable user / payment fakes so we can change them between calls
    # and observe the cache behavior without touching real services.
    users_state = []  # starts empty
    payment_count_state = {"n": 0}

    async def _fake_supa_list_users():
        # Return a fresh copy each call so the route's len() reflects
        # whatever's in the closure at call time.
        return list(users_state)
    monkeypatch.setattr(
        cms, "supa_list_users", _fake_supa_list_users, raising=False
    )

    class _MutPaymentsCursor:
        def sort(self, *_a, **_kw): return self
        async def to_list(self, *_a, **_kw):
            # Each "payment" is a minimal dict that contributes to
            # ``payments_count`` and revenue=0 (no amount fields).
            return [{"verified_at": "2026-01-01T00:00:00+00:00"}
                    for _ in range(payment_count_state["n"])]

    class _MutPayments:
        def find(self, *_a, **_kw): return _MutPaymentsCursor()
        async def count_documents(self, *_a, **_kw):
            return payment_count_state["n"]

    fake_db = type("D", (), {
        "payments": _MutPayments(),
        "seo_topics": _FakeCollection(),
        "seo_pages": _FakeCollection(),
    })()
    monkeypatch.setattr(cms, "db", fake_db, raising=False)

    import deps
    monkeypatch.setattr(deps, "redis_client", None, raising=False)
    _reset_429_windows()

    # ── First call: cache MISS, captures the initial empty state ──
    first = await cms.admin_dashboard_metrics(admin={"username": "test"})
    assert first["users"]["total"] == 0, "initial users.total must be 0"
    assert first["payments_count"] == 0, "initial payments_count must be 0"

    # ── Mutate the underlying fakes BEFORE the cache expires. ──
    users_state.append({"plan": "pro"})
    users_state.append({"plan": "free"})
    payment_count_state["n"] = 7

    # ── Second call WITHIN the TTL: must serve the cached snapshot. ──
    # This proves the cache is real, so the expiry assertion below is
    # not vacuously satisfied by a missing cache.
    cached = await cms.admin_dashboard_metrics(admin={"username": "test"})
    assert cached["users"]["total"] == 0, (
        "Task #394: within the TTL, users.total must come from the cache "
        "(seeing 2 here would mean the cache is broken — which would also "
        "make the expiry assertion below trivially pass)"
    )
    assert cached["payments_count"] == 0, (
        "Task #394: within the TTL, payments_count must come from cache"
    )

    # ── Fast-forward the cache timestamp so it appears expired. ──
    # Rewind ``ts`` by ``_METRICS_CACHE_TTL + 1`` seconds — equivalent
    # to wall-clock time advancing past the TTL but without any sleep.
    cms._metrics_cache["ts"] -= cms._METRICS_CACHE_TTL + 1
    rewound_ts = cms._metrics_cache["ts"]

    # ── Third call: cache MISS (TTL elapsed), heavy fields recomputed. ──
    fresh = await cms.admin_dashboard_metrics(admin={"username": "test"})
    assert fresh["users"]["total"] == 2, (
        "Task #394: after _METRICS_CACHE_TTL elapses, the heavy "
        "supa_list_users query MUST be re-run so admins do not see "
        "stale user counts on the AdminHealth panel"
    )
    assert fresh["users"]["paid"] == 1, (
        "Task #394: derived users.paid count must reflect the fresh data"
    )
    assert fresh["payments_count"] == 7, (
        "Task #394: after the TTL, payments_count MUST be recomputed "
        "from the underlying Mongo find — silently caching forever "
        "would give admins stale revenue numbers"
    )

    # ── Write-back contract: the cache ts must have advanced forward. ──
    # If a future refactor recomputes on miss but forgets to update the
    # cache entry, every subsequent call would also miss (DB hammer)
    # OR worse — keep returning the rewound ``ts`` and never recover.
    assert cms._metrics_cache["ts"] > rewound_ts, (
        "Task #394: a fresh recompute MUST write the new ts back to the "
        "cache, otherwise either every call hits the DB (load spike) or "
        "the cache entry stays permanently expired"
    )
    assert cms._metrics_cache["data"] is fresh, (
        "Task #394: the freshly computed dict must BE the cached entry "
        "(identity-equal), proving the write-back wired up to the same "
        "object the caller saw — Task #388's cache-hit overlay returns a "
        "shallow copy on read, so write-back identity must be checked here"
    )


@pytest.mark.asyncio
async def test_meta_freshness_indicator_shape_and_cache_semantics(
    stub_admin_dependencies, monkeypatch,
):
    """Task #396: /admin/dashboard/metrics MUST piggyback a `_meta` dict
    that lets the AdminHealth panel render a "Throttle: live • Heavy:
    Xs ago" freshness indicator. Now that throttle tiles refresh every
    poll (Task #388) but heavy fields are cached for ~5s (Task #395),
    admins have no way to tell from the numbers alone which half is
    live vs cached.

    Pin the contract in one flow:
      1. SHAPE: every response has `_meta` with exactly two unix-second
         floats — `heavy_cached_at` and `throttle_fresh_at`.
      2. CACHE MISS: both timestamps equal `now` (both halves are live).
      3. CACHE HIT: `heavy_cached_at` is identity-equal to the cached
         `_metrics_cache["ts"]` (proves it really reflects the cached
         snapshot, not the request time), while `throttle_fresh_at`
         strictly advances past the first call's value (proves the
         throttle half is recomputed on every poll, matching the
         Task #388 bypass).
      4. The cache-hit overlay does NOT mutate the cached dict's `_meta`
         (mirrors the throttle-tile shallow-copy contract from Task #388).
    """
    cms = stub_admin_dependencies
    fake_db = type("D", (), {
        "payments": _FakeCollection(),
        "seo_topics": _FakeCollection(),
        "seo_pages": _FakeCollection(),
    })()
    monkeypatch.setattr(cms, "db", fake_db, raising=False)
    import deps
    monkeypatch.setattr(deps, "redis_client", None, raising=False)
    _reset_429_windows()

    # ── First call: cache MISS — both halves are live ──
    first = await cms.admin_dashboard_metrics(admin={"username": "test"})
    assert "_meta" in first, (
        "Task #396: response MUST include _meta so AdminHealth can render "
        "the per-section freshness indicator"
    )
    meta1 = first["_meta"]
    assert set(meta1.keys()) == {"heavy_cached_at", "throttle_fresh_at"}, (
        f"Task #396: _meta shape drift: {set(meta1.keys())} — "
        "AdminHealth.jsx reads exactly these two keys"
    )
    assert isinstance(meta1["heavy_cached_at"], (int, float))
    assert isinstance(meta1["throttle_fresh_at"], (int, float))
    assert meta1["heavy_cached_at"] == meta1["throttle_fresh_at"], (
        "Task #396: on a cache MISS both halves are computed at `now`, "
        "so the two timestamps MUST be equal — divergence here would "
        "mislead admins into thinking the heavy block is stale on the "
        "very first poll after a deploy"
    )
    cached_ts_after_first = cms._metrics_cache["ts"]
    assert meta1["heavy_cached_at"] == cached_ts_after_first, (
        "Task #396: heavy_cached_at MUST equal the cache write-back ts "
        "so the next cache-HIT call can advertise the same value"
    )

    # ── Rewind the cached ts so the next call still hits the cache
    #    (still well within ``_METRICS_CACHE_TTL``) but the heavy
    #    snapshot looks measurably older. We deliberately can't rely
    #    on real wall-clock advance here — back-to-back awaits land
    #    within microseconds — so a fixed-second `time.sleep()` would
    #    bloat the suite and still be flaky. Rewinding the cache ts
    #    is the same trick the existing #394 test uses, and it makes
    #    the heavy/throttle DIVERGENCE on the cache-HIT branch
    #    deterministic and large enough to assert against. ──
    nudge = 0.5
    cms._metrics_cache["ts"] -= nudge
    rewound_heavy_ts = cms._metrics_cache["ts"]

    # ── Second call: cache HIT — heavy stays put, throttle is fresh ──
    second = await cms.admin_dashboard_metrics(admin={"username": "test"})
    assert "_meta" in second
    meta2 = second["_meta"]
    assert set(meta2.keys()) == {"heavy_cached_at", "throttle_fresh_at"}

    assert meta2["heavy_cached_at"] == rewound_heavy_ts, (
        "Task #396: on a cache HIT, heavy_cached_at MUST track the "
        "cached snapshot ts (proves the indicator shows when the heavy "
        "block was actually computed, not when the request arrived). "
        "If this drifts, admins will see the heavy 'Xs ago' counter "
        "reset to 0 on every poll and never know the data is cached."
    )
    # Monotonicity: time only moves forward across two awaits.
    assert meta2["throttle_fresh_at"] >= meta1["throttle_fresh_at"], (
        "Task #396: throttle_fresh_at MUST be monotonic across calls "
        f"(got {meta2['throttle_fresh_at']} < first call "
        f"{meta1['throttle_fresh_at']})"
    )
    # Divergence: this is the whole reason the indicator exists.
    # After the 0.5s rewind, heavy_cached_at is at least `nudge` seconds
    # behind throttle_fresh_at, so any AdminHealth render of the
    # response would show "Throttle: live • Heavy: ≥1s ago".
    divergence = meta2["throttle_fresh_at"] - meta2["heavy_cached_at"]
    assert divergence >= nudge - 0.01, (
        "Task #396: on a cache HIT the two halves MUST DIVERGE — "
        "throttle_fresh_at must be at least the rewound nudge "
        f"({nudge}s) ahead of heavy_cached_at, otherwise the indicator "
        "in AdminHealth.jsx ('Throttle: live • Heavy: Xs ago') is "
        f"indistinguishable from a cache-miss state. Got divergence "
        f"= {divergence}s."
    )

    # ── Cached dict's `_meta` must reflect the original miss values ──
    # The cache-HIT branch returns a shallow copy with an overwritten
    # `_meta`; it must NOT mutate the cached entry, otherwise a
    # subsequent caller within the TTL would see the previous caller's
    # `throttle_fresh_at` (which would be wrong by the time they read
    # it). Same shallow-copy contract as the throttle tiles in #388.
    cached_dict = cms._metrics_cache["data"]
    assert cached_dict["_meta"]["heavy_cached_at"] == cached_dict["_meta"]["throttle_fresh_at"], (
        "Task #396: cache-hit overlay MUST NOT mutate the cached `_meta` "
        "— the cached entry should still reflect its miss-time state "
        "(both timestamps equal). Mirrors Task #388's shallow-copy "
        "contract for the throttle tiles."
    )
