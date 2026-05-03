"""Unit tests for providers.azure_openai key-failover candidate chain (Task #290).

We don't make live HTTP calls; we monkey-patch the module's gateway/key
flags and verify the candidate list order, header shapes, and fail-fast vs
fail-over behaviour for retryable HTTP statuses.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import httpx
import pytest

from providers import azure_openai as az


def _set_modes(monkeypatch, *, gateway: bool, key1: str, key2: str):
    monkeypatch.setattr(az, "_GATEWAY_AVAILABLE", gateway)
    monkeypatch.setattr(az, "_KEY_1", key1)
    monkeypatch.setattr(az, "_KEY_2", key2)
    monkeypatch.setattr(az, "_DIRECT_ENDPOINT", "https://example.openai.azure.com")


def test_candidate_order_full_chain(monkeypatch):
    _set_modes(monkeypatch, gateway=True, key1="k1", key2="k2")
    with patch.object(az, "is_cf_gateway_up", return_value=True), \
         patch.object(az, "cf_gateway_url", return_value="https://gw/azure-openai"):
        chain = az._candidates()
    labels = [c[0] for c in chain]
    assert labels == ["cf_byok", "direct_key_1", "direct_key_2"], labels


def test_candidate_order_direct_only_when_gateway_disabled(monkeypatch):
    _set_modes(monkeypatch, gateway=False, key1="k1", key2="k2")
    chain = az._candidates()
    assert [c[0] for c in chain] == ["direct_key_1", "direct_key_2"]


def test_candidate_skips_missing_key1(monkeypatch):
    _set_modes(monkeypatch, gateway=False, key1="", key2="k2")
    chain = az._candidates()
    assert [c[0] for c in chain] == ["direct_key_2"]


def test_candidate_empty_when_no_endpoint(monkeypatch):
    monkeypatch.setattr(az, "_GATEWAY_AVAILABLE", False)
    monkeypatch.setattr(az, "_KEY_1", "k1")
    monkeypatch.setattr(az, "_KEY_2", "k2")
    monkeypatch.setattr(az, "_DIRECT_ENDPOINT", "")
    assert az._candidates() == []


def test_direct_headers_use_api_key_subscription_form(monkeypatch):
    headers = az._direct_headers("secret-key-xyz")
    assert headers["api-key"] == "secret-key-xyz"
    assert headers["Content-Type"] == "application/json"
    # Direct mode must NOT send the BYOK / cf-aig-* headers.
    assert "cf-aig-byok-key" not in headers
    assert "Authorization" not in headers


def test_gateway_headers_keep_byok_contract():
    """BYOK contract from Task #267: empty Authorization + cf-aig-byok-key true."""
    h = az._gateway_headers()
    assert h["api-key"] == az.BYOK_PLACEHOLDER
    assert h["cf-aig-byok-key"] == "true"
    assert h["Authorization"] == ""


# ── Failover behaviour ───────────────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, status_code: int, json_body: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._json = json_body or {}
        self.text = text or ""

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            req = httpx.Request("POST", "https://x")
            resp = httpx.Response(self.status_code, request=req, text=self.text)
            raise httpx.HTTPStatusError("err", request=req, response=resp)


def _ok_chat_body() -> dict:
    return {"choices": [{"message": {"content": "PONG"}}]}


def test_call_chat_advances_on_429_succeeds_on_key2(monkeypatch):
    _set_modes(monkeypatch, gateway=False, key1="k1", key2="k2")

    posts: list[dict] = []

    class _FakeClient:
        async def post(self, url, headers, json):
            posts.append({"url": url, "api_key": headers.get("api-key")})
            if headers.get("api-key") == "k1":
                return _FakeResponse(429, text="rate limited")
            return _FakeResponse(200, _ok_chat_body())

    monkeypatch.setattr(az, "_get_client", lambda: _FakeClient())

    out = asyncio.run(az.call_chat([{"role": "user", "content": "hi"}], max_tokens=4))
    assert out == "PONG"
    assert [p["api_key"] for p in posts] == ["k1", "k2"]


def test_call_chat_fails_fast_on_404(monkeypatch):
    """Non-retryable 404 (DeploymentNotFound) must NOT advance to KEY_2."""
    _set_modes(monkeypatch, gateway=False, key1="k1", key2="k2")
    posts: list[str] = []

    class _FakeClient:
        async def post(self, url, headers, json):
            posts.append(headers.get("api-key"))
            return _FakeResponse(404, text="DeploymentNotFound")

    monkeypatch.setattr(az, "_get_client", lambda: _FakeClient())

    with pytest.raises(RuntimeError, match=r"HTTP 404"):
        asyncio.run(az.call_chat([{"role": "user", "content": "hi"}], max_tokens=4))
    assert posts == ["k1"], "fail-fast: must NOT call KEY_2 on non-retryable status"


