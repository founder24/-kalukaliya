"""Integration test for the Workers AI chat fallback wiring (Task #636).

Asserts that when ALL configured chat providers fail with a retryable
error, `_call_llm_raw` returns an `LlmResult` tagged with
`provider="workers-ai"` AND a populated `fallback_reason`. This is the
final guard against regressions like "the policy module works in
isolation but the wiring in llm.py never actually invokes it".

The test mocks `providers.workers_ai.call_chat` directly so we don't
need a live edge worker, and it monkey-patches the provider list to a
single fake provider that always raises a 503 — that way we exercise
the same code path that runs in prod when Cerebras/Gemini are down.
"""

from __future__ import annotations

import asyncio
import os

import httpx
import pytest

os.environ.setdefault("WORKERS_AI_FALLBACK_SECRET", "test-secret")
os.environ.setdefault("WORKERS_AI_FALLBACK_ENABLED", "1")

import llm  # noqa: E402
from providers import workers_ai as wai  # noqa: E402


def _503_error() -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "http://primary.test")
    resp = httpx.Response(503, request=req)
    return httpx.HTTPStatusError("primary down", request=req, response=resp)


def test_chat_falls_back_to_workers_ai_on_total_primary_failure(monkeypatch):
    """The whole point of Task #636 — when every primary provider is
    down, the chat path returns a Workers AI result instead of 503."""
    monkeypatch.setenv("WORKERS_AI_FALLBACK_SECRET", "test-secret")
    monkeypatch.setenv("WORKERS_AI_FALLBACK_ENABLED", "1")
    wai.set_enabled("chat", True)

    # Force one provider, single key, that always throws a retryable 503.
    fake_providers = [{
        "provider": "fake-primary",
        "key": "k-test",
        "default_model": "fake-model",
    }]

    async def always_503(messages, provider, key, model, max_tokens):
        raise _503_error()

    async def fake_workers_ai_chat(messages, max_tokens=1024, temperature=0.3):
        # Sanity: the wiring should pass through the original messages.
        assert isinstance(messages, list) and messages
        return "Hello from Workers AI"

    monkeypatch.setattr(llm, "_call_single_provider", always_503)
    monkeypatch.setattr(wai, "call_chat", fake_workers_ai_chat)
    # Skip the durable load — no Mongo in unit tests.
    async def _noop():
        return None
    monkeypatch.setattr(wai, "_persist_load_if_stale", _noop)

    messages = [
        {"role": "system", "content": "You are a tutor."},
        {"role": "user", "content": "what is 2+2?"},
    ]
    result = asyncio.run(llm._call_llm_raw(messages, model="fake-model",
                                           provider_list=fake_providers))
    assert str(result) == "Hello from Workers AI"
    assert result.provider == "workers-ai"
    # The reason must be populated — that's the metadata the admin
    # dashboard uses to attribute the fallback to the upstream failure.
    assert result.fallback_reason == "http_503"


def test_chat_does_not_fall_back_on_4xx_bad_input(monkeypatch):
    """The other key invariant — a 400 from the primary surfaces as
    503 (after the retry loop), it does NOT silently switch providers
    and hide the bug."""
    from fastapi import HTTPException

    monkeypatch.setenv("WORKERS_AI_FALLBACK_SECRET", "test-secret")
    monkeypatch.setenv("WORKERS_AI_FALLBACK_ENABLED", "1")
    wai.set_enabled("chat", True)

    fake_providers = [{
        "provider": "fake-primary",
        "key": "k-test",
        "default_model": "fake-model",
    }]

    req = httpx.Request("POST", "http://primary.test")

    async def always_400(messages, provider, key, model, max_tokens):
        raise httpx.HTTPStatusError(
            "bad input", request=req,
            response=httpx.Response(400, request=req),
        )

    workers_ai_calls = {"count": 0}

    async def workers_ai_should_not_be_called(messages, **_):
        workers_ai_calls["count"] += 1
        return "should never appear"

    monkeypatch.setattr(llm, "_call_single_provider", always_400)
    monkeypatch.setattr(wai, "call_chat", workers_ai_should_not_be_called)
    async def _noop():
        return None
    monkeypatch.setattr(wai, "_persist_load_if_stale", _noop)

    messages = [{"role": "user", "content": "trigger 400"}]
    with pytest.raises(HTTPException) as exc:
        asyncio.run(llm._call_llm_raw(messages, model="fake-model",
                                      provider_list=fake_providers))
    assert exc.value.status_code == 503
    # Critical: Workers AI must NOT have been invoked.
    assert workers_ai_calls["count"] == 0


