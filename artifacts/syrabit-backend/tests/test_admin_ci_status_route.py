"""Task #470 — admin route tests for /admin/ci-status and /admin/ci-rerun."""
import os
import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture
def mock_admin():
    return {"id": "admin-1", "email": "ops@syrabit.ai", "is_admin": True,
            "sub": "admin-1"}


@pytest.fixture
def app_client_authed(mock_admin):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.admin_ci_status import router
    from auth_deps import get_admin_user

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides = {get_admin_user: lambda: mock_admin}
    return TestClient(app)


@pytest.fixture
def app_client_no_auth():
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from routes.admin_ci_status import router
    from auth_deps import get_admin_user

    app = FastAPI()
    app.include_router(router)

    def _deny():
        raise HTTPException(status_code=401, detail="Not authenticated")

    app.dependency_overrides = {get_admin_user: _deny}
    return TestClient(app)


def test_ci_status_requires_admin_auth(app_client_no_auth):
    res = app_client_no_auth.get("/admin/ci-status")
    assert res.status_code in (401, 403)


def test_ci_status_returns_not_configured_when_repo_missing(app_client_authed):
    """When GITHUB_REPO isn't set the route must not blow up — it should
    return a clear ``configured: false`` payload so the dashboard can
    render a setup hint."""
    with patch.dict(os.environ, {"GITHUB_REPO": ""}, clear=False):
        res = app_client_authed.get("/admin/ci-status")
    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is False
    assert body["runs"] == {}


def _mock_response(status_code: int, json_payload):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_payload)
    return resp


class _FakeAsyncClient:
    """Minimal stand-in for httpx.AsyncClient supporting the async context
    manager protocol + ``get`` / ``post``."""

    def __init__(self, responses):
        # responses is a list consumed in order across get + post calls.
        self._responses = list(responses)
        self.calls = []          # (method, url) tuples
        self.post_bodies = []    # raw content bytes passed to post()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url):
        self.calls.append(("GET", url))
        return self._responses.pop(0)

    async def post(self, url, *, content=b""):
        self.calls.append(("POST", url))
        self.post_bodies.append(content)
        return self._responses.pop(0)


def test_ci_status_returns_latest_run_per_workflow(app_client_authed):
    """Happy path: GitHub returns a successful run for both workflows.
    The response should expose ``conclusion=success`` for both with a
    branch-pinned URL filter so feature-branch runs can't poison the
    badge."""
    backend_run = {
        "id": 999, "name": "backend-tests", "status": "completed",
        "conclusion": "success",
        "html_url": "https://github.com/x/y/actions/runs/999",
        "head_branch": "main", "head_sha": "abcdef1234567",
        "event": "push", "run_number": 42,
        "created_at": "2026-04-18T10:00:00Z",
        "updated_at": "2026-04-18T10:05:00Z",
        "actor": {"login": "ci-bot"},
    }
    frontend_run = dict(backend_run, id=1000, name="frontend-tests")
    fake = _FakeAsyncClient([
        _mock_response(200, {"workflow_runs": [backend_run]}),
        _mock_response(200, {"workflow_runs": [frontend_run]}),
    ])
    with patch.dict(os.environ, {"GITHUB_REPO": "x/y"}, clear=False), \
            patch("routes.admin_ci_status.httpx.AsyncClient",
                  return_value=fake):
        res = app_client_authed.get("/admin/ci-status")
    assert res.status_code == 200
    body = res.json()
    assert body["configured"] is True
    assert body["repo"] == "x/y"
    assert body["branch"] == "main"
    assert body["error"] is None
    assert set(body["runs"].keys()) == {"backend-tests.yml", "frontend-tests.yml"}
    be = body["runs"]["backend-tests.yml"]
    assert be["conclusion"] == "success"
    assert be["head_sha"] == "abcdef1"  # truncated to 7
    assert be["age_seconds"] is not None
    # Both API calls must be branch-pinned to avoid feature-branch leaks.
    for _method, url in fake.calls:
        assert "branch=main" in url
        assert "per_page=1" in url


