#!/usr/bin/env python3

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts/check-required-status-contexts.py"


class RequiredStatusContextsTest(unittest.TestCase):
    def run_checker(
        self,
        edge_job_name: str,
        *,
        strategy: str = "",
        required_context: str = "Run Tests",
    ) -> subprocess.CompletedProcess[str]:
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
                f"{strategy}"
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
            required.write_text(json.dumps([required_context]), encoding="utf-8")
            owners = root / "owners.json"
            owners.write_text(
                json.dumps({required_context: ["ci-edge.yml"]}), encoding="utf-8"
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

    def test_accepts_expanded_matrix_context(self) -> None:
        result = self.run_checker(
            "Run Tests",
            strategy=(
                "    strategy:\n"
                "      matrix:\n"
                "        os: [ubuntu-latest, windows-latest]\n"
                "        python: ['3.11', '3.12']\n"
            ),
            required_context="Run Tests (windows-latest, 3.12)",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Run Tests (windows-latest, 3.12)", result.stdout)

    def test_accepts_block_list_matrix_context(self) -> None:
        result = self.run_checker(
            "Run Tests",
            strategy=(
                "    strategy:\n"
                "      matrix:\n"
                "        os:\n"
                "          - ubuntu-latest\n"
                "          - windows-latest\n"
            ),
            required_context="Run Tests (windows-latest)",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_matrix_does_not_emit_unexpanded_static_context(self) -> None:
        result = self.run_checker(
            "Run Tests",
            strategy=(
                "    strategy:\n"
                "      matrix:\n"
                "        os: [ubuntu-latest, windows-latest]\n"
            ),
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("ci-edge.yml no longer emits this context", result.stderr)

    def test_matrix_exclude_and_include_match_generated_contexts(self) -> None:
        strategy = (
            "    strategy:\n"
            "      matrix:\n"
            "        os: [ubuntu-latest, windows-latest]\n"
            "        python: ['3.11', '3.12']\n"
            "        exclude:\n"
            "          - os: windows-latest\n"
            "            python: '3.12'\n"
            "        include:\n"
            "          - os: macos-latest\n"
            "            python: '3.13'\n"
        )
        included = self.run_checker(
            "Run Tests",
            strategy=strategy,
            required_context="Run Tests (macos-latest, 3.13)",
        )
        excluded = self.run_checker(
            "Run Tests",
            strategy=strategy,
            required_context="Run Tests (windows-latest, 3.12)",
        )
        self.assertEqual(included.returncode, 0, included.stderr)
        self.assertEqual(excluded.returncode, 1)

    def test_compatible_include_replaces_unaugmented_contexts(self) -> None:
        strategy = (
            "    strategy:\n"
            "      matrix:\n"
            "        os: [ubuntu-latest, windows-latest]\n"
            "        include:\n"
            "          - experimental: false\n"
        )
        augmented = self.run_checker(
            "Run Tests",
            strategy=strategy,
            required_context="Run Tests (ubuntu-latest, false)",
        )
        stale = self.run_checker(
            "Run Tests",
            strategy=strategy,
            required_context="Run Tests (ubuntu-latest)",
        )
        self.assertEqual(augmented.returncode, 0, augmented.stderr)
        self.assertEqual(stale.returncode, 1)

    def test_redundant_include_keeps_complete_context(self) -> None:
        result = self.run_checker(
            "Run Tests",
            strategy=(
                "    strategy:\n"
                "      matrix:\n"
                "        os: [ubuntu-latest, windows-latest]\n"
                "        python: ['3.11', '3.12']\n"
                "        include:\n"
                "          - os: ubuntu-latest\n"
            ),
            required_context="Run Tests (ubuntu-latest, 3.12)",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_sequential_includes_update_the_same_combinations(self) -> None:
        strategy = (
            "    strategy:\n"
            "      matrix:\n"
            "        os: [ubuntu-latest, windows-latest]\n"
            "        include:\n"
            "          - experimental: false\n"
            "          - os: windows-latest\n"
            "            experimental: true\n"
        )
        windows = self.run_checker(
            "Run Tests",
            strategy=strategy,
            required_context="Run Tests (windows-latest, true)",
        )
        stale_windows = self.run_checker(
            "Run Tests",
            strategy=strategy,
            required_context="Run Tests (windows-latest, false)",
        )
        self.assertEqual(windows.returncode, 0, windows.stderr)
        self.assertEqual(stale_windows.returncode, 1)

    def test_include_can_add_back_an_excluded_combination(self) -> None:
        result = self.run_checker(
            "Run Tests",
            strategy=(
                "    strategy:\n"
                "      matrix:\n"
                "        os: [ubuntu-latest, windows-latest]\n"
                "        exclude:\n"
                "          - os: windows-latest\n"
                "        include:\n"
                "          - os: windows-latest\n"
                "            experimental: false\n"
            ),
            required_context="Run Tests (windows-latest, false)",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_include_only_matrix_expands_contexts(self) -> None:
        result = self.run_checker(
            "Run Tests",
            strategy=(
                "    strategy:\n"
                "      matrix:\n"
                "        include:\n"
                "          - os: ubuntu-latest\n"
                "            python: '3.12'\n"
                "          - os: windows-latest\n"
                "            python: '3.11'\n"
            ),
            required_context="Run Tests (windows-latest, 3.11)",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_dynamic_matrix_fails_closed(self) -> None:
        result = self.run_checker(
            "Run Tests",
            strategy=(
                "    strategy:\n"
                "      matrix: ${{ fromJSON(needs.prepare.outputs.matrix) }}\n"
            ),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot be statically expanded", result.stderr)
        self.assertIn("unsupported inline or dynamic value", result.stderr)

    def test_object_valued_matrix_axis_fails_closed(self) -> None:
        result = self.run_checker(
            "Run Tests",
            strategy=(
                "    strategy:\n"
                "      matrix:\n"
                "        node:\n"
                "          - version: 20\n"
                "            experimental: false\n"
            ),
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot be statically expanded", result.stderr)
        self.assertIn("axis node contains structured values", result.stderr)

    def test_exclude_before_axis_still_expands_matrix(self) -> None:
        strategy = (
            "    strategy:\n"
            "      matrix:\n"
            "        exclude:\n"
            "          - os: windows-latest\n"
            "        os: [ubuntu-latest, windows-latest]\n"
        )
        expanded = self.run_checker(
            "Run Tests",
            strategy=strategy,
            required_context="Run Tests (ubuntu-latest)",
        )
        stale = self.run_checker("Run Tests", strategy=strategy)
        self.assertEqual(expanded.returncode, 0, expanded.stderr)
        self.assertEqual(stale.returncode, 1)

    def test_include_before_axis_still_augments_matrix(self) -> None:
        strategy = (
            "    strategy:\n"
            "      matrix:\n"
            "        include:\n"
            "          - experimental: false\n"
            "        os: [ubuntu-latest, windows-latest]\n"
        )
        augmented = self.run_checker(
            "Run Tests",
            strategy=strategy,
            required_context="Run Tests (windows-latest, false)",
        )
        stale = self.run_checker(
            "Run Tests",
            strategy=strategy,
            required_context="Run Tests (windows-latest)",
        )
        self.assertEqual(augmented.returncode, 0, augmented.stderr)
        self.assertEqual(stale.returncode, 1)


if __name__ == "__main__":
    unittest.main()
