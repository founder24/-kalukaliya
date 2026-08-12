"""
Unit tests for generate_notes() provider-fallback logic and the
process_pdf_entry() progress-log path.

Covers three provider-availability scenarios for BOTH medium="en" and medium="as":
  (a) Sarvam short + Gemini available  → Gemini result returned
  (b) Sarvam short + Gemini unconfigured → NotesProviderUnavailableError raised
  (c) Sarvam short + Gemini also short  → NotesProviderUnavailableError raised

Also verifies:
  - medium="as" routing: is_assamese=True is forwarded to Sarvam and the
    Assamese system prompt (_NOTES_SYSTEM_AS) is selected.
  - process_pdf_entry() records "notes_provider_unavailable" in the progress
    log when generate_notes() raises NotesProviderUnavailableError.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Make the scripts package importable without installing it.
# ---------------------------------------------------------------------------
_BACKEND_DIR = Path(__file__).parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# Import only the symbols under test — not the CLI entry-point.
from scripts.ahsec_ingest import (
    NotesProviderUnavailableError,
    generate_notes,
    _log_progress,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sarvam(output: str = "") -> MagicMock:
    """Return a mock Sarvam client whose generate() returns *output*."""
    sarvam = MagicMock()
    sarvam.generate = AsyncMock(return_value=output)
    return sarvam


_SHORT = "too short"          # < 2500 chars → triggers fallback
_LONG  = "## Topic\n\n" + "x" * 2600  # ≥ 2500 chars → accepted


# ---------------------------------------------------------------------------
# Scenario (a): Sarvam short, Gemini available and returns long output
# Parametrized over both mediums — a regression in the AS routing logic
# (e.g. wrong system prompt selection or missing is_assamese=True) would
# cause AS chapters to receive empty notes without raising any error.
# ---------------------------------------------------------------------------

@pytest.mark.anyio
@pytest.mark.parametrize("medium", ["en", "as"])
async def test_gemini_used_when_sarvam_short(medium):
    """Gemini result is returned when Sarvam gives < 2500 chars (both mediums)."""
    sarvam = _make_sarvam(_SHORT)

    with (
        patch(
            "app.services.ai.gemini_fallback._available",
            return_value=True,
        ),
        patch(
            "app.services.ai.gemini_fallback.generate_gemini",
            new_callable=AsyncMock,
            return_value=_LONG,
        ) as mock_gemini,
    ):
        result = await generate_notes(
            sarvam,
            body_text="Chapter body text " * 20,
            chapter_title="Test Chapter",
            subject_name="Physics",
            medium=medium,
        )

    # Gemini must have been called exactly once regardless of medium
    mock_gemini.assert_called_once()
    # The returned text must come from Gemini (starts with the long marker)
    assert len(result) >= 300
    assert "## Topic" in result


# ---------------------------------------------------------------------------
# Scenario (b): Sarvam short, Gemini NOT configured → error raised
# ---------------------------------------------------------------------------

@pytest.mark.anyio
@pytest.mark.parametrize("medium", ["en", "as"])
async def test_error_raised_when_gemini_unconfigured(medium):
    """NotesProviderUnavailableError is raised when Sarvam output is < 2500 chars and
    Gemini credentials are absent (_available() returns False) — both mediums."""
    sarvam = _make_sarvam(_SHORT)

    with (
        patch(
            "app.services.ai.gemini_fallback._available",
            return_value=False,
        ),
        patch(
            "app.services.ai.gemini_fallback.generate_gemini",
            new_callable=AsyncMock,
        ) as mock_gemini,
    ):
        with pytest.raises(NotesProviderUnavailableError) as exc_info:
            await generate_notes(
                sarvam,
                body_text="Chapter body text " * 20,
                chapter_title="Unconfigured Chapter",
                subject_name="Chemistry",
                medium=medium,
            )

    # Gemini must NOT have been called (we bailed early because _available() is False)
    mock_gemini.assert_not_called()
    # The error message should mention both providers
    assert "unavailable" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Scenario (c): Sarvam short, Gemini also returns short output → error raised
# ---------------------------------------------------------------------------

@pytest.mark.anyio
@pytest.mark.parametrize("medium", ["en", "as"])
async def test_error_raised_when_both_providers_short(medium):
    """NotesProviderUnavailableError is raised when both Sarvam and Gemini
    return fewer than 2500 characters — both mediums."""
    sarvam = _make_sarvam(_SHORT)

    with (
        patch(
            "app.services.ai.gemini_fallback._available",
            return_value=True,
        ),
        patch(
            "app.services.ai.gemini_fallback.generate_gemini",
            new_callable=AsyncMock,
            return_value=_SHORT,   # Gemini also too short
        ) as mock_gemini,
    ):
        with pytest.raises(NotesProviderUnavailableError) as exc_info:
            await generate_notes(
                sarvam,
                body_text="Chapter body text " * 20,
                chapter_title="Both Short Chapter",
                subject_name="Biology",
                medium=medium,
            )

    # Gemini was attempted
    mock_gemini.assert_called_once()
    # Error message should mention both providers
    error_text = str(exc_info.value).lower()
    assert "sarvam" in error_text or "both" in error_text


# ---------------------------------------------------------------------------
# AS-specific: verify medium routing forwards is_assamese=True to Sarvam
# A bug here would silently produce EN-flavoured notes for AS chapters without
# raising any error, so Sarvam output length would be the only signal.
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_assamese_medium_passes_is_assamese_to_sarvam():
    """medium='as' must pass is_assamese=True to sarvam.generate() so that the
    correct Assamese system prompt and tokeniser settings are used."""
    sarvam = _make_sarvam(_LONG)   # long output → accepted without Gemini

    with patch(
        "app.services.ai.gemini_fallback.generate_gemini",
        new_callable=AsyncMock,
    ) as mock_gemini:
        await generate_notes(
            sarvam,
            body_text="Chapter body text " * 20,
            chapter_title="অধ্যায়",
            subject_name="Physics",
            medium="as",
        )

    # Gemini must not have been called (Sarvam succeeded)
    mock_gemini.assert_not_called()

    # sarvam.generate must have been called with is_assamese=True
    call_kwargs = sarvam.generate.call_args
    assert call_kwargs is not None, "sarvam.generate was never called"
    # The keyword argument may be positional or keyword; check both
    args, kwargs = call_kwargs
    is_assamese_val = kwargs.get("is_assamese", args[2] if len(args) > 2 else None)
    assert is_assamese_val is True, (
        "medium='as' must pass is_assamese=True to sarvam.generate(); "
        f"got is_assamese={is_assamese_val!r}"
    )


@pytest.mark.anyio
async def test_english_medium_passes_is_assamese_false_to_sarvam():
    """medium='en' must pass is_assamese=False so EN chapters don't use the
    Assamese tokeniser path."""
    sarvam = _make_sarvam(_LONG)

    await generate_notes(
        sarvam,
        body_text="Chapter body text " * 20,
        chapter_title="Motion in a Plane",
        subject_name="Physics",
        medium="en",
    )

    call_kwargs = sarvam.generate.call_args
    assert call_kwargs is not None
    args, kwargs = call_kwargs
    is_assamese_val = kwargs.get("is_assamese", args[2] if len(args) > 2 else None)
    assert is_assamese_val is False, (
        "medium='en' must pass is_assamese=False to sarvam.generate(); "
        f"got is_assamese={is_assamese_val!r}"
    )


# ---------------------------------------------------------------------------
# Sarvam succeeds on first attempt → Gemini never called
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_sarvam_success_skips_gemini():
    """When Sarvam returns ≥ 2500 chars Gemini is not called at all."""
    sarvam = _make_sarvam(_LONG)

    with (
        patch(
            "app.services.ai.gemini_fallback._available",
            return_value=True,
        ),
        patch(
            "app.services.ai.gemini_fallback.generate_gemini",
            new_callable=AsyncMock,
        ) as mock_gemini,
    ):
        result = await generate_notes(
            sarvam,
            body_text="Body text " * 30,
            chapter_title="Good Chapter",
            subject_name="Maths",
            medium="en",
        )

    mock_gemini.assert_not_called()
    assert len(result) >= 2500


# ---------------------------------------------------------------------------
# process_pdf_entry: progress log receives "notes_provider_unavailable"
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_process_pdf_entry_logs_unavailable_status(tmp_path):
    """When generate_notes() raises NotesProviderUnavailableError,
    process_pdf_entry() writes a 'notes_provider_unavailable' record to the
    progress log and increments the errors counter."""
    import scripts.ahsec_ingest as ingest_mod

    # Redirect the progress file to a temp location so we don't pollute the
    # real progress log and so we can inspect the written records.
    tmp_progress = tmp_path / "progress.jsonl"

    # Build a minimal mock chapter returned by upsert_chapter
    mock_chapter = MagicMock()
    mock_chapter.id = "chapter-abc"
    mock_chapter.notes_en = ""
    mock_chapter.notes_as = ""

    # Build a minimal mock subject returned by upsert_subject
    mock_subj = MagicMock()
    mock_subj.id = "subject-xyz"

    entry = {
        "subject_name": "Physics",
        "subject_slug": "physics",
        "class_level": "11",
        "medium": "en",
        "pdf_url": "https://example.com/physics.pdf",
        "part_num": 1,
        "book_label": "Physics (E)",
    }

    # Fake chapter info returned by split_into_chapters
    fake_chapters = [
        {
            "chapter_num": 1,
            "title": "Physical World",
            "body_text": "Body text " * 50,
            "exercises_text": "",
        }
    ]

    logged_records: list[dict] = []

    def _fake_log_progress(key, status, detail="", chapter_id="", pdf_url="", medium=""):
        import json
        logged_records.append({
            "key": key,
            "status": status,
            "detail": detail,
            "chapter_id": chapter_id,
            "pdf_url": pdf_url,
            "medium": medium,
        })

    with (
        # Redirect PROGRESS_FILE so writes go to tmp_path
        patch.object(ingest_mod, "PROGRESS_FILE", tmp_progress),
        # Mock DB helpers
        patch.object(ingest_mod, "upsert_subject", new_callable=AsyncMock, return_value=mock_subj),
        patch.object(ingest_mod, "extract_pdf_text", new_callable=AsyncMock, return_value=[
            {"page_num": 1, "text": "Some page text that is long enough " * 5},
        ]),
        patch.object(ingest_mod, "split_into_chapters", return_value=fake_chapters),
        patch.object(ingest_mod, "upsert_chapter", new_callable=AsyncMock, return_value=(mock_chapter, True)),
        # Intercept _log_progress to capture calls
        patch.object(ingest_mod, "_log_progress", side_effect=_fake_log_progress),
        # generate_notes always raises the unavailable error
        patch.object(
            ingest_mod,
            "generate_notes",
            new_callable=AsyncMock,
            side_effect=NotesProviderUnavailableError("both providers failed"),
        ),
    ):
        sarvam = _make_sarvam()
        stats = await ingest_mod.process_pdf_entry(
            entry,
            sarvam,
            force=True,
            dry_run=False,
            delay=0.0,
            done_keys=set(),
        )

    # The stats dict must count one error (not one "done")
    assert stats.get("errors", 0) == 1
    assert stats.get("done", 0) == 0

    # The progress log must contain exactly one record with the correct status
    unavailable_records = [r for r in logged_records if r["status"] == "notes_provider_unavailable"]
    assert len(unavailable_records) == 1, (
        f"Expected 1 'notes_provider_unavailable' record, got {len(unavailable_records)}. "
        f"All logged: {logged_records}"
    )

    rec = unavailable_records[0]
    assert rec["chapter_id"] == "chapter-abc"
    assert rec["pdf_url"] == "https://example.com/physics.pdf"
    assert rec["medium"] == "en"
    assert "both providers failed" in rec["detail"]
    # The detail must carry a machine-readable reason token so staff can grep
    assert "reason=" in rec["detail"], (
        "Progress log detail must include 'reason=<value>' so staff can filter "
        "by root cause without reading the full message"
    )


# ---------------------------------------------------------------------------
# reason field: machine-readable root cause on NotesProviderUnavailableError
# ---------------------------------------------------------------------------

class TestNotesProviderUnavailableReason:
    """NotesProviderUnavailableError.reason must be set to a machine-readable
    value that distinguishes 'add the API key' from 'wait for quota reset'."""

    # ── default ──────────────────────────────────────────────────────────────

    def test_default_reason_is_provider_error(self):
        """Constructing without reason= defaults to 'provider_error' for
        backward compatibility with any existing callers."""
        err = NotesProviderUnavailableError("something failed")
        assert err.reason == "provider_error"

    def test_explicit_reason_stored(self):
        """reason= kwarg is stored and accessible on the instance."""
        err = NotesProviderUnavailableError("no key", reason="missing_credentials")
        assert err.reason == "missing_credentials"
        assert str(err) == "no key"

    # ── missing_credentials scenario ─────────────────────────────────────────

    @pytest.mark.anyio
    async def test_missing_credentials_reason_when_gemini_unconfigured(self):
        """When _available() is False the error must carry reason='missing_credentials'.
        Staff seeing this know to deploy the API key, not wait for quota."""
        sarvam = _make_sarvam(_SHORT)

        with (
            patch("app.services.ai.gemini_fallback._available", return_value=False),
            patch("app.services.ai.gemini_fallback.generate_gemini", new_callable=AsyncMock),
        ):
            with pytest.raises(NotesProviderUnavailableError) as exc_info:
                await generate_notes(
                    sarvam,
                    body_text="body " * 30,
                    chapter_title="No Key Chapter",
                    subject_name="Chemistry",
                    medium="en",
                )

        assert exc_info.value.reason == "missing_credentials", (
            "When GEMINI_API_KEY is absent the reason must be 'missing_credentials', "
            f"got {exc_info.value.reason!r}"
        )

    # ── provider_error scenario ───────────────────────────────────────────────

    @pytest.mark.anyio
    async def test_provider_error_reason_when_both_return_short_output(self):
        """When both providers return short output the error must carry
        reason='provider_error'. Staff seeing this know to wait for quota."""
        sarvam = _make_sarvam(_SHORT)

        with (
            patch("app.services.ai.gemini_fallback._available", return_value=True),
            patch(
                "app.services.ai.gemini_fallback.generate_gemini",
                new_callable=AsyncMock,
                return_value=_SHORT,
            ),
        ):
            with pytest.raises(NotesProviderUnavailableError) as exc_info:
                await generate_notes(
                    sarvam,
                    body_text="body " * 30,
                    chapter_title="Quota Chapter",
                    subject_name="Physics",
                    medium="en",
                )

        assert exc_info.value.reason == "provider_error", (
            "When both providers return short output the reason must be "
            f"'provider_error', got {exc_info.value.reason!r}"
        )

    @pytest.mark.anyio
    async def test_provider_error_reason_when_gemini_raises_exception(self):
        """When Gemini raises (quota/rate-limit/network) the error must carry
        reason='provider_error'."""
        sarvam = _make_sarvam(_SHORT)

        with (
            patch("app.services.ai.gemini_fallback._available", return_value=True),
            patch(
                "app.services.ai.gemini_fallback.generate_gemini",
                new_callable=AsyncMock,
                side_effect=RuntimeError("429 quota exceeded"),
            ),
        ):
            with pytest.raises(NotesProviderUnavailableError) as exc_info:
                await generate_notes(
                    sarvam,
                    body_text="body " * 30,
                    chapter_title="Quota Exception Chapter",
                    subject_name="Biology",
                    medium="en",
                )

        assert exc_info.value.reason == "provider_error", (
            "When Gemini throws an exception the reason must be 'provider_error', "
            f"got {exc_info.value.reason!r}"
        )

    # ── reason appears in progress log detail ─────────────────────────────────

    @pytest.mark.anyio
    async def test_progress_log_detail_contains_reason_token(self, tmp_path):
        """The progress log record written by process_pdf_entry() must include
        'reason=<value>' in the detail field so staff can grep by root cause."""
        import scripts.ahsec_ingest as ingest_mod

        tmp_progress = tmp_path / "progress.jsonl"
        mock_chapter = MagicMock()
        mock_chapter.id = "chapter-reason-test"
        mock_chapter.notes_en = ""
        mock_chapter.notes_as = ""
        mock_subj = MagicMock()
        mock_subj.id = "subject-xyz"

        entry = {
            "subject_name": "Physics",
            "subject_slug": "physics",
            "class_level": "11",
            "medium": "en",
            "pdf_url": "https://example.com/physics.pdf",
            "part_num": 1,
            "book_label": "Physics (E)",
        }
        fake_chapters = [{
            "chapter_num": 1,
            "title": "Physical World",
            "body_text": "body " * 50,
            "exercises_text": "",
        }]
        logged_records: list[dict] = []

        def _capture(key, status, detail="", chapter_id="", pdf_url="", medium=""):
            logged_records.append({"status": status, "detail": detail})

        with (
            patch.object(ingest_mod, "PROGRESS_FILE", tmp_progress),
            patch.object(ingest_mod, "upsert_subject", new_callable=AsyncMock, return_value=mock_subj),
            patch.object(ingest_mod, "extract_pdf_text", new_callable=AsyncMock, return_value=[
                {"page_num": 1, "text": "some text here " * 5},
            ]),
            patch.object(ingest_mod, "split_into_chapters", return_value=fake_chapters),
            patch.object(ingest_mod, "upsert_chapter", new_callable=AsyncMock, return_value=(mock_chapter, True)),
            patch.object(ingest_mod, "_log_progress", side_effect=_capture),
            patch.object(
                ingest_mod, "generate_notes", new_callable=AsyncMock,
                side_effect=NotesProviderUnavailableError(
                    "Gemini key missing", reason="missing_credentials"
                ),
            ),
        ):
            await ingest_mod.process_pdf_entry(
                entry, _make_sarvam(),
                force=True, dry_run=False, delay=0.0, done_keys=set(),
            )

        unavail = [r for r in logged_records if r["status"] == "notes_provider_unavailable"]
        assert len(unavail) == 1
        assert "reason=missing_credentials" in unavail[0]["detail"], (
            "Progress log detail must contain 'reason=missing_credentials' so "
            f"staff can grep it — got: {unavail[0]['detail']!r}"
        )
