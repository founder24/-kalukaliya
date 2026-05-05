"""Task #383 — unified ``/admin/cf-health`` route tests."""
from __future__ import annotations

import pytest


@pytest.fixture
def admin_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.admin_cf_health import router
    from auth_deps import get_admin_user

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_admin_user] = lambda: {
        "id": "admin-1", "email": "ops@syrabit.ai", "is_admin": True,
        "sub": "admin-1",
    }
    return TestClient(app)


@pytest.fixture
def unauth_client():
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from routes.admin_cf_health import router
    from auth_deps import get_admin_user

    app = FastAPI()
    app.include_router(router)

    def _deny():
        raise HTTPException(status_code=401, detail="Not authenticated")

    app.dependency_overrides[get_admin_user] = _deny
    return TestClient(app)


def test_route_requires_admin(unauth_client):
    res = unauth_client.get("/admin/cf-health")
    assert res.status_code in (401, 403)


def test_route_returns_all_workstream_blocks(admin_client):
    res = admin_client.get("/admin/cf-health")
    assert res.status_code == 200
    body = res.json()
    expected = {
        "flags", "ai_gateway", "vectorize_shadow", "r2", "kv_cache",
        "turnstile", "cf_web_analytics", "tunnel", "credit_burn",
    }
    assert expected.issubset(body.keys())


def test_flag_block_has_all_seven_flags(admin_client):
    res = admin_client.get("/admin/cf-health")
    flags = res.json()["flags"]
    for name in ("CF_AIGW_OBS_ON", "VECTORIZE_SHADOW_ON", "R2_PRIMARY_ON",
                 "CF_EDGE_CACHE_ON", "TURNSTILE_ON", "CF_WEB_ANALYTICS_ON",
                 "CF_TUNNEL_ONLY_ON", "GA4_ENABLED"):
        assert name in flags, f"missing flag {name}"
        assert isinstance(flags[name], bool)


def test_individual_failure_does_not_500(admin_client, monkeypatch):
    """If one workstream's snapshot raises, the route should still
    return 200 and surface ``error`` for that block — never a 500
    that knocks out every other panel."""
    def _boom():
        raise RuntimeError("kv-cache module broken")

    monkeypatch.setattr("routes.admin_cf_health._kv_cache_snapshot", _boom)
    res = admin_client.get("/admin/cf-health")
    assert res.status_code == 200
    body = res.json()
    assert "error" in body["kv_cache"]


def test_tunnel_block_lists_cidrs(admin_client):
    res = admin_client.get("/admin/cf-health")
    tunnel = res.json()["tunnel"]
    assert "allowed_cidrs" in tunnel
    assert tunnel["cidr_count"] == len(tunnel["allowed_cidrs"])
