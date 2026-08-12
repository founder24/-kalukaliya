"""
Task 141 — Notes editor load/save E2E tests.

Confirms four critical code paths that were audited and fixed to use
notes_en/notes_as (primary pipeline fields) instead of legacy content/content_as:

  1. GET /content/chapters returns notes_en AND the legacy `content` alias so the
     frontend onEditChapter fallback (notes_en || content_en || content) always works.
  2. PATCH /content/chapters/{id} with notes_en saves to chapter.notes_en (not content_en).
  3. PATCH /content/chapters/{id} with notes_as saves to chapter.notes_as (not content_as).
  4. Legacy chapter (content_en only, no notes_en) still shows content via the
     API's legacy `content` alias.

Frontend paths that mirror these (onEditChapter fallback, _contentField() routing,
handleTranslateToAssamese storing to notes_as) are confirmed correct by static
review — they are simple one-liners whose logic cannot diverge from the field names.
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import datetime, timezone

import jwt
from fastapi.testclient import TestClient

from app.config import settings


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


@pytest.fixture
def admin_cookie():
    """Valid admin session cookie (matches require_admin_session logic)."""
    from datetime import timedelta
    expire = datetime.now(timezone.utc) + timedelta(hours=8)
    payload = {"sub": "test_admin_id", "type": "admin", "role": "admin", "exp": expire}
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return {"syrabit_admin_session": token}


def _make_chapter(
    *,
    notes_en: str = "",
    notes_as: str = "",
    content_en: str = "",
    content_as: str = "",
    content_type: str = "notes",
):
    """Build a minimal mock Chapter document."""
    ch = MagicMock()
    ch.id = MagicMock()
    ch.id.__str__ = lambda self: "507f1f77bcf86cd799439011"
    ch.title = "Test Chapter"
    ch.slug = "test-chapter"
    ch.subject_id = MagicMock()
    ch.subject_id.__str__ = lambda self: "507f1f77bcf86cd799439012"
    ch.chapter_number = 1
    ch.status = "draft"
    ch.content_type = content_type
    ch.notes_en = notes_en
    ch.notes_as = notes_as
    ch.content_en = content_en
    ch.content_as = content_as
    ch.rag_text_en = ""
    ch.rag_text_as = ""
    ch.qa_text_en = None
    ch.qa_text_as = None
    ch.qa_rag_text_en = ""
    ch.qa_rag_text_as = ""
    ch.pyq_pdf_url = ""
    ch.word_count = len((notes_en or content_en).split()) if (notes_en or content_en) else 0
    ch.notes_generated = bool(notes_en or content_en)
    ch.description = ""
    ch.title_as = ""
    ch.meta_description = ""
    ch.keywords = []
    ch.faq_jsonld = ""
    ch.published_topics = []
    ch.version = 0
    now = datetime.now(timezone.utc)
    ch.created_at = now
    ch.updated_at = now
    ch.content_saved_at = None
    ch.rag_updated_at = None
    ch.rag_indexed_at = None
    ch.published_at = None
    ch.save = AsyncMock()
    return ch


# ── 1. GET list — primary and legacy fields exposed ───────────────────────────

class TestChapterListFieldsExposed:
    """GET /content/chapters must return both notes_en (primary) and content (legacy alias)."""

    def test_list_returns_notes_en_for_ingested_chapter(self, client, admin_cookie):
        """An ingested chapter (notes_en set) exposes notes_en in the list response."""
        ch = _make_chapter(notes_en="# Ingested notes content")
        chapters_qs = MagicMock()
        chapters_qs.skip.return_value = chapters_qs
        chapters_qs.limit.return_value = chapters_qs
        chapters_qs.to_list = AsyncMock(return_value=[ch])

        with patch("app.models.content.Chapter.find", return_value=chapters_qs):
            resp = client.get(
                "/api/v1/admin/content/chapters?subject_id=507f1f77bcf86cd799439012",
                cookies=admin_cookie,
            )

        assert resp.status_code == 200
        chapters = resp.json()["chapters"]
        assert len(chapters) == 1
        assert chapters[0]["notes_en"] == "# Ingested notes content"

    def test_list_returns_content_alias_for_legacy_chapter(self, client, admin_cookie):
        """A legacy chapter (content_en only) still exposes a non-empty `content` alias."""
        ch = _make_chapter(content_en="# Legacy content only")
        chapters_qs = MagicMock()
        chapters_qs.skip.return_value = chapters_qs
        chapters_qs.limit.return_value = chapters_qs
        chapters_qs.to_list = AsyncMock(return_value=[ch])

        with patch("app.models.content.Chapter.find", return_value=chapters_qs):
            resp = client.get(
                "/api/v1/admin/content/chapters?subject_id=507f1f77bcf86cd799439012",
                cookies=admin_cookie,
            )

        assert resp.status_code == 200
        chapters = resp.json()["chapters"]
        assert len(chapters) == 1
        # notes_en is absent/empty for a legacy chapter
        assert not chapters[0]["notes_en"]
        # but `content` alias is populated — this is what onEditChapter's fallback reads
        assert chapters[0]["content"] == "# Legacy content only"

    def test_list_returns_both_notes_en_and_content_for_migrated_chapter(self, client, admin_cookie):
        """A chapter with both fields set exposes both — frontend picks notes_en first."""
        ch = _make_chapter(notes_en="# Primary notes", content_en="# Legacy notes")
        chapters_qs = MagicMock()
        chapters_qs.skip.return_value = chapters_qs
        chapters_qs.limit.return_value = chapters_qs
        chapters_qs.to_list = AsyncMock(return_value=[ch])

        with patch("app.models.content.Chapter.find", return_value=chapters_qs):
            resp = client.get(
                "/api/v1/admin/content/chapters?subject_id=507f1f77bcf86cd799439012",
                cookies=admin_cookie,
            )

        chapters = resp.json()["chapters"]
        assert chapters[0]["notes_en"] == "# Primary notes"
        assert chapters[0]["content"] == "# Legacy notes"


# ── 2. PATCH — notes_en saved to notes_en, not content_en ────────────────────

class TestChapterPatchSavesNotesEn:
    """PATCH /content/chapters/{id} must write notes_en to chapter.notes_en."""

    def test_patch_notes_en_writes_to_notes_en_field(self, client, admin_cookie):
        """Saving notes_en via PATCH populates chapter.notes_en, not content_en."""
        ch = _make_chapter(notes_en="# Old notes")

        with (
            patch("app.models.content.Chapter.get", new=AsyncMock(return_value=ch)),
            patch(
                "app.api.v1.admin_content._stamp_audit",
                new=AsyncMock(return_value=None),
            ),
        ):
            resp = client.patch(
                "/api/v1/admin/content/chapters/507f1f77bcf86cd799439011",
                json={"title": "Test Chapter", "notes_en": "# Updated notes content"},
                cookies=admin_cookie,
            )

        assert resp.status_code == 200
        # The chapter model attribute must have been updated to the new text
        assert ch.notes_en == "# Updated notes content"
        # content_en must NOT have been touched
        assert ch.content_en == ""
        ch.save.assert_awaited_once()

    def test_patch_notes_en_updates_notes_generated_flag(self, client, admin_cookie):
        """Saving non-empty notes_en sets notes_generated=True."""
        ch = _make_chapter()  # no notes yet

        with (
            patch("app.models.content.Chapter.get", new=AsyncMock(return_value=ch)),
            patch("app.api.v1.admin_content._stamp_audit", new=AsyncMock(return_value=None)),
        ):
            client.patch(
                "/api/v1/admin/content/chapters/507f1f77bcf86cd799439011",
                json={"title": "Test Chapter", "notes_en": "# Notes here"},
                cookies=admin_cookie,
            )

        assert ch.notes_generated is True

    def test_patch_empty_notes_en_clears_notes_generated_flag(self, client, admin_cookie):
        """Clearing notes_en sets notes_generated=False."""
        ch = _make_chapter(notes_en="# Something")

        with (
            patch("app.models.content.Chapter.get", new=AsyncMock(return_value=ch)),
            patch("app.api.v1.admin_content._stamp_audit", new=AsyncMock(return_value=None)),
        ):
            client.patch(
                "/api/v1/admin/content/chapters/507f1f77bcf86cd799439011",
                json={"title": "Test Chapter", "notes_en": ""},
                cookies=admin_cookie,
            )

        assert ch.notes_generated is False

    def test_patch_content_legacy_field_does_not_touch_notes_en(self, client, admin_cookie):
        """Patching only the legacy `content` field must not alter notes_en."""
        ch = _make_chapter(notes_en="# Primary notes already set")

        with (
            patch("app.models.content.Chapter.get", new=AsyncMock(return_value=ch)),
            patch("app.api.v1.admin_content._stamp_audit", new=AsyncMock(return_value=None)),
        ):
            client.patch(
                "/api/v1/admin/content/chapters/507f1f77bcf86cd799439011",
                json={"title": "Test Chapter", "content": "# Legacy write"},
                cookies=admin_cookie,
            )

        # notes_en must be untouched — only content_en changes
        assert ch.notes_en == "# Primary notes already set"
        assert ch.content_en == "# Legacy write"


# ── 3. PATCH — notes_as saved to notes_as, not content_as ────────────────────

class TestChapterPatchSavesNotesAs:
    """PATCH /content/chapters/{id} must write notes_as to chapter.notes_as."""

    def test_patch_notes_as_writes_to_notes_as_field(self, client, admin_cookie):
        """Saving notes_as via PATCH populates chapter.notes_as, not content_as."""
        ch = _make_chapter(notes_en="# English notes")

        with (
            patch("app.models.content.Chapter.get", new=AsyncMock(return_value=ch)),
            patch("app.api.v1.admin_content._stamp_audit", new=AsyncMock(return_value=None)),
        ):
            resp = client.patch(
                "/api/v1/admin/content/chapters/507f1f77bcf86cd799439011",
                json={"title": "Test Chapter", "notes_as": "# অসমীয়া টোকা"},
                cookies=admin_cookie,
            )

        assert resp.status_code == 200
        assert ch.notes_as == "# অসমীয়া টোকা"
        # content_as must NOT have been touched by a notes_as write
        assert ch.content_as == ""
        ch.save.assert_awaited_once()

    def test_patch_content_as_does_not_overwrite_notes_as(self, client, admin_cookie):
        """Patching only content_as must not alter notes_as."""
        ch = _make_chapter(notes_as="# Primary Assamese notes")

        with (
            patch("app.models.content.Chapter.get", new=AsyncMock(return_value=ch)),
            patch("app.api.v1.admin_content._stamp_audit", new=AsyncMock(return_value=None)),
        ):
            client.patch(
                "/api/v1/admin/content/chapters/507f1f77bcf86cd799439011",
                json={"title": "Test Chapter", "content_as": "# Legacy Assamese"},
                cookies=admin_cookie,
            )

        assert ch.notes_as == "# Primary Assamese notes"
        assert ch.content_as == "# Legacy Assamese"


# ── 4. PATCH — word count uses notes_en as source when present ───────────────

class TestWordCountSource:
    """notes_en is authoritative for word_count; content fallback used only if absent."""

    def test_patch_notes_en_updates_word_count(self, client, admin_cookie):
        ch = _make_chapter()

        with (
            patch("app.models.content.Chapter.get", new=AsyncMock(return_value=ch)),
            patch("app.api.v1.admin_content._stamp_audit", new=AsyncMock(return_value=None)),
        ):
            client.patch(
                "/api/v1/admin/content/chapters/507f1f77bcf86cd799439011",
                json={"title": "Test Chapter", "notes_en": "one two three four five"},
                cookies=admin_cookie,
            )

        assert ch.word_count == 5

    def test_patch_content_only_updates_word_count_when_no_notes_en(self, client, admin_cookie):
        """Legacy content field updates word_count only when notes_en is absent from body."""
        ch = _make_chapter()

        with (
            patch("app.models.content.Chapter.get", new=AsyncMock(return_value=ch)),
            patch("app.api.v1.admin_content._stamp_audit", new=AsyncMock(return_value=None)),
        ):
            client.patch(
                "/api/v1/admin/content/chapters/507f1f77bcf86cd799439011",
                json={"title": "Test Chapter", "content": "alpha beta gamma"},
                cookies=admin_cookie,
            )

        assert ch.word_count == 3

    def test_patch_notes_en_takes_precedence_over_content_for_word_count(self, client, admin_cookie):
        """When both notes_en and content are sent, notes_en wins for word_count."""
        ch = _make_chapter()

        with (
            patch("app.models.content.Chapter.get", new=AsyncMock(return_value=ch)),
            patch("app.api.v1.admin_content._stamp_audit", new=AsyncMock(return_value=None)),
        ):
            client.patch(
                "/api/v1/admin/content/chapters/507f1f77bcf86cd799439011",
                json={
                    "title": "Test Chapter",
                    "notes_en": "a b c d e f g h i j",  # 10 words
                    "content": "x y z",                  # 3 words
                },
                cookies=admin_cookie,
            )

        assert ch.word_count == 10


# ── 5. Optimistic locking still works with notes_en saves ────────────────────

class TestOptimisticLockingWithNotes:
    """version conflict must be detected even when notes_en is being saved."""

    def test_version_conflict_rejects_notes_en_save(self, client, admin_cookie):
        ch = _make_chapter(notes_en="# Old")
        ch.version = 5  # server is at version 5

        with patch("app.models.content.Chapter.get", new=AsyncMock(return_value=ch)):
            resp = client.patch(
                "/api/v1/admin/content/chapters/507f1f77bcf86cd799439011",
                json={"title": "Test Chapter", "notes_en": "# New", "version": 3},  # client at 3
                cookies=admin_cookie,
            )

        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["code"] == "version_conflict"
        # notes_en must NOT have been mutated
        assert ch.notes_en == "# Old"
        ch.save.assert_not_awaited()

    def test_omitting_version_bypasses_lock(self, client, admin_cookie):
        """No `version` field in body → force-write, no conflict check."""
        ch = _make_chapter(notes_en="# Old")
        ch.version = 99

        with (
            patch("app.models.content.Chapter.get", new=AsyncMock(return_value=ch)),
            patch("app.api.v1.admin_content._stamp_audit", new=AsyncMock(return_value=None)),
        ):
            resp = client.patch(
                "/api/v1/admin/content/chapters/507f1f77bcf86cd799439011",
                json={"title": "Test Chapter", "notes_en": "# Force-written"},
                cookies=admin_cookie,
            )

        assert resp.status_code == 200
        assert ch.notes_en == "# Force-written"


# ── 6. Seed-notes bulk filter skips ingested chapters ────────────────────────

class TestSeedNotesFilterSkipsIngestedChapters:
    """POST /content/seed-notes must skip chapters that have notes_en (AHSEC pipeline)
    or content_en (legacy seed) already populated, unless force=true is passed.

    This prevents the bulk re-seed from overwriting staff-approved content with
    freshly generated AI text on every run.
    """

    def _make_chapter_qs(self, chapters):
        qs = MagicMock()
        qs.to_list = AsyncMock(return_value=chapters)
        return qs

    def test_filter_excludes_notes_en_chapters_by_default(self, client, admin_cookie):
        """Without force, the Chapter.find() filter must require both notes_en and
        content_en to be absent so AHSEC-ingested chapters are excluded."""
        chapters_qs = self._make_chapter_qs([])

        captured_filter = {}

        def _capture_find(filt=None, **kw):
            if filt:
                captured_filter.update(filt)
            return chapters_qs

        with (
            patch("app.models.content.Chapter.find", side_effect=_capture_find),
            patch("app.models.seed_run.SeedRun", MagicMock()),
        ):
            resp = client.post(
                "/api/v1/admin/content/seed-notes",
                json={"force": False},
                cookies=admin_cookie,
            )

        # Either nothing_to_do (empty chapters) or started — both are fine
        assert resp.status_code == 200

        # The critical assertion: $and must appear and cover both fields
        assert "$and" in captured_filter, "filter must use $and to check both fields"
        and_clauses = captured_filter["$and"]
        assert len(and_clauses) == 2, "must have exactly two $and branches"

        fields_checked = set()
        for clause in and_clauses:
            assert "$or" in clause
            for cond in clause["$or"]:
                fields_checked.update(cond.keys())

        assert "notes_en" in fields_checked, "notes_en absence must be checked"
        assert "content_en" in fields_checked, "content_en absence must be checked"

    def test_force_true_omits_content_filter(self, client, admin_cookie):
        """With force=true the filter must not restrict by content fields."""
        chapters_qs = self._make_chapter_qs([])

        captured_filter = {}

        def _capture_find(filt=None, **kw):
            if filt:
                captured_filter.update(filt)
            return chapters_qs

        with (
            patch("app.models.content.Chapter.find", side_effect=_capture_find),
            patch("app.models.seed_run.SeedRun", MagicMock()),
        ):
            resp = client.post(
                "/api/v1/admin/content/seed-notes",
                json={"force": True},
                cookies=admin_cookie,
            )

        assert resp.status_code == 200
        # No content-field filter at all when force=True
        assert "$and" not in captured_filter
        assert "$or" not in captured_filter


# ── 7. Seed-assamese bulk filter skips ingested chapters ─────────────────────

class TestSeedAssameseFilterSkipsIngestedChapters:
    """POST /content/seed-assamese must skip chapters that already have notes_as
    (AHSEC pipeline) OR content_as (legacy seed) populated, unless force=true.

    The classic bug: checking only content_as absence means chapters whose
    Assamese notes were written directly to notes_as (content_as=None) are
    re-queued on every bulk run, potentially overwriting staff-approved content.
    The fix uses $and to require BOTH notes_as and content_as to be absent.
    """

    def _make_chapter_qs(self, chapters):
        qs = MagicMock()
        qs.to_list = AsyncMock(return_value=chapters)
        return qs

    def test_filter_excludes_notes_as_chapters_by_default(self, client, admin_cookie):
        """Without force, the Chapter.find() filter must require both notes_as and
        content_as to be absent — so AHSEC-ingested Assamese chapters are excluded."""
        chapters_qs = self._make_chapter_qs([])

        captured_filter = {}

        def _capture_find(filt=None, **kw):
            if filt:
                captured_filter.update(filt)
            return chapters_qs

        with (
            patch("app.models.content.Chapter.find", side_effect=_capture_find),
            patch("app.models.seed_run.SeedRun", MagicMock()),
        ):
            resp = client.post(
                "/api/v1/admin/content/seed-assamese",
                json={"force": False},
                cookies=admin_cookie,
            )

        assert resp.status_code == 200

        # The critical assertion: $and must appear and cover both Assamese fields.
        # Without this guard a chapter with notes_as="…" but content_as=None would
        # match the old single-field check and be re-translated unnecessarily.
        assert "$and" in captured_filter, "filter must use $and to check both Assamese fields"
        and_clauses = captured_filter["$and"]
        assert len(and_clauses) == 2, "must have exactly two $and branches"

        fields_checked = set()
        for clause in and_clauses:
            assert "$or" in clause
            for cond in clause["$or"]:
                fields_checked.update(cond.keys())

        assert "notes_as" in fields_checked, "notes_as absence must be checked"
        assert "content_as" in fields_checked, "content_as absence must be checked"

    def test_force_true_omits_assamese_content_filter(self, client, admin_cookie):
        """With force=true the filter must not restrict by Assamese content fields."""
        chapters_qs = self._make_chapter_qs([])

        captured_filter = {}

        def _capture_find(filt=None, **kw):
            if filt:
                captured_filter.update(filt)
            return chapters_qs

        with (
            patch("app.models.content.Chapter.find", side_effect=_capture_find),
            patch("app.models.seed_run.SeedRun", MagicMock()),
        ):
            resp = client.post(
                "/api/v1/admin/content/seed-assamese",
                json={"force": True},
                cookies=admin_cookie,
            )

        assert resp.status_code == 200
        # With force=True, $and must not appear — all chapters with English content
        # are eligible regardless of whether notes_as/content_as are already set.
        assert "$and" not in captured_filter
