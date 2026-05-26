"""
Endpoints Audit Tests

Enumerate all registered routes and verify basic accessibility.
This catches import errors, missing dependencies, and broken route
registrations that would cause deployment failures (500 errors).
"""

import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from fastapi.routing import APIRoute


@pytest.fixture
def sync_client():
    """Synchronous TestClient for endpoint accessibility tests."""
    from app.main import app

    with patch("app.api.v1.auth._check_rate_limit", AsyncMock()):
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client


@pytest.fixture
def app_instance():
    """Get the FastAPI app instance."""
    from app.main import app

    return app


def test_all_routes_registered(app_instance):
    """Verify the app has routes registered (not empty)."""
    routes = [r for r in app_instance.routes if isinstance(r, APIRoute)]
    assert len(routes) > 10, f"Expected many routes to be registered, got {len(routes)}"


def test_health_endpoint_returns_200(sync_client):
    """Health endpoint must return 200."""
    response = sync_client.get("/health")
    assert response.status_code == 200


def test_health_deep_endpoint_accessible(sync_client):
    """Deep health endpoint should be accessible (not 500)."""
    response = sync_client.get("/health/deep")
    # May return non-200 if DB is not connected, but should not crash
    assert response.status_code != 500


def test_legacy_health_redirects(sync_client):
    """Legacy /api/health should redirect to /health."""
    response = sync_client.get("/api/health", follow_redirects=False)
    assert response.status_code == 301


def test_get_endpoints_no_500(sync_client, app_instance):
    """
    All GET endpoints should not return 500 (internal server error).
    401, 403, 404 are acceptable (auth required, not found, etc).
    This catches import errors and broken route handlers.
    """
    routes = [r for r in app_instance.routes if isinstance(r, APIRoute)]
    get_routes = []
    for route in routes:
        if "GET" in route.methods:
            # Use the first path (no path params filled)
            path = route.path
            # Skip paths with path parameters (would need valid IDs)
            if "{" in path:
                continue
            get_routes.append(path)

    assert len(get_routes) > 5, f"Expected multiple GET routes, got {len(get_routes)}"

    failures = []
    for path in get_routes:
        response = sync_client.get(path)
        if response.status_code == 500:
            failures.append(f"{path} returned 500: {response.text[:200]}")

    assert not failures, "The following endpoints returned 500:\n" + "\n".join(failures)


def test_chat_post_endpoint_exists(sync_client):
    """Chat POST endpoint should be accessible (not 500 on validation error)."""
    # Send without body - should get 422 (validation) or 403 (CSRF), not 500
    response = sync_client.post(
        "/api/v1/chat/",
        json={"message": ""},
        headers={"Origin": "https://syrabit.ai"},
    )
    # 422 (validation), 429 (rate limit), 401 (auth) are all acceptable
    assert response.status_code != 500


def test_route_methods_enumeration(app_instance):
    """Enumerate all routes and verify each has at least one HTTP method."""
    routes = [r for r in app_instance.routes if isinstance(r, APIRoute)]
    for route in routes:
        assert route.methods, f"Route {route.path} has no HTTP methods assigned"
        assert route.endpoint is not None, f"Route {route.path} has no endpoint handler"


def test_no_duplicate_routes(app_instance):
    """Verify there are no accidentally duplicated route paths+methods."""
    routes = [r for r in app_instance.routes if isinstance(r, APIRoute)]
    seen = set()
    duplicates = []
    for route in routes:
        for method in route.methods:
            key = f"{method} {route.path}"
            if key in seen:
                duplicates.append(key)
            seen.add(key)

    # Some duplication is acceptable (e.g., include_in_schema=False aliases)
    # but warn if many
    assert len(duplicates) < 5, f"Found many duplicate routes: {duplicates}"
