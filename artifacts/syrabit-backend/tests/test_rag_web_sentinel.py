"""Tasks #9 + #18 — regression guard for the web-search training-knowledge sentinel.

web_search_with_fallback() returns a sentinel dict instead of [] when both
DDG and Exa return zero results.  This prevents the 503-on-empty-web-results
failure mode while honouring V4 §12 (no silent fallbacks) — the sentinel is
always logged and fully auditable by the caller.

A future change that accidentally reverts to returning [] (re-introducing the
503 path), or corrupts the sentinel structure that ai_chat.py depends on,
will fail at least one test here before the change ships.

Run::

    python -m pytest tests/test_rag_web_sentinel.py -v
"""
from __future__ import annotations

import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._deps_stub import install_deps_stub  # noqa: E402

install_deps_stub()

import pytest
from unittest.mock import AsyncMock, patch

import rag


def _clear_web_cache():
    rag._WEB_SEARCH_CACHE.clear()


def _fake_llm_module(provider: str = "workers_ai") -> types.ModuleType:
    mod = types.ModuleType("llm")
    mod.select_provider = lambda *a, **kw: provider  # type: ignore[attr-defined]
    mod.call_search_rag_with_dispatch = AsyncMock(return_value=[])  # type: ignore[attr-defined]
    return mod


class TestWebSearchSentinel:

    def setup_method(self):
        _clear_web_cache()

    async def test_sentinel_returned_when_ddg_empty(self):
        """Core regression: DDG returns [] → sentinel is returned instead of []."""
        fake_llm = _fake_llm_module(provider="workers_ai")
        with patch.object(rag, "_ddg_search", AsyncMock(return_value=[])), \
             patch.dict("sys.modules", {"llm": fake_llm}):
            result = await rag.web_search_with_fallback(
                "completely obscure irrelevant query xyz123"
            )

        assert isinstance(result, list), f"Expected list, got {type(result)}"
        assert len(result) == 1, (
            f"Sentinel must be a single-item list; got {len(result)} items"
        )
        sentinel = result[0]
        assert sentinel.get("_source") == "training_knowledge", sentinel
        assert sentinel.get("_fallback") is True, sentinel
        assert "web_fallback_reason" in sentinel, sentinel

    async def test_sentinel_fallback_reason(self):
        """web_fallback_reason must be 'ddg_zero_results' exactly — ai_chat.py
        key-matches this string to switch to the training-knowledge system prompt."""
        fake_llm = _fake_llm_module(provider="workers_ai")
        with patch.object(rag, "_ddg_search", AsyncMock(return_value=[])), \
             patch.dict("sys.modules", {"llm": fake_llm}):
            result = await rag.web_search_with_fallback("sentinel reason check")

        assert result[0]["web_fallback_reason"] == "ddg_zero_results", (
            f"Unexpected reason: {result[0].get('web_fallback_reason')!r}"
        )

    async def test_sentinel_is_list_of_one(self):
        fake_llm = _fake_llm_module(provider="workers_ai")
        with patch.object(rag, "_ddg_search", AsyncMock(return_value=[])), \
             patch.dict("sys.modules", {"llm": fake_llm}):
            result = await rag.web_search_with_fallback("another empty query")

        assert len(result) == 1

    async def test_non_empty_ddg_never_produces_sentinel(self):
        """When DDG returns real results, the sentinel must NOT appear."""
        fake_results = [
            {"title": "Physics Notes", "href": "https://ncert.nic.in/1", "body": "Newton's laws"},
            {"title": "Chemistry",     "href": "https://ncert.nic.in/2", "body": "Periodic table"},
        ]
        fake_llm = _fake_llm_module(provider="workers_ai")
        with patch.object(rag, "_ddg_search", AsyncMock(return_value=fake_results)), \
             patch.dict("sys.modules", {"llm": fake_llm}):
            result = await rag.web_search_with_fallback("Newton laws physics AHSEC")

        assert len(result) > 0
        for item in result:
            assert item.get("_source") != "training_knowledge", (
                f"Sentinel appeared with real DDG data: {item!r}"
            )
            assert not item.get("_fallback"), (
                f"_fallback=True on real-result item: {item!r}"
            )

    async def test_sentinel_title_field_present(self):
        """The sentinel must carry a 'title' key — callers access result['title']."""
        fake_llm = _fake_llm_module(provider="workers_ai")
        with patch.object(rag, "_ddg_search", AsyncMock(return_value=[])), \
             patch.dict("sys.modules", {"llm": fake_llm}):
            result = await rag.web_search_with_fallback("title field check")

        assert "title" in result[0], f"Sentinel missing 'title' key: {result[0]!r}"

    async def test_sentinel_not_written_to_cache(self):
        """The cache layer must be skipped for sentinel so a later real search
        for the same query is not poisoned with the fallback."""
        fake_llm = _fake_llm_module(provider="workers_ai")
        with patch.object(rag, "_ddg_search", AsyncMock(return_value=[])), \
             patch.dict("sys.modules", {"llm": fake_llm}):
            await rag.web_search_with_fallback("cache poison check sentinel")

        for _key, (_ts, cached_results) in rag._WEB_SEARCH_CACHE.items():
            for item in cached_results:
                assert not item.get("_fallback"), (
                    f"Sentinel was written to the web cache: {item!r}"
                )


