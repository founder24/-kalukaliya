"""Task #667 — verify the Gemini startup self-check hook.

Two guarantees:

1. **Registration**: the lifespan in ``server.py`` schedules
   ``_vertex_startup_probe`` as a background task on boot, so the probe
   actually runs after every Railway deploy.
2. **Failure path**: when ``vertex_services.health_check()`` reports
   ``embeddings`` or ``generation`` as ``False``, the probe emits exactly
   one ``ERROR`` log line containing the upstream failure reason — that
   line is what the on-call sees in deploy logs before any user-facing
   502.

To avoid importing ``server.py`` (which calls ``sys.exit(1)`` on missing
prod env vars and pulls in Mongo/Vertex/CF clients), we extract the
``_vertex_startup_probe`` function via ``ast`` and exec it in an isolated
namespace with stubbed ``vertex_services`` and ``logger`` symbols. The
hook-registration check is a pure source-level assertion against the
``lifespan`` function body.
"""
from __future__ import annotations

import ast
import asyncio
import logging
import pathlib
import textwrap
from typing import Any

import pytest


SERVER_PY = pathlib.Path(__file__).resolve().parent.parent / "server.py"


def _server_module_ast() -> ast.Module:
    return ast.parse(SERVER_PY.read_text(encoding="utf-8"))


def _find_function(tree: ast.Module, name: str) -> ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"async function {name!r} not found in server.py")


def _extract_probe_callable():
    """Return the live ``_vertex_startup_probe`` coroutine function and
    the stub-logger / stub-vertex_services it closes over, so a test can
    assert against captured log records.
    """
    tree = _server_module_ast()
    fn_node = _find_function(tree, "_vertex_startup_probe")

    src = textwrap.dedent(ast.get_source_segment(SERVER_PY.read_text(encoding="utf-8"), fn_node))

    stub_logger = logging.getLogger("syrabit.test.vertex_startup_probe")
    stub_logger.handlers.clear()
    stub_logger.setLevel(logging.DEBUG)
    stub_logger.propagate = False

    captured: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record):  # noqa: D401
            captured.append(record)

    stub_logger.addHandler(_Capture())

    # Globals the probe body references at the module level. Kept in
    # one dict so adding a new dependency on a stdlib symbol (e.g.
    # ``os.environ`` for the configurable timeout introduced after the
    # 2026-04-25 audit) is a one-line change here instead of N test
    # updates.
    import os as _os
    from typing import Optional as _Optional
    namespace: dict[str, Any] = {
        "logger": stub_logger,
        "asyncio": asyncio,
        "os": _os,
        "Optional": _Optional,
    }
    exec(compile(src, str(SERVER_PY), "exec"), namespace)
    return namespace["_vertex_startup_probe"], captured, namespace


# ---------------------------------------------------------------------------
# 1. Hook registration
# ---------------------------------------------------------------------------

def test_lifespan_schedules_vertex_startup_probe():
    """The ``lifespan`` body must contain
    ``asyncio.create_task(_vertex_startup_probe())`` — otherwise a
    deploy with broken Gemini credentials would stay silent until the
    first user-facing 502.
    """
    tree = _server_module_ast()
    lifespan = _find_function(tree, "lifespan")
    src = ast.unparse(lifespan)
    # Task #332 — the boot self-check now schedules through the
    # takeover-aware `_aca_create_task(..., key="vertex-startup-probe")`
    # wrapper so it can be served by `aca-job-vertex-startup-probe`
    # instead of the API container under takeover. Either form OK.
    assert (
        "asyncio.create_task(_vertex_startup_probe())" in src
        or "_aca_create_task(_vertex_startup_probe(), key='vertex-startup-probe')" in src
    ), (
        "Expected lifespan() to schedule _vertex_startup_probe as a "
        "background task (via asyncio.create_task or _aca_create_task); "
        "without it the boot self-check never runs."
    )


def test_vertex_startup_probe_is_defined_as_async():
    tree = _server_module_ast()
    fn = _find_function(tree, "_vertex_startup_probe")
    assert isinstance(fn, ast.AsyncFunctionDef)


# ---------------------------------------------------------------------------
# 2. Failure path logs at ERROR
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


