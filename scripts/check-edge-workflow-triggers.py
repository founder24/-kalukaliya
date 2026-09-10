#!/usr/bin/env python3
"""Guard the branch and path filters that decide when Edge CI runs."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


EXPECTED_PATHS = [
    "apps/edge/**",
    "scripts/test-worker-chat-performance.mjs",
    "scripts/worker-chat-performance-gate.mjs",
    "scripts/worker-chat-performance-gate.test.mjs",
    "scripts/check-required-status-contexts.py",
    "scripts/test-required-status-contexts.py",
    ".github/workflows/ci-edge.yml",
    ".github/workflows/required-status-contexts.yml",
]


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _event_block(lines: list[str], event: str) -> list[str]:
    event_pattern = re.compile(rf"^  {re.escape(event)}:\s*(?:#.*)?$")
    start = next((index for index, line in enumerate(lines) if event_pattern.match(line)), None)
    if start is None:
        raise ValueError(f"missing {event} trigger")

    block: list[str] = []
    for line in lines[start + 1 :]:
        if line.strip() and not line.startswith("    "):
            break
        block.append(line)
    return block


def _list_value(block: list[str], key: str) -> list[str]:
    inline_pattern = re.compile(rf"^    {re.escape(key)}:\s*\[(.*)]\s*(?:#.*)?$")
    expanded_pattern = re.compile(rf"^    {re.escape(key)}:\s*(?:#.*)?$")

    for index, line in enumerate(block):
        inline = inline_pattern.match(line)
        if inline:
            return [_unquote(item) for item in inline.group(1).split(",") if item.strip()]
        if expanded_pattern.match(line):
            values: list[str] = []
            for item_line in block[index + 1 :]:
                match = re.match(r"^      -\s+(.+?)\s*$", item_line)
                if not match:
                    if item_line.strip():
                        break
                    continue
                values.append(_unquote(match.group(1)))
            return values
    raise ValueError(f"missing {key} filter")


def validate_edge_triggers(workflow: Path) -> list[str]:
    lines = workflow.read_text(encoding="utf-8").splitlines()
    errors: list[str] = []

    for event in ("pull_request", "push"):
        try:
            block = _event_block(lines, event)
            branches = _list_value(block, "branches")
            paths = _list_value(block, "paths")
        except ValueError as exc:
            errors.append(str(exc))
            continue

        if branches != ["main"]:
            errors.append(f"{event}.branches must be exactly ['main']; got {branches!r}")
        if paths != EXPECTED_PATHS:
            errors.append(
                f"{event}.paths must match the protected Edge CI path scope; got {paths!r}"
            )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "workflow",
        nargs="?",
        type=Path,
        default=Path(".github/workflows/ci-edge.yml"),
    )
    args = parser.parse_args()

    errors = validate_edge_triggers(args.workflow)
    if errors:
        for error in errors:
            print(f"Edge CI trigger check failed: {error}")
        return 1

    print("Edge CI pull-request and main-push trigger scope is valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())