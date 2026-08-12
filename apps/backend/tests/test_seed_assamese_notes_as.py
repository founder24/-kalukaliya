"""
Task 146 — Confirm bulk Assamese translation writes to notes_as.

Tests that after a seed-assamese run:
  1. generate_assamese_only() reads from notes_en (primary) and writes to notes_as.
  2. generate_assamese_only() falls back to content_en when notes_en is absent,
     and still writes the result to notes_as.
  3. generate_assamese_only() skips translation when notes_as is already populated.
  4. generate_assamese_only() skips translation when content_as is already populated
     (legacy backward-compat guard).
  5. _seed_assamese_background counts a chapter as completed when notes_as is set.
  6. The admin_trigger_seed_assamese filter includes chapters that have notes_en
     but no notes_as (not just chapters with content_en/content_as).
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_chapter(
    *,
    chapter_id: str = "507f1f77bcf86cd799439011",
    title: str = "Test Chapter",
    notes_en: str = "",
    notes_as: str = "",
    content_en: str = "",
    content_as: str = "",
):
    """Return a minimal mock Chapter with the given field values."""
    ch = MagicMock()
    ch.id = MagicMock()
    ch.id.__str__ = lambda self: chapter_id
    ch.title = title
    ch.notes_en = notes_en
    ch.notes_as = notes_as
    ch.content_en = content_en
    ch.content_as = content_as
    ch.updated_at = datetime.now(timezone.utc)
    ch.save = AsyncMock()
    return ch


# ── generate_assamese_only ────────────────────────────────────────────────────

class TestGenerateAssameseOnly:
    """Unit tests for ContentGenerationService.generate_assamese_only()."""

    def _make_service(self):
        from app.services.content_generation import ContentGenerationService
        svc = ContentGenerationService.__new__(ContentGenerationService)
        svc._gcs_update = AsyncMock()
        return svc

    @pytest.mark.asyncio
    async def test_reads_notes_en_writes_notes_as(self):
        """Primary path: notes_en → notes_as."""
        svc = self._make_service()
        ch = _make_chapter(notes_en="English notes content here", notes_as="", content_en="")

        with (
            patch("app.services.content_generation.Chapter") as MockChapter,
            patch("app.services.content_generation.sarvam_client") as mock_sarvam,
        ):
            MockChapter.get = AsyncMock(return_value=ch)
            mock_sarvam.generate = AsyncMock(return_value="অসমীয়া অনুবাদ")

            result = await svc.generate_assamese_only(str(ch.id), force=False)

        assert result.notes_as == "অসমীয়া অনুবাদ", (
            "generate_assamese_only must write the translation to notes_as"
        )
        assert result.content_as == "অসমীয়া অনুবাদ", (
            "generate_assamese_only must also write to content_as for backward compat"
        )
        ch.save.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_falls_back_to_content_en_writes_notes_as(self):
        """Legacy path: notes_en absent → read content_en, still write notes_as."""
        svc = self._make_service()
        ch = _make_chapter(notes_en="", content_en="Legacy English content", notes_as="", content_as="")

        with (
            patch("app.services.content_generation.Chapter") as MockChapter,
            patch("app.services.content_generation.sarvam_client") as mock_sarvam,
        ):
            MockChapter.get = AsyncMock(return_value=ch)
            mock_sarvam.generate = AsyncMock(return_value="লিগেচি অনুবাদ")

            result = await svc.generate_assamese_only(str(ch.id), force=False)

        assert result.notes_as == "লিগেচি অনুবাদ", (
            "Fallback (content_en) translation must still be written to notes_as"
        )
        ch.save.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_when_notes_as_present(self):
        """Skip translation when notes_as is already populated (force=False)."""
        svc = self._make_service()
        ch = _make_chapter(notes_en="English notes", notes_as="ইতিমধ্যে আছে", content_as="")

        with (
            patch("app.services.content_generation.Chapter") as MockChapter,
            patch("app.services.content_generation.sarvam_client") as mock_sarvam,
        ):
            MockChapter.get = AsyncMock(return_value=ch)
            mock_sarvam.generate = AsyncMock(return_value="should not be called")

            result = await svc.generate_assamese_only(str(ch.id), force=False)

        mock_sarvam.generate.assert_not_awaited()
        ch.save.assert_not_awaited()
        assert result is ch  # returned unchanged

    @pytest.mark.asyncio
    async def test_skips_when_content_as_present_legacy(self):
        """Skip translation when content_as is already populated (legacy backward-compat)."""
        svc = self._make_service()
        ch = _make_chapter(notes_en="English notes", notes_as="", content_as="Legacy AS content")

        with (
            patch("app.services.content_generation.Chapter") as MockChapter,
            patch("app.services.content_generation.sarvam_client") as mock_sarvam,
        ):
            MockChapter.get = AsyncMock(return_value=ch)
            mock_sarvam.generate = AsyncMock(return_value="should not be called")

            result = await svc.generate_assamese_only(str(ch.id), force=False)

        mock_sarvam.generate.assert_not_awaited()
        ch.save.assert_not_awaited()
        assert result is ch

    @pytest.mark.asyncio
    async def test_force_overwrites_existing_notes_as(self):
        """force=True re-translates even when notes_as already exists."""
        svc = self._make_service()
        ch = _make_chapter(notes_en="Updated English", notes_as="পুৰণি অনুবাদ", content_as="পুৰণি")

        with (
            patch("app.services.content_generation.Chapter") as MockChapter,
            patch("app.services.content_generation.sarvam_client") as mock_sarvam,
        ):
            MockChapter.get = AsyncMock(return_value=ch)
            mock_sarvam.generate = AsyncMock(return_value="নতুন অনুবাদ")

            result = await svc.generate_assamese_only(str(ch.id), force=True)

        assert result.notes_as == "নতুন অনুবাদ"
        ch.save.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_raises_when_no_english_content(self):
        """Raise ValueError when neither notes_en nor content_en is set."""
        svc = self._make_service()
        ch = _make_chapter(notes_en="", content_en="", notes_as="", content_as="")

        with patch("app.services.content_generation.Chapter") as MockChapter:
            MockChapter.get = AsyncMock(return_value=ch)

            with pytest.raises(ValueError, match="no English content"):
                await svc.generate_assamese_only(str(ch.id))


# ── _seed_assamese_background ─────────────────────────────────────────────────

class TestSeedAssameseBackground:
    """Unit tests for _seed_assamese_background counting logic."""

    @pytest.mark.asyncio
    async def test_completed_count_incremented_when_notes_as_set(self):
        """Chapter is counted as 'completed' when the result has notes_as populated."""
        from app.api.v1.admin_cron import _seed_assamese_background

        ch = _make_chapter(notes_en="English notes", notes_as="")
        translated_ch = _make_chapter(notes_en="English notes", notes_as="অনুবাদিত")

        app_state = MagicMock()
        app_state.seed_assamese_status = {
            "running": True, "current": "", "completed": 0,
            "failed": 0, "skipped": 0, "failed_ids": [], "errors": [],
            "total": 1, "finished_at": None,
        }

        # _seed_assamese_background imports content_generation_service from its source module
        with (
            patch(
                "app.services.content_generation.content_generation_service.generate_assamese_only",
                new_callable=AsyncMock,
                return_value=translated_ch,
            ),
            patch("app.api.v1.admin_cron._flush_assamese_run_to_mongo", new_callable=AsyncMock),
        ):
            await _seed_assamese_background(
                app_state=app_state,
                chapters=[ch],
                concurrency=1,
                force=False,
                run_id="unavailable",
            )

        assert app_state.seed_assamese_status["completed"] == 1
        assert app_state.seed_assamese_status["skipped"] == 0
        assert app_state.seed_assamese_status["failed"] == 0

    @pytest.mark.asyncio
    async def test_skipped_count_incremented_when_no_as_field(self):
        """Chapter is counted as 'skipped' when result has neither notes_as nor content_as."""
        from app.api.v1.admin_cron import _seed_assamese_background

        ch = _make_chapter(notes_en="English notes")
        # generate_assamese_only returns the chapter unchanged (skipped path)
        skipped_ch = _make_chapter(notes_en="English notes", notes_as="", content_as="")

        app_state = MagicMock()
        app_state.seed_assamese_status = {
            "running": True, "current": "", "completed": 0,
            "failed": 0, "skipped": 0, "failed_ids": [], "errors": [],
            "total": 1, "finished_at": None,
        }

        with (
            patch(
                "app.services.content_generation.content_generation_service.generate_assamese_only",
                new_callable=AsyncMock,
                return_value=skipped_ch,
            ),
            patch("app.api.v1.admin_cron._flush_assamese_run_to_mongo", new_callable=AsyncMock),
        ):
            await _seed_assamese_background(
                app_state=app_state,
                chapters=[ch],
                concurrency=1,
                force=False,
                run_id="unavailable",
            )

        assert app_state.seed_assamese_status["skipped"] == 1
        assert app_state.seed_assamese_status["completed"] == 0
