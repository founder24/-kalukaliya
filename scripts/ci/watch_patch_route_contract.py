#!/usr/bin/env python3
"""File-watch wrapper for check_patch_route_contract.py.

Polls routes/admin_edge_*.py every second for mtime changes and
re-runs the contract guard on every detected save.  Violations are
printed immediately so they surface in the workflow console within
seconds of writing the offending code.

Persistent-failure alerting (Task #92)
---------------------------------------
If the check fails for *ALERT_AFTER_POLLS* consecutive polls without a
passing run (default 300 = 5 minutes at a 1-second poll interval), the
watcher:

  1. Writes a flag file at _BROKEN_FLAG_PATH so other tooling can detect
     the persistent breakage without parsing console output.
  2. Prints a prominent "PERSISTENT VIOLATION ALERT" block to stderr.

A single passing run resets the consecutive-failure counter and removes
the flag file (if present).  The flag path can be overridden at runtime
by setting the PATCH_CONTRACT_BROKEN_FLAG env var.

Usage (normally invoked by the patch_contract_guard workflow):
    python scripts/ci/watch_patch_route_contract.py

Environment variables:
    PATCH_CONTRACT_POLL_INTERVAL  — float seconds between polls (default 1.0)
    PATCH_CONTRACT_ALERT_POLLS    — consecutive failures before alert (default 300)
    PATCH_CONTRACT_BROKEN_FLAG    — path of the flag file written on alert

No third-party packages required — pure stdlib only.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

_ROUTES_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "artifacts"
    / "syrabit-backend"
    / "routes"
)
_CHECK_SCRIPT = Path(__file__).resolve().parent / "check_patch_route_contract.py"

_POLL_INTERVAL: float = float(os.environ.get("PATCH_CONTRACT_POLL_INTERVAL", "1.0"))
_ALERT_AFTER_POLLS: int = int(os.environ.get("PATCH_CONTRACT_ALERT_POLLS", "300"))
_BROKEN_FLAG_PATH: Path = Path(
    os.environ.get(
        "PATCH_CONTRACT_BROKEN_FLAG",
        str(Path(__file__).resolve().parent.parent.parent / ".local" / "patch_contract_broken.flag"),
    )
)


def _collect_mtimes(routes_dir: Path) -> dict[Path, float]:
    return {
        p: p.stat().st_mtime
        for p in sorted(routes_dir.glob("admin_edge_*.py"))
        if p.is_file()
    }


def _write_flag(consecutive: int) -> None:
    """Write the broken-flag file so external watchers can detect the alert."""
    try:
        _BROKEN_FLAG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _BROKEN_FLAG_PATH.write_text(
            f"patch_contract_guard: BROKEN for {consecutive} consecutive polls "
            f"(~{consecutive * _POLL_INTERVAL:.0f}s)\n"
        )
    except OSError as exc:
        print(
            f"[watch_patch_route_contract] WARNING: could not write flag file "
            f"{_BROKEN_FLAG_PATH}: {exc}",
            file=sys.stderr,
            flush=True,
        )


def _clear_flag() -> None:
    """Remove the broken-flag file on a passing run."""
    try:
        _BROKEN_FLAG_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def _print_persistent_alert(consecutive: int) -> None:
    elapsed = int(consecutive * _POLL_INTERVAL)
    msg = (
        f"\n{'=' * 72}\n"
        f"  PERSISTENT VIOLATION ALERT — patch_contract_guard\n"
        f"  The PATCH-contract check has been failing for {elapsed}s "
        f"({consecutive} consecutive polls).\n"
        f"  Fix the missing @patch_route_contract decorator(s) listed above\n"
        f"  and save the file to clear this alert.\n"
        f"  Flag file: {_BROKEN_FLAG_PATH}\n"
        f"{'=' * 72}\n"
    )
    print(msg, file=sys.stderr, flush=True)


def run_check() -> bool:
    """Run the contract check.  Returns True if the check passed (exit 0)."""
    result = subprocess.run(
        [sys.executable, str(_CHECK_SCRIPT)],
        capture_output=False,
    )
    return result.returncode == 0


def _handle_result(passed: bool, consecutive_failures: int) -> int:
    """Update failure counter, manage flag file and alerts.

    Returns the updated consecutive_failures value.
    """
    if passed:
        if consecutive_failures > 0:
            _clear_flag()
            print(
                "[watch_patch_route_contract] check passed — alert cleared.",
                flush=True,
            )
        else:
            print("[watch_patch_route_contract] watching for changes …", flush=True)
        return 0

    consecutive_failures += 1
    print(
        "\n[watch_patch_route_contract] ^^^ fix the violation(s) above, "
        "then save again to re-check.\n",
        flush=True,
    )
    if consecutive_failures >= _ALERT_AFTER_POLLS:
        _write_flag(consecutive_failures)
        _print_persistent_alert(consecutive_failures)
    return consecutive_failures


def main() -> None:
    if not _ROUTES_DIR.is_dir():
        print(
            f"[watch_patch_route_contract] routes directory not found: {_ROUTES_DIR}",
            file=sys.stderr,
        )
        sys.exit(1)

    print(
        f"[watch_patch_route_contract] watching {_ROUTES_DIR}/admin_edge_*.py "
        f"(poll {_POLL_INTERVAL}s, alert after {_ALERT_AFTER_POLLS} consecutive failures) …",
        flush=True,
    )

    consecutive_failures = 0
    passed = run_check()
    consecutive_failures = _handle_result(passed, consecutive_failures)

    prev_mtimes = _collect_mtimes(_ROUTES_DIR)

    while True:
        time.sleep(_POLL_INTERVAL)
        current_mtimes = _collect_mtimes(_ROUTES_DIR)

        if current_mtimes != prev_mtimes:
            changed = [
                p.name
                for p in set(current_mtimes) | set(prev_mtimes)
                if current_mtimes.get(p) != prev_mtimes.get(p)
            ]
            print(
                f"\n[watch_patch_route_contract] change detected: {', '.join(sorted(changed))}",
                flush=True,
            )
            passed = run_check()
            consecutive_failures = _handle_result(passed, consecutive_failures)
            prev_mtimes = current_mtimes


if __name__ == "__main__":
    main()
