"""Smoke test — Task #250 provider dispatch coverage for all 15 feature keys.

Run from the syrabit-backend directory:
    python -m pytest tests/test_provider_dispatch.py -v
or standalone:
    python tests/test_provider_dispatch.py

Tests use asyncio.run() + unittest.mock stubs to validate runtime dispatch
paths — not source-text inspection.  Gateway-slug tests validate well-formed
CF AI Gateway URLs against the declared slugs without making live HTTP calls.
"""
from __future__ import annotations

import asyncio
import sys
import os
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import logging
logging.disable(logging.CRITICAL)

from config import PROVIDER_PRIORITY, PROVIDER_CREDITS, cf_gateway_url


# ── PROVIDER_PRIORITY structure ───────────────────────────────────────────────

def test_all_15_feature_keys_present():
    expected = {
        "english_rag_chat", "assamese_rag_chat",
        "content", "assamese_content",
        "tts", "stt", "voice",
        "embed", "rerank", "vector_search",
        "translate", "vision", "safety",
        "search_rag", "live_search",
    }
    missing = expected - set(PROVIDER_PRIORITY)
    assert not missing, f"Missing feature keys in PROVIDER_PRIORITY: {missing}"
    print(f"  PASS: all {len(expected)} feature keys present")


def test_provider_credits_all_referenced_providers_have_entry():
    all_providers = set()
    for providers in PROVIDER_PRIORITY.values():
        all_providers.update(providers)
    missing = {p for p in all_providers if p not in PROVIDER_CREDITS}
    assert not missing, f"Providers in PROVIDER_PRIORITY but missing from PROVIDER_CREDITS: {missing}"
    print(f"  PASS: all {len(all_providers)} providers have PROVIDER_CREDITS entries")


def test_workers_ai_credit_is_zero():
    assert PROVIDER_CREDITS.get("workers_ai", -1) == 0, "workers_ai must have weight 0 (last-resort only)"
    print("  PASS: workers_ai has weight=0 (last-resort only)")


def test_select_provider_returns_valid_provider_for_all_features():
    from llm import select_provider
    for feature, providers in PROVIDER_PRIORITY.items():
        lang = "as" if "assamese" in feature else "en"
        result = select_provider(feature, lang=lang)
        assert result in PROVIDER_CREDITS, f"{feature}: select_provider returned unknown provider {result!r}"
        assert result in providers, f"{feature}: select_provider returned {result!r} not in priority list {providers}"
    print(f"  PASS: select_provider returns valid provider for all {len(PROVIDER_PRIORITY)} feature keys")


def test_assamese_rag_chat_can_select_sarvam():
    from llm import select_provider
    sarvam_seen = False
    for _ in range(50):
        p = select_provider("assamese_rag_chat", lang="as")
        if p == "sarvam":
            sarvam_seen = True
            break
    assert sarvam_seen, "assamese_rag_chat: sarvam never selected across 50 draws (weight > 0 expected)"
    print("  PASS: assamese_rag_chat selects sarvam with non-zero probability")


def test_english_rag_chat_never_selects_sarvam_when_lang_en():
    from llm import select_provider
    for _ in range(50):
        p = select_provider("english_rag_chat", lang="en")
        assert p != "sarvam", "english_rag_chat with lang=en must never select sarvam"
    print("  PASS: english_rag_chat never selects sarvam when lang=en")


def test_workers_ai_fallback_pool_is_workers_only():
    from llm import _LLM_PROVIDERS_WORKERS_ONLY, _LLM_PROVIDERS_CHAT
    wai_providers = {p["provider"] for p in _LLM_PROVIDERS_WORKERS_ONLY}
    chat_providers = {p["provider"] for p in _LLM_PROVIDERS_CHAT}
    groq_in_chat = "groq" in chat_providers or "cerebras" in chat_providers
    assert wai_providers <= {"workers-ai"}, f"Non-workers provider in workers-only pool: {wai_providers}"
    if groq_in_chat:
        assert "groq" not in wai_providers, "Groq leaked into _LLM_PROVIDERS_WORKERS_ONLY"
    print(f"  PASS: _LLM_PROVIDERS_WORKERS_ONLY = {wai_providers} (no Groq/Cerebras/Gemini)")


# ── PROVIDER_PRIORITY correctness — only wired providers ─────────────────────

