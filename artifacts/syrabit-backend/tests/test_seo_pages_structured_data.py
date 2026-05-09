"""Task #11 — structured-data + golden snapshots for ``routes/seo_pages``.

Covers the contract in `.local/tasks/task-11.md`:

* All 7 canonical page-types render and round-trip the URL pattern
  ``/board/{board}/class/{class}/subject/{subject}/chapter/{chapter}/{type}``.
* Every page emits H1 = chapter topic, ≥1 H2, meta description ≤ 160c,
  hreflang as-IN + en-IN, geo IN-AS, JSON-LD (LearningResource + Course
  + FAQPage) and a 40–60 word Quick Answer block.
* ``/mcqs`` and ``/pyqs`` additionally emit a Quiz JSON-LD node.
* The chapter sitemap builder yields one entry per (chapter, page_type)
  plus the chapter root.
* The structured-data linter rejects malformed JSON / missing @context.

Runs without network: a fake ``deps.db`` is wired in via
``tests._deps_stub`` so every Mongo call is local.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from typing import Any, Dict, Iterable, List
from unittest.mock import AsyncMock, MagicMock

from tests._deps_stub import install_deps_stub  # noqa: E402

install_deps_stub()


# ── Fake Mongo plumbing (mirrors test_seo_publish_indexnow_e2e style) ──


class _FakeCursor:
    def __init__(self, docs: Iterable[dict]):
        self._docs = list(docs)

    def __aiter__(self):
        async def _gen():
            for d in self._docs:
                yield d
        return _gen()

    def sort(self, *_a, **_kw):
        return self

    def limit(self, *_a, **_kw):
        return self

    async def to_list(self, length=None):
        return list(self._docs) if length is None else list(self._docs)[:length]


class _FakeCollection:
    def __init__(self, docs: List[dict] | None = None):
        self._docs: List[dict] = list(docs or [])

    def find(self, query: dict | None = None, _proj=None):
        if not query:
            return _FakeCursor(self._docs)
        out = []
        for d in self._docs:
            ok = True
            for k, v in query.items():
                if isinstance(v, dict) and "$in" in v:
                    if d.get(k) not in v["$in"]:
                        ok = False; break
                elif d.get(k) != v:
                    ok = False; break
            if ok:
                out.append(d)
        return _FakeCursor(out)

    async def find_one(self, query: dict | None = None, _proj=None):
        if not query:
            return self._docs[0] if self._docs else None
        for d in self._docs:
            if all(d.get(k) == v for k, v in query.items()):
                return d
        return None


def _build_db() -> MagicMock:
    db = MagicMock()
    db.boards = _FakeCollection([
        {"id": "brd-1", "slug": "ahsec", "name": "AHSEC"},
    ])
    db.classes = _FakeCollection([
        {"id": "cls-11", "slug": "class-11", "name": "Class 11", "board_id": "brd-1"},
    ])
    db.streams = _FakeCollection([
        {"id": "str-sci", "class_id": "cls-11"},
    ])
    db.subjects = _FakeCollection([
        {"id": "sub-bio", "slug": "biology", "name": "Biology",
         "stream_id": "str-sci", "class_id": "cls-11", "board_id": "brd-1",
         "status": "published"},
    ])
    db.chapters = _FakeCollection([
        {"id": "ch-1", "slug": "photosynthesis", "subject_id": "sub-bio",
         "title": "Photosynthesis",
         "description": "Photosynthesis converts light to chemical energy in green plants.",
         "updated_at": "2026-04-30T00:00:00Z",
         "content_as": "ফটোসিন্থেসিস প্ৰক্ৰিয়া।"},
    ])
    db.topics = _FakeCollection([
        {"chapter_id": "ch-1", "slug": "light-reactions",
         "title": "Light reactions", "summary": "ATP + NADPH formation.",
         "status": "published", "order": 1},
        {"chapter_id": "ch-1", "slug": "calvin-cycle",
         "title": "Calvin cycle", "summary": "CO2 fixation pathway.",
         "status": "published", "order": 2},
    ])
    db.aeo_quick_answers = _FakeCollection([])
    db.aeo_faq_entries = _FakeCollection([])
    return db


def _patch_deps(monkeypatch):
    db = _build_db()
    import deps as _deps
    monkeypatch.setattr(_deps, "db", db, raising=False)
    # routes.seo_pages binds `db` at import time
    if "routes.seo_pages" in sys.modules:
        del sys.modules["routes.seo_pages"]
    import routes.seo_pages as sp
    monkeypatch.setattr(sp, "db", db, raising=False)
    return sp, db


def _run(coro):
    return asyncio.run(coro)


# ── Structured-data linter ─────────────────────────────────────────────────


def _extract_jsonld(html: str) -> List[Dict[str, Any]]:
    """Return every `<script type="application/ld+json">` block."""
    out: List[Dict[str, Any]] = []
    for m in re.finditer(
        r'<script type="application/ld\+json">(.*?)</script>',
        html, flags=re.DOTALL,
    ):
        body = m.group(1).strip()
        try:
            out.append(json.loads(body))
        except json.JSONDecodeError as exc:
            raise AssertionError(
                f"structured-data linter: invalid JSON-LD: {exc}\n{body[:400]}"
            )
    return out


def _lint_jsonld(blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Mini Google-style structured-data linter.

    Asserts: each block carries ``@context``; every node in ``@graph``
    has an ``@type``; LearningResource declares ``inLanguage`` +
    ``learningResourceType``; FAQPage carries ≥ 1 Question with an
    accepted Answer; BreadcrumbList items are sequentially numbered
    starting at 1.
    """
    types: List[str] = []
    for blk in blocks:
        assert blk.get("@context") == "https://schema.org", \
            f"missing @context on JSON-LD: {blk!r}"
        graph = blk.get("@graph") or [blk]
        for node in graph:
            t = node.get("@type")
            assert t, f"JSON-LD node missing @type: {node!r}"
            types.append(t)
            if t == "LearningResource":
                assert node.get("inLanguage"), "LearningResource needs inLanguage"
                assert node.get("learningResourceType"), \
                    "LearningResource needs learningResourceType"
            if t == "FAQPage":
                qs = node.get("mainEntity") or []
                assert qs, "FAQPage needs ≥1 Question"
                for q in qs:
                    assert q.get("@type") == "Question"
                    ans = (q.get("acceptedAnswer") or {})
                    assert ans.get("@type") == "Answer" and ans.get("text")
            if t == "BreadcrumbList":
                items = node.get("itemListElement") or []
                positions = [int(i.get("position", 0)) for i in items]
                assert positions == list(range(1, len(positions) + 1)), \
                    f"BreadcrumbList positions not 1..N: {positions}"
    return {"types": types}


