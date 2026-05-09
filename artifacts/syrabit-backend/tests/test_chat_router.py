"""Task #37 — chat_router decision-table tests.

Covers:
* casual / metadata-only intents → ``direct`` (no Pinecone, no embed).
* strong topic match → ``rag`` with the language-correct namespace +
  embed provider (English → workers_ai_custom, Assamese → Bedrock-Cohere).
* weak topic match → ``web``.
* lang_profile() is the single source of truth for provider chain +
  Pinecone namespace + embed provider — anything outside ``en``/``as``
  collapses to English.
* CHAT_ROUTER_TOPIC_THRESHOLD env override + clamp behaviour.

Run::

    python -m pytest tests/test_chat_router.py -v
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import chat_router


# ── lang_profile ─────────────────────────────────────────────────────────────

def test_lang_profile_english_canonical():
    p = chat_router.lang_profile("en")
    assert p["lang"] == "en"
    assert p["feature"] == "english_rag_chat"
    assert p["pinecone_namespace"] == "en"
    assert p["embed_provider"] == "workers_ai_custom"
    assert p["provider_chain"] == ("vertex", "vertex_flash_lite", "workers_ai_llama32_3b")


def test_lang_profile_assamese_uses_bedrock_cohere_and_sarvam_head():
    p = chat_router.lang_profile("as")
    assert p["lang"] == "as"
    assert p["feature"] == "assamese_rag_chat"
    assert p["pinecone_namespace"] == "as"
    # Task #27 — Indic embed routes through Bedrock-Cohere.
    assert p["embed_provider"] == "cohere_multilingual_v3_bedrock"
    # Founder lock — Sarvam is the sole Assamese head.
    assert p["provider_chain"][0] == "sarvam"


def test_lang_profile_unknown_falls_back_to_english():
    # Future Indic codes that aren't wired yet must collapse to the
    # English profile — never silently route Bengali through the
    # Assamese chain.
    for code in ("bn", "hi", "fr", "", None):
        p = chat_router.lang_profile(code)
        assert p["lang"] == "en", f"{code!r} should default to en"


# ── direct branch (casual short-circuit + metadata-only intents) ─────────────

def test_casual_intent_short_circuits_to_direct_no_pinecone_no_embed():
    d = chat_router.route("hi there", lang="en", intent="casual",
                          topic_score=0.95)  # score must be ignored
    assert d.decision == "direct"
    # The direct branch MUST NOT advertise a namespace or embed provider
    # — surfacing those would invite an accidental Pinecone or embed
    # call from the dispatcher.
    assert d.pinecone_namespace == ""
    assert d.embed_provider == ""
    assert "casual" in d.reason


def test_casual_intent_assamese_also_direct():
    d = chat_router.route("নমস্কাৰ", lang="as", intent="casual")
    assert d.decision == "direct"
    assert d.lang == "as"
    # Even on the direct branch, the provider chain must reflect the
    # selector so the LLM dispatcher uses Sarvam (not the English chain).
    assert d.provider_chain[0] == "sarvam"


def test_syllabus_intent_skips_probe():
    d = chat_router.route("show me the class 12 syllabus", lang="en",
                          intent="syllabus")
    assert d.decision == "direct"
    assert "metadata-only" in d.reason


# ── rag branch (strong topic match) ──────────────────────────────────────────

def test_strong_topic_match_routes_to_rag_in_english_namespace():
    d = chat_router.route("explain photosynthesis", lang="en",
                          intent="notes", topic_score=0.82)
    assert d.decision == "rag"
    assert d.pinecone_namespace == "en"
    assert d.embed_provider == "workers_ai_custom"


def test_strong_topic_match_assamese_routes_to_as_namespace_with_bedrock():
    d = chat_router.route("ফটোসিন্থেসিস কি", lang="as",
                          intent="notes", topic_score=0.71)
    assert d.decision == "rag"
    assert d.pinecone_namespace == "as"
    assert d.embed_provider == "cohere_multilingual_v3_bedrock"
    assert d.provider_chain[0] == "sarvam"


# ── web branch (weak topic match) ────────────────────────────────────────────

def test_weak_topic_match_falls_back_to_web():
    d = chat_router.route("who won the cricket match yesterday",
                          lang="en", intent="general", topic_score=0.20)
    assert d.decision == "web"
    # Web branch must NOT query Pinecone but still records embed
    # provider so the deterministic cache key is namespaced correctly.
    assert d.pinecone_namespace == ""
    assert d.embed_provider == "workers_ai_custom"


def test_weak_topic_match_assamese_falls_back_to_web_with_indic_embed_provider():
    d = chat_router.route("কালি ক্ৰিকেট স্ক'ৰ", lang="as",
                          intent="general", topic_score=0.10)
    assert d.decision == "web"
    assert d.pinecone_namespace == ""
    assert d.embed_provider == "cohere_multilingual_v3_bedrock"


# ── threshold env override ───────────────────────────────────────────────────

def test_threshold_env_override_lifts_gate_so_marginal_score_routes_to_web(monkeypatch):
    monkeypatch.setenv("CHAT_ROUTER_TOPIC_THRESHOLD", "0.75")
    d = chat_router.route("explain mitosis", lang="en", intent="notes",
                          topic_score=0.60)  # above default 0.55, below 0.75
    assert d.decision == "web"
    # Explicit `threshold` param should beat the env override.
    d2 = chat_router.route("explain mitosis", lang="en", intent="notes",
                           topic_score=0.60, threshold=0.50)
    assert d2.decision == "rag"


def test_threshold_env_out_of_range_clamps_to_default(monkeypatch):
    monkeypatch.setenv("CHAT_ROUTER_TOPIC_THRESHOLD", "1.5")
    d = chat_router.route("explain mitosis", lang="en", intent="notes",
                          topic_score=0.60)
    # Out-of-range value must clamp to the default 0.55, not silently
    # disable the RAG branch.
    assert d.decision == "rag"


# ── probe-pending sentinel ───────────────────────────────────────────────────

def test_probe_pending_returns_rag_sentinel_when_score_missing():
    """When the caller hasn't yet run the probe, route() should return
    a sentinel `rag` decision so the caller knows to embed + re-route.
    Prevents the silent "score=None → web fallback" failure mode."""
    d = chat_router.route("explain photosynthesis", lang="en",
                          intent="notes", topic_score=None)
    assert d.decision == "rag"
    assert d.extra.get("probe_pending") is True
    assert d.pinecone_namespace == "en"


# ── serialisation ────────────────────────────────────────────────────────────

def test_route_decision_to_dict_is_jsonable():
    import json
    d = chat_router.route("hello", lang="en", intent="casual")
    payload = d.to_dict()
    s = json.dumps(payload)  # must not raise
    parsed = json.loads(s)
    assert parsed["decision"] == "direct"
    assert isinstance(parsed["provider_chain"], list)


# ── guard: direct branch contracts ───────────────────────────────────────────

def test_direct_branch_advertises_no_retrieval_surface_for_any_lang():
    """Belt-and-braces guard. The `direct` branch must NEVER advertise
    a non-empty namespace or embed provider, irrespective of language —
    that's the contract the dispatcher relies on to skip Pinecone +
    Workers-AI / Bedrock embed calls on casual turns."""
    for lang in ("en", "as", "hi", ""):
        d = chat_router.route("hello", lang=lang, intent="casual",
                              topic_score=0.99)
        assert d.decision == "direct"
        assert d.pinecone_namespace == "", lang
        assert d.embed_provider == "", lang


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
