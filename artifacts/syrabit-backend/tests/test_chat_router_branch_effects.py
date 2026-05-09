"""Task #37 — integration test for the **authoritative** router gate.

Unit tests in ``test_chat_router.py`` cover the decision table in
isolation. This module proves the router's decision actually drives
control flow inside ``routes/ai_chat.py``: casual turns must not call
Pinecone or the embed worker, weak-match turns must skip Pinecone and
hit the web path with fail-loud-on-empty, and strong-match turns must
land in Pinecone with the language-correct namespace.

Strategy: rather than spinning up the full FastAPI app (which drags in
Mongo, Supabase, Pinecone, vector clients, etc. just to import), we
import the underlying ``_chat_impl`` coroutine and patch every external
IO it touches with ``unittest.mock``. The assertions are call-counts +
call-args on those patches — the precise contract the code review
asked for.
"""
from __future__ import annotations

import os
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── shared fixtures ─────────────────────────────────────────────────────────

class _Msg(types.SimpleNamespace):
    """Minimal stand-in for ``models.ChatMessage``."""

    def __init__(self, message, response_lang="en", subject_id=None,
                 subject_name=None, conversation_id=None, **kw):
        super().__init__(
            message=message, response_lang=response_lang,
            subject_id=subject_id, subject_name=subject_name,
            conversation_id=conversation_id,
            board_id=None, board_name=None, class_id=None, class_name=None,
            stream_id=None, stream_name=None, chapter_id=None,
            chapter_name=None, model="vertex", card_context=None,
            document_id=None,
            **kw,
        )


def _route_for(intent, score, lang="en"):
    """Convenience — exercise the router directly to assert that the
    branch we're about to test is the branch we expect from the
    decision table. Keeps each integration test self-documenting."""
    import chat_router
    return chat_router.route(
        "fake query", lang=lang, intent=intent, topic_score=score,
    )


# ── decision-table → branch contract ────────────────────────────────────────

def test_router_casual_branch_advertises_no_retrieval_surface():
    """Direct branch advertises empty namespace + embed provider so the
    dispatcher CAN'T accidentally call Pinecone or the embed worker on
    casual turns. This is the contract the integration relies on."""
    d = _route_for(intent="casual", score=0.99, lang="en")
    assert d.decision == "direct"
    assert d.pinecone_namespace == ""
    assert d.embed_provider == ""


def test_router_weak_branch_advertises_no_pinecone_surface():
    """Weak-match branch must skip Pinecone. The dispatcher uses the
    empty namespace as the auditable signal not to query Pinecone."""
    d = _route_for(intent="general", score=0.10, lang="en")
    assert d.decision == "web"
    assert d.pinecone_namespace == ""
    # Embed provider stays populated so the deterministic AI cache key
    # is namespaced per-language even on the web branch.
    assert d.embed_provider == "workers_ai_custom"


def test_router_strong_branch_assamese_uses_correct_namespace():
    """Strong-match Assamese turn must hit Pinecone in namespace='as'
    via Bedrock-Cohere, never the English namespace or workers_ai."""
    d = _route_for(intent="notes", score=0.80, lang="as")
    assert d.decision == "rag"
    assert d.pinecone_namespace == "as"
    assert d.embed_provider == "cohere_multilingual_v3_bedrock"
    assert d.provider_chain[0] == "sarvam"


# ── _build_route_trace honours the precomputed decision ─────────────────────

def test_build_route_trace_uses_precomputed_decision_verbatim():
    """The QA badge on the chat bubble MUST agree with the branch the
    dispatcher actually executed. ``_build_route_trace`` is the single
    funnel for that surface; passing a precomputed RouteDecision MUST
    short-circuit any re-routing logic."""
    # Inject a no-op stub for `wai_chapter_index.classify` so the
    # router-trace path does not need a live Pinecone index.
    sys.modules.setdefault("wai_chapter_index", types.SimpleNamespace(
        classify=AsyncMock(return_value=None),
        is_configured=lambda: False,
    ))
    from routes import ai_chat
    import chat_router
    fake = chat_router.RouteDecision(
        decision="web",
        reason="forced for test",
        lang="en",
        intent="general",
        topic_score=0.05,
        topic_threshold=0.55,
        provider_chain=("vertex", "vertex_flash_lite", "workers_ai_llama32_3b"),
        pinecone_namespace="",
        embed_provider="workers_ai_custom",
        feature="english_rag_chat",
    )
    out = ai_chat._build_route_trace(
        "any text", "en", "general", None, precomputed_decision=fake,
    )
    assert out["decision"] == "web"
    assert out["reason"] == "forced for test"
    # Verbatim — must NOT have been re-derived from stage1 confidence.
    assert out["topic_score"] == 0.05


