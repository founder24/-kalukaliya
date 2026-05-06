"""Task #476 — admin route tests for /admin/kv-health and /admin/kv-alerts."""
import os
import pytest
from unittest.mock import AsyncMock, patch


@pytest.fixture
def mock_admin():
    return {"id": "admin-1", "email": "ops@syrabit.ai", "is_admin": True,
            "sub": "admin-1"}


@pytest.fixture
def app_client_authed(mock_admin):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.admin_kv_health import router

    app = FastAPI()
    app.include_router(router)
    from auth_deps import get_admin_user
    app.dependency_overrides = {get_admin_user: lambda: mock_admin}
    return TestClient(app)


@pytest.fixture
def app_client_no_auth():
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from routes.admin_kv_health import router

    app = FastAPI()
    app.include_router(router)
    from auth_deps import get_admin_user

    def _deny():
        raise HTTPException(status_code=401, detail="Not authenticated")

    app.dependency_overrides = {get_admin_user: _deny}
    return TestClient(app)


# ─────────────── /admin/kv-health ───────────────

def test_kv_health_requires_admin_auth(app_client_no_auth):
    res = app_client_no_auth.get("/admin/kv-health")
    assert res.status_code in (401, 403)


def test_kv_health_returns_not_configured_when_secret_missing(app_client_authed):
    """When the worker secret isn't set the route should not blow up — it
    should return a clear ``configured: false`` payload so the dashboard
    can render a setup hint instead of an error."""
    with patch.dict(os.environ, {"D1_SYNC_SECRET": "", "CF_EDGE_PROXY_URL": ""},
                    clear=False):
        res = app_client_authed.get("/admin/kv-health")
    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is False
    assert body["snapshot"] is None


def test_kv_health_proxies_worker_snapshot(app_client_authed):
    """Happy path: route forwards the secret to the worker and returns
    its JSON snapshot under ``snapshot``."""
    fake_snapshot = {
        "utcDay": "2026-04-18",
        "warningPct": 80,
        "bindings": [
            {"binding": "RATE_LIMIT", "utcDay": "2026-04-18",
             "counters": {"read": 10, "write": 2, "list": 0, "delete": 0},
             "quota": {"read": 100000, "write": 1000, "list": 1000, "delete": 1000},
             "percentages": {"read": 0.0, "write": 0.2, "list": 0.0, "delete": 0.0},
             "status": "healthy", "fallbackActive": False},
        ],
    }
    captured = {}

    class _FakeResp:
        status_code = 200
        def json(self): return fake_snapshot

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
        with patch("routes.admin_kv_health.httpx.AsyncClient", _FakeClient):
            res = app_client_authed.get("/admin/kv-health")

    assert res.status_code == 200
    body = res.json()
    assert body == {"configured": True, "snapshot": fake_snapshot}
    assert captured["url"].endswith("/api/edge/kv-usage")
    # The shared secret must reach the worker via the agreed header so
    # the worker accepts the request.
    assert captured["headers"].get("X-Edge-Admin-Secret") == "topsecret"


