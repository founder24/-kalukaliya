"""Task #677 — verify the periodic Gemini health probe.

The startup probe (Task #667) only catches credential/gateway misconfig
at deploy time. This periodic loop catches Gemini failures *after* boot
(revoked key, AI Gateway throttling, regional outage) and routes them
through the existing alert pipeline so on-call gets paged.

Three guarantees:

1. **Registration**: ``lifespan`` in ``server.py`` schedules
   ``_vertex_periodic_probe_loop`` as a background task.
2. **Single-fire per failure run**: when ``health_check()`` reports a
   failure on >=2 consecutive iterations, the loop dispatches an alert
   via ``metrics._dispatch_alert`` exactly once for that contiguous run.
3. **Counter resets on success**: after a healthy probe, the next
   failure run is allowed to fire a fresh alert.

We extract the loop function from ``server.py`` via ``ast`` and exec it
in an isolated namespace with stubbed ``vertex_services``,
``metrics._dispatch_alert``, and a sleep that breaks out after a fixed
number of iterations — same approach used by
``test_vertex_startup_probe.py``.
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


def _server_source() -> str:
    return SERVER_PY.read_text(encoding="utf-8")


def _server_module_ast() -> ast.Module:
    return ast.parse(_server_source())


def _find_function(tree: ast.Module, name: str) -> ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"async function {name!r} not found in server.py")


# ---------------------------------------------------------------------------
# 1. Hook registration
# ---------------------------------------------------------------------------

def test_lifespan_schedules_vertex_periodic_probe_loop():
    """``lifespan`` must schedule ``_vertex_periodic_probe_loop`` as a
    background task, otherwise mid-day Gemini outages would never page
    on-call.
    """
    tree = _server_module_ast()
    lifespan = _find_function(tree, "lifespan")
    src = ast.unparse(lifespan)
    # Task #332 — registration moved from raw `asyncio.create_task` to
    # the takeover-aware `_aca_create_task(..., key="vertex-periodic-probe")`
    # wrapper so a single env flip switches between in-process and
    # ACA Job execution. Either form satisfies the contract.
    assert (
        "asyncio.create_task(_vertex_periodic_probe_loop())" in src
        or "_aca_create_task(_vertex_periodic_probe_loop(), key='vertex-periodic-probe')" in src
    ), (
        "Expected lifespan() to schedule _vertex_periodic_probe_loop "
        "(via asyncio.create_task or _aca_create_task); without it "
        "Gemini outages after boot stay silent."
    )


def test_periodic_probe_loop_is_async():
    tree = _server_module_ast()
    fn = _find_function(tree, "_vertex_periodic_probe_loop")
    assert isinstance(fn, ast.AsyncFunctionDef)


# ---------------------------------------------------------------------------
# 2. Behavioural tests: extract the loop and run it with stubs
# ---------------------------------------------------------------------------

def _extract_loop(stub_vertex, dispatch_calls, monkeypatch, max_iterations=4,
                  interval_s=600):
    """Return a callable for the periodic loop with stubbed dependencies.

    - ``vertex_services.health_check`` is replaced with ``stub_vertex``.
    - ``metrics._dispatch_alert`` records each call into ``dispatch_calls``.
    - ``asyncio.sleep`` is patched to a no-op that aborts the loop after
      ``max_iterations`` ticks via ``asyncio.CancelledError``.
    """
    tree = _server_module_ast()
    fn_node = _find_function(tree, "_vertex_periodic_probe_loop")
    src = textwrap.dedent(ast.get_source_segment(_server_source(), fn_node))

    stub_logger = logging.getLogger("syrabit.test.vertex_periodic_probe")
    stub_logger.handlers.clear()
    stub_logger.setLevel(logging.DEBUG)
    stub_logger.propagate = False
    captured: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record):  # noqa: D401
            captured.append(record)

    stub_logger.addHandler(_Capture())

    # Stub vertex_services module via monkeypatch.setitem so the original
    # entry (or absence) is restored when the test exits — avoids
    # cross-test sys.modules pollution.
    import sys
    import types
    vmod = types.ModuleType("vertex_services")
    vmod.health_check = stub_vertex  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "vertex_services", vmod)

    # Stub metrics._dispatch_alert by registering a stand-in module
    # (also via monkeypatch so it's restored automatically).
    mmod = types.ModuleType("metrics")

    async def _fake_dispatch(alert_type, title, body, threshold_snapshot=None,
                             force=False, mark_synthetic=False):
        dispatch_calls.append({
            "alert_type": alert_type,
            "title": title,
            "body": body,
            "threshold_snapshot": threshold_snapshot,
        })
        return {}

    mmod._dispatch_alert = _fake_dispatch  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "metrics", mmod)

    # Sleep stub that aborts after N ticks. The loop calls sleep before
    # the first probe and after each iteration, so we count ticks and
    # raise once we've exercised the desired number of iterations.
    state = {"ticks": 0}

    async def _fake_sleep(_seconds):
        state["ticks"] += 1
        if state["ticks"] > max_iterations:
            raise asyncio.CancelledError()
        return None

    namespace: dict[str, Any] = {
        "logger": stub_logger,
        "asyncio": _AsyncioShim(_fake_sleep),
        "os": __import__("os"),
        "_VERTEX_PROBE_INTERVAL_S": interval_s,
        "_VERTEX_PROBE_FAILURE_THRESHOLD": 2,
    }
    exec(compile(src, str(SERVER_PY), "exec"), namespace)
    return namespace["_vertex_periodic_probe_loop"], captured


class _AsyncioShim:
    """Wrap stdlib asyncio so ``sleep`` can be swapped without touching
    the global module (other tests in the suite rely on the real
    ``asyncio.sleep``).
    """
    def __init__(self, fake_sleep):
        self._sleep = fake_sleep
        self.TimeoutError = asyncio.TimeoutError
        self.CancelledError = asyncio.CancelledError
        self.wait_for = asyncio.wait_for

    async def sleep(self, seconds):
        return await self._sleep(seconds)


def _run(coro):
    try:
        asyncio.run(coro)
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# Failure runs
# ---------------------------------------------------------------------------

def test_loop_fires_alert_exactly_once_per_failure_run(monkeypatch):
    """When health_check fails on every iteration, the alert dispatches
    exactly once — on the transition to 2 consecutive failures — not
    once per tick.
    """
    dispatch_calls: list[dict] = []

    async def always_fail():
        return {"embeddings": False, "generation": True, "reason": "key revoked"}

    loop_fn, captured = _extract_loop(always_fail, dispatch_calls, monkeypatch,
                                      max_iterations=5)
    _run(loop_fn())

    assert len(dispatch_calls) == 1, (
        f"Expected exactly one dispatch per failure run, got "
        f"{len(dispatch_calls)}: {dispatch_calls}"
    )
    call = dispatch_calls[0]
    assert call["alert_type"] == "vertex_health_degraded"
    assert "consecutive" in call["body"].lower()


def test_loop_alert_carries_threshold_snapshot(monkeypatch):
    dispatch_calls: list[dict] = []

    async def always_fail():
        return {"embeddings": False, "generation": False, "reason": "boom"}

    loop_fn, _ = _extract_loop(always_fail, dispatch_calls, monkeypatch, max_iterations=3)
    _run(loop_fn())

    assert len(dispatch_calls) == 1
    snap = dispatch_calls[0]["threshold_snapshot"]
    assert snap["metric"] == "vertex_consecutive_failures"
    assert snap["value"] == 2
    assert snap["actual"] >= 2


def test_loop_does_not_alert_on_single_failure(monkeypatch):
    """A single transient failure must NOT page on-call — only sustained
    failures (>=2 consecutive) should.
    """
    dispatch_calls: list[dict] = []
    seq = iter([
        {"embeddings": False, "generation": True, "reason": "blip"},  # fail
        {"embeddings": True, "generation": True},                     # recover
        {"embeddings": True, "generation": True},
    ])

    async def flaky():
        return next(seq)

    loop_fn, _ = _extract_loop(flaky, dispatch_calls, monkeypatch, max_iterations=4)
    _run(loop_fn())

    assert dispatch_calls == [], (
        f"A single failure must not page; got {dispatch_calls}"
    )


def test_loop_no_alert_when_healthy(monkeypatch):
    dispatch_calls: list[dict] = []

    async def always_ok():
        return {"embeddings": True, "generation": True}

    loop_fn, captured = _extract_loop(always_ok, dispatch_calls, monkeypatch,
                                      max_iterations=4)
    _run(loop_fn())

    assert dispatch_calls == []
    errors = [r for r in captured if r.levelno == logging.ERROR]
    assert not errors


def test_loop_resets_after_recovery_and_can_fire_again(monkeypatch):
    """After a success resets the counter, a *new* sustained failure run
    is allowed to fire a fresh alert. This guards against a single
    flapping outage masking a follow-up real outage.

    Task #690 also added an auto-recovery alert on the success that
    closes a fired run, so each cycle is fire→recover.
    """
    dispatch_calls: list[dict] = []
    seq = iter([
        {"embeddings": False, "generation": True, "reason": "outage-1"},
        {"embeddings": False, "generation": True, "reason": "outage-1"},  # alert #1
        {"embeddings": True, "generation": True},                          # recovery #1
        {"embeddings": False, "generation": True, "reason": "outage-2"},
        {"embeddings": False, "generation": True, "reason": "outage-2"},  # alert #2
    ])

    async def flapping():
        return next(seq)

    loop_fn, _ = _extract_loop(flapping, dispatch_calls, monkeypatch, max_iterations=6)
    _run(loop_fn())

    types_fired = [c["alert_type"] for c in dispatch_calls]
    assert types_fired == [
        "vertex_health_degraded",
        "vertex_health_recovered",
        "vertex_health_degraded",
    ], f"Expected fire→recover→fire across two outages, got {types_fired}"


# ---------------------------------------------------------------------------
# Task #690 — Auto-recovery notification
# ---------------------------------------------------------------------------


def test_loop_dispatches_recovery_alert_after_fired_alert(monkeypatch):
    """fire → recover → recovery alert dispatched exactly once.

    Sequence: 2 failures (page on-call) → 1 success (close the loop with
    a single ``vertex_health_recovered`` message) → another success
    (must NOT re-send the recovery message; the run is already closed).
    """
    dispatch_calls: list[dict] = []
    seq = iter([
        {"embeddings": False, "generation": True, "reason": "outage"},
        {"embeddings": False, "generation": True, "reason": "outage"},  # alert fires
        {"embeddings": True, "generation": True},                        # recovery fires
        {"embeddings": True, "generation": True},                        # silent
    ])

    async def flapping():
        return next(seq)

    loop_fn, _ = _extract_loop(flapping, dispatch_calls, monkeypatch, max_iterations=5)
    _run(loop_fn())

    types_fired = [c["alert_type"] for c in dispatch_calls]
    assert types_fired == ["vertex_health_degraded", "vertex_health_recovered"], (
        f"Expected exactly fire→recover, got {types_fired}"
    )
    recovery = dispatch_calls[1]
    assert recovery["threshold_snapshot"]["actual"] == 0
    assert recovery["threshold_snapshot"]["metric"] == "vertex_consecutive_failures"
    assert "healthy again" in recovery["body"].lower()


def test_loop_does_not_dispatch_recovery_when_no_alert_fired(monkeypatch):
    """A single transient failure followed by recovery must NOT send a
    "recovered" message — there was nothing to recover from from the
    on-call's perspective (no degraded alert was ever sent).
    """
    dispatch_calls: list[dict] = []
    seq = iter([
        {"embeddings": False, "generation": True, "reason": "blip"},  # 1 failure (no page)
        {"embeddings": True, "generation": True},                      # recover silently
        {"embeddings": True, "generation": True},
    ])

    async def flaky():
        return next(seq)

    loop_fn, _ = _extract_loop(flaky, dispatch_calls, monkeypatch, max_iterations=4)
    _run(loop_fn())

    assert dispatch_calls == [], (
        f"No alert was fired, so no recovery should be sent; got {dispatch_calls}"
    )


def test_loop_recovery_does_not_repeat_on_sustained_health(monkeypatch):
    """After fire → recover → recover → recover, recovery must dispatch
    exactly once. Flag must clear so a *second* outage cycle still
    triggers its own fire→recover pair.
    """
    dispatch_calls: list[dict] = []
    seq = iter([
        {"embeddings": False, "generation": True, "reason": "outage-1"},
        {"embeddings": False, "generation": True, "reason": "outage-1"},  # alert #1
        {"embeddings": True, "generation": True},                          # recovery #1
        {"embeddings": True, "generation": True},                          # silent
        {"embeddings": False, "generation": True, "reason": "outage-2"},
        {"embeddings": False, "generation": True, "reason": "outage-2"},  # alert #2
        {"embeddings": True, "generation": True},                          # recovery #2
    ])

    async def flapping():
        return next(seq)

    loop_fn, _ = _extract_loop(flapping, dispatch_calls, monkeypatch, max_iterations=8)
    _run(loop_fn())

    types_fired = [c["alert_type"] for c in dispatch_calls]
    assert types_fired == [
        "vertex_health_degraded",
        "vertex_health_recovered",
        "vertex_health_degraded",
        "vertex_health_recovered",
    ], f"Expected two fire→recover cycles, got {types_fired}"


def test_recovery_alert_uses_force_to_bypass_cooldown(monkeypatch):
    """The matching ``vertex_health_degraded`` may have just consumed
    the 30-min cooldown; the recovery message must bypass it (force=True)
    or admins might wait half an hour for the all-clear.
    """
    dispatch_calls: list[dict] = []
    seq = iter([
        {"embeddings": False, "generation": True, "reason": "outage"},
        {"embeddings": False, "generation": True, "reason": "outage"},
        {"embeddings": True, "generation": True},
    ])

    async def flapping():
        return next(seq)

    loop_fn, _ = _extract_loop(flapping, dispatch_calls, monkeypatch, max_iterations=4)

    # Override _extract_loop's fake dispatch to also capture ``force``.
    # Must run AFTER _extract_loop or its monkeypatch.setitem will
    # win the ``metrics`` slot in sys.modules.
    import sys, types
    mmod = types.ModuleType("metrics")

    async def _fake_dispatch(alert_type, title, body, threshold_snapshot=None,
                             force=False, mark_synthetic=False):
        dispatch_calls.append({
            "alert_type": alert_type,
            "force": force,
        })
        return {}

    mmod._dispatch_alert = _fake_dispatch  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "metrics", mmod)

    _run(loop_fn())

    # First call is the degraded alert (force defaults to False), second
    # is the recovery (must be force=True).
    by_type = {c["alert_type"]: c for c in dispatch_calls if "force" in c}
    assert by_type["vertex_health_recovered"]["force"] is True, (
        f"Recovery must use force=True to bypass cooldown; got {dispatch_calls}"
    )


def test_loop_logs_error_on_health_check_exception(monkeypatch):
    """A raising health_check must be caught and logged; the loop must
    not crash.
    """
    dispatch_calls: list[dict] = []

    async def raising():
        raise RuntimeError("AI Gateway TLS handshake failed")

    loop_fn, captured = _extract_loop(raising, dispatch_calls, monkeypatch,
                                      max_iterations=3)
    _run(loop_fn())

    # 2 consecutive raises → one alert
    assert len(dispatch_calls) == 1
    assert "raised" in dispatch_calls[0]["body"] or "TLS" in dispatch_calls[0]["body"]
    errors = [r for r in captured if r.levelno == logging.ERROR]
    assert errors, "Expected ERROR log lines on health_check exceptions"
