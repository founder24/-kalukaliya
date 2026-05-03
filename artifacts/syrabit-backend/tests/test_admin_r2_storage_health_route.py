"""Task #315 — admin route tests for /admin/r2-storage-health."""
import os
import pytest
from unittest.mock import patch


@pytest.fixture
def mock_admin():
    return {"id": "admin-1", "email": "ops@syrabit.ai", "is_admin": True,
            "sub": "admin-1"}


@pytest.fixture
def app_client_authed(mock_admin):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.admin_r2_storage_health import router

    app = FastAPI()
    app.include_router(router)
    from auth_deps import get_admin_user
    app.dependency_overrides = {get_admin_user: lambda: mock_admin}
    return TestClient(app)


@pytest.fixture
def app_client_no_auth():
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from routes.admin_r2_storage_health import router

    app = FastAPI()
    app.include_router(router)
    from auth_deps import get_admin_user

    def _deny():
        raise HTTPException(status_code=401, detail="Not authenticated")

    app.dependency_overrides = {get_admin_user: _deny}
    return TestClient(app)


def test_r2_health_requires_admin_auth(app_client_no_auth):
    res = app_client_no_auth.get("/admin/r2-storage-health")
    assert res.status_code in (401, 403)


def test_r2_health_returns_not_configured_when_secret_missing(app_client_authed):
    """When the worker secret isn't set the route should not blow up — it
    should return a clear ``configured: false`` payload so the dashboard
    can render a setup hint instead of an error."""
    with patch.dict(os.environ, {"D1_SYNC_SECRET": "", "CF_EDGE_PROXY_URL": ""},
                    clear=False):
        res = app_client_authed.get("/admin/r2-storage-health")
    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is False
    assert body["state"] is None


def test_r2_health_proxies_worker_snapshot(app_client_authed):
    fake = {
        "configured": True,
        "disabled": False,
        "buckets": ["syrabit-assets", "syrabit-media"],
        "logpush_cap_gb": 5,
        "rules_applied_at": "2026-01-01T00:00:00Z",
        "rules_age_days": 90,
        "state": {
            "last_evaluated_at": "2026-04-01T00:00:00Z",
            "ia_share_last_fired_at": None,
            "logpush_last_fired_at": None,
            "last_ia_share": 0.42,
            "last_total_gb": 80.0,
            "last_logpush_gb": 1.2,
        },
    }
    captured = {}

    class _FakeResp:
        status_code = 200
        def json(self): return fake

    class _FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, headers=None):
            captured["url"] = url
            captured["headers"] = headers or {}
            return _FakeResp()

    with patch.dict(os.environ, {
            "D1_SYNC_SECRET": "topsecret",
            "CF_EDGE_PROXY_URL": "https://api.example.com"}, clear=False):
        with patch("routes.admin_r2_storage_health.httpx.AsyncClient", _FakeClient):
            res = app_client_authed.get("/admin/r2-storage-health")

    assert res.status_code == 200
    assert res.json() == fake
    assert captured["url"].endswith("/api/edge/r2-storage-health")
    assert captured["headers"].get("X-Edge-Admin-Secret") == "topsecret"


def test_r2_health_handles_worker_error_gracefully(app_client_authed):
    class _FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k):
            raise RuntimeError("connection refused")

    with patch.dict(os.environ, {
            "D1_SYNC_SECRET": "topsecret",
            "CF_EDGE_PROXY_URL": "https://api.example.com"}, clear=False):
        with patch("routes.admin_r2_storage_health.httpx.AsyncClient", _FakeClient):
            res = app_client_authed.get("/admin/r2-storage-health")
    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is True
    assert body["state"] is None
    assert "edge unreachable" in body["reason"]


def test_r2_health_run_proxies_post(app_client_authed):
    captured = {}
    payload = {
        "ok": True,
        "result": {
            "ok": True, "skipped": False,
            "ia_share": 0.4, "total_gb": 80, "standard_gb": 48,
            "infrequent_access_gb": 32,
            "logpush_gb": 1.5, "logpush_cap_gb": 5,
            "ia_alert_fired": False, "logpush_alert_fired": False,
            "rules_age_days": 90,
        },
        "state": {
            "last_evaluated_at": "2026-04-15T10:00:00Z",
            "ia_share_last_fired_at": None, "logpush_last_fired_at": None,
            "last_ia_share": 0.4, "last_total_gb": 80, "last_logpush_gb": 1.5,
        },
    }

    class _FakeResp:
        status_code = 200
        def json(self): return payload

    class _FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None):
            captured["url"] = url
            captured["headers"] = headers or {}
            return _FakeResp()

    with patch.dict(os.environ, {
            "D1_SYNC_SECRET": "topsecret",
            "CF_EDGE_PROXY_URL": "https://api.example.com"}, clear=False):
        with patch("routes.admin_r2_storage_health.httpx.AsyncClient", _FakeClient):
            res = app_client_authed.post("/admin/r2-storage-health/run")

    assert res.status_code == 200
    assert res.json() == payload
    assert captured["url"].endswith("/api/edge/r2-storage-health/run")
    assert captured["headers"].get("X-Edge-Admin-Secret") == "topsecret"


def test_r2_health_run_propagates_cooldown_429(app_client_authed):
    """The worker enforces a short cooldown — if it returns 429 the
    backend must propagate it so the UI can show a "try again later"
    toast instead of treating it as a generic failure."""
    class _FakeResp:
        status_code = 429
        text = '{"ok":false,"reason":"cooldown","retry_after_seconds":42}'
        def json(self):
            return {"ok": False, "reason": "cooldown", "retry_after_seconds": 42}

    class _FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): return _FakeResp()

    with patch.dict(os.environ, {
            "D1_SYNC_SECRET": "topsecret",
            "CF_EDGE_PROXY_URL": "https://api.example.com"}, clear=False):
        with patch("routes.admin_r2_storage_health.httpx.AsyncClient", _FakeClient):
            res = app_client_authed.post("/admin/r2-storage-health/run")
    assert res.status_code == 429
    detail = res.json()["detail"]
    assert detail["reason"] == "cooldown"
    assert detail["retry_after_seconds"] == 42


def test_r2_health_run_503_when_secret_missing(app_client_authed):
    with patch.dict(os.environ, {"D1_SYNC_SECRET": "", "CF_EDGE_PROXY_URL": ""},
                    clear=False):
        res = app_client_authed.post("/admin/r2-storage-health/run")
    assert res.status_code == 503