def test_ci_status_handles_failure_conclusion(app_client_authed):
    """Red CI must surface as ``conclusion=failure`` so the dashboard
    can render a red pill — that's the whole point of the gate."""
    failed = {
        "id": 1, "name": "backend-tests", "status": "completed",
        "conclusion": "failure",
        "html_url": "https://github.com/x/y/actions/runs/1",
        "head_branch": "main", "head_sha": "deadbee0000",
        "event": "push", "run_number": 7,
        "created_at": "2026-04-18T09:00:00Z",
        "updated_at": "2026-04-18T09:02:00Z",
        "actor": {"login": "dev"},
    }
    fake = _FakeAsyncClient([
        _mock_response(200, {"workflow_runs": [failed]}),
        _mock_response(200, {"workflow_runs": []}),  # frontend missing
    ])
    with patch.dict(os.environ, {"GITHUB_REPO": "x/y"}, clear=False), \
            patch("routes.admin_ci_status.httpx.AsyncClient",
                  return_value=fake):
        res = app_client_authed.get("/admin/ci-status")
    body = res.json()
    assert body["runs"]["backend-tests.yml"]["conclusion"] == "failure"
    assert body["runs"]["frontend-tests.yml"] is None  # no runs yet


def test_ci_status_handles_github_error(app_client_authed):
    """When GitHub returns a non-200 the route must not blow up; it
    should return ``error`` so the UI can show "CI status temporarily
    unavailable"."""
    fake = _FakeAsyncClient([
        _mock_response(503, {}),
        _mock_response(503, {}),
    ])
    with patch.dict(os.environ, {"GITHUB_REPO": "x/y"}, clear=False), \
            patch("routes.admin_ci_status.httpx.AsyncClient",
                  return_value=fake):
        res = app_client_authed.get("/admin/ci-status")
    body = res.json()
    assert body["configured"] is True
    assert body["error"] == "github returned 503"
    assert body["runs"]["backend-tests.yml"] is None


def test_ci_status_handles_network_exception(app_client_authed):
    """Network-level failures (DNS, timeout) must also be soft-handled."""
    class BrokenClient(_FakeAsyncClient):
        async def get(self, url):  # noqa: D401
            raise RuntimeError("boom")

    fake = BrokenClient([])
    with patch.dict(os.environ, {"GITHUB_REPO": "x/y"}, clear=False), \
            patch("routes.admin_ci_status.httpx.AsyncClient",
                  return_value=fake):
        res = app_client_authed.get("/admin/ci-status")
    body = res.json()
    assert body["configured"] is True
    assert "github unreachable" in (body["error"] or "")
    assert body["runs"]["backend-tests.yml"] is None


def test_ci_status_uses_token_when_available(app_client_authed):
    """When GITHUB_TOKEN is set the Authorization header must be sent so
    private-repo callers get authenticated quota."""
    captured_headers: dict = {}
    backend_run = {
        "id": 1, "name": "x", "status": "completed", "conclusion": "success",
        "html_url": "u", "head_branch": "main", "head_sha": "abc1234",
        "event": "push", "run_number": 1,
        "created_at": "2026-04-18T10:00:00Z",
        "updated_at": "2026-04-18T10:00:00Z",
        "actor": {"login": "x"},
    }
    fake = _FakeAsyncClient([
        _mock_response(200, {"workflow_runs": [backend_run]}),
        _mock_response(200, {"workflow_runs": [backend_run]}),
    ])

    def _factory(*args, **kwargs):
        captured_headers.update(kwargs.get("headers") or {})
        return fake

    with patch.dict(os.environ,
                    {"GITHUB_REPO": "x/y", "GITHUB_TOKEN": "ghp_secret"},
                    clear=False), \
            patch("routes.admin_ci_status.httpx.AsyncClient", _factory):
        res = app_client_authed.get("/admin/ci-status")
    assert res.status_code == 200
    assert captured_headers.get("Authorization") == "Bearer ghp_secret"


