#!/usr/bin/env python3
"""Regression tests for the Cloudflare-only production release validator."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import tempfile


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = ROOT / "scripts" / "validate-cloudflare-only-production.py"
SPEC = importlib.util.spec_from_file_location("cloudflare_only_validator", VALIDATOR_PATH)
assert SPEC is not None and SPEC.loader is not None
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


def copy_validator_inputs(destination: Path) -> None:
    paths = (
        ".github/workflows",
        ".replit",
        "apps/frontend/vite.config.js",
        "apps/edge/src/index.ts",
        "apps/edge/src/routes/worker-proxy.ts",
        "apps/api/src/routes/chat.ts",
        "apps/api/src/routes/users.ts",
    )
    for relative in paths:
        source = ROOT / relative
        target = destination / relative
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def test_current_d1_quota_reader_passes() -> None:
    with tempfile.TemporaryDirectory() as temp:
        fixture_root = Path(temp)
        copy_validator_inputs(fixture_root)
        validator.ROOT = fixture_root
        assert validator.main() == 0


def test_missing_daily_d1_quota_reader_fails() -> None:
    with tempfile.TemporaryDirectory() as temp:
        fixture_root = Path(temp)
        copy_validator_inputs(fixture_root)
        users_route = fixture_root / "apps/api/src/routes/users.ts"
        source = users_route.read_text(encoding="utf-8")
        assert "getDailyChatQuotaUsage(" in source
        users_route.write_text(source.replace("getDailyChatQuotaUsage(", "retiredQuotaReader("), encoding="utf-8")
        validator.ROOT = fixture_root
        assert validator.main() == 1


if __name__ == "__main__":
    test_current_d1_quota_reader_passes()
    test_missing_daily_d1_quota_reader_fails()
    print("Cloudflare-only production validator regression tests passed.")
