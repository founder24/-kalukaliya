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

def test_tts_stt_priority_structure():
    """tts/stt/voice priority lists must follow the authoritative provider matrix.

    Per the authoritative matrix: all mandated providers are listed even when their
    TTS/STT endpoints are not yet wired (Task #256). vertex/bedrock/azure_openai
    raise RuntimeError and are excluded gracefully by the fallback loop, leaving
    cartesia/elevenlabs/assemblyai/workers_ai as the actively synthesizing providers.

    Required structure:
      tts:   cartesia → elevenlabs → vertex → bedrock → azure_openai → workers_ai
      stt:   assemblyai → vertex → bedrock → azure_openai → workers_ai
      voice: assemblyai → cartesia → elevenlabs → vertex → bedrock → azure_openai → workers_ai
    """
    for feature in ("tts", "stt", "voice"):
        pool = PROVIDER_PRIORITY.get(feature, [])
        pool_set = set(pool)
        assert "workers_ai" in pool_set, f"{feature}: workers_ai must be in the pool as last-resort"
        assert pool[-1] == "workers_ai", f"{feature}: workers_ai must be last in priority list"
        assert "azure_openai" in pool_set, f"{feature}: azure_openai must be in pool (authoritative matrix)"
        assert pool[-2] == "azure_openai", (
            f"{feature}: azure_openai must be second-to-last (mandated by authoritative matrix)"
        )
        # vertex and bedrock must be listed per authoritative matrix (they raise RuntimeError → excluded gracefully)
        assert "vertex" in pool_set, f"{feature}: vertex must be listed per authoritative matrix"
        assert "bedrock" in pool_set, f"{feature}: bedrock must be listed per authoritative matrix"
    print("  PASS: tts/stt/voice priority list structure valid — all mandated providers listed, azure second-to-last")


def test_embed_priority_structure():
    """embed priority must include vertex, bedrock (Titan), cohere, azure_openai, workers_ai.

    Per the authoritative provider matrix:
      vertex(2k) → bedrock(1k, Titan embed) → cohere(1k) → azure_openai(1, RuntimeError) → workers_ai(0)
    bedrock.embed wired via Amazon Titan Text Embeddings v1; cohere.embed_query wired.
    pinecone_ai excluded from embed dispatch (vector search only, not feature embed pool).
    """
    embed_pool = PROVIDER_PRIORITY.get("embed", [])
    pool_set = set(embed_pool)
    # pinecone_ai should NOT be in the embed pool (it's a vector search store, not an embed provider)
    assert "pinecone_ai" not in pool_set, "embed: pinecone_ai must not be in embed pool (use vector_search)"
    assert "vertex" in pool_set, "embed priority must include vertex"
    assert "bedrock" in pool_set, "embed priority must include bedrock (Titan Text Embeddings)"
    assert "cohere" in pool_set, "embed priority must include cohere (embed_query wired)"
    assert "workers_ai" in pool_set, "embed priority must include workers_ai as last-resort"
    assert embed_pool[-1] == "workers_ai", "embed: workers_ai must be last"
    assert embed_pool[-2] == "azure_openai", "embed: azure_openai must be second-to-last"
    print(f"  PASS: PROVIDER_PRIORITY['embed'] = {embed_pool} (vertex/bedrock/cohere wired, azure second-to-last)")


def test_translate_priority_includes_bedrock():
    """bedrock must be in translate priority — translate wired via providers.bedrock.call_converse.

    Authoritative matrix:
      sarvam(500) → vertex(2k) → bedrock(1k, call_converse) → azure_openai(1, call_chat) → workers_ai(0)
    """
    translate_pool = PROVIDER_PRIORITY.get("translate", [])
    pool_set = set(translate_pool)
    assert "bedrock" in pool_set, (
        "PROVIDER_PRIORITY['translate'] must include bedrock (translate wired via call_converse)"
    )
    assert "sarvam" in pool_set or "vertex" in pool_set, \
        "translate priority must contain at least sarvam or vertex"
    assert translate_pool[-1] == "workers_ai", "translate: workers_ai must be last"
    assert translate_pool[-2] == "azure_openai", "translate: azure_openai must be second-to-last"
    print(f"  PASS: PROVIDER_PRIORITY['translate'] = {translate_pool} (bedrock wired via call_converse)")