def test_probe_logs_error_when_embeddings_fail(monkeypatch):
    # Task #332: the gateway-fallback HTTP probe inside
    # `_vertex_startup_probe` will short-circuit to a WARNING / "degraded
    # but passing" health record when the *direct* api.cloudflare.com
    # path returns 200. In a developer environment that has real
    # CF_AI_GATEWAY_ACCOUNT_ID + CLOUDFLARE_API_TOKEN exported, the
    # fallback probe really does fire a network call and really does
    # come back 200 — which masks the ERROR-log path under test.
    # Strip those env vars so we exercise the failure branch
    # deterministically regardless of host environment.
    for _k in ("CF_AI_GATEWAY_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN",
               "CF_AI_GATEWAY_TOKEN", "CF_AI_GATEWAY_ID"):
        monkeypatch.delenv(_k, raising=False)

    probe, captured, ns = _extract_probe_callable()

    class _StubVertex:
        @staticmethod
        async def health_check():
            return {
                "ok": False,
                "auth_mode": "byok",
                "via_cf_gateway": True,
                "embeddings": False,   # <- broken
                "generation": True,
                "reason": None,
            }

    import sys
    monkeypatch.setitem(sys.modules, "vertex_services", _StubVertex)

    _run(probe())

    errors = [r for r in captured if r.levelno == logging.ERROR]
    assert errors, "Expected an ERROR log when embeddings probe fails"
    assert "STARTUP-PROBE" in errors[0].getMessage()
    assert "embeddings=False" in errors[0].getMessage()


def test_probe_logs_error_when_generation_fails(monkeypatch):
    probe, captured, ns = _extract_probe_callable()

    class _StubVertex:
        @staticmethod
        async def health_check():
            return {
                "ok": False,
                "auth_mode": "service_account",
                "via_cf_gateway": False,
                "embeddings": True,
                "generation": False,   # <- broken
                "reason": None,
            }

    import sys
    monkeypatch.setitem(sys.modules, "vertex_services", _StubVertex)

    _run(probe())

    errors = [r for r in captured if r.levelno == logging.ERROR]
    assert errors, "Expected an ERROR log when generation probe fails"
    assert "generation=False" in errors[0].getMessage()


