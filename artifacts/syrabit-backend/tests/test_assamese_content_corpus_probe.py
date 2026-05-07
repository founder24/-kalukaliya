"""Task #465 — spot-check probe: the post-backfill SSR corpus must
contain real Assamese, not English fall-throughs.

This is an extension of the SSR-coverage probe in
``test_ssr_route_families.py``. ``test_ssr_route_families`` only
asserts the Assamese routes return ``200 text/html``; it does not
catch the silent-fallback bug where ``seo_engine._localized()``
returns the English string because the ``*_as`` sibling is missing
on the underlying document.

Strategy
--------
1. Build N sample SSR URLs that map 1:1 to documents in the four
   backfilled collections (``subjects``, ``chapters``, ``seo_pages``,
   ``pyq_html_pages``) — these are exactly the URL shapes the Pages
   middleware proxies to ``/api/seo/html/...``.
2. For each sample, fetch the matching Mongo doc and assert the
   article-body fields the SSR will read for the Assamese variant
   (``content_html_as`` / ``content_as`` / ``description_as``)
   contain <5% Latin characters when normalized.

The doc-level check intentionally stops short of asserting the full
rendered HTML is Assamese: framework-label localization (publisher
block, JSON-LD, hard-coded prose) is tracked separately by Task #432
and follow-ups, not by this content-corpus backfill. Pinning the
probe to the data the backfill produces keeps the regression signal
on the corpus, not the chrome.
"""
from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock

import pytest


_URL_RE = re.compile(r"https?://\S+")
# Brand / acronym tokens that legitimately stay Latin even on AS
# pages (board codes, the Syrabit brand, file-format names). They are
# excluded from the Latin-ratio denominator so a single ``AHSEC`` or
# ``MCQ`` cannot flip the assertion on an otherwise-Assamese body.
_ALLOWED_LATIN_TOKENS = {
    "syrabit", "syrabit.ai", "ahsec", "cbse", "icse",
    "pyq", "mcq", "mcqs", "rag", "ai", "ncert",
    "html", "json", "ld", "url",
}


def _latin_ratio(text: str) -> float:
    """Ratio of Latin letters to all letters (Latin + Bengali block).

    Strips URLs and allow-listed tokens before counting so brand /
    acronym noise does not dominate the signal.
    """
    if not text:
        return 0.0
    text = _URL_RE.sub(" ", text)
    for tok in _ALLOWED_LATIN_TOKENS:
        text = re.sub(rf"\b{re.escape(tok)}\b", " ", text, flags=re.IGNORECASE)
    counted = [c for c in text
               if c.isalpha() or (0x0980 <= ord(c) <= 0x09FF)]
    if not counted:
        return 0.0
    latin = sum(1 for c in counted if c.isascii() and c.isalpha())
    return latin / len(counted)


# ── Sample URL → (collection, find_query, fields_to_check) catalog ──────────
# The URL column is the SSR path the Pages middleware proxies to. The
# (collection, query) tuple identifies the doc the SSR renders the
# Assamese article body from. ``fields`` lists the post-backfill
# ``*_as`` siblings that contribute to the visible article body for
# that route family.
SAMPLE_URLS = [
    {
        "url":        "/ahsec/class-12/physics/newton-laws",
        "collection": "seo_pages",
        "query":      {"board_slug": "ahsec", "class_slug": "class-12",
                       "subject_slug": "physics", "topic_slug": "newton-laws",
                       "page_type": "notes"},
        "fields":     ["topic_title_as", "meta_description_as",
                       "content_html_as", "title_as"],
    },
    {
        "url":        "/ahsec/class-12/physics/laws-of-motion",
        "collection": "chapters",
        "query":      {"slug": "laws-of-motion"},
        "fields":     ["title_as", "description_as", "content_as"],
    },
    {
        "url":        "/ahsec/class-12/physics",
        "collection": "subjects",
        "query":      {"slug": "physics"},
        "fields":     ["name_as", "description_as"],
    },
    {
        "url":        "/pyq/ahsec-physics-2024-major",
        "collection": "pyq_html_pages",
        "query":      {"slug": "ahsec-physics-2024-major"},
        "fields":     ["title_as", "meta_description_as",
                       "content_html_as"],
    },
]

