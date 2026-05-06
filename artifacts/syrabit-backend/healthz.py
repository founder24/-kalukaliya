"""Health + readiness endpoints for the DO Python backend.

Phase 5 — Observability rewire (Task #333).

Replaces the ad-hoc ``/healthz/ai`` / ``/healthz/r2`` probes with a
unified pair:

  * ``GET /api/health``  — liveness only (process alive, event loop
    ticking). Azure Container Apps' liveness probe hits this; a 5xx here
    triggers a pod restart.

  * ``GET /api/readyz``  — readiness, fans out concurrent dependency
    probes (Upstash, Supabase, Mongo, Pinecone, CF AI Gateway,
    Vertex AI). DO's rolling-deploy readiness gate AND the admin
    health panel's "External dependencies" tile both consume this.

Each probe is bounded at ``DEP_PROBE_TIMEOUT_S`` so one slow backend
cannot block the response past the LB's 5s health-check timeout.
Probes are pure read operations — never side-effecting.

The legacy ``/healthz/ai`` and ``/healthz/r2`` routes in ``server.py``
remain in place (admin health panel cards still call them
individually); ``/api/readyz`` is a new aggregate that lets the
infra layer get a single yes/no without polling every leaf endpoint.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

# Per-probe budget. Kept well below Azure Container Apps' 5s health-check
# timeout so a single slow backend never propagates into a flapping
# readiness state for the whole pod.
DEP_PROBE_TIMEOUT_S = 1.5


async def _probe(name: str, fn: Callable[[], Awaitable[Any]]) -> dict[str, Any]:
    """Run a single dependency probe, bounded by ``DEP_PROBE_TIMEOUT_S``."""
    started = time.monotonic()
    try:
        await asyncio.wait_for(fn(), timeout=DEP_PROBE_TIMEOUT_S)
        return {
            "name":       name,
            "ok":         True,
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
    except asyncio.TimeoutError:
        return {
            "name":       name,
            "ok":         False,
            "latency_ms": int(DEP_PROBE_TIMEOUT_S * 1000),
            "error":      f"timeout after {DEP_PROBE_TIMEOUT_S}s",
        }
    except Exception as e:  # noqa: BLE001
        return {
            "name":       name,
            "ok":         False,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "error":      f"{type(e).__name__}: {e}"[:200],
        }


# ─── Per-dependency probes ──────────────────────────────────────────────────
# Each probe uses an inline import so a missing client during cold-start
# (or in a smoke environment without that backend) shows up as a
# "ok=False, error=ImportError: ..." line in the /api/readyz response
# instead of breaking module import.


async def _probe_upstash() -> None:
    """PING the Upstash Redis cluster (rate-limit + cache backplane)."""
    from cache import redis_client  # type: ignore
    if redis_client is None:
        raise RuntimeError("redis_client is None")
    res = redis_client.ping()
    if asyncio.iscoroutine(res):
        await res


async def _probe_supabase() -> None:
    """SELECT 1 on Supabase Postgres (auth + admin metadata store).

    Raises on ImportError — Supabase is a required dependency for the
    DO backend, so a missing client is a configuration failure that
    must show up in the readiness response, not be silently swallowed.
    """
    # Read the pool off the `deps` module at probe time. The pool is
    # filled in by the FastAPI lifespan startup hook (`_init_pg_pool`)
    # and is `None` until then; we deliberately do NOT cache it
    # locally so a probe that runs after a pool refresh sees the
    # current handle.
    import deps as _deps  # type: ignore
    pool = getattr(_deps, "pg_pool", None)
    if pool is None:
        raise RuntimeError("deps.pg_pool is None (lifespan startup not complete?)")
    async with pool.acquire() as conn:
        await conn.fetchval("SELECT 1")


async def _probe_mongo() -> None:
    """Mongo admin ping (primary content + analytics store)."""
    from server import db  # type: ignore
    if db is None:
        raise RuntimeError("server.db is None")
    # Motor returns a coroutine for admin commands.
    await db.client.admin.command("ping")


async def _probe_pinecone() -> None:
    """Pinecone describe-index (vector store for grounded retrieval).

    Resolves the index host via the existing
    ``retrievers.pinecone_vector._get_index_host`` helper — a cheap
    authenticated control-plane describe-index call that exercises
    both API key + network path without spending query units. Raises
    (caught by ``_probe`` and surfaced as ``ok=false``) on missing
    integration, missing API key, or network failure.
    """
    from retrievers.pinecone_vector import _get_index_host  # type: ignore
    host = await _get_index_host()
    if not host:
        raise RuntimeError("pinecone describe-index returned no host")


async def _probe_cf_ai_gateway() -> None:
    """HEAD the Cloudflare AI Gateway base URL (LLM passthrough)."""
    base = (os.environ.get("CF_AI_GATEWAY_URL") or "").strip()
    if not base:
        raise RuntimeError("CF_AI_GATEWAY_URL not set")
    import httpx
    async with httpx.AsyncClient(timeout=DEP_PROBE_TIMEOUT_S) as c:
        r = await c.head(base)
        if r.status_code >= 500:
            raise RuntimeError(f"HTTP {r.status_code}")


async def _probe_vertex_ai() -> None:
    """Vertex AI auth + reachability.

    Vertex remains an inference dependency surfaced in /api/readyz
    even after GCP hosting is retired — the production chat flow
    still calls Vertex via the Cloudflare AI Gateway. The probe
    fetches an OAuth token from the metadata server (or the env-JSON
    SA) and HEADs the regional Vertex endpoint, exercising both the
    credential path AND network reachability under the per-probe
    timeout budget. No tokens spent.
    """
    project = (
        os.environ.get("VERTEX_PROJECT_ID")
        or os.environ.get("GOOGLE_CLOUD_PROJECT")
        or ""
    ).strip()
    if not project:
        raise RuntimeError("VERTEX_PROJECT_ID not set")
    region = (os.environ.get("VERTEX_REGION") or "us-central1").strip()

    # Mint an access token via Application Default Credentials.
    # `google-auth` is already a transitive dep of the Vertex SDK;
    # ImportError here is a real configuration failure.
    import google.auth  # type: ignore
    from google.auth.transport.requests import Request  # type: ignore

    def _token() -> str:
        creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        creds.refresh(Request())
        return creds.token

    token = await asyncio.to_thread(_token)
    if not token:
        raise RuntimeError("ADC produced empty access token")

    # GET the publishers listing — cheapest authenticated read on
    # the regional Vertex endpoint that returns a 200 on success.
    import httpx
    url = f"https://{region}-aiplatform.googleapis.com/v1/projects/{project}/locations/{region}/publishers/google/models"
    async with httpx.AsyncClient(timeout=DEP_PROBE_TIMEOUT_S) as c:
        r = await c.get(url, headers={"Authorization": f"Bearer {token}"}, params={"pageSize": 1})
        if r.status_code >= 400:
            raise RuntimeError(f"vertex HTTP {r.status_code}")


async def collect_dependency_health() -> dict[str, Any]:
    """Fan out every probe in parallel and aggregate.

    Returns a dict shaped for both the readyz endpoint AND the admin
    health panel's "External dependencies" tile. The summary fields
    (``ok``, ``degraded``) let the LB and the React UI agree on a
    single source of truth without re-deriving.
    """
    probes = await asyncio.gather(
        _probe("upstash",       _probe_upstash),
        _probe("supabase",      _probe_supabase),
        _probe("mongo",         _probe_mongo),
        _probe("pinecone",      _probe_pinecone),
        _probe("cf_ai_gateway", _probe_cf_ai_gateway),
        _probe("vertex_ai",     _probe_vertex_ai),
    )
    failed = [p for p in probes if not p["ok"]]
    return {
        "ok":           len(failed) == 0,
        "degraded":     0 < len(failed) < len(probes),
        "checked_at":   int(time.time()),
        "dependencies": probes,
        "failed_count": len(failed),
    }


def install_health_routes(app: Any) -> None:
    """Attach ``/api/health`` and ``/api/readyz`` to a FastAPI app.

    Idempotent: re-invocation registers duplicate routes only on the
    second call (FastAPI logs a warning), so the call site in
    ``server.py`` guards with a module-level flag.
    """

    @app.get("/api/health", tags=["health"], include_in_schema=False)
    async def _api_health() -> dict[str, Any]:
        # Liveness: the event loop is responsive. No external probes.
        return {
            "ok":       True,
            "service":  os.environ.get("OTEL_SERVICE_NAME", "syrabit-backend-do"),
            "version":  os.environ.get("OTEL_SERVICE_VERSION", "2.0.0"),
            "ts":       int(time.time()),
        }

    @app.get("/api/readyz", tags=["health"], include_in_schema=False)
    async def _api_readyz() -> "JSONResponse":
        # Hard-fail readiness on ANY missing dep so the LB's
        # rolling-deploy gate refuses to mark a freshly-rolled pod
        # ready until its dependencies are reachable. The admin
        # panel keys off ``failed_count`` for per-dep traffic lights.
        #
        # We return a JSONResponse with an explicit status_code
        # rather than annotating ``response: Response`` on the
        # handler — under ``from __future__ import annotations`` the
        # parameter annotation becomes a string and FastAPI's
        # dependency resolver treats it as a query parameter, which
        # makes every call 422. JSONResponse sidesteps that pitfall.
        from fastapi.responses import JSONResponse  # local import keeps the module top-level fastapi-free
        snapshot = await collect_dependency_health()
        status = 503 if snapshot["failed_count"] > 0 else 200
        return JSONResponse(snapshot, status_code=status)
