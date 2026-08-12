"""
Tests for _compact_progress_log_sync() in ahsec_ingest and the compaction
calls wired into _seed_notes_background / _seed_assamese_background in
admin_cron so the AHSEC progress log doesn't grow unboundedly between runs.
"""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── helper: make a progress record ────────────────────────────────────────────

def _rec(key: str, status: str, ts: str = "2026-01-01T00:00:00+00:00", **kw) -> dict:
    return {"key": key, "status": status, "ts": ts, "chapter_id": "",
            "pdf_url": "", "medium": "en", "detail": "", **kw}


# ── _compact_progress_log_sync ────────────────────────────────────────────────

class TestCompactProgressLogSync:
    """_compact_progress_log_sync() must deduplicate the JSONL to one record
    per key, atomically rewrite the file, and return a correct summary."""

    def _import_compact(self):
        """Import the function fresh (it's a module-level function)."""
        backend_root = Path(__file__).parent.parent.parent.parent
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))
        from scripts.ahsec_ingest import _compact_progress_log_sync
        return _compact_progress_log_sync

    def test_returns_no_file_when_file_missing(self, tmp_path):
        compact = self._import_compact()
        import scripts.ahsec_ingest as ingest_mod
        orig = ingest_mod.PROGRESS_FILE
        try:
            ingest_mod.PROGRESS_FILE = tmp_path / "nonexistent.jsonl"
            ingest_mod.PROGRESS_LOCK_FILE = tmp_path / "nonexistent.lock"
            result = compact()
        finally:
            ingest_mod.PROGRESS_FILE = orig
        assert result["compacted"] is False
        assert result["file_exists"] is False

    def test_deduplicates_same_key_keeps_latest(self, tmp_path):
        compact = self._import_compact()
        import scripts.ahsec_ingest as ingest_mod

        progress = tmp_path / ".ahsec_ingest_progress.jsonl"
        lock     = tmp_path / ".ahsec_ingest_progress.lock"

        # Three records for the same key — oldest, middle, newest
        records = [
            _rec("pdf1|ch1", "notes_provider_unavailable", ts="2026-01-01T00:00:00+00:00"),
            _rec("pdf1|ch1", "notes_provider_unavailable", ts="2026-01-02T00:00:00+00:00"),
            _rec("pdf1|ch1", "done",                       ts="2026-01-03T00:00:00+00:00"),
        ]
        progress.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

        orig_file = ingest_mod.PROGRESS_FILE
        orig_lock = ingest_mod.PROGRESS_LOCK_FILE
        try:
            ingest_mod.PROGRESS_FILE      = progress
            ingest_mod.PROGRESS_LOCK_FILE = lock
            result = compact()
        finally:
            ingest_mod.PROGRESS_FILE      = orig_file
            ingest_mod.PROGRESS_LOCK_FILE = orig_lock

        assert result["compacted"] is True
        assert result["records_before"] == 3
        assert result["records_after"]  == 1
        assert result["removed"]        == 2

        kept = [json.loads(l) for l in progress.read_text().splitlines() if l.strip()]
        assert len(kept) == 1
        assert kept[0]["status"] == "done"
        assert kept[0]["ts"]     == "2026-01-03T00:00:00+00:00"

    def test_multiple_distinct_keys_all_kept(self, tmp_path):
        compact = self._import_compact()
        import scripts.ahsec_ingest as ingest_mod

        progress = tmp_path / ".ahsec_ingest_progress.jsonl"
        lock     = tmp_path / ".ahsec_ingest_progress.lock"

        records = [
            _rec("pdf1|ch1", "done",                       ts="2026-01-01T00:00:00+00:00"),
            _rec("pdf1|ch2", "notes_provider_unavailable", ts="2026-01-01T01:00:00+00:00"),
            _rec("pdf1|ch3", "done",                       ts="2026-01-01T02:00:00+00:00"),
        ]
        progress.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

        orig_file = ingest_mod.PROGRESS_FILE
        orig_lock = ingest_mod.PROGRESS_LOCK_FILE
        try:
            ingest_mod.PROGRESS_FILE      = progress
            ingest_mod.PROGRESS_LOCK_FILE = lock
            result = compact()
        finally:
            ingest_mod.PROGRESS_FILE      = orig_file
            ingest_mod.PROGRESS_LOCK_FILE = orig_lock

        assert result["records_before"] == 3
        assert result["records_after"]  == 3
        assert result["removed"]        == 0

    def test_mixed_keys_only_duplicates_removed(self, tmp_path):
        compact = self._import_compact()
        import scripts.ahsec_ingest as ingest_mod

        progress = tmp_path / ".ahsec_ingest_progress.jsonl"
        lock     = tmp_path / ".ahsec_ingest_progress.lock"

        # ch1 has 3 entries (2 duplicates); ch2 has 1; ch3 has 2 (1 duplicate)
        records = [
            _rec("pdf1|ch1", "notes_provider_unavailable", ts="2026-01-01T00:00:00+00:00"),
            _rec("pdf1|ch1", "notes_provider_unavailable", ts="2026-01-01T01:00:00+00:00"),
            _rec("pdf1|ch1", "done",                       ts="2026-01-01T02:00:00+00:00"),
            _rec("pdf1|ch2", "done",                       ts="2026-01-01T03:00:00+00:00"),
            _rec("pdf1|ch3", "notes_provider_unavailable", ts="2026-01-01T04:00:00+00:00"),
            _rec("pdf1|ch3", "done",                       ts="2026-01-01T05:00:00+00:00"),
        ]
        progress.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

        orig_file = ingest_mod.PROGRESS_FILE
        orig_lock = ingest_mod.PROGRESS_LOCK_FILE
        try:
            ingest_mod.PROGRESS_FILE      = progress
            ingest_mod.PROGRESS_LOCK_FILE = lock
            result = compact()
        finally:
            ingest_mod.PROGRESS_FILE      = orig_file
            ingest_mod.PROGRESS_LOCK_FILE = orig_lock

        assert result["records_before"] == 6
        assert result["records_after"]  == 3   # ch1-done, ch2-done, ch3-done
        assert result["removed"]        == 3

    def test_empty_file_returns_zero_counts(self, tmp_path):
        compact = self._import_compact()
        import scripts.ahsec_ingest as ingest_mod

        progress = tmp_path / ".ahsec_ingest_progress.jsonl"
        lock     = tmp_path / ".ahsec_ingest_progress.lock"
        progress.write_text("", encoding="utf-8")

        orig_file = ingest_mod.PROGRESS_FILE
        orig_lock = ingest_mod.PROGRESS_LOCK_FILE
        try:
            ingest_mod.PROGRESS_FILE      = progress
            ingest_mod.PROGRESS_LOCK_FILE = lock
            result = compact()
        finally:
            ingest_mod.PROGRESS_FILE      = orig_file
            ingest_mod.PROGRESS_LOCK_FILE = orig_lock

        # An empty file has 0 records; compaction is a no-op but still runs
        assert result["records_before"] == 0
        assert result["records_after"]  == 0
        assert result["removed"]        == 0


