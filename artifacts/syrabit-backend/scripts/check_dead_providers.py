#!/usr/bin/env python3
"""Task #559 — shim over the canonical-delegation umbrella.

Historically (Tasks #297 / #347 / #491 / #494 / #554) this file owned
the bare-token / banned-vendor scan. Task #559 collapsed every
"who-is-canonical-for-feature-X" rule into a single per-feature
guard at `artifacts/syrabit-backend/scripts/ci/check_canonical_delegation.py`.

This module is kept as a behaviour-preserving entry point so:

  * existing pre-deploy gates (`check_budget_ceiling.py` callers,
    `tests/test_dead_providers_guard.py`, the four-cloud-drift
    workflow) keep working without touching their command lines,
  * `python scripts/check_dead_providers.py` from the syrabit-backend
    working dir continues to print the same OK / FAIL summary,
  * git blame on the historical guard rules still resolves to the
    new umbrella via the import below.

If you came here looking for the actual scan logic, open
`scripts/ci/check_canonical_delegation.py`.
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
# Make the sibling ci/ package importable regardless of cwd.
sys.path.insert(0, str(_HERE / "ci"))

from check_canonical_delegation import main  # noqa: E402  (after sys.path insert)


if __name__ == "__main__":
    sys.exit(main())
