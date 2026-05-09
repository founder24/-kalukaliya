#!/usr/bin/env python3
"""Repo-root umbrella shim for the canonical specialist-delegation guard.

Follow-up #25. The real implementation lives at
``artifacts/syrabit-backend/scripts/ci/check_canonical_delegation.py``
(Task #559's umbrella + dead-provider bank). Several runbooks, the
2026 architecture lock, and CI workflows refer to the script by its
repo-root path; this shim makes that path real without duplicating the
~870 lines of enforcement logic.

Behaviour: ``execv`` the backend script with the same Python interpreter
and forward every argv. The backend script's exit code is the shim's
exit code. No Python imports happen here, so the shim cannot
accidentally pin or mask the backend script's behaviour.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CANONICAL = (
    REPO_ROOT
    / "artifacts"
    / "syrabit-backend"
    / "scripts"
    / "ci"
    / "check_canonical_delegation.py"
)


def main() -> int:
    if not CANONICAL.is_file():
        sys.stderr.write(
            "check_canonical_delegation: canonical script missing at "
            f"{CANONICAL}\n"
            "Restore artifacts/syrabit-backend/scripts/ci/check_canonical_delegation.py "
            "or update this shim.\n"
        )
        return 2
    argv = [sys.executable, str(CANONICAL), *sys.argv[1:]]
    os.execv(sys.executable, argv)
    return 0  # unreachable; execv replaces the process


if __name__ == "__main__":
    raise SystemExit(main())
