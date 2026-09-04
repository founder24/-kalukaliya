#!/usr/bin/env python3
"""Statically enforce the Cloudflare-only production release boundary."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def main() -> int:
    errors: list[str] = []
    deploy = (WORKFLOWS / "deploy.yml").read_text(encoding="utf-8")
    release = (WORKFLOWS / "deploy-cloudflare.yml").read_text(encoding="utf-8")

    if "uses: ./.github/workflows/deploy-cloudflare.yml" not in deploy:
        errors.append("deploy.yml must delegate to the canonical Cloudflare release workflow")
    for marker in (
        "validate-cloudflare-release-config.py",
        "test-refresh-token-bridge-guard.py",
        "health/deep",
        "Pages serves crawler artifacts and true 404s",
        "Verify required API Worker secret names",
        "Verify edge Worker secret names",
    ):
        if marker not in release:
            errors.append(f"canonical Cloudflare release is missing validation: {marker}")

    for marker in ("gcloud", "azure/", "az containerapp"):
        if marker in deploy.lower() or marker in release.lower():
            errors.append(f"production release workflow contains retired marker: {marker}")

    forbidden_deploy_commands = (
        "gcloud run",
        "cloud run deploy",
        "azure/login",
        "az containerapp",
    )
    for workflow in WORKFLOWS.glob("*.yml"):
        text = workflow.read_text(encoding="utf-8").lower()
        for marker in forbidden_deploy_commands:
            if marker in text:
                errors.append(
                    f"{workflow.relative_to(ROOT)} contains retired production marker: {marker}"
                )

    if errors:
        print("Cloudflare-only production boundary validation failed:", file=sys.stderr)
        for error in errors:
            print(f" - {error}", file=sys.stderr)
        return 1
    print("Cloudflare-only production release boundary is intact.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())