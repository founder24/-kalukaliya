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


def workflow(paths: list[str] | None = None, include_push: bool = True) -> str:
    paths = paths or CHECKER.EXPECTED_PATHS
    path_lines = "\n".join(f"      - '{path}'" for path in paths)
    push = (
        f"\n  push:\n    branches: [main]\n    paths:\n{path_lines}\n"
        if include_push
        else ""
    )
    return (
        "name: Edge\non:\n"
        f"  pull_request:\n    branches: [main]\n    paths:\n{path_lines}\n"
        f"{push}jobs: {{}}\n"
    )


class EdgeWorkflowTriggerTests(unittest.TestCase):
    def validate(self, content: str) -> list[str]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ci-edge.yml"
            path.write_text(content, encoding="utf-8")
            return CHECKER.validate_edge_triggers(path)

    def test_accepts_intended_scope(self) -> None:
        self.assertEqual(self.validate(workflow()), [])

    def test_rejects_removed_path(self) -> None:
        errors = self.validate(workflow(CHECKER.EXPECTED_PATHS[:-1]))
        self.assertTrue(any("path scope" in error for error in errors))

    def test_rejects_missing_push_trigger(self) -> None:
        self.assertIn("missing push trigger", self.validate(workflow(include_push=False)))


if __name__ == "__main__":
    unittest.main()