def test_call_chat_advances_on_connect_error(monkeypatch):
    _set_modes(monkeypatch, gateway=False, key1="k1", key2="k2")
    seen: list[str] = []

    class _FakeClient:
        async def post(self, url, headers, json):
            seen.append(headers.get("api-key"))
            if headers.get("api-key") == "k1":
                raise httpx.ConnectError("network down")
            return _FakeResponse(200, _ok_chat_body())

    monkeypatch.setattr(az, "_get_client", lambda: _FakeClient())
    out = asyncio.run(az.call_chat([{"role": "user", "content": "hi"}], max_tokens=4))
    assert out == "PONG"
    assert seen == ["k1", "k2"]


def test_call_chat_raises_when_no_candidates(monkeypatch):
    monkeypatch.setattr(az, "_GATEWAY_AVAILABLE", False)
    monkeypatch.setattr(az, "_KEY_1", "")
    monkeypatch.setattr(az, "_KEY_2", "")
    monkeypatch.setattr(az, "_DIRECT_ENDPOINT", "")
    with pytest.raises(RuntimeError, match="no candidates"):
        asyncio.run(az.call_chat([{"role": "user", "content": "hi"}]))


# ── stream_chat failover ─────────────────────────────────────────────────────

class _FakeStreamCM:
    """Minimal async-context-manager mimicking httpx streaming response."""
    def __init__(self, status_code: int, lines: list[str] | None = None,
                 connect_after: int | None = None, body: bytes = b""):
        self.status_code = status_code
        self._lines = lines or []
        self._connect_after = connect_after  # raise mid-stream after N tokens
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aread(self):
        return self._body

    async def aiter_lines(self):
        for i, line in enumerate(self._lines):
            if self._connect_after is not None and i >= self._connect_after:
                raise httpx.ConnectError("network dropped mid-stream")
            yield line


def _sse(content: str) -> str:
    import json as _j
    return "data: " + _j.dumps({"choices": [{"delta": {"content": content}}]})


class _FakeStreamClient:
    def __init__(self, responses_by_key):
        self._by_key = responses_by_key
        self.calls: list[str] = []

    def stream(self, method, url, headers, json, timeout):
        k = headers.get("api-key")
        self.calls.append(k)
        return self._by_key[k]


def test_stream_chat_advances_on_retryable_status_then_succeeds(monkeypatch):
    _set_modes(monkeypatch, gateway=False, key1="k1", key2="k2")
    client = _FakeStreamClient({
        "k1": _FakeStreamCM(429, body=b"slow down"),
        "k2": _FakeStreamCM(200, lines=[_sse("PONG"), "data: [DONE]"]),
    })
    monkeypatch.setattr(az, "_get_client", lambda: client)

    async def collect():
        out = []
        async for t in az.stream_chat([{"role": "user", "content": "hi"}], max_tokens=4):
            out.append(t)
        return out

    assert "".join(asyncio.run(collect())) == "PONG"
    assert client.calls == ["k1", "k2"]


def test_stream_chat_fails_fast_on_404(monkeypatch):
    _set_modes(monkeypatch, gateway=False, key1="k1", key2="k2")
    client = _FakeStreamClient({
        "k1": _FakeStreamCM(404, body=b"DeploymentNotFound"),
    })
    monkeypatch.setattr(az, "_get_client", lambda: client)

    async def collect():
        async for _ in az.stream_chat([{"role": "user", "content": "hi"}], max_tokens=4):
            pass

    with pytest.raises(RuntimeError, match=r"HTTP 404"):
        asyncio.run(collect())
    assert client.calls == ["k1"], "404 must NOT failover to KEY_2"


def test_stream_chat_advances_on_empty_stream(monkeypatch):
    _set_modes(monkeypatch, gateway=False, key1="k1", key2="k2")
    client = _FakeStreamClient({
        "k1": _FakeStreamCM(200, lines=["data: [DONE]"]),  # empty
        "k2": _FakeStreamCM(200, lines=[_sse("PONG"), "data: [DONE]"]),
    })
    monkeypatch.setattr(az, "_get_client", lambda: client)

    async def collect():
        return [t async for t in az.stream_chat([{"role": "user", "content": "hi"}])]

    assert "".join(asyncio.run(collect())) == "PONG"
    assert client.calls == ["k1", "k2"]


def test_stream_chat_propagates_mid_stream_error(monkeypatch):
    """After first token is yielded, a connect error must propagate, NOT failover."""
    _set_modes(monkeypatch, gateway=False, key1="k1", key2="k2")
    client = _FakeStreamClient({
        "k1": _FakeStreamCM(200, lines=[_sse("PO"), _sse("NG")], connect_after=1),
        "k2": _FakeStreamCM(200, lines=[_sse("PONG"), "data: [DONE]"]),
    })
    monkeypatch.setattr(az, "_get_client", lambda: client)

    async def collect():
        out = []
        async for t in az.stream_chat([{"role": "user", "content": "hi"}]):
            out.append(t)
        return out

    with pytest.raises(RuntimeError, match=r"mid-stream"):
        asyncio.run(collect())
    # KEY_2 must NOT be retried — caller should see the partial stream + error
    assert client.calls == ["k1"]


