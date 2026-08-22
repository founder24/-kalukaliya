"""Regression tests for Workers AI-backed AHSEC notes generation."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_BACKEND_DIR = Path(__file__).parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from scripts.ahsec_ingest import NotesProviderUnavailableError, generate_notes

_SHORT = "too short"
_LONG = "## Topic\n\n" + "x" * 2600


def _worker(output: str = _LONG) -> MagicMock:
    client = MagicMock()
    client.generate = AsyncMock(return_value=output)
    return client


@pytest.mark.anyio
@pytest.mark.parametrize("medium,is_assamese", [("en", False), ("as", True)])
async def test_workers_ai_notes_generation_preserves_language_selection(
    medium, is_assamese
):
    worker = _worker()

    result = await generate_notes(
        worker,
        body_text="Chapter body text " * 20,
        chapter_title="Test Chapter",
        subject_name="Physics",
        medium=medium,
    )

    assert result == _LONG
    worker.generate.assert_awaited_once()
    assert worker.generate.await_args.kwargs["is_assamese"] is is_assamese
    assert worker.generate.await_args.kwargs["max_tokens"] == 4096


@pytest.mark.anyio
async def test_notes_retry_smaller_inputs_when_worker_output_is_too_short():
    worker = _worker(_SHORT)

    with pytest.raises(NotesProviderUnavailableError) as exc_info:
        await generate_notes(
            worker,
            body_text="Chapter body text " * 5000,
            chapter_title="Retry Chapter",
            subject_name="Physics",
            medium="en",
        )

    assert worker.generate.await_count == 3
    assert exc_info.value.reason == "provider_error"


@pytest.mark.anyio
async def test_notes_surface_worker_failure_after_all_retries():
    worker = _worker()
    worker.generate.side_effect = RuntimeError("Workers AI temporarily unavailable")

    with pytest.raises(NotesProviderUnavailableError) as exc_info:
        await generate_notes(
            worker,
            body_text="Chapter body text " * 20,
            chapter_title="Unavailable Chapter",
            subject_name="Chemistry",
            medium="as",
        )

    assert worker.generate.await_count == 3
    assert exc_info.value.reason == "provider_error"