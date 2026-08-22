"""
Task 170 — Confirm the 'Clear resolved' button correctly handles a missing
progress log file.

Coverage:
A. HTTP endpoint (POST /admin/content/seed-notes/stuck/clear)
   Mocks _compact_progress_log() to return the four canonical response shapes
   and verifies the endpoint passes them through unchanged.
   Scenarios: file missing · empty file · all-resolved · mixed.

B. _compact_progress_log() unit tests
   Uses a real tmpdir so actual file-I/O and locking paths are exercised.
   Patches only admin_content.__file__ so the function resolves
   _Path(__file__).parent×4 / "scripts" → tmpdir / "scripts".
   Scenarios mirror the four above; Chapter.get is mocked where needed.
"""

import json
import os
import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
from fastapi.testclient import TestClient


# ── shared helpers ─────────────────────────────────────────────────────────────

def _jsonl_rec(key: str, status: str, chapter_id: str = "6a1f87822c1a19d2142de3d6",
               medium: str = "en") -> str:
    return json.dumps({
        "ts":         datetime.now(timezone.utc).isoformat(),
        "key":        key,
        "status":     status,
        "detail":     "",
        "chapter_id": chapter_id,
        "pdf_url":    f"https://example.com/{key.split('|')[0].split('/')[-1]}",
        "medium":     medium,
    })


def _write_jsonl(scripts_dir: Path, *records: str) -> None:
    (scripts_dir / ".ahsec_ingest_progress.jsonl").write_text(
        "\n".join(records) + ("\n" if records else ""),
        encoding="utf-8",
    )


def _make_chapter_mock(notes_en: str = "", notes_as: str = "") -> MagicMock:
    ch = MagicMock()
    ch.notes_en = notes_en
    ch.notes_as = notes_as
    return ch


# ── fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


@pytest.fixture
def admin_cookie():
    from app.config import settings
    expire = datetime.now(timezone.utc) + timedelta(hours=8)
    payload = {"sub": "admin-test", "type": "admin", "role": "admin", "exp": expire}
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return {"syrabit_admin_session": token}


# ══════════════════════════════════════════════════════════════════════════════
# A. HTTP endpoint pass-through tests
#    Patch _compact_progress_log() to return each canonical shape; verify the
#    endpoint forwards the dict to the client without modification.
# ══════════════════════════════════════════════════════════════════════════════

class TestClearResolvedEndpoint:
    """POST /admin/content/seed-notes/stuck/clear — response shape pass-through."""

    URL = "/api/v1/admin/content/seed-notes/stuck/clear"

    def _post(self, client, admin_cookie, compact_return: dict):
        import app.api.v1.admin_content as admin_mod

        async def _fake_compact():
            return compact_return

        with patch.object(admin_mod, "_compact_progress_log", side_effect=_fake_compact):
            resp = client.post(self.URL, cookies=admin_cookie)

        assert resp.status_code == 200, resp.text
        return resp.json()

    def test_file_missing_response_shape(self, client, admin_cookie):
        """When the progress log is absent the endpoint returns
        compacted=False and file_exists=False — the shape the frontend
        uses to show the 'No progress log found' info toast."""
        shape = {
            "compacted":        False,
            "file_exists":      False,
            "records_before":   0,
            "records_after":    0,
            "resolved_cleared": 0,
            "still_stuck":      0,
        }
        data = self._post(client, admin_cookie, shape)

        assert data["compacted"]   is False, "compacted must be False when file is missing"
        assert data["file_exists"] is False, "file_exists must be False when file is missing"
        assert data["resolved_cleared"] == 0
        assert data["still_stuck"]      == 0

    def test_empty_file_response_shape(self, client, admin_cookie):
        """An empty progress file compacts to nothing — compacted=True,
        zero cleared, zero still-stuck."""
        shape = {
            "compacted":        True,
            "file_exists":      True,
            "records_before":   0,
            "records_after":    0,
            "resolved_cleared": 0,
            "still_stuck":      0,
        }
        data = self._post(client, admin_cookie, shape)

        assert data["compacted"]        is True
        assert data["file_exists"]      is True
        assert data["resolved_cleared"] == 0
        assert data["still_stuck"]      == 0

    def test_all_resolved_response_shape(self, client, admin_cookie):
        """All entries resolved: resolved_cleared > 0, still_stuck == 0.
        Frontend shows the success toast with the cleared count."""
        shape = {
            "compacted":        True,
            "file_exists":      True,
            "records_before":   5,
            "records_after":    0,
            "resolved_cleared": 5,
            "still_stuck":      0,
        }
        data = self._post(client, admin_cookie, shape)

        assert data["compacted"]        is True
        assert data["resolved_cleared"] == 5
        assert data["still_stuck"]      == 0
        assert data["records_after"]    == 0

    def test_mixed_stuck_and_resolved_response_shape(self, client, admin_cookie):
        """Mixed log: some still stuck, some resolved.
        Frontend shows success toast with both counts."""
        shape = {
            "compacted":        True,
            "file_exists":      True,
            "records_before":   5,
            "records_after":    2,
            "resolved_cleared": 3,
            "still_stuck":      2,
        }
        data = self._post(client, admin_cookie, shape)

        assert data["compacted"]        is True
        assert data["resolved_cleared"] == 3
        assert data["still_stuck"]      == 2
        assert data["records_before"]   == 5
        assert data["records_after"]    == 2

    def test_endpoint_requires_admin_auth(self, client):
        """POST without a valid session cookie must return 401."""
        resp = client.post(self.URL)
        assert resp.status_code == 401


