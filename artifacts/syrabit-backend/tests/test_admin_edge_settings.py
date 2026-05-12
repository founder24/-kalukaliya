"""Task #76 — Snapshot test: GET /admin/edge/spa-title-miss-settings response keys.

The backend proxy at routes/admin_edge_analytics.py does::

    return {"configured": True, **resp.json()}

Because it spreads the raw edge worker JSON directly into the response, any
extra key the edge worker starts returning would silently appear in the
backend response without any schema gate.  This test pins the *exact* set of
keys that the GET endpoint must surface so the CI suite fails immediately if
a stray field is added at either the edge or the proxy layer.

Canonical key contract — 6 keys total:
  * ``configured``      — backend envelope key (always present, injected by the proxy)
  * ``disabled``        — edge payload: whether the nightly alert is paused
  * ``env_disabled``    — edge payload: env-var default for disabled
  * ``env_threshold``   — edge payload: env-var default for threshold
  * ``kv_override_set`` — edge payload: whether a KV override is currently active
  * ``threshold``       — edge payload: effective alert threshold (paths >= N trigger alert)

The task description lists five edge payload keys; ``configured`` is the sixth
key added by the backend proxy itself.  Both layers are covered here.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


_CANONICAL_KEYS = frozenset({
    "configured",
    "disabled",
    "env_disabled",
    "env_threshold",
    "kv_override_set",
    "threshold",
})

_EDGE_PAYLOAD = {
    "disabled": False,
    "env_disabled": False,
    "env_threshold": 50,
    "kv_override_set": False,
    "threshold": 50,
}


@pytest.fixture
def admin_client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.admin_edge_analytics import router
    from auth_deps import get_admin_user

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_admin_user] = lambda: {
        "id": "admin-1",
        "email": "ops@syrabit.ai",
        "is_admin": True,
        "sub": "admin-1",
    }
    return TestClient(app)


def _make_mock_response(status_code: int, payload: dict) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = payload
    return mock_resp


@pytest.fixture
def env_with_edge(monkeypatch):
    monkeypatch.setenv("CF_EDGE_PROXY_URL", "https://fake-edge.example.com")
    monkeypatch.setenv("D1_SYNC_SECRET", "test-secret-abc")


def test_get_settings_returns_exactly_canonical_keys(admin_client, env_with_edge):
    """The GET response must contain *exactly* the six canonical keys.

    If the edge worker or proxy layer adds any new field, this test fails
    immediately — preventing silent schema drift from reaching the frontend.
    """
    mock_resp = _make_mock_response(200, _EDGE_PAYLOAD)
    mock_async_client = MagicMock()
    mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
    mock_async_client.__aexit__ = AsyncMock(return_value=False)
    mock_async_client.get = AsyncMock(return_value=mock_resp)

    with patch("routes.admin_edge_analytics.httpx.AsyncClient", return_value=mock_async_client):
        res = admin_client.get("/admin/edge/spa-title-miss-settings")

    assert res.status_code == 200
    body = res.json()
    assert set(body.keys()) == _CANONICAL_KEYS, (
        f"GET /admin/edge/spa-title-miss-settings returned unexpected keys.\n"
        f"  got:      {sorted(body.keys())}\n"
        f"  expected: {sorted(_CANONICAL_KEYS)}\n"
        "If the edge worker started returning a new field, add it to "
        "_CANONICAL_KEYS in this test AND update the Task #75 edge snapshot "
        "so both sides stay in sync."
    )


def test_get_settings_canonical_values(admin_client, env_with_edge):
    """Each canonical field must have the correct type and a plausible value."""
    mock_resp = _make_mock_response(200, _EDGE_PAYLOAD)
    mock_async_client = MagicMock()
    mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
    mock_async_client.__aexit__ = AsyncMock(return_value=False)
    mock_async_client.get = AsyncMock(return_value=mock_resp)

    with patch("routes.admin_edge_analytics.httpx.AsyncClient", return_value=mock_async_client):
        res = admin_client.get("/admin/edge/spa-title-miss-settings")

    body = res.json()
    assert body["configured"] is True
    assert isinstance(body["disabled"], bool)
    assert isinstance(body["env_disabled"], bool)
    assert isinstance(body["env_threshold"], int) and body["env_threshold"] > 0
    assert isinstance(body["kv_override_set"], bool)
    assert isinstance(body["threshold"], int) and body["threshold"] > 0


def test_get_settings_extra_edge_field_is_caught():
    """The proxy must drop any unexpected field returned by the edge worker.

    The proxy now filters the edge JSON through an explicit allowlist of the
    five known keys (``disabled``, ``env_disabled``, ``env_threshold``,
    ``kv_override_set``, ``threshold``).  Any additional field the edge starts
    returning must be silently dropped so the frontend schema never drifts.
    """
    extra_payload = {**_EDGE_PAYLOAD, "surprise_field": "should_not_appear"}

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.admin_edge_analytics import router
    from auth_deps import get_admin_user

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_admin_user] = lambda: {
        "id": "admin-1", "email": "ops@syrabit.ai", "is_admin": True, "sub": "admin-1",
    }
    client = TestClient(app)

    mock_resp = _make_mock_response(200, extra_payload)
    mock_async_client = MagicMock()
    mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
    mock_async_client.__aexit__ = AsyncMock(return_value=False)
    mock_async_client.get = AsyncMock(return_value=mock_resp)

    with patch.dict(os.environ, {"CF_EDGE_PROXY_URL": "https://fake-edge.example.com",
                                  "D1_SYNC_SECRET": "test-secret"}):
        with patch("routes.admin_edge_analytics.httpx.AsyncClient", return_value=mock_async_client):
            res = client.get("/admin/edge/spa-title-miss-settings")

    body = res.json()
    assert "surprise_field" not in body, (
        "The proxy passed an unexpected edge field through to the frontend. "
        "The allowlist filter in routes/admin_edge_analytics.py must drop "
        "any key not in {disabled, env_disabled, env_threshold, kv_override_set, threshold}."
    )
    assert set(body.keys()) == _CANONICAL_KEYS, (
        f"Response keys do not match the canonical set after filtering.\n"
        f"  got:      {sorted(body.keys())}\n"
        f"  expected: {sorted(_CANONICAL_KEYS)}\n"
    )


def test_get_settings_not_configured_when_env_missing(admin_client):
    """Without CF_EDGE_PROXY_URL or D1_SYNC_SECRET the route returns
    ``configured: false`` and no canonical settings keys."""
    with patch.dict(os.environ, {"CF_EDGE_PROXY_URL": "", "D1_SYNC_SECRET": ""},
                    clear=False):
        res = admin_client.get("/admin/edge/spa-title-miss-settings")

    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is False
    for key in ("disabled", "env_disabled", "env_threshold", "kv_override_set", "threshold"):
        assert key not in body, (
            f"Key {key!r} must not appear in the unconfigured response; got: {body}"
        )


def test_get_settings_503_from_edge_returns_defaults(admin_client, env_with_edge):
    """When the edge worker returns 503 (KV not bound), the proxy synthesises
    default values and must still expose exactly the canonical key set."""
    mock_resp = _make_mock_response(503, {})
    mock_async_client = MagicMock()
    mock_async_client.__aenter__ = AsyncMock(return_value=mock_async_client)
    mock_async_client.__aexit__ = AsyncMock(return_value=False)
    mock_async_client.get = AsyncMock(return_value=mock_resp)

    with patch("routes.admin_edge_analytics.httpx.AsyncClient", return_value=mock_async_client):
        res = admin_client.get("/admin/edge/spa-title-miss-settings")

    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is True
    assert "threshold" in body
    assert "disabled" in body
    assert "kv_override_set" in body
