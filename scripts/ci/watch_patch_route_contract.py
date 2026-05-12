#!/usr/bin/env python3
"""File-watch wrapper for check_patch_route_contract.py.

Polls routes/admin_edge_*.py every second for mtime changes and
re-runs the contract guard on every detected save.  Violations are
printed immediately so they surface in the workflow console within
seconds of writing the offending code.

Usage (normally invoked by the patch_contract_guard workflow):
    python scripts/ci/watch_patch_route_contract.py

No third-party packages required — pure stdlib only.
"""
from __future__ import annotations

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
_POLL_INTERVAL = 1.0  # seconds


def _collect_mtimes(routes_dir: Path) -> dict[Path, float]:
    return {
        p: p.stat().st_mtime
        for p in sorted(routes_dir.glob("admin_edge_*.py"))
        if p.is_file()
    }


def _run_check() -> None:
    result = subprocess.run(
        [sys.executable, str(_CHECK_SCRIPT)],
        capture_output=False,
    )
    if result.returncode != 0:
        print(
            "\n[watch_patch_route_contract] ^^^ fix the violation(s) above, "
            "then save again to re-check.\n",
            flush=True,
        )
    else:
        print("[watch_patch_route_contract] watching for changes …", flush=True)


def main() -> None:
    if not _ROUTES_DIR.is_dir():
        print(
            f"[watch_patch_route_contract] routes directory not found: {_ROUTES_DIR}",
            file=sys.stderr,
        )
        sys.exit(1)

    print(
        f"[watch_patch_route_contract] watching {_ROUTES_DIR}/admin_edge_*.py "
        f"(poll interval {_POLL_INTERVAL}s) …",
        flush=True,
    )

    _run_check()

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
            _run_check()
            prev_mtimes = current_mtimes


if __name__ == "__main__":
    main()
