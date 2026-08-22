#!/usr/bin/env bash
# Validate D1 database IDs before a Worker deploy can reach tests or migrations.
#
# Usage:
#   bash scripts/validate-d1-config.sh [path/to/wrangler.toml]
#
# The validator intentionally checks every d1_databases entry in the default
# config and named environments. Wrangler can otherwise accept a valid default
# binding while deploying an invalid production binding.

set -euo pipefail

CONFIG_PATH="${1:-apps/api/wrangler.toml}"

if [[ ! -f "$CONFIG_PATH" ]]; then
  echo "D1 configuration file not found: $CONFIG_PATH" >&2
  exit 1
fi

python3 - "$CONFIG_PATH" <<'PY'
import re
import sys
import tomllib
from pathlib import Path

config_path = Path(sys.argv[1])

try:
    with config_path.open("rb") as config_file:
        config = tomllib.load(config_file)
except (OSError, tomllib.TOMLDecodeError) as exc:
    print(f"Unable to read D1 configuration from {config_path}: {exc}", file=sys.stderr)
    sys.exit(1)

uuid_pattern = re.compile(
    r"^[0-9a-fA-F]{8}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}$"
)
placeholder_pattern = re.compile(
    r"^(?:REPLACE_WITH_D1_ID|REPLACE_ME|YOUR[_-].*|<[^>]+>)$",
    re.IGNORECASE,
)

errors = []
bindings_checked = 0


def check_database_bindings(scope_name: str, scope: dict) -> None:
    global bindings_checked
    bindings = scope.get("d1_databases", [])
    if not isinstance(bindings, list):
        errors.append(f"{scope_name} d1_databases must be an array")
        return

    for index, binding in enumerate(bindings):
        label = f"{scope_name} d1_databases[{index}].database_id"
        bindings_checked += 1
        if not isinstance(binding, dict):
            errors.append(f"{label} must be a table")
            continue

        database_id = binding.get("database_id")
        if not isinstance(database_id, str) or not database_id.strip():
            errors.append(f"{label} is missing or blank")
            continue

        database_id = database_id.strip()
        if placeholder_pattern.fullmatch(database_id):
            errors.append(f"{label} still contains placeholder value {database_id!r}")
        elif not uuid_pattern.fullmatch(database_id):
            errors.append(
                f"{label} is malformed ({database_id!r}); expected a Cloudflare D1 UUID"
            )
        elif database_id.lower() == "00000000-0000-0000-0000-000000000000":
            errors.append(f"{label} cannot use the all-zero placeholder UUID")


check_database_bindings("default", config)
environments = config.get("env", {})
if not isinstance(environments, dict):
    errors.append("env must be a table")
else:
    for environment_name, environment in environments.items():
        if isinstance(environment, dict):
            check_database_bindings(f"env.{environment_name}", environment)
        else:
            errors.append(f"env.{environment_name} must be a table")

if bindings_checked == 0:
    errors.append("no d1_databases bindings were found")

if errors:
    print(f"D1 configuration check failed for {config_path}:", file=sys.stderr)
    for error in errors:
        print(f"  - {error}", file=sys.stderr)
    sys.exit(1)

print(f"D1 configuration is valid: {bindings_checked} database ID(s) checked")
PY