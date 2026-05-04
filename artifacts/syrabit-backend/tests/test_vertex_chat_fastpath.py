"""Vertex Gemini Flash fast-path tests (Task #627).

Covers the four behaviours of the ``vertex/gemini-flash`` model branch
in ``call_llm_api_stream`` (artifacts/syrabit-backend/llm.py ~L1150):

  1. Happy path — Vertex streams tokens; we emit
     ``__provider: vertex_gemini`` and never reach the legacy SLM pool.
  2. Pre-first-token failure — Vertex raises before yielding anything;
     we silently fall back to ``openai/gpt-oss-20b`` and a
     ``vertex_gemini -> openai/gpt-oss-20b`` fallback metric is
     recorded.
  3. Mid-stream error — Vertex yields one token then raises; we surface
     ``error: AI service interrupted`` to the client and stop (no
     fallback because we cannot rewind the SSE stream).
  4. Indic bypass — When the response language is Assamese, the Vertex
     fast-path must be skipped entirely so Sarvam-tuned models handle
     the request.

Per Task #627 these tests mock ``vertex_chat.stream_chat`` — the public
integration boundary that talks to Vertex AI — with an async generator
so the ``_stream_vertex_gemini`` wrapper in llm.py is exercised too and
no test ever hits GCP.
"""
import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _run(coro):
    # ``call_llm_api_stream`` is an async generator — each test drives it
    # to completion on a fresh event loop so the module-level
    # ``asyncio.get_event_loop()`` doesn't leak state between tests.
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _collect(agen):
    out = []
    async for chunk in agen:
        out.append(chunk)
    return out


def _parse_sse(chunks):
    """Decode SSE ``data: {...}`` chunks into the list of payload dicts."""
    events = []
    for chunk in chunks:
        for line in chunk.split("\n"):
            line = line.strip()
            if not line.startswith("data:"):
                continue
            try:
                events.append(json.loads(line[len("data:"):].strip()))
            except Exception:
                pass
    return events


@pytest.fixture(autouse=True)
def _isolate_provider_metrics():
    """Reset the in-memory per-provider counters between tests so each
    test sees a clean fallback bucket."""
    import chat_speedup_metrics as csm
    csm._provider_daily.clear()
    csm._provider_fallbacks.clear()
    yield
    csm._provider_daily.clear()
    csm._provider_fallbacks.clear()


# ── Helpers to build async generators that simulate Vertex streaming ──

def _async_gen_from_tokens(tokens):
    """Return an async-generator factory whose signature matches
    ``vertex_chat.stream_chat(messages, *, model, max_tokens,
    temperature)`` and yields the provided tokens in order."""
    async def _stream_chat(messages, *, model=None, max_tokens=2048, temperature=0.1):
        for tok in tokens:
            yield tok
    return _stream_chat


def _async_gen_that_raises(exc, *, after_tokens=()):
    """Async-generator factory that yields any pre-failure tokens, then
    raises ``exc``. Mirrors the ``vertex_chat.stream_chat`` signature."""
    async def _stream_chat(messages, *, model=None, max_tokens=2048, temperature=0.1):
        for tok in after_tokens:
            yield tok
        raise exc
    return _stream_chat


# ────────────────────────────────────────────────────────────────────────
# 1. Happy path
# ────────────────────────────────────────────────────────────────────────

def test_vertex_fastpath_happy_path_emits_provider_tag(monkeypatch):
    """Vertex yields tokens → SSE stream contains the ``__provider``
    marker and the legacy SLM pool is never invoked."""
    import llm
    import vertex_chat

    monkeypatch.setattr(vertex_chat, "is_configured", lambda: True)
    monkeypatch.setattr(
        vertex_chat, "stream_chat",
        _async_gen_from_tokens(["Hel", "lo ", "Wor", "ld"]),
    )
    # Spy on the legacy resolver so we can assert it was never consulted.
    legacy_spy = AsyncMock()
    monkeypatch.setattr(llm, "_resolve_provider_for_model", legacy_spy)

    msgs = [{"role": "user", "content": "hi"}]
    chunks = _run(_collect(llm.call_llm_api_stream(
        msgs, model="vertex/gemini-flash", response_lang="english",
    )))
    events = _parse_sse(chunks)

    contents = [e["content"] for e in events if "content" in e]
    assert "".join(contents) == "Hello World"

    providers = [e["__provider"] for e in events if "__provider" in e]
    assert providers == ["vertex_gemini"]
    # No errors and no legacy resolver consultation.
    assert not any("error" in e for e in events)
    legacy_spy.assert_not_called()

    # The happy path records a provider call in the dashboard metrics.
    import chat_speedup_metrics as csm
    snap = csm.snapshot(days=1)
    by_prov = {p["provider"]: p for p in snap["by_provider"]}
    assert "vertex_gemini" in by_prov
    assert by_prov["vertex_gemini"]["calls"] == 1


