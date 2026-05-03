"""Regression contract for the user-mandated Assamese routing (2026-04-26):

  * Translation: Gemini main + Sarvam polish.
  * Response   : Sarvam main + Gemini fallback.

These tests pin the contract so a future refactor cannot silently revert
to a Sarvam-translates path or to a Gemini-races-Sarvam response path.
"""
from __future__ import annotations

import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests._deps_stub import install_deps_stub  # noqa: E402

install_deps_stub()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

class _FakeResp:
    def __init__(self, status: int, body: dict):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


def _run(coro):
    """Run an async coroutine in a fresh event loop (matches this codebase's
    convention — see test_lang_sanitizer.py / test_ai_chat_indic_route.py)."""
    return asyncio.new_event_loop().run_until_complete(coro)


def _sse_decode_content(body: str) -> str:
    """Concatenate every SSE `data: {"content": "..."}` payload, decoding
    JSON-escaped unicode (ensure_ascii=True is in effect on the wire)."""
    import json
    import re

    out = []
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        try:
            obj = json.loads(line[5:].strip())
        except Exception:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("content"), str):
            out.append(obj["content"])
    return "".join(out)


# ─────────────────────────────────────────────────────────────────────────────
# A. Translation contract — Gemini main, Sarvam polish
# ─────────────────────────────────────────────────────────────────────────────

def test_translation_calls_gemini_first_then_sarvam_polish_for_long_text(monkeypatch):
    """For substantive text (>= _POLISH_MIN_LEN chars) the helper must:
       1. Call vertex_services.translate (Gemini) for the actual translation.
       2. Then send the Gemini output through Sarvam's chat endpoint to polish.
       3. Return the polished output.
    Sarvam's `/translate` endpoint MUST NOT be called — that path is gone."""
    from routes import ai_chat as chat_mod

    long_english = (
        "Carnot's theorem states that no heat engine operating between two "
        "thermal reservoirs can be more efficient than a Carnot engine "
        "operating between the same reservoirs. This is a foundational "
        "result in thermodynamics."
    )
    gemini_assamese = (
        "কাৰ্নোৰ উপপাদ্যই কয় যে দুটা তাপীয় ভঁৰালৰ মাজত পৰিচালিত কোনো তাপ "
        "ইঞ্জিন একে ভঁৰালৰ মাজত পৰিচালিত কাৰ্নো ইঞ্জিনতকৈ অধিক কাৰ্যক্ষম "
        "হ'ব নোৱাৰে। এই ফলটো ঊষ্ণতাগতিবিদ্যাৰ এক ভেটিমূলক ফলাফল।"
    )
    polished_assamese = gemini_assamese + " (পালিচড)"

    fake_vertex = types.ModuleType("vertex_services")
    fake_vertex.translate = AsyncMock(return_value=gemini_assamese)
    monkeypatch.setitem(sys.modules, "vertex_services", fake_vertex)

    sarvam_stub = MagicMock()
    sarvam_chat_post = AsyncMock(return_value=_FakeResp(200, {
        "choices": [{"message": {"content": polished_assamese}}]
    }))
    sarvam_stub.post = sarvam_chat_post

    import deps
    monkeypatch.setattr(deps, "sarvam_llm_client", sarvam_stub, raising=False)
    legacy_sarvam = MagicMock()
    legacy_sarvam.post = AsyncMock(side_effect=AssertionError(
        "Sarvam /translate was called — translation must go through Gemini-main"
    ))
    monkeypatch.setattr(deps, "sarvam_client", legacy_sarvam, raising=False)

    out = _run(chat_mod._assamese_translate_gemini_main_sarvam_polish(
        long_english, target_lang_code="as-IN",
    ))

    fake_vertex.translate.assert_awaited_once()
    _args, _kwargs = fake_vertex.translate.await_args
    assert _kwargs.get("target_lang") == "as"
    assert _kwargs.get("source_lang") == "en"

    sarvam_chat_post.assert_awaited_once()
    _post_args, _post_kwargs = sarvam_chat_post.await_args
    assert _post_args[0] == "/v1/chat/completions"
    payload = _post_kwargs["json"]
    assert payload["model"] == "sarvam-m"
    assert payload["stream"] is False
    msgs = payload["messages"]
    assert msgs[-1]["role"] == "user"
    assert msgs[-1]["content"] == gemini_assamese
    assert "native Assamese" in msgs[0]["content"]

    assert out == polished_assamese


