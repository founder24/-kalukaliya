"""Smoke test — Task #250 provider dispatch coverage for all 15 feature keys.

Run from the syrabit-backend directory:
    python -m pytest tests/test_provider_dispatch.py -v
or standalone:
    python tests/test_provider_dispatch.py
"""
from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import logging
logging.disable(logging.CRITICAL)

from config import PROVIDER_PRIORITY, PROVIDER_CREDITS, cf_gateway_url


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
    from providers import bedrock
    slug_url = cf_gateway_url("bedrock")
    assert "aws-bedrock" in slug_url, f"Expected aws-bedrock slug, got: {slug_url}"
    print(f"  PASS: bedrock CF slug resolves to: {slug_url}")


def test_azure_openai_uses_cf_gateway_slug():
    from providers import azure_openai
    slug_url = cf_gateway_url("azure_openai")
    assert "azure-openai" in slug_url, f"Expected azure-openai slug, got: {slug_url}"
    print(f"  PASS: azure_openai CF slug resolves to: {slug_url}")


def test_all_main_llm_entrypoints_wired():
    import inspect
    from llm import call_llm_api_chat, call_llm_for_rag, call_llm_api_content
    for fn in (call_llm_api_chat, call_llm_for_rag, call_llm_api_content):
        src = inspect.getsource(fn)
        assert "call_with_provider_fallback" in src, f"{fn.__name__}: not wired through call_with_provider_fallback"
        assert "_dispatch_llm_for_feature" in src, f"{fn.__name__}: _dispatch_llm_for_feature not used"
    print("  PASS: call_llm_api_chat / call_llm_for_rag / call_llm_api_content all wired")


def test_dispatch_fn_has_bedrock_and_azure_callers():
    import inspect
    from llm import _dispatch_llm_for_feature
    src = inspect.getsource(_dispatch_llm_for_feature)
    assert "bedrock" in src and "call_converse" in src, "bedrock not wired in _dispatch_llm_for_feature"
    assert "azure_openai" in src and "call_chat" in src, "azure_openai not wired in _dispatch_llm_for_feature"
    assert "_LLM_PROVIDERS_WORKERS_ONLY" in src, "_LLM_PROVIDERS_WORKERS_ONLY not in dispatch fn"
    print("  PASS: _dispatch_llm_for_feature has bedrock + azure_openai + workers-only callers")


def test_safety_feature_key_priority_has_bedrock_first():
    safety_list = PROVIDER_PRIORITY.get("safety", [])
    assert safety_list, "safety feature key missing from PROVIDER_PRIORITY"
    assert safety_list[0] == "bedrock", f"safety: expected bedrock first, got {safety_list}"
    assert "workers_ai" in safety_list, "safety: workers_ai fallback missing"
    print(f"  PASS: safety priority list = {safety_list}")


def test_llm_safety_check_is_exported():
    from guardrails.prompt_safety import llm_classify_safety
    import inspect
    assert inspect.iscoroutinefunction(llm_classify_safety), "llm_classify_safety must be async"
    src = inspect.getsource(llm_classify_safety)
    assert "safety" in src, "llm_classify_safety must reference 'safety' feature key"
    print("  PASS: guardrails.prompt_safety.llm_classify_safety is async and references safety feature key")


if __name__ == "__main__":
    tests = [
        test_all_15_feature_keys_present,
        test_provider_credits_all_referenced_providers_have_entry,
        test_workers_ai_credit_is_zero,
        test_select_provider_returns_valid_provider_for_all_features,
        test_assamese_rag_chat_can_select_sarvam,
        test_english_rag_chat_never_selects_sarvam_when_lang_en,
        test_workers_ai_fallback_pool_is_workers_only,
        test_assemblyai_uses_cf_gateway_url,
        test_bedrock_uses_cf_gateway_slug,
        test_azure_openai_uses_cf_gateway_slug,
        test_all_main_llm_entrypoints_wired,
        test_dispatch_fn_has_bedrock_and_azure_callers,
        test_safety_feature_key_priority_has_bedrock_first,
        test_llm_safety_check_is_exported,
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
