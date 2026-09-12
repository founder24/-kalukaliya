#!/usr/bin/env python3
"""Guard the branch and path filters that decide when critical CI runs."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import yaml


WORKFLOW_CONTRACTS: dict[str, list[str] | None] = {
    ".github/workflows/validate-github-actions.yml": [
        ".github/workflows/**",
        "scripts/check-github-actions.sh",
        "scripts/update-actionlint.py",
        "scripts/check-edge-workflow-triggers.py",
        "scripts/test-edge-workflow-triggers.py",
        "scripts/check-ci-workflow-concurrency.py",
        "scripts/test-ci-workflow-concurrency.py",
        "scripts/check-staff-chunk-release-gate.py",
        "scripts/test-staff-chunk-release-gate.py",
    ],
    ".github/workflows/ci-api.yml": None,
    ".github/workflows/ci-edge.yml": [
        "apps/edge/**",
        "scripts/test-worker-chat-performance.mjs",
        "scripts/worker-chat-performance-gate.mjs",
        "scripts/worker-chat-performance-gate.test.mjs",
        "scripts/check-required-status-contexts.py",
        "scripts/test-required-status-contexts.py",
        ".github/workflows/ci-edge.yml",
        ".github/workflows/required-status-contexts.yml",
    ],
    ".github/workflows/ci-frontend.yml": [
        "apps/frontend/**",
        ".github/workflows/deploy-cloudflare.yml",
        ".github/workflows/ci-frontend.yml",
        "scripts/check-edge-workflow-triggers.py",
        "scripts/check-staff-chunk-release-gate.py",
        "scripts/test-staff-chunk-release-gate.py",
        "pnpm-lock.yaml",
    ],
}

# Kept as a public alias for existing imports of this checker.
EXPECTED_PATHS = WORKFLOW_CONTRACTS[".github/workflows/ci-edge.yml"]
VALIDATOR_EXPECTED_PATHS = WORKFLOW_CONTRACTS[
    ".github/workflows/validate-github-actions.yml"
]
INDEPENDENT_GUARD_WORKFLOW = Path(".github/workflows/ci-api.yml")
REQUIRED_GUARD_COMMANDS = {
    "python3 scripts/check-edge-workflow-triggers.py",
    "python3 scripts/test-edge-workflow-triggers.py",
}


class ContractLoader(yaml.SafeLoader):
    """YAML 1.2-style safe loader that rejects duplicate mapping keys."""


ContractLoader.yaml_implicit_resolvers = {
    key: [
        resolver
        for resolver in resolvers
        if resolver[0] != "tag:yaml.org,2002:bool"
    ]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
ContractLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|false)$", re.IGNORECASE),
    list("tTfF"),
)


def _construct_unique_mapping(
    loader: ContractLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[object, object]:
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


ContractLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def validate_workflow_triggers(
    workflow: Path, expected_paths: list[str] | None
) -> list[str]:
    errors: list[str] = []
    try:
        document = yaml.load(
            workflow.read_text(encoding="utf-8"), Loader=ContractLoader
        )
    except (OSError, yaml.YAMLError) as exc:
        return [f"cannot parse workflow YAML: {exc}"]

    if not isinstance(document, dict):
        return ["workflow document must be a mapping"]
    triggers = document.get("on")
    if not isinstance(triggers, dict):
        return ["missing on trigger mapping"]

    missing = object()
    for event in ("pull_request", "push"):
        event_config = triggers.get(event, missing)
        if event_config is missing:
            errors.append(f"missing {event} trigger")
            continue
        if not isinstance(event_config, dict):
            errors.append(f"{event} trigger must be a mapping")
            continue

        branches = event_config.get("branches")
        if branches != ["main"]:
            errors.append(f"{event}.branches must be exactly ['main']; got {branches!r}")
        allowed_keys = {"branches"}
        if expected_paths is not None:
            allowed_keys.add("paths")
        unexpected_keys = set(event_config) - allowed_keys
        if unexpected_keys:
            errors.append(
                f"{event} has unreviewed trigger filters: "
                f"{sorted(map(str, unexpected_keys))!r}"
            )
        paths = event_config.get("paths")
        if paths != expected_paths:
            scope = "unfiltered" if expected_paths is None else repr(expected_paths)
            errors.append(
                f"{event}.paths must match the protected path scope {scope}; got {paths!r}"
            )

    return errors


def validate_edge_triggers(workflow: Path) -> list[str]:
    return validate_workflow_triggers(workflow, EXPECTED_PATHS)


def validate_independent_guard(workflow: Path) -> list[str]:
    try:
        document = yaml.load(
            workflow.read_text(encoding="utf-8"), Loader=ContractLoader
        )
    except (OSError, yaml.YAMLError) as exc:
        return [f"cannot parse independent guard workflow YAML: {exc}"]

    jobs = document.get("jobs") if isinstance(document, dict) else None
    if not isinstance(jobs, dict):
        return ["independent guard workflow must define jobs"]

    commands = {
        step.get("run")
        for job in jobs.values()
        if isinstance(job, dict)
        for step in job.get("steps", [])
        if isinstance(step, dict) and isinstance(step.get("run"), str)
    }
    missing = REQUIRED_GUARD_COMMANDS - commands
    if missing:
        return [
            "independent guard workflow must invoke: "
            + ", ".join(sorted(missing))
        ]
    return []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "workflows",
        nargs="*",
        type=Path,
        help="Specific contracted workflow paths to check (defaults to all).",
    )
    args = parser.parse_args()

    workflows = args.workflows or [Path(path) for path in WORKFLOW_CONTRACTS]
    failed = False
    for workflow in workflows:
        key = workflow.as_posix()
        expected_paths = WORKFLOW_CONTRACTS.get(key)
        if key not in WORKFLOW_CONTRACTS:
            print(f"Trigger check failed: no reviewed contract for {key}")
            failed = True
            continue
        errors = validate_workflow_triggers(workflow, expected_paths)
        for error in errors:
            print(f"{key} trigger check failed: {error}")
            failed = True

    for error in validate_independent_guard(INDEPENDENT_GUARD_WORKFLOW):
        print(f"Independent trigger guard check failed: {error}")
        failed = True

    if failed:
        return 1

    print(
        "Critical CI pull-request and main-push trigger contracts are valid "
        f"for {len(workflows)} workflow(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())