# ── Tests ─────────────────────────────────────────────────────────────────


def test_all_seven_page_types_render(monkeypatch):
    sp, _db = _patch_deps(monkeypatch)
    assert sp.list_seo_page_types() == [
        "notes", "mcqs", "flashcards", "pyqs",
        "summary", "definitions", "revision",
    ]
    for pt in sp.list_seo_page_types():
        resp = _run(sp.render_seo_page(
            board="ahsec", class_slug="class-11", subject_slug="biology",
            chapter_slug="photosynthesis", page_type=pt,
        ))
        body = resp.body.decode("utf-8")
        # H1 = chapter topic
        h1 = re.search(r"<h1>(.*?)</h1>", body, flags=re.DOTALL)
        assert h1, f"{pt}: missing <h1>"
        assert "Photosynthesis" in h1.group(1), \
            f"{pt}: H1 should contain chapter topic, got {h1.group(1)!r}"
        # ≥1 H2 from sub-topics
        h2s = re.findall(r"<h2>(.*?)</h2>", body)
        assert h2s, f"{pt}: missing <h2> sub-topic blocks"
        # meta description ≤ 160 chars
        md = re.search(
            r'<meta name="description" content="([^"]*)"', body,
        )
        assert md and len(md.group(1)) <= 160, \
            f"{pt}: meta description missing or > 160 chars"
        # hreflang as-IN + en-IN
        assert 'hreflang="en-IN"' in body and 'hreflang="as-IN"' in body, \
            f"{pt}: missing en-IN/as-IN hreflang"
        # geo IN-AS
        assert 'name="geo.region" content="IN-AS"' in body
        assert 'name="geo.placename"' in body
        # JSON-LD blocks
        blocks = _extract_jsonld(body)
        assert blocks, f"{pt}: no JSON-LD blocks"
        info = _lint_jsonld(blocks)
        # LearningResource + Course + BreadcrumbList + FAQPage on every type
        for required in ("LearningResource", "Course",
                         "BreadcrumbList", "FAQPage"):
            assert required in info["types"], \
                f"{pt}: missing JSON-LD @type {required}"
        # Quiz only on /mcqs and /pyqs
        if pt in ("mcqs", "pyqs"):
            assert "Quiz" in info["types"], f"{pt}: Quiz JSON-LD required"
        else:
            assert "Quiz" not in info["types"], \
                f"{pt}: Quiz JSON-LD must NOT be present"
        # Quick-Answer block 40–60 words
        qa = re.search(
            r'data-aeo-block="1"[^>]*>\s*<p>(.*?)</p>',
            body, flags=re.DOTALL,
        )
        assert qa, f"{pt}: missing Quick-Answer block"
        words = qa.group(1).split()
        assert 40 <= len(words) <= 60, \
            f"{pt}: Quick-Answer must be 40-60 words, got {len(words)}"


