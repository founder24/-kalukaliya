"""End-to-end routing chain tests for Assamese chat (Task #270, rebalanced #281).

Verifies the full Sarvam → Vertex/Gemini → Workers AI IndicTrans2 fallback
chain. Task #281 rebalanced the assamese_rag_chat pool to **equal weights**
across all three providers (Bedrock removal across chat/content pools forced
the rotation to be redistributed):

  assamese_rag_chat pool: sarvam (1000) → vertex (1000) → workers_ai_indic (1000)

Tests cover:
  A. select_provider routing for assamese_rag_chat with lang="as"
  B. _dispatch_llm_for_feature: workers_ai_indic path calls call_indic_trans
  C. IndicTrans2 response validated: non-empty + Assamese Unicode U+0980–U+09FF
  D. call_with_provider_fallback fallback chain: sarvam → workers_ai_indic → vertex (2026-05-05)
     All order-sensitive tests use a forced deterministic select_provider to
     avoid flakiness from equal-weight random draw.
  E. English chain: 429 on azure_openai/vertex triggers next provider
     (Task #281 — Bedrock removed from english_rag_chat)
  F. workers_ai_indic guarded from non-Assamese feature pools
  G. Route pipeline: _assamese_translate_gemini_main_sarvam_polish IndicTrans2 path
  H. Live integration tests (gated by CF_AI_GATEWAY_ACCOUNT_ID env var)
     — exercises real Workers AI IndicTrans2 endpoint when credentials are present
"""
from __future__ import annotations

import asyncio
import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests._deps_stub import install_deps_stub

install_deps_stub()

# ── Helpers ──────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _has_assamese_script(text: str) -> bool:
    """Return True if *text* contains at least one Assamese/Bengali script char.

    Assamese script shares the Bengali Unicode block (U+0980–U+09FF), which
    covers all Assamese letters, matras, and digits.
    """
    return any("\u0980" <= ch <= "\u09FF" for ch in text)


def _force_select_provider(llm_module, sequence: list[str]):
    """Context helper: patch llm.select_provider to draw from *sequence* in order.

    Returns (original_fn, patched_fn) so the caller can restore after use.
    Each call pops the next item from sequence; the original is used as fallback
    when sequence is exhausted.
    """
    original = llm_module.select_provider
    _idx = {"i": 0}

    def _forced(feature, lang="", exclude=frozenset()):
        while _idx["i"] < len(sequence):
            candidate = sequence[_idx["i"]]
            _idx["i"] += 1
            if candidate not in exclude:
                return candidate
        return original(feature, lang=lang, exclude=exclude)

    llm_module.select_provider = _forced
    return original, _forced


# Realistic IndicTrans2-style Assamese output
_SAMPLE_ASSAMESE = "নমস্কাৰ, আপুনি কেনে আছে?"

# ── A. select_provider routing ────────────────────────────────────────────────

class TestSelectProviderAssamese:
    """select_provider returns the correct primary/fallback for assamese_rag_chat."""

    def test_lang_as_draws_from_assamese_rag_chat_pool(self):
        """With lang='as' and no exclusions, every selected provider must come
        from the assamese_rag_chat pool. Task #281 rebalanced the pool to
        equal-weight rotation (sarvam=vertex=workers_ai_indic=1000), so all
        three are valid draws and bedrock must never appear."""
        import llm

        providers_seen: set = set()
        for _ in range(50):
            p = llm.select_provider("assamese_rag_chat", lang="as")
            providers_seen.add(p)

        allowed = {"sarvam", "vertex", "workers_ai_indic"}
        assert providers_seen <= allowed, (
            f"Expected only {allowed} in assamese_rag_chat pool; got {providers_seen}"
        )
        assert "bedrock" not in providers_seen, (
            "Bedrock was removed from assamese_rag_chat in Task #281 — "
            f"must not be drawn; got {providers_seen}"
        )

    def test_sarvam_excluded_draws_workers_ai_indic(self):
        """3-leg chain (2026-05-05): when sarvam is excluded the next
        deterministic STRICT pick must be workers_ai_indic (weight 1000 vs
        vertex 100 — 10x ratio fires the strict-primary short-circuit)."""
        import llm

        providers_seen: set = set()
        for _ in range(20):
            p = llm.select_provider(
                "assamese_rag_chat", lang="as",
                exclude=frozenset({"sarvam"}),
            )
            providers_seen.add(p)

        assert providers_seen == {"workers_ai_indic"}, (
            f"Expected only workers_ai_indic when sarvam is excluded "
            f"(STRICT 10x primary handoff); got {providers_seen}"
        )
        assert "sarvam" not in providers_seen, (
            "sarvam must not appear when it is in the exclude set"
        )

    def test_all_three_legs_excluded_returns_none_strict_chain(self):
        """3-leg chain (2026-05-05): sarvam → workers_ai_indic → vertex.
        When all three legs are excluded the pool is exhausted and
        select_provider MUST return None. The last-resort downgrade to
        workers_ai_llama31_8b / generic workers_ai is forbidden because
        they emit non-Assamese (English / Hindi / mixed) output."""
        import llm

        p = llm.select_provider(
            "assamese_rag_chat", lang="as",
            exclude=frozenset({"sarvam", "workers_ai_indic", "vertex"}),
        )
        assert p in (None, ""), (
            f"Expected None for strict-chain exhaustion; got {p!r}. "
            f"workers_ai_llama31_8b / generic workers_ai forbidden on Assamese path."
        )

    def test_lang_en_excludes_sarvam_from_assamese_pool(self):
        """Sarvam is Assamese-only; when lang!='as' it must never be selected
        even if assamese_rag_chat is accidentally used."""
        import llm

        for _ in range(20):
            p = llm.select_provider("assamese_rag_chat", lang="en")
            assert p != "sarvam", (
                "Sarvam must be excluded from assamese_rag_chat when lang is not 'as'"
            )


# ── B. _dispatch_llm_for_feature: workers_ai_indic path ─────────────────────

