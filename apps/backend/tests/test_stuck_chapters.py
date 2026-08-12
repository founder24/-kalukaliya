"""
Task 154 — Confirm the stuck-chapter list clears automatically after a
provider-unavailable re-run succeeds.

Tests the JSONL deduplication logic inside admin_seed_notes_stuck():
  1. A chapter with only a notes_provider_unavailable entry appears in the list.
  2. A chapter whose latest entry is "done" (re-run succeeded) is excluded.
  3. Multiple chapters: mixed statuses are resolved correctly.
  4. An empty progress file returns an empty list.

The endpoint also reconciles against MongoDB (excludes chapters whose notes
field is now populated), so those paths are covered too.
"""

import json
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import jwt

from fastapi.testclient import TestClient


# ── helpers ───────────────────────────────────────────────────────────────────

def _rec(key, status, chapter_id="chapter-abc", medium="en", detail=""):
    return json.dumps({
        "ts": datetime.now(timezone.utc).isoformat(),
        "key": key,
        "status": status,
        "detail": detail,
        "chapter_id": chapter_id,
        "pdf_url": f"https://example.com/{key.split('|')[0].split('/')[-1]}",
        "medium": medium,
    })


def _jsonl(*records) -> str:
    return "\n".join(records)


def _mock_progress_file(content: str):
    """Return (Path-mock-chain, file-mock) for patching pathlib.Path.

    The endpoint does:
        progress_file = _Path(__file__).parent.parent.parent.parent
                        / "scripts" / ".ahsec_ingest_progress.jsonl"
    We need every / operation in the chain to eventually yield file_mock.
    """
    file_mock = MagicMock()
    file_mock.exists.return_value = True
    file_mock.read_text.return_value = content
    # Allow further / chaining on file_mock to return itself
    file_mock.__truediv__ = MagicMock(return_value=file_mock)

    chain = MagicMock()
    chain.parent = chain  # .parent chains
    chain.__truediv__ = MagicMock(return_value=file_mock)  # / returns file_mock

    return chain, file_mock


def _make_chapter_mock(notes_en="", notes_as=""):
    ch = MagicMock()
    ch.notes_en = notes_en
    ch.notes_as = notes_as
    return ch


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


@pytest.fixture
def admin_cookie():
    from app.config import settings
    expire = datetime.now(timezone.utc).replace(microsecond=0)
    from datetime import timedelta
    expire = expire.replace(second=0) + timedelta(hours=8)
    payload = {"sub": "admin-test", "type": "admin", "role": "admin", "exp": expire}
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return {"syrabit_admin_session": token}


# ── core deduplication behaviour ──────────────────────────────────────────────

class TestStuckChapterDeduplication:
    """GET /content/seed-notes/stuck must use the *latest* JSONL entry per key."""

    URL = "/api/v1/admin/content/seed-notes/stuck"

    def _get(self, client, admin_cookie, jsonl_content, chapter_mock=None):
        """Call the stuck endpoint with mocked file + DB and return the JSON body."""
        import app.api.v1.admin_content as admin_mod

        _, file_mock = _mock_progress_file(jsonl_content)

        if chapter_mock is None:
            # Default: chapter has no notes → stays stuck
            chapter_mock = _make_chapter_mock()

        with (
            patch.object(admin_mod, "_AHSEC_PROGRESS_FILE", file_mock),
            patch(
                "app.models.content.Chapter.get",
                new_callable=AsyncMock,
                return_value=chapter_mock,
            ),
        ):
            resp = client.get(self.URL, cookies=admin_cookie)

        assert resp.status_code == 200, resp.text
        return resp.json()

    def test_single_unavailable_entry_appears_in_stuck_list(self, client, admin_cookie):
        """A chapter with only a notes_provider_unavailable entry must appear."""
        jsonl = _jsonl(
            _rec("https://example.com/book.pdf|ch3", "notes_provider_unavailable",
                 chapter_id="aaa111bbb222ccc333ddd444"),
        )
        data = self._get(client, admin_cookie, jsonl)

        assert data["total"] == 1, (
            "One unresolved stuck chapter should be returned, "
            f"got total={data['total']}"
        )
        assert data["stuck"][0]["chapter_id"] == "aaa111bbb222ccc333ddd444"

    def test_done_entry_supersedes_unavailable_clears_chapter(self, client, admin_cookie):
        """After a successful re-run (done entry), the chapter must not appear.

        This is the core regression guard: the stuck list must reconcile the
        JSONL log by latest-status-per-key, not by the presence of any
        notes_provider_unavailable entry anywhere in the file.
        """
        key = "https://example.com/physics.pdf|ch5"
        jsonl = _jsonl(
            _rec(key, "notes_provider_unavailable", chapter_id="aaabbbccc111"),
            _rec(key, "done", chapter_id="aaabbbccc111"),   # re-run succeeded
        )
        # Chapter has notes now (resolved)
        chapter_mock = _make_chapter_mock(notes_en="## Motion\n\n" + "x" * 200)
        data = self._get(client, admin_cookie, jsonl, chapter_mock=chapter_mock)

        assert data["total"] == 0, (
            "A chapter whose latest progress entry is 'done' must not appear "
            f"in the stuck list — got {data['stuck']}"
        )
        assert data["stuck"] == []

    def test_done_entry_clears_even_without_notes_in_db(self, client, admin_cookie):
        """The JSONL latest-status check happens before the DB reconciliation.
        If the latest entry is 'done', the chapter is excluded regardless of
        what MongoDB says — the progress log is the ground truth for the key.
        """
        key = "https://example.com/chemistry.pdf|ch2"
        jsonl = _jsonl(
            _rec(key, "notes_provider_unavailable", chapter_id="fff999eee888"),
            _rec(key, "done", chapter_id="fff999eee888"),
        )
        # DB says notes are still empty — but latest log entry is "done"
        chapter_mock = _make_chapter_mock(notes_en="")
        data = self._get(client, admin_cookie, jsonl, chapter_mock=chapter_mock)

        assert data["total"] == 0, (
            "Latest JSONL status='done' must exclude the chapter even if DB "
            f"notes are empty — got {data['stuck']}"
        )

    def test_multiple_entries_same_key_last_wins(self, client, admin_cookie):
        """Multiple entries for the same key: the last one determines status."""
        key = "https://example.com/maths.pdf|ch7"
        jsonl = _jsonl(
            _rec(key, "done", chapter_id="ddd444eee555"),
            _rec(key, "notes_provider_unavailable", chapter_id="ddd444eee555"),
            _rec(key, "done", chapter_id="ddd444eee555"),  # final = done
        )
        data = self._get(client, admin_cookie, jsonl)
        assert data["total"] == 0, (
            "When the final entry for a key is 'done' the chapter must be excluded"
        )

    def test_mixed_chapters_correct_count(self, client, admin_cookie):
        """Two different chapter keys: one stuck, one resolved."""
        key_stuck   = "https://example.com/bio.pdf|ch1"
        key_resolved = "https://example.com/bio.pdf|ch2"
        jsonl = _jsonl(
            _rec(key_stuck,    "notes_provider_unavailable", chapter_id="stuck001"),
            _rec(key_resolved, "notes_provider_unavailable", chapter_id="resolved002"),
            _rec(key_resolved, "done",                       chapter_id="resolved002"),
        )
        # Both chapters have empty notes in DB → only JSONL status matters
        data = self._get(client, admin_cookie, jsonl)

        assert data["total"] == 1, (
            f"Expected 1 stuck chapter, got {data['total']}: {data['stuck']}"
        )
        assert data["stuck"][0]["chapter_id"] == "stuck001"

    def test_empty_file_returns_empty_list(self, client, admin_cookie):
        """An empty progress file must return an empty stuck list."""
        data = self._get(client, admin_cookie, "")
        assert data["total"] == 0
        assert data["stuck"] == []


# ── MongoDB reconciliation ────────────────────────────────────────────────────

