"""Task #33 / Task #47 — backend tests for GET and PATCH
/admin/edge/spa-title-miss-settings.

These routes proxy the edge worker's GET/PUT /api/edge/spa-title-miss-settings
endpoints so the admin dashboard can read and persist the SPA title-miss alert
threshold and on/off switch at runtime (without a wrangler redeploy).

Coverage:
  GET  /admin/edge/spa-title-miss-settings
    - auth rejection (unauthenticated admin session → 401/403)
    - configured: false when D1_SYNC_SECRET is absent
    - happy path: threshold, disabled, kv_override_set, env_threshold,
      env_disabled forwarded from edge worker
    - X-Edge-Admin-Secret header forwarded with correct value
    - edge returns 503 (RATE_LIMIT KV not bound) → 200 with fallback defaults
    - edge returns non-200/non-503 → 200 with reason
    - edge unreachable → 200 with reason

  PATCH /admin/edge/spa-title-miss-settings
    - auth rejection
    - 503 when D1_SYNC_SECRET is absent (PATCH is stricter than GET)
    - 400 for empty payload (no fields provided)
    - 422 when threshold < 1 (Pydantic ge=1 validation)
    - happy path: threshold + disabled forwarded to PUT, response returned
    - partial update: threshold only → disabled absent in forwarded payload
    - partial update: disabled only → threshold absent in forwarded payload
    - X-Edge-Admin-Secret header and JSON payload forwarded correctly
    - edge returns 503 → propagates 503
    - edge returns 400 → propagates 400
    - edge returns non-200/400/503 → 502
    - edge unreachable → 502
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


# ─── httpx stand-ins ─────────────────────────────────────────────────────────

class _FakeResp:
    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class _FakeClient:
    """Minimal async context manager supporting get() and put()."""

    def __init__(self,
                 get_resp: _FakeResp | None = None,
                 put_resp: _FakeResp | None = None):
        self._get_resp = get_resp or _FakeResp(200, {})
        self._put_resp = put_resp or _FakeResp(200, {})
        self.get_calls: list[dict] = []
        self.put_calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def get(self, url, *, params=None, headers=None):
        self.get_calls.append({"url": url, "params": params, "headers": headers})
        return self._get_resp

    async def put(self, url, *, json=None, headers=None):
        self.put_calls.append({"url": url, "json": json, "headers": headers})
        return self._put_resp


class _RaisingClient:
    """Stand-in that raises on get() and put() to simulate an unreachable worker."""

    def __init__(self, exc: Exception):
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def get(self, *_a, **_kw):
        raise self._exc

    async def put(self, *_a, **_kw):
        raise self._exc


_ENV = {"D1_SYNC_SECRET": "test-secret",
        "CF_EDGE_PROXY_URL": "https://api.example.com"}

# Canonical edge-worker GET response.
_SETTINGS_EDGE_RESP = {
    "threshold":       75,
    "disabled":        False,
    "kv_override_set": True,
    "env_threshold":   50,
    "env_disabled":    False,
}


# ─── GET /admin/edge/spa-title-miss-settings ─────────────────────────────────

def test_get_requires_admin_auth(unauth_client):
    res = unauth_client.get("/admin/edge/spa-title-miss-settings")
    assert res.status_code in (401, 403)


def test_get_configured_false_when_secret_missing(authed_client):
    """D1_SYNC_SECRET absent → configured: false (no network call made)."""
    with patch.dict(os.environ,
                    {"D1_SYNC_SECRET": "", "CF_EDGE_PROXY_URL": "https://x"},
                    clear=False):
        res = authed_client.get("/admin/edge/spa-title-miss-settings")
    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is False


def test_get_happy_path_returns_full_settings(authed_client):
    """Edge returns 200 — all fields must be forwarded to the caller."""
    fake = _FakeClient(get_resp=_FakeResp(200, _SETTINGS_EDGE_RESP))

    with patch.dict(os.environ, _ENV, clear=False), \
            patch("routes.admin_edge_analytics.httpx.AsyncClient",
                  return_value=fake):
        res = authed_client.get("/admin/edge/spa-title-miss-settings")

    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is True
    assert body["threshold"] == 75
    assert body["disabled"] is False
    assert body["kv_override_set"] is True
    assert body["env_threshold"] == 50
    assert body["env_disabled"] is False


def test_get_forwards_secret_in_header_to_correct_url(authed_client):
    """Proxy must attach X-Edge-Admin-Secret and target the correct edge path."""
    fake = _FakeClient(get_resp=_FakeResp(200, _SETTINGS_EDGE_RESP))

    with patch.dict(os.environ, _ENV, clear=False), \
            patch("routes.admin_edge_analytics.httpx.AsyncClient",
                  return_value=fake):
        authed_client.get("/admin/edge/spa-title-miss-settings")

    assert len(fake.get_calls) == 1
    call = fake.get_calls[0]
    assert call["url"].endswith("/api/edge/spa-title-miss-settings")
    assert call["headers"].get("X-Edge-Admin-Secret") == "test-secret"


def test_get_edge_503_surfaces_fallback_defaults(authed_client):
    """Edge 503 means RATE_LIMIT KV is not bound on the worker.
    Route must return 200 with configured:true, a human-readable reason, and
    safe fallback values so the UI can still render."""
    fake = _FakeClient(get_resp=_FakeResp(503, {}))

    with patch.dict(os.environ, _ENV, clear=False), \
            patch("routes.admin_edge_analytics.httpx.AsyncClient",
                  return_value=fake):
        res = authed_client.get("/admin/edge/spa-title-miss-settings")

    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is True
    assert "RATE_LIMIT" in (body.get("reason") or "")
    assert body.get("threshold") == 50
    assert body.get("disabled") is False
    assert body.get("kv_override_set") is False


def test_get_edge_unreachable_returns_200_with_reason(authed_client):
    """Network exceptions must not propagate as unhandled 500s."""
    fake = _RaisingClient(RuntimeError("connection refused"))

    with patch.dict(os.environ, _ENV, clear=False), \
            patch("routes.admin_edge_analytics.httpx.AsyncClient",
                  return_value=fake):
        res = authed_client.get("/admin/edge/spa-title-miss-settings")

    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is True
    assert "RuntimeError" in (body.get("reason") or "")


def test_get_edge_non_200_returns_reason_with_status_code(authed_client):
    """Any non-200/503 response from the edge must surface via reason."""
    fake = _FakeClient(get_resp=_FakeResp(502, {}))

    with patch.dict(os.environ, _ENV, clear=False), \
            patch("routes.admin_edge_analytics.httpx.AsyncClient",
                  return_value=fake):
        res = authed_client.get("/admin/edge/spa-title-miss-settings")

    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is True
    assert "502" in (body.get("reason") or "")


# ─── PATCH /admin/edge/spa-title-miss-settings ───────────────────────────────

def test_patch_requires_admin_auth(unauth_client):
    res = unauth_client.patch("/admin/edge/spa-title-miss-settings",
                              json={"threshold": 100})
    assert res.status_code in (401, 403)


def test_patch_returns_503_when_secret_missing(authed_client):
    """PATCH is stricter than GET: a missing secret raises 503 (not 200 configured:false)
    because the admin CMS cannot safely ignore a save failure."""
    with patch.dict(os.environ,
                    {"D1_SYNC_SECRET": "", "CF_EDGE_PROXY_URL": "https://x"},
                    clear=False):
        res = authed_client.patch("/admin/edge/spa-title-miss-settings",
                                  json={"threshold": 100})
    assert res.status_code == 503


def test_patch_returns_400_for_empty_payload(authed_client):
    """At least one of threshold or disabled must be provided."""
    with patch.dict(os.environ, _ENV, clear=False):
        res = authed_client.patch("/admin/edge/spa-title-miss-settings",
                                  json={})
    assert res.status_code == 400


def test_patch_returns_422_for_threshold_below_1(authed_client):
    """Pydantic Field(ge=1) rejects threshold=0 before the request reaches the
    route handler, so the response is 422 (Unprocessable Entity)."""
    with patch.dict(os.environ, _ENV, clear=False):
        res = authed_client.patch("/admin/edge/spa-title-miss-settings",
                                  json={"threshold": 0})
    assert res.status_code == 422


def test_patch_happy_path_returns_updated_settings(authed_client):
    """Edge returns 200 with ok:true — proxy must surface the response."""
    edge_resp = {"ok": True, "threshold": 100, "disabled": False}
    fake = _FakeClient(put_resp=_FakeResp(200, edge_resp))

    with patch.dict(os.environ, _ENV, clear=False), \
            patch("routes.admin_edge_analytics.httpx.AsyncClient",
                  return_value=fake):
        res = authed_client.patch("/admin/edge/spa-title-miss-settings",
                                  json={"threshold": 100, "disabled": False})

    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["threshold"] == 100
    assert body["disabled"] is False


def test_patch_forwards_secret_payload_and_url(authed_client):
    """Proxy must forward both fields to the edge PUT endpoint with the secret."""
    edge_resp = {"ok": True, "threshold": 80, "disabled": True}
    fake = _FakeClient(put_resp=_FakeResp(200, edge_resp))

    with patch.dict(os.environ, _ENV, clear=False), \
            patch("routes.admin_edge_analytics.httpx.AsyncClient",
                  return_value=fake):
        authed_client.patch("/admin/edge/spa-title-miss-settings",
                            json={"threshold": 80, "disabled": True})

    assert len(fake.put_calls) == 1
    call = fake.put_calls[0]
    assert call["url"].endswith("/api/edge/spa-title-miss-settings")
    assert call["headers"].get("X-Edge-Admin-Secret") == "test-secret"
    assert call["json"] == {"threshold": 80, "disabled": True}


def test_patch_threshold_only_omits_disabled_from_forwarded_payload(authed_client):
    """Only the provided fields should be included in the PUT payload so the
    edge worker can perform a partial merge without clobbering the other value."""
    edge_resp = {"ok": True, "threshold": 120, "disabled": False}
    fake = _FakeClient(put_resp=_FakeResp(200, edge_resp))

    with patch.dict(os.environ, _ENV, clear=False), \
            patch("routes.admin_edge_analytics.httpx.AsyncClient",
                  return_value=fake):
        authed_client.patch("/admin/edge/spa-title-miss-settings",
                            json={"threshold": 120})

    call = fake.put_calls[0]
    assert "threshold" in call["json"]
    assert "disabled" not in call["json"]


def test_patch_disabled_only_omits_threshold_from_forwarded_payload(authed_client):
    """Symmetrical test for the disabled-only partial update case."""
    edge_resp = {"ok": True, "threshold": 50, "disabled": True}
    fake = _FakeClient(put_resp=_FakeResp(200, edge_resp))

    with patch.dict(os.environ, _ENV, clear=False), \
            patch("routes.admin_edge_analytics.httpx.AsyncClient",
                  return_value=fake):
        authed_client.patch("/admin/edge/spa-title-miss-settings",
                            json={"disabled": True})

    call = fake.put_calls[0]
    assert "disabled" in call["json"]
    assert "threshold" not in call["json"]


def test_patch_edge_503_propagates_503(authed_client):
    """Edge 503 (RATE_LIMIT KV not bound) must surface as 503 — settings were
    not saved and the admin must know."""
    fake = _FakeClient(put_resp=_FakeResp(503, {"error": "KV not bound"}))

    with patch.dict(os.environ, _ENV, clear=False), \
            patch("routes.admin_edge_analytics.httpx.AsyncClient",
                  return_value=fake):
        res = authed_client.patch("/admin/edge/spa-title-miss-settings",
                                  json={"threshold": 100})

    assert res.status_code == 503


def test_patch_edge_400_propagates_400(authed_client):
    """Edge 400 (validation error) must be surfaced as 400 so the admin panel
    can display the specific validation message."""
    fake = _FakeClient(
        put_resp=_FakeResp(400, {"error": "threshold must be an integer ≥ 1"}),
    )

    with patch.dict(os.environ, _ENV, clear=False), \
            patch("routes.admin_edge_analytics.httpx.AsyncClient",
                  return_value=fake):
        res = authed_client.patch("/admin/edge/spa-title-miss-settings",
                                  json={"threshold": 100})

    assert res.status_code == 400


def test_patch_edge_502_returns_502(authed_client):
    """Any unexpected non-200/400/503 edge response becomes 502 (Bad Gateway)."""
    fake = _FakeClient(put_resp=_FakeResp(502, {}))

    with patch.dict(os.environ, _ENV, clear=False), \
            patch("routes.admin_edge_analytics.httpx.AsyncClient",
                  return_value=fake):
        res = authed_client.patch("/admin/edge/spa-title-miss-settings",
                                  json={"threshold": 100})

    assert res.status_code == 502


def test_patch_edge_unreachable_returns_502(authed_client):
    """Network exceptions during PATCH must not propagate as unhandled 500s."""
    fake = _RaisingClient(RuntimeError("timeout"))

    with patch.dict(os.environ, _ENV, clear=False), \
            patch("routes.admin_edge_analytics.httpx.AsyncClient",
                  return_value=fake):
        res = authed_client.patch("/admin/edge/spa-title-miss-settings",
                                  json={"threshold": 100})

    assert res.status_code == 502