def test_ci_status_respects_workflow_and_branch_overrides(app_client_authed):
    """Operators must be able to override the workflow filename and
    branch via env vars without code changes."""
    backend_run = {
        "id": 1, "name": "ci", "status": "completed", "conclusion": "success",
        "html_url": "u", "head_branch": "release", "head_sha": "abc1234",
        "event": "push", "run_number": 1,
        "created_at": "2026-04-18T10:00:00Z",
        "updated_at": "2026-04-18T10:00:00Z",
        "actor": {"login": "x"},
    }
    fake = _FakeAsyncClient([
        _mock_response(200, {"workflow_runs": [backend_run]}),
        _mock_response(200, {"workflow_runs": []}),
    ])
    with patch.dict(os.environ, {
        "GITHUB_REPO": "x/y",
        "GITHUB_CI_WORKFLOW": "ci.yml",
        "GITHUB_CI_BRANCH": "release",
    }, clear=False), patch(
        "routes.admin_ci_status.httpx.AsyncClient", return_value=fake
    ):
        res = app_client_authed.get("/admin/ci-status")
    body = res.json()
    assert body["branch"] == "release"
    assert "ci.yml" in body["runs"]
    assert any("branch=release" in url and "ci.yml" in url for _m, url in fake.calls)


# ---------------------------------------------------------------------------
# POST /admin/ci-rerun tests
# ---------------------------------------------------------------------------

def test_ci_rerun_requires_admin_auth(app_client_no_auth):
    """The re-run endpoint must reject unauthenticated callers."""
    res = app_client_no_auth.post(
        "/admin/ci-rerun",
        json={"run_id": 1},
    )
    assert res.status_code in (401, 403)


def test_ci_rerun_returns_503_when_repo_not_configured(app_client_authed):
    """When GITHUB_REPO is absent the route must fail with 503 rather than
    silently accepting the request and sending it to the wrong repo."""
    with patch.dict(os.environ, {"GITHUB_REPO": "", "GITHUB_TOKEN": "tok"},
                    clear=False):
        res = app_client_authed.post("/admin/ci-rerun", json={"run_id": 99})
    assert res.status_code == 503
    assert "GITHUB_REPO" in res.json()["detail"]


def test_ci_rerun_returns_503_when_token_not_configured(app_client_authed):
    """Without GITHUB_TOKEN the route must fail with 503 — there is no
    point forwarding the request because GitHub would reject it anyway."""
    with patch.dict(os.environ, {"GITHUB_REPO": "x/y", "GITHUB_TOKEN": ""},
                    clear=False):
        res = app_client_authed.post("/admin/ci-rerun", json={"run_id": 99})
    assert res.status_code == 503
    assert "GITHUB_TOKEN" in res.json()["detail"]


def test_ci_rerun_happy_path_failed_only(app_client_authed):
    """failed_only=True (default) must POST to .../rerun-failed-jobs and
    return {queued: true, run_id: <id>, failed_only: true}."""
    fake = _FakeAsyncClient([_mock_response(201, {})])
    with patch.dict(os.environ,
                    {"GITHUB_REPO": "x/y", "GITHUB_TOKEN": "ghp_tok"},
                    clear=False), \
            patch("routes.admin_ci_status.httpx.AsyncClient", return_value=fake):
        res = app_client_authed.post(
            "/admin/ci-rerun",
            json={"run_id": 12345, "failed_only": True},
        )
    assert res.status_code == 200
    body = res.json()
    assert body["queued"] is True
    assert body["run_id"] == 12345
    assert body["failed_only"] is True
    # Must have hit the failed-jobs path, not the all-jobs path.
    assert len(fake.calls) == 1
    _method, url = fake.calls[0]
    assert _method == "POST"
    assert "rerun-failed-jobs" in url
    assert "12345" in url