class TestStuckChapterMongoReconciliation:
    """Even when JSONL says 'notes_provider_unavailable', the endpoint reconciles
    against MongoDB and drops chapters whose notes are now populated (>100 chars)."""

    URL = "/api/v1/admin/content/seed-notes/stuck"

    def test_chapter_with_notes_in_db_excluded(self, client, admin_cookie):
        """A stuck chapter whose notes_en is now populated (>100 chars) must be
        excluded — it was resolved by a manual edit or direct DB write."""
        key = "https://example.com/english.pdf|ch4"
        # Use a valid 24-hex ObjectId so PydanticObjectId() doesn't throw
        valid_id = "6a1f87822c1a19d2142de3d6"
        jsonl = _jsonl(
            _rec(key, "notes_provider_unavailable", chapter_id=valid_id),
        )
        # DB says notes are populated
        import app.api.v1.admin_content as admin_mod

        chapter_mock = _make_chapter_mock(notes_en="## Topic\n\n" + "x" * 200)
        _, file_mock = _mock_progress_file(jsonl)

        with (
            patch.object(admin_mod, "_AHSEC_PROGRESS_FILE", file_mock),
            patch("app.models.content.Chapter.get",
                  new_callable=AsyncMock, return_value=chapter_mock),
        ):
            resp = client.get(self.URL, cookies=admin_cookie)

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0, (
            "Chapter with notes_en >100 chars must be reconciled out of the "
            f"stuck list — got {data['stuck']}"
        )

    def test_chapter_without_notes_in_db_included(self, client, admin_cookie):
        """A stuck chapter whose notes_en is still empty must remain in the list."""
        key = "https://example.com/english.pdf|ch5"
        valid_id = "6a1f87822c1a19d2142de3d7"
        jsonl = _jsonl(
            _rec(key, "notes_provider_unavailable", chapter_id=valid_id),
        )
        import app.api.v1.admin_content as admin_mod

        chapter_mock = _make_chapter_mock(notes_en="")
        _, file_mock = _mock_progress_file(jsonl)

        with (
            patch.object(admin_mod, "_AHSEC_PROGRESS_FILE", file_mock),
            patch("app.models.content.Chapter.get",
                  new_callable=AsyncMock, return_value=chapter_mock),
        ):
            resp = client.get(self.URL, cookies=admin_cookie)

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["stuck"][0]["chapter_id"] == valid_id

    def test_missing_progress_file_returns_empty(self, client, admin_cookie):
        """When the progress file does not exist the endpoint returns an empty list."""
        import app.api.v1.admin_content as admin_mod

        file_mock = MagicMock()
        file_mock.exists.return_value = False

        with patch.object(admin_mod, "_AHSEC_PROGRESS_FILE", file_mock):
            resp = client.get(self.URL, cookies=admin_cookie)

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["file_exists"] is False


# ── retry background handler: reason token in progress log ───────────────────

class TestRetryBackgroundReason:
    """_ahsec_stuck_retry_background() must write [reason=<value>] into the
    progress log detail when a chapter still fails during a retry run.

    This mirrors the format established by process_pdf_entry() in task #156
    so staff can grep reason= consistently across both ingestion paths.
    """

    @pytest.mark.anyio
    async def test_retry_writes_reason_token_when_providers_still_unavailable(self):
        """When generate_notes() raises NotesProviderUnavailableError during a
        retry, _log_progress must be called with a detail that contains the
        [reason=<value>] token.

        This mirrors the format established by process_pdf_entry() so staff can
        grep reason= consistently across both ingestion paths.
        """
        import sys
        from pathlib import Path as _Path

        backend_root = _Path(__file__).parent.parent
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))

        from scripts.ahsec_ingest import NotesProviderUnavailableError
        import app.api.v1.admin_content as admin_mod

        # Stuck chapter record (same shape as GET /stuck returns)
        stuck_chapters = [{
            "chapter_id": "6a1f87822c1a19d2142de3d6",   # valid 24-hex ObjectId
            "key": "https://example.com/physics.pdf|ch3",
            "pdf_url": "https://example.com/physics.pdf",
            "medium": "en",
            "detail": "[reason=missing_credentials] Gemini key absent",
            "ts": "2026-01-01T00:00:00+00:00",
        }]

        logged: list[dict] = []

        def _capture_log(key, status, detail="", chapter_id="", pdf_url="", medium=""):
            logged.append({
                "key": key, "status": status, "detail": detail,
                "chapter_id": chapter_id, "pdf_url": pdf_url, "medium": medium,
            })

        # generate_notes still raises provider_error during the retry run
        unavail_err = NotesProviderUnavailableError(
            "Both providers failed on retry", reason="provider_error"
        )

        mock_subj = MagicMock()
        mock_subj.name = "Physics"

        with (
            patch("app.services.ai.sarvam_client.sarvam_client", MagicMock()),
            patch("scripts.ahsec_ingest.extract_pdf_text", new_callable=AsyncMock,
                  return_value=[{"page_num": 1, "text": "some content here " * 5}]),
            patch("scripts.ahsec_ingest.split_into_chapters", return_value=[{
                "chapter_num": 3,
                "title": "Laws of Motion",
                "body_text": "body " * 40,
                "exercises_text": "",
            }]),
            patch("scripts.ahsec_ingest.generate_notes",
                  new_callable=AsyncMock, side_effect=unavail_err),
            patch("scripts.ahsec_ingest._log_progress", side_effect=_capture_log),
            patch("app.models.content.Chapter.get",
                  new_callable=AsyncMock, return_value=_make_chapter_mock()),
            patch("app.models.content.Subject.get",
                  new_callable=AsyncMock, return_value=mock_subj),
        ):
            await admin_mod._ahsec_stuck_retry_background(MagicMock(), stuck_chapters)

        unavail_records = [r for r in logged if r["status"] == "notes_provider_unavailable"]
        assert len(unavail_records) == 1, (
            f"Expected 1 notes_provider_unavailable log record from the retry handler, "
            f"got {len(unavail_records)}. All logged: {logged}"
        )

        detail = unavail_records[0]["detail"]
        assert "reason=" in detail, (
            "Retry progress log detail must contain 'reason=<value>' so staff can "
            f"grep by root cause — got: {detail!r}"
        )
        assert "provider_error" in detail, (
            f"Expected 'provider_error' in detail, got: {detail!r}"
        )

    @pytest.mark.anyio
    async def test_retry_writes_missing_credentials_reason(self):
        """reason=missing_credentials is preserved in the retry log when Gemini
        key is absent (not just provider_error / quota issues)."""
        import sys
        from pathlib import Path as _Path

        backend_root = _Path(__file__).parent.parent
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))

        from scripts.ahsec_ingest import NotesProviderUnavailableError
        import app.api.v1.admin_content as admin_mod

        stuck_chapters = [{
            "chapter_id": "6a1f87822c1a19d2142de3d7",
            "key": "https://example.com/chemistry.pdf|ch1",
            "pdf_url": "https://example.com/chemistry.pdf",
            "medium": "en",
            "detail": "",
            "ts": "2026-01-01T00:00:00+00:00",
        }]

        logged: list[dict] = []

        def _capture_log(key, status, detail="", chapter_id="", pdf_url="", medium=""):
            logged.append({"status": status, "detail": detail})

        unavail_err = NotesProviderUnavailableError(
            "GEMINI_API_KEY is not set", reason="missing_credentials"
        )

        mock_subj = MagicMock()
        mock_subj.name = "Chemistry"

        with (
            patch("app.services.ai.sarvam_client.sarvam_client", MagicMock()),
            patch("scripts.ahsec_ingest.extract_pdf_text", new_callable=AsyncMock,
                  return_value=[{"page_num": 1, "text": "content " * 10}]),
            patch("scripts.ahsec_ingest.split_into_chapters", return_value=[{
                "chapter_num": 1, "title": "Basic Concepts",
                "body_text": "body " * 40, "exercises_text": "",
            }]),
            patch("scripts.ahsec_ingest.generate_notes",
                  new_callable=AsyncMock, side_effect=unavail_err),
            patch("scripts.ahsec_ingest._log_progress", side_effect=_capture_log),
            patch("app.models.content.Chapter.get",
                  new_callable=AsyncMock, return_value=_make_chapter_mock()),
            patch("app.models.content.Subject.get",
                  new_callable=AsyncMock, return_value=mock_subj),
        ):
            await admin_mod._ahsec_stuck_retry_background(MagicMock(), stuck_chapters)

        rec = next((r for r in logged if r["status"] == "notes_provider_unavailable"), None)
        assert rec is not None, f"No unavailable record found — logged: {logged}"
        assert "reason=missing_credentials" in rec["detail"], (
            "Retry handler must write reason=missing_credentials so staff know "
            f"to deploy the key — got: {rec['detail']!r}"
        )


# ── auto-compact after retry ──────────────────────────────────────────────────