def test_tts_stt_priority_excludes_unwired_providers():
    """vertex/bedrock/azure_openai must not be in tts/stt/voice priority lists.

    Those providers have no TTS/STT endpoint wired in _synthesize_with_fallback
    or _transcribe_with_fallback (Phase 2: Task #256).  Listing them causes
    avoidable failed attempts before falling through to workers_ai.
    """
    _unwired = {"vertex", "bedrock", "azure_openai"}
    for feature in ("tts", "stt", "voice"):
        pool = set(PROVIDER_PRIORITY.get(feature, []))
        overlap = pool & _unwired
        assert not overlap, (
            f"PROVIDER_PRIORITY['{feature}'] contains unwired voice providers: {overlap}. "
            f"Remove them until their TTS/STT clients are implemented (Task #256)."
        )
    print("  PASS: tts/stt/voice priority lists exclude vertex/bedrock/azure_openai")


def test_embed_priority_is_vertex_and_workers_only():
    """embed priority must contain only vertex and workers_ai.

    cohere/pinecone/bedrock/azure_openai embed clients are Phase 2 (Task #257).
    """
    embed_pool = PROVIDER_PRIORITY.get("embed", [])
    allowed = {"vertex", "workers_ai"}
    unexpected = set(embed_pool) - allowed
    assert not unexpected, (
        f"PROVIDER_PRIORITY['embed'] contains Phase-2 providers: {unexpected}. "
        f"Remove them until their embed clients are wired (Task #257)."
    )
    assert "vertex" in embed_pool, "embed priority must include vertex"
    assert "workers_ai" in embed_pool, "embed priority must include workers_ai as last-resort"
    print(f"  PASS: PROVIDER_PRIORITY['embed'] = {embed_pool} (vertex + workers_ai only)")


def test_translate_priority_excludes_bedrock_azure():
    """bedrock/azure_openai must not be in translate priority — no translate endpoint wired."""
    translate_pool = PROVIDER_PRIORITY.get("translate", [])
    unwired = {"bedrock", "azure_openai"}
    overlap = set(translate_pool) & unwired
    assert not overlap, (
        f"PROVIDER_PRIORITY['translate'] contains providers with no translate endpoint: {overlap}."
    )
    assert "sarvam" in translate_pool or "vertex" in translate_pool, \
        "translate priority must contain at least sarvam or vertex"
    print(f"  PASS: PROVIDER_PRIORITY['translate'] = {translate_pool} (no unwired bedrock/azure)")


def test_vision_priority_excludes_bedrock_azure():
    """bedrock/azure_openai must not be in vision priority — vision path not wired for them."""
    vision_pool = PROVIDER_PRIORITY.get("vision", [])
    unwired = {"bedrock", "azure_openai"}
    overlap = set(vision_pool) & unwired
    assert not overlap, (
        f"PROVIDER_PRIORITY['vision'] contains providers with no vision dispatch: {overlap}."
    )
    assert "vertex" in vision_pool, "vision priority must include vertex (Gemini vision)"
    print(f"  PASS: PROVIDER_PRIORITY['vision'] = {vision_pool} (no unwired bedrock/azure)")


def test_no_phase2_stub_providers_in_live_search():
    """tavily must not be in live_search priority — Tavily client not yet wired."""
    live_pool = PROVIDER_PRIORITY.get("live_search", [])
    assert "tavily" not in live_pool, (
        "PROVIDER_PRIORITY['live_search'] contains 'tavily' but Tavily client is not wired (Phase 2)."
    )
    assert "exa_ai" in live_pool, "live_search must have exa_ai as primary"
    print(f"  PASS: PROVIDER_PRIORITY['live_search'] = {live_pool} (tavily excluded until wired)")


# ── CF AI Gateway slug URL well-formedness ────────────────────────────────────

def test_assemblyai_uses_cf_gateway_url():
    from providers import assemblyai
    url = assemblyai._base_url()
    from config import is_cf_gateway_up
    if is_cf_gateway_up():
        assert "gateway.ai.cloudflare.com" in url, f"Expected CF gateway URL, got: {url}"
        print(f"  PASS: assemblyai._base_url() → CF gateway: {url}")
    else:
        assert url == assemblyai._DIRECT_BASE
        print(f"  PASS: assemblyai._base_url() → direct (gateway down): {url}")


