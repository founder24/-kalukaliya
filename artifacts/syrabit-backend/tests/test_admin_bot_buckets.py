"""Task #9 — Backend unit test for the admin bot-buckets tile.

Validates that ``GET /api/admin/health/bot-buckets`` aggregates a
mocked per-UA hit dict onto the four canonical buckets in
``infra/bot-rules.yaml`` (so a future regression in either
``collect_recent_bot_hits`` or ``_classify`` doesn't silently zero
out the dashboard).

Mocks `cf_bot_report.collect_recent_bot_hits` so the test runs
without any Cloudflare API access.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND = REPO_ROOT / "artifacts" / "syrabit-backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


@pytest.mark.asyncio
async def test_bot_buckets_aggregation_with_mocked_hits(monkeypatch):
    """When `collect_recent_bot_hits` returns a realistic per-UA dict,
    the route must roll the counts up onto the four canonical buckets
    (verified_search, citation_ai, training_ai, abusive) plus
    `unclassified` for unknown UAs. We assert each bucket gets a
    non-zero hit count from at least one of its YAML tokens."""
    from routes import admin_observability_bot_buckets as mod

    # One UA per canonical bucket + one deliberately-unknown UA.
    mock_per_ua = {
        "mozilla/5.0 (compatible; googlebot/2.1; +http://www.google.com/bot.html)": 5000,
        "mozilla/5.0 (compatible; perplexitybot/1.0)": 1200,
        "mozilla/5.0 (compatible; gptbot/1.0)": 800,
        "scrapy/2.11 (+https://scrapy.org)": 250,
        "totally-new-crawler/0.1 (https://example.com)": 42,
    }

    async def _fake_collect(window_h: int = 24):  # noqa: ARG001
        return mock_per_ua

    # Patch the module-level import target. The route does
    # `from cf_bot_report import collect_recent_bot_hits` lazily inside
    # the handler, so we patch cf_bot_report directly.
    import cf_bot_report
    monkeypatch.setattr(
        cf_bot_report, "collect_recent_bot_hits", _fake_collect, raising=True,
    )

    result = await mod.bot_buckets_health(_admin={"role": "admin"})

    buckets = result["buckets"]
    # Each canonical bucket touched by the mock data must have non-zero hits.
    assert buckets["verified_search"]["hits_24h"] >= 5000, (
        f"verified_search aggregation broken: {buckets['verified_search']}"
    )
    assert buckets["citation_ai"]["hits_24h"] >= 1200, (
        f"citation_ai aggregation broken: {buckets['citation_ai']}"
    )
    assert buckets["training_ai"]["hits_24h"] >= 800, (
        f"training_ai aggregation broken: {buckets['training_ai']}"
    )
    assert buckets["abusive"]["hits_24h"] >= 250, (
        f"abusive aggregation broken: {buckets['abusive']}"
    )
    assert buckets["unclassified"]["hits_24h"] >= 42, (
        f"unclassified bucket must catch unknown UAs: {buckets['unclassified']}"
    )

    # Totals are the sum across all buckets (not just canonical four).
    assert result["totals"]["hits_24h"] == 5000 + 1200 + 800 + 250 + 42

    # rDNS verification block is present even when KV/Redis is empty.
    assert "rdns_verification" in result
    assert "per_family" in result["rdns_verification"]
    assert "total" in result["rdns_verification"]
    # miss_rate is 0.0 when there are no counters yet — never NaN/None.
    assert result["rdns_verification"]["total"]["miss_rate"] == 0.0


@pytest.mark.asyncio
async def test_bot_buckets_rdns_counters_populate_when_redis_mirrors(monkeypatch):
    """When the rDNS Redis mirror has per-family counters, the admin
    health route's `rdns_verification.per_family` block must surface
    non-zero values per family + a global `total.miss_rate`."""
    from routes import admin_observability_bot_buckets as mod
    import cf_bot_report

    async def _hits(window_h: int = 24):  # noqa: ARG001
        return {"mozilla/5.0 (compatible; googlebot/2.1)": 100}

    monkeypatch.setattr(cf_bot_report, "collect_recent_bot_hits", _hits, raising=True)

    # Real route key format is `bot:rdns_ctr:<YYYY-MM-DD>:<family>:<outcome>`
    # where outcome ∈ {hit_pos, hit_neg, miss_pos, miss_neg}. Build today's
    # keys so the test exercises the actual format the edge worker writes.
    from datetime import datetime, timezone
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fake_counters = {
        f"bot:rdns_ctr:{day}:googlebot:hit_pos": "900",
        f"bot:rdns_ctr:{day}:googlebot:miss_pos": "50",
        f"bot:rdns_ctr:{day}:googlebot:miss_neg": "10",
        f"bot:rdns_ctr:{day}:bingbot:hit_pos": "200",
    }

    class _FakeRedisClient:
        def get(self, key):  # upstash_redis sync API
            return fake_counters.get(key)

    import deps
    monkeypatch.setattr(deps, "redis_client", _FakeRedisClient(), raising=False)

    result = await mod.bot_buckets_health(_admin={"role": "admin"})
    rdns = result["rdns_verification"]
    assert "per_family" in rdns and "total" in rdns
    google = rdns["per_family"]["googlebot"]
    assert google["hits"] == 900, f"hit_pos not surfaced: {google}"
    assert google["misses"] == 60, f"miss_pos+miss_neg not surfaced: {google}"
    assert 0.0 < google["miss_rate"] < 1.0, (
        f"miss_rate must reflect mocked counters: {google}"
    )
    assert rdns["per_family"]["bingbot"]["hits"] == 200


@pytest.mark.asyncio
async def test_bot_buckets_empty_state_does_not_500(monkeypatch):
    """When `collect_recent_bot_hits` returns an empty dict (CF API
    unavailable, cold start), the route must still 200 with a
    well-formed empty-state payload — never 500."""
    from routes import admin_observability_bot_buckets as mod
    import cf_bot_report

    async def _empty(window_h: int = 24):  # noqa: ARG001
        return {}

    monkeypatch.setattr(cf_bot_report, "collect_recent_bot_hits", _empty, raising=True)
    result = await mod.bot_buckets_health(_admin={"role": "admin"})
    assert result["totals"]["hits_24h"] == 0
    for bucket in ("verified_search", "citation_ai", "training_ai", "abusive", "unclassified"):
        assert result["buckets"][bucket]["hits_24h"] == 0
