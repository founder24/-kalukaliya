"""
Task #490 — vertex_format.format_with_vertex contract.

The only remaining Vertex surface in syrabit-backend is the
NotebookLM-style content formatter. This test pins the public
contract so a refactor that changes the signature, the return
shape, or the (style, lang) plumbing gets caught immediately.

Network calls are not exercised — we monkeypatch the underlying
Gemini call so the test runs offline.
"""

from __future__ import annotations

import inspect
import pytest

import vertex_format


def test_format_with_vertex_signature_is_keyword_only_style_lang():
    sig = inspect.signature(vertex_format.format_with_vertex)
    params = sig.parameters
    assert "text" in params
    # `style` and `lang` must remain keyword-only so accidental positional
    # callers (e.g. an old `_call_vertex_chat(messages, model, max_tokens)`
    # caller) cannot silently coerce into the new API.
    assert params["style"].kind == inspect.Parameter.KEYWORD_ONLY
    assert params["lang"].kind == inspect.Parameter.KEYWORD_ONLY


@pytest.mark.anyio
async def test_format_with_vertex_returns_string_and_calls_vertex_endpoint(monkeypatch):
    """Patch credentials + httpx to assert format_with_vertex (a) hits a
    Vertex `:generateContent` URL, (b) sends a `contents`/`systemInstruction`
    payload, and (c) returns the joined `parts[].text` from the response."""
    captured: dict = {}

    class _FakeCreds:
        token = "fake-token"

    async def _fake_creds():
        return _FakeCreds(), "fake-project"

    monkeypatch.setattr(vertex_format, "_ensure_creds", _fake_creds, raising=True)

    class _FakeResp:
        def raise_for_status(self):  # noqa: D401
            return None
        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": "POLISHED"}]}}]}

    class _FakeClient:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return _FakeResp()

    import httpx as _httpx
    monkeypatch.setattr(_httpx, "AsyncClient", _FakeClient, raising=True)
    # Prime the breaker so allow() == True
    vertex_format.force_breaker_close()

    out = await vertex_format.format_with_vertex(
        "Some raw notes about photosynthesis.",
        style="notebook_lm",
        lang="as",
    )
    assert out == "POLISHED"
    assert ":generateContent" in captured["url"]
    assert "aiplatform.googleapis.com" in captured["url"]
    payload = captured["json"]
    assert "contents" in payload and "systemInstruction" in payload
    assert payload["contents"][0]["parts"][0]["text"].startswith("Some raw notes")


@pytest.fixture
def anyio_backend():
    return "asyncio"
