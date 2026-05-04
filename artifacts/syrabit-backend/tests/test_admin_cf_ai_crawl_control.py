"""Tests for the admin CF AI Crawl Control card.

Covers:
- The pure helpers in ``cf_bot_report`` (``is_ai_bot``, ``aggregate_daily_series``,
  and the ``fetch_admin_summary`` glue) so the AI vs search-engine split is
  reproducible without hitting Cloudflare.
- The admin route (``/admin/analytics/cf-ai-crawl-control``) so the
  empty-state and happy-path JSON shapes the frontend depends on are
  pinned down.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests._deps_stub import install_deps_stub  # noqa: E402

install_deps_stub()

import cf_bot_report  # noqa: E402
from cf_bot_report import (  # noqa: E402
    aggregate_daily_series,
    aggregate_per_operator,
    fetch_admin_summary,
    is_ai_bot,
    operator_for,
)


# ── Pure helpers ────────────────────────────────────────────────────────────

def test_is_ai_bot_known_ai_crawlers():
    assert is_ai_bot("GPTBot")
    assert is_ai_bot("ClaudeBot")
    assert is_ai_bot("PerplexityBot")
    assert is_ai_bot("Meta-ExternalAgent")
    assert is_ai_bot("Google-Extended")
    assert is_ai_bot("Applebot-Extended")
    assert is_ai_bot("OAI-SearchBot")
    assert is_ai_bot("ChatGPT-User")


def test_is_ai_bot_search_engine_crawlers():
    assert not is_ai_bot("Googlebot")
    assert not is_ai_bot("Bingbot")
    assert not is_ai_bot("Applebot")  # NOT Applebot-Extended
    assert not is_ai_bot("DuckDuckBot")
    assert not is_ai_bot("YandexBot")


def test_is_ai_bot_unknown_returns_false():
    assert not is_ai_bot("")
    assert not is_ai_bot("RandomBot")


def test_aggregate_daily_series_top_n_and_other_collapse():
    # 6 distinct bots over 2 dates; top_n=3 should keep top 3 and roll the
    # remaining 3 into "Other".
    buckets = [
        {"count": 100, "dimensions": {"date": "2026-04-19", "userAgent": "Googlebot/2.1"}},
        {"count": 50, "dimensions": {"date": "2026-04-19", "userAgent": "GPTBot/1.0"}},
        {"count": 30, "dimensions": {"date": "2026-04-19", "userAgent": "Bingbot/2.0"}},
        {"count": 10, "dimensions": {"date": "2026-04-19", "userAgent": "ClaudeBot"}},
        {"count": 5, "dimensions": {"date": "2026-04-19", "userAgent": "PerplexityBot"}},
        {"count": 2, "dimensions": {"date": "2026-04-19", "userAgent": "Applebot/0.1"}},
        {"count": 200, "dimensions": {"date": "2026-04-20", "userAgent": "Googlebot/2.1"}},
        {"count": 80, "dimensions": {"date": "2026-04-20", "userAgent": "GPTBot/1.0"}},
        {"count": 1, "dimensions": {"date": "2026-04-20", "userAgent": "ClaudeBot"}},
    ]
    out = aggregate_daily_series(buckets, top_n=3)
    assert out["top_bots"] == ["Googlebot", "GPTBot", "Bingbot"]
    rows = out["rows"]
    assert len(rows) == 2
    assert rows[0]["date"] == "2026-04-19"
    assert rows[0]["Googlebot"] == 100
    assert rows[0]["GPTBot"] == 50
    assert rows[0]["Bingbot"] == 30
    # ClaudeBot (10) + PerplexityBot (5) + Applebot (2) collapse into Other
    assert rows[0]["Other"] == 17
    assert rows[1]["date"] == "2026-04-20"
    assert rows[1]["Googlebot"] == 200
    assert rows[1]["GPTBot"] == 80
    # ClaudeBot rolls into Other on day 2
    assert rows[1]["Other"] == 1


def test_aggregate_daily_series_skips_unclassified_and_undated():
    buckets = [
        {"count": 100, "dimensions": {"date": "2026-04-19", "userAgent": "TotallyRandom/1.0"}},
        {"count": 50, "dimensions": {"date": "", "userAgent": "Googlebot/2.1"}},
        {"count": 25, "dimensions": {"date": "2026-04-19", "userAgent": "Googlebot/2.1"}},
    ]
    out = aggregate_daily_series(buckets, top_n=5)
    assert out["top_bots"] == ["Googlebot"]
    assert out["rows"] == [{"date": "2026-04-19", "Googlebot": 25}]


def test_aggregate_daily_series_empty_input():
    out = aggregate_daily_series([], top_n=5)
    assert out == {"top_bots": [], "rows": []}


# ── Operator grouping (mirrors CF AI Crawl Control overview tiles) ──────────

def test_operator_for_known_companies():
    assert operator_for("Googlebot") == "Google"
    assert operator_for("Google-Extended") == "Google"
    assert operator_for("Meta-ExternalAgent") == "Meta"
    assert operator_for("Bingbot") == "Microsoft"
    assert operator_for("Applebot") == "Apple"
    assert operator_for("Applebot-Extended") == "Apple"
    assert operator_for("GPTBot") == "OpenAI"
    assert operator_for("ChatGPT-User") == "OpenAI"
    assert operator_for("OAI-SearchBot") == "OpenAI"
    assert operator_for("PerplexityBot") == "Perplexity"
    assert operator_for("ClaudeBot") == "Anthropic"
    assert operator_for("CCBot") == "Common Crawl"


def test_operator_for_unknown_falls_through_to_other():
    assert operator_for("BrandNewBot") == "Other"
    assert operator_for("") == "Other"


def test_aggregate_per_operator_groups_by_company_and_sorts_by_allowed():
    """Mirrors CF's overview: operator tiles ordered by allowed-requests
    desc, with each operator's bots rolled up into a single tile carrying
    a ``bots`` chip-list also ordered by request count desc.

    Numbers chosen to mimic the user-supplied screenshot shape (Google
    leads with Googlebot+Google-Extended; OpenAI shows up under AI).
    """
    per_bot = [
        {"name": "Googlebot", "requests": 6000, "category": "search", "error_rate": 0.05},
        {"name": "Google-Extended", "requests": 200, "category": "ai", "error_rate": 0.0},
        {"name": "Meta-ExternalAgent", "requests": 108, "category": "ai", "error_rate": 0.10},
        {"name": "Bingbot", "requests": 88, "category": "search", "error_rate": 0.0},
        {"name": "Applebot", "requests": 80, "category": "search", "error_rate": 0.0},
        {"name": "GPTBot", "requests": 20, "category": "ai", "error_rate": 0.0},
        {"name": "ChatGPT-User", "requests": 12, "category": "ai", "error_rate": 0.0},
        {"name": "ClaudeBot", "requests": 9, "category": "ai", "error_rate": 0.0},
        {"name": "CCBot", "requests": 7, "category": "ai", "error_rate": 0.0},
    ]
    tiles = aggregate_per_operator(per_bot)

    # Tile order: by allowed desc → Google first by a wide margin
    assert tiles[0]["operator"] == "Google"
    google = tiles[0]
    # Googlebot 6000 * 0.95 = 5700 + Google-Extended 200 * 1.0 = 200 → 5900
    assert google["allowed"] == 5900
    # 6000 * 0.05 = 300 unsuccessful from Googlebot
    assert google["unsuccessful"] == 300
    assert google["requests"] == 6200
    # Bots chip list inside a tile sorted by request count desc
    assert [b["name"] for b in google["bots"]] == ["Googlebot", "Google-Extended"]
    # Mixed search + AI under Google → category bumps to "ai"
    assert google["category"] == "ai"

    # Subsequent tiles still in allowed-desc order
    operators_in_order = [t["operator"] for t in tiles]
    # Meta (108 reqs * 0.9 = 97 allowed) > Microsoft (88) > Apple (80) > OpenAI (32)
    # > Anthropic (9) > Common Crawl (7)
    assert operators_in_order == [
        "Google", "Meta", "Microsoft", "Apple", "OpenAI", "Anthropic", "Common Crawl",
    ]
    # Apple is search-only → tile category stays "search"
    apple = next(t for t in tiles if t["operator"] == "Apple")
    assert apple["category"] == "search"
    # OpenAI tile aggregates GPTBot + ChatGPT-User
    openai = next(t for t in tiles if t["operator"] == "OpenAI")
    assert openai["allowed"] == 32
    assert {b["name"] for b in openai["bots"]} == {"GPTBot", "ChatGPT-User"}


def test_aggregate_per_operator_unknown_bot_falls_into_other():
    per_bot = [
        {"name": "BrandNewBot", "requests": 10, "category": "ai", "error_rate": 0.0},
    ]
    tiles = aggregate_per_operator(per_bot)
    assert len(tiles) == 1
    assert tiles[0]["operator"] == "Other"
    assert tiles[0]["allowed"] == 10


def test_aggregate_per_operator_empty():
    assert aggregate_per_operator([]) == []


def test_aggregate_per_operator_clamps_invalid_error_rate():
    """Defensive: if upstream returns a percent (>1.0) instead of a
    fraction, allowed should not go negative."""
    per_bot = [
        {"name": "Googlebot", "requests": 100, "error_rate": 50.0},  # i.e. 50%
    ]
    tiles = aggregate_per_operator(per_bot)
    assert tiles[0]["allowed"] == 50
    assert tiles[0]["unsuccessful"] == 50


def test_operator_map_covers_every_canonical_ua_pattern():
    """Drift guard: every canonical name produced by ``_classify_ua``
    (i.e. every entry in ``_UA_PATTERNS``) MUST have an explicit
    operator mapping. Without this, a new bot added to ``_UA_PATTERNS``
    would silently fall into the "Other" tile and the operator would
    lose attribution on the AI Crawl Control overview card."""
    canonical_names = {name for _needle, name in cf_bot_report._UA_PATTERNS}
    mapped_names = set(cf_bot_report._OPERATOR_MAP.keys())
    missing = canonical_names - mapped_names
    extra = mapped_names - canonical_names
    assert not missing, f"Canonical bots missing from _OPERATOR_MAP: {sorted(missing)}"
    # Extra entries are allowed (e.g., aliases) but flag for awareness;
    # currently we expect a clean 1:1 so a non-empty set is a red flag.
    assert not extra, f"_OPERATOR_MAP has names not in _UA_PATTERNS: {sorted(extra)}"


# ── fetch_admin_summary glue ────────────────────────────────────────────────

def _run(coro):
    # Task #284 — `asyncio.get_event_loop()` raises ``RuntimeError: There
    # is no current event loop in thread 'MainThread'`` on Python 3.11+
    # when no loop is running. Spin up a fresh loop per call so this helper
    # is order-independent regardless of what earlier tests did to the
    # implicit main-thread loop.
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_fetch_admin_summary_returns_none_when_unconfigured():
    with patch.object(cf_bot_report, "is_configured", return_value=False):
        out = _run(fetch_admin_summary(days=7))
    assert out is None


def test_fetch_admin_summary_happy_path_splits_ai_vs_search():
    cfg = {"zone_id": "zone-test", "api_token": "tok"}
    per_ua_buckets = [
        {"count": 100, "sum": {"edgeResponseBytes": 5000},
         "dimensions": {"userAgent": "Googlebot/2.1", "cacheStatus": "hit", "edgeResponseStatus": 200}},
        {"count": 30, "sum": {"edgeResponseBytes": 1500},
         "dimensions": {"userAgent": "GPTBot/1.0", "cacheStatus": "miss", "edgeResponseStatus": 200}},
        {"count": 5, "sum": {"edgeResponseBytes": 200},
         "dimensions": {"userAgent": "ClaudeBot", "cacheStatus": "miss", "edgeResponseStatus": 404}},
    ]
    daily_buckets = [
        {"count": 100, "dimensions": {"date": "2026-04-25", "userAgent": "Googlebot/2.1"}},
        {"count": 30, "dimensions": {"date": "2026-04-25", "userAgent": "GPTBot/1.0"}},
        {"count": 5, "dimensions": {"date": "2026-04-25", "userAgent": "ClaudeBot"}},
    ]
    with patch.object(cf_bot_report, "is_configured", return_value=True), \
         patch.object(cf_bot_report, "_cfg", return_value=cfg), \
         patch.object(cf_bot_report, "_fetch_per_ua_buckets", new=AsyncMock(return_value=per_ua_buckets)), \
         patch.object(cf_bot_report, "_fetch_per_ua_daily_series", new=AsyncMock(return_value=daily_buckets)):
        out = _run(fetch_admin_summary(days=7))
    assert out is not None
    assert out["source"] == "cloudflare"
    assert out["period_days"] == 7
    assert out["zone_id"] == "zone-test"
    assert out["totals"]["requests"] == 135
    assert out["totals"]["bots"] == 3
    # AI split: GPTBot + ClaudeBot
    assert out["ai_totals"]["requests"] == 35
    assert out["ai_totals"]["bots"] == 2
    # Search split: Googlebot only
    assert out["search_totals"]["requests"] == 100
    assert out["search_totals"]["bots"] == 1
    # per_bot sorted desc by requests
    assert [b["name"] for b in out["per_bot"]] == ["Googlebot", "GPTBot", "ClaudeBot"]
    assert out["per_bot"][0]["category"] == "search"
    assert out["per_bot"][1]["category"] == "ai"
    # ClaudeBot had a 404 → error_rate 1.0
    claude = next(b for b in out["per_bot"] if b["name"] == "ClaudeBot")
    assert claude["error_rate"] == 1.0
    # Daily series populated
    assert "Googlebot" in out["daily_series"]["top_bots"]
    assert out["daily_series"]["rows"][0]["date"] == "2026-04-25"

    # Operator tiles + headline allowed/unsuccessful match CF overview
    assert out["per_operator"]
    operators = [t["operator"] for t in out["per_operator"]]
    # Google (100) > OpenAI (30) > Anthropic (5) by allowed
    assert operators[:3] == ["Google", "OpenAI", "Anthropic"]
    google_tile = next(t for t in out["per_operator"] if t["operator"] == "Google")
    assert google_tile["allowed"] == 100
    assert google_tile["unsuccessful"] == 0
    assert google_tile["category"] == "search"
    # Anthropic 5 reqs * 100% error → 0 allowed, 5 unsuccessful
    anthropic_tile = next(t for t in out["per_operator"] if t["operator"] == "Anthropic")
    assert anthropic_tile["allowed"] == 0
    assert anthropic_tile["unsuccessful"] == 5
    # Headline metrics row totals match the sum of per_operator tiles
    assert out["allowed_total"] == sum(t["allowed"] for t in out["per_operator"])
    assert out["unsuccessful_total"] == sum(t["unsuccessful"] for t in out["per_operator"])
    assert out["allowed_total"] == 130
    assert out["unsuccessful_total"] == 5


def test_fetch_admin_summary_returns_none_when_per_ua_fetch_fails():
    cfg = {"zone_id": "zone-test", "api_token": "tok"}
    with patch.object(cf_bot_report, "is_configured", return_value=True), \
         patch.object(cf_bot_report, "_cfg", return_value=cfg), \
         patch.object(cf_bot_report, "_fetch_per_ua_buckets", new=AsyncMock(return_value=None)):
        out = _run(fetch_admin_summary(days=7))
    assert out is None


def test_fetch_admin_summary_tolerates_daily_series_failure():
    """Per-bot summary should still render when only the daily-series
    secondary query fails (e.g. transient timeout)."""
    cfg = {"zone_id": "zone-test", "api_token": "tok"}
    per_ua_buckets = [
        {"count": 10, "sum": {"edgeResponseBytes": 500},
         "dimensions": {"userAgent": "Googlebot/2.1", "cacheStatus": "hit", "edgeResponseStatus": 200}},
    ]
    with patch.object(cf_bot_report, "is_configured", return_value=True), \
         patch.object(cf_bot_report, "_cfg", return_value=cfg), \
         patch.object(cf_bot_report, "_fetch_per_ua_buckets", new=AsyncMock(return_value=per_ua_buckets)), \
         patch.object(cf_bot_report, "_fetch_per_ua_daily_series", new=AsyncMock(return_value=None)):
        out = _run(fetch_admin_summary(days=7))
    assert out is not None
    assert out["totals"]["requests"] == 10
    assert out["daily_series"] == {"top_bots": [], "rows": []}


# ── Admin route (FastAPI integration) ───────────────────────────────────────

@pytest.fixture
def mock_admin():
    return {"id": "admin-1", "email": "ops@syrabit.ai", "is_admin": True}


@pytest.fixture
def app_client(mock_admin):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.bot_discovery import router

    app = FastAPI()
    app.include_router(router)
    from auth_deps import get_admin_user
    app.dependency_overrides = {get_admin_user: lambda: mock_admin}
    return TestClient(app)


def test_admin_route_unavailable_when_cf_returns_none(app_client):
    with patch("cf_bot_report.fetch_admin_summary",
               new=AsyncMock(return_value=None)):
        r = app_client.get("/admin/analytics/cf-ai-crawl-control?days=7")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert "Cloudflare analytics" in body["reason"]
    assert body["period_days"] == 7
    assert body["totals"] == {"requests": 0, "bytes": 0, "bots": 0}
    assert body["per_bot"] == []
    assert body["per_operator"] == []
    assert body["allowed_total"] == 0
    assert body["unsuccessful_total"] == 0
    assert body["daily_series"] == {"top_bots": [], "rows": []}


def test_admin_route_happy_path_returns_summary(app_client):
    summary = {
        "totals": {"requests": 200, "bytes": 1000, "bots": 2},
        "ai_totals": {"requests": 50, "bots": 1},
        "search_totals": {"requests": 150, "bots": 1},
        "per_bot": [
            {"name": "Googlebot", "requests": 150, "bytes": 800, "category": "search",
             "hit_pct": 80.0, "error_rate": 0.01},
            {"name": "GPTBot", "requests": 50, "bytes": 200, "category": "ai",
             "hit_pct": 60.0, "error_rate": 0.0},
        ],
        "daily_series": {"top_bots": ["Googlebot", "GPTBot"],
                         "rows": [{"date": "2026-04-25", "Googlebot": 150, "GPTBot": 50}]},
        "since": "2026-04-18T00:00:00Z",
        "until": "2026-04-25T00:00:00Z",
        "zone_id": "zone-test",
        "period_days": 7,
        "source": "cloudflare",
    }
    with patch("cf_bot_report.fetch_admin_summary",
               new=AsyncMock(return_value=summary)):
        r = app_client.get("/admin/analytics/cf-ai-crawl-control?days=7")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    # The route strips AI crawlers from the totals (per the policy
    # decision that AI crawlers are blocked at the edge AND hidden from
    # this card — see route docstring at routes/bot_discovery.py L6138).
    # Post-strip totals.requests equals search_totals.requests (150),
    # NOT the raw input totals.requests (200).
    assert body["totals"]["requests"] == 150
    assert body["per_bot"][0]["name"] == "Googlebot"
    # AI bots also stripped from per_bot and daily_series.top_bots.
    assert all(b["category"] != "ai" for b in body["per_bot"])
    assert "GPTBot" not in body["daily_series"]["top_bots"]


def test_admin_route_swallows_unexpected_exception(app_client):
    """A raised exception inside fetch_admin_summary should still surface
    as a graceful empty-state, not a 500 — the card is non-critical."""
    with patch("cf_bot_report.fetch_admin_summary",
               new=AsyncMock(side_effect=RuntimeError("boom"))):
        r = app_client.get("/admin/analytics/cf-ai-crawl-control?days=7")
    assert r.status_code == 200
    assert r.json()["available"] is False


def test_admin_route_rejects_out_of_range_days(app_client):
    with patch("cf_bot_report.fetch_admin_summary",
               new=AsyncMock(return_value=None)):
        r_zero = app_client.get("/admin/analytics/cf-ai-crawl-control?days=0")
        r_huge = app_client.get("/admin/analytics/cf-ai-crawl-control?days=400")
    assert r_zero.status_code == 422
    assert r_huge.status_code == 422