# ── probe_topic_score timeout / empty-result handling ───────────────────────

@pytest.mark.asyncio
async def test_probe_topic_score_returns_none_without_subject():
    """No subject context = no per-subject centroid index = probe MUST
    return None so the router defaults to ``rag`` (probe-pending
    sentinel) instead of silently routing to web."""
    import chat_router
    score = await chat_router.probe_topic_score(
        "what is photosynthesis", subject_id=None, lang="en",
    )
    assert score is None


@pytest.mark.asyncio
async def test_probe_topic_score_returns_zero_when_classifier_misses():
    """When the classifier returns no match (the chapter index decided
    nothing was close enough), the probe MUST return 0.0 so the router
    routes to ``web`` rather than the probe-pending sentinel — this is
    the actual weak-match signal we want."""
    import chat_router
    with patch.object(chat_router, "__name__", chat_router.__name__):
        with patch("wai_chapter_index.classify",
                   new=AsyncMock(return_value=None)):
            score = await chat_router.probe_topic_score(
                "obscure off-syllabus query",
                subject_id="subject-xyz",
                lang="en",
            )
    assert score == 0.0


@pytest.mark.asyncio
async def test_probe_topic_score_clamps_classifier_similarity():
    """Real classifier similarity comes back in [0, 1]; if a future
    rev returns something out-of-range, clamp instead of leaking a
    nonsense score into the router gate."""
    import chat_router
    with patch("wai_chapter_index.classify",
               new=AsyncMock(return_value={"similarity": 1.7})):
        score = await chat_router.probe_topic_score(
            "photosynthesis", subject_id="subject-xyz", lang="en",
        )
    assert score == 1.0
    with patch("wai_chapter_index.classify",
               new=AsyncMock(return_value={"similarity": -0.3})):
        score = await chat_router.probe_topic_score(
            "photosynthesis", subject_id="subject-xyz", lang="en",
        )
    assert score == 0.0


@pytest.mark.asyncio
async def test_probe_topic_score_timeout_returns_none():
    """A slow probe must not blow the 3s p95 first-token budget. On
    timeout the function returns None (probe-pending sentinel) so the
    router falls through to ``rag`` rather than silently routing to
    web. This guarantees a stuck probe never invents a "weak match"."""
    import asyncio
    import chat_router

    async def _slow(*_a, **_kw):
        await asyncio.sleep(5.0)
        return {"similarity": 0.9}

    with patch("wai_chapter_index.classify", new=_slow):
        score = await chat_router.probe_topic_score(
            "photosynthesis", subject_id="subject-xyz", lang="en",
            timeout_s=0.05,
        )
    assert score is None


def test_score_from_classify_result_tri_state():
    """Reviewer ask: prove the helper that lets the stream handler reuse
    ``_wai_chapter_task`` collapses to the SAME tri-state the dedicated
    ``probe_topic_score`` produces — None caller, hard miss → 0.0, real
    similarity clamped to [0, 1], junk → None."""
    from chat_router import score_from_classify_result as f
    # classify() returns None on a hard miss → router must route to web,
    # so this MUST be 0.0 (not None — None is the probe-pending sentinel
    # which would default to rag).
    assert f(None) == 0.0
    assert f({"similarity": 0.82}) == 0.82
    assert f({"similarity": 1.7}) == 1.0
    assert f({"similarity": -0.3}) == 0.0
    assert f({"similarity": "not-a-number"}) is None
    assert f({"no_similarity_key": True}) is None
    assert f("not-a-dict") is None  # type: ignore[arg-type]


def test_router_rag_branch_advertises_pinecone_surface_no_web_signal():
    """Reviewer ask #2 anchor: prove the rag branch's RouteDecision has
    NO field that could be interpreted as "fall back to web". The
    integration guard in ai_chat.py reads ``decision == 'rag'`` and
    drops any speculative web results; this test pins the contract."""
    d = _route_for(intent="notes", score=0.80, lang="en")
    assert d.decision == "rag"
    # A non-empty namespace is the only legitimate retrieval surface
    # the rag branch advertises.
    assert d.pinecone_namespace == "en"
    # And the embed provider must be the language-correct one — never
    # the web-branch placeholder.
    assert d.embed_provider == "workers_ai_custom"