class TestAutoCompactAfterRetry:
    """_ahsec_stuck_retry_background() must call _compact_progress_log() after
    all chapters are processed so the stuck list self-heals without a manual
    'Clear resolved' click.
    """

    @pytest.mark.anyio
    async def test_compact_called_after_successful_retry(self):
        """After a retry run writes a 'done' record, _compact_progress_log()
        is called automatically — the stuck list clears without a manual click."""
        import sys
        from pathlib import Path as _Path

        backend_root = _Path(__file__).parent.parent
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))

        from scripts.ahsec_ingest import NotesProviderUnavailableError
        import app.api.v1.admin_content as admin_mod

        stuck_chapters = [{
            "chapter_id": "6a1f87822c1a19d2142de3d6",
            "key": "https://example.com/physics.pdf|ch3",
            "pdf_url": "https://example.com/physics.pdf",
            "medium": "en",
            "detail": "",
            "ts": "2026-01-01T00:00:00+00:00",
        }]

        compact_calls: list = []

        async def _fake_compact():
            compact_calls.append(True)
            return {"compacted": True, "resolved_cleared": 1, "still_stuck": 0,
                    "records_before": 2, "records_after": 1, "file_exists": True}

        mock_subj = MagicMock()
        mock_subj.name = "Physics"

        with (
            patch("app.services.ai.sarvam_client.sarvam_client", MagicMock()),
            patch("scripts.ahsec_ingest.extract_pdf_text", new_callable=AsyncMock,
                  return_value=[{"page_num": 1, "text": "content " * 10}]),
            patch("scripts.ahsec_ingest.split_into_chapters", return_value=[{
                "chapter_num": 3, "title": "Laws of Motion",
                "body_text": "body " * 40, "exercises_text": "",
            }]),
            patch("scripts.ahsec_ingest.generate_notes",
                  new_callable=AsyncMock, return_value="## Topic\n\n" + "x" * 3000),
            patch("scripts.ahsec_ingest.save_chapter_content",
                  new_callable=AsyncMock, return_value=True),
            patch("scripts.ahsec_ingest.reindex_chapter",
                  new_callable=AsyncMock, return_value=None),
            patch("scripts.ahsec_ingest.notes_to_rag_sections", return_value=[]),
            patch("scripts.ahsec_ingest.extract_topics_from_notes", return_value=[]),
            patch("scripts.ahsec_ingest._log_progress"),
            patch("app.models.content.Chapter.get",
                  new_callable=AsyncMock, return_value=_make_chapter_mock()),
            patch("app.models.content.Subject.get",
                  new_callable=AsyncMock, return_value=mock_subj),
            patch.object(admin_mod, "_compact_progress_log",
                         side_effect=_fake_compact),
        ):
            await admin_mod._ahsec_stuck_retry_background(MagicMock(), stuck_chapters)

        assert len(compact_calls) == 1, (
            "_compact_progress_log() must be called exactly once after the retry "
            f"background task finishes — called {len(compact_calls)} time(s)"
        )

    @pytest.mark.anyio
    async def test_compact_called_when_unexpected_exception_escapes_loop(self):
        """If an unexpected exception propagates out of the processing loop,
        compaction must still fire (via the outer try/finally)."""
        import sys
        from pathlib import Path as _Path

        backend_root = _Path(__file__).parent.parent
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))

        import app.api.v1.admin_content as admin_mod

        stuck_chapters = [{
            "chapter_id": "6a1f87822c1a19d2142de3d9",
            "key": "https://example.com/chem.pdf|ch2",
            "pdf_url": "https://example.com/chem.pdf",
            "medium": "en",
            "detail": "",
            "ts": "2026-01-01T00:00:00+00:00",
        }]

        compact_calls: list = []

        async def _fake_compact():
            compact_calls.append(True)
            return {"compacted": True, "resolved_cleared": 0, "still_stuck": 1,
                    "records_before": 1, "records_after": 1, "file_exists": True}

        # extract_pdf_text raises an unexpected RuntimeError (not caught by name)
        with (
            patch("app.services.ai.sarvam_client.sarvam_client", MagicMock()),
            patch("scripts.ahsec_ingest.extract_pdf_text",
                  new_callable=AsyncMock,
                  side_effect=RuntimeError("unexpected infra error")),
            patch.object(admin_mod, "_compact_progress_log",
                         side_effect=_fake_compact),
        ):
            # The outer try/finally must catch the propagated exception AND compact
            await admin_mod._ahsec_stuck_retry_background(MagicMock(), stuck_chapters)

        assert len(compact_calls) == 1, (
            "_compact_progress_log() must fire via finally even when an unexpected "
            f"exception escapes the processing loop — called {len(compact_calls)} time(s)"
        )

    @pytest.mark.anyio
    async def test_compact_called_when_import_fails(self):
        """If the ingest module cannot be imported, compaction still runs so any
        previously-resolved entries are cleared from the log."""
        import sys
        from pathlib import Path as _Path

        backend_root = _Path(__file__).parent.parent
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))

        import app.api.v1.admin_content as admin_mod

        compact_calls: list = []

        async def _fake_compact():
            compact_calls.append(True)
            return {"compacted": False, "resolved_cleared": 0, "still_stuck": 0,
                    "records_before": 0, "records_after": 0, "file_exists": False}

        # Simulate the ingest module import failing by setting its sys.modules
        # entry to None — Python raises ImportError when it finds None there.
        import sys as _sys
        original = _sys.modules.pop("scripts.ahsec_ingest", _SENTINEL := object())
        _sys.modules["scripts.ahsec_ingest"] = None  # causes ImportError on import
        try:
            with patch.object(admin_mod, "_compact_progress_log",
                              side_effect=_fake_compact):
                await admin_mod._ahsec_stuck_retry_background(MagicMock(), [])
        finally:
            # Restore sys.modules exactly as it was
            if original is _SENTINEL:
                _sys.modules.pop("scripts.ahsec_ingest", None)
            else:
                _sys.modules["scripts.ahsec_ingest"] = original

        assert len(compact_calls) == 1, (
            "_compact_progress_log() must fire via finally even when the ingest "
            f"module import fails — called {len(compact_calls)} time(s)"
        )

    @pytest.mark.anyio
    async def test_compact_called_even_when_chapter_still_fails(self):  # noqa: E501 (kept for history)
        """Auto-compact runs after the retry even when all chapters still fail —
        it is non-destructive (stuck chapters are kept) but removes any entries
        that resolved between retry attempts."""
        import sys
        from pathlib import Path as _Path

        backend_root = _Path(__file__).parent.parent
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))

        from scripts.ahsec_ingest import NotesProviderUnavailableError
        import app.api.v1.admin_content as admin_mod

        stuck_chapters = [{
            "chapter_id": "6a1f87822c1a19d2142de3d8",
            "key": "https://example.com/bio.pdf|ch1",
            "pdf_url": "https://example.com/bio.pdf",
            "medium": "en",
            "detail": "",
            "ts": "2026-01-01T00:00:00+00:00",
        }]

        compact_calls: list = []

        async def _fake_compact():
            compact_calls.append(True)
            return {"compacted": True, "resolved_cleared": 0, "still_stuck": 1,
                    "records_before": 1, "records_after": 1, "file_exists": True}

        mock_subj = MagicMock()
        mock_subj.name = "Biology"

        unavail_err = NotesProviderUnavailableError(
            "Both failed", reason="provider_error"
        )

        with (
            patch("app.services.ai.sarvam_client.sarvam_client", MagicMock()),
            patch("scripts.ahsec_ingest.extract_pdf_text", new_callable=AsyncMock,
                  return_value=[{"page_num": 1, "text": "content " * 10}]),
            patch("scripts.ahsec_ingest.split_into_chapters", return_value=[{
                "chapter_num": 1, "title": "Cell Biology",
                "body_text": "body " * 40, "exercises_text": "",
            }]),
            patch("scripts.ahsec_ingest.generate_notes",
                  new_callable=AsyncMock, side_effect=unavail_err),
            patch("scripts.ahsec_ingest._log_progress"),
            patch("app.models.content.Chapter.get",
                  new_callable=AsyncMock, return_value=_make_chapter_mock()),
            patch("app.models.content.Subject.get",
                  new_callable=AsyncMock, return_value=mock_subj),
            patch.object(admin_mod, "_compact_progress_log",
                         side_effect=_fake_compact),
        ):
            await admin_mod._ahsec_stuck_retry_background(MagicMock(), stuck_chapters)

        assert len(compact_calls) == 1, (
            "_compact_progress_log() must be called even when all chapters still fail "
            f"— called {len(compact_calls)} time(s)"
        )


# ── Integration tests: real JSONL file content ────────────────────────────────


