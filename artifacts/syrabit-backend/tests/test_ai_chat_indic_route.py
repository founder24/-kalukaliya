"""End-to-end route-level tests for Assamese leakage filtering on the
chat streaming endpoint.

These tests mount the actual `/ai/chat/stream` FastAPI route with
stubbed dependencies, prime the in-process answer cache with a leaky
Assamese reply, and assert that the SSE body the client receives has a
non-whitelisted Latin ratio at or below the configured threshold.
"""
from __future__ import annotations

import json
import sys
import types
from unittest.mock import MagicMock, AsyncMock

import pytest

from tests._deps_stub import install_deps_stub  # noqa: E402

install_deps_stub()


CLEAN_ASSAMESE_FRAG = "উৰুকা হৈছে মাঘ বিহুৰ পূৰ্বৰ ৰাতিৰ উৎসৱ। "
LEAKY_ASSAMESE_CACHED = (
    CLEAN_ASSAMESE_FRAG
    + "me uses ssible terms চমুকৈ ক'লে ই এটা উৎসৱ। "
    + "terms thing way time year"
)


def _build_chat_app():
    """Build a minimal FastAPI app that mounts the chat router with
    enough stubs to reach the early-cache path of `/ai/chat/stream`."""
    from fastapi import FastAPI
    from routes import ai_chat as chat_mod
    from auth_deps import rate_limit_chat_optional

    # Anonymous user — bypass credit/auth machinery.
    async def _anon_user():
        return None

    chat_mod.classify_intent = lambda _q: ("notes", "notes")
    chat_mod.get_instant_response = lambda _q: None  # avoid instant fast-path
    chat_mod.evaluate_prompt_safety = lambda _q: (True, None, "")

    app = FastAPI()
    app.include_router(chat_mod.router, prefix="/api")
    app.dependency_overrides[rate_limit_chat_optional] = _anon_user
    return app, chat_mod


def _post_chat_stream(client, body) -> str:
    """POST to the chat stream endpoint and return the concatenated SSE
    body the client would have received."""
    with client.stream("POST", "/api/ai/chat/stream", json=body) as resp:
        assert resp.status_code == 200, resp.text
        chunks: list[str] = []
        for line in resp.iter_lines():
            if line:
                chunks.append(line)
        return "\n".join(chunks)


def _extract_emitted_content(sse_body: str) -> str:
    out = ""
    for line in sse_body.splitlines():
        if not line.startswith("data: "):
            continue
        payload_raw = line[6:].strip()
        if payload_raw in ("", "[DONE]"):
            continue
        try:
            payload = json.loads(payload_raw)
        except Exception:
            continue
        if isinstance(payload, dict) and "content" in payload:
            out += payload["content"]
    return out


def test_chat_stream_assamese_cache_hit_sanitises_leaky_response(monkeypatch):
    """When the early cache holds a leaky Assamese answer for
    response_lang="as", the route must sanitise it before streaming so
    the SSE body the client receives has a leakage ratio under the
    configured threshold."""
    from fastapi.testclient import TestClient

    app, chat_mod = _build_chat_app()

    # Stub out anything that the route's early-cache persist task touches
    # so background tasks do not blow up.
    chat_mod.supa_upsert_conversation = AsyncMock(return_value=None)
    chat_mod._persist_chat_turn = AsyncMock(return_value=None)
    chat_mod._record_chat_latency = lambda *_a, **_kw: None
    chat_mod._refund_credit = AsyncMock(return_value=None)

    # Force in-memory cache (no Redis) and prime it with the leaky reply.
    chat_mod.redis_client = None
    chat_mod._redis_get_ai_cache = lambda _k: None  # miss redis, fall to memory

    msg_text = "uruka কি"  # arbitrary user query
    cache_key_msg = f"{msg_text}::lang=as"
    cache_key = chat_mod._cache_key(
        cache_key_msg, subject_id="", board_id="", conversation_id=""
    )
    chat_mod._ai_response_cache.clear()
    chat_mod._ai_response_cache[cache_key] = LEAKY_ASSAMESE_CACHED

    client = TestClient(app)
    body = {
        "message": msg_text,
        "response_lang": "as",
        "subject_id": "",
        "board_id": "",
        "conversation_id": "",
    }
    sse_body = _post_chat_stream(client, body)
    emitted = _extract_emitted_content(sse_body)

    from lang_sanitizer import measure_leakage, get_threshold

    # Sanity: the cached reply WAS leaky (validates the test fixture).
    assert measure_leakage(LEAKY_ASSAMESE_CACHED)["ratio"] > get_threshold()
    # The actual SSE body the client receives must be under threshold.
    assert measure_leakage(emitted)["ratio"] <= get_threshold(), (
        f"emitted leakage ratio={measure_leakage(emitted)['ratio']:.3f} "
        f"threshold={get_threshold():.3f} emitted={emitted!r}"
    )
    assert "me uses" not in emitted
    assert "ssible" not in emitted
    # Still recognisably Assamese.
    assert "উৎসৱ" in emitted


