#!/usr/bin/env python3
"""Task #5 — Architecture lock CI guard.

Reads ``infra/architecture-matrix.json`` (the machine-readable companion
to ``infra/architecture-locked-2026.md``) and fails the build when:

1. **Source-path drift** — any path listed in a ``source_paths`` array no
   longer exists in the repo. This catches accidental file deletions
   during the downstream cleanup tasks (#6 → #8).
2. **Retired-provider regression** — any token from ``retired_providers``
   reappears in active code outside an allowlisted directory or a
   removal-note line (``# Task #XYZ``, ``removed``, ``retired``,
   ``deprecated``, ``legacy``, ``decommission``).
3. **Schema drift** — the JSON file is missing required keys.

Invoked standalone (``python scripts/check_architecture_lock.py``) and
also from the umbrella canonical-delegation guard so the existing
``canonical_delegation_gate`` workflow job enforces it pre-deploy.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = ROOT / "infra" / "architecture-matrix.json"

REQUIRED_TOP_KEYS = {
    "version",
    "source_blueprint",
    "founder_locks",
    "retired_providers",
    "retired_provider_allowlist_dirs",
    "sections",
}
REQUIRED_FOUNDER_LOCKS = {
    "monthly_usd_cap",
    "voice_paywall_routes",
    "degradation_thresholds_pct",
    "supabase_sole_auth",
    "no_silent_fallbacks",
    "sarvam_assamese_head",
    "pinecone_dim",
}
REMOVAL_NOTE_TOKENS = (
    "removed",
    "retired",
    "deprecated",
    "legacy",
    "decommission",
    "no longer",
    "previously",
    "task #",
    "REMOVED",
)

# Roots we walk for the regression check. We deliberately skip the
# repo root rglob — it would descend into node_modules / .python-deps /
# .pythonlibs / .git / .venv-prod and wedge CI for minutes.
SCAN_ROOTS = (
    "artifacts/syrabit-backend",
    "artifacts/syrabit/src",
    "artifacts/syrabit/workers",
    "artifacts/syrabit/services",
    "workers",
    "scripts",
    "infra",
)
# Files we never scan for the regression check (binaries, large
# vendored trees, the matrix itself which legitimately names every
# retired provider).
SCAN_SKIP_DIRS = {
    ".git", "node_modules", ".local", ".python-deps", ".pythonlibs",
    "attached_assets", "build", "dist", "__pycache__", ".pytest_cache",
    ".mypy_cache", "venv", ".venv", ".venv-prod", "coverage",
    "emergentintegrations",
}
SCAN_SKIP_FILES = {
    "infra/architecture-matrix.json",
    "infra/architecture-locked-2026.md",
    "infra/four-cloud-delegation.md",
    "infra/v4-locked-architecture.md",
    "infra/provider-priority-map.md",
    "infra/per-cloud-feature-delegation.md",
    "infra/credit-burn-runbook.md",
    "infra/cloud-cutover-364.md",
    "scripts/check_architecture_lock.py",
    "scripts/ci_grep_gate.sh",
    "artifacts/syrabit-backend/scripts/check_dead_providers.py",
    "artifacts/syrabit-backend/scripts/ci/check_canonical_delegation.py",
    "replit.md",
    "threat_model.md",
}
SCAN_EXTENSIONS = {".py", ".js", ".jsx", ".ts", ".tsx", ".sh", ".yaml", ".yml", ".toml"}


def _load_matrix() -> tuple[dict | None, list[str]]:
    failures: list[str] = []
    if not MATRIX_PATH.exists():
        failures.append(
            f"{MATRIX_PATH.relative_to(ROOT)}: missing — Task #5 architecture lock requires it."
        )
        return None, failures
    try:
        data = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        failures.append(f"{MATRIX_PATH.relative_to(ROOT)}: invalid JSON — {exc}")
        return None, failures
    return data, failures


def _check_schema(matrix: dict) -> list[str]:
    failures: list[str] = []
    missing_top = REQUIRED_TOP_KEYS - set(matrix.keys())
    if missing_top:
        failures.append(
            f"architecture-matrix.json: missing top-level keys: {sorted(missing_top)}"
        )
    locks = matrix.get("founder_locks") or {}
    missing_locks = REQUIRED_FOUNDER_LOCKS - set(locks.keys())
    if missing_locks:
        failures.append(
            f"architecture-matrix.json: founder_locks missing keys: {sorted(missing_locks)}"
        )
    if locks.get("monthly_usd_cap") not in (None,) and locks.get("monthly_usd_cap") > 100:
        failures.append(
            "architecture-matrix.json: founder_locks.monthly_usd_cap must stay ≤ $100 "
            "(Task #549 founder-lock)."
        )
    if locks.get("pinecone_dim") not in (None, 1024):
        failures.append(
            "architecture-matrix.json: founder_locks.pinecone_dim must equal 1024 "
            "(replit.md gotcha)."
        )
    # Founder-lock semantics — enforce the values, not just presence.
    ladder = locks.get("degradation_thresholds_pct")
    if ladder is not None and list(ladder) != [60, 80, 95]:
        failures.append(
            "architecture-matrix.json: founder_locks.degradation_thresholds_pct "
            "must be exactly [60, 80, 95] (Task #549 ladder)."
        )
    paywall = locks.get("voice_paywall_routes") or []
    required_paywall = {"/voice/tts", "/voice/stt", "/voice/voice"}
    if not required_paywall.issubset(set(paywall)):
        failures.append(
            "architecture-matrix.json: founder_locks.voice_paywall_routes "
            "must include /voice/tts, /voice/stt, /voice/voice (Task #549)."
        )
    if locks.get("supabase_sole_auth") is not True:
        failures.append(
            "architecture-matrix.json: founder_locks.supabase_sole_auth must be true "
            "(replit.md User preferences)."
        )
    if locks.get("sarvam_assamese_head") is not True:
        failures.append(
            "architecture-matrix.json: founder_locks.sarvam_assamese_head must be true "
            "(Task #553)."
        )
    nsf = locks.get("no_silent_fallbacks")
    if not (isinstance(nsf, str) and "V4" in nsf and "12" in nsf):
        failures.append(
            "architecture-matrix.json: founder_locks.no_silent_fallbacks must "
            "reference 'V4 §12' (replit.md User preferences)."
        )
    sections = matrix.get("sections") or []
    if not sections:
        failures.append("architecture-matrix.json: sections[] is empty.")
    for sec in sections:
        for row in sec.get("rows", []) or []:
            if "status" not in row or "source_paths" not in row:
                failures.append(
                    f"architecture-matrix.json: section {sec.get('id')} row "
                    f"{row.get('item','?')!r} missing status/source_paths."
                )
            if row.get("status") not in ("IMPLEMENTED", "PARTIAL", "MISSING", "RETIRED"):
                failures.append(
                    f"architecture-matrix.json: section {sec.get('id')} row "
                    f"{row.get('item','?')!r} has invalid status "
                    f"{row.get('status')!r}."
                )
    return failures


def _check_source_paths(matrix: dict) -> list[str]:
    """Every source_paths entry must exist on disk. Trailing slashes
    indicate a directory; bare paths can be either."""
    failures: list[str] = []
    for sec in matrix.get("sections", []) or []:
        for row in sec.get("rows", []) or []:
            if row.get("status") in ("RETIRED", "MISSING"):
                # Retired/missing rows have empty source_paths by design.
                continue
            for sp in row.get("source_paths") or []:
                p = ROOT / sp
                if not p.exists():
                    failures.append(
                        f"architecture-matrix.json: section {sec.get('id')} row "
                        f"{row.get('item','?')!r} references missing path "
                        f"{sp!r}."
                    )
    return failures


def _is_allowlisted_for_regression(
    rel: str,
    allowlist_dirs: list[str],
    allowlist_paths: list[str],
) -> bool:
    if rel in SCAN_SKIP_FILES:
        return True
    parts = Path(rel).parts
    if any(part in SCAN_SKIP_DIRS for part in parts):
        return True
    for ad in allowlist_dirs:
        if rel.startswith(ad):
            return True
    if rel in allowlist_paths:
        return True
    return False


def _build_active_use_patterns(retired: list[str]) -> tuple[re.Pattern[str] | None, re.Pattern[str] | None]:
    """Two patterns, two strictness tiers:

    * STRICT (no removal-note suppression) — runtime reintroduction
      shapes that cannot appear in audit prose:
        - ``import X`` / ``from X import …`` (Python, JS/TS)
        - ``require("X")`` / ``import("X")`` (JS/TS)
        - ``os.environ.get("X")`` / ``os.environ["X"]`` (Python)
        - ``process.env.X`` (JS/TS)

    * SOFT (removal-note suppression honored) — bare ALL-CAPS env-var
      literals (``SENDGRID_API_KEY``). These can legitimately appear in
      removal-note audit comments (``# RESEND_API_KEY retired …``) so
      a removal-note token on the same line silences the match.
    """
    strict_parts: list[str] = []
    soft_parts: list[str] = []
    for tok in retired:
        esc = re.escape(tok)
        if tok.isupper() and "_" in tok:
            soft_parts.append(rf"\b{esc}\b")
        else:
            strict_parts.append(rf"^\s*import\s+{esc}\b")
            strict_parts.append(rf"^\s*from\s+{esc}\b")
            strict_parts.append(rf"\brequire\(\s*['\"]{esc}['\"]\s*\)")
            strict_parts.append(rf"\bimport\(\s*['\"]{esc}['\"]\s*\)")
            strict_parts.append(rf"\bos\.environ\.(?:get\(\s*['\"]{esc}['\"]|['\"]?{esc}['\"])")
            strict_parts.append(rf"\bprocess\.env\.{esc}\b")
    strict = re.compile("|".join(strict_parts), re.MULTILINE) if strict_parts else None
    soft = re.compile("|".join(soft_parts), re.MULTILINE) if soft_parts else None
    return strict, soft


def _check_retired_provider_regression(matrix: dict) -> list[str]:
    failures: list[str] = []
    retired = matrix.get("retired_providers") or []
    allowlist_dirs = matrix.get("retired_provider_allowlist_dirs") or []
    allowlist_paths = matrix.get("retired_provider_allowlist_paths") or []
    if not retired:
        return failures
    strict_pat, soft_pat = _build_active_use_patterns(retired)

    candidates: list[Path] = []
    for root_rel in SCAN_ROOTS:
        root_path = ROOT / root_rel
        if not root_path.exists():
            continue
        for path in root_path.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix not in SCAN_EXTENSIONS:
                continue
            parts = set(path.relative_to(ROOT).parts)
            if parts & SCAN_SKIP_DIRS:
                continue
            candidates.append(path)
    for path in candidates:
        rel = path.relative_to(ROOT).as_posix()
        if _is_allowlisted_for_regression(rel, allowlist_dirs, allowlist_paths):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for ln, line in enumerate(text.splitlines(), 1):
            if strict_pat and strict_pat.search(line):
                # Runtime reintroduction — removal-note comments cannot
                # silence a live import / env-read.
                failures.append(
                    f"{rel}:{ln}: retired-provider active reintroduction → {line.strip()[:140]}"
                )
                break
            if soft_pat and soft_pat.search(line):
                lower = line.lower()
                if any(tok in lower for tok in REMOVAL_NOTE_TOKENS):
                    continue
                failures.append(
                    f"{rel}:{ln}: retired-provider env-var reintroduction → {line.strip()[:140]}"
                )
                break
    return failures


def main() -> int:
    matrix, fails = _load_matrix()
    if matrix is None:
        for f in fails:
            print(f"  {f}")
        print("Architecture-lock guard FAILED.")
        return 1
    fails.extend(_check_schema(matrix))
    fails.extend(_check_source_paths(matrix))
    fails.extend(_check_retired_provider_regression(matrix))
    if fails:
        print("Architecture-lock guard FAILED:")
        for f in fails:
            print(f"  {f}")
        print(
            f"\n{len(fails)} violation(s). See "
            f"infra/architecture-locked-2026.md + scripts/check_architecture_lock.py."
        )
        return 1
    sec_count = len(matrix.get("sections") or [])
    row_count = sum(len(s.get("rows") or []) for s in matrix.get("sections") or [])
    print(
        f"Architecture-lock guard OK — {sec_count} sections, {row_count} rows verified."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