def test_kv_health_merges_secondary_kv_cache_snapshot(app_client_authed):
    """Task #424 — when ``CF_EDGE_KV_CACHE_URL`` is wired, the route
    must fetch the secondary worker's `/api/edge/kv-usage` and merge its
    ``CF_EDGE_CACHE`` row into the primary snapshot's ``bindings`` list
    so the dashboard shows one unified panel."""
    primary_snapshot = {
        "utcDay": "2026-05-06",
        "warningPct": 80,
        "bindings": [
            {"binding": "RATE_LIMIT", "utcDay": "2026-05-06",
             "counters": {"read": 10, "write": 2, "list": 0, "delete": 0},
             "quota": {"read": 100000, "write": 1000, "list": 1000, "delete": 1000},
             "percentages": {"read": 0.0, "write": 0.2, "list": 0.0, "delete": 0.0},
             "status": "healthy", "fallbackActive": False},
        ],
    }
    secondary_snapshot = {
        "utcDay": "2026-05-06",
        "warningPct": 80,
        "bindings": [
            {"binding": "CF_EDGE_CACHE", "utcDay": "2026-05-06",
             "counters": {"read": 850, "write": 12, "list": 0, "delete": 1},
             "quota": {"read": 100000, "write": 1000, "list": 1000, "delete": 1000},
             "percentages": {"read": 0.9, "write": 1.2, "list": 0.0, "delete": 0.1},
             "status": "healthy", "fallbackActive": False},
        ],
    }
    captured_urls: list[str] = []

    class _FakeResp:
        def __init__(self, body): self._body = body; self.status_code = 200
        def json(self): return self._body

    class _FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, headers=None):
            captured_urls.append(url)
            if "kv-cache-edge" in url:
                return _FakeResp(secondary_snapshot)
            return _FakeResp(primary_snapshot)

    with patch.dict(os.environ, {
            "D1_SYNC_SECRET": "topsecret",
            "CF_EDGE_PROXY_URL": "https://api.example.com",
            "CF_EDGE_KV_CACHE_URL": "https://kv-cache-edge.example.com"},
            clear=False):
        with patch("routes.admin_kv_health.httpx.AsyncClient", _FakeClient):
            res = app_client_authed.get("/admin/kv-health")

    assert res.status_code == 200
    body = res.json()
    bindings = body["snapshot"]["bindings"]
    names = [b["binding"] for b in bindings]
    assert "RATE_LIMIT" in names
    assert "CF_EDGE_CACHE" in names
    cf = next(b for b in bindings if b["binding"] == "CF_EDGE_CACHE")
    assert cf["counters"]["read"] == 850
    # Both edge URLs were probed.
    assert any("api.example.com" in u for u in captured_urls)
    assert any("kv-cache-edge.example.com" in u for u in captured_urls)


def test_kv_health_secondary_failure_does_not_break_primary(app_client_authed):
    """Task #424 — the secondary fetch is best-effort. A 5xx / unreachable
    secondary worker must not blank out the primary RATE_LIMIT /
    BOT_HTML_CACHE rows the dashboard already depends on."""
    primary_snapshot = {
        "utcDay": "2026-05-06",
        "warningPct": 80,
        "bindings": [
            {"binding": "RATE_LIMIT", "utcDay": "2026-05-06",
             "counters": {"read": 10, "write": 2, "list": 0, "delete": 0},
             "quota": {"read": 100000, "write": 1000, "list": 1000, "delete": 1000},
             "percentages": {"read": 0.0, "write": 0.2, "list": 0.0, "delete": 0.0},
             "status": "healthy", "fallbackActive": False},
        ],
    }

    class _FakeResp:
        status_code = 200
        def json(self): return primary_snapshot

    class _FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, headers=None):
            if "kv-cache-edge" in url:
                raise RuntimeError("connection refused")
            return _FakeResp()

    with patch.dict(os.environ, {
            "D1_SYNC_SECRET": "topsecret",
            "CF_EDGE_PROXY_URL": "https://api.example.com",
            "CF_EDGE_KV_CACHE_URL": "https://kv-cache-edge.example.com"},
            clear=False):
        with patch("routes.admin_kv_health.httpx.AsyncClient", _FakeClient):
            res = app_client_authed.get("/admin/kv-health")

    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is True
    names = [b["binding"] for b in body["snapshot"]["bindings"]]
    assert names == ["RATE_LIMIT"]


