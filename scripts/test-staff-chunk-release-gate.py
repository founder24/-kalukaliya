#!/usr/bin/env python3

import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("check-staff-chunk-release-gate.py")
SPEC = importlib.util.spec_from_file_location("staff_chunk_release_gate", SCRIPT)
assert SPEC and SPEC.loader
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


VALID_WORKFLOW = """
on:
  workflow_dispatch:
jobs:
  deploy-frontend:
    steps:
      - name: Build frontend
        run: pnpm --filter @workspace/syrabit run build
      - name: Verify staff production chunks
        run: pnpm --filter @workspace/syrabit run check:staff-chunks
      - name: Upload staff production chunk diagnostics
        if: ${{ failure() }}
        uses: actions/upload-artifact@v4
        with:
          name: staff-production-chunk-diagnostics
          path: |
            apps/frontend/dist/.vite/manifest.json
            apps/frontend/dist/assets/
      - name: Deploy to Cloudflare Pages
        run: pnpm exec wrangler pages deploy ../frontend/dist
"""


class StaffChunkReleaseGateTests(unittest.TestCase):
    def validate(self, content: str) -> list[str]:
        with tempfile.TemporaryDirectory() as directory:
            workflow = Path(directory) / "workflow.yml"
            workflow.write_text(content, encoding="utf-8")
            return CHECKER.validate_staff_chunk_release_gate(workflow)

    def test_accepts_repository_workflow(self) -> None:
        self.assertEqual(
            CHECKER.validate_staff_chunk_release_gate(CHECKER.DEFAULT_WORKFLOW), []
        )

    def test_accepts_complete_contract(self) -> None:
        self.assertEqual(self.validate(VALID_WORKFLOW), [])

    def test_rejects_check_before_build(self) -> None:
        broken = VALID_WORKFLOW.replace(
            """      - name: Build frontend
        run: pnpm --filter @workspace/syrabit run build
      - name: Verify staff production chunks
        run: pnpm --filter @workspace/syrabit run check:staff-chunks
""",
            """      - name: Verify staff production chunks
        run: pnpm --filter @workspace/syrabit run check:staff-chunks
      - name: Build frontend
        run: pnpm --filter @workspace/syrabit run build
""",
        )
        self.assertTrue(any("must follow" in error for error in self.validate(broken)))

    def test_rejects_check_after_publish(self) -> None:
        check = """      - name: Verify staff production chunks
        run: pnpm --filter @workspace/syrabit run check:staff-chunks
"""
        broken = VALID_WORKFLOW.replace(check, "").replace(
            "      - name: Deploy to Cloudflare Pages\n",
            "      - name: Deploy to Cloudflare Pages\n",
        ) + check
        self.assertTrue(
            any("before Cloudflare Pages" in error for error in self.validate(broken))
        )

    def test_rejects_removed_check(self) -> None:
        broken = VALID_WORKFLOW.replace(
            """      - name: Verify staff production chunks
        run: pnpm --filter @workspace/syrabit run check:staff-chunks
""",
            "",
        )
        self.assertTrue(any("staff chunk check" in error for error in self.validate(broken)))

    def test_rejects_disabled_failure_upload(self) -> None:
        broken = VALID_WORKFLOW.replace("${{ failure() }}", "${{ success() }}")
        self.assertTrue(any("on failure" in error for error in self.validate(broken)))

    def test_rejects_missing_diagnostic_path(self) -> None:
        broken = VALID_WORKFLOW.replace(
            "            apps/frontend/dist/assets/\n", ""
        )
        self.assertTrue(any("missing required paths" in error for error in self.validate(broken)))


if __name__ == "__main__":
    unittest.main()