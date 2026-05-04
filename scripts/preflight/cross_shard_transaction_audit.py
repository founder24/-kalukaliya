#!/usr/bin/env python3
"""
Pre-flight Gate 2.1.c — cross-shard transaction audit (Task #363, §2.1.c).

Greps `artifacts/syrabit-backend/` for any multi-document transaction
that touches both `conversations` and `user_profile`. Each hit must
carry a `# shard-safe: same user_id` annotation on the same line or the
preceding line, OR be refactored before sharding.

Exit codes:
  0  — gate passed (0 unannotated cross-shard transactions)
  2  — gate failed (one or more unannotated cross-shard transactions)
  3  — backend dir not found

Usage:
  python3 scripts/preflight/cross_shard_transaction_audit.py
  python3 scripts/preflight/cross_shard_transaction_audit.py \
      --root artifacts/syrabit-backend
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

TXN_BEGIN = re.compile(
    r"\b(start_transaction\(|with_transaction\(|client\.start_session\(\s*causal_consistency=)"
)
COLL_CONV = re.compile(r"\b(conversations|chat_history)\b")
COLL_PROF = re.compile(r"\b(user_profile|user_profiles)\b")
ANNOTATION = re.compile(r"#\s*shard-safe:\s*same\s+user_id", re.IGNORECASE)


def scan_file(path: Path) -> list[tuple[int, str, bool]]:
    """Returns (line_no, line_text, has_annotation) for each suspect block."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    lines = text.splitlines()
    hits: list[tuple[int, str, bool]] = []
    in_txn = False
    txn_start = -1
    saw_conv = False
    saw_prof = False
    annotated = False

    for i, line in enumerate(lines):
        if TXN_BEGIN.search(line):
            in_txn = True
            txn_start = i
            saw_conv = COLL_CONV.search(line) is not None
            saw_prof = COLL_PROF.search(line) is not None
            annotated = bool(
                ANNOTATION.search(line)
                or (i > 0 and ANNOTATION.search(lines[i - 1]))
            )
            continue
        if in_txn:
            if COLL_CONV.search(line):
                saw_conv = True
            if COLL_PROF.search(line):
                saw_prof = True
            if ANNOTATION.search(line):
                annotated = True
            close_block = (
                line.strip() == ""
                or "end_session" in line
                or "commit_transaction" in line
                or i - txn_start > 60
            )
            if close_block:
                if saw_conv and saw_prof:
                    hits.append(
                        (txn_start + 1, lines[txn_start].strip(), annotated)
                    )
                in_txn = False
                saw_conv = saw_prof = annotated = False
                txn_start = -1

    if in_txn and saw_conv and saw_prof:
        hits.append((txn_start + 1, lines[txn_start].strip(), annotated))

    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="artifacts/syrabit-backend")
    args = ap.parse_args()

    root = Path(args.root)
    if not root.exists():
        print(f"ERROR: backend dir not found: {root}", file=sys.stderr)
        return 3

    py_files = [
        p for p in root.rglob("*.py")
        if "test" not in p.name and ".venv" not in p.parts
        and "node_modules" not in p.parts
    ]
    print(f"Scanning {len(py_files)} .py files under {root}...")

    total_hits = 0
    unannotated = 0
    for f in py_files:
        for line_no, line_txt, annotated in scan_file(f):
            total_hits += 1
            tag = "OK (annotated)" if annotated else "FAIL (unannotated)"
            print(f"  {tag}  {f}:{line_no}  {line_txt[:120]}")
            if not annotated:
                unannotated += 1

    print()
    print(f"Cross-shard transaction sites: {total_hits}  "
          f"(annotated safe: {total_hits - unannotated}, "
          f"unannotated: {unannotated})")

    if unannotated > 0:
        print()
        print(f"FAIL: {unannotated} unannotated cross-shard transaction "
              f"site(s) touch both `conversations` and `user_profile`.")
        print()
        print("Apply ONE of the following per hit, BEFORE provisioning the "
              "sharded cluster:")
        print("  1. Annotate `# shard-safe: same user_id` on the txn line "
              "if both docs are guaranteed to land on the same shard via "
              "the same user_id.")
        print("  2. Refactor into two single-shard ops + an idempotency "
              "token so cross-shard atomicity is no longer required.")
        return 2

    print("PASS: 0 unannotated cross-shard transactions.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
