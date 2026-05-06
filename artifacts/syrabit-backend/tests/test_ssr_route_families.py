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
        # Per-locale (`*_as`) fields exercise the `_localized()` lookup
        # in seo_engine. Required so the Task #432 test can assert that
        # the JSON-LD `name` / `description` / breadcrumb / about / FAQ
        # nodes render with Assamese values, not just the framework
        # labels.
        "topic_title_as": "নিউটনৰ গতিৰ সূত্ৰসমূহ",
        "subject_name_as": "পদাৰ্থ বিজ্ঞান",
        "chapter_title_as": "গতিৰ সূত্ৰসমূহ",
        "meta_description_as": "নিউটনৰ তিনিটা সূত্ৰৰ সম্পূৰ্ণ টোকা।",
        "title_as": "নিউটনৰ গতিৰ সূত্ৰসমূহ — অধ্যয়ন টোকা",
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
    db.subjects.count_documents = AsyncMock(return_value=1)
    db.chapters.count_documents = AsyncMock(return_value=1)
    # The aggregate output depends on the pipeline shape: subject /
    # about routes group by ``$page_type`` (string ``_id``); the
    # homepage groups by ``{board, cls, subj}`` (dict ``_id``). The
    # mock inspects the pipeline and returns the matching shape.
    def _aggregate(pipeline):
        group_id = next((st["$group"]["_id"] for st in pipeline if "$group" in st), None)
        if isinstance(group_id, dict):
            return _make_async_cursor([
                {"_id": {"board": "ahsec", "cls": "class-12", "subj": "physics"},
                 "subject_name": "Physics", "board_name": "AHSEC",
                 "class_name": "Class 12", "count": 12},
            ])
        return _make_async_cursor([
            {"_id": "notes", "count": 5},
            {"_id": "mcqs", "count": 3},
        ])
    db.seo_pages.aggregate = MagicMock(side_effect=_aggregate)
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


