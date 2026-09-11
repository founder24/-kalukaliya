#!/usr/bin/env python3
"""Validate the staff chunk gate ordering in the Cloudflare release workflow."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKFLOW = ROOT / ".github" / "workflows" / "deploy-cloudflare.yml"
TRIGGER_CHECKER_PATH = Path(__file__).with_name("check-edge-workflow-triggers.py")
SPEC = importlib.util.spec_from_file_location("workflow_contract_loader", TRIGGER_CHECKER_PATH)
assert SPEC and SPEC.loader
TRIGGER_CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TRIGGER_CHECKER)

FRONTEND_JOB = "deploy-frontend"
BUILD_COMMAND = "pnpm --filter @workspace/syrabit run build"
STAFF_CHECK_COMMAND = "pnpm --filter @workspace/syrabit run check:staff-chunks"
PUBLISH_COMMAND = "wrangler pages deploy"
DIAGNOSTIC_ACTION = "actions/upload-artifact@v4"
DIAGNOSTIC_NAME = "staff-production-chunk-diagnostics"
DIAGNOSTIC_PATHS = {
    "apps/frontend/dist/.vite/manifest.json",
    "apps/frontend/dist/assets/",
}


def _run(step: object) -> str:
    if not isinstance(step, dict):
        return ""
    value = step.get("run")
    return value if isinstance(value, str) else ""


def validate_staff_chunk_release_gate(workflow: Path) -> list[str]:
    try:
        document = yaml.load(
            workflow.read_text(encoding="utf-8"),
            Loader=TRIGGER_CHECKER.ContractLoader,
        )
    except (OSError, yaml.YAMLError) as exc:
        return [f"cannot parse workflow YAML: {exc}"]

    if not isinstance(document, dict):
        return ["workflow document must be a mapping"]
    jobs = document.get("jobs")
    if not isinstance(jobs, dict):
        return ["workflow must define jobs"]
    frontend = jobs.get(FRONTEND_JOB)
    if not isinstance(frontend, dict):
        return [f"workflow must define the {FRONTEND_JOB!r} job"]
    steps = frontend.get("steps")
    if not isinstance(steps, list):
        return [f"{FRONTEND_JOB} must define an ordered steps list"]

    errors: list[str] = []

    def matching_indices(command: str) -> list[int]:
        return [index for index, step in enumerate(steps) if command in _run(step)]

    build = matching_indices(BUILD_COMMAND)
    staff_check = matching_indices(STAFF_CHECK_COMMAND)
    publish = matching_indices(PUBLISH_COMMAND)
    if len(build) != 1:
        errors.append(f"expected exactly one frontend release build step; found {len(build)}")
    if len(staff_check) != 1:
        errors.append(f"expected exactly one staff chunk check step; found {len(staff_check)}")
    if len(publish) != 1:
        errors.append(f"expected exactly one Cloudflare Pages publish step; found {len(publish)}")
    if len(build) == len(staff_check) == 1 and staff_check[0] <= build[0]:
        errors.append("staff chunk check must follow the frontend release build")
    if len(staff_check) == len(publish) == 1 and staff_check[0] >= publish[0]:
        errors.append("staff chunk check must run before Cloudflare Pages publish")

    diagnostics = [
        step
        for step in steps
        if isinstance(step, dict)
        and step.get("uses") == DIAGNOSTIC_ACTION
        and isinstance(step.get("with"), dict)
        and step["with"].get("name") == DIAGNOSTIC_NAME
    ]
    if len(diagnostics) != 1:
        errors.append(
            f"expected exactly one {DIAGNOSTIC_NAME!r} artifact upload step; "
            f"found {len(diagnostics)}"
        )
    else:
        diagnostic = diagnostics[0]
        if diagnostic.get("if") != "${{ failure() }}":
            errors.append("staff chunk diagnostics must upload on failure")
        paths = diagnostic["with"].get("path")
        configured_paths = (
            {line.strip() for line in paths.splitlines() if line.strip()}
            if isinstance(paths, str)
            else set()
        )
        missing_paths = sorted(DIAGNOSTIC_PATHS - configured_paths)
        if missing_paths:
            errors.append(
                "staff chunk diagnostics are missing required paths: "
                + ", ".join(missing_paths)
            )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("workflow", nargs="?", type=Path, default=DEFAULT_WORKFLOW)
    args = parser.parse_args()
    errors = validate_staff_chunk_release_gate(args.workflow)
    for error in errors:
        print(f"Staff chunk release gate check failed: {error}")
    if errors:
        return 1
    print("Staff chunk release gate ordering and failure diagnostics are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())