# ────────────────────────────────────────────────────────────────────────
# 2. Pre-first-token failure → fallback to legacy SLM pool
# ────────────────────────────────────────────────────────────────────────

def test_vertex_fastpath_pre_first_token_failure_falls_back(monkeypatch):
    """Vertex raises before any token → silently fall back to legacy
    SLM pool and the user still gets a real answer. This is the core
    promise of the fast-path (Task #627): a Vertex outage must be
    invisible to the user. We assert all three guarantees in one
    test:

      - Client receives usable ``content`` tokens from the legacy
        provider (chat continuity preserved).
      - No ``__provider: vertex_gemini`` leaks to the client (because
        Vertex didn't actually serve this turn).
      - ``provider_fallbacks["vertex_gemini->openai/gpt-oss-20b"]``
        increments so admins can observe the degradation.
    """
    import llm
    import vertex_chat

    monkeypatch.setattr(vertex_chat, "is_configured", lambda: True)
    monkeypatch.setattr(
        vertex_chat, "stream_chat",
        _async_gen_that_raises(RuntimeError("boom: vertex 503")),
    )

    # Route the legacy fallback through the Azure OpenAI fast-path
    # (T007) — production routes "openai/gpt-oss-20b" requests through
    # Azure first when configured, before the SLM hedged pool. Mocking
    # `_stream_cerebras` here is a red herring: the resolver path is
    # never reached when Azure is enabled. We assert "user gets a real
    # answer" by mocking the actual surface that serves traffic.
    from providers import azure_openai as _az_prov
    monkeypatch.setattr(_az_prov, "ENABLED", True, raising=False)

    async def _fake_azure_stream(messages, max_tokens=None):
        for tok in ("Sure", ", ", "here", " is ", "your ", "answer."):
            yield tok
    monkeypatch.setattr(_az_prov, "stream_chat", _fake_azure_stream)

    msgs = [{"role": "user", "content": "hi"}]
    chunks = _run(_collect(llm.call_llm_api_stream(
        msgs, model="vertex/gemini-flash", response_lang="english",
    )))
    events = _parse_sse(chunks)

    # 1) User still gets a real answer.
    contents = [e["content"] for e in events if "content" in e]
    assert contents, "Legacy fallback produced no content"
    assert "".join(contents) == "Sure, here is your answer."
    # 2) No vertex tag leaks to the client on fallback.
    providers = [e.get("__provider") for e in events if "__provider" in e]
    assert "vertex_gemini" not in providers
    # 3) No `error` event was surfaced — the fallback was invisible.
    assert not any("error" in e for e in events)

    # 4) Fallback metric recorded so admins can see the event.
    import chat_speedup_metrics as csm
    snap = csm.snapshot(days=1)
    fb = {f["transition"]: f["count"] for f in snap["provider_fallbacks"]}
    assert fb.get("vertex_gemini->openai/gpt-oss-20b") == 1


