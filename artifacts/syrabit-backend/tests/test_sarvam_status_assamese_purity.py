"""Task #421 / #492 — /sarvam/status now returns HTTP 410 GONE.

The endpoint historically exposed the live `assamese_purity` block, but
V4 §15 (Task #492) retired the Sarvam admin HTTP surface. Live purity
config is now read from `GET /admin/assamese-purity` (auth-gated). This
test pins the replacement contract: 410 status + V4 §15 citation in the
JSON body.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _reset_runtime_override():
    from lang_sanitizer import clear_runtime_override
    clear_runtime_override()
    yield
    clear_runtime_override()


@pytest.fixture
def app_client():
    from routes.cms_sarvam_health import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_sarvam_status_returns_410_gone_with_v4_s15_citation(app_client):
    r = app_client.get("/sarvam/status")
    assert r.status_code == 410, (
        "Task #492 (V4 §15) retired /sarvam/status; expected 410 GONE"
    )
    body = r.json()
    detail = body.get("detail") or {}
    assert detail.get("error") == "gone"
    assert "V4 §15" in (detail.get("policy") or ""), (
        "410 body must cite V4 §15 so external integrators see the policy "
        "that retired the route"
    )