def test_faq_accordion_renders_when_materialised(monkeypatch):
    """Task #12 — when ``aeo_faq_entries`` carries materialised pairs,
    the chapter template MUST emit a visible ``<details>`` accordion in
    addition to the FAQPage JSON-LD block. Earlier the renderer fed
    the entries only into JSON-LD, so the visible-FAQ contract from
    task-12.md step 4 went silently unfulfilled."""
    sp, fake_db = _patch_deps(monkeypatch)
    fake_db.aeo_faq_entries = _FakeCollection([
        {"chapter_id": "ch-1", "page_type": "notes",
         "question": "What is photosynthesis?",
         "answer":   "Photosynthesis is the process by which green "
                     "plants convert light energy into chemical "
                     "energy stored in glucose.",
         "position": 0},
        {"chapter_id": "ch-1", "page_type": "notes",
         "question": "Where do the light reactions take place?",
         "answer":   "The light reactions take place in the thylakoid "
                     "membranes of the chloroplast.",
         "position": 1},
    ])
    resp = _run(sp.render_seo_page(
        board="ahsec", class_slug="class-11", subject_slug="biology",
        chapter_slug="photosynthesis", page_type="notes",
    ))
    body = resp.body.decode("utf-8")
    assert 'data-aeo-block="faq"' in body, \
        "visible FAQ section is missing from the rendered HTML"
    detail_blocks = re.findall(
        r'<details class="faq-entry">.*?</details>', body, flags=re.DOTALL,
    )
    assert len(detail_blocks) == 2, (
        f"expected one <details> per materialised FAQ pair (got {len(detail_blocks)})"
    )
    assert "What is photosynthesis?" in body
    assert "Where do the light reactions take place?" in body
    # FAQPage JSON-LD must reference the same materialised questions.
    blocks = _extract_jsonld(body)
    faq_qs = []
    for blk in blocks:
        for node in (blk.get("@graph") or [blk]):
            if node.get("@type") == "FAQPage":
                faq_qs.extend(q.get("name") for q in node.get("mainEntity") or [])
    assert "What is photosynthesis?" in faq_qs
    assert "Where do the light reactions take place?" in faq_qs


def test_unknown_page_type_404s(monkeypatch):
    sp, _ = _patch_deps(monkeypatch)
    from fastapi import HTTPException
    try:
        _run(sp.render_seo_page(
            board="ahsec", class_slug="class-11", subject_slug="biology",
            chapter_slug="photosynthesis", page_type="bogus",
        ))
        assert False, "expected HTTPException for unknown page_type"
    except HTTPException as exc:
        assert exc.status_code == 404