class TestDispatchLlmForFeatureIndicTrans:
    """_dispatch_llm_for_feature with provider='workers_ai_indic' must call
    call_indic_trans and return its output unchanged."""

    def test_dispatch_workers_ai_indic_calls_indic_trans(self, monkeypatch):
        """When provider='workers_ai_indic' for a valid translation feature
        (e.g. assamese_content under Task #291 — was assamese_rag_chat under
        the superseded #270 design), _dispatch_llm_for_feature extracts the
        last user message and calls call_indic_trans with direction='en-indic'."""
        import llm

        calls: list[dict] = []

        async def _fake_indic_trans(text, *, direction="en-indic", **kwargs):
            calls.append({"text": text, "direction": direction})
            return _SAMPLE_ASSAMESE

        fake_mod = types.ModuleType("providers.workers_indic")
        fake_mod.call_indic_trans = _fake_indic_trans
        monkeypatch.setitem(sys.modules, "providers.workers_indic", fake_mod)

        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello, how are you?"},
        ]
        result = _run(
            llm._dispatch_llm_for_feature(
                messages, "workers_ai_indic", 512,
                feature="assamese_content",  # Task #291: chat path no longer permits IndicTrans2
            )
        )

        assert len(calls) == 1, "call_indic_trans must be called exactly once"
        assert calls[0]["text"] == "Hello, how are you?", (
            "Must pass the last user message as source text"
        )
        assert calls[0]["direction"] == "en-indic", (
            "Direction must be en-indic (English → Assamese)"
        )
        assert result == _SAMPLE_ASSAMESE

    def test_dispatch_workers_ai_indic_picks_last_user_message(self, monkeypatch):
        """When multiple user turns exist, the LAST user message is translated.
        Uses feature='assamese_content' since assamese_rag_chat no longer
        permits workers_ai_indic under Task #291."""
        import llm

        captured: list[str] = []

        async def _fake_indic_trans(text, *, direction="en-indic", **kwargs):
            captured.append(text)
            return "অসমীয়া উত্তৰ"

        fake_mod = types.ModuleType("providers.workers_indic")
        fake_mod.call_indic_trans = _fake_indic_trans
        monkeypatch.setitem(sys.modules, "providers.workers_indic", fake_mod)

        messages = [
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "First answer"},
            {"role": "user", "content": "Second question — translate this"},
        ]
        _run(
            llm._dispatch_llm_for_feature(
                messages, "workers_ai_indic", 256,
                feature="assamese_content",  # Task #291: chat path no longer permits IndicTrans2
            )
        )
        assert captured == ["Second question — translate this"], (
            "Must pick the last user message, not an earlier one"
        )

    def test_dispatch_workers_ai_indic_no_user_message_raises(self, monkeypatch):
        """If there is no user message in the conversation, a RuntimeError must
        be raised so call_with_provider_fallback can route to workers_ai.
        Uses feature='assamese_content' since assamese_rag_chat no longer
        permits workers_ai_indic under Task #291."""
        import llm

        fake_mod = types.ModuleType("providers.workers_indic")
        fake_mod.call_indic_trans = AsyncMock(return_value="never called")
        monkeypatch.setitem(sys.modules, "providers.workers_indic", fake_mod)

        with pytest.raises(RuntimeError, match="no user message"):
            _run(
                llm._dispatch_llm_for_feature(
                    [{"role": "system", "content": "system only"}],
                    "workers_ai_indic", 256,
                    feature="assamese_content",  # Task #291: chat path no longer permits IndicTrans2
                )
            )

    def test_dispatch_workers_ai_indic_valid_for_translate_feature(self, monkeypatch):
        """workers_ai_indic is also valid for feature='translate'."""
        import llm

        async def _fake_indic_trans(text, *, direction="en-indic", **kwargs):
            return "অনুবাদিত পাঠ"

        fake_mod = types.ModuleType("providers.workers_indic")
        fake_mod.call_indic_trans = _fake_indic_trans
        monkeypatch.setitem(sys.modules, "providers.workers_indic", fake_mod)

        result = _run(
            llm._dispatch_llm_for_feature(
                [{"role": "user", "content": "Translate this text"}],
                "workers_ai_indic", 256,
                feature="translate",
            )
        )
        assert result == "অনুবাদিত পাঠ"


# ── C. IndicTrans2 response validation ───────────────────────────────────────

class TestIndicTrans2ResponseValidation:
    """Validate that IndicTrans2 provider responses meet the Assamese script
    requirement: non-empty and contains Unicode U+0980–U+09FF characters."""

    def test_assamese_script_detection_positive(self):
        """_has_assamese_script returns True for text that contains Assamese chars."""
        assert _has_assamese_script(_SAMPLE_ASSAMESE)
        assert _has_assamese_script("নমস্কাৰ")
        assert _has_assamese_script("some text অসম more text")

    def test_assamese_script_detection_negative(self):
        """_has_assamese_script returns False for pure ASCII / Latin text."""
        assert not _has_assamese_script("Hello world")
        assert not _has_assamese_script("")
        assert not _has_assamese_script("1234 + 5678")

    def test_indic_trans_response_contains_assamese_script(self, monkeypatch):
        """When _dispatch_llm_for_feature calls workers_ai_indic and gets a response,
        that response must contain at least one Assamese Unicode character."""
        import llm

        realistic_outputs = [
            "নমস্কাৰ, আপুনি কেনে আছে?",
            "কাৰ্নোৰ উপপাদ্যই কয় যে কোনো তাপ ইঞ্জিন কাৰ্নো ইঞ্জিনতকৈ দক্ষ নহয়।",
            "অসমৰ ৰাজধানী দিছপুৰ।",
        ]

        for expected_output in realistic_outputs:
            async def _fake_indic_trans(text, *, direction="en-indic",
                                        _out=expected_output, **kw):
                return _out

            fake_mod = types.ModuleType("providers.workers_indic")
            fake_mod.call_indic_trans = _fake_indic_trans
            monkeypatch.setitem(sys.modules, "providers.workers_indic", fake_mod)

            result = _run(
                llm._dispatch_llm_for_feature(
                    [{"role": "user", "content": "test input"}],
                    "workers_ai_indic", 256,
                    feature="assamese_content",  # Task #291: chat path no longer permits IndicTrans2
                )
            )
            assert result, "IndicTrans2 result must be non-empty"
            assert _has_assamese_script(result), (
                f"IndicTrans2 result must contain Assamese script (U+0980–U+09FF); "
                f"got {result!r}"
            )

    def test_indic_trans_empty_response_propagates_as_empty_string(self, monkeypatch):
        """If IndicTrans2 returns an empty string, _dispatch_llm_for_feature passes
        it back unchanged so the caller can detect the failure and try the next
        provider. Uses feature='assamese_content' since assamese_rag_chat no
        longer permits workers_ai_indic under Task #291."""
        import llm

        async def _fake_indic_trans(text, *, direction="en-indic", **kw):
            return ""

        fake_mod = types.ModuleType("providers.workers_indic")
        fake_mod.call_indic_trans = _fake_indic_trans
        monkeypatch.setitem(sys.modules, "providers.workers_indic", fake_mod)

        result = _run(
            llm._dispatch_llm_for_feature(
                [{"role": "user", "content": "test"}],
                "workers_ai_indic", 256,
                feature="assamese_content",  # Task #291: chat path no longer permits IndicTrans2
            )
        )
        assert result == "", "Empty IndicTrans2 response must propagate unchanged"


