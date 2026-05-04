"""Tests for SEO Phase E (Plan 7 + Plan 11):
- bilingual hreflang in `_build_urlset`
- Bing URL Submission API client (`bing_submit_client.submit_url_batch`)
- daily Bing submit task (`_should_run_bing_submit_now`,
  `_load_prior_bing_submit_batch_size`, `admin_bing_submit_stats`)
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from tests._deps_stub import install_deps_stub  # noqa: E402

install_deps_stub()


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Plan 7: hreflang in sitemap _build_urlset
# ---------------------------------------------------------------------------

def test_build_urlset_no_alt_when_no_assamese():
    from seo_engine import _build_urlset
    xml = _build_urlset([
        {"loc": "https://syrabit.ai/x", "lastmod": "2026-04-16",
         "pri": "0.8", "freq": "monthly"},
    ])
    assert "xmlns:xhtml" not in xml
    assert "<xhtml:link" not in xml
    assert "<loc>https://syrabit.ai/x</loc>" in xml


def test_build_urlset_emits_hreflang_triple_when_has_assamese():
    """Phase E hreflang: regional codes (en-IN/as-IN/x-default) and
    path-based Assamese URL (/as/...) — switched away from query-string
    lang=as because path-based slugs are stronger Bing/Google signals."""
    from seo_engine import _build_urlset
    xml = _build_urlset([
        {"loc": "https://syrabit.ai/seba/class-10/science/atoms",
         "lastmod": "2026-04-16", "pri": "0.8", "freq": "monthly",
         "has_assamese": True},
    ])
    assert 'xmlns:xhtml="http://www.w3.org/1999/xhtml"' in xml
    assert ('<xhtml:link rel="alternate" hreflang="en-IN" '
            'href="https://syrabit.ai/seba/class-10/science/atoms"/>') in xml
    assert ('<xhtml:link rel="alternate" hreflang="as-IN" '
            'href="https://syrabit.ai/as/seba/class-10/science/atoms"/>'
            ) in xml
    assert ('<xhtml:link rel="alternate" hreflang="x-default" '
            'href="https://syrabit.ai/seba/class-10/science/atoms"/>') in xml


def test_build_urlset_uses_path_prefix_when_loc_has_query():
    """Path-based Assamese URL preserves the original query string —
    /as/x?ref=seo (not x?ref=seo&lang=as) per the Phase E rewrite."""
    from seo_engine import _build_urlset
    xml = _build_urlset([
        {"loc": "https://syrabit.ai/x?ref=seo",
         "lastmod": "2026-04-16", "pri": "0.5", "freq": "weekly",
         "has_assamese": True},
    ])
    assert 'href="https://syrabit.ai/as/x?ref=seo"' in xml


# ---------------------------------------------------------------------------
# Plan 11: Bing submit client batching
# ---------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


class _FakeClient:
    """Minimal AsyncClient stub that records POST calls and returns a
    queued sequence of responses (so we can simulate per-batch outcomes)."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.posts = []

    async def post(self, url, *, json=None, headers=None):
        self.posts.append({"url": url, "json": json, "headers": headers})
        if not self._responses:
            return _FakeResp(200)
        return self._responses.pop(0)

    async def aclose(self):
        pass


def test_submit_url_batch_no_op_without_api_key():
    from bing_submit_client import submit_url_batch
    res = _run(submit_url_batch("", "https://syrabit.ai", ["https://a"]))
    assert res.submitted == 0 and res.succeeded == 0


def test_submit_url_batch_no_op_without_urls():
    from bing_submit_client import submit_url_batch
    res = _run(submit_url_batch("KEY", "https://syrabit.ai", []))
    assert res.submitted == 0