def test_kv_health_skips_secondary_when_url_matches_primary(app_client_authed):
    """When the secondary URL equals the primary, no second probe must
    fire (the primary worker would already own both bindings) — avoids
    a redundant round-trip and a duplicate row in the merged snapshot."""
    primary_snapshot = {
        "utcDay": "2026-05-06",
        "warningPct": 80,
        "bindings": [
            {"binding": "CF_EDGE_CACHE", "utcDay": "2026-05-06",
             "counters": {"read": 5, "write": 1, "list": 0, "delete": 0},
             "quota": {"read": 100000, "write": 1000, "list": 1000, "delete": 1000},
             "percentages": {"read": 0.0, "write": 0.1, "list": 0.0, "delete": 0.0},
             "status": "healthy", "fallbackActive": False},
        ],
    }
    call_count = {"n": 0}

    class _FakeResp:
        status_code = 200
        def json(self): return primary_snapshot

    class _FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, headers=None):
            call_count["n"] += 1
            return _FakeResp()

    with patch.dict(os.environ, {
            "D1_SYNC_SECRET": "topsecret",
            "CF_EDGE_PROXY_URL": "https://api.example.com",
            "CF_EDGE_KV_CACHE_URL": "https://api.example.com"},
            clear=False):
        with patch("routes.admin_kv_health.httpx.AsyncClient", _FakeClient):
            res = app_client_authed.get("/admin/kv-health")

    assert res.status_code == 200
    assert call_count["n"] == 1


def test_kv_health_merge_does_not_overwrite_primary_binding(app_client_authed):
    """If a binding name appears in both snapshots, the primary wins —
    the worker that owns the binding is the source of truth for its
    counters."""
    primary_snapshot = {
        "utcDay": "2026-05-06",
        "warningPct": 80,
        "bindings": [
            {"binding": "CF_EDGE_CACHE", "utcDay": "2026-05-06",
             "counters": {"read": 999, "write": 9, "list": 0, "delete": 0},
             "quota": {"read": 100000, "write": 1000, "list": 1000, "delete": 1000},
             "percentages": {"read": 1.0, "write": 0.9, "list": 0.0, "delete": 0.0},
             "status": "healthy", "fallbackActive": False},
        ],
    }
    secondary_snapshot = {
        "utcDay": "2026-05-06",
        "warningPct": 80,
        "bindings": [
            {"binding": "CF_EDGE_CACHE", "utcDay": "2026-05-06",
             "counters": {"read": 1, "write": 1, "list": 0, "delete": 0},
             "quota": {"read": 100000, "write": 1000, "list": 1000, "delete": 1000},
             "percentages": {"read": 0.0, "write": 0.1, "list": 0.0, "delete": 0.0},
             "status": "healthy", "fallbackActive": False},
        ],
    }

    class _FakeResp:
        def __init__(self, body): self._body = body; self.status_code = 200
        def json(self): return self._body

    class _FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, headers=None):
            if "kv-cache-edge" in url:
                return _FakeResp(secondary_snapshot)
            return _FakeResp(primary_snapshot)

    with patch.dict(os.environ, {
            "D1_SYNC_SECRET": "topsecret",
            "CF_EDGE_PROXY_URL": "https://api.example.com",
            "CF_EDGE_KV_CACHE_URL": "https://kv-cache-edge.example.com"},
            clear=False):
        with patch("routes.admin_kv_health.httpx.AsyncClient", _FakeClient):
            res = app_client_authed.get("/admin/kv-health")

    assert res.status_code == 200
    bindings = res.json()["snapshot"]["bindings"]
    cfs = [b for b in bindings if b["binding"] == "CF_EDGE_CACHE"]
    assert len(cfs) == 1
    assert cfs[0]["counters"]["read"] == 999  # primary value preserved


def test_kv_health_handles_worker_error_gracefully(app_client_authed):
    """If the edge worker is unreachable the route must still respond
    200 with a structured ``reason`` so the UI can render a degraded
    state rather than crashing the prefs modal."""
    class _FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k):
            raise RuntimeError("connection refused")

    with patch.dict(os.environ, {
            "D1_SYNC_SECRET": "topsecret",
            "CF_EDGE_PROXY_URL": "https://api.example.com"}, clear=False):
        with patch("routes.admin_kv_health.httpx.AsyncClient", _FakeClient):
            res = app_client_authed.get("/admin/kv-health")
    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is True
    assert body["snapshot"] is None
    assert "edge unreachable" in body["reason"]


# ─────────────── /admin/kv-alerts ───────────────

