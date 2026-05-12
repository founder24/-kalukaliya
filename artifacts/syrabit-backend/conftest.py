"""Root-level backend conftest (Task #93).

Adds a session-scoped autouse fixture that runs the PATCH-route contract
guard (``scripts/ci/check_patch_route_contract.py``) once before any test
executes.  If the script returns a non-zero exit code, the entire pytest
session fails immediately with a clear message — no broken PATCH model can
reach the test suite silently.

The script is loaded via ``importlib`` (not a bare import) because it lives
outside the backend package tree, exactly as the existing unit-test for the
script does in ``tests/test_check_patch_route_contract.py``.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "scripts"
    / "ci"
    / "check_patch_route_contract.py"
)


def _load_check_patch_route_contract():
    spec = importlib.util.spec_from_file_location(
        "check_patch_route_contract", _SCRIPT_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session", autouse=True)
def _patch_route_contract_guard():
    """Session-scoped guard: fail fast if any *Patch(BaseModel) class in
    routes/admin_edge_*.py is missing the @patch_route_contract decorator.

    Runs once at the very start of the test session (before the first test).
    A non-zero return value from the CI script means at least one model
    violates the contract; pytest.fail() surfaces the violation immediately
    rather than letting a later, unrelated test expose it by accident.
    """
    mod = _load_check_patch_route_contract()
    exit_code = mod.main([])
    if exit_code != 0:
        pytest.fail(
            "check_patch_route_contract reported unguarded PATCH model(s) — "
            "see output above. Apply @patch_route_contract on the line "
            "immediately preceding each *Patch(BaseModel) class definition "
            "in routes/admin_edge_*.py (see schemas/edge_settings.py for the "
            "full guide). Task #93.",
            pytrace=False,
        )
