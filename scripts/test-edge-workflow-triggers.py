#!/usr/bin/env python3

import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("check-edge-workflow-triggers.py")
SPEC = importlib.util.spec_from_file_location("edge_trigger_check", SCRIPT)
assert SPEC and SPEC.loader
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


def workflow(
    paths: list[str] | None,
    include_push: bool = True,
    branches: list[str] | None = None,
    ignored_filter: tuple[str, list[str]] | None = None,
) -> str:
    branch_list = ", ".join(branches or ["main"])
    path_block = ""
    if paths is not None:
        path_lines = "\n".join(f"      - '{path}'" for path in paths)
        path_block = f"    paths:\n{path_lines}\n"
    ignored_block = ""
    if ignored_filter is not None:
        key, values = ignored_filter
        value_lines = "\n".join(f"      - '{value}'" for value in values)
        ignored_block = f"    {key}:\n{value_lines}\n"
    push = (
        f"\n  push:\n    branches: [{branch_list}]\n{path_block}{ignored_block}"
        if include_push
        else ""
    )
    return (
        "name: Edge\non:\n"
        f"  pull_request:\n    branches: [{branch_list}]\n{path_block}{ignored_block}"
        f"{push}jobs: {{}}\n"
    )


class CriticalWorkflowTriggerTests(unittest.TestCase):
    def validate(self, content: str, expected_paths: list[str] | None) -> list[str]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workflow.yml"
            path.write_text(content, encoding="utf-8")
            return CHECKER.validate_workflow_triggers(path, expected_paths)

    def test_accepts_every_repository_contract(self) -> None:
        for path, expected_paths in CHECKER.WORKFLOW_CONTRACTS.items():
            with self.subTest(workflow=path):
                self.assertEqual(
                    CHECKER.validate_workflow_triggers(Path(path), expected_paths), []
                )

    def test_unfiltered_api_ci_invokes_trigger_guard(self) -> None:
        self.assertEqual(
            CHECKER.validate_independent_guard(CHECKER.INDEPENDENT_GUARD_WORKFLOW),
            [],
        )

    def test_rejects_independent_ci_without_trigger_guard(self) -> None:
        content = Path(CHECKER.INDEPENDENT_GUARD_WORKFLOW).read_text(
            encoding="utf-8"
        ).replace(
            "python3 scripts/check-edge-workflow-triggers.py",
            "echo trigger-check-disabled",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ci-api.yml"
            path.write_text(content, encoding="utf-8")
            errors = CHECKER.validate_independent_guard(path)
        self.assertTrue(any("must invoke" in error for error in errors))

    def test_validator_contract_rejects_missing_pull_request_event(self) -> None:
        content = workflow(CHECKER.VALIDATOR_EXPECTED_PATHS).replace(
            "  pull_request:", "  pull_request_disabled:"
        )
        self.assertIn(
            "missing pull_request trigger",
            self.validate(content, CHECKER.VALIDATOR_EXPECTED_PATHS),
        )

    def test_validator_contract_rejects_missing_push_event(self) -> None:
        self.assertIn(
            "missing push trigger",
            self.validate(
                workflow(
                    CHECKER.VALIDATOR_EXPECTED_PATHS,
                    include_push=False,
                ),
                CHECKER.VALIDATOR_EXPECTED_PATHS,
            ),
        )

    def test_validator_contract_rejects_branch_drift(self) -> None:
        errors = self.validate(
            workflow(
                CHECKER.VALIDATOR_EXPECTED_PATHS,
                branches=["develop"],
            ),
            CHECKER.VALIDATOR_EXPECTED_PATHS,
        )
        self.assertTrue(any("branches must be exactly" in error for error in errors))

    def test_validator_contract_rejects_path_scope_change(self) -> None:
        errors = self.validate(
            workflow(CHECKER.VALIDATOR_EXPECTED_PATHS[:-1]),
            CHECKER.VALIDATOR_EXPECTED_PATHS,
        )
        self.assertTrue(any("path scope" in error for error in errors))

    def test_rejects_removed_path(self) -> None:
        errors = self.validate(
            workflow(CHECKER.EXPECTED_PATHS[:-1]), CHECKER.EXPECTED_PATHS
        )
        self.assertTrue(any("path scope" in error for error in errors))

    def test_rejects_added_path(self) -> None:
        errors = self.validate(
            workflow([*CHECKER.EXPECTED_PATHS, "unexpected/**"]),
            CHECKER.EXPECTED_PATHS,
        )
        self.assertTrue(any("path scope" in error for error in errors))

    def test_rejects_missing_push_trigger(self) -> None:
        self.assertIn(
            "missing push trigger",
            self.validate(
                workflow(CHECKER.EXPECTED_PATHS, include_push=False),
                CHECKER.EXPECTED_PATHS,
            ),
        )

    def test_rejects_missing_pull_request_trigger(self) -> None:
        content = workflow(CHECKER.EXPECTED_PATHS).replace(
            "  pull_request:", "  pull_request_disabled:"
        )
        self.assertIn(
            "missing pull_request trigger",
            self.validate(content, CHECKER.EXPECTED_PATHS),
        )

    def test_rejects_branch_scope_change(self) -> None:
        errors = self.validate(
            workflow(CHECKER.EXPECTED_PATHS, branches=["develop"]),
            CHECKER.EXPECTED_PATHS,
        )
        self.assertTrue(any("branches must be exactly" in error for error in errors))

    def test_rejects_unintended_api_path_filter(self) -> None:
        errors = self.validate(workflow(["apps/edge/**"]), None)
        self.assertTrue(any("path scope unfiltered" in error for error in errors))

    def test_rejects_unintended_api_paths_ignore_filter(self) -> None:
        errors = self.validate(
            workflow(None, ignored_filter=("paths-ignore", ["docs/**"])), None
        )
        self.assertTrue(any("paths-ignore" in error for error in errors))

    def test_rejects_quoted_api_paths_ignore_filter(self) -> None:
        content = workflow(
            None, ignored_filter=("paths-ignore", ["docs/**"])
        ).replace("    paths-ignore:", "    'paths-ignore':")
        errors = self.validate(content, None)
        self.assertTrue(any("paths-ignore" in error for error in errors))

    def test_rejects_branch_ignore_filter(self) -> None:
        errors = self.validate(
            workflow(
                CHECKER.EXPECTED_PATHS,
                ignored_filter=("branches-ignore", ["release/**"]),
            ),
            CHECKER.EXPECTED_PATHS,
        )
        self.assertTrue(any("branches-ignore" in error for error in errors))

    def test_rejects_unintended_pull_request_types_filter(self) -> None:
        content = workflow(
            CHECKER.EXPECTED_PATHS, ignored_filter=("types", ["closed"])
        )
        errors = self.validate(content, CHECKER.EXPECTED_PATHS)
        self.assertTrue(any("types" in error for error in errors))

    def test_rejects_quoted_pull_request_types_filter(self) -> None:
        content = workflow(
            CHECKER.EXPECTED_PATHS, ignored_filter=("types", ["closed"])
        ).replace("    types:", '    "types":')
        errors = self.validate(content, CHECKER.EXPECTED_PATHS)
        self.assertTrue(any("types" in error for error in errors))

    def test_rejects_explicit_mapping_key_filter(self) -> None:
        content = workflow(None).replace(
            "    branches: [main]",
            "    branches: [main]\n    ? paths-ignore\n    : [docs/**]",
        )
        errors = self.validate(content, None)
        self.assertTrue(any("paths-ignore" in error for error in errors))

    def test_rejects_duplicate_event_filter_keys(self) -> None:
        content = workflow(CHECKER.EXPECTED_PATHS).replace(
            "    branches: [main]",
            "    branches: [main]\n    branches: [develop]",
        )
        errors = self.validate(content, CHECKER.EXPECTED_PATHS)
        self.assertTrue(any("duplicate key" in error for error in errors))


if __name__ == "__main__":
    unittest.main()