"""Task #321 — tests for the public ``/r2-watchdog-status`` route.

The route exposes ONLY the secondary "watchdog blind" liveness fields
from the Task #314 R2 cold-storage watchdog so the public on-call
status page can render the same indicator the admin dashboard already
shows. We assert both the happy-path field projection and the
"don't leak cost details" guarantee — IA share, total GB, Logpush GB,
bucket names must NOT appear in the public payload.
"""
import os
import pytest
from unittest.mock import patch


@pytest.fixture
def app_client_public():
    """No auth at all — the public route must work for unauthenticated
    visitors. We deliberately do NOT override get_admin_user; the route
    must not depend on it.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.admin_r2_storage_health import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_public_watchdog_status_requires_no_auth(app_client_public):
    """Even with no admin override, the public route should respond
    200 (configured: false fallback) instead of 401/403 — that's the
    whole point of mirroring it for on-call without admin creds."""
    with patch.dict(os.environ, {"D1_SYNC_SECRET": "", "CF_EDGE_PROXY_URL": ""},
                    clear=False):
        res = app_client_public.get("/r2-watchdog-status")
    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is False
    # The runbook URL + threshold default are still surfaced so the UI
    # can render a sensible tooltip even when the worker isn't wired up.
    assert "runbook_url" in body
    assert body["runbook_url"].endswith(
        "/cloudflare-monthly-cost-review.md#step-5"
    )
    assert body["query_fail_threshold"] == 2
    assert body["state"] is None


def test_public_watchdog_status_projects_only_blindness_fields(app_client_public):
    """The worker's snapshot includes IA share / total GB / Logpush GB /
    bucket names — none of those should leak through the public route.
    Only the watchdog liveness fields the indicator needs are returned."""
    fake = {
        "configured": True,
        "disabled": False,
        # NOTE: these cost-flavoured fields are intentionally present
        # in the upstream payload — the test asserts they are dropped.
        "buckets": ["syrabit-assets", "syrabit-media"],
        "logpush_cap_gb": 5,
        "rules_applied_at": "2026-01-01T00:00:00Z",
        "rules_age_days": 90,
        "query_fail_threshold": 3,
        "state": {
            "last_evaluated_at": "2026-04-01T00:00:00Z",
            "ia_share_last_fired_at": "2026-03-01T00:00:00Z",
            "logpush_last_fired_at": None,
            "last_ia_share": 0.42,
            "last_total_gb": 80.0,
            "last_logpush_gb": 1.2,
            "consecutive_query_failures": 1,
            "query_fail_last_fired_at": None,
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
            res = app_client_public.get("/r2-watchdog-status")

    assert res.status_code == 200
    body = res.json()

    # Hits the same upstream endpoint with the worker secret server-side.
    assert captured["url"].endswith("/api/edge/r2-storage-health")
    assert captured["headers"].get("X-Edge-Admin-Secret") == "topsecret"

    # Only the projected fields are present at the top level.
    assert set(body.keys()) == {
        "configured", "query_fail_threshold", "runbook_url", "state",
    }
    assert body["configured"] is True
    assert body["query_fail_threshold"] == 3

    # State is stripped down to the watchdog liveness signal.
    assert set(body["state"].keys()) == {
        "consecutive_query_failures",
        "query_fail_last_fired_at",
        "last_evaluated_at",
    }
    assert body["state"]["consecutive_query_failures"] == 1
    assert body["state"]["last_evaluated_at"] == "2026-04-01T00:00:00Z"

    # Cost / IA-share / Logpush GB / bucket details must NOT leak.
    forbidden = (
        "buckets", "logpush_cap_gb", "rules_applied_at", "rules_age_days",
        "disabled", "last_ia_share", "last_total_gb", "last_logpush_gb",
        "ia_share_last_fired_at", "logpush_last_fired_at",
    )
    flat = {**body, **(body.get("state") or {})}
    for key in forbidden:
        assert key not in flat, f"{key!r} leaked into public payload"


def test_public_watchdog_status_falls_back_when_threshold_missing(app_client_public):
    """An older worker may omit ``query_fail_threshold`` — the route
    should fall back to the documented default (2) so the indicator
    still renders against the right "tripped" boundary."""
    fake = {
        "configured": True,
        "state": {
            "last_evaluated_at": "2026-04-01T00:00:00Z",
            "consecutive_query_failures": 2,
            "query_fail_last_fired_at": "2026-04-01T00:00:00Z",
        },
    }

    class _FakeResp:
        status_code = 200
        def json(self): return fake

    class _FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return _FakeResp()

    with patch.dict(os.environ, {
            "D1_SYNC_SECRET": "topsecret",
            "CF_EDGE_PROXY_URL": "https://api.example.com"}, clear=False):
        with patch("routes.admin_r2_storage_health.httpx.AsyncClient", _FakeClient):
            res = app_client_public.get("/r2-watchdog-status")
    body = res.json()
    assert body["query_fail_threshold"] == 2
    assert body["state"]["consecutive_query_failures"] == 2


def test_public_watchdog_status_degrades_on_edge_outage(app_client_public):
    """A proxy outage must NOT bubble up as 5xx — the public status
    page would then turn the whole header red just because the
    watchdog endpoint is unreachable. Instead we degrade to
    ``configured: true, state: null`` so the indicator simply hides."""
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
            res = app_client_public.get("/r2-watchdog-status")
    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is True
    assert body["state"] is None
    assert "edge unreachable" in body["reason"]


def test_public_watchdog_status_degrades_on_edge_non_200(app_client_public):
    class _FakeResp:
        status_code = 500
        def json(self): return {"detail": "boom"}

    class _FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, *a, **k): return _FakeResp()

    with patch.dict(os.environ, {
            "D1_SYNC_SECRET": "topsecret",
            "CF_EDGE_PROXY_URL": "https://api.example.com"}, clear=False):
        with patch("routes.admin_r2_storage_health.httpx.AsyncClient", _FakeClient):
            res = app_client_public.get("/r2-watchdog-status")
    assert res.status_code == 200
    body = res.json()
    assert body["state"] is None
    assert "edge returned 500" in body["reason"]
