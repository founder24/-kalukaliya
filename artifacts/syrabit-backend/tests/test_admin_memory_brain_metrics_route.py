"""Task #417 — admin route tests for /admin/memory-brain/metrics.

Pins the auth gate (admin-only) and the response payload shape so a
future refactor of the dashboard tile can rely on the contract.
"""
import os
import pytest


@pytest.fixture
def mock_admin():
    return {"id": "admin-1", "email": "ops@syrabit.ai", "is_admin": True,
            "sub": "admin-1"}


@pytest.fixture
def app_client_authed(mock_admin):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.admin_memory_brain_metrics import router
    app = FastAPI()
    app.include_router(router)
    from auth_deps import get_admin_user
    app.dependency_overrides = {get_admin_user: lambda: mock_admin}
    return TestClient(app)


@pytest.fixture
def app_client_no_auth():
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from routes.admin_memory_brain_metrics import router
    app = FastAPI()
    app.include_router(router)
    from auth_deps import get_admin_user

    def _deny():
        raise HTTPException(status_code=401, detail="Not authenticated")

    app.dependency_overrides = {get_admin_user: _deny}
    return TestClient(app)


@pytest.fixture(autouse=True)
def _isolate_metrics():
    import memory_brain_metrics as _m
    _m.reset()
    yield
    _m.reset()


def test_route_requires_admin_auth(app_client_no_auth):
    res = app_client_no_auth.get("/admin/memory-brain/metrics")
    assert res.status_code in (401, 403)


def test_route_returns_expected_shape_and_reflects_recorded_events(app_client_authed):
    import memory_brain_metrics as _m
    _m.record_event("write", kind="qa", ok=True)
    _m.record_event("write", kind="fact", ok=False, reason="voyage_error")
    _m.record_event("read",  kind="query", ok=True)

    res = app_client_authed.get("/admin/memory-brain/metrics")
    assert res.status_code == 200
    body = res.json()

    # Top-level shape contract the AdminMemoryBrainTile depends on.
    # Task #446 added fleet_stats / fleet_buckets alongside the per-
    # worker view; both are required so the frontend can offer the
    # scope toggle without conditional-key juggling.
    for key in ("ok", "stats", "buckets", "fleet_stats", "fleet_buckets",
                "worker_pid", "feature_enabled", "alert_threshold"):
        assert key in body, f"missing top-level key: {key}"
    # Fleet payload always carries the availability flag so the
    # frontend can disable the toggle when Upstash isn't wired
    # (e.g. in this test, where redis_client is None).
    assert "fleet_available" in body["fleet_stats"]
    assert body["fleet_stats"]["scope"] == "fleet"
    assert isinstance(body["fleet_buckets"], list) and len(body["fleet_buckets"]) == 24
    assert body["ok"] is True
    assert isinstance(body["worker_pid"], int)

    stats = body["stats"]
    assert stats["total"] == 3
    assert stats["failures"] == 1
    assert stats["by_op"]["write"] == {"ok": 1, "fail": 1, "total": 2}
    assert stats["by_op"]["read"]  == {"ok": 1, "fail": 0, "total": 1}
    reasons = {r["reason"] for r in stats["top_failure_reasons"]}
    assert "voyage_error" in reasons

    # Buckets default to 24 entries with the four counter keys.
    assert isinstance(body["buckets"], list) and len(body["buckets"]) == 24
    for b in body["buckets"]:
        assert {"hour_start_ts", "writes_ok", "writes_fail",
                "reads_ok", "reads_fail"} <= set(b.keys())

    # Banner threshold mirrors the live alert config so the tile turns
    # red at the same number that pages on-call.
    at = body["alert_threshold"]
    assert "failure_rate_pct" in at and "failure_min_sample" in at
    assert at["failure_rate_pct"] > 0


def test_route_window_param_caps_at_24h(app_client_authed):
    res = app_client_authed.get(
        "/admin/memory-brain/metrics?window_seconds=999999&hours=99"
    )
    # Pydantic Query validators reject out-of-bounds values.
    assert res.status_code == 422


def test_route_reports_feature_disabled_when_env_off(app_client_authed, monkeypatch):
    monkeypatch.setenv("MEMORY_BRAIN_CHAT_ENABLED", "0")
    res = app_client_authed.get("/admin/memory-brain/metrics")
    assert res.status_code == 200
    assert res.json()["feature_enabled"] is False