def test_translation_skips_polish_for_short_fragments(monkeypatch):
    """Per-fragment sanitiser splice (text < _POLISH_MIN_LEN) must NOT
    incur a Sarvam polish round-trip — the per-fragment latency budget
    cannot afford it. Returns the un-polished Gemini output verbatim."""
    from routes import ai_chat as chat_mod

    short_english = "known as Carnot's theorem"  # < 80 chars
    gemini_short = "কাৰ্নোৰ উপপাদ্য নামেৰে জনাজাত"

    fake_vertex = types.ModuleType("vertex_services")
    fake_vertex.translate = AsyncMock(return_value=gemini_short)
    monkeypatch.setitem(sys.modules, "vertex_services", fake_vertex)

    sarvam_stub = MagicMock()
    sarvam_stub.post = AsyncMock(side_effect=AssertionError(
        "Sarvam polish was called for a short fragment — should be skipped"
    ))
    import deps
    monkeypatch.setattr(deps, "sarvam_llm_client", sarvam_stub, raising=False)

    out = _run(chat_mod._assamese_translate_gemini_main_sarvam_polish(
        short_english, target_lang_code="as-IN",
    ))
    assert out == gemini_short
    fake_vertex.translate.assert_awaited_once()


def test_translation_returns_gemini_when_polish_fails(monkeypatch):
    """Sarvam polish failure must degrade gracefully to the un-polished
    Gemini output — translation still landed, just not native-polished."""
    from routes import ai_chat as chat_mod

    long_english = "x" * 200
    gemini_long = "ক" * 200

    fake_vertex = types.ModuleType("vertex_services")
    fake_vertex.translate = AsyncMock(return_value=gemini_long)
    monkeypatch.setitem(sys.modules, "vertex_services", fake_vertex)

    sarvam_stub = MagicMock()
    sarvam_stub.post = AsyncMock(return_value=_FakeResp(503, {"error": "down"}))
    import deps
    monkeypatch.setattr(deps, "sarvam_llm_client", sarvam_stub, raising=False)

    out = _run(chat_mod._assamese_translate_gemini_main_sarvam_polish(
        long_english, target_lang_code="as-IN",
    ))
    assert out == gemini_long


def test_translation_returns_empty_when_gemini_fails(monkeypatch):
    """Gemini failure → return "" so the caller can fall back to its own
    strip / original-text path. Sarvam polish must NOT be attempted on a
    no-Gemini-output baseline (would polish nothing)."""
    from routes import ai_chat as chat_mod

    fake_vertex = types.ModuleType("vertex_services")
    fake_vertex.translate = AsyncMock(return_value=None)
    monkeypatch.setitem(sys.modules, "vertex_services", fake_vertex)

    sarvam_stub = MagicMock()
    sarvam_stub.post = AsyncMock(side_effect=AssertionError(
        "Sarvam polish must not be called when Gemini returned nothing"
    ))
    import deps
    monkeypatch.setattr(deps, "sarvam_llm_client", sarvam_stub, raising=False)

    out = _run(chat_mod._assamese_translate_gemini_main_sarvam_polish(
        "Hello world this is a long test message that exceeds the polish length threshold easily.",
        target_lang_code="as-IN",
    ))
    assert out == ""