def test_chat_stream_assamese_cache_hit_emits_diagnostic_log(monkeypatch, caplog):
    """Per-Task #419 the route MUST emit one [INDIC-SANITIZE] diagnostic
    log line per Assamese reply (even on no-op clean replies). This test
    seeds the early cache with a leaky reply, runs the stream, and
    asserts a diagnostic line containing ratio/action/translated/
    regenerated/sample_tokens fields was logged."""
    import logging as _logging
    from fastapi.testclient import TestClient

    app, chat_mod = _build_chat_app()
    chat_mod.supa_upsert_conversation = AsyncMock(return_value=None)
    chat_mod._persist_chat_turn = AsyncMock(return_value=None)
    chat_mod._record_chat_latency = lambda *_a, **_kw: None
    chat_mod._refund_credit = AsyncMock(return_value=None)
    chat_mod.redis_client = None
    chat_mod._redis_get_ai_cache = lambda _k: None

    msg_text = "uruka কি"
    cache_key_msg = f"{msg_text}::lang=as"
    cache_key = chat_mod._cache_key(
        cache_key_msg, subject_id="", board_id="", conversation_id=""
    )
    chat_mod._ai_response_cache.clear()
    chat_mod._ai_response_cache[cache_key] = LEAKY_ASSAMESE_CACHED

    client = TestClient(app)
    body = {
        "message": msg_text,
        "response_lang": "as",
        "subject_id": "",
        "board_id": "",
        "conversation_id": "",
    }
    with caplog.at_level(_logging.INFO, logger="routes.ai_chat"):
        _post_chat_stream(client, body)

    # Find the per-reply diagnostic line. Task #467: filter to lines
    # that actually carry the structured diagnostic fields. Other
    # `[INDIC-SANITIZE]` log lines (e.g. the cms_sarvam_health "insert
    # run failed" warning that fires when a sibling test left a
    # MagicMock as `db.indic_sanitize_runs`) must not be picked.
    indic_logs = [
        r for r in caplog.records
        if "[INDIC-SANITIZE]" in r.getMessage() and "action=" in r.getMessage()
    ]
    assert indic_logs, (
        "expected at least one [INDIC-SANITIZE] diagnostic log line "
        "per Assamese reply (Task #419 requirement)"
    )
    # The line must contain all required diagnostic fields.
    msg = indic_logs[-1].getMessage()
    for required_field in (
        "action=", "ratio=", "threshold=", "behaviour=",
        "translated=", "regenerated=", "sample_tokens=",
    ):
        assert required_field in msg, f"diagnostic log missing field: {required_field!r} in {msg!r}"


def test_translate_plus_regenerate_skips_regenerate_when_translate_cleans():
    """Per-Task #419 `translate+regenerate` must be a true FALLBACK:
    if the translate step alone brings leakage at or below threshold,
    the regenerate LLM call MUST NOT be made (it would only add latency
    + cost without improving purity)."""
    import asyncio as _aio  # noqa: F401 (kept for back-compat)
    import asyncio
    from lang_sanitizer import sanitize_assamese_with_optional_regenerate

    regen_called = {"n": 0}

    async def _should_not_run():
        regen_called["n"] += 1
        return "এইটো এটা পুনৰ লিখা উত্তৰ।"

    async def _good_translate(_fragment: str) -> str:
        # Returns a long Assamese span so post-translate ratio drops to ~0.
        return "অসমীয়া অনুবাদিত পাঠ"

    cleaned, diag = _aio.run(
        sanitize_assamese_with_optional_regenerate(
            LEAKY_ASSAMESE_CACHED,
            behaviour="translate+regenerate",
            translate_callable=_good_translate,
            regenerate_callable=_should_not_run,
        )
    )
    # Translate alone cleaned the reply, so regenerate must NOT have run.
    assert regen_called["n"] == 0, (
        "translate+regenerate must skip regenerate when translate "
        "already brings ratio at/under threshold"
    )
    assert diag.get("translated") is True
    assert diag.get("regenerated") is False
    assert "me uses" not in cleaned and "ssible" not in cleaned


