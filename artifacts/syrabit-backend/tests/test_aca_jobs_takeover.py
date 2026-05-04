"""Regression test for the `_aca_create_task` / `_aca_jobs_takeover`
contract introduced in Task #332.

The contract:

* When `_aca_jobs_takeover()` returns False (legacy mode, opt-in via
  `RUN_LEGACY_LOOPS=1`), `_aca_create_task(coro, key=...)` MUST schedule
  the coroutine on the running event loop and return the resulting
  `asyncio.Task`. This preserves backwards compat for emergency rollback
  to the in-process scheduler.

* When `_aca_jobs_takeover()` returns True (the default once the
  Container Apps Jobs are deployed), `_aca_create_task(coro, key=...)`
  MUST close the coroutine without scheduling and return None. Returning
  None is what lets every caller in `server.py.lifespan` use the same
  call site for both modes; the module-level handles
  (`_speedup_flush_task`, `_deps_mod._rate_cleanup_task`, etc.) become
  None and the lifespan-shutdown path null-checks them.

If either invariant breaks, the API tier silently double-runs the
periodic loops alongside the ACA Jobs (or worse, never runs them at
all), which is exactly the cutover bug Task #332 is supposed to prevent.

Imports `server` indirectly via AST extraction (same pattern as
`test_vertex_periodic_probe.py`) so the test does not require the
backend's full runtime dependency graph (mongo client, supabase, etc).
"""

import ast
import asyncio
import logging
import os
import pathlib

import pytest


_SERVER_PATH = pathlib.Path(__file__).resolve().parent.parent / "server.py"


def _extract_aca_helpers():
    """Pull `_aca_jobs_takeover` and `_aca_create_task` out of server.py
    and exec them in an isolated namespace with the bare minimum stubs
    they reference (asyncio, os.environ, a logger).
    """
    tree = ast.parse(_SERVER_PATH.read_text())
    wanted = {"_aca_jobs_takeover", "_aca_create_task"}
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    assert {n.name for n in fns} == wanted, (
        f"server.py must define {wanted}; found {[n.name for n in fns]}"
    )
    module = ast.Module(body=fns, type_ignores=[])
    ns: dict = {
        "asyncio": asyncio,
        "os": os,
        "logger": logging.getLogger("test_aca_jobs_takeover"),
    }
    exec(compile(module, str(_SERVER_PATH), "exec"), ns)
    return ns["_aca_jobs_takeover"], ns["_aca_create_task"]


_aca_jobs_takeover, _aca_create_task = _extract_aca_helpers()


@pytest.mark.asyncio
async def test_aca_create_task_returns_none_under_takeover(monkeypatch):
    """Default posture: takeover ON → coroutine is closed, returns None."""
    monkeypatch.delenv("RUN_LEGACY_LOOPS", raising=False)
    assert _aca_jobs_takeover() is True

    ran = {"flag": False}

    async def _coro():
        ran["flag"] = True

    coro_obj = _coro()
    result = _aca_create_task(coro_obj, key="test-takeover")

    assert result is None, (
        "_aca_create_task must return None under takeover so lifespan "
        "module-level handles become None and the shutdown path stays "
        "null-safe."
    )
    # Coroutine must NOT have been awaited (it's owned by the ACA Job).
    assert ran["flag"] is False
    # And it must have been closed cleanly so we don't leak a
    # "coroutine was never awaited" RuntimeWarning.
    with pytest.raises((RuntimeError, StopIteration)):
        coro_obj.send(None)


@pytest.mark.asyncio
async def test_aca_create_task_schedules_under_legacy_opt_out(monkeypatch):
    """`RUN_LEGACY_LOOPS=1` is the ONLY opt-out and MUST restore the
    legacy `asyncio.create_task` semantics."""
    monkeypatch.setenv("RUN_LEGACY_LOOPS", "1")
    assert _aca_jobs_takeover() is False

    ran = asyncio.Event()

    async def _coro():
        ran.set()

    task = _aca_create_task(_coro(), key="test-legacy")
    assert isinstance(task, asyncio.Task)
    await asyncio.wait_for(ran.wait(), timeout=1.0)
    await task


def test_takeover_default_is_on(monkeypatch):
    """Without any env override the takeover must be ON. Guards against
    a regression where someone defaults `RUN_LEGACY_LOOPS=0` or flips
    the polarity of the gate."""
    monkeypatch.delenv("RUN_LEGACY_LOOPS", raising=False)
    assert _aca_jobs_takeover() is True


@pytest.mark.parametrize("falsy", ["", "0", "false", "FALSE", "no"])
def test_takeover_stays_on_for_falsy_legacy_flag(monkeypatch, falsy):
    """Anything other than the literal opt-in `1` keeps takeover ON."""
    monkeypatch.setenv("RUN_LEGACY_LOOPS", falsy)
    assert _aca_jobs_takeover() is True
