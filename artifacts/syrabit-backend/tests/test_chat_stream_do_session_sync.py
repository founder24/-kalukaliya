"""End-to-end regression test for ChatSession DO read+write on the
streaming chat endpoint (Task #429).

We drive ``/api/ai/chat/stream`` for a single authenticated turn with
``do_chat.is_enabled`` patched to True and ``do_chat._do_request``
mocked. The test asserts that:

1. Both ``session_get_total`` and ``session_put_total`` increment for
   the streaming turn (i.e. the route both reads the prior session at
   the top of ``_chat_stream_impl`` and persists ``last_provider`` /
   ``last_assistant_answer`` after the turn completes).
2. The mocked ``_do_request`` PUT receives a payload that carries
   ``last_provider``, ``conversation_id``, the user's last message and
   the assistant's last answer.

The test reaches the post-LLM persist block by priming the L2 legacy
``_ai_response_cache`` so ``event_stream`` short-circuits via the
"cached_answer" path inside ``event_stream`` (no real LLM dispatch),
which still flows through the persist + DO-mirror block at the bottom
of the streaming generator.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from tests._deps_stub import install_deps_stub  # noqa: E402

install_deps_stub()


CACHED_ANSWER = "Cached test answer for DO-session sync."


def _build_chat_app(user_id: str = "u-test-do-sync"):
    """Mount the chat router with stubs sufficient to drive a single
    authenticated streaming turn that hits the L2 cached_answer path
    inside ``event_stream``.
    """
    from fastapi import FastAPI
    from routes import ai_chat as chat_mod
    from auth_deps import rate_limit_chat_optional

    async def _auth_user():
        return {"id": user_id, "plan": "free", "email": "do-sync@test"}

    chat_mod.classify_intent = lambda _q: ("notes", "notes")
    chat_mod.get_instant_response = lambda _q: None
    chat_mod.evaluate_prompt_safety = lambda _q: (True, None, "")

    # Credit/auth machinery — keep credits available, deduction succeeds,
    # background side-effects are no-ops.
    chat_mod.get_user_credits = AsyncMock(
        return_value={"used": 0, "limit": 100, "remaining": 100}
    )
    chat_mod.atomic_deduct_credit = AsyncMock(return_value=True)
    chat_mod._refund_credit = AsyncMock(return_value=None)
    chat_mod._persist_chat_turn = AsyncMock(return_value=None)
    chat_mod._log_chat_message = AsyncMock(return_value=None)
    chat_mod.supa_upsert_conversation = AsyncMock(return_value=None)
    chat_mod.supa_update_conversation = AsyncMock(return_value=None)
    chat_mod.supa_update_user = AsyncMock(return_value=None)
    chat_mod.supa_get_user_by_id = AsyncMock(return_value=None)
    # Critical: supa_get_conversation is awaited inside _prefetch_history /
    # _fetch_followup_info; without an AsyncMock it returns a non-awaitable
    # MagicMock from the deps stub and the route blows up assembling
    # raw_history. Returning None matches a fresh, server-unknown conv.
    chat_mod.supa_get_conversation = AsyncMock(return_value=None)
    # Memory-brain helpers are awaited in Phase 0; default stubs return
    # an empty list so the personalised-memory branch is a no-op.
    chat_mod._mb_query_user_memories = AsyncMock(return_value=[])
    chat_mod._mb_write_chat_turn_memory = AsyncMock(return_value=None)
    chat_mod._record_chat_latency = lambda *_a, **_kw: None
    chat_mod._record_llm_cost = lambda *_a, **_kw: None

    # Force in-memory answer cache (no Redis path).
    chat_mod.redis_client = None
    chat_mod._redis_get_ai_cache = lambda _k: None

    app = FastAPI()
    app.include_router(chat_mod.router, prefix="/api")
    app.dependency_overrides[rate_limit_chat_optional] = _auth_user
    return app, chat_mod


def _post_chat_stream(client, body) -> str:
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


def test_chat_stream_do_session_get_and_put_both_increment(monkeypatch):
    """Driving a single streaming turn with DO_CHAT_ON simulated must
    bump both ``session_get_total`` (top-of-impl resume read) and
    ``session_put_total`` (post-LLM mirror write), and the PUT payload
    must carry ``last_provider``, ``conversation_id``, and the
    user/assistant turn.
    """
    from fastapi.testclient import TestClient
    import do_chat

    app, chat_mod = _build_chat_app()

    # --- DO_CHAT_ON simulation -------------------------------------------
    # ``is_enabled()`` reads ``config.DO_CHAT_ON`` lazily, so we patch
    # the source flag rather than the function. This matches how the
    # production code gates the get/put calls.
    import config as _config
    monkeypatch.setattr(_config, "DO_CHAT_ON", True, raising=False)
    # ``_do_request`` only fires when both base URL + secret are set; the
    # real values would point at the edge worker. We only need
    # ``_do_configured()`` to return True so the get/put dispatch path
    # is selected.
    monkeypatch.setattr(do_chat, "_DO_BASE", "https://edge.test", raising=False)
    monkeypatch.setattr(do_chat, "_DO_SECRET", "test-secret", raising=False)

    # --- Capture every _do_request call ----------------------------------
    captured: list[dict] = []

    async def _fake_do_request(method, path, *, json=None):
        captured.append({"method": method, "path": path, "json": json})
        if method == "GET":
            # No prior session — matches a cold conversation.
            return {"ok": True, "session": None}
        return {"ok": True}

    monkeypatch.setattr(do_chat, "_do_request", _fake_do_request, raising=True)

    # Counter baselines so we measure the delta caused by THIS turn.
    base = do_chat.snapshot()
    base_get = base["session_get_total"]
    base_put = base["session_put_total"]

    # --- Prime the L2 legacy answer cache --------------------------------
    # event_stream's L2 lookup uses ``_cache_key(message, subject_id,
    # board_id, conversation_id)`` (the legacy helper). Priming it makes
    # ``cached_answer`` non-empty inside event_stream so the route
    # short-circuits LLM dispatch and flows straight into the persist
    # + DO-mirror block.
    msg_text = "what is photosynthesis"
    conv_id = "conv-do-sync-test"
    legacy_key = chat_mod._cache_key(
        msg_text, subject_id="", board_id="", conversation_id=conv_id
    )
    chat_mod._ai_response_cache.clear()
    chat_mod._ai_response_cache[legacy_key] = CACHED_ANSWER

    # --- Drive the streaming turn ----------------------------------------
    client = TestClient(app)
    body = {
        "message": msg_text,
        "response_lang": "en",
        "subject_id": "",
        "board_id": "",
        "conversation_id": conv_id,
    }
    sse_body = _post_chat_stream(client, body)
    emitted = _extract_emitted_content(sse_body)
    assert CACHED_ANSWER in emitted, (
        f"expected cached answer to surface in SSE body; got {emitted!r}"
    )

    # The DO put is fire-and-forget via ``asyncio.create_task`` inside
    # event_stream. The TestClient already drained the response body
    # generator (which is what created the task), but the task itself
    # may still be a tick away from running. Spin the loop briefly so
    # any pending PUTs land before we read counters.
    async def _drain():
        for _ in range(20):
            await asyncio.sleep(0)
    asyncio.get_event_loop().run_until_complete(_drain())

    snap = do_chat.snapshot()
    assert snap["session_get_total"] - base_get >= 1, (
        f"session_get_total did not move: base={base_get} now={snap['session_get_total']}"
    )
    assert snap["session_put_total"] - base_put >= 1, (
        f"session_put_total did not move: base={base_put} now={snap['session_put_total']}"
    )

    # --- Validate the persisted PUT payload ------------------------------
    put_calls = [c for c in captured if c["method"] == "PUT"]
    get_calls = [c for c in captured if c["method"] == "GET"]
    assert get_calls, "expected at least one GET /do/chat-session/<id> call"
    assert get_calls[0]["path"].endswith(f"/do/chat-session/{conv_id}"), (
        f"unexpected GET path: {get_calls[0]['path']!r}"
    )
    assert put_calls, "expected at least one PUT /do/chat-session/<id> call"

    put = put_calls[-1]
    assert put["path"].endswith(f"/do/chat-session/{conv_id}"), (
        f"unexpected PUT path: {put['path']!r}"
    )
    assert isinstance(put["json"], dict)
    session_payload = put["json"].get("session")
    assert isinstance(session_payload, dict), (
        f"PUT body missing 'session' object: {put['json']!r}"
    )
    # Required fields per Task #429.
    assert "last_provider" in session_payload, session_payload
    assert session_payload.get("conversation_id") == conv_id, session_payload
    assert session_payload.get("last_user_message") == msg_text, session_payload
    assert CACHED_ANSWER in (session_payload.get("last_assistant_answer") or ""), (
        session_payload
    )
