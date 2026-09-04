#!/usr/bin/env python3
"""Gate removal of the refresh-token KV bridge on persisted rollout evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys


BRIDGE_MARKER = re.compile(
    r"export\s+const\s+REFRESH_TOKEN_KV_BRIDGE_ENABLED\s*=\s*(true|false)\s*;"
)
ROUTE_GUARDS = {
    "logout-route": {
        "bridge_blocks": ("logout-kv",),
        "full_hash": "5630d3a1f512d27af5114db056063dee55595a30788c6b09d858ef10b7c4f55b",
        "d1_hash": "1974b5eec70bbd183950420bb5013a339dffc8c8603dac7a3ec6600aa2c3d1ab",
    },
    "refresh-route": {
        "bridge_blocks": ("legacy-read", "refresh-kv"),
        "full_hash": "1ed08b05b3d4f27f30e58d2ccd319676bb2f9fc8f18da45b2c9362c093191c03",
        "d1_hash": "29efb8621bbf398b8cbc96721d241e279aed942ca0a15a17a9719b55974761dd",
    },
}
SHA = re.compile(r"^[0-9a-f]{40}$")


def fail(message: str) -> int:
    print(f"::error title=Refresh-token bridge safety gate::{message}", file=sys.stderr)
    return 1


def eligibility_timestamp(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def read_evidence(raw: str) -> dict[str, object] | None:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None

    # wrangler d1 execute --json returns an array of result envelopes.
    if not isinstance(payload, list) or len(payload) != 1:
        return None
    result = payload[0]
    if not isinstance(result, dict):
        return None
    rows = result.get("results")
    if not isinstance(rows, list) or len(rows) != 1:
        return None
    row = rows[0]
    return row if isinstance(row, dict) else None


def guarded_block(source: str, name: str) -> str | None:
    pattern = re.compile(
        rf"^[ \t]*// REFRESH_TOKEN_ROLLOUT_GUARD: {re.escape(name)}:start\n"
        rf"(?P<body>.*?)"
        rf"^[ \t]*// REFRESH_TOKEN_ROLLOUT_GUARD: {re.escape(name)}:end$",
        re.MULTILINE | re.DOTALL,
    )
    matches = list(pattern.finditer(source))
    if len(matches) != 1:
        return None
    return matches[0].group("body")


def without_bridge_blocks(route: str, names: tuple[str, ...]) -> str | None:
    normalized = route
    for name in names:
        pattern = re.compile(
            rf"^[ \t]*// REFRESH_TOKEN_ROLLOUT_GUARD: {re.escape(name)}:start\n"
            rf".*?"
            rf"^[ \t]*// REFRESH_TOKEN_ROLLOUT_GUARD: {re.escape(name)}:end\n?",
            re.MULTILINE | re.DOTALL,
        )
        normalized, count = pattern.subn(
            f"// REFRESH_TOKEN_ROLLOUT_GUARD: {name}:normalized\n",
            normalized,
        )
        if count != 1:
            return None
    return normalized


def digest(value: str | None) -> str | None:
    return hashlib.sha256(value.encode()).hexdigest() if value is not None else None


def validate_evidence(row: dict[str, object], now: int, ttl: int) -> tuple[int, str, str] | str:
    required = (
        "first_deployed_at",
        "first_version",
        "last_deployed_at",
        "last_version",
        "successful_deployments",
    )
    if any(key not in row for key in required):
        return "D1 rollout evidence is incomplete; refusing to remove the bridge."

    first_at = row["first_deployed_at"]
    last_at = row["last_deployed_at"]
    first_version = row["first_version"]
    last_version = row["last_version"]
    deployments = row["successful_deployments"]
    if not (
        isinstance(first_at, int)
        and not isinstance(first_at, bool)
        and isinstance(last_at, int)
        and not isinstance(last_at, bool)
        and isinstance(first_version, str)
        and isinstance(last_version, str)
        and isinstance(deployments, int)
        and not isinstance(deployments, bool)
    ):
        return "D1 rollout evidence has invalid types; refusing to remove the bridge."
    if (
        first_at <= 0
        or last_at < first_at
        or first_at > now
        or last_at > now
        or not SHA.fullmatch(first_version)
        or not SHA.fullmatch(last_version)
        or deployments < 1
    ):
        return "D1 rollout evidence is ambiguous or impossible; refusing to remove the bridge."

    eligible_at = first_at + ttl
    return eligible_at, first_version, last_version


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--auth-source", required=True, type=Path)
    parser.add_argument("--evidence-json", required=True)
    parser.add_argument("--now", required=True, type=int)
    parser.add_argument("--refresh-token-ttl-seconds", required=True, type=int)
    args = parser.parse_args()
    if args.now <= 0 or args.refresh_token_ttl_seconds <= 0:
        return fail("The release time or refresh-token TTL is invalid; refusing deployment.")

    source = args.auth_source.read_text(encoding="utf-8")
    matches = BRIDGE_MARKER.findall(source)
    if len(matches) != 1:
        return fail(
            "The refresh-token bridge status marker is missing or duplicated; "
            "refusing to guess whether cleanup is being deployed."
        )

    bridge_enabled = matches[0] == "true"
    changed_d1_routes: list[str] = []
    changed_bridge_routes: list[str] = []
    for route_name, guard in ROUTE_GUARDS.items():
        route = guarded_block(source, route_name)
        bridge_blocks = guard["bridge_blocks"]
        assert isinstance(bridge_blocks, tuple)
        normalized = without_bridge_blocks(route, bridge_blocks) if route is not None else None
        if digest(normalized) != guard["d1_hash"]:
            changed_d1_routes.append(route_name)
        if bridge_enabled and digest(route) != guard["full_hash"]:
            changed_bridge_routes.append(route_name)

    if changed_d1_routes:
        return fail(
            "Authoritative D1 refresh-token route behavior changed in: "
            + ", ".join(changed_d1_routes)
            + ". Cleanup may edit only inside the designated KV bridge spans."
        )

    if changed_bridge_routes:
        return fail(
            "The bridge marker is enabled but protected route behavior changed in: "
            + ", ".join(changed_bridge_routes)
            + ". Set the marker to false only after the safety window is eligible."
        )

    row = read_evidence(args.evidence_json)
    if row is None:
        if bridge_enabled:
            print(
                "Refresh-token bridge remains enabled; no D1 rollout evidence exists yet. "
                "A cleanup release will remain blocked until evidence is recorded."
            )
            return 0
        return fail("D1 rollout evidence is missing or ambiguous; refusing to remove the bridge.")

    evidence = validate_evidence(row, args.now, args.refresh_token_ttl_seconds)
    if isinstance(evidence, str):
        return fail(evidence)
    eligible_at, first_version, last_version = evidence
    timestamp = eligibility_timestamp(eligible_at)
    print(
        f"Refresh-token bridge cleanup eligibility date: {timestamp} "
        f"(first D1-claims version {first_version}, latest recorded version {last_version})."
    )

    if bridge_enabled:
        print("Refresh-token KV bridge remains enabled; safety-window gate is informational.")
        return 0
    if args.now < eligible_at:
        remaining = eligible_at - args.now
        return fail(
            f"Refresh-token bridge cleanup is not eligible until {timestamp} "
            f"({remaining} seconds remaining); refusing deployment."
        )

    print(f"Refresh-token bridge cleanup is eligible as of {timestamp}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())