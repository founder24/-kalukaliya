#!/usr/bin/env bash
# Lightweight regression tests for validate-d1-config.sh.

set -euo pipefail

VALIDATOR="$(dirname "$0")/validate-d1-config.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

valid_config() {
  local id="$1"
  cat > "$TMP_DIR/wrangler.toml" <<EOF
[[d1_databases]]
binding = "DB"
database_name = "syrabit-db"
database_id = "$id"

[env.production]
[[env.production.d1_databases]]
binding = "DB"
database_name = "syrabit-db"
database_id = "$id"
EOF
}

expect_valid() {
  valid_config "$1"
  bash "$VALIDATOR" "$TMP_DIR/wrangler.toml" >/dev/null
}

expect_invalid() {
  valid_config "$1"
  if bash "$VALIDATOR" "$TMP_DIR/wrangler.toml" >/dev/null 2>&1; then
    echo "Expected D1 configuration to be rejected: $1" >&2
    exit 1
  fi
}

expect_valid "ff8e76ec-02c5-45f3-92ea-4d67d7d2a510"
expect_invalid ""
expect_invalid "not-a-d1-id"
expect_invalid "REPLACE_WITH_D1_ID"
expect_invalid "00000000-0000-0000-0000-000000000000"

echo "D1 configuration validator tests passed"