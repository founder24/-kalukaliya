#!/usr/bin/env python3
"""Focused tests for the refresh-token bridge release safety gate."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts/guard-refresh-token-bridge.py"
AUTH_SOURCE = (ROOT / "apps/api/src/routes/auth.ts").read_text(encoding="utf-8")
SHA_A = "a" * 40
SHA_B = "b" * 40
FIRST_DEPLOYED_AT = 1_700_000_000
TTL = 30 * 24 * 60 * 60


def evidence(
    *,
    first_at: int = FIRST_DEPLOYED_AT,
    last_at: int = FIRST_DEPLOYED_AT + 60,
) -> str:
    return json.dumps([{
        "results": [{
            "first_deployed_at": first_at,
            "first_version": SHA_A,
            "last_deployed_at": last_at,
            "last_version": SHA_B,
            "successful_deployments": 2,
        }],
    }])


class RefreshTokenBridgeGuardTests(unittest.TestCase):
    def run_guard(
        self,
        marker: str,
        rollout_evidence: str,
        now: int,
        *,
        source: str = AUTH_SOURCE,
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            auth_source = Path(directory) / "auth.ts"
            auth_source.write_text(
                source.replace(
                    "export const REFRESH_TOKEN_KV_BRIDGE_ENABLED = true;",
                    f"export const REFRESH_TOKEN_KV_BRIDGE_ENABLED = {marker};",
                ),
                encoding="utf-8",
            )
            return subprocess.run(
                [
                    sys.executable,
                    str(GUARD),
                    "--auth-source",
                    str(auth_source),
                    "--evidence-json",
                    rollout_evidence,
                    "--now",
                    str(now),
                    "--refresh-token-ttl-seconds",
                    str(TTL),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

    def test_enabled_bridge_bootstraps_without_evidence(self) -> None:
        result = self.run_guard("true", '[{"results":[]}]', FIRST_DEPLOYED_AT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("bridge remains enabled", result.stdout)

    def test_cleanup_fails_closed_without_evidence(self) -> None:
        result = self.run_guard("false", '[{"results":[]}]', FIRST_DEPLOYED_AT)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("evidence is missing or ambiguous", result.stderr)

    def test_cleanup_is_blocked_before_full_token_ttl(self) -> None:
        now = FIRST_DEPLOYED_AT + TTL - 1
        result = self.run_guard("false", evidence(), now)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("1 seconds remaining", result.stderr)
        self.assertIn("eligibility date:", result.stdout)

    def test_cleanup_is_allowed_at_full_token_ttl(self) -> None:
        now = FIRST_DEPLOYED_AT + TTL
        result = self.run_guard("false", evidence(), now)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("cleanup is eligible as of", result.stdout)

    def test_cleanup_fails_closed_on_impossible_evidence(self) -> None:
        result = self.run_guard(
            "false",
            evidence(last_at=FIRST_DEPLOYED_AT - 1),
            FIRST_DEPLOYED_AT + TTL,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ambiguous or impossible", result.stderr)

    def test_missing_source_marker_fails_closed(self) -> None:
        result = self.run_guard(
            "true",
            evidence(),
            FIRST_DEPLOYED_AT + TTL,
            source=AUTH_SOURCE.replace(
                "export const REFRESH_TOKEN_KV_BRIDGE_ENABLED = true;",
                "",
            ),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("status marker is missing or duplicated", result.stderr)

    def test_enabled_marker_cannot_hide_removed_bridge_code(self) -> None:
        source_without_one_write = AUTH_SOURCE.replace(
            "await c.env.RATE_LIMIT_KV.put(",
            "await removedRateLimitKvPut(",
            1,
        )
        result = self.run_guard(
            "true",
            evidence(),
            FIRST_DEPLOYED_AT + TTL,
            source=source_without_one_write,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("protected route behavior changed in: logout-route", result.stderr)

    def test_deployment_without_both_d1_claims_is_never_trusted(self) -> None:
        source_without_one_claim = AUTH_SOURCE.replace(
            "await claimRefreshToken(c.env.DB,",
            "await removedClaimRefreshToken(c.env.DB,",
            1,
        )
        result = self.run_guard(
            "true",
            evidence(),
            FIRST_DEPLOYED_AT + TTL,
            source=source_without_one_claim,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Authoritative D1 refresh-token route behavior changed in: logout-route", result.stderr)

    def test_retained_kv_read_without_rejection_is_not_trusted(self) -> None:
        source_without_rejection = AUTH_SOURCE.replace(
            "if (legacyRevoked !== null) {",
            "if (false && legacyRevoked !== null) {",
            1,
        )
        result = self.run_guard(
            "true",
            evidence(),
            FIRST_DEPLOYED_AT + TTL,
            source=source_without_rejection,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("protected route behavior changed in: refresh-route", result.stderr)

    def test_unreachable_kv_write_is_not_trusted(self) -> None:
        source_with_unreachable_write = AUTH_SOURCE.replace(
            "if (REFRESH_TOKEN_KV_BRIDGE_ENABLED) {",
            "if (false && REFRESH_TOKEN_KV_BRIDGE_ENABLED) {",
            1,
        )
        result = self.run_guard(
            "true",
            evidence(),
            FIRST_DEPLOYED_AT + TTL,
            source=source_with_unreachable_write,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("protected route behavior changed in: logout-route", result.stderr)

    def test_eligible_cleanup_cannot_remove_d1_claim(self) -> None:
        source_without_d1_claim = AUTH_SOURCE.replace(
            "await claimRefreshToken(c.env.DB,",
            "await removedClaimRefreshToken(c.env.DB,",
            1,
        )
        result = self.run_guard(
            "false",
            evidence(),
            FIRST_DEPLOYED_AT + TTL,
            source=source_without_d1_claim,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Authoritative D1 refresh-token route behavior changed in: logout-route", result.stderr)

    def test_eligible_cleanup_cannot_disable_replay_rejection(self) -> None:
        source_without_rejection = AUTH_SOURCE.replace(
            "if (!claimed) {",
            "if (false && !claimed) {",
            1,
        )
        result = self.run_guard(
            "false",
            evidence(),
            FIRST_DEPLOYED_AT + TTL,
            source=source_without_rejection,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Authoritative D1 refresh-token route behavior changed in: refresh-route", result.stderr)

    def test_eligible_cleanup_cannot_wrap_d1_claim_in_unreachable_code(self) -> None:
        source_with_unreachable_claim = AUTH_SOURCE.replace(
            "// REFRESH_TOKEN_ROLLOUT_GUARD: refresh-d1:start",
            "if (false) {\n  // REFRESH_TOKEN_ROLLOUT_GUARD: refresh-d1:start",
            1,
        ).replace(
            "// REFRESH_TOKEN_ROLLOUT_GUARD: refresh-d1:end",
            "// REFRESH_TOKEN_ROLLOUT_GUARD: refresh-d1:end\n  }",
            1,
        )
        result = self.run_guard(
            "false",
            evidence(),
            FIRST_DEPLOYED_AT + TTL,
            source=source_with_unreachable_claim,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Authoritative D1 refresh-token route behavior changed in: refresh-route", result.stderr)

    def test_enabled_bridge_cannot_be_wrapped_in_unreachable_code(self) -> None:
        source_with_unreachable_bridge = AUTH_SOURCE.replace(
            "// REFRESH_TOKEN_ROLLOUT_GUARD: legacy-read:start",
            "if (false) {\n  // REFRESH_TOKEN_ROLLOUT_GUARD: legacy-read:start",
            1,
        ).replace(
            "// REFRESH_TOKEN_ROLLOUT_GUARD: legacy-read:end",
            "// REFRESH_TOKEN_ROLLOUT_GUARD: legacy-read:end\n  }",
            1,
        )
        result = self.run_guard(
            "true",
            evidence(),
            FIRST_DEPLOYED_AT + TTL,
            source=source_with_unreachable_bridge,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Authoritative D1 refresh-token route behavior changed in: refresh-route", result.stderr)


if __name__ == "__main__":
    unittest.main()