def test_submit_url_batch_splits_into_500_url_chunks_and_dedupes():
    from bing_submit_client import submit_url_batch
    urls = [f"https://syrabit.ai/p/{i}" for i in range(1100)] + [
        "https://syrabit.ai/p/0",  # duplicate
    ]
    fake = _FakeClient([_FakeResp(200), _FakeResp(200), _FakeResp(200)])
    res = _run(submit_url_batch(
        "KEY", "https://syrabit.ai", urls,
        batch_size=500, inter_batch_sleep_s=0, client=fake,
    ))
    assert res.submitted == 1100  # dedupe drops the duplicate
    assert res.succeeded == 1100
    assert res.failed == 0
    assert len(fake.posts) == 3
    assert len(fake.posts[0]["json"]["urlList"]) == 500
    assert len(fake.posts[1]["json"]["urlList"]) == 500
    assert len(fake.posts[2]["json"]["urlList"]) == 100
    assert fake.posts[0]["json"]["siteUrl"] == "https://syrabit.ai"
    assert "apikey=KEY" in fake.posts[0]["url"]


def test_submit_url_batch_aborts_and_flags_on_429():
    from bing_submit_client import submit_url_batch
    urls = [f"https://syrabit.ai/p/{i}" for i in range(700)]
    fake = _FakeClient([_FakeResp(200), _FakeResp(429, "rate-limited")])
    res = _run(submit_url_batch(
        "KEY", "https://syrabit.ai", urls,
        batch_size=500, inter_batch_sleep_s=0, client=fake,
    ))
    assert res.rate_limited is True
    assert res.succeeded == 500
    assert res.failed == 200
    assert len(fake.posts) == 2  # second call hit 429, loop aborts


def test_submit_url_batch_records_non_2xx_as_failure():
    from bing_submit_client import submit_url_batch
    fake = _FakeClient([_FakeResp(500, "boom")])
    res = _run(submit_url_batch(
        "KEY", "https://syrabit.ai", ["https://a", "https://b"],
        batch_size=500, inter_batch_sleep_s=0, client=fake,
    ))
    assert res.submitted == 2 and res.failed == 2 and res.succeeded == 0
    assert res.errors and "HTTP 500" in res.errors[0]


# ---------------------------------------------------------------------------
# Plan 11: daily task gating + batch backoff
# ---------------------------------------------------------------------------

def test_should_run_bing_submit_now_inside_window_first_run():
    from routes.bot_discovery import _should_run_bing_submit_now
    now = datetime(2026, 4, 16, 3, 0, tzinfo=timezone.utc)
    assert _should_run_bing_submit_now(now, "") is True


def test_should_run_bing_submit_now_skips_outside_window():
    from routes.bot_discovery import _should_run_bing_submit_now
    now = datetime(2026, 4, 16, 12, 0, tzinfo=timezone.utc)
    assert _should_run_bing_submit_now(now, "") is False


def test_should_run_bing_submit_now_dedupes_same_day():
    from routes.bot_discovery import _should_run_bing_submit_now
    now = datetime(2026, 4, 16, 3, 5, tzinfo=timezone.utc)
    assert _should_run_bing_submit_now(now, "2026-04-16") is False


def test_load_prior_batch_size_halves_after_429():
    from routes.bot_discovery import _load_prior_bing_submit_batch_size
    db = MagicMock()
    coll = MagicMock()
    coll.find_one = AsyncMock(return_value={
        "date": "2026-04-15", "rate_limited": True, "batch_size": 500,
    })
    db.__getitem__.return_value = coll
    out = _run(_load_prior_bing_submit_batch_size(db))
    assert out == 250


def test_load_prior_batch_size_default_when_no_history():
    from routes.bot_discovery import _load_prior_bing_submit_batch_size
    from bing_submit_client import BING_DEFAULT_BATCH_SIZE
    db = MagicMock()
    coll = MagicMock()
    coll.find_one = AsyncMock(return_value=None)
    db.__getitem__.return_value = coll
    out = _run(_load_prior_bing_submit_batch_size(db))
    assert out == BING_DEFAULT_BATCH_SIZE


def test_load_prior_batch_size_resets_after_clean_run():
    from routes.bot_discovery import _load_prior_bing_submit_batch_size
    from bing_submit_client import BING_DEFAULT_BATCH_SIZE
    db = MagicMock()
    coll = MagicMock()
    coll.find_one = AsyncMock(return_value={
        "date": "2026-04-15", "rate_limited": False, "batch_size": 250,
    })
    db.__getitem__.return_value = coll
    out = _run(_load_prior_bing_submit_batch_size(db))
    assert out == BING_DEFAULT_BATCH_SIZE


