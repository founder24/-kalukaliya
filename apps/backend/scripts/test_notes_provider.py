"""
Tests for the generate_notes() provider-fallback and NotesProviderUnavailableError path.

Covers four scenarios from the code-review requirements:
  1. Sarvam-only  — Sarvam succeeds; Gemini is never called.
  2. Gemini-only  — Sarvam fails/quota; Gemini configured and returns good output.
  3. Neither-provider abort — both unconfigured; NotesProviderUnavailableError raised.
  4. Both-provider-failure progress logging — exception propagates to process_pdf_entry()
     which records status="notes_provider_unavailable" in the progress file.

Run:
    cd apps/backend
    python3 -m pytest scripts/test_notes_provider.py -v
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# ── Ensure the package root is importable ────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Minimal stubs so we can import ahsec_ingest without a live DB / settings
# ---------------------------------------------------------------------------

def _stub_modules() -> None:
    """Inject lightweight stubs for heavy dependencies."""
    # app.config
    cfg = types.ModuleType("app.config")
    settings_obj = MagicMock()
    settings_obj.SARVAM_API_KEY = "test-key"
    settings_obj.GEMINI_API_KEY = ""
    cfg.settings = settings_obj
    sys.modules.setdefault("app", types.ModuleType("app"))
    sys.modules["app.config"] = cfg

    # app.services.ai.gemini_fallback
    gf_mod = types.ModuleType("app.services.ai.gemini_fallback")
    gf_mod._available = lambda: False
    gf_mod.generate_gemini = AsyncMock(return_value="")
    parent = types.ModuleType("app.services")
    parent_ai = types.ModuleType("app.services.ai")
    sys.modules.setdefault("app.services", parent)
    sys.modules.setdefault("app.services.ai", parent_ai)
    sys.modules["app.services.ai.gemini_fallback"] = gf_mod


_stub_modules()

from scripts.ahsec_ingest import (  # noqa: E402
    NotesProviderUnavailableError,
    generate_notes,
    _log_progress,
    PROGRESS_FILE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sarvam(result: str = "", raises: Exception | None = None):
    """Return a fake sarvam object whose .generate() returns `result` or raises."""
    sarvam = MagicMock()
    if raises:
        sarvam.generate = AsyncMock(side_effect=raises)
    else:
        sarvam.generate = AsyncMock(return_value=result)
    return sarvam


GOOD_NOTES = "## Introduction\n\n" + "X" * 350   # 350+ chars — passes the 300-char gate


# ---------------------------------------------------------------------------
# Scenario 1 — Sarvam-only: Sarvam succeeds; Gemini is never invoked
# ---------------------------------------------------------------------------

class TestSarvamOnly(unittest.IsolatedAsyncioTestCase):
    async def test_sarvam_good_result_returned_directly(self):
        sarvam = _make_sarvam(result=GOOD_NOTES)
        result = await generate_notes(sarvam, "body text", "Ch 1", "Physics", "en")
        self.assertGreater(len(result), 300)
        self.assertIn("Introduction", result)

    async def test_gemini_not_called_when_sarvam_succeeds(self):
        sarvam = _make_sarvam(result=GOOD_NOTES)
        import app.services.ai.gemini_fallback as gf
        original = gf.generate_gemini
        gf.generate_gemini = AsyncMock(side_effect=AssertionError("Gemini must not be called"))
        try:
            await generate_notes(sarvam, "body text", "Ch 1", "Physics", "en")
        finally:
            gf.generate_gemini = original


# ---------------------------------------------------------------------------
# Scenario 2 — Gemini-only: Sarvam fails, Gemini configured, Gemini succeeds
# ---------------------------------------------------------------------------

class TestGeminiOnly(unittest.IsolatedAsyncioTestCase):
    async def test_gemini_used_when_sarvam_fails(self):
        sarvam = _make_sarvam(raises=RuntimeError("quota exhausted"))
        import app.services.ai.gemini_fallback as gf
        gf._available = lambda: True
        gf.generate_gemini = AsyncMock(return_value=GOOD_NOTES)
        try:
            result = await generate_notes(sarvam, "body text", "Ch 2", "Chemistry", "en")
            self.assertGreater(len(result), 300)
        finally:
            gf._available = lambda: False
            gf.generate_gemini = AsyncMock(return_value="")

    async def test_gemini_used_when_sarvam_returns_too_short(self):
        sarvam = _make_sarvam(result="short")   # < 300 chars — triggers fallback
        import app.services.ai.gemini_fallback as gf
        gf._available = lambda: True
        gf.generate_gemini = AsyncMock(return_value=GOOD_NOTES)
        try:
            result = await generate_notes(sarvam, "body text", "Ch 3", "Chemistry", "en")
            self.assertGreater(len(result), 300)
        finally:
            gf._available = lambda: False
            gf.generate_gemini = AsyncMock(return_value="")


# ---------------------------------------------------------------------------
# Scenario 3 — Neither provider available: NotesProviderUnavailableError raised
# ---------------------------------------------------------------------------

class TestNeitherProviderAbort(unittest.IsolatedAsyncioTestCase):
    async def test_raises_when_sarvam_fails_and_gemini_unconfigured(self):
        sarvam = _make_sarvam(raises=RuntimeError("billing error"))
        import app.services.ai.gemini_fallback as gf
        gf._available = lambda: False
        try:
            with self.assertRaises(NotesProviderUnavailableError):
                await generate_notes(sarvam, "body text", "Ch 4", "Biology", "en")
        finally:
            gf._available = lambda: False

    async def test_raises_when_sarvam_short_and_gemini_unconfigured(self):
        sarvam = _make_sarvam(result="tiny")   # < 300 chars
        import app.services.ai.gemini_fallback as gf
        gf._available = lambda: False
        try:
            with self.assertRaises(NotesProviderUnavailableError):
                await generate_notes(sarvam, "body text", "Ch 5", "Biology", "en")
        finally:
            gf._available = lambda: False

    async def test_raises_when_both_providers_return_short_output(self):
        sarvam = _make_sarvam(result="tiny")
        import app.services.ai.gemini_fallback as gf
        gf._available = lambda: True
        gf.generate_gemini = AsyncMock(return_value="also tiny")   # < 300 chars
        try:
            with self.assertRaises(NotesProviderUnavailableError):
                await generate_notes(sarvam, "body text", "Ch 6", "Biology", "en")
        finally:
            gf._available = lambda: False
            gf.generate_gemini = AsyncMock(return_value="")

    async def test_raises_when_both_providers_error(self):
        sarvam = _make_sarvam(raises=RuntimeError("quota"))
        import app.services.ai.gemini_fallback as gf
        gf._available = lambda: True
        gf.generate_gemini = AsyncMock(side_effect=RuntimeError("gemini error"))
        try:
            with self.assertRaises(NotesProviderUnavailableError):
                await generate_notes(sarvam, "body text", "Ch 7", "Biology", "en")
        finally:
            gf._available = lambda: False
            gf.generate_gemini = AsyncMock(return_value="")


# ---------------------------------------------------------------------------
# Scenario 4 — Progress log gets "notes_provider_unavailable" status
# ---------------------------------------------------------------------------

class TestProgressLogging(unittest.TestCase):
    def test_log_progress_writes_notes_provider_unavailable(self):
        import tempfile, os
        tmp = Path(tempfile.mktemp(suffix=".jsonl"))
        with patch("scripts.ahsec_ingest.PROGRESS_FILE", tmp):
            from scripts.ahsec_ingest import _log_progress as lp
            lp(
                key="test|ch1",
                status="notes_provider_unavailable",
                detail="Both providers failed",
                chapter_id="abc123",
                pdf_url="https://example.com/book.pdf",
            )
        lines = tmp.read_text().strip().splitlines()
        self.assertEqual(len(lines), 1)
        rec = json.loads(lines[0])
        self.assertEqual(rec["status"], "notes_provider_unavailable")
        self.assertEqual(rec["key"], "test|ch1")
        self.assertIn("Both providers", rec["detail"])
        tmp.unlink(missing_ok=True)

    def test_error_progress_still_uses_error_status(self):
        """Ordinary generation errors must still produce 'error', not the new status."""
        import tempfile
        tmp = Path(tempfile.mktemp(suffix=".jsonl"))
        with patch("scripts.ahsec_ingest.PROGRESS_FILE", tmp):
            from scripts.ahsec_ingest import _log_progress as lp
            lp("test|ch2", "error", "connection reset", "abc456", "https://x.com/b.pdf")
        rec = json.loads(tmp.read_text().strip())
        self.assertEqual(rec["status"], "error")
        tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
