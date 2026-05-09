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
CONFIG_PY  = ROOT / "config.py"
VOICE_PY   = ROOT / "routes" / "voice.py"

ALLOWED_CHAT_HEADS = {"workers_ai_llama32_3b", "workers_ai_mistral_7b",
                      "workers_ai", "vertex", "vertex_flash_lite"}
PAID_VOICE_ROUTES  = {"/voice/tts", "/voice/stt", "/voice/voice"}

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
                    # Task #581 §L10 — free-tier-first ladder.
                    "DEGRADATION_PCT_FREE_TIGHTEN_1",
                    "DEGRADATION_PCT_FREE_TIGHTEN_2",
                    "DEGRADATION_PCT_FREE_TIGHTEN_3",
                    "DEGRADATION_PCT_FREE_TIGHTEN_4",
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

    # Task #581 §L10 — free-tier-first ladder. All four steps MUST sit
    # below DEGRADATION_PCT_PAUSE_BATCH (the legacy 60 % step) so the
    # system sheds free-user load BEFORE touching paid features.
    free_keys = [
        "DEGRADATION_PCT_FREE_TIGHTEN_1",
        "DEGRADATION_PCT_FREE_TIGHTEN_2",
        "DEGRADATION_PCT_FREE_TIGHTEN_3",
        "DEGRADATION_PCT_FREE_TIGHTEN_4",
    ]
    for k in free_keys:
        if k not in degr_thresholds:
            _fail(f"{k} missing from cost_caps.py (Task #581 §L10)")
        v = degr_thresholds[k]
        if not (0.0 < v < 1.0):
            _fail(f"{k}={v} must be in (0.0, 1.0)")
    f1, f2, f3, f4 = (degr_thresholds[k] for k in free_keys)
    if not (f1 < f2 < f3 < f4):
        _fail(
            f"§L10 free-tier ladder must be strictly increasing; "
            f"got tighten_1={f1}, tighten_2={f2}, tighten_3={f3}, tighten_4={f4}"
        )
    if f4 >= a:
        _fail(
            f"§L10 free-tier ladder MUST sit below the legacy "
            f"DEGRADATION_PCT_PAUSE_BATCH={a} so free load sheds before "
            f"paid features; got tighten_4={f4} >= pause_batch={a}"
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


def _check_chat_priority_head() -> None:
    """PROVIDER_PRIORITY heads must match the Task #549 contract:
      * english_rag_chat  → workers_ai variant or vertex
      * assamese_rag_chat → sarvam (locked Indic specialist primary)
    """
    src = CONFIG_PY.read_text(encoding="utf-8")
    # english chat head
    m = re.search(r'"english_rag_chat"\s*:\s*\[\s*"([^"]+)"', src)
    if not m:
        _fail("could not locate PROVIDER_PRIORITY['english_rag_chat'] head in config.py")
    head = m.group(1)
    if head not in ALLOWED_CHAT_HEADS:
        _fail(
            f"PROVIDER_PRIORITY['english_rag_chat'] head must be one of "
            f"{sorted(ALLOWED_CHAT_HEADS)} (Task #549); got {head!r}"
        )
    # assamese chat head — must remain sarvam per project goal
    # ("Sarvam stays Assamese primary unchanged").
    m_as = re.search(r'"assamese_rag_chat"\s*:\s*\[\s*"([^"]+)"', src)
    if not m_as:
        _fail("could not locate PROVIDER_PRIORITY['assamese_rag_chat'] head in config.py")
    as_head = m_as.group(1)
    if as_head != "sarvam":
        _fail(
            f"PROVIDER_PRIORITY['assamese_rag_chat'] head must remain 'sarvam' "
            f"(Task #549 — Sarvam is the locked Assamese-chat primary); "
            f"got {as_head!r}"
        )


def _check_voice_paid_gate() -> None:
    """All three /voice/* paid routes must depend on require_paid_plan."""
    src = VOICE_PY.read_text(encoding="utf-8")
    if "require_paid_plan" not in src:
        _fail("routes/voice.py must import + use require_paid_plan (Task #549)")
    # Each paid endpoint declaration must sit above a Depends(require_paid_plan)
    # OR Depends(require_paid_plan_or_voice_preview) (Task #581 §L9 free-tier
    # voice preview wrapper — same paid gate, plus a metered 1-call/day free
    # preview that still 402s on the second call).
    accepted_deps = (
        "Depends(require_paid_plan)",
        "Depends(require_paid_plan_or_voice_preview)",
    )
    for route in PAID_VOICE_ROUTES:
        # Find the route decorator and check the next ~30 lines for the dep.
        idx = src.find(f'"{route}"')
        if idx < 0:
            _fail(f"routes/voice.py missing route decorator for {route}")
        window = src[idx: idx + 2000]
        if not any(dep in window for dep in accepted_deps):
            _fail(
                f"routes/voice.py: {route} must use Depends(require_paid_plan) "
                f"or Depends(require_paid_plan_or_voice_preview) "
                f"to gate free-plan callers with HTTP 402 (Task #549/#581)"
            )


def main() -> int:
    _check_cost_caps()
    _check_meter_d()
    _check_chat_priority_head()
    _check_voice_paid_gate()
    print(f"[check_budget_ceiling] OK — budget ceiling <= ${CEILING_USD:.0f}/month, "
          f"chat head ∈ {{workers_ai*, vertex}}, voice routes paid-gated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