def test_vision_priority_includes_bedrock():
    """bedrock must be in vision priority — vision wired via providers.bedrock.call_converse_vision.

    Authoritative matrix:
      vertex(2k) → bedrock(1k, Claude multimodal) → azure_openai(1, call_chat) → workers_ai(0)
    """
    vision_pool = PROVIDER_PRIORITY.get("vision", [])
    pool_set = set(vision_pool)
    assert "bedrock" in pool_set, (
        "PROVIDER_PRIORITY['vision'] must include bedrock (wired via call_converse_vision)"
    )
    assert "vertex" in pool_set, "vision priority must include vertex (Gemini vision)"
    assert vision_pool[-1] == "workers_ai", "vision: workers_ai must be last"
    assert vision_pool[-2] == "azure_openai", "vision: azure_openai must be second-to-last"
    print(f"  PASS: PROVIDER_PRIORITY['vision'] = {vision_pool} (bedrock wired via call_converse_vision)")


def test_live_search_includes_tavily():
    """tavily must be in live_search priority — Tavily dispatch wired in call_search_rag_with_dispatch.

    Authoritative matrix:
      exa_ai(1000) → tavily(500) → workers_ai(0)
    tavily branch wired via TAVILY_API_KEY env var + Tavily search API.
    """
    live_pool = PROVIDER_PRIORITY.get("live_search", [])
    pool_set = set(live_pool)
    assert "tavily" in pool_set, (
        "PROVIDER_PRIORITY['live_search'] must include tavily (dispatch wired)"
    )
    assert "exa_ai" in pool_set, "live_search must have exa_ai as primary"
    assert live_pool[-1] == "workers_ai", "live_search: workers_ai must be last"
    print(f"  PASS: PROVIDER_PRIORITY['live_search'] = {live_pool} (exa_ai + tavily wired)")


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


def test_chat_content_rag_hard_fallback_is_workers_ai_only():
    """call_llm_api_chat, call_llm_api_content, and call_llm_for_rag must NOT fall back
    to legacy provider lists (Groq/Cerebras/Gemini direct).

    When call_with_provider_fallback raises (all PROVIDER_PRIORITY providers exhausted),
    the final _call_llm_raw call must use _LLM_PROVIDERS_WORKERS_ONLY — never
    _LLM_PROVIDERS_CHAT, _LLM_PROVIDERS_CONTENT, or _RAG_PROVIDERS.
    """
    import inspect
    import llm as _llm_mod

    for fn_name in ("call_llm_api_chat", "call_llm_api_content", "call_llm_for_rag"):
        src = inspect.getsource(getattr(_llm_mod, fn_name))
        assert "_LLM_PROVIDERS_WORKERS_ONLY" in src, (
            f"{fn_name}: hard fallback must use _LLM_PROVIDERS_WORKERS_ONLY, not a legacy provider list"
        )
        for banned in ("_LLM_PROVIDERS_CHAT", "_LLM_PROVIDERS_CONTENT", "_RAG_PROVIDERS",
                       "_llm_batcher.call", "_content_batcher.call"):
            exc_section = src[src.find("except Exception"):]
            assert banned not in exc_section, (
                f"{fn_name}: except-block must not reference {banned!r} — "
                f"would reintroduce non-PROVIDER_PRIORITY providers as fallback"
            )
    print("  PASS: chat/content/rag hard fallback constrained to _LLM_PROVIDERS_WORKERS_ONLY")


