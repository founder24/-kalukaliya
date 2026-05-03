#!/usr/bin/env bash
# Apply R2 lifecycle rules from version-controlled JSON.
#
# Source of truth: docs/cloudflare-r2-lifecycle.md
# Referenced by:   docs/cloudflare-cost-map.md
#
# Usage:
#   CLOUDFLARE_API_TOKEN=...  ./infra/r2-lifecycle/apply.sh           # apply
#   CLOUDFLARE_API_TOKEN=...  ./infra/r2-lifecycle/apply.sh --verify  # list only
#
# Requires: wrangler >= 3.x and an API token with R2 Edit permission for
# the syrabit-assets and syrabit-media buckets in the production account.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

BUCKETS=(syrabit-assets syrabit-media)

# Require an explicit account id so a misconfigured wrangler context can't
# accidentally apply rules to the wrong Cloudflare account.
: "${CLOUDFLARE_ACCOUNT_ID:=${CF_AI_GATEWAY_ACCOUNT_ID:-}}"
if [[ -z "${CLOUDFLARE_ACCOUNT_ID}" ]]; then
  echo "error: CLOUDFLARE_ACCOUNT_ID (or CF_AI_GATEWAY_ACCOUNT_ID) must be set" >&2
  exit 1
fi
if [[ -z "${CLOUDFLARE_API_TOKEN:-}" ]]; then
  echo "error: CLOUDFLARE_API_TOKEN must be set (R2 Edit on the prod account)" >&2
  exit 1
fi
export CLOUDFLARE_ACCOUNT_ID CLOUDFLARE_API_TOKEN

if [[ "${1:-}" == "--verify" ]]; then
  for b in "${BUCKETS[@]}"; do
    echo "── lifecycle rules on $b ──"
    npx --yes wrangler r2 bucket lifecycle list "$b"
  done
  exit 0
fi

for b in "${BUCKETS[@]}"; do
  cfg="$HERE/$b.json"
  if [[ ! -f "$cfg" ]]; then
    echo "missing config: $cfg" >&2
    exit 1
  fi
  echo "── applying lifecycle rules to $b from $cfg ──"
  npx --yes wrangler r2 bucket lifecycle set "$b" --file "$cfg"
done

echo "── verification ──"
"$0" --verify
