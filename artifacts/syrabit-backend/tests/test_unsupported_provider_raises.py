"""Task #6 (2026-05-09) — V4 §12 no-silent-fallbacks regression.

After the retired LLM-shim package and one retired provider branch were
purged from ``llm.py`` (see ``docs/cleanup/2026-purge-log.md``), two
former fall-through paths now raise loudly instead of silently producing
a stale response. These tests pin the contract so a future "let me
restore the helpful default" patch cannot silently re-introduce a
fallback for an unknown provider name.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

import llm as llm_mod


def test_call_single_provider_unknown_raises_500_with_provider_name():
    """`_call_single_provider` must raise HTTPException(500) naming the
    unsupported provider (post-Task-#6 unsupported-provider raise)."""
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            llm_mod._call_single_provider(
                provider="some-future-shim",
                api_key="x",
                model="x",
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=8,
            )
        )
    assert excinfo.value.status_code == 500
    assert "some-future-shim" in str(excinfo.value.detail)
    assert "Task #6" in str(excinfo.value.detail)


def test_stream_dispatcher_has_loud_unsupported_provider_raise():
    """`_stream_from_provider` is a closure inside `call_llm_api_stream`
    (not exposed at module scope), so we pin the V4 §12 contract via
    source inspection: the streaming dispatcher's `else:` branch must
    raise `HTTPException(500)` and name the provider — never silently
    fall back to a default shim."""
    import inspect

    src = inspect.getsource(llm_mod.call_llm_api_stream)
    assert "streaming provider" in src and "is not supported" in src, (
        "call_llm_api_stream must surface unsupported providers loudly "
        "(V4 §12 no-silent-fallbacks; Task #6)."
    )
    assert "raise HTTPException(\n                status_code=500" in src or (
        "raise HTTPException" in src and "status_code=500" in src
    ), "unsupported-provider branch must raise HTTPException(500)."
    assert "{p_name!r}" in src, (
        "unsupported-provider raise must include the offending provider "
        "name in the detail (debuggability requirement)."
    )