# ── D. call_with_provider_fallback: Assamese chain fallthrough ────────────────

class TestCallWithProviderFallbackAssamese:
    """call_with_provider_fallback correctly falls through the assamese_rag_chat
    pool when earlier providers raise.

    All order-sensitive tests use _force_select_provider to produce a deterministic
    draw sequence, avoiding flakiness from weighted-random selection.
    """

    def test_sarvam_failure_falls_through_to_workers_ai_indic(self):
        """RuntimeError from sarvam must cause fallback to workers_ai_indic
        (2nd leg of the 3-leg chain re-introduced 2026-05-05).

        select_provider is forced to return sarvam first, workers_ai_indic
        second, so the test is deterministic regardless of weight ratios.
        """
        import llm

        call_order: list[str] = []
        original, _ = _force_select_provider(llm, ["sarvam", "workers_ai_indic", "vertex"])
        try:
            async def _attempt(provider: str) -> str:
                call_order.append(provider)
                if provider == "sarvam":
                    raise RuntimeError("Sarvam simulated failure")
                return f"response-from-{provider}"

            result = _run(
                llm.call_with_provider_fallback(
                    "assamese_rag_chat", "as", _attempt,
                )
            )
        finally:
            llm.select_provider = original

        assert call_order[0] == "sarvam", "sarvam must be tried first (forced sequence)"
        assert call_order[1] == "workers_ai_indic", (
            "workers_ai_indic must be the 2nd-leg fallback after sarvam fails"
        )
        assert result == "response-from-workers_ai_indic"

    def test_sarvam_and_workers_ai_indic_failure_falls_through_to_vertex(self):
        """When sarvam and workers_ai_indic both raise RuntimeError, the
        fallback must eventually reach vertex (3rd / last leg).

        select_provider is forced to emit sarvam → workers_ai_indic → vertex
        in that order so the test does not depend on random weights.
        """
        import llm

        call_order: list[str] = []
        original, _ = _force_select_provider(
            llm, ["sarvam", "workers_ai_indic", "vertex"]
        )
        try:
            async def _attempt(provider: str) -> str:
                call_order.append(provider)
                if provider in ("sarvam", "workers_ai_indic"):
                    raise RuntimeError(f"{provider} simulated failure")
                return f"vertex-response-from-{provider}"

            result = _run(
                llm.call_with_provider_fallback(
                    "assamese_rag_chat", "as", _attempt,
                )
            )
        finally:
            llm.select_provider = original

        assert call_order == ["sarvam", "workers_ai_indic", "vertex"], (
            f"Expected deterministic draw sarvam→workers_ai_indic→vertex; got {call_order}"
        )
        assert result == "vertex-response-from-vertex"

    def test_all_providers_fail_raises_runtime_error(self):
        """When all providers fail, call_with_provider_fallback must raise
        RuntimeError with an informative message."""
        import llm

        async def _always_fail(provider: str) -> str:
            raise RuntimeError(f"{provider} unavailable")

        with pytest.raises(RuntimeError, match="All providers exhausted"):
            _run(
                llm.call_with_provider_fallback(
                    "assamese_rag_chat", "as", _always_fail,
                    max_attempts=10,
                )
            )

    def test_first_success_stops_fallback_chain(self):
        """Once a provider returns successfully, no further providers are tried."""
        import llm

        call_order: list[str] = []

        async def _attempt(provider: str) -> str:
            call_order.append(provider)
            return "Assamese answer"

        result = _run(
            llm.call_with_provider_fallback(
                "assamese_rag_chat", "as", _attempt,
            )
        )
        assert result == "Assamese answer"
        assert len(call_order) == 1, (
            "Only one provider must be called when the first attempt succeeds"
        )

    def test_failed_provider_excluded_from_subsequent_draws(self):
        """A provider that raises must not be drawn again in the same request.

        We force sarvam first, then workers_ai_indic. After sarvam raises
        RuntimeError, workers_ai_indic must succeed without sarvam being
        re-drawn.
        """
        import llm

        call_order: list[str] = []
        original, _ = _force_select_provider(llm, ["sarvam", "workers_ai_indic"])
        try:
            async def _attempt(provider: str) -> str:
                call_order.append(provider)
                if provider == "sarvam":
                    raise RuntimeError("sarvam down")
                return "ok"

            _run(llm.call_with_provider_fallback("assamese_rag_chat", "as", _attempt))
        finally:
            llm.select_provider = original

        sarvam_count = call_order.count("sarvam")
        assert sarvam_count == 1, (
            f"sarvam appeared {sarvam_count} times — must not be retried after failing"
        )


# ── E. English chain: 429 on azure_openai/vertex triggers next provider ─────
# Task #281 rebalanced english_rag_chat to ["azure_openai", "vertex",
# "workers_ai"] (equal-weight rotation, Bedrock removed). The fallback
# semantics being tested here are unchanged — only the providers that
# participate in the rotation differ.

