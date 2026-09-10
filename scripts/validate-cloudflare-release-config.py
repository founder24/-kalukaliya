#!/usr/bin/env python3
"""Fail a Cloudflare release when required production bindings/routes are absent."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def binding_names(config: dict, section: str) -> set[str]:
    return {
        item.get("binding", "")
        for item in config.get("env", {}).get("production", {}).get(section, [])
    }


def require(label: str, actual: set[str], expected: set[str]) -> list[str]:
    missing = sorted(expected - actual)
    return [f"{label} is missing: {', '.join(missing)}"] if missing else []


def main() -> int:
    api = tomllib.loads((ROOT / "apps/api/wrangler.toml").read_text())
    edge = tomllib.loads((ROOT / "apps/edge/wrangler.toml").read_text())
    routes = json.loads((ROOT / "apps/frontend/public/_routes.json").read_text())
    worker = (ROOT / "apps/frontend/public/_worker.js").read_text()
    release = (ROOT / ".github/workflows/deploy-cloudflare.yml").read_text()
    staff_rehearsal = (
        ROOT / ".github/workflows/verify-production-staff-portal.yml"
    ).read_text()
    indexnow_submitter = (ROOT / "apps/frontend/scripts/indexnow-submit.mjs").read_text()
    errors: list[str] = []

    errors += require("API D1 bindings", binding_names(api, "d1_databases"), {"DB"})
    errors += require("API R2 bindings", binding_names(api, "r2_buckets"), {"R2_BUCKET"})
    errors += require(
        "API KV bindings",
        binding_names(api, "kv_namespaces"),
        {"CONTENT_KV", "RATE_LIMIT_KV"},
    )
    errors += require("API Vectorize bindings", binding_names(api, "vectorize"), {"VECTORIZE"})
    if api.get("env", {}).get("production", {}).get("ai", {}).get("binding") != "AI":
        errors.append("API Workers AI binding AI is missing")

    errors += require(
        "Edge KV bindings",
        binding_names(edge, "kv_namespaces"),
        {"CONTENT_KV", "RATE_LIMIT_KV", "ISR_CACHE_KV"},
    )
    errors += require("Edge R2 bindings", binding_names(edge, "r2_buckets"), {"R2_BUCKET"})
    errors += require("Edge service bindings", binding_names(edge, "services"), {"API_WORKER"})
    if edge.get("env", {}).get("production", {}).get("ai", {}).get("binding") != "AI":
        errors.append("Edge Workers AI binding AI is missing")

    excluded = set(routes.get("exclude", []))
    for route in ("/feed.xml", "/feed.json", "/feed/*", "/llms.txt", "/llms-full.txt", "/robots.txt"):
        if route in excluded:
            errors.append(f"Pages route {route} must reach the custom Worker")
    for marker in ("SEO_PASSTHROUGH_RE", "bot-render-not-found", "env.ASSETS.fetch"):
        if marker not in worker:
            errors.append(f"Pages custom Worker is missing required behavior marker: {marker}")

    for marker in (
        "INDEXNOW_SECRET: ${{ secrets.INDEXNOW_INTERNAL_SECRET }}",
        "INDEXNOW_BACKEND_URL: https://api.syrabit.ai",
    ):
        if marker not in release:
            errors.append(f"Cloudflare release is missing required IndexNow wiring: {marker}")
    for marker in (
        "rehearse_chat_latency_failure:",
        "github.event_name == 'workflow_dispatch' && inputs.rehearse_chat_latency_failure",
        "needs: [chat-performance, disposable-staff-auth]",
        "CHAT_PERFORMANCE_RESULT: ${{ needs.chat-performance.result }}",
        "STAFF_ACCESS_RESULT: ${{ needs.disposable-staff-auth.result }}",
        'echo "| Chat first-token latency | ${CHAT_PERFORMANCE_RESULT} |"',
        'echo "| Disposable staff access | ${STAFF_ACCESS_RESULT} |"',
    ):
        if marker not in release:
            errors.append(
                "Cloudflare release is missing required independent-check rehearsal wiring: "
                + marker
            )
    staff_job = release.partition("  disposable-staff-auth:")[2].partition(
        "  release-check-summary:"
    )[0]
    if "needs: post-native-smoke" not in staff_job:
        errors.append(
            "Disposable staff verification must depend on post-native-smoke, not chat performance"
        )
    if "needs: chat-performance" in staff_job:
        errors.append(
            "Disposable staff verification must remain independent of chat performance"
        )
    for marker in (
        "bash scripts/run-disposable-staff-portal-check.sh",
        "if: ${{ always() }}",
        'bash scripts/cleanup-disposable-staff-portal.sh "$RUNNER_TEMP/release-staff-portal-fixture.env"',
    ):
        if marker not in staff_job:
            errors.append(
                "Disposable staff verification is missing required lease/cleanup wiring: "
                + marker
            )
    for marker in (
        "rehearse_chat_latency_failure:",
        "needs: confirm-production",
        "needs: [chat-performance, staff-portal]",
        "CHAT_PERFORMANCE_RESULT: ${{ needs.chat-performance.result }}",
        "STAFF_ACCESS_RESULT: ${{ needs.staff-portal.result }}",
        'echo "| Chat first-token latency | ${CHAT_PERFORMANCE_RESULT} |"',
        'echo "| Disposable staff access | ${STAFF_ACCESS_RESULT} |"',
        'bash scripts/cleanup-disposable-staff-portal.sh "$RUNNER_TEMP/release-staff-portal-fixture.env"',
    ):
        if marker not in staff_rehearsal:
            errors.append(
                "Staff verification rehearsal is missing required independent-check wiring: "
                + marker
            )
    if 'process.env.INDEXNOW_BACKEND_URL || "https://api.syrabit.ai"' not in indexnow_submitter:
        errors.append("IndexNow submitter must default to the production API origin")

    if errors:
        print("Cloudflare release configuration is incomplete:", file=sys.stderr)
        for error in errors:
            print(f" - {error}", file=sys.stderr)
        return 1
    print("Cloudflare production bindings and crawler routes are complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())