class TestStuckListSelfHealsAfterRetry:
    """End-to-end JSONL integration tests.

    Unlike TestAutoCompactAfterRetry (which mocks _compact_progress_log itself),
    these tests let _log_progress AND _compact_progress_log both run against a
    real temp file.  They prove that the file content is correct *after* the
    full pipeline executes — not just that compaction was invoked.
    """

    CHAPTER_ID = "5a1f87822c1a19d2142de3d6"   # 24-hex valid ObjectId
    PDF_URL    = "https://example.com/test_physics.pdf"
    KEY        = f"{PDF_URL}|ch5"

    def _stuck_record(self) -> str:
        """A single notes_provider_unavailable JSONL line for the test chapter."""
        return json.dumps({
            "ts":         "2026-01-01T00:00:00+00:00",
            "key":        self.KEY,
            "status":     "notes_provider_unavailable",
            "detail":     "both providers exhausted",
            "chapter_id": self.CHAPTER_ID,
            "pdf_url":    self.PDF_URL,
            "medium":     "en",
        }) + "\n"

    def _read_stuck(self, progress_file) -> list[dict]:
        """Return all notes_provider_unavailable records for self.KEY in the file."""
        lines = [
            l for l in progress_file.read_text(encoding="utf-8").splitlines()
            if l.strip()
        ]
        return [
            json.loads(l) for l in lines
            if json.loads(l).get("status") == "notes_provider_unavailable"
               and json.loads(l).get("key") == self.KEY
        ]

    @pytest.mark.anyio
    async def test_jsonl_entry_cleared_after_successful_retry(self, tmp_path):
        """When generate_notes() succeeds, _log_progress writes 'done', then
        _compact_progress_log prunes the old notes_provider_unavailable entry.
        The JSONL file must be empty (or have no stuck entry) afterwards."""
        import sys
        from pathlib import Path as _Path

        backend_root = _Path(__file__).parent.parent
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))

        import app.api.v1.admin_content as admin_mod
        import scripts.ahsec_ingest as ingest_mod

        progress_file = tmp_path / ".ahsec_ingest_progress.jsonl"
        lock_file     = tmp_path / ".ahsec_ingest_progress.lock"
        progress_file.write_text(self._stuck_record(), encoding="utf-8")

        stuck_chapters = [{
            "chapter_id": self.CHAPTER_ID,
            "key":        self.KEY,
            "pdf_url":    self.PDF_URL,
            "medium":     "en",
            "detail":     "",
            "ts":         "2026-01-01T00:00:00+00:00",
        }]

        mock_chapter = _make_chapter_mock(notes_en="", notes_as="")
        mock_chapter.subject_id = MagicMock()
        mock_subj = MagicMock()
        mock_subj.name = "Physics"

        with (
            patch("app.services.ai.sarvam_client.sarvam_client", MagicMock()),
            patch("scripts.ahsec_ingest.extract_pdf_text", new_callable=AsyncMock,
                  return_value=[{"page_num": 1, "text": "content " * 20}]),
            patch("scripts.ahsec_ingest.split_into_chapters", return_value=[{
                "chapter_num": 5, "title": "Thermodynamics",
                "body_text": "body " * 60, "exercises_text": "",
            }]),
            # generate_notes SUCCEEDS → _log_progress will write "done"
            patch("scripts.ahsec_ingest.generate_notes",
                  new_callable=AsyncMock,
                  return_value="## Thermodynamics\n\n" + "Notes content. " * 200),
            patch("scripts.ahsec_ingest.notes_to_rag_sections", return_value=[]),
            patch("scripts.ahsec_ingest.extract_topics_from_notes", return_value=[]),
            patch("scripts.ahsec_ingest.save_chapter_content",
                  new_callable=AsyncMock, return_value=True),
            patch("scripts.ahsec_ingest.reindex_chapter",
                  new_callable=AsyncMock, return_value=None),
            patch("app.models.content.Chapter.get",
                  new_callable=AsyncMock, return_value=mock_chapter),
            patch("app.models.content.Subject.get",
                  new_callable=AsyncMock, return_value=mock_subj),
            # Redirect _log_progress writes to the temp file
            patch.object(ingest_mod, "PROGRESS_FILE",      progress_file),
            patch.object(ingest_mod, "PROGRESS_LOCK_FILE", lock_file),
            # Redirect _compact_progress_log reads/writes to the same temp file
            patch.object(admin_mod, "_AHSEC_PROGRESS_FILE", progress_file),
            patch.object(admin_mod, "_AHSEC_PROGRESS_LOCK",  lock_file),
        ):
            await admin_mod._ahsec_stuck_retry_background(MagicMock(), stuck_chapters)

        # After a successful retry the stuck entry must be gone
        stuck_after = self._read_stuck(progress_file)
        assert stuck_after == [], (
            f"Expected no notes_provider_unavailable entry for {self.KEY!r} after a "
            f"successful retry, but file still contains: {stuck_after}"
        )

    @pytest.mark.anyio
    async def test_jsonl_entry_preserved_when_all_chapters_fail(self, tmp_path):
        """When generate_notes() raises for every chapter, _compact_progress_log
        must NOT remove the stuck entry — failed chapters remain visible so staff
        can investigate and retry again."""
        import sys
        from pathlib import Path as _Path

        backend_root = _Path(__file__).parent.parent
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))

        from scripts.ahsec_ingest import NotesProviderUnavailableError
        import app.api.v1.admin_content as admin_mod
        import scripts.ahsec_ingest as ingest_mod

        progress_file = tmp_path / ".ahsec_ingest_progress.jsonl"
        lock_file     = tmp_path / ".ahsec_ingest_progress.lock"
        progress_file.write_text(self._stuck_record(), encoding="utf-8")

        stuck_chapters = [{
            "chapter_id": self.CHAPTER_ID,
            "key":        self.KEY,
            "pdf_url":    self.PDF_URL,
            "medium":     "en",
            "detail":     "",
            "ts":         "2026-01-01T00:00:00+00:00",
        }]

        # generate_notes FAILS → _log_progress writes another notes_provider_unavailable
        unavail_err = NotesProviderUnavailableError(
            "Both providers exhausted", reason="provider_error"
        )

        mock_chapter = _make_chapter_mock(notes_en="", notes_as="")
        mock_chapter.subject_id = MagicMock()
        mock_subj = MagicMock()
        mock_subj.name = "Physics"

        with (
            patch("app.services.ai.sarvam_client.sarvam_client", MagicMock()),
            patch("scripts.ahsec_ingest.extract_pdf_text", new_callable=AsyncMock,
                  return_value=[{"page_num": 1, "text": "content " * 20}]),
            patch("scripts.ahsec_ingest.split_into_chapters", return_value=[{
                "chapter_num": 5, "title": "Thermodynamics",
                "body_text": "body " * 60, "exercises_text": "",
            }]),
            patch("scripts.ahsec_ingest.generate_notes",
                  new_callable=AsyncMock, side_effect=unavail_err),
            patch("app.models.content.Chapter.get",
                  new_callable=AsyncMock, return_value=mock_chapter),
            patch("app.models.content.Subject.get",
                  new_callable=AsyncMock, return_value=mock_subj),
            # Redirect _log_progress writes to the temp file
            patch.object(ingest_mod, "PROGRESS_FILE",      progress_file),
            patch.object(ingest_mod, "PROGRESS_LOCK_FILE", lock_file),
            # Redirect _compact_progress_log reads/writes to the same temp file
            patch.object(admin_mod, "_AHSEC_PROGRESS_FILE", progress_file),
            patch.object(admin_mod, "_AHSEC_PROGRESS_LOCK",  lock_file),
        ):
            await admin_mod._ahsec_stuck_retry_background(MagicMock(), stuck_chapters)

        # After an all-fail retry the stuck entry must still be present
        stuck_after = self._read_stuck(progress_file)
        assert len(stuck_after) == 1, (
            f"Expected the notes_provider_unavailable entry for {self.KEY!r} to survive "
            f"after an all-fail retry, but got {len(stuck_after)} entries: {stuck_after}"
        )

    @pytest.mark.anyio
    async def test_jsonl_entry_cleared_via_mongo_reconciliation_when_retry_fails(
        self, tmp_path
    ):
        """MongoDB reconciliation path in _compact_progress_log() clears a stuck
        entry even when generate_notes() still raises (retry fails).

        Scenario:
          1. A notes_provider_unavailable entry exists in the JSONL file.
          2. generate_notes() raises NotesProviderUnavailableError → the retry
             writes another notes_provider_unavailable record (still stuck in log).
          3. Chapter.get() returns a chapter whose notes_en is already populated
             (>100 chars) — a staff member edited MongoDB directly while providers
             were down.
          4. _compact_progress_log() detects the non-empty notes field and drops
             the entry from the compacted file.
          5. After the full pipeline the JSONL file must contain no stuck entry.
        """
        import sys
        from pathlib import Path as _Path

        backend_root = _Path(__file__).parent.parent
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))

        from scripts.ahsec_ingest import NotesProviderUnavailableError
        import app.api.v1.admin_content as admin_mod
        import scripts.ahsec_ingest as ingest_mod

        progress_file = tmp_path / ".ahsec_ingest_progress.jsonl"
        lock_file     = tmp_path / ".ahsec_ingest_progress.lock"
        progress_file.write_text(self._stuck_record(), encoding="utf-8")

        stuck_chapters = [{
            "chapter_id": self.CHAPTER_ID,
            "key":        self.KEY,
            "pdf_url":    self.PDF_URL,
            "medium":     "en",
            "detail":     "",
            "ts":         "2026-01-01T00:00:00+00:00",
        }]

        # generate_notes FAILS — providers are still down during the retry
        unavail_err = NotesProviderUnavailableError(
            "Both providers exhausted on retry", reason="provider_error"
        )

        # Chapter in MongoDB already has notes — staff edited it directly
        manually_fixed_chapter = _make_chapter_mock(
            notes_en="## Thermodynamics\n\n" + "Manually written content. " * 10
        )
        manually_fixed_chapter.subject_id = MagicMock()
        mock_subj = MagicMock()
        mock_subj.name = "Physics"

        with (
            patch("app.services.ai.sarvam_client.sarvam_client", MagicMock()),
            patch("scripts.ahsec_ingest.extract_pdf_text", new_callable=AsyncMock,
                  return_value=[{"page_num": 1, "text": "content " * 20}]),
            patch("scripts.ahsec_ingest.split_into_chapters", return_value=[{
                "chapter_num": 5, "title": "Thermodynamics",
                "body_text": "body " * 60, "exercises_text": "",
            }]),
            # Retry fails — providers still unavailable
            patch("scripts.ahsec_ingest.generate_notes",
                  new_callable=AsyncMock, side_effect=unavail_err),
            # Chapter in MongoDB has notes (manually fixed by staff)
            patch("app.models.content.Chapter.get",
                  new_callable=AsyncMock, return_value=manually_fixed_chapter),
            patch("app.models.content.Subject.get",
                  new_callable=AsyncMock, return_value=mock_subj),
            # Redirect _log_progress writes to the temp file
            patch.object(ingest_mod, "PROGRESS_FILE",      progress_file),
            patch.object(ingest_mod, "PROGRESS_LOCK_FILE", lock_file),
            # Redirect _compact_progress_log reads/writes to the same temp file
            patch.object(admin_mod, "_AHSEC_PROGRESS_FILE", progress_file),
            patch.object(admin_mod, "_AHSEC_PROGRESS_LOCK",  lock_file),
        ):
            await admin_mod._ahsec_stuck_retry_background(MagicMock(), stuck_chapters)

        # _compact_progress_log must have seen notes_en in MongoDB and cleared
        # the stuck entry even though generate_notes() still raised.
        stuck_after = self._read_stuck(progress_file)
        assert stuck_after == [], (
            f"Expected no notes_provider_unavailable entry for {self.KEY!r} after "
            f"_compact_progress_log reconciled MongoDB notes (manually fixed chapter), "
            f"but file still contains: {stuck_after}"
        )