def test_bedrock_uses_cf_gateway_slug():
    slug_url = cf_gateway_url("bedrock")
    assert "aws-bedrock" in slug_url, f"Expected aws-bedrock slug, got: {slug_url}"
    print(f"  PASS: bedrock CF slug resolves to: {slug_url}")


def test_azure_openai_uses_cf_gateway_slug():
    slug_url = cf_gateway_url("azure_openai")
    assert "azure-openai" in slug_url, f"Expected azure-openai slug, got: {slug_url}"
    print(f"  PASS: azure_openai CF slug resolves to: {slug_url}")


def test_all_safety_providers_have_cf_gateway_slugs():
    """Every provider in PROVIDER_PRIORITY['safety'] must have a well-formed CF gateway slug.

    This validates that the gateway URL builder doesn't silently return an empty
    string or non-gateway URL for declared safety providers.
    """
    from config import CF_GATEWAY_ENABLED, CF_GATEWAY_BASE
    if not CF_GATEWAY_ENABLED:
        print("  PASS: CF gateway not configured — slug check skipped")
        return
    safety_providers = PROVIDER_PRIORITY.get("safety", [])
    for provider in safety_providers:
        if provider == "workers_ai":
            continue
        slug_url = cf_gateway_url(provider)
        assert slug_url.startswith("https://gateway.ai.cloudflare.com"), (
            f"safety provider {provider!r}: expected CF gateway URL, got {slug_url!r}"
        )
        assert len(slug_url) > len(CF_GATEWAY_BASE) + 2, (
            f"safety provider {provider!r}: slug URL looks truncated: {slug_url!r}"
        )
    print(f"  PASS: all {len(safety_providers)} safety providers have well-formed CF gateway slugs")


# ── Runtime dispatch stub tests ───────────────────────────────────────────────

def test_dispatch_routes_bedrock_at_runtime():
    """_dispatch_llm_for_feature routes 'bedrock' to providers.bedrock.call_converse."""
    from llm import _dispatch_llm_for_feature
    stub = mock.AsyncMock(return_value="bedrock-response")
    with mock.patch("providers.bedrock.call_converse", stub):
        result = asyncio.run(
            _dispatch_llm_for_feature([{"role": "user", "content": "hi"}], "bedrock", 16)
        )
    assert result == "bedrock-response", f"Expected bedrock-response, got {result!r}"
    stub.assert_called_once()
    print("  PASS: _dispatch_llm_for_feature routes 'bedrock' → providers.bedrock.call_converse at runtime")


def test_dispatch_routes_azure_openai_at_runtime():
    """_dispatch_llm_for_feature routes 'azure_openai' to providers.azure_openai.call_chat."""
    from llm import _dispatch_llm_for_feature
    stub = mock.AsyncMock(return_value="azure-response")
    with mock.patch("providers.azure_openai.call_chat", stub):
        result = asyncio.run(
            _dispatch_llm_for_feature([{"role": "user", "content": "hi"}], "azure_openai", 16)
        )
    assert result == "azure-response", f"Expected azure-response, got {result!r}"
    stub.assert_called_once()
    print("  PASS: _dispatch_llm_for_feature routes 'azure_openai' → providers.azure_openai.call_chat at runtime")


def test_call_with_provider_fallback_invokes_attempt_fn():
    """call_with_provider_fallback calls attempt_fn with the selected provider."""
    from llm import call_with_provider_fallback
    calls = []

    async def _stub(provider: str) -> str:
        calls.append(provider)
        return f"ok:{provider}"

    result = asyncio.run(call_with_provider_fallback("content", "en", _stub))
    assert calls, "attempt_fn was never called by call_with_provider_fallback"
    assert result.startswith("ok:"), f"Unexpected result: {result!r}"
    print(f"  PASS: call_with_provider_fallback invokes attempt_fn with provider={calls[0]!r}")