# ---------------------------------------------------------------------------
# Plan 7: bot-render snapshot — hreflang in chapter SSR HTML
# ---------------------------------------------------------------------------

def _render_chapter(chapter_doc, subj_doc=None):
    """Invoke `_render_chapter_fallback` against an in-memory db stub."""
    import sys, types
    from unittest.mock import AsyncMock as _AM, MagicMock as _MM
    deps_stub = sys.modules.get("deps") or types.ModuleType("deps")
    db = _MM()
    db.subjects.find_one = _AM(return_value=subj_doc or {
        "id": "S1", "name": "Science",
        "board_slug": "seba", "class_slug": "class-10",
    })
    db.chapters.find_one = _AM(return_value=chapter_doc)
    deps_stub.db = db
    deps_stub.is_mongo_available = _AM(return_value=True)
    sys.modules["deps"] = deps_stub
    from routes import cms_sarvam_health
    middleware = cms_sarvam_health.BotRenderMiddleware(app=None)
    return _run(middleware._render_chapter_fallback(
        "seba", "class-10", "science", "atoms",
    ))


def test_bot_render_chapter_emits_hreflang_when_assamese_present():
    html = _render_chapter({
        "title": "Atoms", "description": "intro", "content": "...",
        "topics": [], "content_as": "পৰমাণু আৰু অণু",
    })
    assert html is not None
    assert 'hreflang="en"' in html
    assert 'hreflang="as"' in html
    assert 'hreflang="x-default"' in html
    assert "?lang=as" in html
    assert 'hreflang="en-IN"' not in html


def test_bot_render_chapter_omits_hreflang_when_no_assamese():
    html = _render_chapter({
        "title": "Atoms", "description": "intro", "content": "...",
        "topics": [], "content_as": "",
    })
    assert html is not None
    assert 'hreflang="as"' not in html
    assert 'hreflang="x-default"' not in html
    assert 'hreflang="en-IN"' in html


def test_bot_render_chapter_omits_hreflang_when_content_as_missing():
    html = _render_chapter({
        "title": "Atoms", "description": "intro", "content": "...",
        "topics": [],
    })
    assert html is not None
    assert 'hreflang="as"' not in html
    assert 'hreflang="en-IN"' in html


# ---------------------------------------------------------------------------
# Plan 11: daily task end-to-end + stats endpoint smoke
# ---------------------------------------------------------------------------

def test_try_run_bing_submit_skips_without_api_key(monkeypatch):
    from routes.bot_discovery import _try_run_bing_submit_once
    monkeypatch.delenv("BING_WEBMASTER_API_KEY", raising=False)
    db = MagicMock()
    out = _run(_try_run_bing_submit_once(
        db, datetime(2026, 4, 16, 3, 0, tzinfo=timezone.utc),
    ))
    assert out["claimed"] is False and out["reason"] == "no_api_key"


def test_try_run_bing_submit_persists_stats_end_to_end(monkeypatch):
    """Full path: claim slot -> collect URLs -> submit -> persist daily doc."""
    from routes import bot_discovery
    import bing_submit_client

    monkeypatch.setenv("BING_WEBMASTER_API_KEY", "TEST")

    persisted = {}
    db = MagicMock()
    coll = MagicMock()
    coll.find_one = AsyncMock(return_value=None)
    async def _upsert(filt, update, upsert=False):
        persisted.update(update.get("$set", {}))
        return MagicMock()
    coll.update_one = AsyncMock(side_effect=_upsert)
    db.__getitem__.return_value = coll
    db.job_locks.find_one = AsyncMock(return_value=None)
    db.job_locks.find_one_and_update = AsyncMock(return_value={"_id": "x"})
    db.job_locks.insert_one = AsyncMock()

    async def _stub_collect():
        return [f"https://syrabit.ai/p/{i}" for i in range(3)]
    monkeypatch.setattr(
        bot_discovery, "_collect_current_sitemap_urls", _stub_collect,
    )

    async def _stub_submit(api_key, site, urls, *, batch_size, **_):
        res = bing_submit_client.BingSubmitResult()
        res.submitted = len(urls)
        res.succeeded = len(urls)
        return res
    monkeypatch.setattr(
        bing_submit_client, "submit_url_batch", _stub_submit,
    )

    out = _run(bot_discovery._try_run_bing_submit_once(
        db, datetime(2026, 4, 16, 3, 5, tzinfo=timezone.utc),
    ))
    assert out["claimed"] is True
    assert out["submitted"] == 3 and out["succeeded"] == 3
    assert persisted["date"] == "2026-04-16"
    assert persisted["url_catalog_size"] == 3
    assert persisted["batch_size"] == bing_submit_client.BING_DEFAULT_BATCH_SIZE
    coll.update_one.assert_awaited_once()


