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
    "remaining_by_source": {"legacy": 250},
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


# ── Task #438 — embed_environments payload shape ────────────────────────────

def test_embed_stack_health_surfaces_staging_row(app_client_authed,
                                                  patched_backfill,
                                                  monkeypatch):
    """The route must surface ``embed_environments`` with one row per
    registered worker env, and a failing staging row must NOT flip the
    top-level ``ok`` field (staging is a canary — see Task #438)."""
    from providers import workers_embed as _we

    async def _fake_envs():
        return [
            {"env": "production", "label": "Production", "ok": True,
             "configured": True, "pages": True, "dims": 1024,
             "model_version": "1.0.0", "latency_ms": 42, "status_code": 200,
             "url": "https://embed.test.local"},
            {"env": "staging", "label": "Staging", "ok": False,
             "configured": True, "pages": False, "dims": 1024,
             "model_version": "staging-2026-05-06",
             "latency_ms": 71, "status_code": 503,
             "url": "https://embed-staging.test.local",
             "reason": "workers_embed: HTTP 503: down"},
        ]

    async def _fake_health_check():
        return {"ok": True, "configured": True, "dims": 1024,
                "model_version": "1.0.0", "latency_ms": 42}

    async def _fake_rerank_health_check():
        return {"ok": True}

    async def _fake_memory_health_check():
        return {"ok": True}

    monkeypatch.setattr(_we, "health_check_environments", _fake_envs)
    monkeypatch.setattr(_we, "health_check", _fake_health_check)

    from providers import pinecone_ai as _pc
    from providers import memory_brain as _mb
    monkeypatch.setattr(_pc, "rerank_health_check",
                        _fake_rerank_health_check, raising=False)
    monkeypatch.setattr(_mb, "health_check",
                        _fake_memory_health_check, raising=False)

    res = app_client_authed.get("/admin/health/embed-stack")
    assert res.status_code == 200
    body = res.json()

    # Multi-env payload must be present + correctly shaped.
    envs = body.get("embed_environments")
    assert isinstance(envs, list) and len(envs) == 2
    by_env = {e["env"]: e for e in envs}
    assert set(by_env) == {"production", "staging"}
    for required in ("dims", "model_version", "latency_ms",
                     "configured", "pages", "ok"):
        assert required in by_env["production"], f"prod missing {required}"
        assert required in by_env["staging"],    f"staging missing {required}"
    assert by_env["production"]["pages"] is True
    assert by_env["staging"]["pages"] is False

    # Critical: staging being down must NOT page — top-level ok stays True
    # because production embed + rerank + memory_brain are all healthy.
    assert body["ok"] is True
    assert body["embed"]["ok"] is True  # back-compat field still production-only