def test_unknown_chapter_404s(monkeypatch):
    sp, _ = _patch_deps(monkeypatch)
    from fastapi import HTTPException
    try:
        _run(sp.render_seo_page(
            board="ahsec", class_slug="class-11", subject_slug="biology",
            chapter_slug="does-not-exist", page_type="notes",
        ))
        assert False, "expected HTTPException for missing chapter"
    except HTTPException as exc:
        assert exc.status_code == 404


def test_chapter_sitemap_builds_seven_per_chapter(monkeypatch):
    sp, _db = _patch_deps(monkeypatch)
    entries = _run(sp.build_chapter_sitemap_entries())
    # 1 chapter root + 7 page-types = 8 entries per chapter.
    assert len(entries) == 8, f"expected 8 entries, got {len(entries)}: {entries}"
    locs = [e["loc"] for e in entries]
    base = ("https://syrabit.ai/board/ahsec/class/class-11/subject/biology"
            "/chapter/photosynthesis")
    assert base in locs, "chapter root URL missing from sitemap"
    for pt in sp.list_seo_page_types():
        assert f"{base}/{pt}" in locs, f"sitemap missing {pt} entry"


def test_golden_snapshot_notes(monkeypatch):
    """Golden snapshot of the /notes render — guards against silent
    drift in the H1 / Quick-Answer / hreflang / geo skeleton."""
    sp, _ = _patch_deps(monkeypatch)
    resp = _run(sp.render_seo_page(
        board="ahsec", class_slug="class-11", subject_slug="biology",
        chapter_slug="photosynthesis", page_type="notes",
    ))
    body = resp.body.decode("utf-8")
    # Snapshot tokens — every one of these must remain stable.
    expected_tokens = [
        '<title>Photosynthesis — Class 11 Biology AHSEC notes | Syrabit.ai</title>',
        '<link rel="canonical" href="https://syrabit.ai/board/ahsec/class/class-11/subject/biology/chapter/photosynthesis/notes"/>',
        '<link rel="alternate" hreflang="en-IN" href="https://syrabit.ai/board/ahsec/class/class-11/subject/biology/chapter/photosynthesis/notes"/>',
        '<link rel="alternate" hreflang="as-IN" href="https://syrabit.ai/as/board/ahsec/class/class-11/subject/biology/chapter/photosynthesis/notes"',
        '<meta name="geo.region" content="IN-AS"/>',
        '<meta name="geo.placename" content="Assam"/>',
        '<h1>Photosynthesis — Class 11 Biology AHSEC notes</h1>',
        '<h2>Light reactions</h2>',
        '<h2>Calvin cycle</h2>',
        'data-aeo-block="1"',
    ]
    for tok in expected_tokens:
        assert tok in body, f"golden snapshot drift: missing {tok!r}"


def test_quick_answer_uses_materialised_when_present(monkeypatch):
    """When Task #12's ``aeo_quick_answers`` row exists, the renderer
    must surface it verbatim instead of the deterministic stub."""
    sp, db = _patch_deps(monkeypatch)
    materialised = (
        "Photosynthesis is the process by which green plants and certain "
        "other organisms transform light energy into chemical energy stored "
        "in glucose. It happens in two stages — light-dependent reactions "
        "in the thylakoids and the Calvin cycle in the stroma — and is the "
        "primary entry point of energy into Earth's biosphere for AHSEC "
        "Class 11 Biology students."
    )
    db.aeo_quick_answers = _FakeCollection([
        {"chapter_id": "ch-1", "page_type": "notes", "answer": materialised},
    ])
    if "routes.seo_pages" in sys.modules:
        sys.modules["routes.seo_pages"].db = db
    resp = _run(sp.render_seo_page(
        board="ahsec", class_slug="class-11", subject_slug="biology",
        chapter_slug="photosynthesis", page_type="notes",
    ))
    body = resp.body.decode("utf-8")
    # Jinja2's autoescape uses markupsafe, which encodes apostrophes as
    # ``&#39;`` (vs. ``html.escape`` which emits ``&#x27;``). Either is
    # valid HTML; assert against the markupsafe form the renderer
    # actually produces.
    from markupsafe import escape as _ms_escape
    assert str(_ms_escape(materialised)) in body, \
        "renderer must surface the materialised AEO answer card verbatim"