def test_probe_logs_error_with_reason_when_no_credential(monkeypatch):
    probe, captured, ns = _extract_probe_callable()

    class _StubVertex:
        @staticmethod
        async def health_check():
            return {
                "ok": False,
                "auth_mode": "none",
                "reason": "No credential available (set VERTEX_SERVICE_ACCOUNT or GOOGLE_APPLICATION_CREDENTIALS_JSON).",
            }

    import sys
    monkeypatch.setitem(sys.modules, "vertex_services", _StubVertex)

    _run(probe())

    errors = [r for r in captured if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "No credential available" in errors[0].getMessage()


def test_probe_does_not_log_error_when_healthy(monkeypatch):
    probe, captured, ns = _extract_probe_callable()

    class _StubVertex:
        @staticmethod
        async def health_check():
            return {
                "ok": True,
                "auth_mode": "byok",
                "via_cf_gateway": True,
                "embeddings": True,
                "generation": True,
            }

    import sys
    monkeypatch.setitem(sys.modules, "vertex_services", _StubVertex)

    _run(probe())

    errors = [r for r in captured if r.levelno == logging.ERROR]
    assert not errors, f"Healthy probe must not log ERROR, got: {[r.getMessage() for r in errors]}"


def test_probe_logs_error_on_timeout(monkeypatch):
    """A hanging upstream must not stall the probe — the 5s
    ``asyncio.wait_for`` guard must trip and emit a single ERROR line so
    the deploy log surfaces the unreachable Gemini quickly.
    """
    probe, captured, ns = _extract_probe_callable()

    class _StubVertex:
        @staticmethod
        async def health_check():
            await asyncio.sleep(60)  # never returns within the 5s budget
            return {"embeddings": True, "generation": True}

    import sys
    monkeypatch.setitem(sys.modules, "vertex_services", _StubVertex)

    # Patch wait_for to use a short fake timeout so the test stays fast,
    # while still exercising the real TimeoutError branch.
    real_wait_for = asyncio.wait_for

    async def _fast_wait_for(coro, timeout):  # noqa: ARG001
        return await real_wait_for(coro, timeout=0.05)

    monkeypatch.setattr(asyncio, "wait_for", _fast_wait_for)

    _run(probe())

    errors = [r for r in captured if r.levelno == logging.ERROR]
    assert errors, "Expected an ERROR log on probe timeout"
    assert "timed out" in errors[0].getMessage().lower()


def test_probe_swallows_exceptions_and_logs_error(monkeypatch):
    """A raising health_check (e.g. network blow-up) must not crash the
    background task — it must log a single ERROR and return cleanly so
    the rest of the API stays up.
    """
    probe, captured, ns = _extract_probe_callable()

    class _StubVertex:
        @staticmethod
        async def health_check():
            raise RuntimeError("boom: AI Gateway TLS handshake failed")

    import sys
    monkeypatch.setitem(sys.modules, "vertex_services", _StubVertex)

    _run(probe())  # must not raise

    errors = [r for r in captured if r.levelno == logging.ERROR]
    assert errors
    assert "boom" in errors[0].getMessage()


# ---------------------------------------------------------------------------
# 3. Failure-path diagnostics (post-2026-04-25 audit)
# ---------------------------------------------------------------------------
#
# Before the audit fix, both the timeout and exception branches called
# ``vertex_health_cache.record(False, reason=..., source="startup")`` WITHOUT
# passing ``auth_mode`` or ``via_cf_gateway``. That made ``/healthz/ai`` show
# ``"auth_mode": null`` even when credentials WERE configured and the upstream
# was simply slow — operators couldn't tell whether the box had no creds at
# all or whether SA-mode was attempted and the network hung.
#
# These tests pin the new behavior: failure paths must look up the auth
# meta from the vertex_services module (which captures ``_AUTH_MODE`` /
# ``_CF_GW_ENABLED`` at import time) and forward it into the cache.

def _extract_record_calls(monkeypatch):
    """Patch vertex_health_cache.record to capture call kwargs and return
    the captured list. Stubs the module before the probe runs."""
    import sys
    import types

    captured: list[dict[str, Any]] = []
    stub = types.ModuleType("vertex_health_cache")

    def _record(ok, **kwargs):  # noqa: ANN001
        captured.append({"ok": ok, **kwargs})

    stub.record = _record  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "vertex_health_cache", stub)
    return captured


def test_timeout_path_captures_auth_mode_and_gateway(monkeypatch):
    """Regression test: the timeout branch must forward ``auth_mode`` and
    ``via_cf_gateway`` to the health cache (looked up from the
    vertex_services module-level state) so /healthz/ai reports a real
    auth mode instead of null.
    """
    probe, captured_logs, ns = _extract_probe_callable()
    captured_records = _extract_record_calls(monkeypatch)

    import sys
    import types
    fake_vs = types.ModuleType("vertex_services")
    fake_vs._AUTH_MODE = "vertex_ai_service_account"  # type: ignore[attr-defined]
    fake_vs._CF_GW_ENABLED = True  # type: ignore[attr-defined]

    async def _hang():
        await asyncio.sleep(60)
        return {}

    fake_vs.health_check = _hang  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "vertex_services", fake_vs)

    real_wait_for = asyncio.wait_for

    async def _fast_wait_for(coro, timeout):  # noqa: ARG001
        return await real_wait_for(coro, timeout=0.05)

    monkeypatch.setattr(asyncio, "wait_for", _fast_wait_for)

    _run(probe())

    assert len(captured_records) == 1, "Probe must record exactly one outcome"
    rec = captured_records[0]
    assert rec["ok"] is False
    assert rec["auth_mode"] == "vertex_ai_service_account", (
        f"Expected auth_mode forwarded from vertex_services._AUTH_MODE, "
        f"got {rec.get('auth_mode')!r}. Without this, /healthz/ai shows "
        f"auth_mode: null on timeout — operators can't distinguish a "
        f"no-credentials deploy from a slow upstream."
    )
    assert rec["via_cf_gateway"] is True
    assert rec["source"] == "startup"
    assert "timed out" in rec["reason"].lower()