class TestEnglishChain429Fallback:
    """429 HTTP errors on azure_openai and vertex must trigger provider removal
    from the english_rag_chat pool so the next provider is tried."""

    def _make_429_error(self):
        import httpx
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        return httpx.HTTPStatusError("429 Too Many Requests",
                                     request=MagicMock(), response=mock_resp)

    def test_azure_openai_429_excluded_from_subsequent_draws(self):
        """HTTP 429 from azure_openai must exclude it and the request must
        succeed via vertex or workers_ai without azure_openai being retried.

        select_provider is forced to draw azure_openai first to avoid flakiness
        from the equal-weight (1000:1000:1000) random draw.
        """
        import llm

        call_order: list[str] = []
        err_429 = self._make_429_error()
        original, _ = _force_select_provider(llm, ["azure_openai", "vertex"])
        try:
            async def _attempt(provider: str) -> str:
                call_order.append(provider)
                if provider == "azure_openai":
                    raise err_429
                return f"response-from-{provider}"

            result = _run(
                llm.call_with_provider_fallback("english_rag_chat", "en", _attempt)
            )
        finally:
            llm.select_provider = original

        assert call_order[0] == "azure_openai", "azure_openai must be first (forced)"
        assert call_order.count("azure_openai") == 1, (
            "azure_openai must not be retried after a 429"
        )
        assert result.startswith("response-from-"), "Must return a successful result"

    def test_azure_openai_and_vertex_429_falls_through_to_workers_ai(self):
        """HTTP 429 on both azure_openai and vertex must ultimately reach workers_ai."""
        import llm

        call_order: list[str] = []
        import httpx
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        err_429 = httpx.HTTPStatusError("429", request=MagicMock(), response=mock_resp)

        original, _ = _force_select_provider(
            llm, ["azure_openai", "vertex", "workers_ai"]
        )
        try:
            async def _attempt(provider: str) -> str:
                call_order.append(provider)
                if provider in ("azure_openai", "vertex"):
                    raise err_429
                return f"response-from-{provider}"

            result = _run(
                llm.call_with_provider_fallback("english_rag_chat", "en", _attempt)
            )
        finally:
            llm.select_provider = original

        assert call_order == ["azure_openai", "vertex", "workers_ai"], (
            f"Expected azure_openai→vertex→workers_ai; got {call_order}"
        )
        assert result == "response-from-workers_ai"

    def test_429_provider_not_retried_in_same_request(self):
        """A provider that returned 429 must not be drawn again in the same request."""
        import llm

        call_order: list[str] = []
        import httpx
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        err_429 = httpx.HTTPStatusError("429", request=MagicMock(), response=mock_resp)

        original, _ = _force_select_provider(llm, ["azure_openai", "vertex"])
        try:
            async def _attempt(provider: str) -> str:
                call_order.append(provider)
                if provider == "azure_openai":
                    raise err_429
                return "ok"

            _run(llm.call_with_provider_fallback("english_rag_chat", "en", _attempt))
        finally:
            llm.select_provider = original

        azure_count = call_order.count("azure_openai")
        assert azure_count == 1, (
            f"azure_openai appeared {azure_count} times — should only be tried once "
            "after a 429 (exclude logic broken)"
        )


# ── F. workers_ai_indic guarded from non-Assamese features ───────────────────

class TestIndicTrans2FeatureGuard:
    """workers_ai_indic must raise RuntimeError when used with a feature
    outside _INDICTRANS_VALID_FEATURES (english_rag_chat, content, safety, etc.)."""

    @pytest.mark.parametrize("feature", [
        "english_rag_chat",
        "content",
        "safety",
        "tts",
        "stt",
        # assamese_rag_chat REMOVED from the rejected set 2026-05-05 —
        # workers_ai_indic is now a permitted 2nd-leg fallback in the
        # 3-leg Assamese chat chain (sarvam → workers_ai_indic → vertex).
    ])
    def test_workers_ai_indic_rejected_for_non_assamese_features(self, feature, monkeypatch):
        """_dispatch_llm_for_feature must raise RuntimeError immediately when
        provider='workers_ai_indic' is drawn for a feature outside
        _INDICTRANS_VALID_FEATURES."""
        import llm

        fake_mod = types.ModuleType("providers.workers_indic")
        fake_mod.call_indic_trans = AsyncMock(return_value="should-never-be-called")
        monkeypatch.setitem(sys.modules, "providers.workers_indic", fake_mod)

        with pytest.raises(RuntimeError, match="not valid for feature"):
            _run(
                llm._dispatch_llm_for_feature(
                    [{"role": "user", "content": "some question"}],
                    "workers_ai_indic", 256,
                    feature=feature,
                )
            )

        fake_mod.call_indic_trans.assert_not_awaited()

    @pytest.mark.parametrize("feature", [
        # 2026-05-05 — assamese_rag_chat re-added to the accepted set as
        # the 2nd leg of the 3-leg chain (sarvam → workers_ai_indic →
        # vertex). assamese_content (note generation) and translate
        # (explicit MT) remain in the accepted set as before.
        "assamese_content",
        "translate",
        "assamese_rag_chat",
    ])
    def test_workers_ai_indic_accepted_for_valid_assamese_features(self, feature, monkeypatch):
        """_dispatch_llm_for_feature must NOT raise for valid Assamese features."""
        import llm

        async def _fake_indic_trans(text, *, direction="en-indic", **kw):
            return _SAMPLE_ASSAMESE

        fake_mod = types.ModuleType("providers.workers_indic")
        fake_mod.call_indic_trans = _fake_indic_trans
        monkeypatch.setitem(sys.modules, "providers.workers_indic", fake_mod)

        result = _run(
            llm._dispatch_llm_for_feature(
                [{"role": "user", "content": "English input text"}],
                "workers_ai_indic", 256,
                feature=feature,
            )
        )
        assert result == _SAMPLE_ASSAMESE
        assert _has_assamese_script(result), (
            "Result from valid assamese feature must contain Assamese script"
        )


# ── G. Route pipeline: _assamese_translate_gemini_main_sarvam_polish ──────────

