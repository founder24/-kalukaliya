"""Task #513 §C — tier-routing on-wire enforcement regression test.

Asserts that when `cost_caps._select_chat_model` returns the cheap tier
(`workers_ai_mistral_7b` + `@cf/mistral/mistral-7b-instruct-v0.3`),
`llm.call_llm_api_chat` actually dispatches to `_call_llm_raw` with that
exact model id pinned — not the generic Workers-AI default
(`@cf/openai/gpt-oss-20b`).

This is the wire-level check the round-3 code review flagged as missing:
source-string proofs are not enough; we must verify the dispatched
model.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch, AsyncMock


def test_select_chat_model_returns_mistral_7b_for_free_turn_1():
    from cost_caps import _select_chat_model

    decision = _select_chat_model(
        user_id="u1", session_turn_count=1, user_plan="free", lang="en",
    )
    assert decision["tier"] == "cheap"
    assert decision["provider"] == "workers_ai_mistral_7b"
    assert decision["model"] == "@cf/mistral/mistral-7b-instruct-v0.3"


def test_select_chat_model_returns_mistral_7b_when_cheaponly_locked():
    from cost_caps import _select_chat_model

    decision = _select_chat_model(
        user_id="u1", session_turn_count=8, user_plan="pro",
        lang="en", cheaponly_active=True,
    )
    assert decision["tier"] == "cheap"
    assert decision["provider"] == "workers_ai_mistral_7b"
    assert decision["model"] == "@cf/mistral/mistral-7b-instruct-v0.3"
    assert decision.get("cheaponly_lock") is True


def test_call_llm_api_chat_with_mistral_override_pins_mistral_model_on_wire():
    """Free-tier turn ⇒ cheap-tier provider ⇒ wire dispatch uses Mistral-7B."""
    import llm

    captured = {}

    async def _fake_raw(messages, model=None, max_tokens=1024,
                       provider_list=None, feature_key=""):
        captured["model"] = model
        captured["feature_key"] = feature_key
        captured["provider_list_canonical"] = [
            p.get("provider") for p in (provider_list or [])
        ]
        return "ok"

    with patch.object(llm, "_call_llm_raw", new=AsyncMock(side_effect=_fake_raw)):
        out = asyncio.run(llm.call_llm_api_chat(
            [{"role": "user", "content": "hi"}],
            model=None,
            max_tokens=600,
            lang="en",
            provider_override="workers_ai_mistral_7b",
        ))

    assert out == "ok"
    assert captured.get("model") == "@cf/mistral/mistral-7b-instruct-v0.3", (
        f"tier-routing did not pin Mistral on the wire — got {captured.get('model')!r}"
    )
    assert captured.get("feature_key") == "english_rag_chat"
    assert all(p == "workers-ai" for p in captured["provider_list_canonical"]), (
        "tier-routing must dispatch through the Workers-AI-only pool "
        f"(got {captured['provider_list_canonical']!r})"
    )


def test_call_llm_api_chat_with_llama_override_pins_llama_model_on_wire():
    import llm

    captured = {}

    async def _fake_raw(messages, model=None, max_tokens=1024,
                       provider_list=None, feature_key=""):
        captured["model"] = model
        return "ok"

    with patch.object(llm, "_call_llm_raw", new=AsyncMock(side_effect=_fake_raw)):
        out = asyncio.run(llm.call_llm_api_chat(
            [{"role": "user", "content": "hi"}],
            lang="en",
            provider_override="workers_ai_llama32_3b",
        ))

    assert out == "ok"
    assert captured.get("model") == "@cf/meta/llama-3.2-3b-instruct"


# ── Task #513 §C round-6 — turn-counting regression ──────────────────


def _decision_for_history(history_messages):
    """Mirror the routes/ai_chat.py session_turn_count formula and
    return what `_select_chat_model` would emit for that turn."""
    from cost_caps import _select_chat_model
    turn = int(
        (len(history_messages) // 2) + 1
        if isinstance(history_messages, list) else 1
    )
    return _select_chat_model(
        user_id="u1", session_turn_count=turn,
        user_plan="free", lang="en",
    )


def test_turn_count_first_message_is_cheap():
    """No prior history → turn 1 → cheap (Mistral-7B)."""
    d = _decision_for_history([])
    assert d["tier"] == "cheap"
    assert d["provider"] == "workers_ai_mistral_7b"


def test_turn_count_second_message_is_cheap():
    """1 prior user+assistant pair → turn 2 → cheap."""
    history = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
    ]
    d = _decision_for_history(history)
    assert d["tier"] == "cheap"


def test_turn_count_third_message_flips_to_primary():
    """2 prior user+assistant pairs → turn 3 → primary (Azure).

    This is the round-5 off-by-one regression: previously
    `len(history) // 2 == 2` was passed as the turn count, which
    `_select_chat_model` still classified as cheap.
    """
    history = [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "a2"},
    ]
    d = _decision_for_history(history)
    assert d["tier"] == "primary", (
        f"turn 3 must flip to primary, got tier={d['tier']!r} "
        f"provider={d['provider']!r}"
    )
    # Task #549 — perpetual $100/mo budget. Default primary is the
    # Workers-AI Llama-3.2-3B free-tier slot; ops can flip to vertex
    # via CHAT_PRIMARY_OVERRIDE=vertex.
    assert d["provider"] == "workers_ai_llama32_3b"
    assert d["model"] == "@cf/meta/llama-3.2-3b-instruct"
