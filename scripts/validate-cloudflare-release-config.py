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