class TestAssameseTranslatePipelineIndicTrans2Path:
    """Tests that exercise the IndicTrans2 path inside the ai_chat.py translation
    pipeline (_assamese_translate_gemini_main_sarvam_polish).

    The pipeline is:
      Step 0 (primary):  Sarvam /translate
      Step 1 (fallback): Workers AI IndicTrans2 → Gemini (if IndicTrans2 fails)
      Step 2 (polish):   Sarvam-m chat (optional, for long text)

    These tests stub Sarvam translate as unavailable so IndicTrans2 (Step 1) fires.
    """

    def test_indictrans2_fires_when_sarvam_translate_unavailable(self, monkeypatch):
        """When deps.sarvam_translate_client is None (Sarvam translate down),
        the pipeline falls through to Workers AI IndicTrans2 and returns
        Assamese text."""
        from routes import ai_chat as chat_mod

        indic_calls: list[str] = []

        async def _fake_indic_trans(text, *, direction="en-indic", **kw):
            indic_calls.append(text)
            return _SAMPLE_ASSAMESE

        fake_indic_mod = types.ModuleType("providers.workers_indic")
        fake_indic_mod.call_indic_trans = _fake_indic_trans
        monkeypatch.setitem(sys.modules, "providers.workers_indic", fake_indic_mod)

        import deps
        monkeypatch.setattr(deps, "sarvam_translate_client", None, raising=False)
        monkeypatch.setattr(deps, "sarvam_client", None, raising=False)
        monkeypatch.setattr(deps, "sarvam_llm_client", None, raising=False)

        chat_mod.redis_client = None

        src = "Carnot's theorem states that no heat engine is more efficient."
        result = _run(
            chat_mod._assamese_translate_gemini_main_sarvam_polish(
                src, target_lang_code="as-IN"
            )
        )

        assert len(indic_calls) == 1, "IndicTrans2 must be called exactly once"
        assert indic_calls[0] == src, (
            "IndicTrans2 must receive the original English source text"
        )
        assert result == _SAMPLE_ASSAMESE, (
            "Pipeline must return IndicTrans2 output when Sarvam is unavailable"
        )
        assert _has_assamese_script(result), (
            "IndicTrans2 output must contain Assamese Unicode characters"
        )

    def test_indictrans2_fires_when_sarvam_translate_returns_http_error(self, monkeypatch):
        """When Sarvam /translate returns non-200, the pipeline falls through to
        IndicTrans2 as the Step 1 provider."""
        from routes import ai_chat as chat_mod

        class _FakeResp:
            def __init__(self, status):
                self.status_code = status
            def json(self):
                return {"error": "service unavailable"}

        indic_calls: list[str] = []

        async def _fake_indic_trans(text, *, direction="en-indic", **kw):
            indic_calls.append(text)
            return "ৰাজধানী দিছপুৰ"

        fake_sarvam_tc = MagicMock()
        fake_sarvam_tc.post = AsyncMock(return_value=_FakeResp(503))

        fake_indic_mod = types.ModuleType("providers.workers_indic")
        fake_indic_mod.call_indic_trans = _fake_indic_trans
        monkeypatch.setitem(sys.modules, "providers.workers_indic", fake_indic_mod)

        import deps
        monkeypatch.setattr(deps, "sarvam_translate_client", fake_sarvam_tc, raising=False)
        monkeypatch.setattr(deps, "sarvam_client", None, raising=False)
        monkeypatch.setattr(deps, "sarvam_llm_client", None, raising=False)

        chat_mod.redis_client = None

        result = _run(
            chat_mod._assamese_translate_gemini_main_sarvam_polish(
                "Capital of Assam", target_lang_code="as-IN"
            )
        )

        assert len(indic_calls) == 1, "IndicTrans2 must fire as fallback for Sarvam 503"
        assert result == "ৰাজধানী দিছপুৰ"
        assert _has_assamese_script(result)

    def test_indictrans2_result_validated_non_empty_assamese(self, monkeypatch):
        """The translation pipeline must return non-empty Assamese-script text
        when IndicTrans2 succeeds, satisfying the U+0980–U+09FF validation contract."""
        from routes import ai_chat as chat_mod

        test_pairs = [
            ("What is photosynthesis?", "সালোক সংশ্লেষণ হৈছে এটা প্ৰক্ৰিয়া।"),
            ("Newton's first law of motion", "নিউটনৰ গতিৰ প্ৰথম সূত্ৰ।"),
        ]

        import deps
        monkeypatch.setattr(deps, "sarvam_translate_client", None, raising=False)
        monkeypatch.setattr(deps, "sarvam_client", None, raising=False)
        monkeypatch.setattr(deps, "sarvam_llm_client", None, raising=False)
        chat_mod.redis_client = None

        for english_src, expected_assamese in test_pairs:
            async def _fake_indic_trans(text, *, direction="en-indic",
                                        _out=expected_assamese, **kw):
                return _out

            fake_indic_mod = types.ModuleType("providers.workers_indic")
            fake_indic_mod.call_indic_trans = _fake_indic_trans
            monkeypatch.setitem(sys.modules, "providers.workers_indic", fake_indic_mod)

            result = _run(
                chat_mod._assamese_translate_gemini_main_sarvam_polish(
                    english_src, target_lang_code="as-IN"
                )
            )
            assert result, f"Result must be non-empty for input {english_src!r}"
            assert _has_assamese_script(result), (
                f"Result {result!r} must contain Assamese Unicode (U+0980–U+09FF)"
            )

    @pytest.mark.skip(reason=(
        "Task #270 design (Gemini-as-translate-fallback) was superseded by "
        "Task #291: IndicTrans2 is the SOLE translator and Vertex is polish-"
        "only. IndicTrans2 failure now correctly returns '' — covered by "
        "tests/test_assamese_translation_polish_contract.py::"
        "test_translation_returns_empty_when_indictrans2_fails."
    ))
    def test_gemini_fallback_fires_when_indictrans2_fails(self, monkeypatch):
        """When IndicTrans2 raises RuntimeError, the pipeline must fall through to
        Gemini (Tier B) and return its Assamese output."""
        from routes import ai_chat as chat_mod

        gemini_assamese = "কাৰ্নোৰ উপপাদ্য।"

        async def _fake_failing_indic_trans(text, *, direction="en-indic", **kw):
            raise RuntimeError("workers_indic: CF API returned 503")

        fake_indic_mod = types.ModuleType("providers.workers_indic")
        fake_indic_mod.call_indic_trans = _fake_failing_indic_trans
        monkeypatch.setitem(sys.modules, "providers.workers_indic", fake_indic_mod)

        gemini_calls: list = []

        # Vertex-only Gemini auth (2026-05-03): stub `_call_vertex_chat` (signature
        # `messages, model, max_tokens` — no api_key arg) instead of the deleted
        # `_call_gemini`.
        async def _fake_gemini(messages, model, max_tokens):
            gemini_calls.append(messages)
            return gemini_assamese

        import llm as _llm
        monkeypatch.setattr(_llm, "_call_vertex_chat", _fake_gemini, raising=False)

        import deps
        monkeypatch.setattr(deps, "sarvam_translate_client", None, raising=False)
        monkeypatch.setattr(deps, "sarvam_client", None, raising=False)
        monkeypatch.setattr(deps, "sarvam_llm_client", None, raising=False)
        chat_mod.redis_client = None

        result = _run(
            chat_mod._assamese_translate_gemini_main_sarvam_polish(
                "Carnot's theorem",
                target_lang_code="as-IN",
            )
        )

        assert len(gemini_calls) == 1, "Gemini must be called as Tier B fallback"
        assert result == gemini_assamese
        assert _has_assamese_script(result)


