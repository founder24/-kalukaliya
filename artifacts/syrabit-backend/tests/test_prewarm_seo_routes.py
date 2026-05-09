"""Task #13 — golden tests for the SEO prewarm engine.

Covers the contract in ``.local/tasks/task-13.md``:

  * ``cache_calendar.recommended_ttl_seconds`` resolves the right
    TTL for content_type / route / season combinations.
  * ``aca_jobs.prewarm_seo_routes.select_target_chapters`` unions
    the traffic + exam-look-ahead chapter sets and dedupes them.
  * ``run_prewarm`` walks every (chapter × page_type) pair, records
    successes/failures per board, computes ``success_rate`` and
    emits a stable ``db.seo_prewarm_runs`` row.

Runs without network: the HTTP client is injected as a fake.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests._deps_stub import install_deps_stub  # noqa: E402

install_deps_stub()

import cache_calendar  # noqa: E402
from aca_jobs import prewarm_seo_routes  # noqa: E402


# ─── recommended_ttl_seconds ────────────────────────────────────────────


@pytest.fixture()
def normal_calendar(tmp_path):
    cal = tmp_path / "cal.yaml"
    cal.write_text("windows: []\n")
    cache_calendar.reset_for_tests(cal)
    yield
    cache_calendar.reset_for_tests(None)


@pytest.fixture()
def exam_calendar(tmp_path):
    cal = tmp_path / "cal.yaml"
    cal.write_text(
        "windows:\n"
        "  - name: 'AHSEC Exam 2026'\n"
        "    kind: exam\n"
        "    start: '2026-05-01'\n"
        "    end:   '2026-05-31'\n"
    )
    cache_calendar.reset_for_tests(cal)
    yield
    cache_calendar.reset_for_tests(None)


def test_recommended_ttl_route_normal(normal_calendar):
    today = datetime(2026, 8, 15, tzinfo=timezone.utc)
    # SEO chapter route → normal-season prefix TTL.
    assert cache_calendar.recommended_ttl_seconds(
        route="/board/ahsec/class/11/subject/biology/chapter/photosynthesis/notes",
        today=today,
    ) == 3600


def test_recommended_ttl_route_exam_stretches(exam_calendar):
    today = datetime(2026, 5, 10, tzinfo=timezone.utc)
    assert cache_calendar.recommended_ttl_seconds(
        route="/board/ahsec/class/11/subject/biology/chapter/photosynthesis/notes",
        today=today,
    ) == 21600
    # PYQ family stretches harder (long-tail content).
    assert cache_calendar.recommended_ttl_seconds(
        route="/api/pyq/biology/2024", today=today,
    ) == 86400


def test_recommended_ttl_content_type_stretches(exam_calendar):
    today = datetime(2026, 5, 10, tzinfo=timezone.utc)
    # mcq is in EXAM_STRETCH_CONTENT_TYPES.
    assert cache_calendar.recommended_ttl_seconds(
        content_type="mcq", today=today,
    ) == cache_calendar.EXAM_TTL_SEC
    # formatter is NOT in the stretch set.
    assert cache_calendar.recommended_ttl_seconds(
        content_type="formatter", today=today,
    ) == cache_calendar.NORMAL_TTL_SEC


def test_recommended_ttl_default(normal_calendar):
    today = datetime(2026, 8, 15, tzinfo=timezone.utc)
    # No content_type, no route → falls back to deterministic-cache default.
    assert cache_calendar.recommended_ttl_seconds(today=today) > 0


# ─── prewarm engine — fakes ─────────────────────────────────────────────


class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def sort(self, *args, **kwargs):
        return self

    async def to_list(self, _n):
        return list(self._rows)


class _FakeCollection:
    def __init__(self, rows=None):
        self._rows = list(rows or [])
        self.inserted = []
        self.deleted = 0

    @staticmethod
    def _matches(row, query):
        for k, v in (query or {}).items():
            if isinstance(v, dict) and "$in" in v:
                if row.get(k) not in v["$in"]:
                    return False
            else:
                if row.get(k) != v:
                    return False
        return True

    def find(self, query=None, projection=None):
        rows = [r for r in self._rows if self._matches(r, query)]
        return _FakeCursor(rows)

    async def find_one(self, query, projection=None, sort=None):
        for r in self._rows:
            if self._matches(r, query):
                return dict(r)
        if sort and self.inserted:
            return dict(self.inserted[-1])
        return None

    def aggregate(self, pipeline):
        # Used by traffic aggregation in tests — return precomputed rows.
        return _FakeCursor(self._rows)

    async def insert_one(self, doc):
        self.inserted.append(dict(doc))

    async def delete_many(self, _q):
        self.deleted += 1

    async def distinct(self, *args, **kwargs):
        return []


def _make_db():
    """Default fixture DB: AHSEC / class-11 / biology / two chapters."""
    db = _FakeDB.__new__(_FakeDB)
    db.page_views = _FakeCollection([
        {"_id": "/board/ahsec/class/11/subject/biology/chapter/photosynthesis/", "hits": 100},
        {"_id": "/board/ahsec/class/11/subject/biology/chapter/respiration/",    "hits":  50},
    ])
    db.subjects = _FakeCollection([
        {"id": "subj-bio", "slug": "biology", "name": "Biology",
         "class_id": "cls-11", "stream_id": None, "board_id": "brd-ahsec",
         "status": "published"},
    ])
    db.streams = _FakeCollection([])
    db.classes = _FakeCollection([
        {"id": "cls-11", "slug": "11", "name": "Class 11",
         "board_id": "brd-ahsec"},
    ])
    db.boards = _FakeCollection([
        {"id": "brd-ahsec", "slug": "ahsec", "name": "AHSEC"},
    ])
    db.chapters = _FakeCollection([
        {"id": "ch-photo", "slug": "photosynthesis",
         "title": "Photosynthesis", "subject_id": "subj-bio",
         "status": "published"},
        {"id": "ch-resp",  "slug": "respiration",
         "title": "Respiration",    "subject_id": "subj-bio",
         "status": "published"},
    ])
    db.seo_prewarm_runs = _FakeCollection([])
    return db


class _FakeDB:
    """Marker class — instances are built via ``_make_db()`` so each
    test gets a freshly populated stand-in."""
    pass


class _FakeResp:
    def __init__(self, status_code):
        self.status_code = status_code


class _FakeClient:
    def __init__(self, status=200):
        self.status = status
        self.calls = []
        self.headers_seen = []
        self.methods_seen = []

    async def get(self, url, follow_redirects=True, headers=None):
        self.calls.append(url)
        self.headers_seen.append(dict(headers or {}))
        self.methods_seen.append("GET")
        sc = self.status(url) if callable(self.status) else self.status
        return _FakeResp(sc)

    async def head(self, url, follow_redirects=True, headers=None):
        self.calls.append(url)
        self.headers_seen.append(dict(headers or {}))
        self.methods_seen.append("HEAD")
        sc = self.status(url) if callable(self.status) else self.status
        return _FakeResp(sc)

    async def aclose(self):
        pass


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ─── select_target_chapters ─────────────────────────────────────────────


def test_select_target_chapters_unions_traffic_and_exam(normal_calendar):
    db = _make_db()
    chapters = _run(prewarm_seo_routes.select_target_chapters(
        db, top_n=10, exam_lookahead_days=0,
        today=datetime(2026, 8, 15, tzinfo=timezone.utc),
    ))
    assert {c["slug"] for c in chapters} == {"photosynthesis", "respiration"}


def test_select_target_chapters_distinguishes_duplicate_slugs(normal_calendar):
    """Regression: two boards both have a chapter slugged "photosynthesis"
    under their respective biology subjects. Selecting on slug ALONE
    would warm one chapter and miss the other (or warm the wrong one);
    the full-tuple resolver must keep them distinct."""
    db = _make_db()
    db.boards._rows.append(
        {"id": "brd-cbse", "slug": "cbse", "name": "CBSE"})
    db.classes._rows.append(
        {"id": "cls-11-cbse", "slug": "11", "name": "Class 11",
         "board_id": "brd-cbse"})
    db.subjects._rows.append(
        {"id": "subj-bio-cbse", "slug": "biology", "name": "Biology",
         "class_id": "cls-11-cbse", "stream_id": None,
         "board_id": "brd-cbse", "status": "published"})
    db.chapters._rows.append(
        {"id": "ch-photo-cbse", "slug": "photosynthesis",
         "title": "Photosynthesis", "subject_id": "subj-bio-cbse",
         "status": "published"})
    db.page_views._rows.append(
        {"_id": "/board/cbse/class/11/subject/biology/chapter/photosynthesis/",
         "hits": 80})

    chapters = _run(prewarm_seo_routes.select_target_chapters(
        db, top_n=10, exam_lookahead_days=0,
        today=datetime(2026, 8, 15, tzinfo=timezone.utc),
    ))
    chapter_ids = {c["id"] for c in chapters}
    # BOTH photosynthesis chapters resolved (different ids) — not collapsed
    # to a single slug.
    assert "ch-photo" in chapter_ids
    assert "ch-photo-cbse" in chapter_ids


def test_exam_lookahead_raises_on_db_failure(exam_calendar):
    """V4 §12 — DB failure in the exam leg must NOT silently shrink
    the warmed set. Today is inside the exam window so the lookahead
    must execute and propagate the failure as TrafficSelectionError."""
    db = _make_db()

    class _BoomCursor:
        def find(self, *_a, **_k):
            class _C:
                async def to_list(self, _n):
                    raise RuntimeError("subjects offline")
            return _C()

    db.subjects = _BoomCursor()
    with pytest.raises(prewarm_seo_routes.TrafficSelectionError):
        _run(prewarm_seo_routes._exam_lookahead_subject_ids(
            db, lookahead_days=60,
            today=datetime(2026, 5, 10, tzinfo=timezone.utc),
        ))


def test_resolve_chapter_for_route_propagates_db_failure(normal_calendar):
    """V4 §12 — resolver DB failure must propagate, not silently
    return None (which would record the chapter as 'not found')."""
    db = _make_db()

    async def _boom(*_a, **_k):
        raise RuntimeError("boards offline")

    db.boards.find_one = _boom
    with pytest.raises(RuntimeError):
        _run(prewarm_seo_routes._resolve_chapter_for_route(
            db, board_slug="ahsec", class_slug="11",
            subject_slug="biology", chapter_slug="photosynthesis",
        ))


def test_top_chapters_by_traffic_raises_on_db_failure(normal_calendar):
    """V4 §12 — silent fallback would mask analytics outage."""
    db = _make_db()

    class _BoomCursor:
        async def to_list(self, _n):
            raise RuntimeError("boom")

    db.page_views.aggregate = lambda _p: _BoomCursor()
    with pytest.raises(prewarm_seo_routes.TrafficSelectionError):
        _run(prewarm_seo_routes._top_chapters_by_traffic(db, top_n=10))


# ─── run_prewarm ────────────────────────────────────────────────────────


def test_run_prewarm_warms_every_page_type(normal_calendar):
    db = _make_db()
    client = _FakeClient(status=200)
    summary = _run(prewarm_seo_routes.run_prewarm(
        db, top_n=10, concurrency=4, http_client=client,
        public_base_url="https://example.test",
        today=datetime(2026, 8, 15, tzinfo=timezone.utc),
    ))
    assert summary["scanned"] == 2
    # 7 SEO page-types + 1 FAQ JSON-LD leg per chapter × 2 chapters = 16.
    assert summary["urls_attempted"] == 16
    assert summary["urls_warmed"] == 16
    assert summary["urls_failed"] == 0
    assert summary["success_rate"] == 1.0
    assert summary["by_board"]["AHSEC"]["warmed"] == 16
    assert len(db.seo_prewarm_runs.inserted) == 1
    seo_calls = [u for u in client.calls if "/board/" in u]
    assert seo_calls and seo_calls[0].startswith(
        "https://example.test/board/ahsec/class/11/subject/biology/chapter/")
    assert seo_calls[0].rsplit("/", 1)[-1] in prewarm_seo_routes.PAGE_TYPES
    # X-Prewarm-Recommended-TTL header must be set on every request so the
    # worker can pin its tiered-cache entry to the season-aware TTL.
    assert all(
        "X-Prewarm-Recommended-TTL" in h for h in client.headers_seen
    )
    # Task #13 — two-phase warm contract: GET on KV-eligible page-
    # types (mcqs/flashcards/definitions/summary/pyqs) AND on the FAQ
    # JSON-LD leg so the deterministic-template / FAQPage renderer
    # fills KV + ai_input_cache; HEAD on edge-only legs
    # (notes/revision) which only need a tiered-cache entry.
    assert set(client.methods_seen) == {"GET", "HEAD"}
    get_urls = [u for (u, m) in zip(client.calls, client.methods_seen) if m == "GET"]
    head_urls = [u for (u, m) in zip(client.calls, client.methods_seen) if m == "HEAD"]
    # 5 KV-eligible SEO + 1 FAQ × 2 chapters = 12 GETs; 2 edge-only × 2 = 4 HEADs.
    assert len(get_urls) == 12
    assert len(head_urls) == 4
    faq_urls = [u for u in get_urls if "/content/chapters/" in u and u.endswith("/faq-jsonld")]
    seo_get_urls = [u for u in get_urls if "/board/" in u]
    assert len(faq_urls) == 2
    assert len(seo_get_urls) == 10
    for u in seo_get_urls:
        assert u.rsplit("/", 1)[-1] in prewarm_seo_routes.KV_ELIGIBLE_PAGE_TYPES
    for u in head_urls:
        assert u.rsplit("/", 1)[-1] in {"notes", "revision"}
    # KV-eligible accounting: 5 SEO page-types + 1 FAQ leg per chapter × 2.
    assert summary["kv_attempted"] == 12
    assert summary["kv_warmed"] == 12
    assert summary["kv_failed"] == 0
    assert summary["kv_success_rate"] == 1.0


def test_run_prewarm_per_page_type_ttl_diverges_in_exam_mode(exam_calendar):
    """Task #13 — `pyqs` (EXAM_STRETCH_CONTENT_TYPES → 90d) MUST stretch
    harder than `notes` (route-table → 6h) during exam mode. A
    regression here would flatten every page-type onto the catch-all
    `/board/` TTL and silently under-cache the highest-value
    materialization legs during the exam spike."""
    db = _make_db()
    client = _FakeClient(status=200)
    _run(prewarm_seo_routes.run_prewarm(
        db, top_n=10, concurrency=4, http_client=client,
        public_base_url="https://example.test",
        today=datetime(2026, 5, 10, tzinfo=timezone.utc),
    ))
    by_url = {
        u: int(h["X-Prewarm-Recommended-TTL"])
        for u, h in zip(client.calls, client.headers_seen)
    }
    pyq_ttls = {ttl for u, ttl in by_url.items() if u.endswith("/pyqs")}
    notes_ttls = {ttl for u, ttl in by_url.items() if u.endswith("/notes")}
    assert pyq_ttls == {cache_calendar.EXAM_TTL_SEC}, pyq_ttls
    # `/board/` route entry: 21600s (6h) during exam stretch.
    assert notes_ttls == {21600}, notes_ttls
    assert pyq_ttls != notes_ttls


def test_run_prewarm_emits_x_prewarm_auth_when_token_present(normal_calendar):
    """Task #13 round-3 — verify the auth header actually reaches the wire.

    The bootstrap mapping + Lambda env wiring tests pin the env-var
    plumbing, but only this test proves that ``run_prewarm(prewarm_auth=...)``
    propagates the token onto every HEAD request as ``X-Prewarm-Auth``.
    Without this header the worker's ``getPrewarmOverrideTtl`` returns
    ``null`` and silently drops the cache-calendar TTL override.
    """
    db = _make_db()
    client = _FakeClient(status=200)
    summary = _run(prewarm_seo_routes.run_prewarm(
        db, top_n=10, concurrency=4, http_client=client,
        public_base_url="https://example.test",
        today=datetime(2026, 8, 15, tzinfo=timezone.utc),
        prewarm_auth="shared-secret-xyz",
    ))
    assert summary["urls_warmed"] == 16
    assert client.headers_seen, "no requests captured"
    for h in client.headers_seen:
        assert h.get("X-Prewarm-Auth") == "shared-secret-xyz"
        assert "X-Prewarm-Recommended-TTL" in h


def test_run_prewarm_omits_x_prewarm_auth_when_token_absent(normal_calendar):
    """Token-less runs must NOT send a bogus header (worker would 401-style ignore)."""
    db = _make_db()
    client = _FakeClient(status=200)
    _run(prewarm_seo_routes.run_prewarm(
        db, top_n=10, concurrency=4, http_client=client,
        public_base_url="https://example.test",
        today=datetime(2026, 8, 15, tzinfo=timezone.utc),
    ))
    for h in client.headers_seen:
        assert "X-Prewarm-Auth" not in h


def test_run_prewarm_records_failures(normal_calendar):
    db = _make_db()
    client = _FakeClient(status=lambda url: 502 if url.endswith("/notes") else 200)
    summary = _run(prewarm_seo_routes.run_prewarm(
        db, top_n=10, concurrency=4, http_client=client,
        public_base_url="https://example.test",
        today=datetime(2026, 8, 15, tzinfo=timezone.utc),
    ))
    # 7 SEO page-types + 1 FAQ leg per chapter × 2 chapters = 16.
    assert summary["urls_attempted"] == 16
    assert summary["urls_failed"] == 2
    assert summary["urls_warmed"] == 14
    assert 0.87 < summary["success_rate"] < 0.88
    assert summary["samples_failed"]
    assert summary["samples_failed"][0]["status"] == 502
    # `notes` is NOT KV-eligible — KV success rate must remain 1.0
    # even though the combined success rate dipped. KV-attempted
    # totals 5 SEO + 1 FAQ × 2 chapters = 12 (Task #13 round-9
    # accounts the FAQ leg as KV-eligible).
    assert summary["kv_attempted"] == 12
    assert summary["kv_failed"] == 0
    assert summary["kv_success_rate"] == 1.0


def test_run_prewarm_kv_failure_isolated_to_kv_metric(normal_calendar):
    """Task #13 round-3 — `mcqs` IS KV-eligible; failures must surface
    on `kv_success_rate` so the split CW metric pages on-call when the
    materialization path degrades even if the edge-only `notes` /
    `revision` legs are healthy."""
    db = _make_db()
    client = _FakeClient(status=lambda url: 503 if url.endswith("/mcqs") else 200)
    summary = _run(prewarm_seo_routes.run_prewarm(
        db, top_n=10, concurrency=4, http_client=client,
        public_base_url="https://example.test",
        today=datetime(2026, 8, 15, tzinfo=timezone.utc),
    ))
    # KV-attempted: 5 SEO + 1 FAQ × 2 chapters = 12; mcqs failure
    # injects 2 failures (1 per chapter) so 10 warmed, 2 failed.
    assert summary["kv_attempted"] == 12
    assert summary["kv_failed"] == 2
    assert summary["kv_warmed"] == 10
    assert abs(summary["kv_success_rate"] - 10 / 12) < 1e-4
    # Failure samples must label KV-eligibility so the admin tile can
    # filter the queue by impacted layer.
    kv_samples = [s for s in summary["samples_failed"] if s.get("kv_eligible")]
    assert kv_samples and kv_samples[0]["page_type"] == "mcqs"


def test_run_prewarm_no_chapters_marks_healthy(normal_calendar):
    db = _make_db()
    db.chapters = _FakeCollection([])
    db.page_views = _FakeCollection([])
    client = _FakeClient(status=200)
    summary = _run(prewarm_seo_routes.run_prewarm(
        db, top_n=10, concurrency=4, http_client=client,
        public_base_url="https://example.test",
        today=datetime(2026, 8, 15, tzinfo=timezone.utc),
    ))
    assert summary["scanned"] == 0
    assert summary["urls_attempted"] == 0
    assert summary["success_rate"] == 1.0
    # Task #13 round-3 — no-chapters path must still publish the KV
    # split metric, otherwise the cache-kv-prewarm-success-rate-low
    # alarm (treat_missing_data=breaching) trips on quiet days.
    assert summary["kv_success_rate"] == 1.0
    assert len(db.seo_prewarm_runs.inserted) == 1


def test_run_prewarm_fails_loud_on_selection_error(normal_calendar):
    """V4 §12 — selection failure surfaces as 0.0 metric + re-raise."""
    db = _make_db()

    class _BoomCursor:
        async def to_list(self, _n):
            raise RuntimeError("mongo down")

    db.page_views.aggregate = lambda _p: _BoomCursor()
    client = _FakeClient(status=200)
    with pytest.raises(prewarm_seo_routes.TrafficSelectionError):
        _run(prewarm_seo_routes.run_prewarm(
            db, top_n=10, concurrency=4, http_client=client,
            public_base_url="https://example.test",
            today=datetime(2026, 8, 15, tzinfo=timezone.utc),
        ))
    # Persisted run row carries the failure context for the admin tile.
    assert len(db.seo_prewarm_runs.inserted) == 1
    persisted = db.seo_prewarm_runs.inserted[-1]
    assert persisted["success_rate"] == 0.0
    # Task #13 round-3 — selection failure also flips the KV split
    # metric to 0.0 so the new cache-kv-prewarm-success-rate-low alarm
    # is not silently skipped.
    assert persisted["kv_success_rate"] == 0.0
    assert "mongo down" in (persisted.get("selection_error") or "")