def test_translation_returns_gemini_when_sarvam_client_is_none(monkeypatch):
    """If `deps.sarvam_llm_client` is None (Sarvam disabled), polish is
    skipped and the Gemini output is returned. Translation still succeeds
    because Gemini is the MAIN, not the FALLBACK."""
    from routes import ai_chat as chat_mod

    long_english = "x" * 200
    gemini_long = "ক" * 200

    fake_vertex = types.ModuleType("vertex_services")
    fake_vertex.translate = AsyncMock(return_value=gemini_long)
    monkeypatch.setitem(sys.modules, "vertex_services", fake_vertex)

    import deps
    monkeypatch.setattr(deps, "sarvam_llm_client", None, raising=False)

    out = _run(chat_mod._assamese_translate_gemini_main_sarvam_polish(
        long_english, target_lang_code="as-IN",
    ))
    assert out == gemini_long


# ─────────────────────────────────────────────────────────────────────────────
# B. Response contract — Sarvam main, Gemini fallback (sequential)
# ─────────────────────────────────────────────────────────────────────────────

def test_indic_response_phase1_sarvam_wins_phase2_gemini_not_called(monkeypatch):
    """When at least one Sarvam key emits a chunk during Phase 1, Phase 2
    (Gemini) must NEVER be reached. Gemini cannot 'steal' first-token from
    Sarvam due to network jitter — that would violate Sarvam-MAIN."""
    import llm

    monkeypatch.setattr(llm, "_SARVAM_PROVIDERS", [
        {"provider": "sarvam", "key": "fake-sarvam-key", "default_model": "sarvam-m"},
    ], raising=False)

    sarvam_calls = {"n": 0}
    gemini_calls = {"n": 0}

    async def _fake_sarvam(messages, api_key, model, max_tokens, *, response_lang=""):
        sarvam_calls["n"] += 1
        yield "নমস্কাৰ"
        yield " পৃথিৱী"

    async def _fake_gemini(messages, model, max_tokens):
        # Vertex SA path post 2026-05-03 — `_stream_vertex_gemini` signature
        # is `(messages, model, max_tokens)` (no api_key).
        gemini_calls["n"] += 1
        yield "GEMINI WAS CALLED"

    monkeypatch.setattr(llm, "_stream_sarvam", _fake_sarvam, raising=False)
    monkeypatch.setattr(llm, "_stream_vertex_gemini", _fake_gemini, raising=False)
    monkeypatch.setattr(llm._vertex_chat, "is_configured", lambda: False, raising=False)

    async def _drive():
        chunks = []
        async for chunk in llm.call_llm_api_stream(
            [{"role": "user", "content": "hi"}],
            model="sarvam-m",
            max_tokens=128,
            intent="casual",
            response_lang="as",
        ):
            chunks.append(chunk)
        return chunks

    chunks = _run(_drive())
    body = "".join(chunks)
    decoded = _sse_decode_content(body)

    assert sarvam_calls["n"] >= 1, "Sarvam must be called in Phase 1"
    assert gemini_calls["n"] == 0, (
        "Gemini must NOT be called when Sarvam wins Phase 1 — "
        "Sarvam-MAIN contract violated"
    )
    assert "নমস্কাৰ" in decoded
    assert "GEMINI WAS CALLED" not in decoded
    assert '"__provider": "sarvam"' in body