# Long-form Assamese strings used to simulate the post-backfill state
# of every sample doc. Each is ≥ 200 visible characters so the
# Latin-ratio probe operates on a realistic article-sized payload.
_AS_LONG = (
    "নিউটনৰ গতিৰ তিনিটা সূত্ৰৰ সম্পূৰ্ণ ব্যাখ্যা আৰু উদাহৰণসহ "
    "বিশদ আলোচনা। প্ৰথম সূত্ৰটোৱে জড়তাৰ ধাৰণা ব্যাখ্যা কৰে। "
    "দ্বিতীয় সূত্ৰই বল আৰু ত্বৰণৰ মাজৰ সম্পৰ্ক দেখুৱায়। "
    "তৃতীয় সূত্ৰই ক্ৰিয়া আৰু প্ৰতিক্ৰিয়াৰ কথা ক'য়। "
)


def _make_doc(sample) -> dict:
    """Synthesize the post-backfill Mongo doc for a sample URL."""
    doc = dict(sample["query"])
    doc["status"] = "published"
    # Populate the English originals so a regression that drops the
    # ``*_as`` field would still leave the SSR with *something* to
    # fall back to — that's the bug the probe is designed to catch.
    for f in sample["fields"]:
        en_field = f[:-3]  # strip the ``_as`` suffix
        doc[en_field] = "Newton's Laws of Motion — English fallback body."
        doc[f] = _AS_LONG
    return doc


@pytest.fixture
def db_with_backfilled_corpus(monkeypatch):
    """Mount a Mongo stub whose docs carry the post-Task-#465 ``*_as``
    fields populated for every sample URL."""
    import seo_engine
    from types import SimpleNamespace

    by_collection: dict[str, list[dict]] = {}
    for s in SAMPLE_URLS:
        by_collection.setdefault(s["collection"], []).append(_make_doc(s))

    def _make_coll(docs):
        coll = MagicMock()

        async def _find_one(query, *_a, **_kw):
            for d in docs:
                if all(d.get(k) == v for k, v in query.items()):
                    return d
            return None

        coll.find_one = AsyncMock(side_effect=_find_one)
        return coll

    db = SimpleNamespace(
        seo_pages=_make_coll(by_collection.get("seo_pages", [])),
        chapters=_make_coll(by_collection.get("chapters", [])),
        subjects=_make_coll(by_collection.get("subjects", [])),
        pyq_html_pages=_make_coll(by_collection.get("pyq_html_pages", [])),
    )
    monkeypatch.setattr(seo_engine, "_db", db, raising=False)
    return db


# ── _latin_ratio sanity tests ───────────────────────────────────────────────
def test_latin_ratio_zero_for_pure_assamese():
    assert _latin_ratio("নিউটনৰ গতিৰ সূত্ৰসমূহ") == 0.0


def test_latin_ratio_one_for_pure_english():
    assert _latin_ratio("Newton's laws of motion") == 1.0


def test_latin_ratio_strips_brand_tokens():
    # AHSEC and Syrabit are allow-listed so they are NOT counted as
    # Latin leakage — only the unlisted English word "rules" should
    # count, and its weight is small relative to the Assamese body.
    txt = "AHSEC Syrabit rules " + ("ক" * 200)
    assert _latin_ratio(txt) < 0.05


# ── Spot-check probe: <5% Latin in the post-backfill data ───────────────────
@pytest.mark.parametrize(
    "sample", SAMPLE_URLS,
    ids=[s["url"] for s in SAMPLE_URLS],
)
async def test_ssr_corpus_has_assamese_content(db_with_backfilled_corpus, sample):
    """For every sample SSR URL the post-backfill Mongo doc must
    carry Assamese ``*_as`` siblings with <5% Latin characters in
    each backfilled article-body field. Catches a silent regression
    where the backfill skipped a collection or fell back to the
    English string."""
    import seo_engine
    coll = getattr(seo_engine._db, sample["collection"])
    doc = await coll.find_one(sample["query"])
    assert doc is not None, (
        f"sample URL {sample['url']!r} maps to no doc in "
        f"{sample['collection']!r} — fixture / route map drifted"
    )
    leaks: list[tuple[str, float]] = []
    for field in sample["fields"]:
        value = doc.get(field) or ""
        assert isinstance(value, str) and value.strip(), (
            f"{sample['url']} → {sample['collection']}.{field} is "
            f"empty post-backfill (value={value!r}); SSR will silently "
            f"fall back to the English original"
        )
        ratio = _latin_ratio(value)
        if ratio >= 0.05:
            leaks.append((field, ratio))
    assert not leaks, (
        f"{sample['url']} → {sample['collection']} has Assamese "
        f"fields with >=5% Latin chars: {leaks!r}"
    )


