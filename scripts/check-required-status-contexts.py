#!/usr/bin/env python3
"""Verify that branch-protection status contexts are emitted by PR workflows."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


WORKFLOW_SUFFIXES = {".yml", ".yaml"}


def _strip_yaml_comment(value: str) -> str:
    quote = None
    for index, char in enumerate(value):
        if char in {"'", '"'}:
            quote = None if quote == char else char if quote is None else quote
        elif char == "#" and quote is None:
            return value[:index].rstrip()
    return value.rstrip()


def _scalar(value: str) -> str:
    value = _strip_yaml_comment(value).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def workflow_contexts(path: Path) -> set[str]:
    """Return job display names from a workflow that handles pull requests."""
    lines = path.read_text(encoding="utf-8").splitlines()
    has_pull_request = any(
        re.match(r"^  pull_request(?:_target)?:\s*(?:.*)?$", line)
        for line in lines
    )
    if not has_pull_request:
        return set()

    contexts: set[str] = set()
    in_jobs = False
    current_job: str | None = None
    current_name: str | None = None

    def save_job() -> None:
        if current_job:
            contexts.add(current_name or current_job)

    for line in lines:
        if re.match(r"^jobs:\s*(?:#.*)?$", line):
            in_jobs = True
            continue
        if in_jobs and line and not line.startswith((" ", "\t", "#")):
            save_job()
            break
        if not in_jobs:
            continue

        job_match = re.match(r"^  ([A-Za-z0-9_-]+):\s*(?:#.*)?$", line)
        if job_match:
            save_job()
            current_job = job_match.group(1)
            current_name = None
            continue

        name_match = re.match(r"^    name:\s*(.+?)\s*$", line)
        if current_job and name_match:
            current_name = _scalar(name_match.group(1))
    else:
        save_job()

    return contexts


def emitted_contexts(workflows_dir: Path) -> set[str]:
    contexts: set[str] = set()
    for path in sorted(workflows_dir.iterdir()):
        if path.suffix in WORKFLOW_SUFFIXES:
            contexts.update(workflow_contexts(path))
    return contexts


def validate_context_owners(
    required: set[str], workflows_dir: Path, owners_file: Path
) -> list[str]:
    owners = json.loads(owners_file.read_text(encoding="utf-8"))
    errors: list[str] = []
    for context, workflow_names in owners.items():
        if context not in required:
            continue
        for workflow_name in workflow_names:
            workflow_path = workflows_dir / workflow_name
            if not workflow_path.is_file():
                errors.append(f"{context}: workflow {workflow_name} does not exist")
                continue
            if context not in workflow_contexts(workflow_path):
                errors.append(
                    f"{context}: {workflow_name} no longer emits this context"
                )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--workflows-dir", type=Path, default=Path(".github/workflows")
    )
    parser.add_argument(
        "--required-contexts-file",
        type=Path,
        default=Path(".github/required-status-contexts.json"),
        help="JSON array of required contexts, fetched from main by CI.",
    )
    parser.add_argument(
        "--context-owners-file",
        type=Path,
        default=Path(".github/required-status-context-owners.json"),
        help="Map required contexts to workflows that must continue emitting them.",
    )
    args = parser.parse_args()

    try:
        required = set(
            json.loads(args.required_contexts_file.read_text(encoding="utf-8"))
        )

        emitted = emitted_contexts(args.workflows_dir)
        missing = sorted(required - emitted)
        ownership_errors = validate_context_owners(
            required, args.workflows_dir, args.context_owners_file
        )
        if missing or ownership_errors:
            details = [f"no PR workflow emits: {', '.join(missing)}"] if missing else []
            details.extend(ownership_errors)
            print(
                "::error title=Required status context has no PR job::"
                "Branch protection and PR workflows disagree: "
                + "; ".join(details),
                file=sys.stderr,
            )
            print(
                "Restore the job name or update branch protection in the same change.",
                file=sys.stderr,
            )
            return 1

        print(
            f"Verified {len(required)} required status context(s): "
            + (", ".join(sorted(required)) or "(none)")
        )
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(
            f"::error title=Required status context check failed::{error}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())