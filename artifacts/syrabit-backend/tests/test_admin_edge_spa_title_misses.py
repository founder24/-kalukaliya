"""Task #34 — backend tests for GET /admin/edge/spa-title-misses.

Stubs the httpx call so tests run offline.  Covers:
(a) successful proxy with misses — happy path
(b) successful proxy with empty misses list
(c) edge worker returning 503 (CF_ANALYTICS_TOKEN not set on worker)
(d) edge worker unreachable (network exception)

Plus guards for:
- missing env vars → configured: false
- invalid range → 400
- unauthenticated caller → 401/403
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest


# ─── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_admin():
    return {"id": "admin-1", "email": "ops@syrabit.ai", "is_admin": True,
            "sub": "admin-1"}


@pytest.fixture
def authed_client(mock_admin):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.admin_edge_analytics import router
    from auth_deps import get_admin_user

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides = {get_admin_user: lambda: mock_admin}
    return TestClient(app)


@pytest.fixture
def unauth_client():
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from routes.admin_edge_analytics import router
    from auth_deps import get_admin_user

    app = FastAPI()
    app.include_router(router)

    def _deny():
        raise HTTPException(status_code=401, detail="Not authenticated")

    app.dependency_overrides = {get_admin_user: _deny}
    return TestClient(app)


# ─── minimal async httpx stand-ins ───────────────────────────────────────────

class _FakeResp:
    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class _FakeClient:
    """Minimal stand-in for httpx.AsyncClient (async context manager + get)."""

    def __init__(self, resp: _FakeResp):
        self._resp = resp
        self.calls: list[dict] = []

    def __init_subclass__(cls, **kw):  # noqa: D105
        super().__init_subclass__(**kw)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def get(self, url, *, params=None, headers=None):
        self.calls.append({"url": url, "params": params, "headers": headers})
        return self._resp


class _RaisingClient(_FakeClient):
    """Stand-in that raises on get() to simulate an unreachable edge worker."""

    def __init__(self, exc: Exception):
        self._exc = exc

    async def get(self, *_a, **_kw):
        raise self._exc


_ENV = {"D1_SYNC_SECRET": "test-secret",
        "CF_EDGE_PROXY_URL": "https://api.example.com"}


# ─── auth ────────────────────────────────────────────────────────────────────

def test_requires_admin_auth(unauth_client):
    res = unauth_client.get("/admin/edge/spa-title-misses")
    assert res.status_code in (401, 403)


# ─── missing env vars → configured: false ────────────────────────────────────

def test_not_configured_when_secret_missing(authed_client):
    with patch.dict(os.environ,
                    {"D1_SYNC_SECRET": "", "CF_EDGE_PROXY_URL": "https://x"},
                    clear=False):
        res = authed_client.get("/admin/edge/spa-title-misses")
    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is False
    assert body["misses"] is None


def test_not_configured_when_both_secret_and_url_missing(authed_client):
    """Both D1_SYNC_SECRET and CF_EDGE_PROXY_URL missing → configured: false.
    Note: _edge_url() falls back to the hard-coded default when CF_EDGE_PROXY_URL
    is blank, so only an absent *secret* is sufficient to flip configured: false
    (the `not secret or not base` check).  This test covers the combined absence."""
    with patch.dict(os.environ,
                    {"D1_SYNC_SECRET": "", "CF_EDGE_PROXY_URL": ""},
                    clear=False):
        res = authed_client.get("/admin/edge/spa-title-misses")
    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is False
    assert body["misses"] is None


# ─── invalid range → 400 ─────────────────────────────────────────────────────

def test_invalid_range_returns_400(authed_client):
    res = authed_client.get("/admin/edge/spa-title-misses?range=99d")
    assert res.status_code == 400


# ─── shared enriched-object format (Task #32 / Task #39) ─────────────────────
# After Task #32 the edge worker returns an enriched object (not a flat array):
#   { range, threshold, alert_disabled, gaps_found,
#     gaps_above_threshold, gaps: [...], tag_handlers: {...} }
# After Task #39 tag_handlers is always present in the 200 response.

_TAG_HANDLERS = {
    "og_title": True, "og_description": True, "og_image": True,
    "og_image_alt": True, "twitter_title": True, "twitter_description": True,
    "twitter_card": True, "twitter_image": True, "twitter_image_alt": True,
}

def _enriched_edge_resp(gaps, *, alert_disabled=False):
    """Build a realistic enriched edge response (post-Task-#32 shape)."""
    return {
        "range":               "24h",
        "threshold":           50,
        "alert_disabled":      alert_disabled,
        "gaps_found":          len(gaps),
        "gaps_above_threshold": len(gaps),
        "gaps":                gaps,
        "tag_handlers":        _TAG_HANDLERS,
    }


# ─── (a) successful proxy with misses ────────────────────────────────────────

def test_happy_path_with_misses(authed_client):
    """Edge returns 200 with an enriched object (Task #32 shape) — the route
    must extract the ``gaps`` array into ``misses`` and surface ``tag_handlers``."""
    gaps = [
        {"pathname": "/learn/physics/chapter-1", "count": 42, "suggested_title": "Chapter 1"},
        {"pathname": "/learn/maths/chapter-2",   "count": 17, "suggested_title": "Chapter 2"},
    ]
    fake = _FakeClient(_FakeResp(200, _enriched_edge_resp(gaps)))

    with patch.dict(os.environ, _ENV, clear=False), \
            patch("routes.admin_edge_analytics.httpx.AsyncClient",
                  return_value=fake):
        res = authed_client.get("/admin/edge/spa-title-misses?range=24h")

    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is True
    assert body["misses"] == gaps
    # Task #39 — tag_handlers must be surfaced so the admin tile can show
    # which tags are being rewritten on matched routes.
    assert body["tag_handlers"]["twitter_image"] is True
    assert body["tag_handlers"]["twitter_image_alt"] is True
    assert body["tag_handlers"]["og_image"] is True
    assert body["tag_handlers"]["og_image_alt"] is True


def test_tag_handlers_present_even_when_no_gaps(authed_client):
    """tag_handlers must be returned even when gaps is empty so the admin
    tile can always show tag coverage status."""
    fake = _FakeClient(_FakeResp(200, _enriched_edge_resp([])))

    with patch.dict(os.environ, _ENV, clear=False), \
            patch("routes.admin_edge_analytics.httpx.AsyncClient",
                  return_value=fake):
        res = authed_client.get("/admin/edge/spa-title-misses")

    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is True
    assert body["misses"] == []
    assert isinstance(body.get("tag_handlers"), dict)
    assert body["tag_handlers"].get("twitter_image") is True


def test_legacy_flat_array_still_works(authed_client):
    """If an older worker returns a flat array (pre-Task-#32), the route must
    treat the array as the gaps list and return an empty tag_handlers dict."""
    legacy = [
        {"pathname": "/learn/old-path", "count": 5},
    ]
    fake = _FakeClient(_FakeResp(200, legacy))

    with patch.dict(os.environ, _ENV, clear=False), \
            patch("routes.admin_edge_analytics.httpx.AsyncClient",
                  return_value=fake):
        res = authed_client.get("/admin/edge/spa-title-misses?range=7d")

    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is True
    assert body["misses"] == legacy
    assert body.get("tag_handlers") == {}


def test_happy_path_forwards_range_param_and_secret(authed_client):
    """The edge URL, range query-param, and X-Edge-Admin-Secret header must all
    be forwarded so the worker can authenticate and filter by the correct window."""
    fake = _FakeClient(_FakeResp(200, _enriched_edge_resp([])))

    with patch.dict(os.environ, _ENV, clear=False), \
            patch("routes.admin_edge_analytics.httpx.AsyncClient",
                  return_value=fake):
        authed_client.get("/admin/edge/spa-title-misses?range=7d")

    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["url"].endswith("/api/edge/spa-title-misses")
    assert call["params"] == {"range": "7d"}
    assert call["headers"].get("X-Edge-Admin-Secret") == "test-secret"


# ─── (b) empty misses list ───────────────────────────────────────────────────

def test_happy_path_with_empty_misses(authed_client):
    """Edge returns 200 with empty gaps — route must surface ``misses: []``
    (not None) so the dashboard can distinguish "no data yet" from "not set up"."""
    fake = _FakeClient(_FakeResp(200, _enriched_edge_resp([])))

    with patch.dict(os.environ, _ENV, clear=False), \
            patch("routes.admin_edge_analytics.httpx.AsyncClient",
                  return_value=fake):
        res = authed_client.get("/admin/edge/spa-title-misses")

    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is True
    assert body["misses"] == []


# ─── (c) edge worker returns 503 (CF_ANALYTICS_TOKEN missing on worker) ──────

def test_edge_503_surfaces_configured_true_with_reason(authed_client):
    """A 503 from the edge worker means CF_ANALYTICS_TOKEN is not set there.
    The route must return 200 (not 503) with ``configured: true``, ``misses: None``,
    and a human-readable ``reason`` describing how to fix it."""
    fake = _FakeClient(_FakeResp(503, {}))

    with patch.dict(os.environ, _ENV, clear=False), \
            patch("routes.admin_edge_analytics.httpx.AsyncClient",
                  return_value=fake):
        res = authed_client.get("/admin/edge/spa-title-misses")

    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is True
    assert body["misses"] is None
    assert "CF_ANALYTICS_TOKEN" in (body.get("reason") or "")


# ─── (d) edge worker unreachable ─────────────────────────────────────────────

def test_edge_unreachable_returns_200_with_reason(authed_client):
    """A network-level exception (timeout, DNS, connection refused) must not
    propagate as an unhandled 500.  The route must absorb it and return 200
    with ``configured: true``, ``misses: None``, and a ``reason`` that names
    the exception type."""
    exc = RuntimeError("connection refused")
    fake = _RaisingClient(exc)

    with patch.dict(os.environ, _ENV, clear=False), \
            patch("routes.admin_edge_analytics.httpx.AsyncClient",
                  return_value=fake):
        res = authed_client.get("/admin/edge/spa-title-misses")

    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is True
    assert body["misses"] is None
    assert "RuntimeError" in (body.get("reason") or "")


def test_edge_timeout_returns_200_with_reason(authed_client):
    """An httpx.TimeoutException is also absorbed — the UI must never see a 500
    because the Analytics Engine is slow."""
    import httpx as _httpx

    exc = _httpx.TimeoutException("timed out")
    fake = _RaisingClient(exc)

    with patch.dict(os.environ, _ENV, clear=False), \
            patch("routes.admin_edge_analytics.httpx.AsyncClient",
                  return_value=fake):
        res = authed_client.get("/admin/edge/spa-title-misses")

    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is True
    assert body["misses"] is None
    assert "TimeoutException" in (body.get("reason") or "")


# ─── non-200 / non-503 edge responses ────────────────────────────────────────

def test_edge_502_surfaces_reason_with_status_code(authed_client):
    """Any non-200, non-503 edge response must not surface as an unhandled
    exception; the route must return 200 with a ``reason`` encoding the status
    code so operators know the upstream is degraded."""
    fake = _FakeClient(_FakeResp(502, {}))

    with patch.dict(os.environ, _ENV, clear=False), \
            patch("routes.admin_edge_analytics.httpx.AsyncClient",
                  return_value=fake):
        res = authed_client.get("/admin/edge/spa-title-misses")

    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is True
    assert body["misses"] is None
    assert "502" in (body.get("reason") or "")


# ─── valid range values (smoke) ───────────────────────────────────────────────

@pytest.mark.parametrize("range_val", ["1h", "6h", "24h", "7d"])
def test_all_valid_ranges_are_accepted(authed_client, range_val):
    fake = _FakeClient(_FakeResp(200, []))

    with patch.dict(os.environ, _ENV, clear=False), \
            patch("routes.admin_edge_analytics.httpx.AsyncClient",
                  return_value=fake):
        res = authed_client.get(f"/admin/edge/spa-title-misses?range={range_val}")

    assert res.status_code == 200
    assert res.json()["configured"] is True
