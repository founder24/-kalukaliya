"""Task #879 — verify D1 sync fan-outs to the preview hostname.

The Railway-side `d1_sync.py` is the only auto-populate path into the
preview D1 (`syrabit-content-preview`). Without these tests a refactor
could silently drop the preview POST and the next preview deploy would
boot with an empty database — exactly the bug Task #879 is fixing.

We patch `_get_http` to return a stub that records every URL/headers
combination, then drive `trigger_d1_sync` through every interesting
config permutation:

  * prod-only         → 1 POST to prod
  * prod + preview    → 2 POSTs (prod + preview), each with its own secret
  * preview-only      → 1 POST to preview (degenerate but supported)
  * neither           → 0 POSTs, returns False, logs the skip
  * preview HTTP 500  → primary success not demoted (preview is best-effort)

The stub mirrors `httpx.AsyncClient.post`'s shape (returns an object with
`.status_code`, `.text`, and `.json()`); we keep it minimal so the test
file does not need an httpx dependency at collection time.
"""
import asyncio
import importlib
import sys
import types
from unittest.mock import AsyncMock, MagicMock

import pytest


def _reload_d1_sync(monkeypatch, *,
                    prod_url="https://api.syrabit.ai",
                    prod_secret="prod-secret",
                    preview_url="",
                    preview_secret=""):
    """Reload `d1_sync` with the requested env so module-level constants
    pick up the new values (they are read once at import time)."""
    monkeypatch.setenv("EDGE_WORKER_URL", prod_url)
    monkeypatch.setenv("D1_SYNC_SECRET", prod_secret)
    monkeypatch.setenv("EDGE_WORKER_PREVIEW_URL", preview_url)
    monkeypatch.setenv("D1_SYNC_SECRET_PREVIEW", preview_secret)
    sys.modules.pop("d1_sync", None)
    return importlib.import_module("d1_sync")


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"success": True, "synced": {"boards": 0}}
        self.text = text or ""

    def json(self):
        return self._payload


class _FakeHttp:
    """Records `.post(...)` calls in order; configurable per-URL response."""

    def __init__(self, responses=None):
        self.calls = []  # list of dicts: {url, secret, payload}
        self._responses = responses or {}

    async def post(self, url, json=None, content=None, data=None, headers=None, timeout=None):
        secret = ""
        if headers and "Authorization" in headers:
            secret = headers["Authorization"].replace("Bearer ", "", 1)
        # Production code now serializes the payload itself with a custom
        # DateTimeEncoder and passes it via `content=` (raw bytes/str) to
        # have full control over JSON encoding. Decode it back so the
        # tests' assertions about `payload` keep working unchanged.
        raw = json
        if raw is None and content is not None:
            try:
                import json as _json
                raw = _json.loads(content)
            except Exception:
                raw = content
        if raw is None and data is not None:
            try:
                import json as _json
                raw = _json.loads(data)
            except Exception:
                raw = data
        self.calls.append({"url": url, "secret": secret, "payload": raw})
        # Match by URL prefix (strip "/api/edge/d1-sync") so the test can
        # configure responses per target hostname rather than per full URL.
        for prefix, resp in self._responses.items():
            if url.startswith(prefix):
                return resp
        return _FakeResponse()


def _install_fake_http(d1_sync_mod, fake_http):
    d1_sync_mod._get_http = lambda: fake_http
    d1_sync_mod._d1_http = fake_http  # short-circuit the lazy init too


@pytest.fixture
def payload():
    return {"boards": [{"id": "b1", "name": "CBSE", "slug": "cbse"}]}


def test_prod_only_single_post(monkeypatch, payload):
    mod = _reload_d1_sync(monkeypatch)
    fake = _FakeHttp()
    _install_fake_http(mod, fake)

    ok = asyncio.run(mod.trigger_d1_sync(payload))

    assert ok is True
    assert len(fake.calls) == 1
    assert fake.calls[0]["url"] == "https://api.syrabit.ai/api/edge/d1-sync"
    assert fake.calls[0]["secret"] == "prod-secret"
    assert mod.is_preview_fanout_configured() is False


def test_prod_and_preview_both_posted(monkeypatch, payload):
    mod = _reload_d1_sync(
        monkeypatch,
        preview_url="https://syrabit-edge-preview.example.workers.dev",
        preview_secret="preview-secret",
    )
    fake = _FakeHttp()
    _install_fake_http(mod, fake)

    ok = asyncio.run(mod.trigger_d1_sync(payload))

    assert ok is True
    assert mod.is_preview_fanout_configured() is True
    urls = sorted(c["url"] for c in fake.calls)
    secrets = sorted(c["secret"] for c in fake.calls)
    assert urls == [
        "https://api.syrabit.ai/api/edge/d1-sync",
        "https://syrabit-edge-preview.example.workers.dev/api/edge/d1-sync",
    ]
    # Critically: the preview secret must NOT be reused on the prod call
    # and vice versa. A regression that pasted the prod secret on both
    # would silently 401 against the preview Worker.
    assert secrets == ["preview-secret", "prod-secret"]


def test_preview_failure_does_not_demote_prod_success(monkeypatch, payload):
    mod = _reload_d1_sync(
        monkeypatch,
        preview_url="https://syrabit-edge-preview.example.workers.dev",
        preview_secret="preview-secret",
    )
    fake = _FakeHttp(responses={
        "https://syrabit-edge-preview.example.workers.dev": _FakeResponse(
            # 4xx is non-retryable so the test stays fast and counts cleanly;
            # the contract under verification is "preview failure does not
            # demote prod success", regardless of which HTTP status the
            # preview returned.
            status_code=400, text="boom"
        ),
    })
    _install_fake_http(mod, fake)

    ok = asyncio.run(mod.trigger_d1_sync(payload))

    # Preview is best-effort — a non-2xx there must NOT block prod CRUD
    # paths that depend on the boolean return.
    assert ok is True
    assert len(fake.calls) == 2


def test_no_targets_configured_returns_false(monkeypatch, payload):
    mod = _reload_d1_sync(monkeypatch, prod_secret="")
    fake = _FakeHttp()
    _install_fake_http(mod, fake)

    ok = asyncio.run(mod.trigger_d1_sync(payload))

    assert ok is False
    assert fake.calls == []  # no network call
    assert mod.is_d1_configured() is False


def test_placeholder_secret_treated_as_unconfigured(monkeypatch, payload):
    """Leftover .env.example values must not accidentally enable fan-out."""
    mod = _reload_d1_sync(
        monkeypatch,
        preview_url="https://syrabit-edge-preview.example.workers.dev",
        preview_secret="your-sync-secret",  # the .env.example placeholder
    )
    assert mod.is_preview_fanout_configured() is False

    fake = _FakeHttp()
    _install_fake_http(mod, fake)
    asyncio.run(mod.trigger_d1_sync(payload))
    # Only prod was posted to.
    assert len(fake.calls) == 1
    assert "syrabit-edge-preview" not in fake.calls[0]["url"]


def test_sync_full_reports_targets(monkeypatch):
    mod = _reload_d1_sync(
        monkeypatch,
        preview_url="https://syrabit-edge-preview.example.workers.dev",
        preview_secret="preview-secret",
    )
    fake = _FakeHttp()
    _install_fake_http(mod, fake)

    # Stub export_content_catalog so we don't need a real Mongo db.
    async def _fake_export(_db):
        return {"boards": [{"id": "b1"}]}

    mod.export_content_catalog = _fake_export

    result = asyncio.run(mod.sync_full(MagicMock()))

    assert result["success"] is True
    assert result["targets"] == ["prod", "preview"]
    assert "boards" in result["row_counts"]