def test_last_run_summary_classifies_states():
    from routes.bot_discovery import _bing_submit_last_run_summary
    assert _bing_submit_last_run_summary([])["status"] == "never_run"
    assert _bing_submit_last_run_summary([{"submitted": 100, "succeeded": 100,
        "failed": 0}])["status"] == "ok"
    assert _bing_submit_last_run_summary([{"submitted": 100, "succeeded": 80,
        "failed": 20}])["status"] == "partial_failure"
    assert _bing_submit_last_run_summary([{"submitted": 100, "succeeded": 0,
        "failed": 100}])["status"] == "failed"
    assert _bing_submit_last_run_summary([{"submitted": 100, "succeeded": 50,
        "failed": 50, "rate_limited": True}])["status"] == "rate_limited"


def test_rolling_7d_usage_caps_at_7_days_and_computes_pct():
    from routes.bot_discovery import _bing_submit_rolling_7d_usage
    rows = [{"submitted": 1000, "succeeded": 1000, "failed": 0} for _ in range(10)]
    out = _bing_submit_rolling_7d_usage(rows)
    assert out["days_with_data"] == 7
    assert out["submitted"] == 7000
    assert out["weekly_cap"] == 70000
    assert out["pct_of_weekly_cap"] == 10.0


def test_admin_bing_submit_stats_returns_full_payload(monkeypatch):
    """Smoke test: endpoint returns enabled/last_run/rolling_7d/quota/days."""
    from routes import bot_discovery
    import bing_submit_client

    monkeypatch.setenv("BING_WEBMASTER_API_KEY", "TEST")

    db = MagicMock()
    coll = MagicMock()
    rows = [
        {"date": "2026-04-16", "submitted": 1000, "succeeded": 1000,
         "failed": 0, "batch_size": 500, "url_catalog_size": 1000,
         "ts": "2026-04-16T03:00:00+00:00"},
        {"date": "2026-04-15", "submitted": 1000, "succeeded": 1000,
         "failed": 0, "batch_size": 500},
    ]
    cursor = MagicMock()
    cursor.sort.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.to_list = AsyncMock(return_value=rows)
    coll.find.return_value = cursor
    db.__getitem__.return_value = coll

    import sys, types
    deps_stub = sys.modules.get("deps") or types.ModuleType("deps")
    deps_stub.db = db
    deps_stub.is_mongo_available = AsyncMock(return_value=True)
    sys.modules["deps"] = deps_stub

    async def _stub_quota(api_key, site, **_):
        return (9000, 280000)
    monkeypatch.setattr(bing_submit_client, "get_quota", _stub_quota)

    out = _run(bot_discovery.admin_bing_submit_stats(
        days=7, admin={"id": "u"},
    ))
    assert out["enabled"] is True
    assert out["site_url"] == "https://syrabit.ai"
    assert out["daily_cap"] == 10000
    assert out["last_run"]["status"] == "ok"
    assert out["last_run"]["date"] == "2026-04-16"
    assert out["rolling_7d"]["submitted"] == 2000
    assert out["rolling_7d"]["weekly_cap"] == 70000
    assert out["quota"]["daily_remaining"] == 9000
    assert out["quota"]["monthly_remaining"] == 280000
    assert len(out["days"]) == 2
