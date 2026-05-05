"""Task #382 — Worker contract tests for providers.workers_embed.

Pins the HTTP contract the backend speaks to the custom Cloudflare
Worker at ``WORKERS_EMBED_URL``: auth header, JSON body shape,
response shape, batch splitting, dim validation, and retry behaviour.

The real worker isn't exercised here — we mock the HTTP transport so
the test suite stays hermetic.
"""
from __future__ import annotations

import json
import os
import sys

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Configure the worker provider for the test process BEFORE importing it
# so the module-level WORKERS_EMBED_URL/SECRET reads pick up the values.
os.environ["WORKERS_EMBED_URL"] = "https://embed.test.local"
os.environ["WORKERS_EMBED_SECRET"] = "test-secret-abc123"
os.environ["WORKERS_EMBED_DIMS"] = "1024"
os.environ["WORKERS_EMBED_MAX_BATCH"] = "4"
os.environ["WORKERS_EMBED_RETRIES"] = "1"


def _fresh_module():
    """Re-import providers.workers_embed so env-var patches stick."""
    sys.modules.pop("providers.workers_embed", None)
    import providers.workers_embed as we  # noqa: WPS433
    return we


class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, handler):
        self._handler = handler
        self.calls: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        return self._handler(request)


def _patch_client(monkeypatch, we_mod, transport: _MockTransport):
    client = httpx.AsyncClient(transport=transport, timeout=5.0)

    async def _get_client():
        return client

    monkeypatch.setattr(we_mod, "_get_client", _get_client)
    return client


@pytest.mark.asyncio
async def test_embed_sends_secret_header_and_returns_vectors(monkeypatch):
    we = _fresh_module()

    def handler(request):
        assert request.method == "POST"
        assert request.url.path == "/embed"
        assert request.headers["x-embed-secret"] == "test-secret-abc123"
        body = json.loads(request.content)
        assert body["texts"] == ["hello", "world"]
        assert body["task_type"] == "search_document"
        return httpx.Response(
            200,
            json={
                "vectors": [[0.1] * 1024, [0.2] * 1024],
                "dims": 1024,
                "count": 2,
                "model_version": "1.0.0",
                "models": ["@cf/test/a", "@cf/test/b"],
            },
        )

    transport = _MockTransport(handler)
    _patch_client(monkeypatch, we, transport)
    out = await we.embed(["hello", "world"], input_type="search_document")
    assert len(out) == 2
    assert all(len(v) == 1024 for v in out)
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_embed_splits_oversize_batch_into_max_batch_chunks(monkeypatch):
    we = _fresh_module()

    def handler(request):
        body = json.loads(request.content)
        n = len(body["texts"])
        return httpx.Response(
            200,
            json={
                "vectors": [[0.0] * 1024 for _ in range(n)],
                "dims": 1024,
                "count": n,
                "model_version": "1.0.0",
                "models": [],
            },
        )

    transport = _MockTransport(handler)
    _patch_client(monkeypatch, we, transport)
    # 9 texts with WORKERS_EMBED_MAX_BATCH=4 → 3 calls (4 + 4 + 1)
    out = await we.embed([f"t{i}" for i in range(9)])
    assert len(out) == 9
    sizes = [len(json.loads(c.content)["texts"]) for c in transport.calls]
    assert sizes == [4, 4, 1]


@pytest.mark.asyncio
async def test_embed_rejects_dim_mismatch(monkeypatch):
    we = _fresh_module()

    def handler(request):
        return httpx.Response(
            200,
            json={
                "vectors": [[0.0] * 768],  # wrong dim
                "dims": 768,
                "count": 1,
                "model_version": "1.0.0",
                "models": [],
            },
        )

    transport = _MockTransport(handler)
    _patch_client(monkeypatch, we, transport)
    with pytest.raises(RuntimeError, match="dim mismatch"):
        await we.embed(["x"])


@pytest.mark.asyncio
async def test_embed_query_returns_empty_on_failure(monkeypatch):
    we = _fresh_module()

    def handler(request):
        return httpx.Response(503, json={"error": "down"})

    transport = _MockTransport(handler)
    _patch_client(monkeypatch, we, transport)
    out = await we.embed_query("hi")
    assert out == []
    # 1 retry + initial = 2 calls
    assert len(transport.calls) == 2


@pytest.mark.asyncio
async def test_health_check_reports_dims_and_models(monkeypatch):
    we = _fresh_module()

    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/health"
        return httpx.Response(
            200,
            json={
                "ok": True,
                "dims": 1024,
                "batch": 32,
                "models": ["@cf/google/gemma-3-1b-it", "@cf/qwen/qwen2.5-0.5b-instruct"],
                "version": "1.0.0",
            },
        )

    transport = _MockTransport(handler)
    _patch_client(monkeypatch, we, transport)
    info = await we.health_check()
    assert info["ok"] is True
    assert info["dims"] == 1024
    assert "@cf/google/gemma-3-1b-it" in info["models"]


def test_is_enabled_and_expected_dims():
    we = _fresh_module()
    assert we.is_enabled() is True
    assert we.expected_dims() == 1024
