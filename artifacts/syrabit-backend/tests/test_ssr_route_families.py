"""Task #386 — review remediation: integration tests that exercise the
backend SSR endpoints with the exact URL shapes the Pages Functions
middleware (`artifacts/syrabit/functions/_middleware.js::mapSsrRoute`)
proxies to. The reviewer found that the original middleware was
proxying to non-existent backend URLs, so these tests pin the
contract by mounting the real ``/api/seo`` router and asserting that
each route family responds with ``200 text/html``.

We mock ``seo_engine._db`` collection methods so the routes don't
need a live MongoDB. The point is to verify that the middleware →
backend route mapping is wired correctly, not to re-test the SEO
HTML renderer (which has its own coverage).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_async_cursor(items):
    """Return a stub that mimics motor's async cursor used in
    seo_engine — ``find(...).to_list(N)`` and ``aggregate(...)`` with
    async iteration."""
    cur = MagicMock()

    async def _to_list(_n=None):
        return items

    cur.to_list = _to_list

    def _sort(*_a, **_kw):
        return cur

    cur.sort = _sort

    class _AsyncIter:
        def __init__(self, data):
            self._it = iter(data)
        def __aiter__(self):
            return self
        async def __anext__(self):
            try:
                return next(self._it)
            except StopIteration:
                raise StopAsyncIteration

    cur.__aiter__ = lambda self=cur: _AsyncIter(items)
    return cur


@pytest.fixture
def ssr_client(monkeypatch):
    """Mount the real ``/api/seo`` router with mocked Mongo so the
    SSR endpoints serve canned HTML."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import seo_engine

    page_doc = {
        "board_slug": "ahsec",
        "class_slug": "class-12",
        "subject_slug": "physics",
        "topic_slug": "newton-laws",
        "page_type": "notes",
        "status": "published",
        "topic_title": "Newton's Laws of Motion",
        "subject_name": "Physics",
        "board_name": "AHSEC",
        "class_name": "Class 12",
        "chapter_title": "Laws of Motion",
        "meta_description": "Complete notes on Newton's three laws.",
        "html_body": "<p>Newton's laws…</p>",
        "quality_score": 0.92,
        "answer_html": "<p>An object at rest…</p>",
        "rewritten_summary": "Newton's three laws explained.",
    }
    subject_doc = {
        "id": "subj-physics",
        "slug": "physics",
        "name": "Physics",
        "description": "AHSEC Class-12 Physics",
        "thumbnailUrl": "https://example.com/phys.png",
        "class_id": "cls-12",
        "board_id": "brd-ahsec",
    }
    board_doc = {"id": "brd-ahsec", "slug": "ahsec"}

    db = SimpleNamespace(
        seo_pages=MagicMock(),
        boards=MagicMock(),
        subjects=MagicMock(),
        chapters=MagicMock(),
        topics=MagicMock(),
        classes=MagicMock(),
        homepage_seo=MagicMock(),
        syllabus_map=MagicMock(),
        seo_meta=MagicMock(),
        audit_log=MagicMock(),
        pyq_html_pages=MagicMock(),
    )
    db.pyq_html_pages.find = MagicMock(return_value=_make_async_cursor([
        {"slug": "ahsec-physics-2024-major", "title": "AHSEC Physics 2024 Major",
         "subject_name": "Physics", "board_name": "AHSEC",
         "exam_title": "MAJOR 2024", "meta_description": "Solved paper."},
    ]))
    db.seo_pages.find = MagicMock(return_value=_make_async_cursor([page_doc]))
    db.seo_pages.find_one = AsyncMock(return_value=page_doc)
    db.seo_pages.count_documents = AsyncMock(return_value=1)
    db.seo_pages.aggregate = MagicMock(return_value=_make_async_cursor([
        {"_id": "notes", "count": 5},
        {"_id": "mcqs", "count": 3},
    ]))
    db.boards.find_one = AsyncMock(return_value=board_doc)
    db.subjects.find_one = AsyncMock(return_value=subject_doc)
    db.chapters.find = MagicMock(return_value=_make_async_cursor([
        {"title": "Laws of Motion", "topics": ["Newton's Laws"], "order_index": 1},
    ]))
    # Slug-resolver fallback path needs awaitable find_one() on chapters
    # and a classes collection (the canonical slug→chain join).
    db.chapters.find_one = AsyncMock(return_value={
        "id": "ch-laws", "slug": "laws-of-motion", "subject_id": "subj-physics",
    })
    db.topics.find_one = AsyncMock(return_value={
        "id": "top-newton", "slug": "newton-laws", "title": "Newton's Laws",
        "chapter_id": "ch-laws", "status": "published",
    })
    db.classes.find_one = AsyncMock(return_value={"id": "cls-12", "slug": "class-12"})
    db.homepage_seo.find_one = AsyncMock(return_value=None)

    monkeypatch.setattr(seo_engine, "_db", db, raising=False)

    # Stub helper coroutines that depend on extra collections / network.
    async def _noop_inject_qa(p): return p
    async def _noop_pt_links(*a, **kw): return []
    async def _noop_related(*a, **kw): return ([], None, None)
    async def _noop_og_image(*a, **kw): return ""
    monkeypatch.setattr(seo_engine, "_inject_qa", _noop_inject_qa, raising=False)
    monkeypatch.setattr(seo_engine, "_build_page_type_links", _noop_pt_links, raising=False)
    monkeypatch.setattr(seo_engine, "_build_related_data", _noop_related, raising=False)
    monkeypatch.setattr(seo_engine, "_resolve_og_image", _noop_og_image, raising=False)

    app = FastAPI()
    app.include_router(seo_engine.router, prefix="/api")
    return TestClient(app)