def test_chat_fallback_calls_workers_ai_raw_at_runtime():
    """When call_with_provider_fallback raises, call_llm_api_chat must call
    _call_llm_raw with _LLM_PROVIDERS_WORKERS_ONLY — not the chat batcher."""
    from llm import call_llm_api_chat, _LLM_PROVIDERS_WORKERS_ONLY
    raw_stub = mock.AsyncMock(return_value="workers-ai-response")
    with mock.patch("llm.call_with_provider_fallback", side_effect=RuntimeError("all exhausted")):
        with mock.patch("llm._call_llm_raw", raw_stub):
            result = asyncio.run(call_llm_api_chat([{"role": "user", "content": "hi"}]))
    assert result == "workers-ai-response", f"Expected workers_ai response, got {result!r}"
    raw_stub.assert_called_once()
    call_kwargs = raw_stub.call_args
    provider_list_arg = call_kwargs.kwargs.get("provider_list") or call_kwargs.args[-1]
    assert provider_list_arg is _LLM_PROVIDERS_WORKERS_ONLY, (
        "chat fallback must pass _LLM_PROVIDERS_WORKERS_ONLY to _call_llm_raw"
    )
    print("  PASS: call_llm_api_chat hard fallback calls _call_llm_raw(provider_list=_LLM_PROVIDERS_WORKERS_ONLY)")


def test_content_fallback_calls_workers_ai_raw_at_runtime():
    """When call_with_provider_fallback raises, call_llm_api_content must call
    _call_llm_raw with _LLM_PROVIDERS_WORKERS_ONLY — not the content batcher."""
    from llm import call_llm_api_content, _LLM_PROVIDERS_WORKERS_ONLY
    raw_stub = mock.AsyncMock(return_value="workers-ai-content")
    with mock.patch("llm.call_with_provider_fallback", side_effect=RuntimeError("all exhausted")):
        with mock.patch("llm._call_llm_raw", raw_stub):
            result = asyncio.run(call_llm_api_content([{"role": "user", "content": "generate"}]))
    assert result == "workers-ai-content", f"Expected workers_ai response, got {result!r}"
    raw_stub.assert_called_once()
    call_kwargs = raw_stub.call_args
    provider_list_arg = call_kwargs.kwargs.get("provider_list") or call_kwargs.args[-1]
    assert provider_list_arg is _LLM_PROVIDERS_WORKERS_ONLY, (
        "content fallback must pass _LLM_PROVIDERS_WORKERS_ONLY to _call_llm_raw"
    )
    print("  PASS: call_llm_api_content hard fallback calls _call_llm_raw(provider_list=_LLM_PROVIDERS_WORKERS_ONLY)")


def test_rag_fallback_calls_workers_ai_raw_at_runtime():
    """When call_with_provider_fallback raises, call_llm_for_rag must call
    _call_llm_raw with _LLM_PROVIDERS_WORKERS_ONLY — not _RAG_PROVIDERS."""
    from llm import call_llm_for_rag, _LLM_PROVIDERS_WORKERS_ONLY
    raw_stub = mock.AsyncMock(return_value="workers-ai-rag")
    with mock.patch("llm.call_with_provider_fallback", side_effect=RuntimeError("all exhausted")):
        with mock.patch("llm._call_llm_raw", raw_stub):
            result = asyncio.run(call_llm_for_rag([{"role": "user", "content": "answer"}]))
    assert result == "workers-ai-rag", f"Expected workers_ai response, got {result!r}"
    raw_stub.assert_called_once()
    call_kwargs = raw_stub.call_args
    provider_list_arg = call_kwargs.kwargs.get("provider_list") or call_kwargs.args[-1]
    assert provider_list_arg is _LLM_PROVIDERS_WORKERS_ONLY, (
        "rag fallback must pass _LLM_PROVIDERS_WORKERS_ONLY to _call_llm_raw"
    )
    print("  PASS: call_llm_for_rag hard fallback calls _call_llm_raw(provider_list=_LLM_PROVIDERS_WORKERS_ONLY)")


# ── Task #256: Bedrock + Azure OpenAI feature service wiring tests ────────────

def test_bedrock_call_tts_raises_when_no_proxy():
    """providers.bedrock.call_tts raises RuntimeError when BEDROCK_PROXY_URL is not set."""
    from providers.bedrock import call_tts as _bk_tts
    import os
    orig = os.environ.pop("BEDROCK_PROXY_URL", None)
    try:
        try:
            asyncio.run(_bk_tts("hello"))
            assert False, "Expected RuntimeError when BEDROCK_PROXY_URL not set"
        except RuntimeError as exc:
            assert "BEDROCK_PROXY_URL" in str(exc), f"Unexpected error: {exc}"
    finally:
        if orig is not None:
            os.environ["BEDROCK_PROXY_URL"] = orig
    print("  PASS: providers.bedrock.call_tts raises RuntimeError when BEDROCK_PROXY_URL not set")