# ── main() calls compact after the bulk loop ──────────────────────────────────

class TestMainCallsCompactAfterBulkRun:
    """ahsec_ingest.main() must call _compact_progress_log_sync() once after
    the processing loop finishes (and only in live mode, not dry-run)."""

    @pytest.mark.anyio
    async def test_compact_called_after_main_live_run(self):
        # init_mongo and settings are imported lazily inside main(), so patch
        # them at their source modules, not on scripts.ahsec_ingest.
        import scripts.ahsec_ingest as ingest_mod

        compact_calls: list = []

        def _fake_compact():
            compact_calls.append(True)
            return {"compacted": True, "records_before": 5, "records_after": 3, "removed": 2}

        fake_args = MagicMock()
        fake_args.dry_run  = False
        fake_args.pilot    = False
        fake_args.class11  = True
        fake_args.class12  = False
        fake_args.medium   = "en"
        fake_args.subject  = None
        fake_args.force    = False
        fake_args.delay    = 0.0
        fake_args.limit    = None

        fake_settings = MagicMock()
        fake_settings.MONGODB_URI     = "mongodb://localhost"
        fake_settings.MONGODB_DB_NAME = "test"
        fake_settings.SARVAM_API_KEY  = "sk-test"

        with (
            patch("scripts.ahsec_ingest._parse_args",    return_value=fake_args),
            # init_mongo is imported as `from app.db.mongo import init_mongo`
            patch("app.db.mongo.init_mongo",             new_callable=AsyncMock),
            # settings is imported as `from app.config import settings`
            patch("app.config.settings",                 fake_settings),
            patch("scripts.ahsec_ingest.build_catalogue", return_value=[]),
            patch("scripts.ahsec_ingest._load_done_keys", return_value=set()),
            # gemini_available imported as `from app.services.ai.gemini_fallback import _available`
            patch("app.services.ai.gemini_fallback._available", return_value=True),
            patch("scripts.ahsec_ingest._compact_progress_log_sync",
                  side_effect=_fake_compact),
        ):
            await ingest_mod.main()

        assert len(compact_calls) == 1, (
            "main() must call _compact_progress_log_sync() once after the "
            f"processing loop — called {len(compact_calls)} time(s)"
        )

    @pytest.mark.anyio
    async def test_compact_not_called_in_dry_run(self):
        """Dry-run mode must NOT compact — the file is never modified in dry-run."""
        import scripts.ahsec_ingest as ingest_mod

        compact_calls: list = []

        def _fake_compact():
            compact_calls.append(True)
            return {}

        fake_args = MagicMock()
        fake_args.dry_run  = True
        fake_args.pilot    = False
        fake_args.class11  = True
        fake_args.class12  = False
        fake_args.medium   = "en"
        fake_args.subject  = None
        fake_args.force    = False
        fake_args.delay    = 0.0
        fake_args.limit    = None

        fake_settings = MagicMock()
        fake_settings.MONGODB_URI     = "mongodb://localhost"
        fake_settings.MONGODB_DB_NAME = "test"
        fake_settings.SARVAM_API_KEY  = "sk-test"

        with (
            patch("scripts.ahsec_ingest._parse_args",    return_value=fake_args),
            patch("app.db.mongo.init_mongo",             new_callable=AsyncMock),
            patch("app.config.settings",                 fake_settings),
            patch("scripts.ahsec_ingest.build_catalogue", return_value=[]),
            patch("scripts.ahsec_ingest._load_done_keys", return_value=set()),
            patch("scripts.ahsec_ingest._compact_progress_log_sync",
                  side_effect=_fake_compact),
        ):
            await ingest_mod.main()

        assert len(compact_calls) == 0, (
            "main() must NOT call _compact_progress_log_sync() in dry-run mode — "
            f"called {len(compact_calls)} time(s)"
        )