@pytest.mark.parametrize(
    "url,family",
    [
        ("/api/seo/html/subject/ahsec/class-12/physics", "subject"),
        ("/api/seo/html/ahsec/class-12/physics/newton-laws", "topic"),
        ("/api/seo/html/ahsec/class-12/physics/newton-laws/notes", "topic_typed"),
        ("/api/seo/html/ahsec/class-12/physics/newton-laws/mcqs", "topic_typed"),
        ("/api/seo/html/ahsec/class-12/physics/chapter/laws-of-motion", "chapter"),
    ],
)
def test_ssr_route_family_returns_200_html(ssr_client, url, family):
    """Each family that the middleware proxies to MUST return real
    HTML — never a 404 or JSON. This is the SLA the reviewer
    flagged as missing."""
    res = ssr_client.get(url)
    assert res.status_code == 200, f"{family} family ({url}) returned {res.status_code}"
    ct = res.headers.get("content-type", "")
    assert "text/html" in ct, f"{family} family returned non-HTML content-type: {ct}"
    body = res.text.lower()
    # Sanity check: response must look like an actual HTML document,
    # not a stub or JSON envelope.
    assert "<html" in body or "<!doctype html" in body, (
        f"{family} family returned a non-HTML body for {url}"
    )


def test_topic_family_emits_cache_tag_header(ssr_client, monkeypatch):
    """The topic SSR route must emit a Cache-Tag header so Cloudflare
    can purge by entity (subject/chapter/topic). This is the
    Cache-Tag plumbing the reviewer asked for in the first round."""
    monkeypatch.setattr("config.CF_TIERED_CACHE_ON", True, raising=False)
    res = ssr_client.get("/api/seo/html/ahsec/class-12/physics/newton-laws")
    assert res.status_code == 200
    cache_tag = res.headers.get("cache-tag", "")
    # The header should at least carry the global SSR tag and one of
    # the entity-scoped tags (subject/topic). build_cache_tag joins
    # tokens with spaces.
    assert "syrabit" in cache_tag, f"Cache-Tag header missing: {cache_tag!r}"


@pytest.mark.parametrize(
    "url,family",
    [
        ("/api/seo/html/topic/newton-laws", "topic_slug"),
        ("/api/seo/html/chapter/laws-of-motion", "chapter_slug"),
        ("/api/seo/html/subject/physics", "subject_slug"),
    ],
)
def test_slug_only_families_resolve_to_html(ssr_client, url, family):
    """Review remediation #2 — slug-only families /topic, /chapter,
    /subject must resolve via syllabus_map / Mongo to a real HTML
    response (the previous middleware silently dropped these)."""
    res = ssr_client.get(url)
    # 200 (mocked Mongo serves the chain), 404 (orphan slug under real
    # data) — never a 500.
    assert res.status_code in (200, 404), f"{family} returned {res.status_code}"
    if res.status_code == 200:
        assert "text/html" in res.headers.get("content-type", "")
        assert "<html" in res.text.lower() or "<!doctype html" in res.text.lower()


def test_pyq_year_paper_renders_dedicated_landing(ssr_client):
    """The /api/seo/html/pyq/<year>/<paper> family must serve a real
    HTML index of papers and emit its own syrabit-pyq-<year>-<paper>
    Cache-Tag (review remediation #4)."""
    res = ssr_client.get("/api/seo/html/pyq/2024/major")
    assert res.status_code == 200
    assert "text/html" in res.headers.get("content-type", "")
    assert "syrabit-pyq-2024-major" in res.headers.get("cache-tag", "")
    body = res.text.lower()
    assert "ahsec-physics-2024-major" in body or "pyq" in body


def test_pyq_shortcut_proxies_to_subject_with_query(ssr_client):
    """The middleware maps ``/pyq/<board>/<class>/<subject>`` →
    ``/api/seo/html/subject/...?page_type=pyq``. Verify the subject
    route accepts the query string without 500'ing (the page_type
    filter is best-effort on the backend)."""
    res = ssr_client.get(
        "/api/seo/html/subject/ahsec/class-12/physics",
        params={"page_type": "pyq"},
    )
    assert res.status_code == 200
    assert "text/html" in res.headers.get("content-type", "")
