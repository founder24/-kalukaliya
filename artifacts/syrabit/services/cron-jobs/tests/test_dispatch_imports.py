"""CI guard for services/cron-jobs/run.py DISPATCH integrity.

Phase 4 — Cron port (Task #332).
"""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import inspect
import re
import sys
from pathlib import Path

import pytest


HERE = Path(__file__).resolve()
RUNNER_DIR = HERE.parents[1]
REPO_ROOT  = HERE.parents[3]
RUN_PY     = RUNNER_DIR / "run.py"
TF_FILE    = REPO_ROOT / "infra" / "azure" / "container-apps-jobs.tf"


def _load_run_module():
    spec = importlib.util.spec_from_file_location("cron_runner_run", RUN_PY)
    assert spec and spec.loader, f"could not spec_from_file_location for {RUN_PY}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules["cron_runner_run"] = mod
    spec.loader.exec_module(mod)
    return mod


_RUN = _load_run_module()
DISPATCH: dict[str, tuple[str, int, bool]] = _RUN.DISPATCH


def _tf_job_keys() -> set[str]:
    src = TF_FILE.read_text(encoding="utf-8")
    m = re.search(r"cron_jobs\s*=\s*\{", src)
    assert m, "Could not find `cron_jobs = {` in container-apps-jobs.tf"
    tail = src[m.end():]
    end = re.search(r"^\}", tail, re.MULTILINE)
    block = tail[: end.start() if end else len(tail)]
    return set(re.findall(r'^\s*"([a-z0-9-]+)"\s*=\s*\{', block, re.MULTILINE))


@pytest.mark.parametrize("job_name,spec", sorted(DISPATCH.items()))
def test_dispatch_target_resolves(job_name: str, spec: tuple[str, int, bool]) -> None:
    assert len(spec) == 3, f"DISPATCH[{job_name}] must be (target, timeout, has_boot_stagger)"
    target, timeout_s, has_stagger = spec
    assert isinstance(timeout_s, int) and timeout_s > 0
    assert isinstance(has_stagger, bool), f"has_boot_stagger for {job_name} must be a bool"

    if target == "__adapter:internal_linker":
        from seo_internal_linker import _internal_linker_loop  # type: ignore
        assert asyncio.iscoroutinefunction(_internal_linker_loop)
        return

    if target.startswith("__adapter:lang-"):
        from bench.grounded_recall import per_language_nightly_loops  # type: ignore
        lang = target.split("-", 1)[1]
        factories = per_language_nightly_loops()
        assert lang in factories, f"per_language_nightly_loops missing {lang!r}"
        return

    mod_name, _, fn_name = target.partition(":")
    assert mod_name and fn_name, f"DISPATCH[{job_name}] target {target!r} not in 'module:fn' form"
    mod = importlib.import_module(mod_name)
    assert hasattr(mod, fn_name), f"{mod_name} has no attribute {fn_name!r} (job={job_name})"
    fn = getattr(mod, fn_name)
    assert callable(fn)
    assert asyncio.iscoroutinefunction(fn) or inspect.isasyncgenfunction(fn), (
        f"{mod_name}:{fn_name} must be async (job={job_name})"
    )


def test_terraform_and_dispatch_key_sets_match() -> None:
    tf_keys = _tf_job_keys()
    dispatch_keys = set(DISPATCH.keys())
    missing_in_dispatch = tf_keys - dispatch_keys
    missing_in_tf = dispatch_keys - tf_keys
    assert not missing_in_dispatch, (
        f"In TF but not DISPATCH: {sorted(missing_in_dispatch)}"
    )
    assert not missing_in_tf, (
        f"In DISPATCH but not TF: {sorted(missing_in_tf)}"
    )


def test_one_shot_runner_executes_body_exactly_once() -> None:
    """Regression test for Task #332 reviewer feedback rev #5.

    A canonical legacy loop shaped as
    ``while True: <body>; await asyncio.sleep(N)`` must run its body
    exactly once per dispatch — never zero, never twice — for both
    the with-stagger and no-stagger variants.
    """

    async def _drive(has_stagger: bool, with_initial_stagger: bool) -> int:
        body_runs = 0

        async def loop():
            nonlocal body_runs
            if with_initial_stagger:
                await asyncio.sleep(120)  # boot stagger
            while True:
                body_runs += 1
                # Mid-body retry (short sleep — must pass through):
                await asyncio.sleep(2)
                # Inter-iteration wait (long sleep — exit point):
                await asyncio.sleep(300)

        await _RUN._invoke_loop_once(loop, timeout_s=30, has_boot_stagger=has_stagger,
                                      is_native_one_shot=False)
        return body_runs

    # Loop with stagger + flag set: body runs exactly once.
    assert asyncio.run(_drive(has_stagger=True, with_initial_stagger=True)) == 1
    # Loop with no stagger + flag unset: body runs exactly once.
    assert asyncio.run(_drive(has_stagger=False, with_initial_stagger=False)) == 1