# ── H. call_llm_api_chat: end-to-end feature dispatch ────────────────────────

class TestCallLlmApiChatAssamese:
    """call_llm_api_chat with lang='as' routes through the assamese_rag_chat
    feature key and returns a valid Assamese answer."""

    def test_call_llm_api_chat_lang_as_uses_assamese_feature(self, monkeypatch):
        """When lang='as', call_llm_api_chat must dispatch via
        'assamese_rag_chat' (not 'english_rag_chat')."""
        import llm

        dispatched_features: list[str] = []

        async def _fake_fallback(feature, lang, attempt_fn, max_attempts=6):
            dispatched_features.append(feature)
            return "নমস্কাৰ"

        monkeypatch.setattr(llm, "call_with_provider_fallback", _fake_fallback,
                            raising=False)

        result = _run(
            llm.call_llm_api_chat(
                [{"role": "user", "content": "কি কবা?"}],
                max_tokens=256,
                lang="as",
            )
        )
        assert dispatched_features == ["assamese_rag_chat"], (
            f"Expected 'assamese_rag_chat' feature; got {dispatched_features}"
        )
        assert result == "নমস্কাৰ"

    def test_call_llm_api_chat_lang_en_uses_english_feature(self, monkeypatch):
        """When lang='en', call_llm_api_chat must dispatch via 'english_rag_chat'."""
        import llm

        dispatched_features: list[str] = []

        async def _fake_fallback(feature, lang, attempt_fn, max_attempts=6):
            dispatched_features.append(feature)
            return "Hello"

        monkeypatch.setattr(llm, "call_with_provider_fallback", _fake_fallback,
                            raising=False)

        _run(
            llm.call_llm_api_chat(
                [{"role": "user", "content": "Hello?"}],
                max_tokens=256,
                lang="en",
            )
        )
        assert dispatched_features == ["english_rag_chat"], (
            f"Expected 'english_rag_chat' for lang='en'; got {dispatched_features}"
        )

    def test_call_llm_api_chat_assamese_strict_chain_raises_503(self, monkeypatch):
        """3-leg strict-chain integration (2026-05-05). When all three legs
        (sarvam, workers_ai_indic, vertex) fail, call_llm_api_chat(lang='as')
        must:
          1. Surface a clean HTTPException 503 (no wrong-language fallback).
          2. NEVER dispatch to workers_ai_llama31_8b or the generic
             workers_ai shorthand — both would emit non-Assamese
             (English / Hindi / mixed) output for an Assamese prompt,
             which is worse for UX than an honest error.
        """
        from fastapi import HTTPException
        import llm

        monkeypatch.setattr(llm, "_SARVAM_PROVIDERS", [], raising=False)

        called_providers: list[str] = []

        async def _fake_dispatch(messages, provider, max_tokens, *, feature=""):
            called_providers.append(provider)
            if provider in ("sarvam", "workers_ai_indic", "vertex"):
                raise RuntimeError(f"no {provider} key")
            # Forbidden — any other provider on this path is a silent
            # contract violation (workers_ai_llama31_8b / generic
            # workers_ai both emit wrong-language output). Raise a
            # distinctive marker so the test fails loudly.
            raise AssertionError(
                f"Forbidden provider {provider!r} reached call_llm_api_chat "
                f"on the assamese_rag_chat strict chain"
            )

        monkeypatch.setattr(llm, "_dispatch_llm_for_feature", _fake_dispatch,
                            raising=False)

        with pytest.raises(HTTPException) as exc_info:
            _run(
                llm.call_llm_api_chat(
                    [{"role": "user", "content": "What is photosynthesis?"}],
                    max_tokens=256,
                    lang="as",
                )
            )
        assert exc_info.value.status_code == 503, (
            f"Strict-chain exhaustion must surface 503; got "
            f"{exc_info.value.status_code}"
        )

        # workers_ai_indic IS now permitted on the chat path as the 2nd leg.
        assert "workers_ai_indic" in called_providers, (
            "workers_ai_indic must be reached on the chat path (3-leg chain); "
            f"called_providers={called_providers}"
        )
        assert "workers_ai_llama31_8b" not in called_providers, (
            "workers_ai_llama31_8b must NOT be reached on the chat path; "
            f"called_providers={called_providers}"
        )
        assert "workers_ai" not in called_providers, (
            "Generic workers_ai must NOT be reached on the assamese_rag_chat path "
            f"(Task #291 — wrong-language fallback forbidden); "
            f"called_providers={called_providers}"
        )


# ── I. Live integration tests (require CF credentials) ───────────────────────

_CF_ACCOUNT_ID = os.environ.get("CF_AI_GATEWAY_ACCOUNT_ID", "").strip()
_CF_API_TOKEN  = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
_LIVE_ENABLED  = bool(_CF_ACCOUNT_ID and _CF_API_TOKEN)

_live_skip = pytest.mark.skipif(
    not _LIVE_ENABLED,
    reason=(
        "Live CF Workers AI tests skipped: "
        "CF_AI_GATEWAY_ACCOUNT_ID and CLOUDFLARE_API_TOKEN must both be set"
    ),
)


def _reset_indic_client() -> None:
    """Reset the cached httpx.AsyncClient in providers.workers_indic.

    The module caches a single AsyncClient globally.  Each `asyncio.run()` call
    creates a *new* event loop.  When the next test tries to reuse the cached
    client (bound to a previous loop), httpcore raises
    'bound to a different event loop'.

    Setting the module-level ``_client`` to None forces the next
    ``_get_client()`` call to create a fresh client in the current loop.
    """
    try:
        import providers.workers_indic as _wi
        _wi._client = None
    except Exception:
        pass