# ── Task #477 — staging embed-worker outage must never page on-call ─────
# The Task #438 design contract is "staging failures show a yellow row but
# DO NOT page". The provider-level behaviour is unit-tested in
# ``test_health_check_environments_staging_failure_does_not_flip_pages``,
# but the route-level guarantee — that the admin embed-stack endpoint's
# top-level ``ok`` field stays ``True`` when only the staging row is down
# — needs its own pin so a future refactor of the aggregation logic
# can't silently regress the on-call paging contract.
def test_embed_stack_health_staging_only_outage_does_not_page(
        app_client_authed, patched_backfill, monkeypatch):
    from providers import workers_embed as _we
    from providers import pinecone_ai as _pc
    from providers import memory_brain as _mb

    async def _fake_envs():
        return [
            {"env": "production", "label": "Production", "ok": True,
             "configured": True, "pages": True, "dims": 1024,
             "model_version": "1.0.0", "latency_ms": 38, "status_code": 200,
             "url": "https://embed.test.local"},
            {"env": "staging", "label": "Staging", "ok": False,
             "configured": True, "pages": False, "dims": 1024,
             "model_version": "staging-2026-05-07",
             "latency_ms": 0, "status_code": 503,
             "url": "https://embed-staging.test.local",
             "reason": "workers_embed: HTTP 503: upstream down"},
        ]

    async def _fake_embed_health():
        return {"ok": True, "configured": True, "dims": 1024,
                "model_version": "1.0.0", "latency_ms": 38}

    async def _fake_rerank_health():
        return {"ok": True}

    async def _fake_memory_health():
        return {"ok": True}

    monkeypatch.setattr(_we, "health_check_environments", _fake_envs)
    monkeypatch.setattr(_we, "health_check", _fake_embed_health)
    monkeypatch.setattr(_pc, "rerank_health_check", _fake_rerank_health,
                        raising=False)
    monkeypatch.setattr(_mb, "health_check", _fake_memory_health,
                        raising=False)

    res = app_client_authed.get("/admin/health/embed-stack")
    assert res.status_code == 200
    body = res.json()

    # Critical contract: a staging-only outage must NOT page on-call.
    assert body["ok"] is True, (
        "staging embed-worker outage flipped the page-level ok flag — "
        "this would page on-call for a canary failure (Task #438)"
    )
    # Production-only back-compat pill stays green.
    assert body["embed"]["ok"] is True

    # Staging row is exposed under embed_environments so the dashboard
    # can still render the yellow canary row.
    envs = body.get("embed_environments")
    assert isinstance(envs, list)
    by_env = {e["env"]: e for e in envs}
    assert "staging" in by_env, "staging row missing from embed_environments"
    assert by_env["staging"]["ok"] is False
    assert by_env["production"]["ok"] is True


# ── Task #469 — embed-stack alert pill counter contract ─────────────────
# Task #436 added the per-leg "N/3 consecutive failures" badge driven by
# the Task #412 watchdog counters in metrics.py. The metrics accessor
# and the route fields are now load-bearing for on-call awareness, but
# nothing pinned their shape — a silent rename of any of
# ``consecutive_failures`` / ``firing`` / ``alert_threshold`` /
# ``alert_state.legs.<leg>`` would blank out the dashboard badge
# without any test failing. These tests lock the contract.


def _reset_embed_stack_counters():
    import metrics as _m
    for leg in _m._EMBED_STACK_LEGS:
        _m._embed_stack_consecutive_failures[leg] = 0
        _m._embed_stack_was_firing[leg] = False
        _m._embed_stack_last_error[leg] = None
        _m._embed_stack_last_latency_ms[leg] = None


@pytest.fixture
def reset_embed_stack_counters():
    _reset_embed_stack_counters()
    yield
    _reset_embed_stack_counters()


def test_get_embed_stack_alert_snapshot_shape(reset_embed_stack_counters):
    """The Task #436 dashboard badge reads exactly these keys; if any
    field is renamed the per-leg counter pill goes dark in production."""
    import metrics as _m
    snap = _m.get_embed_stack_alert_snapshot()

    # Top-level: threshold (int) + legs dict keyed by every leg.
    assert isinstance(snap, dict)
    assert "threshold" in snap and isinstance(snap["threshold"], int)
    assert snap["threshold"] >= 1
    assert "legs" in snap and isinstance(snap["legs"], dict)
    assert set(snap["legs"].keys()) == set(_m._EMBED_STACK_LEGS)

    # Per-leg: every key the EmbedStackHealthPill consumes.
    for leg, leg_state in snap["legs"].items():
        for key in ("consecutive_failures", "firing",
                    "last_error", "last_latency_ms"):
            assert key in leg_state, f"{leg} missing {key}"
        assert leg_state["consecutive_failures"] == 0
        assert leg_state["firing"] is False


