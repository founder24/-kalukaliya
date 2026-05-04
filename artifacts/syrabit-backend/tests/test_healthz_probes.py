"""
tests/test_healthz_probes.py — Task #333.

Focused contract tests for ``healthz.collect_dependency_health()`` and
the FastAPI route wiring in ``healthz.install_health_routes()``.

These tests deliberately monkey-patch each individual probe so we
exercise the aggregator/route plumbing rather than every dependency's
real network path. The goal is to catch the class of regression that
keeps slipping in on this task — silent ImportError-on-rename of a
required dependency causing ``/api/readyz`` to flap to 503 across
the entire fleet.

Coverage:
  * All probes pass         → status 200, ok=True, degraded=False
  * One probe fails         → status 503, ok=False, degraded=True,
                              the failed dep surfaces in the snapshot
  * Every probe fails       → status 503, ok=False, degraded=False
  * Slow probe is bounded   → completes inside DEP_PROBE_TIMEOUT_S
  * Each real probe symbol  → exists in ``healthz`` (catches the
                              ImportError-class regression directly)
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import healthz


# ─── Symbol-existence guard (regression test for the ImportError class) ──

def test_every_probe_symbol_exists() -> None:
    """Each probe referenced by ``collect_dependency_health`` must be a
    callable on the ``healthz`` module. This catches `_probe_xxx`
    deletions, renames, and broken `from X import Y` statements
    that would otherwise only surface at runtime under load."""
    for name in (
        "_probe_upstash",
        "_probe_supabase",
        "_probe_mongo",
        "_probe_pinecone",
        "_probe_cf_ai_gateway",
        "_probe_vertex_ai",
    ):
        assert callable(getattr(healthz, name, None)), (
            f"healthz.{name} missing or not callable — readiness aggregator "
            f"will fail at runtime"
        )


# ─── Aggregator behaviour ────────────────────────────────────────────────

async def _ok() -> None:
    return None


async def _boom() -> None:
    raise RuntimeError("synthetic-failure")


@pytest.fixture
def patch_all_probes(monkeypatch: pytest.MonkeyPatch):
    """Helper: replace every probe with a controllable coroutine."""
    def _patch(**overrides: Any) -> None:
        for name in (
            "_probe_upstash",
            "_probe_supabase",
            "_probe_mongo",
            "_probe_pinecone",
            "_probe_cf_ai_gateway",
            "_probe_vertex_ai",
        ):
            monkeypatch.setattr(healthz, name, overrides.get(name, _ok))
    return _patch


@pytest.mark.asyncio
async def test_all_probes_pass(patch_all_probes) -> None:
    patch_all_probes()
    snap = await healthz.collect_dependency_health()
    assert snap["ok"] is True
    assert snap["degraded"] is False
    assert snap["failed_count"] == 0
    assert {d["name"] for d in snap["dependencies"]} == {
        "upstash", "supabase", "mongo", "pinecone", "cf_ai_gateway", "vertex_ai",
    }
    assert all(d["ok"] for d in snap["dependencies"])


@pytest.mark.asyncio
async def test_one_probe_failure_marks_degraded(patch_all_probes) -> None:
    patch_all_probes(_probe_pinecone=_boom)
    snap = await healthz.collect_dependency_health()
    assert snap["ok"] is False
    assert snap["degraded"] is True
    assert snap["failed_count"] == 1
    failed = next(d for d in snap["dependencies"] if not d["ok"])
    assert failed["name"] == "pinecone"
    assert "synthetic-failure" in failed["error"]


@pytest.mark.asyncio
async def test_all_probes_fail_not_degraded(patch_all_probes) -> None:
    patch_all_probes(
        _probe_upstash=_boom, _probe_supabase=_boom, _probe_mongo=_boom,
        _probe_pinecone=_boom, _probe_cf_ai_gateway=_boom, _probe_vertex_ai=_boom,
    )
    snap = await healthz.collect_dependency_health()
    assert snap["ok"] is False
    # Fully-down ≠ degraded; 6/6 failed is a hard outage, not partial.
    assert snap["degraded"] is False
    assert snap["failed_count"] == 6


@pytest.mark.asyncio
async def test_probe_timeout_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hung probe must not block the aggregator past
    ``DEP_PROBE_TIMEOUT_S`` (with a small scheduling slack)."""
    async def _hang() -> None:
        await asyncio.sleep(60)

    for name in (
        "_probe_upstash", "_probe_supabase", "_probe_mongo",
        "_probe_pinecone", "_probe_cf_ai_gateway", "_probe_vertex_ai",
    ):
        monkeypatch.setattr(healthz, name, _hang)

    started = time.monotonic()
    snap = await healthz.collect_dependency_health()
    elapsed = time.monotonic() - started
    assert elapsed < healthz.DEP_PROBE_TIMEOUT_S + 1.0, (
        f"aggregator took {elapsed:.2f}s — probe timeout not enforced"
    )
    assert snap["ok"] is False
    assert snap["failed_count"] == 6


# ─── Route wiring ────────────────────────────────────────────────────────

def test_health_route_returns_200(patch_all_probes) -> None:
    patch_all_probes()
    app = FastAPI()
    healthz.install_health_routes(app)
    client = TestClient(app)
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "service" in body and "ts" in body


def test_readyz_route_200_when_healthy(patch_all_probes) -> None:
    patch_all_probes()
    app = FastAPI()
    healthz.install_health_routes(app)
    client = TestClient(app)
    r = client.get("/api/readyz")
    assert r.status_code == 200
    assert r.json()["failed_count"] == 0


def test_readyz_route_503_on_any_failure(patch_all_probes) -> None:
    patch_all_probes(_probe_supabase=_boom)
    app = FastAPI()
    healthz.install_health_routes(app)
    client = TestClient(app)
    r = client.get("/api/readyz")
    assert r.status_code == 503
    body = r.json()
    assert body["ok"] is False
    assert body["failed_count"] == 1
    failed = [d for d in body["dependencies"] if not d["ok"]]
    assert failed and failed[0]["name"] == "supabase"