def test_translate_plus_regenerate_runs_regenerate_when_translate_insufficient():
    """The opposite case: when the translate step CANNOT bring ratio
    under threshold (e.g. callable returns empty), `translate+regenerate`
    MUST fall back to the regenerate LLM call."""
    import asyncio as _aio  # noqa: F401 (kept for back-compat)
    import asyncio
    from lang_sanitizer import sanitize_assamese_with_optional_regenerate

    regen_called = {"n": 0}
    clean_retry = (
        "উৰুকা হৈছে মাঘ বিহুৰ পূৰ্বৰ ৰাতিৰ উৎসৱ। "
        "অসমৰ মানুহে এই ৰাতি একেলগে ভোজন কৰে।"
    )

    async def _retry():
        regen_called["n"] += 1
        return clean_retry

    async def _empty_translate(_fragment: str) -> str:
        return ""

    cleaned, diag = _aio.run(
        sanitize_assamese_with_optional_regenerate(
            LEAKY_ASSAMESE_CACHED,
            behaviour="translate+regenerate",
            translate_callable=_empty_translate,
            regenerate_callable=_retry,
        )
    )
    # Translate produced nothing, leakage still above threshold → regenerate must fire.
    assert regen_called["n"] == 1
    assert diag.get("regenerated") is True
    assert "me uses" not in cleaned and "ssible" not in cleaned
    # The clean retry text won.
    assert "একেলগে" in cleaned


def test_chat_stream_off_mode_still_logs_and_passes_through_unchanged(monkeypatch, caplog):
    """Per reviewer feedback on Task #419: when ASSAMESE_LEAK_BEHAVIOUR=off
    the chat route MUST still measure leakage and emit one
    [INDIC-SANITIZE] diagnostic log per Assamese reply, but MUST leave
    the response bytes untouched."""
    import logging as _logging
    from fastapi.testclient import TestClient

    monkeypatch.setenv("ASSAMESE_LEAK_BEHAVIOUR", "off")

    app, chat_mod = _build_chat_app()
    chat_mod.supa_upsert_conversation = AsyncMock(return_value=None)
    chat_mod._persist_chat_turn = AsyncMock(return_value=None)
    chat_mod._record_chat_latency = lambda *_a, **_kw: None
    chat_mod._refund_credit = AsyncMock(return_value=None)
    chat_mod.redis_client = None
    chat_mod._redis_get_ai_cache = lambda _k: None

    msg_text = "uruka কি"
    cache_key_msg = f"{msg_text}::lang=as"
    cache_key = chat_mod._cache_key(
        cache_key_msg, subject_id="", board_id="", conversation_id=""
    )
    chat_mod._ai_response_cache.clear()
    chat_mod._ai_response_cache[cache_key] = LEAKY_ASSAMESE_CACHED

    client = TestClient(app)
    body = {
        "message": msg_text,
        "response_lang": "as",
        "subject_id": "",
        "board_id": "",
        "conversation_id": "",
    }
    with caplog.at_level(_logging.INFO, logger="routes.ai_chat"):
        emitted = _post_chat_stream(client, body)

    # 1. Diagnostic line MUST still appear in `off` mode. Task #467:
    #    filter to lines that actually carry the structured diagnostic
    #    fields so unrelated `[INDIC-SANITIZE]` warnings (e.g. the
    #    cms_sarvam_health "insert run failed" message that fires when
    #    a sibling test left a MagicMock as `db.indic_sanitize_runs`)
    #    cannot mask the real diagnostic.
    indic_logs = [
        r for r in caplog.records
        if "[INDIC-SANITIZE]" in r.getMessage() and "behaviour=" in r.getMessage()
    ]
    assert indic_logs, (
        "[INDIC-SANITIZE] diagnostic line must be emitted even when "
        "ASSAMESE_LEAK_BEHAVIOUR=off"
    )
    msg = indic_logs[-1].getMessage()
    assert "behaviour=off" in msg
    assert "ratio=" in msg
    # 2. The response stream MUST be byte-identical to the leaky cached
    #    answer — `off` is measure-only, never mutate.
    assert "me uses" in emitted, (
        "off mode must NOT mutate the response — the leaky text "
        "should pass through to the user untouched"
    )


def test_chat_stream_english_cache_hit_passes_through_unchanged(monkeypatch):
    """For non-Assamese requests the sanitiser must be a no-op so we
    don't accidentally strip legitimate English answers."""
    from fastapi.testclient import TestClient

    app, chat_mod = _build_chat_app()
    chat_mod.supa_upsert_conversation = AsyncMock(return_value=None)
    chat_mod._persist_chat_turn = AsyncMock(return_value=None)
    chat_mod._record_chat_latency = lambda *_a, **_kw: None
    chat_mod._refund_credit = AsyncMock(return_value=None)

    chat_mod.redis_client = None
    chat_mod._redis_get_ai_cache = lambda _k: None

    msg_text = "what is uruka"
    english_answer = "Uruka is the night before Magh Bihu in Assamese culture."
    cache_key = chat_mod._cache_key(
        msg_text, subject_id="", board_id="", conversation_id=""
    )
    chat_mod._ai_response_cache.clear()
    chat_mod._ai_response_cache[cache_key] = english_answer

    client = TestClient(app)
    body = {
        "message": msg_text,
        "response_lang": "en",
        "subject_id": "",
        "board_id": "",
        "conversation_id": "",
    }
    sse_body = _post_chat_stream(client, body)
    emitted = _extract_emitted_content(sse_body)
    assert emitted == english_answer