def test_seo_routes_mounted_at_canonical_path_not_under_api(monkeypatch):
    """Task #11 spec: public SEO pages MUST be served at the canonical
    ``/board/{board}/class/{class}/subject/{subject}/chapter/{chapter}/{type}``
    URL — NOT under the ``/api`` JSON prefix. The canonical link
    elements + sitemap fan-out are emitted at this exact path, so any
    accidental re-mount under ``/api`` would point Google at 404s."""
    sp, db = _patch_deps(monkeypatch)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(sp.router)
    client = TestClient(app)

    canonical_path = (
        "/board/ahsec/class/class-11/subject/biology"
        "/chapter/photosynthesis/notes"
    )
    resp = client.get(canonical_path)
    assert resp.status_code == 200, (
        f"canonical SEO path must serve 200 at {canonical_path}, "
        f"got {resp.status_code}"
    )
    assert "<h1>" in resp.text and "Photosynthesis" in resp.text

    # And the same path under /api MUST NOT be a SEO render.
    resp_api = client.get("/api" + canonical_path)
    assert resp_api.status_code == 404, (
        "SEO pages must NOT be reachable under /api — that prefix is "
        "reserved for JSON routes and would split crawl signal"
    )


def test_indexnow_endpoints_include_yandex_and_central():
    """Yandex IndexNow + central api.indexnow.org are wired alongside
    Bing — the only IndexNow endpoint Task #11 explicitly requires
    in addition to Google Indexing API (handled separately)."""
    from routes.bot_discovery import INDEXNOW_ENDPOINTS
    eps = " ".join(INDEXNOW_ENDPOINTS)
    assert "yandex.com/indexnow" in eps, "Yandex IndexNow endpoint missing"
    assert "bing.com/indexnow" in eps, "Bing IndexNow endpoint missing"
    assert "api.indexnow.org" in eps, "central IndexNow endpoint missing"


def test_resolver_rejects_subject_slug_under_wrong_class(monkeypatch):
    """A subject slug shared across two classes (e.g. ``biology``
    under both Class 11 and Class 12) must resolve to the row whose
    class chain matches the URL, not the first row Mongo returns.

    Regression guard: an earlier draft pulled the first published
    subject by slug regardless of class, which would have served the
    wrong chapter under a duplicate-slug split."""
    sp, db = _patch_deps(monkeypatch)
    db.classes = _FakeCollection([
        {"id": "cls-11", "slug": "class-11", "name": "Class 11", "board_id": "brd-1"},
        {"id": "cls-12", "slug": "class-12", "name": "Class 12", "board_id": "brd-1"},
    ])
    db.subjects = _FakeCollection([
        # Same slug under two different classes — only the Class 12 row
        # has a matching chapter for this test, so resolution under
        # /class-11/biology/photosynthesis must 404 (chapter not in chain),
        # while /class-12/... must succeed.
        {"id": "sub-bio-12", "slug": "biology", "name": "Biology",
         "class_id": "cls-12", "board_id": "brd-1", "status": "published"},
        {"id": "sub-bio-11", "slug": "biology", "name": "Biology",
         "class_id": "cls-11", "board_id": "brd-1", "status": "published"},
    ])
    db.chapters = _FakeCollection([
        {"id": "ch-12", "slug": "photosynthesis", "subject_id": "sub-bio-12",
         "title": "Photosynthesis (Class 12)",
         "description": "Class 12 build of the photosynthesis chapter.",
         "updated_at": "2026-04-30T00:00:00Z", "content_as": ""},
    ])
    if "routes.seo_pages" in sys.modules:
        sys.modules["routes.seo_pages"].db = db
    # Class 11 must 404 — the only `photosynthesis` chapter is under sub-bio-12.
    from fastapi import HTTPException
    try:
        _run(sp.render_seo_page(
            board="ahsec", class_slug="class-11", subject_slug="biology",
            chapter_slug="photosynthesis", page_type="notes",
        ))
        assert False, "must not resolve duplicate-slug subject under wrong class"
    except HTTPException as exc:
        assert exc.status_code == 404
    # Class 12 must succeed.
    resp = _run(sp.render_seo_page(
        board="ahsec", class_slug="class-12", subject_slug="biology",
        chapter_slug="photosynthesis", page_type="notes",
    ))
    body = resp.body.decode("utf-8")
    assert "Photosynthesis (Class 12)" in body