@pytest.mark.live
class TestLiveIndicTrans2:
    """Live integration tests against the real CF Workers AI IndicTrans2 endpoint.

    Skipped unless CF_AI_GATEWAY_ACCOUNT_ID and CLOUDFLARE_API_TOKEN are set.

    Background (Task #270 finding):
      CF Workers AI IndicTrans2 (@cf/ai4bharat/indictrans2-en-indic-1b) has been
      observed to return Devanagari (Hindi, U+0900–U+097F) rather than Bengali-
      script Assamese (U+0980–U+09FF) for target_lang="asm_Beng".

    Production fix (providers/workers_indic.py):
      call_indic_trans() now validates the output script for direction="en-indic".
      If no U+0980–U+09FF characters are found it raises RuntimeError so the
      caller's fallback chain (Gemini Tier B in _assamese_translate_gemini_main_
      sarvam_polish, or the next provider in call_with_provider_fallback) can
      produce correct Assamese output.

    Test structure:
      I1 — CF endpoint raises due to wrong script: documents the script-validation
           behaviour added to providers/workers_indic.py.
      I2 — translation pipeline produces Assamese (U+0980–U+09FF): IndicTrans2
           raises → Gemini fires as Tier B → strict Assamese assertion.  This
           is the primary live acceptance test for Task #270.
      I3 — routing diagnostic: verifies workers_ai_indic IS reached in the
           fallback chain; accepts the RuntimeError raised by script validation.
      I4 — call_llm_api_chat(lang='as') live test: forces sarvam+vertex out,
           exercises the translation pipeline for Assamese chat, strict assertion.

    Event-loop isolation: each test calls _reset_indic_client() before running.
    """

    @_live_skip
    def test_live_indic_trans_raises_for_wrong_script(self):
        """I1 — script validation: call_indic_trans raises RuntimeError when
        the CF endpoint returns Devanagari instead of Bengali-script Assamese.

        This test documents and confirms the script-validation logic added to
        providers/workers_indic.py (Task #270): if CF returns non-U+0980–U+09FF
        output for direction='en-indic', RuntimeError is raised with a message
        describing the wrong script.  This lets callers (Gemini Tier B, etc.)
        fall through to a provider that produces correct Assamese.
        """
        _reset_indic_client()
        import asyncio
        from providers.workers_indic import ENABLED

        assert ENABLED, "workers_indic.ENABLED is False — check env vars"

        src = "Hello, how are you?"

        async def _run_live():
            from providers.workers_indic import call_indic_trans
            return await call_indic_trans(src, direction="en-indic")

        try:
            result = asyncio.run(_run_live())
            # If CF is fixed and returns real Assamese — test passes
            assert _has_assamese_script(result), (
                f"call_indic_trans returned {result!r} which is non-empty but "
                f"lacks Bengali-script Assamese (U+0980–U+09FF). "
                f"Script validation should have raised RuntimeError — "
                f"check providers/workers_indic.py validation logic."
            )
        except RuntimeError as exc:
            # Expected path: CF returned Devanagari, script validation raised.
            # Confirm the error message describes the wrong-script situation.
            assert "non-Assamese script" in str(exc) or "U+0980" in str(exc), (
                f"RuntimeError message should reference non-Assamese script; "
                f"got: {exc}"
            )
            # This confirms the validation logic works correctly.

    @pytest.mark.skip(reason=(
        "Task #270 live design (Gemini Tier B fallback after IndicTrans2 "
        "raises) was superseded by Task #291. IndicTrans2 raise now returns "
        "'' and the caller falls back to its own original-text path — there "
        "is no in-pipeline Gemini-as-translate step. Re-enable only if a "
        "future task reintroduces a translate fallback tier."
    ))
    @_live_skip
    def test_live_translation_pipeline_produces_assamese_script(self):
        """I2 — PRIMARY live acceptance test (Task #270):
        _assamese_translate_gemini_main_sarvam_polish with Sarvam disabled
        → IndicTrans2 hits real CF endpoint (raises for wrong script)
        → Gemini Tier B fires (stubbed to return Assamese)
        → result must contain Bengali-script Assamese (U+0980–U+09FF).

        Hybrid approach:
          - IndicTrans2 (Tier A): LIVE HTTP call to CF Workers AI.  The script
            validation in providers/workers_indic.py raises RuntimeError when
            CF returns Devanagari, which is the expected current behavior.
          - Gemini (Tier B): stubbed to return a known Assamese string.  Gemini's
            correctness is verified separately; here we verify that (a) the
            pipeline routes to Gemini after IndicTrans2 raises, and (b) the final
            result satisfies the U+0980–U+09FF contract.

        What this test confirms end-to-end:
          1. CF Workers AI endpoint is reachable (live).
          2. Script validation fires and raises for Devanagari output (live).
          3. Pipeline correctly falls through to Gemini Tier B (routing).
          4. Result contains strict Assamese Unicode U+0980–U+09FF (no xfail).
        """
        _reset_indic_client()
        import asyncio
        import llm as _llm
        import deps
        from routes import ai_chat as chat_mod

        _ASSAMESE_STUB = "অসমৰ ৰাজধানী দিছপুৰ।"

        gemini_calls: list = []

        async def _fake_gemini(messages, model, max_tokens):
            # Tier B Gemini call confirmed (Vertex SA path post 2026-05-03);
            # signature is `(messages, model, max_tokens)` — no api_key arg.
            gemini_calls.append(model)
            return _ASSAMESE_STUB

        original_sarvam_tc  = getattr(deps, "sarvam_translate_client", None)
        original_sarvam_c   = getattr(deps, "sarvam_client", None)
        original_sarvam_llm = getattr(deps, "sarvam_llm_client", None)
        original_redis      = chat_mod.redis_client
        original_vertex_fn  = getattr(_llm, "_call_vertex_chat", None)
        try:
            deps.sarvam_translate_client = None
            deps.sarvam_client           = None
            deps.sarvam_llm_client       = None
            chat_mod.redis_client        = None
            # Vertex-only Gemini auth (2026-05-03): patch `_call_vertex_chat`.
            _llm._call_vertex_chat       = _fake_gemini

            result = asyncio.run(
                chat_mod._assamese_translate_gemini_main_sarvam_polish(
                    "The capital of Assam is Dispur.",
                    target_lang_code="as-IN",
                )
            )
        finally:
            deps.sarvam_translate_client = original_sarvam_tc
            deps.sarvam_client           = original_sarvam_c
            deps.sarvam_llm_client       = original_sarvam_llm
            chat_mod.redis_client        = original_redis
            if original_vertex_fn is not None:
                _llm._call_vertex_chat = original_vertex_fn

        # IndicTrans2 was invoked live (providers/workers_indic.py log confirms:
        # "en-indic returned no Assamese script — CF endpoint returning Devanagari").
        # Script validation raised RuntimeError → pipeline fell through to Gemini.
        assert len(gemini_calls) == 1, (
            "Gemini (Tier B) must be triggered after IndicTrans2 raises for wrong "
            f"script (CF returned Devanagari, script validation raised); "
            f"gemini_calls={gemini_calls}"
        )
        assert result == _ASSAMESE_STUB, (
            f"Pipeline must return Gemini's Assamese output; got {result!r}"
        )
        assert _has_assamese_script(result), (
            f"Pipeline result {result!r} must contain Assamese Unicode U+0980–U+09FF"
        )

    @_live_skip
    def test_live_workers_ai_indic_routing_confirmed(self):
        """I3 — routing diagnostic: when sarvam is excluded, the 3-leg
        chain (2026-05-05) correctly routes to workers_ai_indic as the
        2nd leg (routing wiring is correct).

        Because the script-validation fix causes workers_ai_indic to raise
        RuntimeError when CF returns Devanagari, this test verifies the routing
        reaches workers_ai_indic (either successfully or via the known wrong-
        script error). Provider selection is forced deterministically.
        """
        _reset_indic_client()
        import asyncio
        import llm

        providers_attempted: list[str] = []
        errors_seen: list[str] = []

        original, _ = _force_select_provider(
            llm, ["sarvam", "workers_ai_indic", "vertex"]
        )
        try:
            async def _attempt(provider: str) -> str:
                providers_attempted.append(provider)
                # Exclude both sarvam and vertex from the routing test so the
                # only "real" call goes to workers_ai_indic. Without the
                # vertex exclusion, the 3-leg chain (2026-05-05) would let
                # vertex answer the English prompt with English text and
                # the Assamese-script assertion would fail.
                if provider in ("sarvam", "vertex"):
                    raise RuntimeError(f"{provider} excluded for routing test")
                return await llm._dispatch_llm_for_feature(
                    [{"role": "user", "content": "What is photosynthesis?"}],
                    provider, 512,
                    feature="assamese_rag_chat",
                )

            try:
                result = asyncio.run(
                    llm.call_with_provider_fallback(
                        "assamese_rag_chat", "as", _attempt,
                    )
                )
                # workers_ai_indic succeeded with actual Assamese script
                assert "workers_ai_indic" in providers_attempted
                assert _has_assamese_script(result), (
                    f"Successful workers_ai_indic result {result!r} must "
                    f"contain Assamese script (U+0980–U+09FF)"
                )
            except RuntimeError as exc:
                errors_seen.append(str(exc))
                # Acceptable: workers_ai_indic was reached but raised
                # (wrong script from CF endpoint, script validation triggered).
                assert "workers_ai_indic" in providers_attempted, (
                    f"workers_ai_indic must be attempted before exhaustion; "
                    f"attempted: {providers_attempted}, errors: {errors_seen}"
                )
        finally:
            llm.select_provider = original

        assert "workers_ai_indic" in providers_attempted, (
            f"Routing must reach workers_ai_indic; attempted: {providers_attempted}"
        )

    @pytest.mark.skip(reason=(
        "Task #270 live design (Gemini Tier B fallback inside "
        "_assamese_translate_gemini_main_sarvam_polish) was superseded by "
        "Task #291: IndicTrans2 is the sole translator. Re-enable only if a "
        "future task reintroduces a translate fallback tier."
    ))
    @_live_skip
    def test_live_call_llm_api_chat_lang_as_produces_assamese_output(self):
        """I4 — call_llm_api_chat(lang='as') live end-to-end test.

        Hybrid approach (same pattern as I2):
          - Sarvam translate: None (disabled)
          - IndicTrans2 (Tier A): LIVE HTTP call to CF Workers AI.
            Script validation raises for Devanagari output (confirmed live).
          - Gemini (Tier B): stubbed to return Assamese, confirming pipeline
            routing to Tier B and strict U+0980–U+09FF output.

        This exercises the highest-level pipeline path, mimicking a real
        Assamese chat request processed by _assamese_translate_gemini_main_
        sarvam_polish when Sarvam translate is unavailable.
        """
        _reset_indic_client()
        import asyncio
        import llm as _llm
        import deps
        from routes import ai_chat as chat_mod

        _ASSAMESE_STUB = "নিউটনৰ গতিৰ প্ৰথম সূত্ৰ কি?"

        indic_trans_calls: list[str] = []
        gemini_calls: list = []

        async def _fake_gemini(messages, model, max_tokens):
            # Vertex SA path post 2026-05-03 — no api_key arg.
            gemini_calls.append(model)
            return _ASSAMESE_STUB

        original_sarvam_tc  = getattr(deps, "sarvam_translate_client", None)
        original_sarvam_c   = getattr(deps, "sarvam_client", None)
        original_sarvam_llm = getattr(deps, "sarvam_llm_client", None)
        original_redis      = chat_mod.redis_client
        original_vertex_fn  = getattr(_llm, "_call_vertex_chat", None)
        try:
            deps.sarvam_translate_client = None
            deps.sarvam_client           = None
            deps.sarvam_llm_client       = None
            chat_mod.redis_client        = None
            # Vertex-only Gemini auth (2026-05-03): patch `_call_vertex_chat`.
            _llm._call_vertex_chat       = _fake_gemini

            result = asyncio.run(
                chat_mod._assamese_translate_gemini_main_sarvam_polish(
                    "What is Newton's first law of motion?",
                    target_lang_code="as-IN",
                )
            )
        finally:
            deps.sarvam_translate_client = original_sarvam_tc
            deps.sarvam_client           = original_sarvam_c
            deps.sarvam_llm_client       = original_sarvam_llm
            chat_mod.redis_client        = original_redis
            if original_vertex_fn is not None:
                _llm._call_vertex_chat = original_vertex_fn

        assert len(gemini_calls) == 1, (
            "Gemini (Tier B) must be triggered as fallback when IndicTrans2 raises "
            f"for wrong script; gemini_calls={gemini_calls}"
        )
        assert result == _ASSAMESE_STUB, (
            f"Pipeline must return Gemini's Assamese stub; got {result!r}"
        )
        assert _has_assamese_script(result), (
            f"Pipeline result {result!r} must contain Assamese Unicode U+0980–U+09FF"
        )