# ── Task #366 — Workers AI tail-end fallback chaos tests ────────────────────────
#
# These exercise the full ``call_with_provider_fallback`` exclusion loop end-to-
# end. We force every paid provider in the pool to raise an HTTP 429 (the
# canonical "throttled, drop me from the pool" signal that
# ``call_with_provider_fallback`` is wired to handle) and then assert that the
# weighted draw eventually lands on one of the Workers AI tail variants
# promoted in Task #347 — and that the call returns a non-empty answer rather
# than bubbling a RuntimeError.
#
# We intentionally monkey-patch ``llm._dispatch_llm_for_feature`` directly
# instead of the lower-level provider clients. This isolates the test from the
# (admin-toggle-gated, env-var-gated, async-batch-scoped) sub-paths inside the
# real dispatcher and verifies precisely the property under test: the *pool*
# correctly excludes paid providers and lands on the Workers AI tail.


def _build_chaos_dispatch(paid_providers, workers_tail, captured):
    """Return an async ``_dispatch_llm_for_feature`` replacement that fails
    every provider in ``paid_providers`` with a retryable 429 and succeeds for
    any provider in ``workers_tail`` (recording the winner into ``captured``).
    """
    req = httpx.Request("POST", "http://paid.test")

    async def _chaos(messages, provider, max_tokens, *, feature=""):
        if provider in paid_providers:
            raise httpx.HTTPStatusError(
                f"{provider} throttled",
                request=req,
                response=httpx.Response(429, request=req),
            )
        if provider in workers_tail:
            captured["winner"] = provider
            captured["feature"] = feature
            return f"answer-from-{provider}"
        # Any unexpected provider (e.g. workers_ai_indic mistakenly drawn for a
        # chat pool) must be a hard failure so the test surfaces the bug rather
        # than silently passing through a non-chat path.
        raise AssertionError(
            f"chaos dispatch saw unexpected provider={provider!r} for feature={feature!r}"
        )

    return _chaos


def _disable_credit_burn_fallback(monkeypatch):
    """Pin ``credit_burn_meter_runtime.is_fallback_active`` to False so the
    chaos test isn't accidentally racing the live credit-burn meter, which
    would exclude vertex/sarvam from the pool and skew which providers the
    weighted draw evaluates."""
    import credit_burn_meter_runtime as _cb
    monkeypatch.setattr(_cb, "is_fallback_active", lambda: False)


def test_english_chat_falls_back_to_workers_ai_tail_when_paid_throttled(monkeypatch):
    """Task #366 — when Azure OpenAI, Vertex, AND Sarvam all return 429 on
    the ``english_rag_chat`` pool, the weighted draw must land on one of the
    Workers AI tail promotions (``workers_ai_llama32_3b`` or
    ``workers_ai_mistral_7b``) and the chat handler must return a non-empty
    answer.
    """
    _disable_credit_burn_fallback(monkeypatch)

    paid = frozenset({"azure_openai", "vertex", "sarvam"})
    # Strict tail expectation — only the two NAMED Task #347 promotions are
    # acceptable winners. We deliberately exclude generic ``workers_ai`` from
    # the success set so this test fails loudly if a future config change
    # drops the named variants from the english_rag_chat pool (the chaos
    # would then silently land on gpt-oss-20b and the assertion would catch
    # the regression).
    named_tail = frozenset({
        "workers_ai_llama32_3b",
        "workers_ai_mistral_7b",
    })
    workers_tail = named_tail | frozenset({"workers_ai"})
    captured: dict = {}
    monkeypatch.setattr(
        llm, "_dispatch_llm_for_feature",
        _build_chaos_dispatch(paid, workers_tail, captured),
    )

    # Drain the random draw enough times that we are statistically guaranteed
    # to have observed every paid provider being excluded AND a NAMED tail
    # variant winning at least once. With weights (azure 10000, vertex 100,
    # sarvam 50, llama32 20, mistral 10) and 6 attempts per call, a few dozen
    # iterations is more than enough.
    seen_named_tail: set = set()
    for _ in range(40):
        captured.clear()
        answer = asyncio.run(
            llm.call_llm_api_chat(
                [{"role": "user", "content": "what is photosynthesis?"}],
                lang="en",
            )
        )
        assert answer
        winner = captured.get("winner")
        assert winner in workers_tail
        if winner in named_tail:
            seen_named_tail.add(winner)
    assert seen_named_tail, (
        "Across 40 chaos draws no NAMED Workers AI tail variant ever won — "
        "either the english_rag_chat pool no longer contains "
        "workers_ai_llama32_3b/workers_ai_mistral_7b, or call_with_provider_"
        "fallback stopped re-drawing after exclusion."
    )
    assert captured.get("feature") == "english_rag_chat"