def test_exception_path_captures_auth_mode_and_gateway(monkeypatch):
    """Same regression as the timeout test, but for the generic-exception
    branch (e.g. TLS handshake error). The cache must still record which
    auth path was attempted.
    """
    probe, captured_logs, ns = _extract_probe_callable()
    captured_records = _extract_record_calls(monkeypatch)

    import sys
    import types
    fake_vs = types.ModuleType("vertex_services")
    fake_vs._AUTH_MODE = "google_ai_studio_api_key"  # type: ignore[attr-defined]
    fake_vs._CF_GW_ENABLED = False  # type: ignore[attr-defined]

    async def _boom():
        raise RuntimeError("TLS handshake refused")

    fake_vs.health_check = _boom  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "vertex_services", fake_vs)

    _run(probe())

    assert len(captured_records) == 1
    rec = captured_records[0]
    assert rec["ok"] is False
    assert rec["auth_mode"] == "google_ai_studio_api_key"
    assert rec["via_cf_gateway"] is False
    assert "TLS handshake refused" in rec["reason"]


def test_probe_timeout_is_configurable_via_env(monkeypatch):
    """The cold-start probe budget must be configurable. The default of
    15s replaced the legacy 5s (which was too tight for two sequential
    HTTPS calls + OAuth2 token exchange on a cold container). When
    ``VERTEX_STARTUP_PROBE_TIMEOUT_S`` is set, the probe must honor it
    so operators can tune up/down per environment without a code change.
    """
    probe, captured_logs, ns = _extract_probe_callable()

    import sys
    import types
    fake_vs = types.ModuleType("vertex_services")
    fake_vs._AUTH_MODE = "disabled"  # type: ignore[attr-defined]
    fake_vs._CF_GW_ENABLED = False  # type: ignore[attr-defined]

    seen_timeouts: list[float] = []
    real_wait_for = asyncio.wait_for

    async def _spy_wait_for(coro, timeout):
        seen_timeouts.append(float(timeout))
        # Run the coro to completion so the probe path is exercised end-to-end.
        return await real_wait_for(coro, timeout=1.0)

    async def _ok():
        return {
            "embeddings": True,
            "generation": True,
            "auth_mode": "disabled",
            "via_cf_gateway": False,
        }

    fake_vs.health_check = _ok  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "vertex_services", fake_vs)
    # Stub the cache so we don't depend on the real module here.
    _extract_record_calls(monkeypatch)
    monkeypatch.setattr(asyncio, "wait_for", _spy_wait_for)
    monkeypatch.setenv("VERTEX_STARTUP_PROBE_TIMEOUT_S", "27")

    _run(probe())

    assert seen_timeouts, "Probe must call asyncio.wait_for"
    assert seen_timeouts[0] == 27.0, (
        f"Expected probe to honor VERTEX_STARTUP_PROBE_TIMEOUT_S=27, "
        f"got timeout={seen_timeouts[0]!r}."
    )


def test_probe_timeout_default_is_15_seconds(monkeypatch):
    """Without the env override the default budget must be 15s, not the
    old 5s — the audit found 5s was insufficient for cold-start cases.
    """
    probe, captured_logs, ns = _extract_probe_callable()

    import sys
    import types
    fake_vs = types.ModuleType("vertex_services")
    fake_vs._AUTH_MODE = "disabled"  # type: ignore[attr-defined]
    fake_vs._CF_GW_ENABLED = False  # type: ignore[attr-defined]

    seen_timeouts: list[float] = []
    real_wait_for = asyncio.wait_for

    async def _spy_wait_for(coro, timeout):
        seen_timeouts.append(float(timeout))
        return await real_wait_for(coro, timeout=1.0)

    async def _ok():
        return {"embeddings": True, "generation": True}

    fake_vs.health_check = _ok  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "vertex_services", fake_vs)
    _extract_record_calls(monkeypatch)
    monkeypatch.setattr(asyncio, "wait_for", _spy_wait_for)
    monkeypatch.delenv("VERTEX_STARTUP_PROBE_TIMEOUT_S", raising=False)

    _run(probe())

    assert seen_timeouts and seen_timeouts[0] == 15.0, (
        f"Default startup-probe timeout must be 15s, got {seen_timeouts!r}."
    )