def test_indic_response_phase1_all_sarvam_fail_then_phase2_gemini(monkeypatch):
    """When ALL Sarvam keys fail in Phase 1, Phase 2 Gemini fallback streams
    the response. This is the documented failure mode for the
    Sarvam-MAIN + Gemini-FALLBACK contract."""
    import llm

    monkeypatch.setattr(llm, "_SARVAM_PROVIDERS", [
        {"provider": "sarvam", "key": "fake-sarvam-key", "default_model": "sarvam-m"},
        {"provider": "sarvam", "key": "fake-sarvam-key-2", "default_model": "sarvam-m"},
    ], raising=False)
    monkeypatch.setattr(llm, "_LLM_PROVIDERS", [
        {"provider": "gemini", "key": "fake-gemini-key", "default_model": "gemini-2.5-flash"},
    ], raising=False)

    sarvam_calls = {"n": 0}
    gemini_calls = {"n": 0}

    async def _fake_sarvam(messages, api_key, model, max_tokens, *, response_lang=""):
        sarvam_calls["n"] += 1
        raise RuntimeError("Sarvam down (simulated)")
        yield  # pragma: no cover

    async def _fake_gemini(messages, model, max_tokens):
        # Vertex SA path post 2026-05-03 — `_stream_vertex_gemini` signature
        # is `(messages, model, max_tokens)` (no api_key).
        gemini_calls["n"] += 1
        yield "নমস্কাৰ ফ্ৰম জেমিনি"
        yield " (গেমিনি ফলব্যাক)"

    monkeypatch.setattr(llm, "_stream_sarvam", _fake_sarvam, raising=False)
    monkeypatch.setattr(llm, "_stream_vertex_gemini", _fake_gemini, raising=False)
    monkeypatch.setattr(llm._vertex_chat, "is_configured", lambda: False, raising=False)

    async def _drive():
        chunks = []
        async for chunk in llm.call_llm_api_stream(
            [{"role": "user", "content": "hi"}],
            model="sarvam-m",
            max_tokens=128,
            intent="casual",
            response_lang="as",
        ):
            chunks.append(chunk)
        return chunks

    chunks = _run(_drive())
    body = "".join(chunks)
    decoded = _sse_decode_content(body)

    assert sarvam_calls["n"] == 2, "Both Sarvam keys must be tried in Phase 1"
    assert gemini_calls["n"] == 1, (
        "Gemini must be called exactly once as Phase 2 fallback when all "
        "Sarvam keys fail"
    )
    assert "জেমিনি" in decoded
    assert '"__provider": "gemini"' in body


def test_indic_response_no_gemini_key_returns_error_after_sarvam_fails(monkeypatch):
    """When all Sarvam keys fail AND no Gemini key is configured, the
    response must surface a user-friendly error (not silently hang)."""
    import llm

    monkeypatch.setattr(llm, "_SARVAM_PROVIDERS", [
        {"provider": "sarvam", "key": "fake-sarvam-key", "default_model": "sarvam-m"},
    ], raising=False)
    monkeypatch.setattr(llm, "_LLM_PROVIDERS", [], raising=False)

    sarvam_calls = {"n": 0}
    gemini_calls = {"n": 0}

    async def _fake_sarvam(messages, api_key, model, max_tokens, *, response_lang=""):
        sarvam_calls["n"] += 1
        raise RuntimeError("Sarvam down")
        yield  # pragma: no cover

    async def _fake_gemini(messages, model, max_tokens):
        # Vertex SA path post 2026-05-03 — `_stream_vertex_gemini` signature
        # is `(messages, model, max_tokens)` (no api_key).
        gemini_calls["n"] += 1
        raise AssertionError(
            "Gemini must not be called when no Gemini provider is configured"
        )
        yield  # pragma: no cover

    monkeypatch.setattr(llm, "_stream_sarvam", _fake_sarvam, raising=False)
    monkeypatch.setattr(llm, "_stream_vertex_gemini", _fake_gemini, raising=False)
    monkeypatch.setattr(llm._vertex_chat, "is_configured", lambda: False, raising=False)

    async def _drive():
        chunks = []
        async for chunk in llm.call_llm_api_stream(
            [{"role": "user", "content": "hi"}],
            model="sarvam-m",
            max_tokens=128,
            intent="casual",
            response_lang="as",
        ):
            chunks.append(chunk)
        return chunks

    chunks = _run(_drive())
    body = "".join(chunks)
    assert sarvam_calls["n"] == 1
    assert '"error"' in body
    assert "temporarily unavailable" in body