def test_pyq_shortcut_falls_back_to_full_syllabus_when_no_pyq_pages(monkeypatch):
    """Task #464 — when ``page_type=pyq`` is set for a subject that
    has zero ``important-questions`` pages generated yet, the
    subject landing must fall back to listing every chapter/topic
    from the syllabus marked as 'PYQ coming soon' rather than
    rendering an almost-empty topic grid."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import seo_engine

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
    # No PYQ (important-questions) pages exist for this subject.
    db.seo_pages.find = MagicMock(return_value=_make_async_cursor([]))
    db.seo_pages.find_one = AsyncMock(return_value=None)
    db.seo_pages.count_documents = AsyncMock(return_value=0)
    db.seo_pages.aggregate = MagicMock(return_value=_make_async_cursor([]))
    db.boards.find_one = AsyncMock(return_value=board_doc)
    db.subjects.find_one = AsyncMock(return_value=subject_doc)
    db.chapters.find = MagicMock(return_value=_make_async_cursor([
        {"title": "Laws of Motion",
         "topics": ["Newton's First Law", "Newton's Second Law"],
         "order_index": 1},
        {"title": "Work and Energy",
         "topics": "Work, Kinetic Energy, Potential Energy",
         "order_index": 2},
    ]))
    db.homepage_seo.find_one = AsyncMock(return_value=None)

    monkeypatch.setattr(seo_engine, "_db", db, raising=False)

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
    client = TestClient(app)

    res = client.get(
        "/api/seo/html/subject/ahsec/class-12/physics",
        params={"page_type": "pyq"},
    )
    assert res.status_code == 200
    body = res.text

    # The PYQ framing (title/meta/canonical) must still apply.
    assert "Previous Year Questions" in body
    assert "https://syrabit.ai/pyq/ahsec/class-12/physics" in body

    # Both syllabus chapters must appear in the topic list.
    assert "Laws of Motion" in body
    assert "Work and Energy" in body

    # Each topic from the syllabus (list-form and CSV-form) must be
    # rendered as a clickable PYQ link, even though no PYQ page
    # exists yet (links go to the per-topic important-questions URL
    # which will generate on demand).
    assert "newtons-first-law/important-questions" in body
    assert "newtons-second-law/important-questions" in body
    assert "work/important-questions" in body
    assert "kinetic-energy/important-questions" in body
    assert "potential-energy/important-questions" in body

    # The "PYQ coming soon" hint must surface so visitors understand
    # why the per-topic pages aren't ready yet.
    assert "PYQ coming soon" in body

    # The PYQ stat badge must reflect the real count (0 sets), not
    # the synthesized topic count.
    assert "<strong>0</strong> PYQ sets" not in body  # zero suppressed entirely
    # Default subject "Complete Study Guide" framing must NOT leak.
    assert "Complete Study Guide" not in body


def test_pyq_shortcut_proxies_to_subject_with_query(ssr_client):
    """The middleware maps ``/pyq/<board>/<class>/<subject>`` →
    ``/api/seo/html/subject/...?page_type=pyq``. Task #431 — when
    ``page_type=pyq`` is set, the subject landing must reframe to
    PYQ (title/meta + topic links pointing at the PYQ page)."""
    res = ssr_client.get(
        "/api/seo/html/subject/ahsec/class-12/physics",
        params={"page_type": "pyq"},
    )
    assert res.status_code == 200
    assert "text/html" in res.headers.get("content-type", "")
    body = res.text
    # Title + meta description must reflect the PYQ framing.
    assert "Previous Year Questions" in body, "PYQ title framing missing"
    assert "PYQ" in body
    # Topic links must point at the per-topic PYQ (important-questions)
    # page, not the generic notes landing.
    assert "/important-questions" in body, "PYQ topic links missing"
    # The default subject framing should NOT leak through.
    assert "Complete Study Guide" not in body
    # Canonical URL should reflect the PYQ shortcut path.
    assert "https://syrabit.ai/pyq/ahsec/class-12/physics" in body


# Task #408 — every SSR family the Pages middleware proxies must emit
# a BreadcrumbList JSON-LD node so the SSR-coverage probe can assert
# structured data on every match (not just the canonical topic page).
@pytest.mark.parametrize(
    "url",
    [
        "/api/seo/html/homepage",
        "/api/seo/html/about",
        "/api/seo/html/subject/ahsec/class-12/physics",
        "/api/seo/html/ahsec/class-12/physics/newton-laws",
        "/api/seo/html/ahsec/class-12/physics/chapter/laws-of-motion",
        "/api/seo/html/chapter/laws-of-motion",
        "/api/seo/html/pyq/2024/major",
    ],
)
def test_every_family_emits_breadcrumb_jsonld(ssr_client, url):
    res = ssr_client.get(url)
    assert res.status_code == 200, url
    body = res.text
    assert "application/ld+json" in body, f"{url} missing JSON-LD block"
    assert "BreadcrumbList" in body, f"{url} missing BreadcrumbList JSON-LD"


def test_subject_route_accepts_lang_as_and_flips_html_lang(ssr_client):
    """Task #408 — the Assamese URL family (``/as/...``) is rewritten
    by the middleware to ``?lang=as``. The subject renderer must
    accept the param and flip ``<html lang>`` so screen readers and
    Google know the page is an Assamese variant."""
    res = ssr_client.get(
        "/api/seo/html/subject/ahsec/class-12/physics",
        params={"lang": "as"},
    )
    assert res.status_code == 200
    assert '<html lang="as-IN">' in res.text


def test_lang_as_renders_assamese_strings_in_body(ssr_client):
    """Task #432 — every SSR family must surface visible Assamese
    when ``?lang=as`` is sent. We assert specific glyph strings
    (breadcrumbs, section headings, JSON-LD ``inLanguage``) — flipping
    only ``<html lang>`` is no longer sufficient."""
    families = [
        # subject landing
        ("/api/seo/html/subject/ahsec/class-12/physics", ["ঘৰ", "পুথিভঁৰাল", "পাঠ্যক্ৰম পৰিচয়"]),
        # default topic notes
        ("/api/seo/html/ahsec/class-12/physics/newton-laws", ["ঘৰ", "পুথিভঁৰাল", "এই অধ্যয়ন সামগ্ৰীৰ বিষয়ে"]),
        # typed topic page (mcqs)
        ("/api/seo/html/ahsec/class-12/physics/newton-laws/mcqs", ["ঘৰ", "পুথিভঁৰাল", "এই অধ্যয়ন সামগ্ৰীৰ বিষয়ে"]),
        # board-scoped chapter landing
        ("/api/seo/html/ahsec/class-12/physics/chapter/laws-of-motion", ["ঘৰ", "এই অধ্যায়ৰ বিষয়সমূহ"]),
        # slug-only chapter
        ("/api/seo/html/chapter/laws-of-motion", ["ঘৰ", "এই অধ্যায়ৰ বিষয়সমূহ"]),
        # slug-only topic
        ("/api/seo/html/topic/newton-laws", ["ঘৰ", "এই অধ্যয়ন সামগ্ৰীৰ বিষয়ে"]),
        # slug-only subject
        ("/api/seo/html/subject/physics", ["ঘৰ", "পাঠ্যক্ৰম পৰিচয়"]),
        # PYQ year+paper landing
        ("/api/seo/html/pyq/2024/major", ["ঘৰ", "বিগত বছৰৰ প্ৰশ্ন"]),
    ]
    for url, expected_strings in families:
        res = ssr_client.get(url, params={"lang": "as"})
        assert res.status_code == 200, f"{url} → {res.status_code}"
        body = res.text
        assert '<html lang="as-IN">' in body, f"{url} missing as-IN html lang"
        # Hreflang must continue to point as-IN at the same URL the
        # reader is on (and en-IN at the canonical English URL).
        assert 'hreflang="as-IN"' in body, f"{url} missing as-IN hreflang"
        assert 'hreflang="en-IN"' in body, f"{url} missing en-IN hreflang"
        # JSON-LD must declare the page as Assamese so structured-data
        # consumers don't tag the page as English.
        assert '"inLanguage": "as-IN"' in body, f"{url} missing inLanguage as-IN in JSON-LD"
        # og:locale + content-language must flip too.
        assert 'content="as_IN"' in body, f"{url} missing og:locale as_IN"
        assert 'content-language" content="as-IN"' in body, f"{url} missing content-language as-IN"
        # Body strings — the actual visible-text assertion the task
        # spec requires (must not just be the <html lang> attribute).
        for needle in expected_strings:
            assert needle in body, f"{url} missing Assamese string {needle!r}"

    # Topic-family JSON-LD must surface Assamese values from the
    # `*_as` sibling fields (Article.headline / .description /
    # .about.name / .educationalAlignment.targetName, LearningResource
    # .description / .teaches, BreadcrumbList item names, WebPage
    # .name / .description). The fallback FAQ Question.name / Answer
    # .text are likewise generated in Assamese when the doc resolves
    # to ``lang=as``.
    res = ssr_client.get(
        "/api/seo/html/ahsec/class-12/physics/newton-laws",
        params={"lang": "as"},
    )
    assert res.status_code == 200
    body = res.text
    # Pull the Article + LearningResource + BreadcrumbList + WebPage
    # node from the @graph and check Assamese values are inside.
    assert '"name": "নিউটনৰ গতিৰ সূত্ৰসমূহ"' in body, "JSON-LD name missing Assamese topic title"
    assert '"headline": "নিউটনৰ গতিৰ সূত্ৰসমূহ — অধ্যয়ন টোকা"' in body, "Article.headline not Assamese"
    assert '"description": "নিউটনৰ তিনিটা সূত্ৰৰ সম্পূৰ্ণ টোকা।"' in body, "JSON-LD description not Assamese"
    assert '"targetName": "পদাৰ্থ বিজ্ঞান"' in body, "educationalAlignment.targetName not Assamese"
    assert '"teaches": "নিউটনৰ গতিৰ সূত্ৰসমূহ"' in body, "LearningResource.teaches not Assamese"
    # The auto-generated FAQ fallback question must read in Assamese.
    assert "পদাৰ্থ বিজ্ঞানত নিউটনৰ গতিৰ সূত্ৰসমূহ কি?" in body, "Auto-FAQ Question.name not Assamese"

    # Sanity: the *English* run of the same URL must keep English
    # JSON-LD values, so we know the localization is gated on `lang`
    # and not a global rewrite.
    res_en = ssr_client.get("/api/seo/html/ahsec/class-12/physics/newton-laws")
    assert res_en.status_code == 200
    assert "Newton's Laws of Motion" in res_en.text
    assert '"inLanguage": "en-IN"' in res_en.text


def test_homepage_about_and_subject_record_ssr_render(ssr_client):
    """Task #408 — homepage / about / subject / typed-topic hits must
    increment the cf-health ``rendered`` counter so the success-rate
    row reflects every family the middleware can serve, not just the
    canonical topic page."""
    import cf_ssr_health
    cf_ssr_health.reset()
    for url in (
        "/api/seo/html/homepage",
        "/api/seo/html/about",
        "/api/seo/html/subject/ahsec/class-12/physics",
        "/api/seo/html/ahsec/class-12/physics/newton-laws/notes",
    ):
        assert ssr_client.get(url).status_code == 200, url
    snap = asyncio.new_event_loop().run_until_complete(
        cf_ssr_health.snapshot(probe=False)
    )
    assert snap["rendered"] >= 4, snap
    assert snap["fallback"] == 0, snap
    assert snap["success_rate"] == 1.0, snap