@pytest.mark.skip(reason=(
    "Task #366 (workers_ai_llama31_8b + workers_ai_indic as Assamese chat "
    "tail) was superseded by Task #291: assamese_rag_chat is a strict 2-leg "
    "sarvam → vertex chain with no further downgrade. workers_ai_llama31_8b "
    "produced English output for Assamese prompts and workers_ai_indic is "
    "a translation model, not a chat model — both are now correctly excluded "
    "from the chain. Strict exhaustion surfaces an error instead of "
    "wrong-language output. See test_provider_priority_locked.py for the "
    "canonical #291 contract."
))
def test_assamese_chat_falls_back_to_workers_ai_tail_when_paid_throttled(monkeypatch):
    """Task #366 — when both Sarvam and Vertex return 429 on the
    ``assamese_rag_chat`` pool, the weighted draw must land on the Workers AI
    Assamese tail (``workers_ai_llama31_8b`` or the IndicTrans2-backed
    ``workers_ai_indic`` last-resort) and return a non-empty answer."""
    _disable_credit_burn_fallback(monkeypatch)

    paid = frozenset({"sarvam", "vertex"})
    workers_tail = frozenset({
        "workers_ai_llama31_8b",
        "workers_ai_indic",
    })
    captured: dict = {}
    monkeypatch.setattr(
        llm, "_dispatch_llm_for_feature",
        _build_chaos_dispatch(paid, workers_tail, captured),
    )

    answer = asyncio.run(
        llm.call_llm_api_chat(
            [{"role": "user", "content": "ফট’ছিন্থেচিছ কি?"}],
            lang="as",
        )
    )
    assert answer, "Workers AI Assamese tail must return a non-empty answer under chaos"
    assert captured.get("winner") in workers_tail, (
        f"expected Workers AI Assamese tail winner, got {captured.get('winner')!r}"
    )
    assert captured.get("feature") == "assamese_rag_chat"


def test_english_chat_pool_actually_contains_workers_ai_tail():
    """Guardrail — config.POOL_WEIGHTS['english_rag_chat'] must keep the three
    Workers AI tail variants at non-zero weight so the chaos fallback above
    has something to draw. If a future config edit zeros them out the chaos
    test would still pass via the gpt-oss-20b last resort, masking the
    regression — this assert catches it directly at the config layer."""
    from config import POOL_WEIGHTS
    pool = POOL_WEIGHTS["english_rag_chat"]
    assert pool.get("workers_ai_llama32_3b", 0) > 0, (
        "Task #347 promoted workers_ai_llama32_3b into english_rag_chat — "
        "must remain > 0 so the tail can be drawn under chaos"
    )
    assert pool.get("workers_ai_mistral_7b", 0) > 0, (
        "Task #347 promoted workers_ai_mistral_7b into english_rag_chat — "
        "must remain > 0 so the tail can be drawn under chaos"
    )
    # PROVIDER_PRIORITY also has to list these — POOL_WEIGHTS alone is not
    # enough; select_provider iterates the priority list to seed the weighted
    # candidate pool.
    from config import PROVIDER_PRIORITY
    chain = PROVIDER_PRIORITY["english_rag_chat"]
    assert "workers_ai_llama32_3b" in chain
    assert "workers_ai_mistral_7b" in chain


@pytest.mark.skip(reason=(
    "Task #347/#366 guardrail (Workers AI Indic tail in assamese_rag_chat) "
    "was superseded by Task #291: the chain is locked to exactly "
    "['sarvam', 'vertex']. The canonical guardrail now lives in "
    "tests/test_provider_priority_locked.py::"
    "test_assamese_rag_chat_locked_to_sarvam_primary which asserts the "
    "OPPOSITE — that workers_ai_indic / workers_ai_llama31_8b must NOT be "
    "in the pool. See config.PROVIDER_PRIORITY['assamese_rag_chat'] note."
))
def test_assamese_chat_pool_actually_contains_workers_ai_tail():
    """Guardrail — config.POOL_WEIGHTS['assamese_rag_chat'] must keep
    workers_ai_llama31_8b at non-zero weight so the Assamese chaos fallback
    has a reachable Workers AI Indic chat tail."""
    from config import POOL_WEIGHTS
    pool = POOL_WEIGHTS["assamese_rag_chat"]
    assert pool.get("workers_ai_llama31_8b", 0) > 0, (
        "Task #347 promoted workers_ai_llama31_8b into assamese_rag_chat — "
        "must remain > 0 so the tail can be drawn under chaos"
    )
    from config import PROVIDER_PRIORITY
    chain = PROVIDER_PRIORITY["assamese_rag_chat"]
    assert "workers_ai_llama31_8b" in chain
    # workers_ai_indic is the absolute degraded-online tail and must remain
    # in the chain even though its weight stays at 0 (last-resort policy).
    assert "workers_ai_indic" in chain