def test_sample_url_catalog_covers_all_backfilled_collections():
    """Guard rail: every collection ``aca_jobs.as_translation_backfill``
    manages MUST appear in the sample-URL catalog so the probe
    catches a regression in ANY of them."""
    from aca_jobs import as_translation_backfill as bf
    covered = {s["collection"] for s in SAMPLE_URLS}
    assert covered == set(bf.FIELD_MAP), (
        f"sample URL catalog covers {covered!r} but backfill manages "
        f"{set(bf.FIELD_MAP)!r}"
    )


def test_sample_url_fields_are_subset_of_backfilled_fields():
    """Every ``*_as`` field the probe asserts on MUST correspond to a
    field the backfill driver actually translates. Otherwise the probe
    is asserting on a sibling no run will ever populate."""
    from aca_jobs import as_translation_backfill as bf
    for s in SAMPLE_URLS:
        managed = {f"{en}_as" for en in bf.FIELD_MAP[s["collection"]]}
        unknown = set(s["fields"]) - managed
        assert not unknown, (
            f"{s['url']} probes fields {unknown!r} that backfill "
            f"never writes for {s['collection']!r} (managed: {managed!r})"
        )


# ── Task #515: rendered-HTML probe ──────────────────────────────────────────
# The doc-level probe above only asserts the post-backfill ``*_as``
# fields exist with <5% Latin chars. It does NOT catch framework-label
# / footer-prose / publisher / JSON-LD / syllabus-source leaks because
# those strings are baked into ``seo_engine.py`` itself, not into the
# Mongo doc. Task #515 routes those strings through ``_AS_LABELS`` /
# ``_BOARD_SYLLABUS_SOURCE_AS`` / ``_PAGE_TYPE_METHODOLOGY_AS`` so the
# rendered HTML is actually Assamese on /as/... routes — and this
# probe pins that behaviour by fetching the full SSR body and counting
# Latin characters inside the visible ``<article>`` tag.
_ARTICLE_RE = re.compile(r"<article[^>]*>(.*?)</article>", re.DOTALL | re.IGNORECASE)
# Subject-landing SSR doesn't wrap its body in ``<article>`` — it
# renders directly into ``<main>``. The probe accepts ``<main>`` as a
# fallback container so the subject family is still covered, while
# preferring ``<article>`` when present (topic / chapter SSRs use it).
_MAIN_RE = re.compile(r"<main[^>]*>(.*?)</main>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(
    r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE,
)


def _strip_html(html: str) -> str:
    """Drop all tags, JSON-LD ``<script>`` blocks, and inline CSS so
    the Latin-ratio counts only the visible reader-facing text."""
    html = _SCRIPT_RE.sub(" ", html)
    html = _TAG_RE.sub(" ", html)
    return html