def test_tts_cartesia_fails_falls_back_to_workers_ai():
    """_synthesize_with_fallback: cartesia fails → falls back to next available (workers_ai).

    vertex/bedrock/azure_openai are not in PROVIDER_PRIORITY['tts'], so the
    fallback pool is: cartesia → elevenlabs → workers_ai.  We simulate cartesia
    being selected first (fails) then workers_ai succeeding.
    """
    from routes.voice import _synthesize_with_fallback
    side_effects = iter(["cartesia", "workers_ai"])

    def _fake_select(feature, lang="en", exclude=frozenset()):
        return next(side_effects)

    workers_stub = mock.AsyncMock(return_value=b"audio-bytes")
    with mock.patch("llm.select_provider", side_effect=_fake_select):
        with mock.patch("routes.voice._tts_workers_ai", workers_stub):
            result = asyncio.run(_synthesize_with_fallback("hello", None, None, "en"))

    assert result == b"audio-bytes", "Fallback to workers_ai should return audio bytes"
    workers_stub.assert_called_once()
    print("  PASS: TTS cartesia fails, fallback recovers to workers_ai (vertex/bedrock not in pool)")


def test_stt_assemblyai_fails_falls_back_to_workers_ai():
    """_transcribe_with_fallback: assemblyai fails → falls back to workers_ai.

    vertex/bedrock/azure_openai are not in PROVIDER_PRIORITY['stt'], so the
    fallback pool is: assemblyai → workers_ai.
    """
    from routes.voice import _transcribe_with_fallback
    side_effects = iter(["assemblyai", "workers_ai"])

    def _fake_select(feature, lang="en", exclude=frozenset()):
        return next(side_effects)

    workers_stub = mock.AsyncMock(return_value="transcript-text")
    with mock.patch("llm.select_provider", side_effect=_fake_select):
        with mock.patch("routes.voice._stt_workers_ai", workers_stub):
            result = asyncio.run(_transcribe_with_fallback(b"audio", "en"))

    assert result == "transcript-text", f"Unexpected result: {result!r}"
    workers_stub.assert_called_once()
    print("  PASS: STT assemblyai fails, fallback recovers to workers_ai (vertex/bedrock not in pool)")


def test_embed_dispatch_routes_to_vertex_at_runtime():
    """call_embed_with_dispatch routes 'embed' → vertex_services.embed_text via select_provider."""
    from llm import call_embed_with_dispatch
    embed_stub = mock.AsyncMock(return_value=[0.1, 0.2, 0.3])
    with mock.patch("llm.select_provider", return_value="vertex"):
        with mock.patch("vertex_services.embed_text", embed_stub):
            result = asyncio.run(call_embed_with_dispatch("test text", lang="en"))
    assert result == [0.1, 0.2, 0.3], f"Expected embedding list, got {result!r}"
    embed_stub.assert_called_once()
    print("  PASS: call_embed_with_dispatch routes select_provider('embed')='vertex' → vertex_services.embed_text")


def test_translate_dispatch_routes_sarvam_or_vertex():
    """call_translate_with_dispatch routes 'translate' via select_provider and calls the provider."""
    from llm import call_translate_with_dispatch
    gemini_stub = mock.AsyncMock(return_value="translated")
    with mock.patch("llm.select_provider", return_value="vertex"):
        with mock.patch("llm._call_gemini", gemini_stub):
            result = asyncio.run(
                call_translate_with_dispatch("hello", "en-IN", "as-IN", lang="as")
            )
    assert result == "translated", f"Unexpected: {result!r}"
    gemini_stub.assert_called_once()
    print("  PASS: call_translate_with_dispatch routes select_provider('translate')='vertex' → _call_gemini")


def test_vision_dispatch_routes_vertex_at_runtime():
    """call_vision_with_dispatch routes 'vision' via select_provider → _call_gemini for vertex."""
    from llm import call_vision_with_dispatch
    gemini_stub = mock.AsyncMock(return_value="image analysis")
    with mock.patch("llm.select_provider", return_value="vertex"):
        with mock.patch("llm._call_gemini", gemini_stub):
            with mock.patch("llm._GEMINI_KEY", "fake-key"):
                result = asyncio.run(
                    call_vision_with_dispatch("base64data", "Describe this image", lang="en")
                )
    assert result == "image analysis", f"Unexpected: {result!r}"
    gemini_stub.assert_called_once()
    print("  PASS: call_vision_with_dispatch routes select_provider('vision')='vertex' → _call_gemini")


def test_safety_feature_key_priority_has_bedrock_first():
    safety_list = PROVIDER_PRIORITY.get("safety", [])
    assert safety_list, "safety feature key missing from PROVIDER_PRIORITY"
    assert safety_list[0] == "bedrock", f"safety: expected bedrock first, got {safety_list}"
    assert "workers_ai" in safety_list, "safety: workers_ai fallback missing"
    print(f"  PASS: safety priority list = {safety_list}")