# ══════════════════════════════════════════════════════════════════════════════
# B. _compact_progress_log() unit tests — real file I/O via tmpdir
#
# We temporarily replace admin_content.__file__ with a path that is 4 levels
# deep inside tmp_path, so the function's own path resolution
#   _Path(__file__).parent.parent.parent.parent / "scripts"
# resolves to:
#   tmp_path / "scripts"
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def scripts_dir(tmp_path):
    """Create a real scripts/ subdir inside tmp_path and return it."""
    d = tmp_path / "scripts"
    d.mkdir()
    return d


@pytest.fixture
def fake_module_file(tmp_path):
    """4-level-deep fake path so .parent×4 == tmp_path."""
    return str(tmp_path / "a" / "b" / "c" / "admin_content.py")


class TestCompactProgressLogUnit:
    """_compact_progress_log() — real-file unit tests."""

    @pytest.fixture(autouse=True)
    def redirect_progress_paths(self, scripts_dir, monkeypatch):
        """Point the module's explicit progress paths at this test's log."""
        import app.api.v1.admin_content as admin_mod

        monkeypatch.setattr(
            admin_mod,
            "_AHSEC_PROGRESS_FILE",
            scripts_dir / ".ahsec_ingest_progress.jsonl",
        )
        monkeypatch.setattr(
            admin_mod,
            "_AHSEC_PROGRESS_LOCK",
            scripts_dir / ".ahsec_ingest_progress.lock",
        )

    @pytest.mark.anyio
    async def test_missing_file_returns_file_exists_false(
        self, scripts_dir, fake_module_file
    ):
        """When .ahsec_ingest_progress.jsonl does not exist the function
        returns compacted=False and file_exists=False without raising."""
        import app.api.v1.admin_content as admin_mod

        # Do NOT create the progress file
        original = admin_mod.__file__
        try:
            admin_mod.__file__ = fake_module_file
            result = await admin_mod._compact_progress_log()
        finally:
            admin_mod.__file__ = original

        assert result["compacted"]   is False, (
            "compacted must be False when the progress file is absent"
        )
        assert result["file_exists"] is False, (
            "file_exists must be False when the progress file is absent"
        )
        assert result["resolved_cleared"] == 0
        assert result["still_stuck"]      == 0

    @pytest.mark.anyio
    async def test_empty_file_compacts_to_zero(self, scripts_dir, fake_module_file):
        """An empty progress file compacts successfully — no entries kept,
        zero resolved, zero still-stuck."""
        _write_jsonl(scripts_dir)   # writes an empty file (just a trailing newline)

        import app.api.v1.admin_content as admin_mod

        original = admin_mod.__file__
        try:
            admin_mod.__file__ = fake_module_file
            result = await admin_mod._compact_progress_log()
        finally:
            admin_mod.__file__ = original

        assert result["compacted"]        is True
        assert result["file_exists"]      is True
        assert result["resolved_cleared"] == 0
        assert result["still_stuck"]      == 0
        assert result["records_before"]   == 0

    @pytest.mark.anyio
    async def test_all_resolved_file_clears_everything(
        self, scripts_dir, fake_module_file
    ):
        """A file containing only non-stuck entries (status='done' etc.) is
        compacted to empty: resolved_cleared == number of distinct keys,
        still_stuck == 0."""
        _write_jsonl(
            scripts_dir,
            _jsonl_rec("https://ex.com/book.pdf|ch1", "done",    "aaa000bbb111ccc222ddd333"),
            _jsonl_rec("https://ex.com/book.pdf|ch2", "done",    "aaa000bbb111ccc222ddd334"),
            _jsonl_rec("https://ex.com/book.pdf|ch3", "skipped", "aaa000bbb111ccc222ddd335"),
        )

        import app.api.v1.admin_content as admin_mod

        original = admin_mod.__file__
        try:
            admin_mod.__file__ = fake_module_file
            result = await admin_mod._compact_progress_log()
        finally:
            admin_mod.__file__ = original

        assert result["compacted"]        is True
        assert result["still_stuck"]      == 0, (
            f"All entries are resolved; still_stuck must be 0, got {result['still_stuck']}"
        )
        assert result["resolved_cleared"] == 3, (
            f"Three distinct resolved keys; resolved_cleared must be 3, got {result['resolved_cleared']}"
        )

    @pytest.mark.anyio
    async def test_mixed_file_keeps_only_stuck_entries(
        self, scripts_dir, fake_module_file
    ):
        """A log with 2 stuck + 3 resolved entries: after compaction still_stuck==2,
        resolved_cleared==3, and the rewritten file contains exactly 2 lines."""
        stuck_id_1 = "aaa000bbb111ccc222ddd340"
        stuck_id_2 = "aaa000bbb111ccc222ddd341"
        _write_jsonl(
            scripts_dir,
            _jsonl_rec("https://ex.com/p.pdf|ch1", "notes_provider_unavailable", stuck_id_1),
            _jsonl_rec("https://ex.com/p.pdf|ch2", "notes_provider_unavailable", stuck_id_2),
            _jsonl_rec("https://ex.com/p.pdf|ch3", "done",    "aaa000bbb111ccc222ddd342"),
            _jsonl_rec("https://ex.com/p.pdf|ch4", "done",    "aaa000bbb111ccc222ddd343"),
            _jsonl_rec("https://ex.com/p.pdf|ch5", "skipped", "aaa000bbb111ccc222ddd344"),
        )

        import app.api.v1.admin_content as admin_mod

        # Chapter.get must return empty-notes mocks so stuck chapters aren't
        # resolved by the MongoDB reconciliation path.
        with patch(
            "app.models.content.Chapter.get",
            new_callable=AsyncMock,
            return_value=_make_chapter_mock(notes_en=""),
        ):
            original = admin_mod.__file__
            try:
                admin_mod.__file__ = fake_module_file
                result = await admin_mod._compact_progress_log()
            finally:
                admin_mod.__file__ = original

        assert result["compacted"]        is True
        assert result["still_stuck"]      == 2, (
            f"Two stuck entries must remain — got {result['still_stuck']}"
        )
        assert result["resolved_cleared"] == 3, (
            f"Three resolved entries must be cleared — got {result['resolved_cleared']}"
        )
        assert result["records_before"]   == 5
        assert result["records_after"]    == 2

        # Verify the rewritten file really has only 2 lines
        progress_file = scripts_dir / ".ahsec_ingest_progress.jsonl"
        remaining = [
            ln for ln in progress_file.read_text(encoding="utf-8").splitlines() if ln.strip()
        ]
        assert len(remaining) == 2, (
            f"Rewritten file must have 2 stuck lines; found {len(remaining)}: {remaining}"
        )
        ids_in_file = {json.loads(ln)["chapter_id"] for ln in remaining}
        assert ids_in_file == {stuck_id_1, stuck_id_2}

    @pytest.mark.anyio
    async def test_last_entry_per_key_wins_mixed_order(
        self, scripts_dir, fake_module_file
    ):
        """JSONL is append-only: the last entry per key determines status.
        A key that starts as 'notes_provider_unavailable' and ends as 'done'
        must be counted as resolved, not stuck."""
        chapter_id = "aaa000bbb111ccc222ddd350"
        key = "https://ex.com/q.pdf|ch7"
        _write_jsonl(
            scripts_dir,
            _jsonl_rec(key, "notes_provider_unavailable", chapter_id),
            _jsonl_rec(key, "done", chapter_id),   # re-run succeeded
        )

        import app.api.v1.admin_content as admin_mod

        original = admin_mod.__file__
        try:
            admin_mod.__file__ = fake_module_file
            result = await admin_mod._compact_progress_log()
        finally:
            admin_mod.__file__ = original

        assert result["still_stuck"]      == 0, (
            "Chapter whose final log entry is 'done' must not count as stuck"
        )
        assert result["resolved_cleared"] == 1

    @pytest.mark.anyio
    async def test_mongo_resolved_chapter_not_counted_as_stuck(
        self, scripts_dir, fake_module_file
    ):
        """Even if the JSONL still says 'notes_provider_unavailable', a chapter
        whose notes_en is now populated (>100 chars) must be treated as resolved
        by the MongoDB reconciliation pass."""
        valid_id = "aaa000bbb111ccc222ddd360"
        _write_jsonl(
            scripts_dir,
            _jsonl_rec("https://ex.com/r.pdf|ch9", "notes_provider_unavailable", valid_id),
        )

        import app.api.v1.admin_content as admin_mod

        populated_mock = _make_chapter_mock(notes_en="## Chapter\n\n" + "x" * 200)
        with patch(
            "app.models.content.Chapter.get",
            new_callable=AsyncMock,
            return_value=populated_mock,
        ):
            original = admin_mod.__file__
            try:
                admin_mod.__file__ = fake_module_file
                result = await admin_mod._compact_progress_log()
            finally:
                admin_mod.__file__ = original

        assert result["still_stuck"] == 0, (
            "A chapter with notes_en >100 chars must be reconciled as resolved "
            f"by the MongoDB pass — got still_stuck={result['still_stuck']}"
        )
        assert result["resolved_cleared"] == 1