def test_hreflang_always_emits_both_languages(monkeypatch):
    """Task #11 spec: every rendered page MUST advertise both
    ``as-IN`` and ``en-IN`` hreflang alternates unconditionally — even
    when the Assamese chapter body is still in the Task #12 backlog.

    The Assamese sibling URL lives at ``/as/<path>`` and the SPA
    serves bilingual chrome until the body translation lands; this
    matches how Google's hreflang validator expects locale variants
    to be advertised at the URL level rather than gated on body
    materialisation."""
    sp, db = _patch_deps(monkeypatch)
    # Strip the Assamese body from the chapter fixture so we exercise
    # the "no translation yet" branch.
    db.chapters = _FakeCollection([
        {"id": "ch-1", "slug": "photosynthesis", "subject_id": "sub-bio",
         "title": "Photosynthesis",
         "description": "Photosynthesis converts light to chemical energy.",
         "updated_at": "2026-04-30T00:00:00Z",
         "content_as": ""},
    ])
    if "routes.seo_pages" in sys.modules:
        sys.modules["routes.seo_pages"].db = db
    resp = _run(sp.render_seo_page(
        board="ahsec", class_slug="class-11", subject_slug="biology",
        chapter_slug="photosynthesis", page_type="notes",
    ))
    body = resp.body.decode("utf-8")
    assert 'hreflang="en-IN"' in body, "en-IN hreflang missing"
    assert 'hreflang="as-IN"' in body, \
        "as-IN hreflang must be emitted unconditionally per Task #11 spec"
    assert 'hreflang="x-default"' in body, "x-default hreflang missing"
    # The Assamese alternate must point at the /as/ sibling URL.
    assert ('hreflang="as-IN" href="https://syrabit.ai/as/board/ahsec/'
            in body), "as-IN must link to /as/ sibling path"
    # Internal translation-backlog state must NOT leak into the public HTML.
    assert "data-content-as" not in body


def test_meta_description_always_includes_chapter_topic(monkeypatch):
    """Task #11 spec: <meta name="description"> MUST include the
    chapter topic. Many production chapter rows store a generic
    description that does not name the topic — the renderer is
    responsible for prepending it before truncation."""
    sp, db = _patch_deps(monkeypatch)
    db.chapters = _FakeCollection([
        {"id": "ch-1", "slug": "photosynthesis", "subject_id": "sub-bio",
         "title": "Photosynthesis",
         # Description deliberately omits the chapter title.
         "description": "A high-yield exam chapter covering chloroplast biology.",
         "updated_at": "2026-04-30T00:00:00Z", "content_as": ""},
    ])
    if "routes.seo_pages" in sys.modules:
        sys.modules["routes.seo_pages"].db = db
    resp = _run(sp.render_seo_page(
        board="ahsec", class_slug="class-11", subject_slug="biology",
        chapter_slug="photosynthesis", page_type="notes",
    ))
    body = resp.body.decode("utf-8")
    md = re.search(r'<meta name="description" content="([^"]*)"', body)
    assert md, "meta description missing"
    content = md.group(1)
    assert "Photosynthesis" in content, \
        f"meta description must include chapter topic, got: {content!r}"
    assert len(content) <= 160, f"meta description > 160 chars: {len(content)}"