def test_get_embed_stack_alert_snapshot_reflects_counter_mutation(
        reset_embed_stack_counters):
    """Driving the in-memory counters must show through the snapshot —
    this is the watchdog -> dashboard data path the badge depends on."""
    import metrics as _m
    _m._embed_stack_consecutive_failures["embed"] = 2
    _m._embed_stack_was_firing["rerank"] = True
    _m._embed_stack_consecutive_failures["rerank"] = 5
    _m._embed_stack_last_error["memory_brain"] = "workers_ai_custom 503"
    _m._embed_stack_last_latency_ms["embed"] = 137

    snap = _m.get_embed_stack_alert_snapshot()
    assert snap["legs"]["embed"]["consecutive_failures"] == 2
    assert snap["legs"]["embed"]["firing"] is False
    assert snap["legs"]["embed"]["last_latency_ms"] == 137
    assert snap["legs"]["rerank"]["consecutive_failures"] == 5
    assert snap["legs"]["rerank"]["firing"] is True
    assert snap["legs"]["memory_brain"]["last_error"] == "workers_ai_custom 503"


def test_admin_embed_stack_health_surfaces_alert_state_per_leg(
        app_client_authed, patched_backfill, monkeypatch,
        reset_embed_stack_counters):
    """GET /admin/health/embed-stack must carry the per-leg counter
    fields on each leg pill (embed/rerank/memory) AND the top-level
    ``alert_state`` block. The frontend pill renders ``firing`` red and
    ``1..threshold-1`` amber — both come from these fields."""
    import metrics as _m
    # Drive the watchdog state: embed in warm-up window (amber on the
    # frontend), rerank firing (red), memory_brain clean (emerald).
    _m._embed_stack_consecutive_failures["embed"] = 1
    _m._embed_stack_consecutive_failures["rerank"] = 3
    _m._embed_stack_was_firing["rerank"] = True

    from providers import workers_embed as _we
    from providers import pinecone_ai as _pc
    from providers import memory_brain as _mb

    async def _envs():
        return [{"env": "production", "label": "Production", "ok": True,
                 "configured": True, "pages": True, "dims": 1024,
                 "model_version": "1.0.0", "latency_ms": 42,
                 "status_code": 200, "url": "https://embed.test"}]

    async def _embed_health():
        return {"ok": True, "configured": True, "dims": 1024,
                "model_version": "1.0.0", "latency_ms": 42}

    async def _rerank_health():
        return {"ok": False, "reason": "down"}

    async def _memory_health():
        return {"ok": True}

    monkeypatch.setattr(_we, "health_check_environments", _envs)
    monkeypatch.setattr(_we, "health_check", _embed_health)
    monkeypatch.setattr(_pc, "rerank_health_check", _rerank_health,
                        raising=False)
    monkeypatch.setattr(_mb, "health_check", _memory_health, raising=False)

    res = app_client_authed.get("/admin/health/embed-stack")
    assert res.status_code == 200
    body = res.json()

    # Every leg pill carries the three counter fields the dashboard
    # badge depends on. A silent rename here blanks out the badge.
    for leg in ("embed", "rerank", "memory"):
        pill = body[leg]
        for key in ("consecutive_failures", "firing", "alert_threshold"):
            assert key in pill, f"{leg} pill missing {key}"
        assert isinstance(pill["consecutive_failures"], int)
        assert isinstance(pill["firing"], bool)
        assert isinstance(pill["alert_threshold"], int)

    assert body["embed"]["consecutive_failures"] == 1
    assert body["embed"]["firing"] is False
    assert body["rerank"]["consecutive_failures"] == 3
    assert body["rerank"]["firing"] is True
    assert body["memory"]["consecutive_failures"] == 0
    assert body["memory"]["firing"] is False

    # Top-level alert_state block — the EmbedStackHealthPill component
    # also reads this for the page-wide threshold/legs view.
    alert_state = body.get("alert_state")
    assert isinstance(alert_state, dict)
    assert isinstance(alert_state.get("threshold"), int)
    assert alert_state["threshold"] == body["embed"]["alert_threshold"]
    assert isinstance(alert_state.get("legs"), dict)
    assert set(alert_state["legs"].keys()) >= {"embed", "rerank",
                                                "memory_brain"}
    for leg_state in alert_state["legs"].values():
        for key in ("consecutive_failures", "firing"):
            assert key in leg_state