# ── Admin cron jobs must NOT destroy done-key resume state ────────────────────

class TestCronJobsPreserveDoneKeys:
    """_seed_notes_background and _seed_assamese_background must not compact the
    AHSEC progress JSONL — they use content_generation_service (MongoDB), not
    ahsec_ingest, so they never write to the JSONL.  Running the destructive
    _compact_progress_log() from there would erase 'done' entries and force the
    CLI to re-process every chapter on the next non-forced run.

    These tests write 'done' entries to a temp JSONL, run the admin cron
    background functions, and verify _load_done_keys() still returns them.
    """

    def _make_app_state(self, key: str) -> MagicMock:
        state = MagicMock()
        setattr(state, key, {
            "running": True, "total": 0, "completed": 0, "failed": 0,
            "skipped": 0, "failed_ids": [], "errors": [], "current": "",
            "topics_seeded": 0, "finished_at": None,
        })
        return state

    @pytest.mark.anyio
    async def test_seed_notes_background_preserves_done_keys(self, tmp_path):
        """After _seed_notes_background finishes, _load_done_keys() still
        returns the 'done' entries written before the job ran."""
        import scripts.ahsec_ingest as ingest_mod
        import app.api.v1.admin_cron as cron_mod

        progress = tmp_path / ".ahsec_ingest_progress.jsonl"
        lock     = tmp_path / ".ahsec_ingest_progress.lock"

        # Pre-populate with two 'done' entries
        records = [
            _rec("pdf1|ch1", "done", ts="2026-01-01T00:00:00+00:00"),
            _rec("pdf1|ch2", "done", ts="2026-01-01T01:00:00+00:00"),
        ]
        progress.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

        orig_file = ingest_mod.PROGRESS_FILE
        orig_lock = ingest_mod.PROGRESS_LOCK_FILE
        try:
            ingest_mod.PROGRESS_FILE      = progress
            ingest_mod.PROGRESS_LOCK_FILE = lock

            app_state = self._make_app_state("seed_notes_status")
            with patch("app.api.v1.admin_cron._flush_run_to_mongo", new_callable=AsyncMock):
                await cron_mod._seed_notes_background(
                    app_state, chapters=[], concurrency=1, force=False, run_id="unavailable"
                )

            done_keys = ingest_mod._load_done_keys()
        finally:
            ingest_mod.PROGRESS_FILE      = orig_file
            ingest_mod.PROGRESS_LOCK_FILE = orig_lock

        assert "pdf1|ch1" in done_keys, (
            "_seed_notes_background must not erase 'done' entries — "
            f"_load_done_keys() returned: {done_keys}"
        )
        assert "pdf1|ch2" in done_keys, (
            "_seed_notes_background must not erase 'done' entries — "
            f"_load_done_keys() returned: {done_keys}"
        )

    @pytest.mark.anyio
    async def test_seed_assamese_background_preserves_done_keys(self, tmp_path):
        """After _seed_assamese_background finishes, _load_done_keys() still
        returns the 'done' entries written before the job ran."""
        import scripts.ahsec_ingest as ingest_mod
        import app.api.v1.admin_cron as cron_mod

        progress = tmp_path / ".ahsec_ingest_progress.jsonl"
        lock     = tmp_path / ".ahsec_ingest_progress.lock"

        records = [
            _rec("pdf2|ch5", "done", ts="2026-02-01T00:00:00+00:00"),
            _rec("pdf2|ch6", "done", ts="2026-02-01T01:00:00+00:00"),
        ]
        progress.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

        orig_file = ingest_mod.PROGRESS_FILE
        orig_lock = ingest_mod.PROGRESS_LOCK_FILE
        try:
            ingest_mod.PROGRESS_FILE      = progress
            ingest_mod.PROGRESS_LOCK_FILE = lock

            app_state = self._make_app_state("seed_assamese_status")
            with patch("app.api.v1.admin_cron._flush_assamese_run_to_mongo",
                       new_callable=AsyncMock):
                await cron_mod._seed_assamese_background(
                    app_state, chapters=[], concurrency=1, force=False, run_id="unavailable"
                )

            done_keys = ingest_mod._load_done_keys()
        finally:
            ingest_mod.PROGRESS_FILE      = orig_file
            ingest_mod.PROGRESS_LOCK_FILE = orig_lock

        assert "pdf2|ch5" in done_keys, (
            "_seed_assamese_background must not erase 'done' entries — "
            f"_load_done_keys() returned: {done_keys}"
        )
        assert "pdf2|ch6" in done_keys, (
            "_seed_assamese_background must not erase 'done' entries — "
            f"_load_done_keys() returned: {done_keys}"
        )

    @pytest.mark.anyio
    async def test_sync_compact_preserves_done_keys(self, tmp_path):
        """_compact_progress_log_sync() keeps 'done' entries (it's a pure
        dedup — keeps the LATEST record per key regardless of status), so
        _load_done_keys() still returns them after compaction."""
        import scripts.ahsec_ingest as ingest_mod
        from scripts.ahsec_ingest import _compact_progress_log_sync

        progress = tmp_path / ".ahsec_ingest_progress.jsonl"
        lock     = tmp_path / ".ahsec_ingest_progress.lock"

        # Two 'done' entries with duplicate older entries
        records = [
            _rec("pdf3|ch1", "notes_provider_unavailable", ts="2026-03-01T00:00:00+00:00"),
            _rec("pdf3|ch1", "done",                       ts="2026-03-01T01:00:00+00:00"),
            _rec("pdf3|ch2", "notes_provider_unavailable", ts="2026-03-01T02:00:00+00:00"),
            _rec("pdf3|ch2", "done",                       ts="2026-03-01T03:00:00+00:00"),
        ]
        progress.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

        orig_file = ingest_mod.PROGRESS_FILE
        orig_lock = ingest_mod.PROGRESS_LOCK_FILE
        try:
            ingest_mod.PROGRESS_FILE      = progress
            ingest_mod.PROGRESS_LOCK_FILE = lock

            result = _compact_progress_log_sync()
            done_keys = ingest_mod._load_done_keys()
        finally:
            ingest_mod.PROGRESS_FILE      = orig_file
            ingest_mod.PROGRESS_LOCK_FILE = orig_lock

        assert result["records_before"] == 4
        assert result["records_after"]  == 2   # one latest per key
        assert "pdf3|ch1" in done_keys, (
            "_compact_progress_log_sync must preserve 'done' entries — "
            f"_load_done_keys() returned: {done_keys}"
        )
        assert "pdf3|ch2" in done_keys, (
            "_compact_progress_log_sync must preserve 'done' entries — "
            f"_load_done_keys() returned: {done_keys}"
        )
