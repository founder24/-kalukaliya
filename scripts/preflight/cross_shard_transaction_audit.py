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

# Broad txn-begin detector. Covers the common Motor / PyMongo patterns
# AND the wrapper patterns commonly used in our codebase.
TXN_BEGIN = re.compile(
    r"\b("
    r"start_transaction\s*\(|"
    r"with_transaction\s*\(|"
    r"start_session\s*\(|"
    r"in_transaction\s*\(|"
    r"transactional\b|"
    r"Transaction\s*\(|"
    r"@\s*atomic\b|"
    r"@\s*transactional\b"
    r")"
)
COLL_CONV = re.compile(
    r"\b(conversations|chat_history|chat_messages|messages|sessions)\b"
)
COLL_PROF = re.compile(
    r"\b(user_profile|user_profiles|profiles|users|user_state)\b"
)
ANNOTATION = re.compile(r"#\s*shard-safe:\s*same\s+user_id", re.IGNORECASE)
# An additional weaker signal: any `async with` / `with` block that
# includes both collection names within the same indented block.
WITH_BLOCK = re.compile(r"^\s*(async\s+)?with\s+")


def _block_extent(lines: list[str], start: int) -> int:
    """Return the line index where the indented block starting at `start`
    ends (exclusive). Falls back to start+200 to bound the scan."""
    if start >= len(lines):
        return start
    base_indent = len(lines[start]) - len(lines[start].lstrip())
    end = min(len(lines), start + 200)
    for j in range(start + 1, end):
        s = lines[j]
        if s.strip() == "":
            continue
        ind = len(s) - len(s.lstrip())
        if ind <= base_indent and not s.lstrip().startswith("#"):
            return j
    return end


def scan_file(path: Path) -> list[tuple[int, str, bool]]:
    """Returns (line_no, line_text, has_annotation) for each suspect block.

    Uses an indentation-aware block scan (no fragile bounded-line heuristic).
    Both explicit txn-begin patterns AND `with`/`async with` blocks
    that touch both collections are reported.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    lines = text.splitlines()
    hits: list[tuple[int, str, bool]] = []
    visited: set[int] = set()

    for i, line in enumerate(lines):
        if i in visited:
            continue
        is_txn_begin = bool(TXN_BEGIN.search(line))
        is_with_block = bool(WITH_BLOCK.match(line))
        if not (is_txn_begin or is_with_block):
            continue

        end = _block_extent(lines, i)
        block = "\n".join(lines[i:end])
        # Skip pure `with` blocks that don't carry a collection mention
        if not (COLL_CONV.search(block) and COLL_PROF.search(block)):
            continue
        # Require at least one mongo-ish call inside (filters out e.g.
        # generic httpx context managers that happen to contain the
        # words "users" and "messages" only as URL paths).
        mongo_signal = any(
            tok in block
            for tok in (
                ".find(", ".find_one(", ".update_one(", ".update_many(",
                ".insert_one(", ".insert_many(", ".delete_one(",
                ".delete_many(", ".bulk_write(", ".aggregate(",
                ".replace_one(", ".find_one_and_update(",
            )
        )
        if not mongo_signal:
            continue

        annotated = bool(
            ANNOTATION.search(block)
            or (i > 0 and ANNOTATION.search(lines[i - 1]))
        )
        hits.append((i + 1, lines[i].strip(), annotated))
        for k in range(i, end):
            visited.add(k)

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
