#!/usr/bin/env python3

import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("check-ci-workflow-concurrency.py")
SPEC = importlib.util.spec_from_file_location("concurrency_check", SCRIPT)
assert SPEC and SPEC.loader
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)

VALID_CONCURRENCY = """\
name: CI
on: [push]
concurrency:
  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: true
jobs: {}
"""


class CIWorkflowConcurrencyTests(unittest.TestCase):
    def validate(self, content: str) -> list[str]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "workflow.yml"
            path.write_text(content, encoding="utf-8")
            return CHECKER.validate_concurrency(path)

    def test_accepts_both_repository_workflows(self) -> None:
        for workflow in CHECKER.PROTECTED_WORKFLOWS:
            with self.subTest(workflow=workflow):
                self.assertEqual(CHECKER.validate_concurrency(workflow), [])

    def test_rejects_missing_top_level_concurrency(self) -> None:
        errors = self.validate(
            VALID_CONCURRENCY.replace(
                "concurrency:\n"
                "  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}\n"
                "  cancel-in-progress: true\n",
                "",
            )
        )
        self.assertIn("top-level concurrency must be a mapping", errors)

    def test_rejects_group_that_does_not_distinguish_workflows(self) -> None:
        errors = self.validate(VALID_CONCURRENCY.replace("github.workflow", "'ci'"))
        self.assertTrue(any("must be exactly" in error for error in errors))

    def test_rejects_group_that_does_not_distinguish_pull_requests(self) -> None:
        errors = self.validate(
            VALID_CONCURRENCY.replace(
                "github.event.pull_request.number || github.ref", "github.ref"
            )
        )
        self.assertTrue(any("must be exactly" in error for error in errors))

    def test_rejects_group_that_does_not_distinguish_branches(self) -> None:
        errors = self.validate(
            VALID_CONCURRENCY.replace(
                "github.event.pull_request.number || github.ref",
                "github.event.pull_request.number",
            )
        )
        self.assertTrue(any("must be exactly" in error for error in errors))

    def test_rejects_context_names_used_as_static_text(self) -> None:
        errors = self.validate(
            VALID_CONCURRENCY.replace(
                "${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}",
                "github.workflow-github.event.pull_request.number-github.ref",
            )
        )
        self.assertTrue(any("must be exactly" in error for error in errors))

    def test_rejects_partially_interpolated_group(self) -> None:
        errors = self.validate(
            VALID_CONCURRENCY.replace(
                "${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}",
                "github.workflow-${{ github.event.pull_request.number || github.ref }}",
            )
        )
        self.assertTrue(any("must be exactly" in error for error in errors))

    def test_rejects_cancel_in_progress_being_disabled(self) -> None:
        errors = self.validate(
            VALID_CONCURRENCY.replace("cancel-in-progress: true", "cancel-in-progress: false")
        )
        self.assertIn("concurrency.cancel-in-progress must be true", errors)


if __name__ == "__main__":
    unittest.main()