def test_jsonld_does_not_break_out_of_script_tag(monkeypatch):
    """Security: DB-backed FAQ / chapter strings containing
    ``</script>`` (or any ``</…>``) MUST NOT break out of the
    surrounding ``<script type=\"application/ld+json\">`` block.

    Without escaping, a malicious chapter title like
    ``<script>alert(1)</script>`` would let the FAQ JSON-LD payload
    terminate the script context and inject arbitrary JS into every
    public chapter page."""
    sp, db = _patch_deps(monkeypatch)
    payload = "</script><script>alert('xss')</script>"
    db.chapters = _FakeCollection([
        {"id": "ch-1", "slug": "photosynthesis", "subject_id": "sub-bio",
         "title": f"Photosynthesis {payload}",
         "description": f"Photosynthesis chapter {payload}",
         "updated_at": "2026-04-30T00:00:00Z", "content_as": ""},
    ])
    db.aeo_faq_entries = _FakeCollection([
        {"chapter_id": "ch-1", "page_type": "notes",
         "question": f"What about {payload}?",
         "answer": f"It is {payload} dangerous.", "position": 1},
    ])
    if "routes.seo_pages" in sys.modules:
        sys.modules["routes.seo_pages"].db = db
    resp = _run(sp.render_seo_page(
        board="ahsec", class_slug="class-11", subject_slug="biology",
        chapter_slug="photosynthesis", page_type="notes",
    ))
    body = resp.body.decode("utf-8")
    # Extract every <script type="application/ld+json"> block. There
    # MUST be exactly ONE — if `</script>` leaked through, the regex
    # would terminate the first block early and find a second one.
    blocks = re.findall(
        r'<script type="application/ld\+json">(.*?)</script>',
        body, flags=re.DOTALL,
    )
    assert len(blocks) == 1, (
        f"JSON-LD script tag escape broken: found {len(blocks)} script "
        f"blocks instead of 1 — DB content broke out of <script>"
    )
    # The serialised JSON MUST still parse — even with escaped `</`
    # browsers (and the Google linter) treat the block as valid JSON.
    raw = blocks[0].replace("<\\/", "</")
    data = json.loads(raw)
    assert data.get("@context") == "https://schema.org"


def test_publish_url_set_fans_out_to_indexnow_and_google(monkeypatch):
    """Task #11 step 7 — the unified publish helper must call BOTH
    the IndexNow endpoint set (Bing + Yandex + central) and the
    Google Indexing API in a single invocation, so callers no longer
    need to manage the two pipelines separately."""
    import asyncio as _aio
    from routes import bot_discovery as bd

    indexnow_calls: list[list[str]] = []
    google_calls: list[str] = []

    async def _fake_push(urls, source="auto", target_endpoints=None):
        indexnow_calls.append(list(urls))
        return {ep: True for ep in (target_endpoints or bd.INDEXNOW_ENDPOINTS)}

    async def _fake_gpub(url, source="publish_chain"):
        google_calls.append(url)
        return True

    monkeypatch.setattr(bd, "push_indexnow", _fake_push)
    import sys as _sys
    fake_gi = type(_sys)("google_indexing_client")
    fake_gi.publish_url = _fake_gpub
    monkeypatch.setitem(_sys.modules, "google_indexing_client", fake_gi)

    urls = [
        "https://syrabit.ai/board/ahsec/class/class-11/subject/biology/chapter/photosynthesis/notes",
        "https://syrabit.ai/board/ahsec/class/class-11/subject/biology/chapter/photosynthesis/mcqs",
    ]
    result = _aio.run(bd.publish_url_set(urls, source="task11-test"))
    assert indexnow_calls == [urls], "IndexNow must receive the URL set once"
    assert google_calls == urls, "Google Indexing must be called per URL"
    assert "indexnow" in result and "google_indexing" in result
    assert all(r.get("ok") for r in result["google_indexing"])


def test_structured_data_linter_rejects_malformed():
    bad = [{"@graph": [{"name": "no-type-node"}]}]  # missing @context + @type
    try:
        _lint_jsonld(bad)
        assert False, "linter should reject missing @context"
    except AssertionError as exc:
        assert "@context" in str(exc) or "@type" in str(exc)