# ── Task #18 — Exa-launched-but-empty paths ───────────────────────────────────

class TestWebSearchSentinelExaPath:
    """Guard the sentinel when Exa IS launched (provider == 'exa_ai') but
    returns zero results or raises.

    The Exa task is started via asyncio.ensure_future before _ddg_search
    awaits; its results are merged after DDG returns.  When both layers are
    empty, the sentinel must still be returned — not [].

    If someone changes the except-block inside the Exa await branch to swallow
    the empty list silently and return [] early, one of these tests will catch
    it before the change ships.
    """

    def setup_method(self):
        _clear_web_cache()

    async def test_sentinel_when_exa_returns_empty_list(self):
        """DDG returns [], Exa task resolves to [] → all_results stays empty →
        sentinel is returned.  This is the most likely real-world failure mode:
        Exa's search_rag endpoint responds 200 but finds nothing."""
        import asyncio
        fake_llm = _fake_llm_module(provider="exa_ai")
        fake_llm.call_search_rag_with_dispatch = AsyncMock(return_value=[])

        with patch.object(rag, "_ddg_search", AsyncMock(return_value=[])), \
             patch.dict("sys.modules", {"llm": fake_llm}):
            result = await rag.web_search_with_fallback(
                "exa empty list sentinel test query"
            )

        assert isinstance(result, list), f"Expected list, got {type(result)}"
        assert len(result) == 1, (
            f"Sentinel must be a single-item list; got {len(result)} items: {result!r}"
        )
        sentinel = result[0]
        assert sentinel.get("_source") == "training_knowledge", sentinel
        assert sentinel.get("_fallback") is True, sentinel
        assert sentinel.get("web_fallback_reason") == "ddg_zero_results", sentinel

    async def test_sentinel_when_exa_raises_timeout(self):
        """DDG returns [], Exa task raises asyncio.TimeoutError → the except
        block in rag.py swallows it (non-fatal) → all_results stays empty →
        sentinel is returned.  Guards the 'non-fatal' path in lines 331-332."""
        import asyncio
        fake_llm = _fake_llm_module(provider="exa_ai")
        fake_llm.call_search_rag_with_dispatch = AsyncMock(
            side_effect=asyncio.TimeoutError("exa timed out")
        )

        with patch.object(rag, "_ddg_search", AsyncMock(return_value=[])), \
             patch.dict("sys.modules", {"llm": fake_llm}):
            result = await rag.web_search_with_fallback(
                "exa timeout sentinel test query"
            )

        assert isinstance(result, list), f"Expected list, got {type(result)}"
        assert len(result) == 1, (
            f"Sentinel must be a single-item list; got {len(result)} items: {result!r}"
        )
        sentinel = result[0]
        assert sentinel.get("_source") == "training_knowledge", sentinel
        assert sentinel.get("_fallback") is True, sentinel

    async def test_sentinel_when_exa_raises_generic_exception(self):
        """DDG returns [], Exa task raises a generic RuntimeError → caught by
        except Exception → all_results empty → sentinel returned.  Covers the
        broader exception branch, not just TimeoutError."""
        fake_llm = _fake_llm_module(provider="exa_ai")
        fake_llm.call_search_rag_with_dispatch = AsyncMock(
            side_effect=RuntimeError("exa connection reset")
        )

        with patch.object(rag, "_ddg_search", AsyncMock(return_value=[])), \
             patch.dict("sys.modules", {"llm": fake_llm}):
            result = await rag.web_search_with_fallback(
                "exa generic error sentinel test"
            )

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0].get("_fallback") is True

    async def test_exa_results_used_when_ddg_empty_but_exa_non_empty(self):
        """Inverse check: when DDG returns [] but Exa returns real results,
        the sentinel must NOT appear — Exa results alone are sufficient."""
        fake_llm = _fake_llm_module(provider="exa_ai")
        fake_llm.call_search_rag_with_dispatch = AsyncMock(return_value=[
            {"title": "Exa result", "url": "https://exa.example.com/1",
             "text": "Newton's laws of motion explained"},
        ])

        with patch.object(rag, "_ddg_search", AsyncMock(return_value=[])), \
             patch.dict("sys.modules", {"llm": fake_llm}):
            result = await rag.web_search_with_fallback(
                "Newton laws physics exa only result"
            )

        assert isinstance(result, list)
        assert len(result) > 0
        for item in result:
            assert item.get("_source") != "training_knowledge", (
                f"Sentinel appeared when Exa had real results: {item!r}"
            )
            assert not item.get("_fallback"), (
                f"_fallback=True on Exa-result item: {item!r}"
            )