# ── process_pdf_entry Assamese medium: blocked-chapter entry written ──────────

class TestProcessPdfEntryAssameseBlocked:
    """process_pdf_entry() must write a notes_provider_unavailable JSONL entry
    with medium='as' (not 'en') when both providers fail for an Assamese chapter.

    This is the upstream guard: if the medium field in the log entry is wrong
    (e.g. 'en'), the stuck-chapter reconciliation later checks the wrong
    MongoDB field and may silently miss a blocked Assamese chapter.
    """

    @pytest.mark.anyio
    async def test_blocked_assamese_entry_has_correct_medium(self):
        """When generate_notes() raises NotesProviderUnavailableError for
        medium='as', _log_progress must be called with medium='as' and
        status='notes_provider_unavailable'.
        """
        import sys
        from pathlib import Path as _Path

        backend_root = _Path(__file__).parent.parent
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))

        from scripts.ahsec_ingest import (
            NotesProviderUnavailableError,
            process_pdf_entry,
        )

        entry = {
            "subject_name": "Physics",
            "subject_slug": "physics",
            "class_level": 12,
            "medium": "as",
            "pdf_url": "https://example.com/assamese_physics.pdf",
            "part_num": 1,
            "book_label": "Physics Part I",
        }

        logged: list[dict] = []

        def _capture_log(key, status, detail="", chapter_id="", pdf_url="", medium=""):
            logged.append({
                "key": key,
                "status": status,
                "detail": detail,
                "chapter_id": chapter_id,
                "pdf_url": pdf_url,
                "medium": medium,
            })

        unavail_err = NotesProviderUnavailableError(
            "Both Sarvam and Gemini failed for Assamese chapter",
            reason="provider_error",
        )

        mock_subject = MagicMock()
        mock_subject.id = "subj001"
        mock_subject.name = "Physics"

        mock_chapter = MagicMock()
        mock_chapter.id = "6a1f87822c1a19d2142de3d6"
        mock_chapter.notes_en = ""
        mock_chapter.notes_as = ""    # no existing notes → will attempt generation

        with (
            patch("app.services.ai.sarvam_client.sarvam_client", MagicMock()),
            patch("scripts.ahsec_ingest.upsert_subject",
                  new_callable=AsyncMock, return_value=mock_subject),
            patch("scripts.ahsec_ingest.extract_pdf_text", new_callable=AsyncMock,
                  return_value=[{"page_num": 1, "text": "কিছু বিষয়বস্তু " * 10}]),
            patch("scripts.ahsec_ingest.split_into_chapters", return_value=[{
                "chapter_num": 1,
                "title": "গতিবিজ্ঞানের ভূমিকা",
                "body_text": "body " * 40,
                "exercises_text": "",
            }]),
            patch("scripts.ahsec_ingest.upsert_chapter",
                  new_callable=AsyncMock, return_value=(mock_chapter, True)),
            patch("scripts.ahsec_ingest.generate_notes",
                  new_callable=AsyncMock, side_effect=unavail_err),
            patch("scripts.ahsec_ingest._log_progress", side_effect=_capture_log),
        ):
            await process_pdf_entry(
                entry,
                MagicMock(),   # sarvam client (not used — generate_notes is mocked)
                force=True,
                dry_run=False,
                delay=0,
                done_keys=set(),
            )

        unavail_records = [r for r in logged if r["status"] == "notes_provider_unavailable"]
        assert len(unavail_records) == 1, (
            "Expected exactly 1 notes_provider_unavailable log record when both "
            f"providers fail for Assamese medium — got {len(unavail_records)}. "
            f"All logged entries: {logged}"
        )

        rec = unavail_records[0]
        assert rec["medium"] == "as", (
            "The blocked-chapter JSONL entry must carry medium='as' so the "
            "reconciliation step checks notes_as (not notes_en) later — "
            f"got medium={rec['medium']!r}"
        )
        assert rec["status"] == "notes_provider_unavailable", (
            f"Expected status='notes_provider_unavailable', got {rec['status']!r}"
        )

    @pytest.mark.anyio
    async def test_blocked_assamese_entry_medium_not_en(self):
        """Regression guard: the medium field in the JSONL entry must never be
        'en' for an Assamese ingestion run, even if the chapter has English
        content fields populated."""
        import sys
        from pathlib import Path as _Path

        backend_root = _Path(__file__).parent.parent
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))

        from scripts.ahsec_ingest import (
            NotesProviderUnavailableError,
            process_pdf_entry,
        )

        entry = {
            "subject_name": "Chemistry",
            "subject_slug": "chemistry",
            "class_level": 11,
            "medium": "as",
            "pdf_url": "https://example.com/assamese_chemistry.pdf",
            "part_num": 1,
            "book_label": "Chemistry Part I",
        }

        logged: list[dict] = []

        def _capture_log(key, status, detail="", chapter_id="", pdf_url="", medium=""):
            logged.append({"status": status, "medium": medium})

        unavail_err = NotesProviderUnavailableError(
            "Sarvam quota exceeded, Gemini key absent", reason="missing_credentials"
        )

        mock_subject = MagicMock()
        mock_subject.id = "subj002"

        # Chapter already has English notes (from a prior EN run) but no Assamese notes
        mock_chapter = MagicMock()
        mock_chapter.id = "7b2e98933d2b20e3253ef4e7"
        mock_chapter.notes_en = "## Atomic Structure\n\n" + "x" * 300  # EN already present
        mock_chapter.notes_as = ""    # AS missing → should attempt generation

        with (
            patch("app.services.ai.sarvam_client.sarvam_client", MagicMock()),
            patch("scripts.ahsec_ingest.upsert_subject",
                  new_callable=AsyncMock, return_value=mock_subject),
            patch("scripts.ahsec_ingest.extract_pdf_text", new_callable=AsyncMock,
                  return_value=[{"page_num": 1, "text": "পরমাণুর গঠন " * 10}]),
            patch("scripts.ahsec_ingest.split_into_chapters", return_value=[{
                "chapter_num": 2,
                "title": "পরমাণুর গঠন",
                "body_text": "body " * 40,
                "exercises_text": "",
            }]),
            patch("scripts.ahsec_ingest.upsert_chapter",
                  new_callable=AsyncMock, return_value=(mock_chapter, False)),
            patch("scripts.ahsec_ingest.generate_notes",
                  new_callable=AsyncMock, side_effect=unavail_err),
            patch("scripts.ahsec_ingest._log_progress", side_effect=_capture_log),
        ):
            await process_pdf_entry(
                entry,
                MagicMock(),
                force=True,
                dry_run=False,
                delay=0,
                done_keys=set(),
            )

        unavail_records = [r for r in logged if r["status"] == "notes_provider_unavailable"]
        assert len(unavail_records) >= 1, (
            f"Expected at least 1 notes_provider_unavailable entry — got: {logged}"
        )
        for rec in unavail_records:
            assert rec["medium"] != "en", (
                "Assamese ingestion must never write medium='en' in a blocked-chapter "
                f"entry — that would cause reconciliation to check the wrong field. "
                f"Got: {rec}"
            )
            assert rec["medium"] == "as", (
                f"Expected medium='as' in blocked entry, got {rec['medium']!r}"
            )


    @pytest.mark.anyio
    async def test_blocked_assamese_entry_contains_reason_token(self):
        """The detail field of the blocked-chapter entry must contain [reason=<value>]
        so staff can grep by root cause, matching the format used by the retry handler."""
        import sys
        from pathlib import Path as _Path

        backend_root = _Path(__file__).parent.parent
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))

        from scripts.ahsec_ingest import (
            NotesProviderUnavailableError,
            process_pdf_entry,
        )

        entry = {
            "subject_name": "Biology",
            "subject_slug": "biology",
            "class_level": 12,
            "medium": "as",
            "pdf_url": "https://example.com/assamese_biology.pdf",
            "part_num": 1,
            "book_label": "Biology",
        }

        logged: list[dict] = []

        def _capture_log(key, status, detail="", chapter_id="", pdf_url="", medium=""):
            logged.append({"status": status, "medium": medium, "detail": detail})

        unavail_err = NotesProviderUnavailableError(
            "All providers exhausted", reason="quota_exceeded"
        )

        mock_subject = MagicMock()
        mock_subject.id = "subj003"

        mock_chapter = MagicMock()
        mock_chapter.id = "8c3f09044e3c31f4364fg5f8"
        mock_chapter.notes_en = ""
        mock_chapter.notes_as = ""

        with (
            patch("app.services.ai.sarvam_client.sarvam_client", MagicMock()),
            patch("scripts.ahsec_ingest.upsert_subject",
                  new_callable=AsyncMock, return_value=mock_subject),
            patch("scripts.ahsec_ingest.extract_pdf_text", new_callable=AsyncMock,
                  return_value=[{"page_num": 1, "text": "জীববিজ্ঞান " * 10}]),
            patch("scripts.ahsec_ingest.split_into_chapters", return_value=[{
                "chapter_num": 1,
                "title": "কোষ বিভাজন",
                "body_text": "body " * 40,
                "exercises_text": "",
            }]),
            patch("scripts.ahsec_ingest.upsert_chapter",
                  new_callable=AsyncMock, return_value=(mock_chapter, True)),
            patch("scripts.ahsec_ingest.generate_notes",
                  new_callable=AsyncMock, side_effect=unavail_err),
            patch("scripts.ahsec_ingest._log_progress", side_effect=_capture_log),
        ):
            await process_pdf_entry(
                entry,
                MagicMock(),
                force=True,
                dry_run=False,
                delay=0,
                done_keys=set(),
            )

        unavail_records = [r for r in logged if r["status"] == "notes_provider_unavailable"]
        assert len(unavail_records) == 1, (
            f"Expected 1 blocked-chapter entry for Assamese Biology — got: {logged}"
        )

        rec = unavail_records[0]
        assert rec["medium"] == "as", (
            f"medium must be 'as', got {rec['medium']!r}"
        )
        assert "reason=" in rec["detail"], (
            "Blocked-chapter detail must contain 'reason=<value>' token — "
            f"got: {rec['detail']!r}"
        )
        assert "quota_exceeded" in rec["detail"], (
            f"Expected reason token to include 'quota_exceeded' — got: {rec['detail']!r}"
        )


    @pytest.mark.anyio
    async def test_english_run_still_writes_medium_en(self):
        """Sanity check: an English run (medium='en') that fails must still
        produce a blocked entry with medium='en', not 'as'."""
        import sys
        from pathlib import Path as _Path

        backend_root = _Path(__file__).parent.parent
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))

        from scripts.ahsec_ingest import (
            NotesProviderUnavailableError,
            process_pdf_entry,
        )

        entry = {
            "subject_name": "Mathematics",
            "subject_slug": "mathematics",
            "class_level": 11,
            "medium": "en",
            "pdf_url": "https://example.com/english_maths.pdf",
            "part_num": 1,
            "book_label": "Mathematics",
        }

        logged: list[dict] = []

        def _capture_log(key, status, detail="", chapter_id="", pdf_url="", medium=""):
            logged.append({"status": status, "medium": medium})

        unavail_err = NotesProviderUnavailableError(
            "Both providers failed", reason="provider_error"
        )

        mock_subject = MagicMock()
        mock_subject.id = "subj004"

        mock_chapter = MagicMock()
        mock_chapter.id = "9d4e10155f4d42g5475gh6g9"
        mock_chapter.notes_en = ""
        mock_chapter.notes_as = ""

        with (
            patch("app.services.ai.sarvam_client.sarvam_client", MagicMock()),
            patch("scripts.ahsec_ingest.upsert_subject",
                  new_callable=AsyncMock, return_value=mock_subject),
            patch("scripts.ahsec_ingest.extract_pdf_text", new_callable=AsyncMock,
                  return_value=[{"page_num": 1, "text": "algebra content " * 10}]),
            patch("scripts.ahsec_ingest.split_into_chapters", return_value=[{
                "chapter_num": 1,
                "title": "Sets",
                "body_text": "body " * 40,
                "exercises_text": "",
            }]),
            patch("scripts.ahsec_ingest.upsert_chapter",
                  new_callable=AsyncMock, return_value=(mock_chapter, True)),
            patch("scripts.ahsec_ingest.generate_notes",
                  new_callable=AsyncMock, side_effect=unavail_err),
            patch("scripts.ahsec_ingest._log_progress", side_effect=_capture_log),
        ):
            await process_pdf_entry(
                entry,
                MagicMock(),
                force=True,
                dry_run=False,
                delay=0,
                done_keys=set(),
            )

        unavail_records = [r for r in logged if r["status"] == "notes_provider_unavailable"]
        assert len(unavail_records) == 1, (
            f"Expected 1 blocked-chapter entry for English Mathematics — got: {logged}"
        )
        assert unavail_records[0]["medium"] == "en", (
            f"English run must write medium='en' — got {unavail_records[0]['medium']!r}"
        )


    @pytest.mark.anyio
    async def test_assamese_run_with_real_progress_file_writes_medium_as(self, tmp_path):
        """End-to-end: when process_pdf_entry() runs with the real _log_progress
        writing to a temp file, the written JSONL record must have medium='as'."""
        import sys
        from pathlib import Path as _Path

        backend_root = _Path(__file__).parent.parent
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))

        from scripts.ahsec_ingest import (
            NotesProviderUnavailableError,
            process_pdf_entry,
        )
        import scripts.ahsec_ingest as ingest_mod

        progress_file = tmp_path / ".ahsec_ingest_progress.jsonl"
        lock_file     = tmp_path / ".ahsec_ingest_progress.lock"

        entry = {
            "subject_name": "Physics",
            "subject_slug": "physics",
            "class_level": 12,
            "medium": "as",
            "pdf_url": "https://example.com/as_physics.pdf",
            "part_num": 1,
            "book_label": "Physics",
        }

        unavail_err = NotesProviderUnavailableError(
            "Sarvam 402, Gemini key absent", reason="missing_credentials"
        )

        mock_subject = MagicMock()
        mock_subject.id = "subj005"

        mock_chapter = MagicMock()
        mock_chapter.id = "aabbccddeeff001122334455"
        mock_chapter.notes_en = ""
        mock_chapter.notes_as = ""

        with (
            patch("app.services.ai.sarvam_client.sarvam_client", MagicMock()),
            patch("scripts.ahsec_ingest.upsert_subject",
                  new_callable=AsyncMock, return_value=mock_subject),
            patch("scripts.ahsec_ingest.extract_pdf_text", new_callable=AsyncMock,
                  return_value=[{"page_num": 1, "text": "পদার্থবিজ্ঞান " * 10}]),
            patch("scripts.ahsec_ingest.split_into_chapters", return_value=[{
                "chapter_num": 3,
                "title": "গতিসূত্র",
                "body_text": "body " * 40,
                "exercises_text": "",
            }]),
            patch("scripts.ahsec_ingest.upsert_chapter",
                  new_callable=AsyncMock, return_value=(mock_chapter, True)),
            patch("scripts.ahsec_ingest.generate_notes",
                  new_callable=AsyncMock, side_effect=unavail_err),
            patch.object(ingest_mod, "PROGRESS_FILE",      progress_file),
            patch.object(ingest_mod, "PROGRESS_LOCK_FILE", lock_file),
        ):
            await process_pdf_entry(
                entry,
                MagicMock(),
                force=True,
                dry_run=False,
                delay=0,
                done_keys=set(),
            )

        # Parse the real JSONL written by _log_progress
        assert progress_file.exists(), "progress file must be created by _log_progress"
        lines = [
            json.loads(l) for l in progress_file.read_text(encoding="utf-8").splitlines()
            if l.strip()
        ]
        unavail_lines = [l for l in lines if l.get("status") == "notes_provider_unavailable"]
        assert len(unavail_lines) == 1, (
            f"Expected 1 notes_provider_unavailable entry in progress file — "
            f"got {len(unavail_lines)}: {unavail_lines}"
        )
        rec = unavail_lines[0]
        assert rec["medium"] == "as", (
            "Real JSONL progress entry must have medium='as' for an Assamese run — "
            f"got medium={rec['medium']!r}. Full record: {rec}"
        )


    @pytest.mark.anyio
    async def test_assamese_run_skips_if_notes_as_already_populated(self):
        """When force=False and notes_as is already populated, process_pdf_entry()
        must skip generation and write 'done' — not notes_provider_unavailable.
        The medium='as' path must respect the early-skip guard the same way the
        medium='en' path does."""
        import sys
        from pathlib import Path as _Path

        backend_root = _Path(__file__).parent.parent
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))

        from scripts.ahsec_ingest import process_pdf_entry

        entry = {
            "subject_name": "History",
            "subject_slug": "history",
            "class_level": 11,
            "medium": "as",
            "pdf_url": "https://example.com/as_history.pdf",
            "part_num": 1,
            "book_label": "History",
        }

        logged: list[dict] = []

        def _capture_log(key, status, detail="", chapter_id="", pdf_url="", medium=""):
            logged.append({"status": status, "medium": medium})

        mock_subject = MagicMock()
        mock_subject.id = "subj006"

        # Assamese notes already exist and are >100 chars → must skip
        mock_chapter = MagicMock()
        mock_chapter.id = "ccddee112233445566778899"
        mock_chapter.notes_en = ""
        mock_chapter.notes_as = "## ইতিহাস\n\n" + "বিষয়বস্তু। " * 20

        with (
            patch("app.services.ai.sarvam_client.sarvam_client", MagicMock()),
            patch("scripts.ahsec_ingest.upsert_subject",
                  new_callable=AsyncMock, return_value=mock_subject),
            patch("scripts.ahsec_ingest.extract_pdf_text", new_callable=AsyncMock,
                  return_value=[{"page_num": 1, "text": "ইতিহাস " * 10}]),
            patch("scripts.ahsec_ingest.split_into_chapters", return_value=[{
                "chapter_num": 1,
                "title": "ভূমিকা",
                "body_text": "body " * 40,
                "exercises_text": "",
            }]),
            patch("scripts.ahsec_ingest.upsert_chapter",
                  new_callable=AsyncMock, return_value=(mock_chapter, False)),
            patch("scripts.ahsec_ingest._log_progress", side_effect=_capture_log),
        ):
            await process_pdf_entry(
                entry,
                MagicMock(),
                force=False,   # must honour the skip
                dry_run=False,
                delay=0,
                done_keys=set(),
            )

        # Must have written 'done' (skip path), not 'notes_provider_unavailable'
        unavail = [r for r in logged if r["status"] == "notes_provider_unavailable"]
        done    = [r for r in logged if r["status"] == "done"]

        assert unavail == [], (
            "When notes_as is already populated and force=False, "
            f"process_pdf_entry must skip (write 'done'), not flag as blocked — "
            f"got unexpected blocked entries: {unavail}"
        )
        assert len(done) == 1, (
            "Expected exactly 1 'done' entry for the already-populated Assamese chapter — "
            f"got: {logged}"
        )


    @pytest.mark.anyio
    async def test_assamese_run_with_force_overwrites_existing_notes(self):
        """When force=True, even a chapter with existing notes_as must attempt
        generation. If both providers fail, a blocked entry with medium='as' must
        be written — the early-skip guard must NOT fire."""
        import sys
        from pathlib import Path as _Path

        backend_root = _Path(__file__).parent.parent
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))

        from scripts.ahsec_ingest import (
            NotesProviderUnavailableError,
            process_pdf_entry,
        )

        entry = {
            "subject_name": "Geography",
            "subject_slug": "geography",
            "class_level": 12,
            "medium": "as",
            "pdf_url": "https://example.com/as_geography.pdf",
            "part_num": 1,
            "book_label": "Geography",
        }

        logged: list[dict] = []

        def _capture_log(key, status, detail="", chapter_id="", pdf_url="", medium=""):
            logged.append({"status": status, "medium": medium, "detail": detail})

        unavail_err = NotesProviderUnavailableError(
            "Network timeout on all providers", reason="provider_error"
        )

        mock_subject = MagicMock()
        mock_subject.id = "subj007"

        # Chapter already has notes_as, but force=True means we must still attempt
        mock_chapter = MagicMock()
        mock_chapter.id = "eeff334455667788990011aa"
        mock_chapter.notes_en = ""
        mock_chapter.notes_as = "## ভূগোল\n\n" + "পুরনো বিষয়বস্তু। " * 20

        with (
            patch("app.services.ai.sarvam_client.sarvam_client", MagicMock()),
            patch("scripts.ahsec_ingest.upsert_subject",
                  new_callable=AsyncMock, return_value=mock_subject),
            patch("scripts.ahsec_ingest.extract_pdf_text", new_callable=AsyncMock,
                  return_value=[{"page_num": 1, "text": "ভূগোল " * 10}]),
            patch("scripts.ahsec_ingest.split_into_chapters", return_value=[{
                "chapter_num": 4,
                "title": "জলবায়ু",
                "body_text": "body " * 40,
                "exercises_text": "",
            }]),
            patch("scripts.ahsec_ingest.upsert_chapter",
                  new_callable=AsyncMock, return_value=(mock_chapter, False)),
            patch("scripts.ahsec_ingest.generate_notes",
                  new_callable=AsyncMock, side_effect=unavail_err),
            patch("scripts.ahsec_ingest._log_progress", side_effect=_capture_log),
        ):
            await process_pdf_entry(
                entry,
                MagicMock(),
                force=True,    # must bypass the skip guard
                dry_run=False,
                delay=0,
                done_keys=set(),
            )

        unavail = [r for r in logged if r["status"] == "notes_provider_unavailable"]
        assert len(unavail) == 1, (
            "force=True must attempt generation even when notes_as is already "
            f"populated; if both providers fail, a blocked entry must be written — "
            f"got: {logged}"
        )
        assert unavail[0]["medium"] == "as", (
            f"Blocked entry must have medium='as' — got {unavail[0]['medium']!r}"
        )


    @pytest.mark.anyio
    async def test_assamese_dry_run_does_not_call_generate_notes(self):
        """dry_run=True must skip generate_notes entirely for Assamese chapters.
        No notes_provider_unavailable entry should be written — the chapter gets
        placeholder notes and proceeds to save."""
        import sys
        from pathlib import Path as _Path

        backend_root = _Path(__file__).parent.parent
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))

        from scripts.ahsec_ingest import process_pdf_entry

        entry = {
            "subject_name": "Political Science",
            "subject_slug": "political-science",
            "class_level": 11,
            "medium": "as",
            "pdf_url": "https://example.com/as_polsci.pdf",
            "part_num": 1,
            "book_label": "Political Science",
        }

        logged: list[dict] = []

        def _capture_log(key, status, detail="", chapter_id="", pdf_url="", medium=""):
            logged.append({"status": status, "medium": medium})

        mock_subject = MagicMock()
        mock_subject.id = "subj008"

        mock_chapter = MagicMock()
        mock_chapter.id = "ff00112233445566778899aa"
        mock_chapter.notes_en = ""
        mock_chapter.notes_as = ""

        with (
            patch("app.services.ai.sarvam_client.sarvam_client", MagicMock()),
            patch("scripts.ahsec_ingest.upsert_subject",
                  new_callable=AsyncMock, return_value=mock_subject),
            patch("scripts.ahsec_ingest.extract_pdf_text", new_callable=AsyncMock,
                  return_value=[{"page_num": 1, "text": "ৰাজনীতি বিজ্ঞান " * 10}]),
            patch("scripts.ahsec_ingest.split_into_chapters", return_value=[{
                "chapter_num": 1,
                "title": "ৰাজনৈতিক তত্ত্ব",
                "body_text": "body " * 40,
                "exercises_text": "",
            }]),
            patch("scripts.ahsec_ingest.upsert_chapter",
                  new_callable=AsyncMock, return_value=(mock_chapter, True)),
            patch("scripts.ahsec_ingest.notes_to_rag_sections", return_value=[]),
            patch("scripts.ahsec_ingest.extract_topics_from_notes", return_value=[]),
            patch("scripts.ahsec_ingest.save_chapter_content",
                  new_callable=AsyncMock, return_value=True),
            patch("scripts.ahsec_ingest.reindex_chapter",
                  new_callable=AsyncMock, return_value=None),
            patch("scripts.ahsec_ingest._log_progress", side_effect=_capture_log),
        ):
            await process_pdf_entry(
                entry,
                MagicMock(),
                force=True,
                dry_run=True,   # must skip generate_notes
                delay=0,
                done_keys=set(),
            )

        unavail = [r for r in logged if r["status"] == "notes_provider_unavailable"]
        assert unavail == [], (
            "dry_run=True must not trigger a blocked-chapter entry — "
            f"generate_notes is not called in dry-run mode. Got: {logged}"
        )


    @pytest.mark.anyio
    async def test_multiple_assamese_chapters_each_get_correct_medium(self):
        """When a PDF has multiple chapters and all fail, every blocked entry
        must have medium='as' — the medium value must not drift to 'en' across
        chapters within the same process_pdf_entry() call."""
        import sys
        from pathlib import Path as _Path

        backend_root = _Path(__file__).parent.parent
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))

        from scripts.ahsec_ingest import (
            NotesProviderUnavailableError,
            process_pdf_entry,
        )

        entry = {
            "subject_name": "Economics",
            "subject_slug": "economics",
            "class_level": 12,
            "medium": "as",
            "pdf_url": "https://example.com/as_economics.pdf",
            "part_num": 1,
            "book_label": "Economics",
        }

        logged: list[dict] = []

        def _capture_log(key, status, detail="", chapter_id="", pdf_url="", medium=""):
            logged.append({"status": status, "medium": medium, "key": key})

        unavail_err = NotesProviderUnavailableError(
            "Both providers failed", reason="provider_error"
        )

        mock_subject = MagicMock()
        mock_subject.id = "subj009"

        def _make_ch_mock(ch_id: str):
            m = MagicMock()
            m.id = ch_id
            m.notes_en = ""
            m.notes_as = ""
            return m

        ch_mocks = [
            _make_ch_mock("aabb001122334455667788cc"),
            _make_ch_mock("bbcc112233445566778899dd"),
            _make_ch_mock("ccdd223344556677889900ee"),
        ]
        upsert_side = [(m, True) for m in ch_mocks]

        with (
            patch("app.services.ai.sarvam_client.sarvam_client", MagicMock()),
            patch("scripts.ahsec_ingest.upsert_subject",
                  new_callable=AsyncMock, return_value=mock_subject),
            patch("scripts.ahsec_ingest.extract_pdf_text", new_callable=AsyncMock,
                  return_value=[{"page_num": 1, "text": "অৰ্থনীতি " * 10}]),
            patch("scripts.ahsec_ingest.split_into_chapters", return_value=[
                {"chapter_num": 1, "title": "ভূমিকা",     "body_text": "body " * 40, "exercises_text": ""},
                {"chapter_num": 2, "title": "চাহিদা",      "body_text": "body " * 40, "exercises_text": ""},
                {"chapter_num": 3, "title": "যোগান",       "body_text": "body " * 40, "exercises_text": ""},
            ]),
            patch("scripts.ahsec_ingest.upsert_chapter",
                  new_callable=AsyncMock, side_effect=upsert_side),
            patch("scripts.ahsec_ingest.generate_notes",
                  new_callable=AsyncMock, side_effect=unavail_err),
            patch("scripts.ahsec_ingest._log_progress", side_effect=_capture_log),
        ):
            await process_pdf_entry(
                entry,
                MagicMock(),
                force=True,
                dry_run=False,
                delay=0,
                done_keys=set(),
            )

        unavail = [r for r in logged if r["status"] == "notes_provider_unavailable"]
        assert len(unavail) == 3, (
            f"Expected 3 blocked entries (one per chapter) — got {len(unavail)}: {logged}"
        )
        for rec in unavail:
            assert rec["medium"] == "as", (
                f"All blocked entries must carry medium='as' — got {rec['medium']!r} "
                f"for key {rec.get('key')!r}"
            )


    @pytest.mark.anyio
    async def test_reason_token_is_propagated_from_error_to_log(self):
        """The [reason=<value>] token in the detail must match the reason
        attribute set on the NotesProviderUnavailableError instance, not a
        hard-coded fallback string."""
        import sys
        from pathlib import Path as _Path

        backend_root = _Path(__file__).parent.parent
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))

        from scripts.ahsec_ingest import (
            NotesProviderUnavailableError,
            process_pdf_entry,
        )

        for reason_value in ("provider_error", "missing_credentials", "quota_exceeded"):
            logged: list[dict] = []

            def _capture(key, status, detail="", chapter_id="", pdf_url="", medium="", _l=logged):
                _l.append({"status": status, "detail": detail, "medium": medium})

            entry = {
                "subject_name": "Science",
                "subject_slug": "science",
                "class_level": 11,
                "medium": "as",
                "pdf_url": f"https://example.com/as_sci_{reason_value}.pdf",
                "part_num": 1,
                "book_label": "Science",
            }

            err = NotesProviderUnavailableError(f"error for {reason_value}", reason=reason_value)

            mock_subject = MagicMock()
            mock_subject.id = "subj_r"

            mock_chapter = MagicMock()
            mock_chapter.id = "000000000000000000000001"
            mock_chapter.notes_en = ""
            mock_chapter.notes_as = ""

            with (
                patch("app.services.ai.sarvam_client.sarvam_client", MagicMock()),
                patch("scripts.ahsec_ingest.upsert_subject",
                      new_callable=AsyncMock, return_value=mock_subject),
                patch("scripts.ahsec_ingest.extract_pdf_text", new_callable=AsyncMock,
                      return_value=[{"page_num": 1, "text": "বিজ্ঞান " * 10}]),
                patch("scripts.ahsec_ingest.split_into_chapters", return_value=[{
                    "chapter_num": 1, "title": "পদার্থ",
                    "body_text": "body " * 40, "exercises_text": "",
                }]),
                patch("scripts.ahsec_ingest.upsert_chapter",
                      new_callable=AsyncMock, return_value=(mock_chapter, True)),
                patch("scripts.ahsec_ingest.generate_notes",
                      new_callable=AsyncMock, side_effect=err),
                patch("scripts.ahsec_ingest._log_progress", side_effect=_capture),
            ):
                await process_pdf_entry(
                    entry, MagicMock(),
                    force=True, dry_run=False, delay=0, done_keys=set(),
                )

            unavail = [r for r in logged if r["status"] == "notes_provider_unavailable"]
            assert len(unavail) == 1, (
                f"reason={reason_value!r}: expected 1 blocked entry — got {logged}"
            )
            assert f"reason={reason_value}" in unavail[0]["detail"], (
                f"reason={reason_value!r} must appear in detail — "
                f"got: {unavail[0]['detail']!r}"
            )
            assert unavail[0]["medium"] == "as", (
                f"reason={reason_value!r}: medium must be 'as' — "
                f"got {unavail[0]['medium']!r}"
            )


    @pytest.mark.anyio
    async def test_assamese_jsonl_entry_cleared_via_mongo_reconciliation(
        self, tmp_path
    ):
        """MongoDB reconciliation checks notes_as (not notes_en) for medium='as'.

        Scenario:
          1. A notes_provider_unavailable JSONL entry exists with medium='as'.
          2. generate_notes() still raises (providers still down during retry).
          3. Chapter.get() returns a chapter with notes_as populated (>100 chars)
             and notes_en empty — a staff member patched the Assamese field directly.
          4. _compact_progress_log() must branch on medium='as', check notes_as,
             and drop the stuck entry.
          5. After the full pipeline the JSONL file must contain no stuck entry.

        This guards against a regression where the medium branch is typo'd
        (e.g. 'as' → 'en'), which would cause the wrong field to be checked and
        the Assamese chapter to remain stuck indefinitely.
        """
        import sys
        from pathlib import Path as _Path

        backend_root = _Path(__file__).parent.parent
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))

        from scripts.ahsec_ingest import NotesProviderUnavailableError
        import app.api.v1.admin_content as admin_mod
        import scripts.ahsec_ingest as ingest_mod

        # Build an Assamese stuck record
        as_chapter_id = "7b2e98933d2b20e3253ef4e7"   # 24-hex valid ObjectId
        as_pdf_url    = "https://example.com/test_assamese_physics.pdf"
        as_key        = f"{as_pdf_url}|ch5"

        as_stuck_record = json.dumps({
            "ts":         "2026-01-01T00:00:00+00:00",
            "key":        as_key,
            "status":     "notes_provider_unavailable",
            "detail":     "both providers exhausted on Assamese run",
            "chapter_id": as_chapter_id,
            "pdf_url":    as_pdf_url,
            "medium":     "as",
        }) + "\n"

        progress_file = tmp_path / ".ahsec_ingest_progress.jsonl"
        lock_file     = tmp_path / ".ahsec_ingest_progress.lock"
        progress_file.write_text(as_stuck_record, encoding="utf-8")

        stuck_chapters = [{
            "chapter_id": as_chapter_id,
            "key":        as_key,
            "pdf_url":    as_pdf_url,
            "medium":     "as",
            "detail":     "",
            "ts":         "2026-01-01T00:00:00+00:00",
        }]

        # generate_notes FAILS — providers still down during retry
        unavail_err = NotesProviderUnavailableError(
            "Both providers exhausted on Assamese retry", reason="provider_error"
        )

        # Chapter in MongoDB has notes_as populated but notes_en empty —
        # staff patched the Assamese field directly while providers were down.
        manually_fixed_chapter = _make_chapter_mock(
            notes_en="",
            notes_as="## তাপগতিবিদ্যা\n\n" + "হস্তলিখিত বিষয়বস্তু। " * 10,
        )
        manually_fixed_chapter.subject_id = MagicMock()
        mock_subj = MagicMock()
        mock_subj.name = "Physics"

        with (
            patch("app.services.ai.sarvam_client.sarvam_client", MagicMock()),
            patch("scripts.ahsec_ingest.extract_pdf_text", new_callable=AsyncMock,
                  return_value=[{"page_num": 1, "text": "content " * 20}]),
            patch("scripts.ahsec_ingest.split_into_chapters", return_value=[{
                "chapter_num": 5, "title": "Thermodynamics",
                "body_text": "body " * 60, "exercises_text": "",
            }]),
            # Retry fails — providers still unavailable
            patch("scripts.ahsec_ingest.generate_notes",
                  new_callable=AsyncMock, side_effect=unavail_err),
            # Chapter in MongoDB has notes_as (manually fixed); notes_en is empty
            patch("app.models.content.Chapter.get",
                  new_callable=AsyncMock, return_value=manually_fixed_chapter),
            patch("app.models.content.Subject.get",
                  new_callable=AsyncMock, return_value=mock_subj),
            # Redirect _log_progress writes to the temp file
            patch.object(ingest_mod, "PROGRESS_FILE",      progress_file),
            patch.object(ingest_mod, "PROGRESS_LOCK_FILE", lock_file),
            # Redirect _compact_progress_log reads/writes to the same temp file
            patch.object(admin_mod, "_AHSEC_PROGRESS_FILE", progress_file),
            patch.object(admin_mod, "_AHSEC_PROGRESS_LOCK",  lock_file),
        ):
            await admin_mod._ahsec_stuck_retry_background(MagicMock(), stuck_chapters)

        # _compact_progress_log must have read notes_as (not notes_en) because
        # medium='as', found it populated, and dropped the stuck entry.
        stuck_lines = [
            l for l in progress_file.read_text(encoding="utf-8").splitlines()
            if l.strip()
            and json.loads(l).get("status") == "notes_provider_unavailable"
            and json.loads(l).get("key") == as_key
        ]
        assert stuck_lines == [], (
            f"Expected no notes_provider_unavailable entry for Assamese key {as_key!r} "
            f"after _compact_progress_log reconciled notes_as in MongoDB, "
            f"but file still contains: {stuck_lines}"
        )
