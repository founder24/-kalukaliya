#!/usr/bin/env python3
"""Verify that branch-protection status contexts are emitted by PR workflows."""

from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
from pathlib import Path
from typing import TypeAlias


WORKFLOW_SUFFIXES = {".yml", ".yaml"}
MatrixValue: TypeAlias = (
    str | int | float | bool | None | list["MatrixValue"] | dict[str, "MatrixValue"]
)


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


def _split_inline(value: str) -> list[str]:
    items: list[str] = []
    start = 0
    depth = 0
    quote = None
    for index, char in enumerate(value):
        if char in {"'", '"'}:
            quote = None if quote == char else char if quote is None else quote
        elif quote is None:
            if char in "[{":
                depth += 1
            elif char in "]}":
                depth -= 1
            elif char == "," and depth == 0:
                items.append(value[start:index].strip())
                start = index + 1
    items.append(value[start:].strip())
    return [item for item in items if item]


def _split_mapping_field(value: str) -> tuple[str, str] | None:
    depth = 0
    quote = None
    for index, char in enumerate(value):
        if char in {"'", '"'}:
            quote = None if quote == char else char if quote is None else quote
        elif quote is None:
            if char in "[{":
                depth += 1
            elif char in "]}":
                depth -= 1
            elif (
                char == ":"
                and depth == 0
                and (index + 1 == len(value) or value[index + 1].isspace())
            ):
                return _scalar(value[:index]), value[index + 1 :].strip()
    return None


def _value(value: str) -> MatrixValue:
    value = _strip_yaml_comment(value).strip()
    if value.startswith("[") and value.endswith("]"):
        return [_value(item) for item in _split_inline(value[1:-1])]
    if value.startswith("{") and value.endswith("}"):
        result: dict[str, MatrixValue] = {}
        for field in _split_inline(value[1:-1]):
            pair = _split_mapping_field(field)
            if pair is None:
                raise ValueError(f"invalid inline matrix object field: {field}")
            key, field_value = pair
            result[key] = _value(field_value)
        return result
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return _scalar(value)
    if value in {"true", "false"}:
        return value == "true"
    if value in {"null", "~"}:
        return None
    if re.fullmatch(r"-?(?:0|[1-9][0-9]*)", value):
        return int(value)
    if re.fullmatch(r"-?(?:0|[1-9][0-9]*)\.[0-9]+", value):
        return float(value)
    return _scalar(value)


def _inline_list(value: str) -> list[MatrixValue] | None:
    value = _strip_yaml_comment(value).strip()
    if not (value.startswith("[") and value.endswith("]")):
        return None
    return [_value(item) for item in _split_inline(value[1:-1])]


def _render_matrix_value(value: MatrixValue) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    if isinstance(value, bool):
        return str(value).lower()
    if value is None:
        return "null"
    return str(value)