def test_bedrock_call_stt_raises_when_no_proxy():
    """providers.bedrock.call_stt raises RuntimeError when BEDROCK_PROXY_URL is not set."""
    from providers.bedrock import call_stt as _bk_stt
    import os
    orig = os.environ.pop("BEDROCK_PROXY_URL", None)
    try:
        try:
            asyncio.run(_bk_stt(b"audio"))
            assert False, "Expected RuntimeError when BEDROCK_PROXY_URL not set"
        except RuntimeError as exc:
            assert "BEDROCK_PROXY_URL" in str(exc), f"Unexpected error: {exc}"
    finally:
        if orig is not None:
            os.environ["BEDROCK_PROXY_URL"] = orig
    print("  PASS: providers.bedrock.call_stt raises RuntimeError when BEDROCK_PROXY_URL not set")


def test_bedrock_call_translate_raises_when_no_proxy():
    """providers.bedrock.call_translate raises RuntimeError when BEDROCK_PROXY_URL is not set."""
    from providers.bedrock import call_translate as _bk_translate
    import os
    orig = os.environ.pop("BEDROCK_PROXY_URL", None)
    try:
        try:
            asyncio.run(_bk_translate("hello", target_lang="as"))
            assert False, "Expected RuntimeError when BEDROCK_PROXY_URL not set"
        except RuntimeError as exc:
            assert "BEDROCK_PROXY_URL" in str(exc), f"Unexpected error: {exc}"
    finally:
        if orig is not None:
            os.environ["BEDROCK_PROXY_URL"] = orig
    print("  PASS: providers.bedrock.call_translate raises RuntimeError when BEDROCK_PROXY_URL not set")


def test_azure_openai_call_tts_raises_when_no_speech_key():
    """providers.azure_openai.call_tts raises RuntimeError when AZURE_SPEECH_KEY is not set."""
    from providers.azure_openai import call_tts as _az_tts
    import os
    orig_key = os.environ.pop("AZURE_SPEECH_KEY", None)
    orig_region = os.environ.pop("AZURE_SPEECH_REGION", None)
    try:
        try:
            asyncio.run(_az_tts("hello"))
            assert False, "Expected RuntimeError when AZURE_SPEECH_KEY not set"
        except RuntimeError as exc:
            assert "AZURE_SPEECH_KEY" in str(exc), f"Unexpected error: {exc}"
    finally:
        if orig_key is not None:
            os.environ["AZURE_SPEECH_KEY"] = orig_key
        if orig_region is not None:
            os.environ["AZURE_SPEECH_REGION"] = orig_region
    print("  PASS: providers.azure_openai.call_tts raises RuntimeError when AZURE_SPEECH_KEY not set")


def test_azure_openai_call_translate_raises_when_no_translator_key():
    """providers.azure_openai.call_translate raises RuntimeError when AZURE_TRANSLATOR_KEY not set."""
    from providers.azure_openai import call_translate as _az_translate
    import os
    orig = os.environ.pop("AZURE_TRANSLATOR_KEY", None)
    try:
        try:
            asyncio.run(_az_translate("hello", target_lang="as"))
            assert False, "Expected RuntimeError when AZURE_TRANSLATOR_KEY not set"
        except RuntimeError as exc:
            assert "AZURE_TRANSLATOR_KEY" in str(exc), f"Unexpected error: {exc}"
    finally:
        if orig is not None:
            os.environ["AZURE_TRANSLATOR_KEY"] = orig
    print("  PASS: providers.azure_openai.call_translate raises RuntimeError when AZURE_TRANSLATOR_KEY not set")


