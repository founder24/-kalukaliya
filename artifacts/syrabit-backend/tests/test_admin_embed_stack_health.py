"""Task #435 — admin route tests for the embed-backfill surface.

Covers the FastAPI surface in
``routes/admin_embed_stack_health.py`` for the two new endpoints added
in Task #411:

* ``GET  /admin/embed/backfill/progress``
* ``POST /admin/embed/backfill/run``

The unit tests for ``aca_jobs.embed_backfill.get_progress`` and
``aca_jobs.embed_backfill.run_backfill`` exist already; what was
missing was a route-level pin on:

* the admin-auth dependency (Depends(get_admin_user) on both routes),
* the ``batch_size`` Query bounds (1 ≤ N ≤ 32 — the worker's hard cap),
* the ``max_chunks`` Query bounds (1 ≤ N ≤ 200_000),
* the ``already_running`` short-circuit (POST returns
  ``started=False`` without scheduling a second background task), and
* the progress payload shape returned to the admin UI.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def mock_admin():
    return {"id": "admin-1", "email": "ops@syrabit.ai", "is_admin": True,
            "sub": "admin-1"}


@pytest.fixture
def app_client_authed(mock_admin):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.admin_embed_stack_health import router
    from auth_deps import get_admin_user
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides = {get_admin_user: lambda: mock_admin}
    return TestClient(app)


@pytest.fixture
def app_client_no_auth():
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    from routes.admin_embed_stack_health import router
    from auth_deps import get_admin_user
    app = FastAPI()
    app.include_router(router)

    def _deny():
        raise HTTPException(status_code=401, detail="Not authenticated")

    app.dependency_overrides = {get_admin_user: _deny}
    return TestClient(app)


_SAMPLE_PROGRESS = {
    "target_source":   "workers_ai_custom",
    "total_chunks":    1000,
    "remaining":       250,
    "remaining_by_source": {"legacy_voyage": 250},
    "re_embedded":     750,
    "percent":         75.0,
    "running":         False,
    "last_processed_id": "chunk-deadbeef",
    "started_at":      None,
    "updated_at":      None,
    "completed_at":    None,
    "last_run":        None,
    "batch_size":      32,
    "max_rpm":         60,
}


@pytest.fixture
def patched_backfill(monkeypatch):
    """Stub get_progress / run_backfill so the route tests don't hit
    the real Mongo-backed implementations. Returns a small handle the
    test can use to flip the ``running`` flag and inspect run_backfill
    invocations."""
    from aca_jobs import embed_backfill as _bf

    state = {
        "progress": dict(_SAMPLE_PROGRESS),
        "run_calls": [],
    }

    async def _fake_get_progress(db):
        return dict(state["progress"])

    async def _fake_run_backfill(db, *, max_chunks=None, batch_size=32):
        state["run_calls"].append({"max_chunks": max_chunks,
                                   "batch_size": batch_size})
        return {"processed": 0}

    monkeypatch.setattr(_bf, "get_progress", _fake_get_progress)
    monkeypatch.setattr(_bf, "run_backfill", _fake_run_backfill)
    return state


# ── Auth ────────────────────────────────────────────────────────────────

def test_progress_requires_admin_auth(app_client_no_auth):
    res = app_client_no_auth.get("/admin/embed/backfill/progress")
    assert res.status_code in (401, 403)


def test_run_requires_admin_auth(app_client_no_auth):
    res = app_client_no_auth.post("/admin/embed/backfill/run")
    assert res.status_code in (401, 403)


# ── GET /admin/embed/backfill/progress ──────────────────────────────────

def test_progress_returns_expected_shape(app_client_authed, patched_backfill):
    res = app_client_authed.get("/admin/embed/backfill/progress")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    # All keys the AdminBackfillProgress tile depends on.
    for key in ("target_source", "total_chunks", "remaining",
                "remaining_by_source", "re_embedded", "percent",
                "running", "last_processed_id", "batch_size", "max_rpm"):
        assert key in body, f"missing top-level key: {key}"
    assert body["total_chunks"] == 1000
    assert body["re_embedded"] == 750
    assert body["percent"] == 75.0
    assert body["running"] is False


def test_progress_reports_db_unavailable(app_client_authed, monkeypatch,
                                         patched_backfill):
    import deps
    monkeypatch.setattr(deps, "db", None, raising=False)
    res = app_client_authed.get("/admin/embed/backfill/progress")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["reason"] == "db unavailable"


# ── POST /admin/embed/backfill/run ──────────────────────────────────────

def test_run_rejects_batch_size_above_worker_cap(app_client_authed,
                                                 patched_backfill):
    # Worker hard limit is 32 inputs per /embed call.
    res = app_client_authed.post(
        "/admin/embed/backfill/run?batch_size=33"
    )
    assert res.status_code == 422
    assert patched_backfill["run_calls"] == []


def test_run_rejects_batch_size_zero(app_client_authed, patched_backfill):
    res = app_client_authed.post(
        "/admin/embed/backfill/run?batch_size=0"
    )
    assert res.status_code == 422
    assert patched_backfill["run_calls"] == []


def test_run_rejects_max_chunks_above_cap(app_client_authed, patched_backfill):
    res = app_client_authed.post(
        "/admin/embed/backfill/run?max_chunks=200001"
    )
    assert res.status_code == 422
    assert patched_backfill["run_calls"] == []


def test_run_rejects_max_chunks_zero(app_client_authed, patched_backfill):
    # ge=1 floor — a 0-chunk run is meaningless and would otherwise
    # still spin up a background task that does nothing.
    res = app_client_authed.post(
        "/admin/embed/backfill/run?max_chunks=0"
    )
    assert res.status_code == 422
    assert patched_backfill["run_calls"] == []


def test_run_short_circuits_when_already_running(app_client_authed,
                                                 patched_backfill):
    patched_backfill["progress"]["running"] = True
    res = app_client_authed.post("/admin/embed/backfill/run")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["started"] is False
    assert body["reason"] == "already_running"
    assert body["progress"]["running"] is True
    # Critical: must NOT have scheduled a second concurrent backfill.
    assert patched_backfill["run_calls"] == []


def test_run_starts_when_not_running(app_client_authed, patched_backfill):
    res = app_client_authed.post(
        "/admin/embed/backfill/run?max_chunks=100&batch_size=8"
    )
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["started"] is True
    assert body["max_chunks"] == 100
    assert body["batch_size"] == 8
    assert body["progress"]["running"] is False

    # Background task scheduled with the same params the route reported.
    # asyncio.create_task is fire-and-forget; TestClient runs the route
    # to completion in its own event loop, which is enough for the
    # task to be created and awaited at least once.
    assert len(patched_backfill["run_calls"]) == 1
    call = patched_backfill["run_calls"][0]
    assert call["max_chunks"] == 100
    assert call["batch_size"] == 8


def test_run_reports_db_unavailable(app_client_authed, monkeypatch,
                                    patched_backfill):
    import deps
    monkeypatch.setattr(deps, "db", None, raising=False)
    res = app_client_authed.post("/admin/embed/backfill/run")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert body["reason"] == "db unavailable"
    assert patched_backfill["run_calls"] == []
