#!/usr/bin/env python3
"""Task #549 — CI guard: budget ceiling is founder-locked.

Walks `cost_caps.py` + `credit_burn_meter.py` and fails the build when
the perpetual $100/month budget ceiling is raised without the same
"# COST-CAP-OVERRIDE: <reason>" discipline that protects TOKEN_BUDGETS.

Specifically:
  1. `_DEFAULT_MONTHLY_TOTAL_USD_CAP` in cost_caps.py must be <= 100.0
     unless the line carries a `# COST-CAP-OVERRIDE:` comment.
  2. `MeterDConfig.cap_usd` default in credit_burn_meter.py must be
     <= 100.0 under the same rule.
  3. The three-stage degradation thresholds in cost_caps.py
     (DEGRADATION_PCT_PAUSE_BATCH / VOICE_OFF / FREE_503) must remain
     monotonically increasing and each within (0.0, 1.0).

Exit code 0 on pass, 1 on violation. Designed to run in CI without
imports of the live app — purely textual / AST inspection.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COST_CAPS = ROOT / "cost_caps.py"
CREDIT_BURN = ROOT / "credit_burn_meter.py"

CEILING_USD = 100.0
OVERRIDE_RE = re.compile(r"#\s*COST-CAP-OVERRIDE\s*:")


def _fail(msg: str) -> None:
    print(f"[check_budget_ceiling] FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def _check_cost_caps() -> None:
    src = COST_CAPS.read_text(encoding="utf-8")
    tree = ast.parse(src)
    src_lines = src.splitlines()

    found_default = False
    degr_thresholds: dict[str, float] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if not isinstance(tgt, ast.Name):
                    continue
                if tgt.id == "_DEFAULT_MONTHLY_TOTAL_USD_CAP":
                    found_default = True
                    if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, (int, float)):
                        _fail("_DEFAULT_MONTHLY_TOTAL_USD_CAP must be a numeric literal")
                    val = float(node.value.value)
                    line = src_lines[node.lineno - 1]
                    if val > CEILING_USD and not OVERRIDE_RE.search(line):
                        _fail(
                            f"_DEFAULT_MONTHLY_TOTAL_USD_CAP={val} > {CEILING_USD} "
                            f"without '# COST-CAP-OVERRIDE: <reason>' marker on line {node.lineno}"
                        )
                if tgt.id in {
                    "DEGRADATION_PCT_PAUSE_BATCH",
                    "DEGRADATION_PCT_VOICE_OFF",
                    "DEGRADATION_PCT_FREE_503",
                }:
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, (int, float)):
                        degr_thresholds[tgt.id] = float(node.value.value)

    if not found_default:
        _fail("_DEFAULT_MONTHLY_TOTAL_USD_CAP missing from cost_caps.py")

    expected = ["DEGRADATION_PCT_PAUSE_BATCH",
                "DEGRADATION_PCT_VOICE_OFF",
                "DEGRADATION_PCT_FREE_503"]
    for k in expected:
        if k not in degr_thresholds:
            _fail(f"{k} missing from cost_caps.py")
        v = degr_thresholds[k]
        if not (0.0 < v < 1.0):
            _fail(f"{k}={v} must be in (0.0, 1.0)")
    a, b, c = (degr_thresholds[k] for k in expected)
    if not (a < b < c):
        _fail(
            f"degradation thresholds must be strictly increasing; "
            f"got pause_batch={a}, voice_off={b}, free_503={c}"
        )


def _check_meter_d() -> None:
    src = CREDIT_BURN.read_text(encoding="utf-8")
    tree = ast.parse(src)
    src_lines = src.splitlines()

    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "MeterDConfig":
            for body in node.body:
                if isinstance(body, ast.AnnAssign) and isinstance(body.target, ast.Name):
                    if body.target.id == "cap_usd" and body.value is not None:
                        found = True
                        if isinstance(body.value, ast.Constant) and isinstance(body.value.value, (int, float)):
                            val = float(body.value.value)
                            line = src_lines[body.lineno - 1]
                            if val > CEILING_USD and not OVERRIDE_RE.search(line):
                                _fail(
                                    f"MeterDConfig.cap_usd default={val} > {CEILING_USD} "
                                    f"without '# COST-CAP-OVERRIDE: <reason>' marker on line {body.lineno}"
                                )
    if not found:
        _fail("MeterDConfig.cap_usd default missing from credit_burn_meter.py")


def main() -> int:
    _check_cost_caps()
    _check_meter_d()
    print(f"[check_budget_ceiling] OK — budget ceiling <= ${CEILING_USD:.0f}/month")
    return 0


if __name__ == "__main__":
    sys.exit(main())
