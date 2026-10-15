#!/usr/bin/env python3
"""Statically enforce the Cloudflare-only production release boundary."""

from __future__ import annotations

from pathlib import Path
import os
import sys
import tomllib
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def main() -> int:
    errors: list[str] = []
    deploy = (WORKFLOWS / "deploy.yml").read_text(encoding="utf-8")
    release = (WORKFLOWS / "deploy-cloudflare.yml").read_text(encoding="utf-8")
    replit_text = (ROOT / ".replit").read_text(encoding="utf-8")
    replit = tomllib.loads(replit_text)

    deployment = replit.get("deployment", {})
    if deployment.get("deploymentTarget") != "static":
        errors.append("Replit deployment must serve the static frontend, not the legacy Python API")
    if deployment.get("publicDir") != "apps/frontend/dist":
        errors.append("Replit deployment publicDir must be apps/frontend/dist")
    if deployment.get("run"):
        errors.append("Replit static deployment must not start a backend process")
    if "MONGODB_DB_NAME" in replit.get("userenv", {}).get("shared", {}):
        errors.append("Replit shared app environment must not configure the retired MongoDB backend")

    start_workflow = next(
        (
            workflow
            for workflow in replit.get("workflows", {}).get("workflow", [])
            if workflow.get("name") == "Start application"
        ),
        {},
    )
    start_command = " ".join(
        str(task.get("args", "")) for task in start_workflow.get("tasks", [])
    )
    if "pnpm --filter @workspace/syrabit run dev" not in start_command:
        errors.append("Replit Start application workflow must launch the Vite frontend")
    if "apps/backend" in start_command or "uvicorn" in start_command:
        errors.append("Replit Start application workflow must not launch the legacy Python backend")

    vite_config = (ROOT / "apps" / "frontend" / "vite.config.js").read_text(encoding="utf-8")
    if "'https://api.syrabit.ai'" not in vite_config:
        errors.append("Local Vite API proxy must default to the Cloudflare Edge Worker")

    edge_index = (ROOT / "apps" / "edge" / "src" / "index.ts").read_text(encoding="utf-8")
    edge_proxy = (ROOT / "apps" / "edge" / "src" / "routes" / "worker-proxy.ts").read_text(encoding="utf-8")
    if "if (requiresApiWorker && !env.API_WORKER)" not in edge_index:
        errors.append("Edge Worker must reject API traffic when the API_WORKER binding is missing")
    if "api_worker_binding_unavailable" not in edge_index:
        errors.append("Edge Worker must return its explicit missing-binding error")
    if "env.API_WORKER!.fetch(outRequest)" not in edge_proxy:
        errors.append("Edge API proxy must forward through the private API_WORKER service binding")

    chat_route = (ROOT / "apps" / "api" / "src" / "routes" / "chat.ts").read_text(encoding="utf-8")
    users_route = (ROOT / "apps" / "api" / "src" / "routes" / "users.ts").read_text(encoding="utf-8")
    if "INSERT INTO quota_usage" not in chat_route or "INSERT INTO anonymous_quota_usage" not in chat_route:
        errors.append("Live chat quota reservation must remain D1-backed")
    if "SELECT count FROM quota_usage" not in users_route or "getDailyChatQuotaUsage(" not in users_route:
        errors.append("Live user quota reads must remain D1-backed")

    # The release workflow passes the resolved Pages API origin here, without
    # printing it. A misconfigured secret must never point the bundle at the
    # retired Python/MongoDB backend.
    frontend_api = os.environ.get("VITE_BACKEND_URL", "https://api.syrabit.ai").strip()
    parsed_api = urlsplit(frontend_api)
    if (
        parsed_api.scheme != "https"
        or parsed_api.netloc != "api.syrabit.ai"
        or parsed_api.path not in ("", "/")
        or parsed_api.query
        or parsed_api.fragment
    ):
        errors.append("Production frontend API origin must be exactly the Cloudflare Edge Worker origin")

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