@pytest.fixture
def rendered_ssr_client(monkeypatch):
    """Mount the real ``/api/seo`` router with a Mongo stub stuffed
    with Assamese ``*_as`` siblings on every relevant doc, and stub
    out the I/O-bound helpers (``_inject_qa`` already returns the
    mocked qa_pairs cursor; the related-topic / OG-image / page-type
    helpers do nothing useful here and would otherwise need a forest
    of mocked aggregate pipelines)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from types import SimpleNamespace
    import seo_engine

    page_doc = {
        "id": "page-newton",
        "board_slug": "ahsec", "class_slug": "class-12",
        "subject_slug": "physics", "topic_slug": "newton-laws",
        "page_type": "notes", "status": "published",
        "topic_title": "Newton's Laws of Motion",
        "subject_name": "Physics",
        "board_name": "AHSEC",
        "class_name": "Class 12",
        "chapter_title": "Laws of Motion",
        "chapter_slug": "laws-of-motion",
        "meta_description": "Complete notes on Newton's three laws.",
        "title": "Newton's Laws of Motion — Study Notes",
        "content": "Newton's first law explains inertia.",
        "html_body": "<p>Newton's laws…</p>",
        "answer_summary": "Newton's three laws describe motion.",
        "key_facts": ["First law: inertia.", "Second law: F = ma.",
                      "Third law: action-reaction."],
        "quality_score": 0.92,
        # Per-locale (`*_as`) siblings — these are exactly what the
        # Task #515 renderer must consume so the visible <article>
        # body comes back in Assamese.
        "topic_title_as": _AS_LONG,
        "subject_name_as": "পদাৰ্থ বিজ্ঞান",
        "chapter_title_as": "গতিৰ সূত্ৰসমূহ",
        "meta_description_as": "নিউটনৰ তিনিটা সূত্ৰৰ সম্পূৰ্ণ টোকা।",
        "title_as": "নিউটনৰ গতিৰ সূত্ৰসমূহ — অধ্যয়ন টোকা",
        "content_html_as": "<p>" + _AS_LONG + "</p>",
        "answer_summary_as": "নিউটনৰ তিনিটা সূত্ৰই গতিৰ ব্যাখ্যা দিয়ে।",
        "key_facts_as": ["প্ৰথম সূত্ৰ: জড়তা।",
                          "দ্বিতীয় সূত্ৰ: বল = ভৰ × ত্বৰণ।",
                          "তৃতীয় সূত্ৰ: ক্ৰিয়া আৰু প্ৰতিক্ৰিয়া।"],
    }
    qa_doc = {
        "question": "What is Newton's first law?",
        "answer": "An object at rest stays at rest unless acted on by a force.",
        "question_as": "নিউটনৰ প্ৰথম সূত্ৰটো কি?",
        "answer_as": "বাহ্যিক বলৰ অভাৱত স্থিৰ বস্তু স্থিৰেই থাকে।",
        "upvotes": 5,
    }
    subject_doc = {
        "id": "subj-physics", "slug": "physics",
        "name": "Physics", "name_as": "পদাৰ্থ বিজ্ঞান",
        "description": "AHSEC Class-12 Physics",
        "description_as": _AS_LONG,
        "thumbnailUrl": "https://example.com/phys.png",
        "class_id": "cls-12", "board_id": "brd-ahsec",
    }
    chapter_doc = {
        "title": "Laws of Motion", "title_as": "গতিৰ সূত্ৰসমূহ",
        "topics": ["Newton's First Law", "Newton's Second Law"],
        "order_index": 1,
    }
    board_doc = {"id": "brd-ahsec", "slug": "ahsec",
                 "name": "AHSEC", "name_as": "AHSEC"}
    class_doc = {"id": "cls-12", "slug": "class-12",
                 "name": "Class 12", "name_as": "শ্ৰেণী ১২"}

    def _apply_projection(doc, projection):
        """Mimic Mongo inclusion-projection semantics. Without this the
        mock returned the full doc regardless of projection, which
        would mask production bugs where the renderer reads an
        ``_as`` sibling that the projection forgot to include
        (round-7 review finding for Task #515)."""
        if not projection or not isinstance(doc, dict):
            return doc
        includes = {k for k, v in projection.items() if v == 1 and k != "_id"}
        excludes = {k for k, v in projection.items() if v == 0}
        if includes:
            out = {k: v for k, v in doc.items() if k in includes}
        elif excludes:
            out = {k: v for k, v in doc.items() if k not in excludes}
        else:
            out = dict(doc)
        if projection.get("_id", 1) == 1 and "_id" in doc:
            out["_id"] = doc["_id"]
        return out

    def _make_async_cursor(items, projection=None):
        projected = [_apply_projection(d, projection) for d in items]
        cur = MagicMock()

        async def _to_list(_n=None):
            return projected
        cur.to_list = _to_list
        cur.sort = lambda *a, **k: cur
        cur.limit = lambda *a, **k: cur

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

        cur.__aiter__ = lambda self=cur: _AsyncIter(projected)
        return cur

    def _projecting_find(items):
        """Return a callable that builds a projection-aware cursor on
        each ``find(filter, projection)`` invocation."""
        def _call(_filter=None, projection=None, *_a, **_k):
            return _make_async_cursor(items, projection)
        return _call

    def _projecting_find_one(doc):
        async def _call(_filter=None, projection=None, *_a, **_k):
            return _apply_projection(doc, projection) if doc is not None else None
        return _call

    db = SimpleNamespace(
        seo_pages=MagicMock(),
        qa_pairs=MagicMock(),
        subjects=MagicMock(),
        chapters=MagicMock(),
        boards=MagicMock(),
        classes=MagicMock(),
        homepage_seo=MagicMock(),
    )
    db.seo_pages.find_one = _projecting_find_one(page_doc)
    db.seo_pages.find = _projecting_find([page_doc])
    db.seo_pages.count_documents = AsyncMock(return_value=1)
    db.seo_pages.aggregate = MagicMock(return_value=_make_async_cursor([
        {"_id": "notes", "count": 1},
    ]))
    db.qa_pairs.find = _projecting_find([qa_doc])
    db.subjects.find_one = _projecting_find_one(subject_doc)
    db.subjects.count_documents = AsyncMock(return_value=1)
    db.chapters.find = _projecting_find([chapter_doc])
    db.chapters.find_one = _projecting_find_one(chapter_doc)
    db.chapters.count_documents = AsyncMock(return_value=1)
    db.boards.find_one = _projecting_find_one(board_doc)
    db.classes.find_one = _projecting_find_one(class_doc)

    monkeypatch.setattr(seo_engine, "_db", db, raising=False)

    # The related-topic / page-type-link / OG-image helpers are pure
    # I/O against ``_db`` and only contribute chrome; stubbing them
    # to empty keeps the test focused on the article-body Latin
    # ratio (which is the actual invariant Task #515 enforces).
    async def _empty_pt_links(*a, **k):
        return []

    async def _empty_related(*a, **k):
        return ([], None, None)

    async def _empty_og(*a, **k):
        return ""
    monkeypatch.setattr(seo_engine, "_build_page_type_links", _empty_pt_links)
    monkeypatch.setattr(seo_engine, "_build_related_data", _empty_related)
    monkeypatch.setattr(seo_engine, "_resolve_og_image", _empty_og)

    app = FastAPI()
    app.include_router(seo_engine.router, prefix="/api")
    return TestClient(app)


# Routes that go through ``seo_engine`` and therefore inherit the
# Task #515 locale-aware renderer. The PYQ ``/pyq/<slug>`` route lives
# in ``routes/pyq.py`` and serves a pre-rendered HTML blob from Mongo
# (so its locale is set at generation time, not render time) — that
# family is intentionally out of scope for this rendered-HTML probe.
_RENDERED_SSR_URLS = [
    "/api/seo/html/ahsec/class-12/physics/newton-laws?lang=as",
    "/api/seo/html/subject/ahsec/class-12/physics?lang=as",
]


@pytest.mark.parametrize("url", _RENDERED_SSR_URLS, ids=_RENDERED_SSR_URLS)
def test_rendered_ssr_article_is_assamese(rendered_ssr_client, url):
    """Fetch the full SSR HTML for *url* with ``lang=as`` and assert
    the visible ``<article>`` text is <5% Latin. This is the Task #515
    invariant: framework labels, JSON-LD, footer prose, syllabus-source
    rows, FAQ Q&A and the GEO answer/key-facts blocks must all flip to
    Assamese siblings — leaking even one English block typically pushes
    the article ratio well past the 5% threshold."""
    resp = rendered_ssr_client.get(url)
    assert resp.status_code == 200, (
        f"{url} returned {resp.status_code}: {resp.text[:300]!r}"
    )
    body = resp.text
    match = _ARTICLE_RE.search(body) or _MAIN_RE.search(body)
    assert match, f"{url} response is missing an <article>/<main> block"
    article_text = _strip_html(match.group(1))
    ratio = _latin_ratio(article_text)
    assert ratio < 0.05, (
        f"{url} <article> body has {ratio:.1%} Latin chars (>=5%). "
        f"Sample: {article_text[:400]!r}"
    )