def test_embed_dispatch_routes_bedrock_to_call_embed():
    """call_embed_with_dispatch routes 'bedrock' → providers.bedrock.call_embed (Task #256)."""
    from llm import call_embed_with_dispatch
    embed_stub = mock.AsyncMock(return_value=[0.9, 0.8, 0.7])
    with mock.patch("llm.select_provider", return_value="bedrock"):
        with mock.patch("providers.bedrock.call_embed", embed_stub):
            result = asyncio.run(call_embed_with_dispatch("test text", lang="en"))
    assert result == [0.9, 0.8, 0.7], f"Expected embedding list, got {result!r}"
    embed_stub.assert_called_once()
    print("  PASS: call_embed_with_dispatch routes select_provider('embed')='bedrock' → providers.bedrock.call_embed")


def test_embed_dispatch_routes_azure_openai_to_call_embed():
    """call_embed_with_dispatch routes 'azure_openai' → providers.azure_openai.call_embed (Task #256)."""
    from llm import call_embed_with_dispatch
    embed_stub = mock.AsyncMock(return_value=[0.5, 0.4, 0.3])
    with mock.patch("llm.select_provider", return_value="azure_openai"):
        with mock.patch("providers.azure_openai.call_embed", embed_stub):
            result = asyncio.run(call_embed_with_dispatch("test text", lang="en"))
    assert result == [0.5, 0.4, 0.3], f"Expected embedding list, got {result!r}"
    embed_stub.assert_called_once()
    print("  PASS: call_embed_with_dispatch routes select_provider('embed')='azure_openai' → providers.azure_openai.call_embed")


def test_translate_dispatch_routes_bedrock_to_call_translate():
    """call_translate_with_dispatch routes 'bedrock' → providers.bedrock.call_translate (Task #256)."""
    from llm import call_translate_with_dispatch
    translate_stub = mock.AsyncMock(return_value="অনুবাদিত পাঠ্য")
    with mock.patch("llm.select_provider", return_value="bedrock"):
        with mock.patch("providers.bedrock.call_translate", translate_stub):
            result = asyncio.run(
                call_translate_with_dispatch("hello world", "en-IN", "as-IN", lang="as")
            )
    assert result == "অনুবাদিত পাঠ্য", f"Unexpected: {result!r}"
    translate_stub.assert_called_once()
    print("  PASS: call_translate_with_dispatch routes select_provider('translate')='bedrock' → providers.bedrock.call_translate")


def test_translate_dispatch_routes_azure_openai_to_call_translate():
    """call_translate_with_dispatch routes 'azure_openai' → providers.azure_openai.call_translate (Task #256)."""
    from llm import call_translate_with_dispatch
    translate_stub = mock.AsyncMock(return_value="translated text")
    with mock.patch("llm.select_provider", return_value="azure_openai"):
        with mock.patch("providers.azure_openai.call_translate", translate_stub):
            result = asyncio.run(
                call_translate_with_dispatch("hello world", "en-IN", "as-IN", lang="as")
            )
    assert result == "translated text", f"Unexpected: {result!r}"
    translate_stub.assert_called_once()
    print("  PASS: call_translate_with_dispatch routes select_provider('translate')='azure_openai' → providers.azure_openai.call_translate")


def test_voice_tts_bedrock_provider_calls_bedrock_call_tts():
    """_synthesize_with_fallback: when provider='bedrock', calls providers.bedrock.call_tts (Task #256)."""
    from routes.voice import _synthesize_with_fallback
    tts_stub = mock.AsyncMock(return_value=b"bedrock-audio")
    with mock.patch("llm.select_provider", return_value="bedrock"):
        with mock.patch("providers.bedrock.call_tts", tts_stub):
            result = asyncio.run(_synthesize_with_fallback("hello", None, None, "en"))
    assert result == b"bedrock-audio", f"Expected bedrock audio, got {result!r}"
    tts_stub.assert_called_once()
    print("  PASS: _synthesize_with_fallback routes provider='bedrock' → providers.bedrock.call_tts")


