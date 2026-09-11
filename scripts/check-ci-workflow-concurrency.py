#!/usr/bin/env python3
"""Guard concurrency policy for CI workflows that consume significant runner time."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path


TRIGGER_CHECKER_PATH = Path(__file__).with_name("check-edge-workflow-triggers.py")
SPEC = importlib.util.spec_from_file_location("workflow_yaml", TRIGGER_CHECKER_PATH)
assert SPEC and SPEC.loader
WORKFLOW_YAML = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(WORKFLOW_YAML)

PROTECTED_WORKFLOWS = (
    Path(".github/workflows/ci-frontend.yml"),
    Path(".github/workflows/ci-backend.yml"),
)
EXPECTED_GROUP = (
    "${{ github.workflow }}-"
    "${{ github.event.pull_request.number || github.ref }}"
)


def validate_concurrency(workflow: Path) -> list[str]:
    errors: list[str] = []
    try:
        document = WORKFLOW_YAML.yaml.load(
            workflow.read_text(encoding="utf-8"),
            Loader=WORKFLOW_YAML.ContractLoader,
        )
    except (OSError, WORKFLOW_YAML.yaml.YAMLError) as exc:
        return [f"cannot parse workflow YAML: {exc}"]

    if not isinstance(document, dict):
        return ["workflow document must be a mapping"]

    concurrency = document.get("concurrency")
    if not isinstance(concurrency, dict):
        return ["top-level concurrency must be a mapping"]

    group = concurrency.get("group")
    if group != EXPECTED_GROUP:
        errors.append(
            "concurrency.group must be exactly "
            f"{EXPECTED_GROUP!r}; got {group!r}"
        )

    if concurrency.get("cancel-in-progress") is not True:
        errors.append("concurrency.cancel-in-progress must be true")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "workflows",
        nargs="*",
        type=Path,
        help="Workflow paths to check (defaults to protected frontend and backend CI).",
    )
    args = parser.parse_args()
    workflows = args.workflows or list(PROTECTED_WORKFLOWS)

    failed = False
    for workflow in workflows:
        errors = validate_concurrency(workflow)
        for error in errors:
            print(f"{workflow.as_posix()} concurrency check failed: {error}")
            failed = True

    if failed:
        return 1

    print(f"CI concurrency contracts are valid for {len(workflows)} workflow(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())