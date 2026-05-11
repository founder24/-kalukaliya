"""Task #90 — regression test for `_check_no_tracked_dist_files`.

The original implementation used `git ls-files artifacts/syrabit/dist*/`
with a trailing slash, which silently returns 0 results in real git
and would have let the guard report clean even when tracked dist
files existed. This test pins the working pathspec (literal directory
names without the trailing-slash wildcard) and the failure-formatting
contract.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_CI = REPO_ROOT / "artifacts" / "syrabit-backend" / "scripts" / "ci"
sys.path.insert(0, str(SCRIPTS_CI))

import check_canonical_delegation as guard  # noqa: E402


class _FakeProc:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


def test_clean_tree_yields_no_failures():
    with mock.patch.object(subprocess, "run", return_value=_FakeProc("")):
        assert guard._check_no_tracked_dist_files() == []


def test_tracked_files_produce_failure_with_remediation_hint():
    fake = "artifacts/syrabit/dist-ssr/a.js\nartifacts/syrabit/dist/b.js\n"
    with mock.patch.object(subprocess, "run", return_value=_FakeProc(fake)):
        out = guard._check_no_tracked_dist_files()
    assert len(out) == 1
    msg = out[0]
    assert "Task #90" in msg
    assert "2 tracked file(s)" in msg
    assert "artifacts/syrabit/dist*/" in msg


def test_pathspec_uses_literal_dirs_without_trailing_slash():
    """The trailing-slash wildcard `dist*/` returns 0 in real git;
    pin the working invocation so the bug can't regress silently."""
    captured: dict = {}

    def _record(cmd, **kw):
        captured["cmd"] = cmd
        return _FakeProc("")

    with mock.patch.object(subprocess, "run", side_effect=_record):
        guard._check_no_tracked_dist_files()

    cmd = captured["cmd"]
    assert "artifacts/syrabit/dist" in cmd
    assert "artifacts/syrabit/dist-ssr" in cmd
    for arg in cmd:
        if isinstance(arg, str) and arg.endswith("dist*/"):
            raise AssertionError(
                "trailing-slash wildcard must not return — git ls-files "
                f"silently emits 0 results for it: {arg!r}"
            )


def test_missing_git_falls_back_to_silence():
    with mock.patch.object(
        subprocess, "run", side_effect=FileNotFoundError("git")
    ):
        assert guard._check_no_tracked_dist_files() == []


def test_nonzero_returncode_falls_back_to_silence():
    with mock.patch.object(
        subprocess, "run", return_value=_FakeProc("", returncode=128)
    ):
        assert guard._check_no_tracked_dist_files() == []