def test_ci_rerun_happy_path_all_jobs(app_client_authed):
    """failed_only=False must POST to .../rerun (re-run all jobs) and
    return {failed_only: false}."""
    fake = _FakeAsyncClient([_mock_response(201, {})])
    with patch.dict(os.environ,
                    {"GITHUB_REPO": "x/y", "GITHUB_TOKEN": "ghp_tok"},
                    clear=False), \
            patch("routes.admin_ci_status.httpx.AsyncClient", return_value=fake):
        res = app_client_authed.post(
            "/admin/ci-rerun",
            json={"run_id": 99, "failed_only": False},
        )
    assert res.status_code == 200
    body = res.json()
    assert body["queued"] is True
    assert body["failed_only"] is False
    _method, url = fake.calls[0]
    # Must use the "rerun" path, NOT "rerun-failed-jobs".
    assert url.endswith("/rerun")
    assert "rerun-failed-jobs" not in url


def test_ci_rerun_forwards_github_403_as_502(app_client_authed):
    """A 403 from GitHub (token lacks actions:write) must be surfaced as
    502 — not 200 — so the frontend can display the error detail."""
    error_body = {"message": "Resource not accessible by integration"}
    fake = _FakeAsyncClient([_mock_response(403, error_body)])
    resp_mock = fake._responses[0]
    resp_mock.text = "Resource not accessible by integration"

    with patch.dict(os.environ,
                    {"GITHUB_REPO": "x/y", "GITHUB_TOKEN": "ghp_tok"},
                    clear=False), \
            patch("routes.admin_ci_status.httpx.AsyncClient", return_value=fake):
        res = app_client_authed.post("/admin/ci-rerun", json={"run_id": 1})
    assert res.status_code == 502
    detail = res.json()["detail"]
    assert "403" in detail


def test_ci_rerun_forwards_github_500_as_502(app_client_authed):
    """GitHub 5xx responses must also be surfaced as 502."""
    fake = _FakeAsyncClient([_mock_response(500, {})])
    resp_mock = fake._responses[0]
    resp_mock.text = "Internal Server Error"

    with patch.dict(os.environ,
                    {"GITHUB_REPO": "x/y", "GITHUB_TOKEN": "ghp_tok"},
                    clear=False), \
            patch("routes.admin_ci_status.httpx.AsyncClient", return_value=fake):
        res = app_client_authed.post("/admin/ci-rerun", json={"run_id": 7})
    assert res.status_code == 502
    assert "500" in res.json()["detail"]


def test_ci_rerun_network_exception_raises_502(app_client_authed):
    """A network-level failure must raise 502 — not propagate as an
    unhandled 500 — so the dashboard error message is actionable."""
    class _BrokenClient(_FakeAsyncClient):
        async def post(self, url, **_kw):
            raise RuntimeError("connect timeout")

    fake = _BrokenClient([])
    with patch.dict(os.environ,
                    {"GITHUB_REPO": "x/y", "GITHUB_TOKEN": "ghp_tok"},
                    clear=False), \
            patch("routes.admin_ci_status.httpx.AsyncClient", return_value=fake):
        res = app_client_authed.post("/admin/ci-rerun", json={"run_id": 5})
    assert res.status_code == 502
    assert "GitHub unreachable" in res.json()["detail"]


def test_ci_rerun_sends_authorization_header(app_client_authed):
    """The re-run POST must include Authorization: Bearer <token> so
    private-repo callers are authenticated with actions:write scope."""
    captured: dict = {}
    fake = _FakeAsyncClient([_mock_response(201, {})])

    def _factory(*args, **kwargs):
        captured.update(kwargs.get("headers") or {})
        return fake

    with patch.dict(os.environ,
                    {"GITHUB_REPO": "x/y", "GITHUB_TOKEN": "ghp_write_tok"},
                    clear=False), \
            patch("routes.admin_ci_status.httpx.AsyncClient", _factory):
        res = app_client_authed.post("/admin/ci-rerun", json={"run_id": 1})
    assert res.status_code == 200
    assert captured.get("Authorization") == "Bearer ghp_write_tok"
