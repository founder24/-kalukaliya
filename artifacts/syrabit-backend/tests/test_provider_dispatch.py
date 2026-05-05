"""Provider-dispatch smoke tests — Task #347 steady state.

Run from the syrabit-backend directory:
    python -m pytest tests/test_provider_dispatch.py -v

This file was rewritten in Task #347 to drop every Bedrock-specific
assertion (Bedrock has been fully decommissioned: providers/bedrock.py
deleted, every elif branch removed, no PROVIDER_PRIORITY pool routes
to ``bedrock``). The remaining tests cover the post-cleanup invariants:

  * PROVIDER_PRIORITY structure (every feature key, all entries present
    in PROVIDER_CREDITS). Count is derived from ``len(PROVIDER_PRIORITY)``
    rather than a hard-coded number — see Task #368 for the rationale
    (the historical "15" silently went stale once embed was split into
    embed/embed_en/embed_indic).
  * Inverse Bedrock invariant — must NOT appear in any pool / weight map.
  * Workers AI promotion aliases (mistral_7b, llama32_3b, llama31_8b)
    are wired into _PROVIDER_DEFAULT_MODELS + _PROVIDER_CANONICAL so the
    weighted draw cannot raise KeyError.
  * select_provider returns a valid provider for every feature key.
  * The locked Task #291 chains (translate, embed, tts/stt/voice) still
    have their primaries first.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import logging

# Suppress noisy import-time logging from config/llm without leaking the
# global ``logging.disable`` state into other test modules. Task #402:
# leaving ``logging.disable(logging.CRITICAL)`` set at module scope made
# tests/test_vertex_startup_probe.py fail 5/12 cases when run after this
# file (the probe asserts on captured ERROR records, which a non-NOTSET
# disable level silently swallows). Save the previous level, suppress
# only across the import that needs it, then restore.
_PREV_LOGGING_DISABLE_LEVEL = logging.root.manager.disable
logging.disable(logging.CRITICAL)
try:
    from config import PROVIDER_PRIORITY, PROVIDER_CREDITS
finally:
    logging.disable(_PREV_LOGGING_DISABLE_LEVEL)


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


def test_provider_credits_all_referenced_providers_have_entry():
    all_providers = set()
    for providers in PROVIDER_PRIORITY.values():
        all_providers.update(providers)
    missing = {p for p in all_providers if p not in PROVIDER_CREDITS}
    assert not missing, (
        f"Providers in PROVIDER_PRIORITY but missing from PROVIDER_CREDITS: {missing}"
    )


# ── Task #347: bedrock fully decommissioned ──────────────────────────────────

def test_task_347_bedrock_absent_from_every_pool():
    """Bedrock must not appear in any PROVIDER_PRIORITY pool or POOL_WEIGHTS map."""
    from config import POOL_WEIGHTS
    for pool, providers in PROVIDER_PRIORITY.items():
        assert "bedrock" not in providers, (
            f"PROVIDER_PRIORITY[{pool!r}] still lists 'bedrock' (Task #347)"
        )
    for pool, weights in POOL_WEIGHTS.items():
        assert "bedrock" not in weights, (
            f"POOL_WEIGHTS[{pool!r}] still has a 'bedrock' weight (Task #347)"
        )


# ── Task #347: every PROVIDER_PRIORITY alias must have a default model ───────

def test_task_347_every_priority_alias_has_default_model_and_canonical():
    """KeyError-prevention guard: ``_PROVIDER_DEFAULT_MODELS[p]`` and
    ``_PROVIDER_CANONICAL[p]`` lookups in ``call_with_provider_fallback``
    must succeed for every provider that the weighted draw can return.

    Regression for the Task #347 review finding that
    ``workers_ai_mistral_7b`` / ``workers_ai_llama32_3b`` /
    ``workers_ai_llama31_8b`` were added to PROVIDER_PRIORITY without
    matching dispatch entries.
    """
    from llm import _PROVIDER_DEFAULT_MODELS, _PROVIDER_CANONICAL
    referenced: set[str] = set()
    for pool in PROVIDER_PRIORITY.values():
        referenced.update(pool)
    # Some providers (assemblyai, deepgram, elevenlabs, mongodb_atlas etc.)
    # have their own dispatch surfaces and don't go through the chat
    # dispatch path — only LLM-style providers must have model + canonical.
    chat_like = {
        "vertex", "azure_openai", "sarvam", "workers_ai",
        "workers_ai_indic",
        "workers_ai_mistral_7b", "workers_ai_llama32_3b",
        "workers_ai_llama31_8b",
    }
    for provider in referenced & chat_like:
        assert provider in _PROVIDER_DEFAULT_MODELS, (
            f"{provider!r} appears in PROVIDER_PRIORITY but is missing "
            f"from llm._PROVIDER_DEFAULT_MODELS — call_with_provider_fallback "
            f"will raise KeyError when the weighted draw selects it."
        )
        assert provider in _PROVIDER_CANONICAL, (
            f"{provider!r} appears in PROVIDER_PRIORITY but is missing "
            f"from llm._PROVIDER_CANONICAL — call_with_provider_fallback "
            f"will raise KeyError when the weighted draw selects it."
        )


# ── select_provider sanity ────────────────────────────────────────────────────

def test_select_provider_returns_valid_provider_for_all_features():
    from llm import select_provider
    for feature, providers in PROVIDER_PRIORITY.items():
        lang = "as" if "assamese" in feature else "en"
        result = select_provider(feature, lang=lang)
        assert result in PROVIDER_CREDITS, (
            f"{feature}: select_provider returned unknown provider {result!r}"
        )
        assert result in providers, (
            f"{feature}: select_provider returned {result!r} not in priority list {providers}"
        )


# ── Locked-chain invariants (Task #291, preserved through #347) ──────────────

def test_translate_priority_locked_chain():
    """translate primary must be ``workers_ai_indic`` (IndicTrans2)."""
    pool = PROVIDER_PRIORITY.get("translate", [])
    assert pool and pool[0] == "workers_ai_indic", (
        f"translate: workers_ai_indic must be first in the locked chain, got {pool}"
    )
    assert "vertex" in pool, "translate: vertex must remain as Gemini fallback"


def test_embed_priority_chain_intact():
    """embed pool must terminate in workers_ai (free-tier last resort)."""
    pool = PROVIDER_PRIORITY.get("embed", [])
    assert pool, "embed pool must not be empty"
    assert pool[-1] == "workers_ai", (
        f"embed: workers_ai must be the last-resort tail, got {pool}"
    )


def test_tts_stt_voice_have_workers_ai_tail():
    """tts/stt/voice pools must end with workers_ai as the free-tier last resort."""
    for feature in ("tts", "stt", "voice"):
        pool = PROVIDER_PRIORITY.get(feature, [])
        assert pool and pool[-1] == "workers_ai", (
            f"{feature}: workers_ai must be the last entry, got {pool}"
        )
