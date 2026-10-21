#!/usr/bin/env python3
"""Guard production release checks that can sign in or invoke live AI."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import re
import subprocess
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
PRODUCTION_RELEASE_SECRETS = {
    "CF_API_TOKEN",
    "CF_ACCOUNT_ID",
    "JWT_SECRET",
    "ADMIN_JWT_SECRET",
    "RESET_TOKEN_SECRET",
    "EDGE_SHARED_SECRET",
    "RESEND_API_KEY",
    "INDEXNOW_API_KEY",
    "INDEXNOW_INTERNAL_SECRET",
}
RESET_DELIVERY_SECRETS = {
    "CUTOVER_RESET_EMAIL",
    "CUTOVER_RESET_LINK",
    "CUTOVER_RESET_PASSWORD",
}
PROTECTED_ENVIRONMENT_SECRETS = PRODUCTION_RELEASE_SECRETS | RESET_DELIVERY_SECRETS


class ProductionReleaseOptInTests(unittest.TestCase):
    def run_workflow_script(self, script: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-euo", "pipefail", "-c", script],
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, **env},
        )

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

    def test_production_release_refs_are_gated_before_secret_scoped_jobs(self) -> None:
        ref_gate = RELEASE["jobs"]["validate-release-ref"]
        self.assertEqual(
            ref_gate["steps"][0]["uses"],
            "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
        )
        self.assertEqual(ref_gate["steps"][0]["with"]["fetch-depth"], 0)
        run = ref_gate["steps"][1]["run"]
        self.assertIn("validate_reset_delivery requires validate_authenticated=true", run)
        self.assertIn("refs/heads/main", run)
        self.assertIn("refs/tags/release/", run)
        self.assertIn("refs/tags/v[0-9]", run)
        self.assertIn("git merge-base --is-ancestor", run)
        self.assertNotIn("secrets.", str(ref_gate))

        verify = RELEASE["jobs"]["verify"]
        self.assertEqual(
            verify.get("needs"),
            ["validate-release-ref", "preflight-reset-delivery-secrets"],
        )
        self.assertIn("needs.validate-release-ref.result == 'success'", verify["if"])
        self.assertIn("needs.preflight-reset-delivery-secrets.result == 'skipped'", verify["if"])
        self.assertEqual(verify.get("environment"), "production")
        self.assertEqual(RELEASE["jobs"]["deploy-api"].get("needs"), "verify")

    def test_release_secret_pass_through_and_permissions_are_scoped(self) -> None:
        validation_secrets = {
            "CUTOVER_STUDENT_TOKEN",
            "CUTOVER_STAFF_TOKEN",
            "CUTOVER_ADMIN_SESSION_TOKEN",
            "TRANSLATE_CRON_SECRET",
            "CF_ACCESS_CLIENT_ID",
            "CF_ACCESS_CLIENT_SECRET",
        }
        forwarded_secrets = validation_secrets | PROTECTED_ENVIRONMENT_SECRETS | {
            "R2_PUBLIC_URL",
            "RATE_LIMIT_CLEANUP_ALERT_WEBHOOK_URL",
            "VITE_BACKEND_URL",
        }
        call_secrets = RELEASE["on"]["workflow_call"]["secrets"]
        caller_secrets = ENTRYPOINT["jobs"]["cloudflare-release"]["secrets"]
        self.assertEqual(set(call_secrets), forwarded_secrets)
        self.assertEqual(set(caller_secrets), forwarded_secrets)
        for name in forwarded_secrets:
            with self.subTest(secret=name):
                self.assertIs(call_secrets[name]["required"], False)
                self.assertEqual(caller_secrets.get(name), f"${{{{ secrets.{name} }}}}")
        self.assertNotIn("inherit", str(caller_secrets).lower())
        release_text = (ROOT / ".github/workflows/deploy-cloudflare.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("  verify:\n", release_text)
        for job_name, job in RELEASE["jobs"].items():
            if not re.search(r"secrets\.[A-Z0-9_]+", str(job)):
                continue
            expected_environment = (
                "cutover-reset-delivery"
                if job_name in {
                    "preflight-reset-delivery-secrets",
                    "request-reset-email",
                    "validate-reset-email",
                }
                else "production"
            )
            with self.subTest(job=job_name):
                self.assertEqual(job.get("environment"), expected_environment)
        reset_preflight = RELEASE["jobs"]["preflight-reset-delivery-secrets"]
        self.assertEqual(reset_preflight.get("environment"), "cutover-reset-delivery")
        self.assertIn("CUTOVER_RESET_EMAIL", reset_preflight["steps"][0]["run"])
        self.assertIn("CUTOVER_RESET_PASSWORD", reset_preflight["steps"][0]["run"])
        preflight = next(
            step for step in RELEASE["jobs"]["verify"]["steps"]
            if step.get("name") == "Require secrets for requested authenticated validations"
        )
        for name in validation_secrets:
            self.assertIn(name, preflight["env"])
        for name in validation_secrets:
            self.assertIn(name, preflight["run"])

        api_preflight = next(
            step for step in RELEASE["jobs"]["verify"]["steps"]
            if step.get("name") == "Require API Worker release secrets"
        )
        required_names = {
            "CF_API_TOKEN",
            "CF_ACCOUNT_ID",
            "JWT_SECRET",
            "ADMIN_JWT_SECRET",
            "RESET_TOKEN_SECRET",
            "EDGE_SHARED_SECRET",
            "RESEND_API_KEY",
            "INDEXNOW_API_KEY",
            "INDEXNOW_INTERNAL_SECRET",
        }
        self.assertLessEqual(required_names, set(api_preflight["env"]))
        for name in required_names:
            self.assertIn(name, api_preflight["run"])
        self.assertIn("${missing[*]}", api_preflight["run"])
        self.assertNotIn('echo "$', preflight["run"])
        self.assertNotIn("issues", RELEASE["permissions"])
        self.assertNotIn("issues", ENTRYPOINT["permissions"])
        self.assertEqual(
            RELEASE["jobs"]["chat-performance"]["permissions"]["issues"],
            "write",
        )
        self.assertEqual(
            ENTRYPOINT["jobs"]["cloudflare-release"]["permissions"]["issues"],
            "write",
        )

    def test_requested_authenticated_validation_fails_with_missing_secret_names(self) -> None:
        preflight = next(
            step for step in RELEASE["jobs"]["verify"]["steps"]
            if step.get("name") == "Require secrets for requested authenticated validations"
        )
        env = {name: "" for name in preflight["env"] if name.startswith(("CUTOVER_", "CF_ACCESS_", "TRANSLATE_"))}
        env.update(VALIDATE_AUTHENTICATED="true", VALIDATE_STAFF_AUTH="false")
        result = self.run_workflow_script(preflight["run"], env)
        self.assertNotEqual(result.returncode, 0)
        output = result.stdout + result.stderr
        for name in (
            "CUTOVER_STUDENT_TOKEN",
            "CUTOVER_STAFF_TOKEN",
            "CUTOVER_ADMIN_SESSION_TOKEN",
            "TRANSLATE_CRON_SECRET",
            "CF_ACCESS_CLIENT_ID",
            "CF_ACCESS_CLIENT_SECRET",
        ):
            self.assertIn(name, output)

        env.update(VALIDATE_AUTHENTICATED="false", VALIDATE_STAFF_AUTH="false")
        self.assertEqual(self.run_workflow_script(preflight["run"], env).returncode, 0)

    def test_reset_validation_preflights_and_names_missing_environment_secrets(self) -> None:
        reset_job = RELEASE["jobs"]["preflight-reset-delivery-secrets"]
        preflight = reset_job["steps"][0]
        result = self.run_workflow_script(
            preflight["run"],
            {"CUTOVER_RESET_EMAIL": "", "CUTOVER_RESET_PASSWORD": ""},
        )
        self.assertNotEqual(result.returncode, 0)
        output = result.stdout + result.stderr
        self.assertIn("CUTOVER_RESET_EMAIL", output)
        self.assertIn("CUTOVER_RESET_PASSWORD", output)

        confirm = next(
            step for step in RELEASE["jobs"]["validate-reset-email"]["steps"]
            if step.get("name") == "Confirm the newly delivered link exactly once"
        )
        result = self.run_workflow_script(
            confirm["run"],
            {
                "CUTOVER_RESET_EMAIL": "release-cutover@example.invalid",
                "CUTOVER_RESET_LINK": "",
                "CUTOVER_RESET_PASSWORD": "temporary-test-value",
            },
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CUTOVER_RESET_LINK", result.stdout + result.stderr)

    def test_production_release_secrets_are_only_read_in_production_jobs(self) -> None:
        workflow_dir = ROOT / ".github" / "workflows"
        paths = sorted(workflow_dir.glob("*.yml")) + sorted(workflow_dir.glob("*.yaml"))
        for path in paths:
            workflow = load_workflow(str(path.relative_to(ROOT)))
            for job_name, job in workflow.get("jobs", {}).items():
                referenced = (
                    set(re.findall(r"secrets\.([A-Z0-9_]+)", str(job)))
                    & PROTECTED_ENVIRONMENT_SECRETS
                )
                if not referenced:
                    continue
                if path.name == "deploy.yml" and job_name == "cloudflare-release":
                    self.assertEqual(
                        job.get("uses"),
                        "./.github/workflows/deploy-cloudflare.yml",
                    )
                    self.assertEqual(referenced, PROTECTED_ENVIRONMENT_SECRETS)
                    continue
                expected_environment = (
                    "cutover-reset-delivery"
                    if referenced & RESET_DELIVERY_SECRETS
                    else "production"
                )
                with self.subTest(workflow=path.name, job=job_name):
                    self.assertEqual(job.get("environment"), expected_environment)

        analytics = load_workflow(".github/workflows/cloudflare-analytics-health.yml")
        analytics_job = analytics["jobs"]["cloudflare-analytics-health"]
        self.assertNotIn("CF_API_TOKEN", str(analytics_job))
        self.assertEqual(
            analytics_job["steps"][1]["env"]["CLOUDFLARE_ZONE_ID"],
            "${{ vars.CF_ZONE_ID }}",
        )

    def test_legacy_python_dependencies_are_not_a_production_gate(self) -> None:
        self.assertNotIn("dep-check", ENTRYPOINT["jobs"])
        self.assertNotIn("skip_dep_check", ENTRYPOINT["on"]["workflow_dispatch"]["inputs"])
        legacy_dependencies = load_workflow(".github/workflows/ci-deps.yml")
        self.assertNotIn("workflow_call", legacy_dependencies["on"])

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
