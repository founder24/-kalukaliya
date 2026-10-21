#!/usr/bin/env python3
"""Guard production release checks that can sign in or invoke live AI."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
TRIGGER_CHECKER_PATH = ROOT / "scripts" / "check-edge-workflow-triggers.py"
SPEC = importlib.util.spec_from_file_location(
    "release_validation_trigger_checker", TRIGGER_CHECKER_PATH
)
assert SPEC and SPEC.loader
TRIGGER_CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TRIGGER_CHECKER)


def load_workflow(path: str) -> dict:
    return TRIGGER_CHECKER.yaml.load(
        (ROOT / path).read_text(encoding="utf-8"),
        Loader=TRIGGER_CHECKER.ContractLoader,
    )


RELEASE = load_workflow(".github/workflows/deploy-cloudflare.yml")
ENTRYPOINT = load_workflow(".github/workflows/deploy.yml")
VALIDATOR = load_workflow(".github/workflows/validate-github-actions.yml")


class ProductionReleaseOptInTests(unittest.TestCase):
    def assert_boolean_opt_in(self, inputs: dict, name: str) -> None:
        with self.subTest(input=name):
            config = inputs.get(name)
            self.assertIsInstance(config, dict)
            self.assertIs(config.get("required"), False)
            self.assertIs(config.get("default"), False)
            self.assertEqual(config.get("type"), "boolean")

    def test_live_chat_and_staff_auth_default_to_off(self) -> None:
        for trigger in ("workflow_dispatch", "workflow_call"):
            inputs = RELEASE["on"][trigger]["inputs"]
            self.assert_boolean_opt_in(inputs, "validate_live_chat")
            self.assert_boolean_opt_in(inputs, "validate_staff_auth")
        self.assert_boolean_opt_in(
            ENTRYPOINT["on"]["workflow_dispatch"]["inputs"],
            "validate_live_chat",
        )
        self.assert_boolean_opt_in(
            ENTRYPOINT["on"]["workflow_dispatch"]["inputs"],
            "validate_staff_auth",
        )

    def test_reusable_release_passes_explicit_boolean_values(self) -> None:
        with_values = ENTRYPOINT["jobs"]["cloudflare-release"]["with"]
        self.assertEqual(
            with_values.get("validate_live_chat"),
            "${{ inputs.validate_live_chat == true }}",
        )
        self.assertEqual(
            with_values.get("validate_staff_auth"),
            "${{ inputs.validate_staff_auth == true }}",
        )

    def test_every_live_chat_job_requires_its_opt_in(self) -> None:
        for job_name in ("chat-performance", "live-chat-contract"):
            with self.subTest(job=job_name):
                condition = str(RELEASE["jobs"][job_name].get("if", ""))
                self.assertIn("inputs.validate_live_chat == true", condition)

    def test_disposable_staff_auth_requires_its_opt_in(self) -> None:
        condition = str(RELEASE["jobs"]["disposable-staff-auth"].get("if", ""))
        self.assertIn("inputs.validate_staff_auth == true", condition)
        self.assertIn("needs.post-native-smoke.result == 'success'", condition)

    def test_staff_opt_in_preserves_both_fixture_cleanup_paths(self) -> None:
        job = RELEASE["jobs"]["disposable-staff-auth"]
        steps = job["steps"]
        run_commands = {step.get("run") for step in steps}
        self.assertIn("bash scripts/run-disposable-staff-auth-cutover.sh", run_commands)
        self.assertIn("bash scripts/run-disposable-staff-portal-check.sh", run_commands)
        cleanup = next(
            step for step in steps
            if step.get("name") == "Always remove an interrupted staff portal fixture"
        )
        self.assertEqual(cleanup.get("if"), "${{ always() }}")
        self.assertEqual(
            cleanup.get("run"),
            'bash scripts/cleanup-disposable-staff-portal.sh "$RUNNER_TEMP/release-staff-portal-fixture.env"',
        )

        auth_script = (ROOT / "scripts/run-disposable-staff-auth-cutover.sh").read_text(
            encoding="utf-8"
        )
        portal_script = (ROOT / "scripts/run-disposable-staff-portal-check.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("trap finish EXIT", auth_script)
        self.assertIn("cleanup_fixture || status=1", auth_script)
        self.assertIn("trap finish EXIT", portal_script)
        self.assertIn("cleanup_fixture || status=1", portal_script)

    def test_public_smoke_and_routing_remain_independent_of_opt_ins(self) -> None:
        for job_name in (
            "post-native-smoke",
            "smoke",
            "deploy-api",
            "deploy-edge",
            "deploy-frontend",
        ):
            with self.subTest(job=job_name):
                job = RELEASE["jobs"][job_name]
                condition = str(job.get("if", ""))
                self.assertNotIn("validate_live_chat", condition)
                self.assertNotIn("validate_staff_auth", condition)
        cors_check = next(
            step for step in RELEASE["jobs"]["smoke"]["steps"]
            if step.get("name") == "Public edge CORS contract is intact"
        )
        self.assertIn("-X OPTIONS", cors_check["run"])

    def test_summary_requires_skips_when_opt_ins_are_off(self) -> None:
        summary = RELEASE["jobs"]["release-check-summary"]
        env = summary["steps"][0]["env"]
        self.assertEqual(env.get("VALIDATE_LIVE_CHAT"), "${{ inputs.validate_live_chat }}")
        self.assertEqual(env.get("VALIDATE_STAFF_AUTH"), "${{ inputs.validate_staff_auth }}")
        run = summary["steps"][0]["run"]
        self.assertIn('local expected="skipped"', run)
        self.assertIn('[[ "$enabled" == "true" ]] && expected="success"', run)

    def test_authenticated_cutover_chat_also_requires_live_chat_opt_in(self) -> None:
        steps = RELEASE["jobs"]["post-native-smoke"]["steps"]
        cutover = next(
            step for step in steps
            if step.get("name") == "Validate authenticated Cloudflare API routing"
        )
        env = cutover["env"]
        self.assertEqual(
            env.get("CUTOVER_STAGE"),
            "${{ inputs.validate_authenticated && 'full' || 'public' }}",
        )
        self.assertEqual(
            env.get("CUTOVER_VALIDATE_CHAT"),
            "${{ inputs.validate_live_chat && inputs.validate_authenticated }}",
        )
        for token_name in ("STUDENT_TOKEN", "STAFF_TOKEN", "ADMIN_SESSION_TOKEN"):
            secret_name = f"CUTOVER_{token_name}"
            with self.subTest(token=token_name):
                self.assertEqual(
                    env.get(token_name),
                    f"${{{{ inputs.validate_authenticated && secrets.{secret_name} || '' }}}}",
                )
        script = (ROOT / "scripts/validate-cloudflare-api-cutover.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('if [[ "$CUTOVER_VALIDATE_CHAT" == "true" ]]; then', script)
        self.assertIn('echo "Authenticated chat request skipped: CUTOVER_VALIDATE_CHAT=false."', script)

    def test_opt_in_guard_runs_in_unfiltered_workflow_validation(self) -> None:
        self.assertIn(
            "python3 scripts/test-release-validation-opt-ins.py",
            {
                step.get("run")
                for job in VALIDATOR["jobs"].values()
                for step in job.get("steps", [])
            },
        )
        for event in ("pull_request", "push"):
            self.assertIn(".github/workflows/**", VALIDATOR["on"][event]["paths"])
            self.assertIn(
                "scripts/test-release-validation-opt-ins.py",
                VALIDATOR["on"][event]["paths"],
            )


if __name__ == "__main__":
    unittest.main()