def test_vertex_fastpath_fallback_with_no_legacy_key_emits_config_error(monkeypatch):
    """Negative-case companion to the happy fallback test above:
    when Vertex pre-fails AND the legacy pool has no API key, we
    must surface ``LLM API key not configured`` instead of silently
    streaming nothing. Guards against a future refactor that might
    swallow the legacy key-missing branch."""
    import llm
    import vertex_chat

    monkeypatch.setattr(vertex_chat, "is_configured", lambda: True)
    monkeypatch.setattr(
        vertex_chat, "stream_chat",
        _async_gen_that_raises(RuntimeError("boom: vertex 503")),
    )
    monkeypatch.setattr(llm, "_resolve_provider_for_model",
                        lambda model, providers: ("", ""))

    msgs = [{"role": "user", "content": "hi"}]
    chunks = _run(_collect(llm.call_llm_api_stream(
        msgs, model="vertex/gemini-flash", response_lang="english",
    )))
    events = _parse_sse(chunks)

    errors = [e["error"] for e in events if "error" in e]
    assert errors and "not configured" in errors[-1].lower()
    # Fallback metric still fires because we DID route to the legacy
    # pool — the failure was further downstream.
    import chat_speedup_metrics as csm
    snap = csm.snapshot(days=1)
    fb = {f["transition"]: f["count"] for f in snap["provider_fallbacks"]}
    assert fb.get("vertex_gemini->openai/gpt-oss-20b") == 1


# ────────────────────────────────────────────────────────────────────────
# 3. Mid-stream error → surface error, no fallback
# ────────────────────────────────────────────────────────────────────────

def test_vertex_fastpath_mid_stream_error_surfaces_error(monkeypatch):
    """Vertex yields a token then raises → client receives the
    ``AI service interrupted`` error and the legacy fallback is NOT
    triggered (we cannot rewind partial SSE output)."""
    import llm
    import vertex_chat

    monkeypatch.setattr(vertex_chat, "is_configured", lambda: True)
    monkeypatch.setattr(
        vertex_chat, "stream_chat",
        _async_gen_that_raises(RuntimeError("boom mid-stream"),
                               after_tokens=["Hello"]),
    )
    legacy_spy = AsyncMock()
    monkeypatch.setattr(llm, "_resolve_provider_for_model", legacy_spy)

    msgs = [{"role": "user", "content": "hi"}]
    chunks = _run(_collect(llm.call_llm_api_stream(
        msgs, model="vertex/gemini-flash", response_lang="english",
    )))
    events = _parse_sse(chunks)

    # Partial content was streamed before the failure.
    assert any(e.get("content") == "Hello" for e in events)
    # Explicit error event emitted.
    errors = [e["error"] for e in events if "error" in e]
    assert errors and "interrupted" in errors[-1].lower()
    # Crucially, we did NOT fall through to legacy (would corrupt the
    # partial SSE response).
    legacy_spy.assert_not_called()

    import chat_speedup_metrics as csm
    snap = csm.snapshot(days=1)
    assert snap["provider_fallbacks"] == []


# ────────────────────────────────────────────────────────────────────────
# 4. Indic (Assamese) requests bypass Vertex entirely
# ────────────────────────────────────────────────────────────────────────

def test_vertex_fastpath_skipped_for_indic_languages(monkeypatch):
    """Assamese responses must bypass Vertex entirely so Sarvam routing
    handles the call. ``vertex_chat.stream_chat`` must never be
    invoked when ``response_lang='as'``."""
    import llm
    import vertex_chat

    vertex_spy = AsyncMock()
    monkeypatch.setattr(vertex_chat, "is_configured", lambda: True)
    monkeypatch.setattr(vertex_chat, "stream_chat", vertex_spy)

    # Stub the legacy resolver so the Indic path exits cleanly without
    # contacting any provider.
    monkeypatch.setattr(llm, "_resolve_provider_for_model",
                        lambda model, providers: ("", ""))

    msgs = [{"role": "user", "content": "hi"}]
    _run(_collect(llm.call_llm_api_stream(
        msgs, model="vertex/gemini-flash", response_lang="as",
    )))

    vertex_spy.assert_not_called()


# ────────────────────────────────────────────────────────────────────────
# 5. Bonus — unconfigured Vertex short-circuits to legacy without
#    attempting the Vertex call. Guards against accidental regressions
#    in environments where VERTEX_PROJECT_ID is intentionally empty.
# ────────────────────────────────────────────────────────────────────────