def _matrix_contexts(
    job_name: str,
    axes: dict[str, list[MatrixValue]],
    excludes: list[dict[str, MatrixValue]],
    includes: list[dict[str, MatrixValue]],
) -> set[str]:
    if not axes and not includes:
        return {job_name}

    axis_names = list(axes)
    original_combinations = [
        dict(zip(axis_names, values))
        for values in itertools.product(*(axes[name] for name in axis_names))
    ] if axes else []
    original_combinations = [
        combination
        for combination in original_combinations
        if not any(
            all(combination.get(key) == value for key, value in exclusion.items())
            for exclusion in excludes
        )
    ]
    combinations = [combination.copy() for combination in original_combinations]
    standalone_includes: list[dict[str, MatrixValue]] = []
    for included in includes:
        merged = False
        for index, original in enumerate(original_combinations):
            if all(
                key not in original or original[key] == value
                for key, value in included.items()
            ):
                combinations[index].update(included)
                merged = True
        if not merged and included not in standalone_includes:
            standalone_includes.append(included.copy())

    combinations.extend(standalone_includes)

    return {
        f"{job_name} "
        f"({', '.join(_render_matrix_value(combination[name]) for name in combination)})"
        for combination in combinations
    }


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
    matrix_axes: dict[str, list[MatrixValue]] = {}
    matrix_excludes: list[dict[str, MatrixValue]] = []
    matrix_includes: list[dict[str, MatrixValue]] = []
    matrix_section: str | None = None
    matrix_item: dict[str, MatrixValue] | None = None
    matrix_axis_item: dict[str, MatrixValue] | None = None
    matrix_axis: str | None = None
    matrix_error: str | None = None

    def save_job() -> None:
        if current_job:
            if matrix_error:
                raise RuntimeError(
                    f"{path.name}: job {current_job} has a matrix that cannot be "
                    f"statically expanded ({matrix_error})"
                )
            contexts.update(
                _matrix_contexts(
                    current_name or current_job,
                    matrix_axes,
                    matrix_excludes,
                    matrix_includes,
                )
            )

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
            matrix_axes = {}
            matrix_excludes = []
            matrix_includes = []
            matrix_section = None
            matrix_item = None
            matrix_axis_item = None
            matrix_axis = None
            matrix_error = None
            continue

        name_match = re.match(r"^    name:\s*(.+?)\s*$", line)
        if current_job and name_match:
            current_name = _scalar(name_match.group(1))
            continue

        matrix_match = re.match(r"^      matrix:\s*(.*?)\s*$", line)
        if current_job and matrix_match:
            matrix_section = "axes"
            matrix_item = None
            matrix_axis_item = None
            matrix_axis = None
            matrix_value = _strip_yaml_comment(matrix_match.group(1)).strip()
            if matrix_value:
                matrix_error = f"unsupported inline or dynamic value: {matrix_value}"
            continue

        section_match = re.match(r"^        (include|exclude):\s*(?:#.*)?$", line)
        if current_job and matrix_section and section_match:
            matrix_section = section_match.group(1)
            matrix_item = None
            matrix_axis_item = None
            matrix_axis = None
            continue

        axis_match = re.match(r"^        ([A-Za-z0-9_-]+):(?:\s*(.+?))?\s*$", line)
        if current_job and matrix_section is not None and axis_match:
            matrix_section = "axes"
            matrix_item = None
            matrix_axis_item = None
            matrix_axis = axis_match.group(1)
            value = axis_match.group(2) or ""
            values = _inline_list(value)
            if value and values is None:
                matrix_error = f"axis {matrix_axis} is not a static list"
                matrix_axes[matrix_axis] = []
            else:
                matrix_axes[matrix_axis] = values or []
            continue

        axis_item_match = re.match(r"^          -\s+(.+?)\s*$", line)
        if (
            current_job
            and matrix_section == "axes"
            and matrix_axis
            and axis_item_match
        ):
            item_text = axis_item_match.group(1)
            pair = _split_mapping_field(item_text)
            if pair:
                key, field_value = pair
                matrix_axis_item = {key: _value(field_value)}
                matrix_axes[matrix_axis].append(matrix_axis_item)
            else:
                matrix_axis_item = None
                matrix_axes[matrix_axis].append(_value(item_text))
            continue

        axis_item_field_match = re.match(
            r"^            ([A-Za-z0-9_-]+):\s*(.+?)\s*$", line
        )
        if (
            matrix_section == "axes"
            and matrix_axis_item is not None
            and axis_item_field_match
        ):
            matrix_axis_item[axis_item_field_match.group(1)] = _value(
                axis_item_field_match.group(2)
            )
            continue

        item_match = re.match(r"^          -\s+([A-Za-z0-9_-]+):\s*(.+?)\s*$", line)
        if current_job and matrix_section in {"include", "exclude"} and item_match:
            matrix_item = {item_match.group(1): _value(item_match.group(2))}
            target = (
                matrix_includes if matrix_section == "include" else matrix_excludes
            )
            target.append(matrix_item)
            continue

        item_field_match = re.match(
            r"^            ([A-Za-z0-9_-]+):\s*(.+?)\s*$", line
        )
        if matrix_item is not None and item_field_match:
            matrix_item[item_field_match.group(1)] = _value(item_field_match.group(2))
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