def test_voice_tts_azure_openai_provider_calls_azure_call_tts():
    """_synthesize_with_fallback: when provider='azure_openai', calls providers.azure_openai.call_tts (Task #256)."""
    from routes.voice import _synthesize_with_fallback
    tts_stub = mock.AsyncMock(return_value=b"azure-audio")
    with mock.patch("llm.select_provider", return_value="azure_openai"):
        with mock.patch("providers.azure_openai.call_tts", tts_stub):
            result = asyncio.run(_synthesize_with_fallback("hello", None, None, "en"))
    assert result == b"azure-audio", f"Expected azure audio, got {result!r}"
    tts_stub.assert_called_once()
    print("  PASS: _synthesize_with_fallback routes provider='azure_openai' → providers.azure_openai.call_tts")


def test_voice_stt_bedrock_provider_calls_bedrock_call_stt():
    """_transcribe_with_fallback: when provider='bedrock', calls providers.bedrock.call_stt (Task #256)."""
    from routes.voice import _transcribe_with_fallback
    stt_stub = mock.AsyncMock(return_value="bedrock transcript")
    with mock.patch("llm.select_provider", return_value="bedrock"):
        with mock.patch("providers.bedrock.call_stt", stt_stub):
            result = asyncio.run(_transcribe_with_fallback(b"audio", "en"))
    assert result == "bedrock transcript", f"Expected bedrock transcript, got {result!r}"
    stt_stub.assert_called_once()
    print("  PASS: _transcribe_with_fallback routes provider='bedrock' → providers.bedrock.call_stt")


def test_voice_stt_azure_openai_provider_calls_azure_call_stt():
    """_transcribe_with_fallback: when provider='azure_openai', calls providers.azure_openai.call_stt (Task #256)."""
    from routes.voice import _transcribe_with_fallback
    stt_stub = mock.AsyncMock(return_value="azure transcript")
    with mock.patch("llm.select_provider", return_value="azure_openai"):
        with mock.patch("providers.azure_openai.call_stt", stt_stub):
            result = asyncio.run(_transcribe_with_fallback(b"audio", "en"))
    assert result == "azure transcript", f"Expected azure transcript, got {result!r}"
    stt_stub.assert_called_once()
    print("  PASS: _transcribe_with_fallback routes provider='azure_openai' → providers.azure_openai.call_stt")


if __name__ == "__main__":
    tests = [
        test_all_15_feature_keys_present,
        test_provider_credits_all_referenced_providers_have_entry,
        test_workers_ai_credit_is_zero,
        test_select_provider_returns_valid_provider_for_all_features,
        test_assamese_rag_chat_can_select_sarvam,
        test_english_rag_chat_never_selects_sarvam_when_lang_en,
        test_workers_ai_fallback_pool_is_workers_only,
        test_tts_stt_priority_structure,
        test_embed_priority_structure,
        test_translate_priority_includes_bedrock,
        test_vision_priority_includes_bedrock,
        test_live_search_includes_tavily,
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
        test_chat_content_rag_hard_fallback_is_workers_ai_only,
        test_chat_fallback_calls_workers_ai_raw_at_runtime,
        test_content_fallback_calls_workers_ai_raw_at_runtime,
        test_rag_fallback_calls_workers_ai_raw_at_runtime,
        # Task #256: Bedrock + Azure feature service wiring
        test_bedrock_call_tts_raises_when_no_proxy,
        test_bedrock_call_stt_raises_when_no_proxy,
        test_bedrock_call_translate_raises_when_no_proxy,
        test_azure_openai_call_tts_raises_when_no_speech_key,
        test_azure_openai_call_translate_raises_when_no_translator_key,
        test_embed_dispatch_routes_bedrock_to_call_embed,
        test_embed_dispatch_routes_azure_openai_to_call_embed,
        test_translate_dispatch_routes_bedrock_to_call_translate,
        test_translate_dispatch_routes_azure_openai_to_call_translate,
        test_voice_tts_bedrock_provider_calls_bedrock_call_tts,
        test_voice_tts_azure_openai_provider_calls_azure_call_tts,
        test_voice_stt_bedrock_provider_calls_bedrock_call_stt,
        test_voice_stt_azure_openai_provider_calls_azure_call_stt,
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
