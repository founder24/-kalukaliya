"""Tests for scripts/ci/watch_patch_route_contract.py alerting logic (Task #92).

Verifies that:
  - _handle_result returns 0 and clears the flag when the check passes
  - _handle_result increments the counter when the check fails
  - _write_flag writes the expected flag file content
  - _clear_flag removes the flag file
  - No alert is triggered below the threshold
  - An alert IS triggered once consecutive failures >= _ALERT_AFTER_POLLS
  - A passing run after an alert resets the counter and removes the flag
  - _run_one_poll advances the failure counter WITHOUT file-save changes
    (the key scenario: developer steps away while broken)
  - _run_one_poll does NOT run the check when all clear and no changes
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch as mock_patch

import pytest


# ---------------------------------------------------------------------------
# Load the watcher script as a module (lives outside the backend package tree)
# ---------------------------------------------------------------------------

_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "scripts"
    / "ci"
    / "watch_patch_route_contract.py"
)


def _load_watcher():
    spec = importlib.util.spec_from_file_location(
        "watch_patch_route_contract", _SCRIPT_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# _handle_result tests
# ---------------------------------------------------------------------------


def test_handle_result_pass_from_zero_returns_zero(tmp_path, monkeypatch):
    mod = _load_watcher()
    monkeypatch.setattr(mod, "_BROKEN_FLAG_PATH", tmp_path / "broken.flag")
    result = mod._handle_result(passed=True, consecutive_failures=0)
    assert result == 0


def test_handle_result_fail_increments_counter(tmp_path, monkeypatch):
    mod = _load_watcher()
    monkeypatch.setattr(mod, "_BROKEN_FLAG_PATH", tmp_path / "broken.flag")
    result = mod._handle_result(passed=False, consecutive_failures=0)
    assert result == 1


def test_handle_result_fail_increments_from_nonzero(tmp_path, monkeypatch):
    mod = _load_watcher()
    monkeypatch.setattr(mod, "_BROKEN_FLAG_PATH", tmp_path / "broken.flag")
    result = mod._handle_result(passed=False, consecutive_failures=14)
    assert result == 15


def test_handle_result_below_threshold_does_not_write_flag(tmp_path, monkeypatch):
    mod = _load_watcher()
    flag = tmp_path / "broken.flag"
    monkeypatch.setattr(mod, "_BROKEN_FLAG_PATH", flag)
    monkeypatch.setattr(mod, "_ALERT_AFTER_POLLS", 300)
    mod._handle_result(passed=False, consecutive_failures=50)
    assert not flag.exists()


def test_handle_result_at_threshold_writes_flag(tmp_path, monkeypatch):
    mod = _load_watcher()
    flag = tmp_path / "broken.flag"
    monkeypatch.setattr(mod, "_BROKEN_FLAG_PATH", flag)
    monkeypatch.setattr(mod, "_ALERT_AFTER_POLLS", 5)
    mod._handle_result(passed=False, consecutive_failures=4)
    assert flag.exists()


def test_handle_result_flag_content_mentions_poll_count(tmp_path, monkeypatch):
    mod = _load_watcher()
    flag = tmp_path / "broken.flag"
    monkeypatch.setattr(mod, "_BROKEN_FLAG_PATH", flag)
    monkeypatch.setattr(mod, "_ALERT_AFTER_POLLS", 5)
    mod._handle_result(passed=False, consecutive_failures=4)
    content = flag.read_text()
    assert "5" in content
    assert "BROKEN" in content


def test_handle_result_pass_after_alert_clears_flag(tmp_path, monkeypatch):
    mod = _load_watcher()
    flag = tmp_path / "broken.flag"
    monkeypatch.setattr(mod, "_BROKEN_FLAG_PATH", flag)
    monkeypatch.setattr(mod, "_ALERT_AFTER_POLLS", 5)
    mod._handle_result(passed=False, consecutive_failures=4)
    assert flag.exists()
    result = mod._handle_result(passed=True, consecutive_failures=5)
    assert result == 0
    assert not flag.exists()


def test_handle_result_pass_after_alert_resets_counter(tmp_path, monkeypatch):
    mod = _load_watcher()
    flag = tmp_path / "broken.flag"
    monkeypatch.setattr(mod, "_BROKEN_FLAG_PATH", flag)
    result = mod._handle_result(passed=True, consecutive_failures=99)
    assert result == 0


# ---------------------------------------------------------------------------
# _write_flag / _clear_flag tests
# ---------------------------------------------------------------------------


def test_write_flag_creates_file_with_expected_content(tmp_path, monkeypatch):
    mod = _load_watcher()
    flag = tmp_path / "subdir" / "broken.flag"
    monkeypatch.setattr(mod, "_BROKEN_FLAG_PATH", flag)
    monkeypatch.setattr(mod, "_POLL_INTERVAL", 1.0)
    mod._write_flag(42)
    assert flag.exists()
    content = flag.read_text()
    assert "42" in content
    assert "BROKEN" in content


def test_clear_flag_removes_existing_flag(tmp_path, monkeypatch):
    mod = _load_watcher()
    flag = tmp_path / "broken.flag"
    flag.write_text("broken\n")
    monkeypatch.setattr(mod, "_BROKEN_FLAG_PATH", flag)
    mod._clear_flag()
    assert not flag.exists()


def test_clear_flag_is_idempotent_when_flag_absent(tmp_path, monkeypatch):
    mod = _load_watcher()
    flag = tmp_path / "nonexistent.flag"
    monkeypatch.setattr(mod, "_BROKEN_FLAG_PATH", flag)
    mod._clear_flag()
    assert not flag.exists()


# ---------------------------------------------------------------------------
# _run_one_poll integration tests
#
# These tests verify the core correctness requirement:
# the counter MUST advance on every poll while in failing state, even when
# no watched file changes (the "developer stepped away" scenario).
# ---------------------------------------------------------------------------


def test_run_one_poll_no_changes_all_clear_does_not_call_run_check(
    tmp_path, monkeypatch
):
    """When passing (consecutive_failures=0) and no mtimes change, skip the check."""
    mod = _load_watcher()
    flag = tmp_path / "broken.flag"
    monkeypatch.setattr(mod, "_BROKEN_FLAG_PATH", flag)

    fake_mtimes = {Path("admin_edge_x.py"): 1.0}

    calls = []

    def fake_collect(routes_dir):
        return dict(fake_mtimes)

    def fake_run_check():
        calls.append(1)
        return True

    monkeypatch.setattr(mod, "_collect_mtimes", fake_collect)
    monkeypatch.setattr(mod, "run_check", fake_run_check)

    new_cf, new_mtimes = mod._run_one_poll(
        consecutive_failures=0,
        prev_mtimes=dict(fake_mtimes),
        routes_dir=tmp_path,
    )

    assert calls == [], "run_check must NOT be called when all-clear and no file change"
    assert new_cf == 0


def test_run_one_poll_no_file_change_but_broken_calls_run_check(tmp_path, monkeypatch):
    """When in failing state and no mtimes change, run_check IS called."""
    mod = _load_watcher()
    flag = tmp_path / "broken.flag"
    monkeypatch.setattr(mod, "_BROKEN_FLAG_PATH", flag)
    monkeypatch.setattr(mod, "_ALERT_AFTER_POLLS", 300)

    fake_mtimes = {Path("admin_edge_x.py"): 1.0}
    calls = []

    def fake_collect(routes_dir):
        return dict(fake_mtimes)

    def fake_run_check():
        calls.append(1)
        return False

    monkeypatch.setattr(mod, "_collect_mtimes", fake_collect)
    monkeypatch.setattr(mod, "run_check", fake_run_check)

    new_cf, _ = mod._run_one_poll(
        consecutive_failures=1,
        prev_mtimes=dict(fake_mtimes),
        routes_dir=tmp_path,
    )

    assert calls == [1], "run_check MUST be called when in failing state, even without a file change"
    assert new_cf == 2


def test_run_one_poll_persistent_failure_reaches_threshold_and_writes_flag(
    tmp_path, monkeypatch
):
    """After ALERT_AFTER_POLLS iterations with no file changes, flag is written."""
    mod = _load_watcher()
    flag = tmp_path / "broken.flag"
    monkeypatch.setattr(mod, "_BROKEN_FLAG_PATH", flag)
    monkeypatch.setattr(mod, "_ALERT_AFTER_POLLS", 5)
    monkeypatch.setattr(mod, "_POLL_INTERVAL", 1.0)

    fake_mtimes = {Path("admin_edge_x.py"): 1.0}

    def fake_collect(routes_dir):
        return dict(fake_mtimes)

    def fake_run_check():
        return False

    monkeypatch.setattr(mod, "_collect_mtimes", fake_collect)
    monkeypatch.setattr(mod, "run_check", fake_run_check)

    consecutive_failures = 1
    for _ in range(4):
        consecutive_failures, _ = mod._run_one_poll(
            consecutive_failures=consecutive_failures,
            prev_mtimes=dict(fake_mtimes),
            routes_dir=tmp_path,
        )

    assert consecutive_failures == 5
    assert flag.exists(), "Flag file must be written once the alert threshold is reached"
    content = flag.read_text()
    assert "BROKEN" in content


def test_run_one_poll_pass_after_persistent_failure_clears_flag(tmp_path, monkeypatch):
    """A single passing poll after threshold resets the counter and removes the flag."""
    mod = _load_watcher()
    flag = tmp_path / "broken.flag"
    flag.write_text("BROKEN\n")
    monkeypatch.setattr(mod, "_BROKEN_FLAG_PATH", flag)
    monkeypatch.setattr(mod, "_ALERT_AFTER_POLLS", 5)

    fake_mtimes = {Path("admin_edge_x.py"): 1.0}

    def fake_collect(routes_dir):
        return dict(fake_mtimes)

    def fake_run_check():
        return True

    monkeypatch.setattr(mod, "_collect_mtimes", fake_collect)
    monkeypatch.setattr(mod, "run_check", fake_run_check)

    new_cf, _ = mod._run_one_poll(
        consecutive_failures=5,
        prev_mtimes=dict(fake_mtimes),
        routes_dir=tmp_path,
    )

    assert new_cf == 0
    assert not flag.exists(), "Flag file must be removed when check passes"


# ---------------------------------------------------------------------------
# Module smoke test
# ---------------------------------------------------------------------------


def test_module_imports_cleanly():
    mod = _load_watcher()
    assert callable(mod._handle_result)
    assert callable(mod._write_flag)
    assert callable(mod._clear_flag)
    assert callable(mod._run_one_poll)
    assert callable(mod.run_check)
    assert callable(mod.main)
