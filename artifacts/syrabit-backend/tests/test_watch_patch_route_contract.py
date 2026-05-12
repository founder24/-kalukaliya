"""Tests for scripts/ci/watch_patch_route_contract.py alerting logic (Task #92).

Verifies that:
  - _handle_result returns 0 and clears the flag when the check passes
  - _handle_result increments the counter when the check fails
  - _write_flag writes the expected flag file content
  - _clear_flag removes the flag file
  - No alert is triggered below the threshold
  - An alert IS triggered once consecutive failures >= _ALERT_AFTER_POLLS
  - A passing run after an alert resets the counter and removes the flag
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

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
# Tests
# ---------------------------------------------------------------------------


def test_handle_result_pass_from_zero_returns_zero(tmp_path, monkeypatch, capsys):
    mod = _load_watcher()
    monkeypatch.setattr(mod, "_BROKEN_FLAG_PATH", tmp_path / "broken.flag")
    result = mod._handle_result(passed=True, consecutive_failures=0)
    assert result == 0


def test_handle_result_fail_increments_counter(tmp_path, monkeypatch, capsys):
    mod = _load_watcher()
    monkeypatch.setattr(mod, "_BROKEN_FLAG_PATH", tmp_path / "broken.flag")
    result = mod._handle_result(passed=False, consecutive_failures=0)
    assert result == 1


def test_handle_result_fail_increments_from_nonzero(tmp_path, monkeypatch, capsys):
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


def test_module_imports_cleanly():
    mod = _load_watcher()
    assert callable(mod._handle_result)
    assert callable(mod._write_flag)
    assert callable(mod._clear_flag)
    assert callable(mod.run_check)
    assert callable(mod.main)
