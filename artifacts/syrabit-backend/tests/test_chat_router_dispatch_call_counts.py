"""Task #38 integration test — call-count contract on the chat dispatcher.

Pins the runtime behaviour of ``_chat_impl`` for each ``RouteDecision``:

* ``direct`` -> no ``resolve_rag_context``, no embed, no Pinecone, no web.
* ``web``    -> no ``resolve_rag_context``, no embed, no Pinecone; one web call.
* ``rag``    -> exactly one ``resolve_rag_context`` call; no web fallback.

Plus the V4 §12 fail-loud guard for empty web results, and the
language-correct ``lang='as'`` forwarding on Assamese rag turns.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._deps_stub import install_deps_stub  # noqa: E402

install_deps_stub()


def _stub_chat_module(monkeypatch, chat_mod):
    """Stub every IO surface ``_chat_impl`` touches via monkeypatch so all
    mutations are auto-reverted after the test — no module-state leakage
    into the chat_router unit tests that share this process."""
    monkeypatch.setattr(chat_mod, "classify_intent", lambda _q: ("notes", "notes"))
    monkeypatch.setattr(chat_mod, "get_instant_response", lambda _q: None)
    monkeypatch.setattr(chat_mod, "evaluate_prompt_safety", lambda _q: (True, None, ""))
    monkeypatch.setattr(chat_mod, "should_use_pipeline", lambda *_a, **_kw: False)
    monkeypatch.setattr(chat_mod, "stage1_resolve_topic", AsyncMock(return_value=None))
    monkeypatch.setattr(chat_mod, "detect_followup", lambda *_a, **_kw: None)
    monkeypatch.setattr(chat_mod, "compute_answer_budget", lambda *_a, **_kw: 256)
    monkeypatch.setattr(chat_mod, "_resolve_subject_context", AsyncMock(return_value={}))
    monkeypatch.setattr(chat_mod, "_resolve_semester_class_id", AsyncMock(return_value=None))
    monkeypatch.setattr(chat_mod, "_fetch_internal_chapters", AsyncMock(return_value=[]))
    monkeypatch.setattr(chat_mod, "_mb_query_user_memories", AsyncMock(return_value=[]))
    monkeypatch.setattr(chat_mod, "_mb_write_chat_turn_memory", AsyncMock(return_value=None))
    monkeypatch.setattr(
        chat_mod, "get_user_credits",
        AsyncMock(return_value={"used": 0, "limit": 100, "remaining": 100}),
    )
    monkeypatch.setattr(chat_mod, "atomic_deduct_credit", AsyncMock(return_value=True))
    monkeypatch.setattr(chat_mod, "_refund_credit", AsyncMock(return_value=None))
    monkeypatch.setattr(chat_mod, "_persist_chat_turn", AsyncMock(return_value=None))
    monkeypatch.setattr(chat_mod, "_log_chat_message", AsyncMock(return_value=None))
    monkeypatch.setattr(chat_mod, "supa_upsert_conversation", AsyncMock(return_value=None))
    monkeypatch.setattr(chat_mod, "supa_update_conversation", AsyncMock(return_value=None))
    monkeypatch.setattr(chat_mod, "supa_update_user", AsyncMock(return_value=None))
    monkeypatch.setattr(chat_mod, "supa_get_user_by_id", AsyncMock(return_value=None))
    monkeypatch.setattr(chat_mod, "supa_get_conversation", AsyncMock(return_value=None))
    monkeypatch.setattr(chat_mod, "_record_chat_latency", lambda *_a, **_kw: None)
    monkeypatch.setattr(chat_mod, "_record_llm_cost", lambda *_a, **_kw: None)
    monkeypatch.setattr(chat_mod, "build_rag_system_prompt", lambda *_a, **_kw: "system")
    monkeypatch.setattr(chat_mod, "_sources_from_rag_ctx", lambda *_a, **_kw: [])
    monkeypatch.setattr(chat_mod, "_sources_from_web_results", lambda *_a, **_kw: [])
    monkeypatch.setattr(
        chat_mod, "_remap_card_context_source_to_library", lambda *_a, **_kw: None
    )
    monkeypatch.setattr(chat_mod, "redis_client", None)
    monkeypatch.setattr(chat_mod, "ai_cache_aget", AsyncMock(return_value=None))
    monkeypatch.setattr(chat_mod, "ai_cache_aset", AsyncMock(return_value=None))
    chat_mod._ai_response_cache.clear()


def _force_route(monkeypatch, chat_mod, decision: str, *, lang: str = "en",
                 intent: str = "notes"):
    import chat_router

    pinecone_namespace = "en" if (decision == "rag" and lang == "en") else (
        "as" if (decision == "rag" and lang == "as") else ""
    )
    embed_provider = (
        "" if decision == "direct"
        else ("cohere_multilingual_v3_bedrock" if lang == "as" else "workers_ai_custom")
    )
    forced = chat_router.RouteDecision(
        decision=decision,
        reason=f"forced for test ({decision})",
        lang=lang,
        intent=intent,
        topic_score=0.99 if decision == "rag" else (0.0 if decision == "web" else None),
        topic_threshold=0.55,
        provider_chain=("vertex", "vertex_flash_lite", "workers_ai_llama32_3b")
        if lang == "en" else ("sarvam", "vertex_assamese", "retrieval_only"),
        pinecone_namespace=pinecone_namespace,
        embed_provider=embed_provider,
        feature="english_rag_chat" if lang == "en" else "assamese_rag_chat",
    )
    monkeypatch.setattr(chat_mod._chat_router, "route", MagicMock(return_value=forced))
    return forced


@pytest.fixture
def patched_chat_app(monkeypatch):
    from fastapi import FastAPI
    from routes import ai_chat as chat_mod
    from auth_deps import rate_limit_chat_optional

    _stub_chat_module(monkeypatch, chat_mod)

    resolve_mock = AsyncMock(return_value={
        "chunks": [{"text": "stub", "category": "notes"}],
        "chapters": [], "chunk_chapters": [], "subjects": [],
        "vector_hits": [], "source": "internal", "quality": "high",
        "_general_knowledge_fallback": False,
        "_has_internal_content": True,
    })
    web_mock = AsyncMock(return_value=[
        {"title": "t", "url": "https://example.com/x", "snippet": "s"},
    ])
    llm_mock = AsyncMock(return_value="OK ANSWER")

    monkeypatch.setattr(chat_mod, "resolve_rag_context", resolve_mock, raising=True)
    monkeypatch.setattr(chat_mod, "web_search_with_fallback", web_mock, raising=True)
    monkeypatch.setattr(chat_mod, "call_llm_api_chat", llm_mock, raising=True)

    embed_mock = AsyncMock(return_value=[0.0] * 1024)
    pinecone_query_mock = AsyncMock(return_value=[])

    import llm as _llm_mod
    monkeypatch.setattr(_llm_mod, "call_embed_with_dispatch", embed_mock, raising=False)

    try:
        from retrievers import pinecone_vector as _pcv

        class _SpyRetriever:
            def is_configured(self):
                return True

            async def query(self, *a, **kw):
                # Awaited so AsyncMock.await_count actually increments —
                # otherwise an unawaited call would silently mask a real
                # Pinecone hit and turn the contract into a false negative.
                return await pinecone_query_mock(*a, **kw)

        monkeypatch.setattr(
            _pcv, "PineconeVectorRetriever",
            lambda *_a, **_kw: _SpyRetriever(), raising=False,
        )
    except Exception:
        pass

    async def _auth_user():
        return {"id": "u-router-dispatch-test", "plan": "free",
                "email": "router-dispatch@test"}

    app = FastAPI()
    app.include_router(chat_mod.router, prefix="/api")
    app.dependency_overrides[rate_limit_chat_optional] = _auth_user

    return {
        "app": app,
        "chat_mod": chat_mod,
        "monkeypatch": monkeypatch,
        "resolve_mock": resolve_mock,
        "web_mock": web_mock,
        "llm_mock": llm_mock,
        "embed_mock": embed_mock,
        "pinecone_query_mock": pinecone_query_mock,
    }


def _post_chat(client, **overrides):
    body = {
        "message": overrides.get("message", "explain photosynthesis"),
        "response_lang": overrides.get("response_lang", "en"),
        "subject_id": overrides.get("subject_id", ""),
        "board_id": overrides.get("board_id", ""),
        "conversation_id": overrides.get("conversation_id", ""),
    }
    return client.post("/api/ai/chat", json=body)


def _assert_no_pinecone_or_embed(state):
    assert state["embed_mock"].await_count == 0
    assert state["embed_mock"].call_count == 0
    assert state["pinecone_query_mock"].await_count == 0
    assert state["pinecone_query_mock"].call_count == 0


def test_direct_decision_skips_pinecone_embed_and_web(patched_chat_app):
    from fastapi.testclient import TestClient

    chat_mod = patched_chat_app["chat_mod"]
    _force_route(patched_chat_app["monkeypatch"], chat_mod, "direct",
                 lang="en", intent="casual")

    client = TestClient(patched_chat_app["app"])
    resp = _post_chat(client, message="hi there")
    assert resp.status_code == 200, resp.text
    assert resp.json()["route_trace"]["decision"] == "direct"

    assert patched_chat_app["resolve_mock"].await_count == 0
    assert patched_chat_app["web_mock"].await_count == 0
    _assert_no_pinecone_or_embed(patched_chat_app)
    assert patched_chat_app["llm_mock"].await_count == 1


def test_web_decision_skips_pinecone_and_calls_web_once(patched_chat_app):
    from fastapi.testclient import TestClient

    chat_mod = patched_chat_app["chat_mod"]
    _force_route(patched_chat_app["monkeypatch"], chat_mod, "web",
                 lang="en", intent="general")

    client = TestClient(patched_chat_app["app"])
    resp = _post_chat(client, message="who won the cricket match yesterday")
    assert resp.status_code == 200, resp.text
    assert resp.json()["route_trace"]["decision"] == "web"

    assert patched_chat_app["resolve_mock"].await_count == 0
    _assert_no_pinecone_or_embed(patched_chat_app)
    assert patched_chat_app["web_mock"].await_count == 1


def test_web_decision_with_zero_results_fails_loud(patched_chat_app):
    from fastapi.testclient import TestClient

    chat_mod = patched_chat_app["chat_mod"]
    _force_route(patched_chat_app["monkeypatch"], chat_mod, "web",
                 lang="en", intent="general")
    patched_chat_app["web_mock"].return_value = []

    client = TestClient(patched_chat_app["app"])
    resp = _post_chat(client, message="off-syllabus query that finds nothing")
    assert resp.status_code == 503, resp.text


def test_rag_decision_calls_resolve_rag_context_once(patched_chat_app):
    from fastapi.testclient import TestClient

    chat_mod = patched_chat_app["chat_mod"]
    _force_route(patched_chat_app["monkeypatch"], chat_mod, "rag",
                 lang="en", intent="notes")

    client = TestClient(patched_chat_app["app"])
    resp = _post_chat(client, message="explain photosynthesis")
    assert resp.status_code == 200, resp.text
    assert resp.json()["route_trace"]["decision"] == "rag"

    assert patched_chat_app["resolve_mock"].await_count == 1
    assert patched_chat_app["web_mock"].await_count == 0


def test_rag_decision_assamese_routes_resolve_with_lang_as(patched_chat_app):
    from fastapi.testclient import TestClient

    chat_mod = patched_chat_app["chat_mod"]
    _force_route(patched_chat_app["monkeypatch"], chat_mod, "rag",
                 lang="as", intent="notes")

    client = TestClient(patched_chat_app["app"])
    resp = _post_chat(client, message="ফটোসিন্থেসিস কি", response_lang="as")
    assert resp.status_code == 200, resp.text

    assert patched_chat_app["resolve_mock"].await_count == 1
    assert patched_chat_app["resolve_mock"].await_args.kwargs.get("lang") == "as"


def test_dispatcher_gate_is_real_not_observability_only(patched_chat_app):
    """Sentinel: if the gate were observability-only, forcing 'direct'
    would still trigger resolve_rag_context. Switch decisions between
    turns and assert the call count of resolve_rag_context only moves on
    rag turns. Pins the dispatcher refactor at routes/ai_chat.py
    L1039-1051 (and the streaming mirror at L2829)."""
    from fastapi.testclient import TestClient

    chat_mod = patched_chat_app["chat_mod"]
    mp = patched_chat_app["monkeypatch"]
    client = TestClient(patched_chat_app["app"])

    _force_route(mp, chat_mod, "rag", lang="en", intent="notes")
    assert _post_chat(client, message="explain mitosis").status_code == 200
    rag_calls_after_rag = patched_chat_app["resolve_mock"].await_count

    _force_route(mp, chat_mod, "direct", lang="en", intent="casual")
    assert _post_chat(client, message="hello there").status_code == 200
    rag_calls_after_direct = patched_chat_app["resolve_mock"].await_count

    assert rag_calls_after_rag == 1
    assert rag_calls_after_direct == 1, (
        "direct turn must NOT invoke resolve_rag_context — if this fails "
        "the dispatcher is observability-only and Task #38 is unmet"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
