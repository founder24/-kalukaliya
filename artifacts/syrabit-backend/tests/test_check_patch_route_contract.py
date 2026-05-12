"""Tests for scripts/ci/check_patch_route_contract.py (Task #90).

Verifies that the CI guard script:
  - returns exit code 1 when a *Patch(BaseModel) class lacks the decorator
  - returns exit code 0 when the decorator is present

Both tests monkeypatch ``_ROUTES_DIR`` so the script scans a temporary
directory with a synthetic ``admin_edge_test.py`` file rather than the
real routes tree.  This keeps the tests self-contained and deterministic
regardless of which actual route files exist in the repo.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Load the CI script as a module.  It lives outside the backend package
# tree so we use importlib rather than a bare import.
# ---------------------------------------------------------------------------

_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "scripts"
    / "ci"
    / "check_patch_route_contract.py"
)


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "check_patch_route_contract", _SCRIPT_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_UNGUARDED_SOURCE = """\
from pydantic import BaseModel


class FooPatch(BaseModel):
    name: str
"""

_GUARDED_SOURCE = """\
from pydantic import BaseModel
from schemas.edge_settings import patch_route_contract

PATCHABLE_KEYS = frozenset({"name"})
CANONICAL_KEYS = frozenset({"name"})


@patch_route_contract(PATCHABLE_KEYS, CANONICAL_KEYS)
class FooPatch(BaseModel):
    name: str
"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_missing_decorator_returns_exit_code_1(tmp_path, monkeypatch):
    """A bare *Patch(BaseModel) class without @patch_route_contract → exit 1."""
    routes_dir = tmp_path / "routes"
    routes_dir.mkdir()
    (routes_dir / "admin_edge_test.py").write_text(_UNGUARDED_SOURCE, encoding="utf-8")

    mod = _load_script()
    monkeypatch.setattr(mod, "_ROUTES_DIR", routes_dir)

    result = mod.main(["--quiet"])
    assert result == 1


def test_decorated_class_returns_exit_code_0(tmp_path, monkeypatch):
    """A *Patch(BaseModel) class with @patch_route_contract present → exit 0."""
    routes_dir = tmp_path / "routes"
    routes_dir.mkdir()
    (routes_dir / "admin_edge_test.py").write_text(_GUARDED_SOURCE, encoding="utf-8")

    mod = _load_script()
    monkeypatch.setattr(mod, "_ROUTES_DIR", routes_dir)

    result = mod.main(["--quiet"])
    assert result == 0