def _alert_app():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.admin_kv_health import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_kv_alert_rejects_missing_secret():
    client = _alert_app()
    with patch.dict(os.environ, {"KV_ALERT_SECRET": "right-secret"}, clear=False):
        res = client.post("/admin/kv-alerts", json={"binding": "RATE_LIMIT"})
    assert res.status_code == 401


def test_kv_alert_rejects_wrong_secret():
    client = _alert_app()
    with patch.dict(os.environ, {"KV_ALERT_SECRET": "right-secret"}, clear=False):
        res = client.post(
            "/admin/kv-alerts",
            json={"binding": "RATE_LIMIT"},
            headers={"X-KV-Alert-Secret": "wrong-but-same-len-aaaa"},
        )
    assert res.status_code == 401


def test_kv_alert_503_when_secret_not_configured():
    """When ``KV_ALERT_SECRET`` is unset the endpoint must reject every
    request — silently accepting alerts would let any worker (or any
    third party) inject admin notifications."""
    client = _alert_app()
    with patch.dict(os.environ, {"KV_ALERT_SECRET": ""}, clear=False):
        res = client.post(
            "/admin/kv-alerts",
            json={"binding": "RATE_LIMIT"},
            headers={"X-KV-Alert-Secret": "anything"},
        )
    assert res.status_code == 503


def test_kv_alert_persists_admin_notification_on_valid_secret():
    """A correctly-authenticated alert must persist an admin notification
    so the dashboard inbox surfaces it; the kv_quota_alert metadata must
    be captured so we can deduplicate / inspect later."""
    client = _alert_app()
    persisted = []

    async def _fake_insert(notif):
        persisted.append(notif)

    payload = {
        "binding": "RATE_LIMIT",
        "op": "write",
        "used": 850,
        "quota": 1000,
        "percentage": 85.0,
        "severity": "warning",
        "utc_day": "2026-04-18",
    }
    with patch.dict(os.environ, {"KV_ALERT_SECRET": "shh"}, clear=False):
        with patch("routes.admin_kv_health.supa_insert_notification",
                   AsyncMock(side_effect=_fake_insert)):
            with patch("routes.admin_kv_health._email_admins_about_kv_alert",
                       AsyncMock(return_value=None)):
                res = client.post(
                    "/admin/kv-alerts",
                    json=payload,
                    headers={"X-KV-Alert-Secret": "shh"},
                )

    assert res.status_code == 200
    assert res.json().get("ok") is True
    assert len(persisted) == 1
    n = persisted[0]
    assert n["title"].startswith("KV warning: RATE_LIMIT.write")
    assert "85" in n["title"]
    assert n["audience"] == "admins"
    assert n["status"] == "sent"
    assert n["meta"]["kind"] == "kv_quota_alert"
    assert n["meta"]["binding"] == "RATE_LIMIT"
    assert n["meta"]["op"] == "write"
    assert n["meta"]["severity"] == "warning"


def test_kv_alert_marks_exhausted_severity_as_error_type():
    """An ``exhausted`` alert (>=100% of quota) must surface as type
    ``error`` so the admin inbox renders it with the highest urgency."""
    client = _alert_app()
    persisted = []

    async def _fake_insert(notif):
        persisted.append(notif)

    payload = {
        "binding": "BOT_HTML_CACHE",
        "op": "read",
        "used": 100_500,
        "quota": 100_000,
        "percentage": 100.5,
        "severity": "exhausted",
        "utc_day": "2026-04-18",
    }
    with patch.dict(os.environ, {"KV_ALERT_SECRET": "shh"}, clear=False):
        with patch("routes.admin_kv_health.supa_insert_notification",
                   AsyncMock(side_effect=_fake_insert)):
            with patch("routes.admin_kv_health._email_admins_about_kv_alert",
                       AsyncMock(return_value=None)):
                res = client.post(
                    "/admin/kv-alerts",
                    json=payload,
                    headers={"X-KV-Alert-Secret": "shh"},
                )
    assert res.status_code == 200
    assert persisted[0]["type"] == "error"
    assert persisted[0]["meta"]["severity"] == "exhausted"
