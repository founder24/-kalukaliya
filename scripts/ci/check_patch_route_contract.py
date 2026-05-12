#!/usr/bin/env python3
"""CI guard — every *Patch(BaseModel) class in routes/admin_edge_*.py
must be decorated with @patch_route_contract.

Why this exists
---------------
The ``@patch_route_contract`` decorator (``schemas/edge_settings.py``)
enforces the PATCH field contract at import time.  Without it a new
route model can diverge from its key frozenset silently — the error only
surfaces when a specific test runs, not when the backend starts.

This script makes the pattern *structurally* opt-out: every class whose
name ends in ``Patch`` and that inherits (directly) from ``BaseModel``
in any ``routes/admin_edge_*.py`` file must have the decorator applied on
the immediately preceding non-blank, non-comment line.

Usage
-----
    python scripts/ci/check_patch_route_contract.py          # exits 1 on violation
    python scripts/ci/check_patch_route_contract.py --quiet  # no output on success

The script is intentionally pure-stdlib so it runs without any
installed packages.

Exit codes
----------
0 — all PATCH models carry the decorator (or no admin_edge_*.py files exist).
1 — at least one model is missing the decorator; violations printed to stdout.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_ROUTES_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "artifacts"
    / "syrabit-backend"
    / "routes"
)

_CLASS_RE = re.compile(
    r"^class\s+(\w+Patch)\s*\(\s*BaseModel\s*\)\s*:",
    re.MULTILINE,
)
_DECORATOR_RE = re.compile(r"^\s*@patch_route_contract\b")
_BLANK_OR_COMMENT_RE = re.compile(r"^\s*(#.*)?$")


def _check_file(path: Path) -> list[str]:
    """Return a list of violation strings for *path*.

    A violation is a ``*Patch(BaseModel)`` class definition that does not
    have ``@patch_route_contract`` on the immediately preceding non-blank,
    non-comment line.
    """
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines()
    violations: list[str] = []

    for match in _CLASS_RE.finditer(source):
        class_name = match.group(1)
        line_no = source[: match.start()].count("\n") + 1

        preceding_idx = line_no - 2
        while preceding_idx >= 0 and _BLANK_OR_COMMENT_RE.match(lines[preceding_idx]):
            preceding_idx -= 1

        if preceding_idx < 0 or not _DECORATOR_RE.match(lines[preceding_idx]):
            preceding_line = (
                lines[preceding_idx].strip() if preceding_idx >= 0 else "<start of file>"
            )
            violations.append(
                f"{path}:{line_no}: class {class_name}(BaseModel) is missing "
                f"@patch_route_contract decorator "
                f"(preceding non-blank line: {preceding_line!r})"
            )

    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="suppress output when all checks pass",
    )
    args = parser.parse_args(argv)

    if not _ROUTES_DIR.is_dir():
        print(
            f"[check_patch_route_contract] routes directory not found: {_ROUTES_DIR}",
            file=sys.stderr,
        )
        return 1

    admin_edge_files = sorted(_ROUTES_DIR.glob("admin_edge_*.py"))

    if not admin_edge_files:
        if not args.quiet:
            print("[check_patch_route_contract] no admin_edge_*.py files found — nothing to check")
        return 0

    all_violations: list[str] = []
    for path in admin_edge_files:
        all_violations.extend(_check_file(path))

    if all_violations:
        print(
            "[check_patch_route_contract] FAILED — "
            f"{len(all_violations)} unguarded PATCH model(s) found:\n"
        )
        for v in all_violations:
            print(f"  {v}")
        print(
            "\nFix: apply @patch_route_contract(PATCHABLE_*_KEYS, CANONICAL_*_KEYS) "
            "on the line immediately preceding each *Patch(BaseModel) class definition.\n"
            "See schemas/edge_settings.py module docstring for the full guide."
        )
        return 1

    if not args.quiet:
        checked = ", ".join(p.name for p in admin_edge_files)
        print(
            f"[check_patch_route_contract] OK — "
            f"all *Patch(BaseModel) classes carry @patch_route_contract "
            f"({len(admin_edge_files)} file(s): {checked})"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