def test_vertex_fastpath_unconfigured_routes_to_legacy(monkeypatch):
    """When ``is_configured()`` is False, we must skip the Vertex call
    entirely (no crash, no fallback metric — it's not a runtime
    failure) and continue on the legacy resolution path."""
    import llm
    import vertex_chat

    monkeypatch.setattr(vertex_chat, "is_configured", lambda: False)
    vertex_spy = AsyncMock()
    monkeypatch.setattr(vertex_chat, "stream_chat", vertex_spy)
    monkeypatch.setattr(llm, "_resolve_provider_for_model",
                        lambda model, providers: ("", ""))

    msgs = [{"role": "user", "content": "hi"}]
    _run(_collect(llm.call_llm_api_stream(
        msgs, model="vertex/gemini-flash", response_lang="english",
    )))

    vertex_spy.assert_not_called()
    # Not a runtime failure — no fallback metric should fire when the
    # config is simply missing (the retry/fallback counter is reserved
    # for *runtime* Vertex failures).
    import chat_speedup_metrics as csm
    snap = csm.snapshot(days=1)
    assert snap["provider_fallbacks"] == []


# ────────────────────────────────────────────────────────────────────────
# 6. Circuit breaker open → fast-path skips Vertex without an attempt
# ────────────────────────────────────────────────────────────────────────

def test_vertex_fastpath_skipped_when_breaker_open(monkeypatch):
    """When ``is_configured()`` is True but ``is_available()`` is False
    (circuit breaker open), the fast-path must skip the Vertex call
    entirely so we don't pay the connect timeout per request. The
    request still routes to the legacy SLM pool — but transparently,
    without a fallback metric (the breaker prevented the attempt; no
    runtime Vertex failure happened on this turn)."""
    import llm
    import vertex_chat

    monkeypatch.setattr(vertex_chat, "is_configured", lambda: True)
    monkeypatch.setattr(vertex_chat, "is_available", lambda: False)
    vertex_spy = AsyncMock()
    monkeypatch.setattr(vertex_chat, "stream_chat", vertex_spy)

    # Production routes the legacy fallback for "openai/gpt-oss-20b"
    # through the Azure OpenAI fast-path (T007) when Azure is enabled,
    # so mock that surface — not `_stream_cerebras`, which is not on
    # this code path.
    from providers import azure_openai as _az_prov
    monkeypatch.setattr(_az_prov, "ENABLED", True, raising=False)

    async def _fake_azure_stream(messages, max_tokens=None):
        for tok in ("Hello", " ", "world"):
            yield tok
    monkeypatch.setattr(_az_prov, "stream_chat", _fake_azure_stream)

    msgs = [{"role": "user", "content": "hi"}]
    chunks = _run(_collect(llm.call_llm_api_stream(
        msgs, model="vertex/gemini-flash", response_lang="english",
    )))
    events = _parse_sse(chunks)

    # User still gets a real answer from the legacy pool.
    contents = [e["content"] for e in events if "content" in e]
    assert contents and "".join(contents) == "Hello world"
    # Vertex was NEVER attempted — that's the whole point.
    vertex_spy.assert_not_called()
    # No fallback metric — there was no Vertex *attempt* to fall back
    # from. The breaker pre-empted the call.
    import chat_speedup_metrics as csm
    snap = csm.snapshot(days=1)
    assert snap["provider_fallbacks"] == []


def test_vertex_chat_breaker_opens_on_repeated_http_failures():
    """End-to-end: vertex_chat's breaker module-level instance opens
    after the configured threshold of failures and ``is_available()``
    flips to False, so the fast-path automatically routes to the
    SLM pool on the next call."""
    import vertex_chat

    # Use the real breaker instance — exercise it directly.
    vertex_chat._breaker.force_close()
    vertex_chat.VERTEX_PROJECT_ID = "test-project"  # make is_configured True

    threshold = vertex_chat._CHAT_BREAKER_THRESHOLD
    assert vertex_chat.is_configured() is True
    assert vertex_chat.is_available() is True

    for i in range(threshold):
        vertex_chat._breaker.record_failure(f"http_503_{i}")

    assert vertex_chat.is_configured() is True
    assert vertex_chat.is_available() is False
    snap = vertex_chat.breaker_snapshot()
    assert snap["state"] == "open"
    assert snap["consecutive_failures"] >= threshold

    # Operator override resets it.
    vertex_chat.force_breaker_close()
    assert vertex_chat.is_available() is True