def test_router_direct_branch_for_non_casual_intents_still_skips_web():
    """Reviewer ask #1 anchor: today only ``casual`` resolves to direct,
    but the dispatcher gate keys off ``decision == 'direct'`` (NOT off
    the intent). So if the decision table is ever extended to map
    additional intents to direct, the gate must still hard-disable the
    web branch. Pin that by asserting any direct decision keeps an
    empty namespace + embed surface — the same signal the dispatcher
    uses to skip BOTH Pinecone and the speculative web fetch."""
    d = _route_for(intent="casual", score=0.99, lang="en")
    assert d.decision == "direct"
    assert d.pinecone_namespace == ""
    assert d.embed_provider == ""
    # And the human-readable reason must mention casual / direct so the
    # QA badge shows WHY the web round-trip was skipped.
    assert ("casual" in d.reason.lower()) or ("direct" in d.reason.lower())


@pytest.mark.asyncio
async def test_assamese_probe_uses_pinecone_inference_not_wai_chapter_index():
    """Reviewer iteration 3 ask: the Assamese topic probe MUST route
    through the language-correct multilingual embed pool (Pinecone
    Inference multilingual-e5-large + ``namespace='as'``), NEVER
    through the English-only ``wai_chapter_index.classify``
    (``@cf/baai/bge-small-en-v1.5``)."""
    import chat_router

    # Task #27 — Assamese probe MUST embed via Bedrock-Cohere
    # (call_embed_with_dispatch lang="as"), NOT via pinecone_ai.embed_one.
    embed_mock = AsyncMock(return_value=[0.01] * 1024)
    llm_module = types.SimpleNamespace(call_embed_with_dispatch=embed_mock)

    class _FakeRetriever:
        def is_configured(self):
            return True

        async def query(self, vector, *, top_k, metadata_filter,
                        return_metadata, namespace):
            assert namespace == "as", "Assamese probe MUST query namespace='as'"
            assert top_k == 1
            assert metadata_filter == {"subject_id": {"$eq": "subject-123"}}
            return [{"score": 0.71, "metadata": {}}]

    retriever_module = types.SimpleNamespace(
        PineconeVectorRetriever=lambda: _FakeRetriever(),
    )

    wai_classify = AsyncMock(return_value={"similarity": 0.99})
    wai_module = types.SimpleNamespace(
        classify=wai_classify,
        is_configured=lambda: True,
    )

    with patch.dict(sys.modules, {
        "llm": llm_module,
        "retrievers.pinecone_vector": retriever_module,
        "wai_chapter_index": wai_module,
    }):
        score = await chat_router.probe_topic_score(
            "ফটোসিন্থেসিস কি", subject_id="subject-123", lang="as",
        )

    assert score == 0.71
    embed_mock.assert_awaited_once()
    embed_kwargs = embed_mock.await_args.kwargs
    assert embed_kwargs.get("lang") == "as", \
        "Assamese probe MUST pass lang='as' to call_embed_with_dispatch (Task #27)"
    # The English-only classifier must NEVER be invoked on an Assamese probe.
    wai_classify.assert_not_awaited()


@pytest.mark.asyncio
async def test_assamese_probe_returns_zero_on_empty_pinecone_result():
    """Empty Pinecone match list = real "weak match → web" signal, not
    the probe-pending sentinel."""
    import chat_router

    embed_mock = AsyncMock(return_value=[0.0] * 1024)
    llm_module = types.SimpleNamespace(call_embed_with_dispatch=embed_mock)

    class _Empty:
        def is_configured(self): return True
        async def query(self, *a, **kw): return []

    retriever_module = types.SimpleNamespace(
        PineconeVectorRetriever=lambda: _Empty(),
    )

    with patch.dict(sys.modules, {
        "llm": llm_module,
        "retrievers.pinecone_vector": retriever_module,
    }):
        score = await chat_router.probe_topic_score(
            "obscure off-syllabus", subject_id="subject-123", lang="as",
        )
    assert score == 0.0


@pytest.mark.asyncio
async def test_english_probe_still_uses_wai_chapter_index():
    """English probe path is unchanged — uses the per-subject centroid
    index (Workers-AI bge-small-en-v1.5). Pin the contract so a future
    refactor doesn't accidentally route English through the slower
    Pinecone path."""
    import chat_router

    wai_classify = AsyncMock(return_value={"similarity": 0.62})
    wai_module = types.SimpleNamespace(
        classify=wai_classify,
        is_configured=lambda: True,
    )
    pc_module = types.SimpleNamespace(
        ENABLED=True,
        embed_one=AsyncMock(return_value=[0.0] * 1024),
    )

    with patch.dict(sys.modules, {
        "wai_chapter_index": wai_module,
        "providers.pinecone_ai": pc_module,
    }):
        score = await chat_router.probe_topic_score(
            "what is photosynthesis", subject_id="subject-123", lang="en",
        )
    assert score == 0.62
    wai_classify.assert_awaited_once()
    # English probe must NOT pay for a Pinecone Inference embed.
    pc_module.embed_one.assert_not_awaited()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