# ── call_embed parity ───────────────────────────────────────────────────────

def test_call_embed_advances_on_429(monkeypatch):
    _set_modes(monkeypatch, gateway=False, key1="k1", key2="k2")
    seen: list[str] = []

    class _C:
        async def post(self, url, headers, json):
            seen.append(headers.get("api-key"))
            if headers.get("api-key") == "k1":
                return _FakeResponse(429, text="rate")
            return _FakeResponse(200, {"data": [{"embedding": [0.1, 0.2, 0.3]}]})

    monkeypatch.setattr(az, "_get_client", lambda: _C())
    vec = asyncio.run(az.call_embed("hello"))
    assert vec == [0.1, 0.2, 0.3]
    assert seen == ["k1", "k2"]


def test_call_embed_fails_fast_on_404(monkeypatch):
    _set_modes(monkeypatch, gateway=False, key1="k1", key2="k2")
    seen: list[str] = []

    class _C:
        async def post(self, url, headers, json):
            seen.append(headers.get("api-key"))
            return _FakeResponse(404, text="DeploymentNotFound")

    monkeypatch.setattr(az, "_get_client", lambda: _C())
    with pytest.raises(RuntimeError, match=r"HTTP 404"):
        asyncio.run(az.call_embed("hello"))
    assert seen == ["k1"]


def test_call_embed_advances_on_empty_then_succeeds(monkeypatch):
    _set_modes(monkeypatch, gateway=False, key1="k1", key2="k2")

    class _C:
        async def post(self, url, headers, json):
            if headers.get("api-key") == "k1":
                return _FakeResponse(200, {"data": [{"embedding": []}]})
            return _FakeResponse(200, {"data": [{"embedding": [1.0]}]})

    monkeypatch.setattr(az, "_get_client", lambda: _C())
    assert asyncio.run(az.call_embed("hi")) == [1.0]


# ── call_stt parity ─────────────────────────────────────────────────────────

def test_call_stt_advances_on_connect_error(monkeypatch):
    _set_modes(monkeypatch, gateway=False, key1="k1", key2="k2")
    seen: list[str] = []

    class _C:
        async def post(self, url, headers, files, data):
            seen.append(headers.get("api-key"))
            if headers.get("api-key") == "k1":
                raise httpx.ConnectError("nope")
            return _FakeResponse(200, {"text": "hello world"})

    monkeypatch.setattr(az, "_get_client", lambda: _C())
    out = asyncio.run(az.call_stt(b"audio"))
    assert out == "hello world"
    assert seen == ["k1", "k2"]


def test_call_stt_fails_fast_on_400(monkeypatch):
    _set_modes(monkeypatch, gateway=False, key1="k1", key2="k2")
    seen: list[str] = []

    class _C:
        async def post(self, url, headers, files, data):
            seen.append(headers.get("api-key"))
            return _FakeResponse(400, text="bad request")

    monkeypatch.setattr(az, "_get_client", lambda: _C())
    with pytest.raises(RuntimeError, match=r"HTTP 400"):
        asyncio.run(az.call_stt(b"audio"))
    assert seen == ["k1"]


def test_call_stt_strips_content_type_for_multipart(monkeypatch):
    _set_modes(monkeypatch, gateway=False, key1="k1", key2="")
    captured: dict = {}

    class _C:
        async def post(self, url, headers, files, data):
            captured["headers"] = headers
            return _FakeResponse(200, {"text": "ok"})

    monkeypatch.setattr(az, "_get_client", lambda: _C())
    asyncio.run(az.call_stt(b"a"))
    # multipart requires httpx to set its own Content-Type with boundary
    assert "Content-Type" not in captured["headers"]
    assert captured["headers"].get("api-key") == "k1"


# ── health_check semantics ──────────────────────────────────────────────────

def test_health_check_reports_full_chain(monkeypatch):
    _set_modes(monkeypatch, gateway=True, key1="k1", key2="k2")
    with patch.object(az, "is_cf_gateway_up", return_value=True), \
         patch.object(az, "cf_gateway_url", return_value="https://gw/azure-openai"):
        h = asyncio.run(az.health_check())
    assert h["ok"] is True
    assert h["candidates"] == ["cf_byok", "direct_key_1", "direct_key_2"]
    assert h["gateway_available"] is True
    assert h["direct_available"] is True
    assert h["key_1_set"] is True
    assert h["key_2_set"] is True


def test_health_check_gateway_down_direct_available(monkeypatch):
    _set_modes(monkeypatch, gateway=False, key1="k1", key2="")
    h = asyncio.run(az.health_check())
    assert h["ok"] is True
    assert h["candidates"] == ["direct_key_1"]
    assert h["gateway_available"] is False
    assert h["direct_available"] is True


def test_health_check_disabled_when_nothing_configured(monkeypatch):
    monkeypatch.setattr(az, "ENABLED", False)
    h = asyncio.run(az.health_check())
    assert h["ok"] is False
    assert "no candidates" in h["reason"]