def test_llm_safety_check_async_and_env_gated():
    """llm_classify_safety must be async.

    When ENABLE_LLM_SAFETY_CHECK=false it returns None immediately.
    When the CF gateway is configured, _ENABLE_LLM_SAFETY defaults to True
    (auto-enabled via is_cf_gateway_up()) but we test the disabled path
    explicitly by patching the module flag.
    """
    from guardrails import prompt_safety as _ps
    assert asyncio.iscoroutinefunction(_ps.llm_classify_safety), \
        "llm_classify_safety must be an async function"
    orig = _ps._ENABLE_LLM_SAFETY
    try:
        _ps._ENABLE_LLM_SAFETY = False
        result = asyncio.run(_ps.llm_classify_safety("normal educational question"))
        assert result is None, f"Expected None when safety disabled, got {result!r}"
    finally:
        _ps._ENABLE_LLM_SAFETY = orig
    print("  PASS: llm_classify_safety is async; returns None when _ENABLE_LLM_SAFETY=False")


def test_safety_auto_enabled_when_cf_gateway_is_configured():
    """_default_llm_safety_enabled() returns True iff CF gateway env vars are set.

    This validates that safety routing is active by default in any environment
    where the CF AI Gateway is configured (CF_ACCOUNT_ID + CF_AI_GATEWAY_ID set),
    without requiring an explicit ENABLE_LLM_SAFETY_CHECK=true.
    """
    from guardrails.prompt_safety import _default_llm_safety_enabled
    from config import CF_GATEWAY_ENABLED
    result = _default_llm_safety_enabled()
    assert isinstance(result, bool), "_default_llm_safety_enabled must return bool"
    assert result == CF_GATEWAY_ENABLED, (
        f"Safety auto-enable mismatch: CF_GATEWAY_ENABLED={CF_GATEWAY_ENABLED} "
        f"but _default_llm_safety_enabled()={result}"
    )
    print(
        f"  PASS: safety auto-enabled={result} (matches CF_GATEWAY_ENABLED={CF_GATEWAY_ENABLED})"
    )


if __name__ == "__main__":
    tests = [
        test_all_15_feature_keys_present,
        test_provider_credits_all_referenced_providers_have_entry,
        test_workers_ai_credit_is_zero,
        test_select_provider_returns_valid_provider_for_all_features,
        test_assamese_rag_chat_can_select_sarvam,
        test_english_rag_chat_never_selects_sarvam_when_lang_en,
        test_workers_ai_fallback_pool_is_workers_only,
        test_tts_stt_priority_excludes_unwired_providers,
        test_embed_priority_is_vertex_and_workers_only,
        test_translate_priority_excludes_bedrock_azure,
        test_vision_priority_excludes_bedrock_azure,
        test_no_phase2_stub_providers_in_live_search,
        test_assemblyai_uses_cf_gateway_url,
        test_bedrock_uses_cf_gateway_slug,
        test_azure_openai_uses_cf_gateway_slug,
        test_all_safety_providers_have_cf_gateway_slugs,
        test_dispatch_routes_bedrock_at_runtime,
        test_dispatch_routes_azure_openai_at_runtime,
        test_call_with_provider_fallback_invokes_attempt_fn,
        test_tts_cartesia_fails_falls_back_to_workers_ai,
        test_stt_assemblyai_fails_falls_back_to_workers_ai,
        test_embed_dispatch_routes_to_vertex_at_runtime,
        test_translate_dispatch_routes_sarvam_or_vertex,
        test_vision_dispatch_routes_vertex_at_runtime,
        test_safety_feature_key_priority_has_bedrock_first,
        test_llm_safety_check_async_and_env_gated,
        test_safety_auto_enabled_when_cf_gateway_is_configured,
    ]
    failed = 0
    for t in tests:
        try:
            print(f"\n{t.__name__}")
            t()
        except Exception as exc:
            print(f"  FAIL: {exc}")
            failed += 1
    print(f"\n{'='*60}")
    print(f"Results: {len(tests) - failed}/{len(tests)} passed")
    if failed:
        sys.exit(1)
    print("All provider dispatch smoke tests PASSED")
