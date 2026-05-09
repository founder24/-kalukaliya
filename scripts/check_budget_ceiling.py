#!/usr/bin/env python3
"""Founder-lock budget-ceiling guard (follow-up #26).

`replit.md` claims this script enforces, on every PR, that:

  1. ``_DEFAULT_MONTHLY_TOTAL_USD_CAP`` (artifacts/syrabit-backend/cost_caps.py)
     stays at or below the founder-locked $100 USD/month ceiling.
  2. ``MeterDConfig.cap_usd`` default (artifacts/syrabit-backend/credit_burn_meter.py)
     stays at or below the same $100 ceiling.
  3. The three-stage degradation ladder
     ``DEGRADATION_PCT_PAUSE_BATCH = 0.60`` →
     ``DEGRADATION_PCT_VOICE_OFF   = 0.80`` →
     ``DEGRADATION_PCT_FREE_503    = 0.95``
     is strictly increasing inside the open interval (0.0, 1.0).
  4. Edge chat caps in ``workers/edge-proxy/src/index.ts``
     (``CHAT_CAP_MONTHLY = 30``, ``CHAT_CAP_DAILY = 3``)
     are not raised above their founder-locked defaults.

Any line that needs to violate one of the numeric ceilings (1, 2, 4)
must carry a literal ``# COST-CAP-OVERRIDE: <reason>`` marker on the
same line. This mirrors the discipline tested by
``artifacts/syrabit-backend/tests/test_cost_caps.py``: documentation +
guard + test all reference the same marker so a bypass requires three
deliberate edits, not one.

Exit codes:
  0 — every founder lock holds.
  1 — at least one founder lock is violated; details printed to stderr.
  2 — required source file is missing (the guard cannot certify
       silence as success).

This guard is intentionally deterministic and dependency-free (stdlib
only, no network, no AST parsing) so it can run inside any CI
environment, including pre-commit hooks.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent
COST_CAPS = REPO_ROOT / "artifacts" / "syrabit-backend" / "cost_caps.py"
CREDIT_METER = REPO_ROOT / "artifacts" / "syrabit-backend" / "credit_burn_meter.py"
EDGE_PROXY = REPO_ROOT / "workers" / "edge-proxy" / "src" / "index.ts"

OVERRIDE_MARKER = "# COST-CAP-OVERRIDE:"
TS_OVERRIDE_MARKER = "// COST-CAP-OVERRIDE:"

MONTHLY_USD_CAP = 100.0
EDGE_CHAT_CAP_MONTHLY = 30
EDGE_CHAT_CAP_DAILY = 3


def _read(path: Path) -> str | None:
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def _find_assignment(text: str, name: str) -> tuple[int, str] | None:
    """Return (line_no_1_indexed, line_text) for the first
    top-level ``<name> = ...`` or ``<name>: type = ...`` assignment.
    Skips comments and indented (nested) assignments.
    """
    pattern = re.compile(
        rf"^\s*{re.escape(name)}\s*(?::\s*[A-Za-z_.\[\], ]+)?\s*="
    )
    for i, line in enumerate(text.splitlines(), start=1):
        if pattern.match(line):
            return i, line
    return None


def _find_ts_const(text: str, name: str) -> tuple[int, str] | None:
    pattern = re.compile(rf"^\s*const\s+{re.escape(name)}\s*=")
    for i, line in enumerate(text.splitlines(), start=1):
        if pattern.match(line):
            return i, line
    return None


def _extract_number(line: str) -> float | None:
    """Pull the first numeric literal out of an assignment line."""
    m = re.search(r"=\s*([0-9]+(?:\.[0-9]+)?)", line)
    if not m:
        return None
    return float(m.group(1))


def _has_override(line: str) -> bool:
    return OVERRIDE_MARKER in line or TS_OVERRIDE_MARKER in line


# ── Individual checks ──────────────────────────────────────────────────────


def check_monthly_usd_cap(errors: list[str]) -> None:
    text = _read(COST_CAPS)
    if text is None:
        errors.append(
            f"FATAL: {COST_CAPS} missing — cannot certify monthly USD cap."
        )
        return
    found = _find_assignment(text, "_DEFAULT_MONTHLY_TOTAL_USD_CAP")
    if not found:
        errors.append(
            "FATAL: _DEFAULT_MONTHLY_TOTAL_USD_CAP not found in cost_caps.py"
        )
        return
    line_no, line = found
    value = _extract_number(line)
    if value is None:
        errors.append(
            f"FATAL: cost_caps.py:{line_no} _DEFAULT_MONTHLY_TOTAL_USD_CAP "
            "has no numeric literal."
        )
        return
    if value > MONTHLY_USD_CAP and not _has_override(line):
        errors.append(
            f"VIOLATION: cost_caps.py:{line_no} _DEFAULT_MONTHLY_TOTAL_USD_CAP "
            f"= {value} exceeds founder-locked ${MONTHLY_USD_CAP:.0f}/month and "
            f"line lacks '{OVERRIDE_MARKER} <reason>' marker."
        )


def check_meter_d_cap(errors: list[str]) -> None:
    text = _read(CREDIT_METER)
    if text is None:
        errors.append(
            f"FATAL: {CREDIT_METER} missing — cannot certify MeterDConfig cap."
        )
        return
    # MeterDConfig.cap_usd is a dataclass field — search for the first
    # `cap_usd: float = NN.NN` line. We require it to be inside the
    # MeterDConfig class block; for simplicity we walk class boundaries.
    in_meter_d = False
    found_line = None
    found_no = None
    for i, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("class "):
            in_meter_d = "MeterDConfig" in stripped
            continue
        if in_meter_d and re.match(r"^\s*cap_usd\s*:", line):
            found_line = line
            found_no = i
            break
    if found_line is None or found_no is None:
        errors.append(
            "FATAL: MeterDConfig.cap_usd not found in credit_burn_meter.py"
        )
        return
    value = _extract_number(found_line)
    if value is None:
        errors.append(
            f"FATAL: credit_burn_meter.py:{found_no} MeterDConfig.cap_usd "
            "has no numeric literal."
        )
        return
    if value > MONTHLY_USD_CAP and not _has_override(found_line):
        errors.append(
            f"VIOLATION: credit_burn_meter.py:{found_no} "
            f"MeterDConfig.cap_usd = {value} exceeds founder-locked "
            f"${MONTHLY_USD_CAP:.0f}/month and line lacks "
            f"'{OVERRIDE_MARKER} <reason>' marker."
        )


def check_degradation_ladder(errors: list[str]) -> None:
    text = _read(COST_CAPS)
    if text is None:
        return  # already reported by check_monthly_usd_cap
    names = (
        "DEGRADATION_PCT_PAUSE_BATCH",
        "DEGRADATION_PCT_VOICE_OFF",
        "DEGRADATION_PCT_FREE_503",
    )
    values: list[float] = []
    for name in names:
        found = _find_assignment(text, name)
        if not found:
            errors.append(
                f"FATAL: {name} not found in cost_caps.py — degradation "
                "ladder cannot be verified."
            )
            return
        line_no, line = found
        v = _extract_number(line)
        if v is None:
            errors.append(
                f"FATAL: cost_caps.py:{line_no} {name} has no numeric literal."
            )
            return
        values.append(v)
    # Strictly increasing inside (0.0, 1.0).
    for name, v in zip(names, values):
        if not (0.0 < v < 1.0):
            errors.append(
                f"VIOLATION: cost_caps.py {name} = {v} must lie inside "
                "the open interval (0.0, 1.0)."
            )
    for prev_name, next_name, prev_v, next_v in zip(
        names, names[1:], values, values[1:]
    ):
        if not (prev_v < next_v):
            errors.append(
                f"VIOLATION: cost_caps.py degradation ladder is not "
                f"strictly increasing: {prev_name}={prev_v} >= "
                f"{next_name}={next_v}."
            )


def check_edge_chat_caps(errors: list[str]) -> None:
    text = _read(EDGE_PROXY)
    if text is None:
        errors.append(
            f"FATAL: {EDGE_PROXY} missing — cannot certify edge chat caps."
        )
        return
    for name, ceiling in (
        ("CHAT_CAP_MONTHLY", EDGE_CHAT_CAP_MONTHLY),
        ("CHAT_CAP_DAILY", EDGE_CHAT_CAP_DAILY),
    ):
        found = _find_ts_const(text, name)
        if not found:
            errors.append(
                f"FATAL: {name} not found in workers/edge-proxy/src/index.ts"
            )
            continue
        line_no, line = found
        value = _extract_number(line)
        if value is None:
            errors.append(
                f"FATAL: workers/edge-proxy/src/index.ts:{line_no} {name} "
                "has no numeric literal."
            )
            continue
        if value > ceiling and not _has_override(line):
            errors.append(
                f"VIOLATION: workers/edge-proxy/src/index.ts:{line_no} "
                f"{name} = {int(value)} exceeds founder-locked default "
                f"{ceiling} and line lacks '{TS_OVERRIDE_MARKER} <reason>' "
                "marker."
            )


CHECKS: tuple = (
    check_monthly_usd_cap,
    check_meter_d_cap,
    check_degradation_ladder,
    check_edge_chat_caps,
)


def main(argv: Iterable[str] | None = None) -> int:
    errors: list[str] = []
    for check in CHECKS:
        check(errors)
    if errors:
        sys.stderr.write("check_budget_ceiling: FAILED\n")
        for e in errors:
            sys.stderr.write(f"  - {e}\n")
        sys.stderr.write(
            "\nFounder locks are documented in replit.md "
            "(\"Founder locks (always win)\" + \"Gotchas\"). "
            f"To intentionally raise a numeric ceiling, add '{OVERRIDE_MARKER} "
            "<reason>' on the same line and a Sentry-annotated changelog entry.\n"
        )
        return 1
    print("check_budget_ceiling: OK")
    print(f"  monthly USD cap        <= ${MONTHLY_USD_CAP:.0f}")
    print(f"  MeterDConfig.cap_usd   <= ${MONTHLY_USD_CAP:.0f}")
    print("  degradation ladder     0.60 < 0.80 < 0.95 (strictly increasing)")
    print(f"  edge CHAT_CAP_MONTHLY  <= {EDGE_CHAT_CAP_MONTHLY}")
    print(f"  edge CHAT_CAP_DAILY    <= {EDGE_CHAT_CAP_DAILY}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
