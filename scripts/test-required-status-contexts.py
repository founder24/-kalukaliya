#!/usr/bin/env python3

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check-required-status-contexts.py"


class RequiredStatusContextsTest(unittest.TestCase):
    def run_checker(self, edge_job_name: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflows = root / "workflows"
            workflows.mkdir()
            (workflows / "ci-edge.yml").write_text(
                "name: CI Edge\n"
                "on:\n"
                "  pull_request:\n"
                "    branches: [main]\n"
                "jobs:\n"
                "  test:\n"
                f"    name: {edge_job_name}\n"
                "    runs-on: ubuntu-latest\n",
                encoding="utf-8",
            )
            (workflows / "ci-frontend.yml").write_text(
                "name: CI Frontend\n"
                "on:\n"
                "  pull_request:\n"
                "jobs:\n"
                "  test:\n"
                "    name: Run Tests\n"
                "    runs-on: ubuntu-latest\n",
                encoding="utf-8",
            )
            required = root / "required.json"
            required.write_text(json.dumps(["Run Tests"]), encoding="utf-8")
            owners = root / "owners.json"
            owners.write_text(
                json.dumps({"Run Tests": ["ci-edge.yml"]}), encoding="utf-8"
            )
            return subprocess.run(
                [
                    "python3",
                    str(CHECKER),
                    "--workflows-dir",
                    str(workflows),
                    "--required-contexts-file",
                    str(required),
                    "--context-owners-file",
                    str(owners),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

    def test_accepts_required_run_tests_context(self) -> None:
        result = self.run_checker("Run Tests")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Run Tests", result.stdout)

    def test_rejects_renamed_run_tests_context(self) -> None:
        result = self.run_checker("Edge Tests")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Run Tests", result.stderr)
        self.assertIn("ci-edge.yml no longer emits this context", result.stderr)
        self.assertIn("Restore the job name or update branch protection", result.stderr)


if __name__ == "__main__":
    unittest.main()