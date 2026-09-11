#!/usr/bin/env bash
# Remove a disposable staff-portal fixture using non-secret metadata persisted
# for the GitHub Actions always-run cleanup step.
set -euo pipefail
set +x

metadata_file="${1:?Pass the disposable staff portal metadata file}"
[[ -f "$metadata_file" ]] || exit 0

# shellcheck disable=SC1090
source "$metadata_file"
: "${fixture_id:?Fixture metadata is missing fixture_id}"
: "${fixture_email:?Fixture metadata is missing fixture_email}"
: "${telemetry_id:?Fixture metadata is missing telemetry_id}"
: "${telemetry_route:?Fixture metadata is missing telemetry_route}"

[[ "$fixture_id" =~ ^release-staff-auth-[0-9a-f]{32}$ ]]
[[ "$fixture_email" == "${fixture_id}@example.invalid" ]]
[[ "$telemetry_id" == "${fixture_id}-portal" ]]
[[ "$telemetry_route" =~ ^/release-staff-portal/[0-9a-f]{32}$ ]]

cleanup_sql="DELETE FROM refresh_token_claims WHERE user_id = '${fixture_id}';
  DELETE FROM content_audit_log WHERE user_id = '${fixture_id}' OR target_id = '${fixture_id}';
  DELETE FROM analytics_events WHERE id = '${telemetry_id}' OR route_path = '${telemetry_route}';
  DELETE FROM users WHERE id = '${fixture_id}' OR email = '${fixture_email}';
  DELETE FROM release_staff_auth_leases WHERE fixture_id = '${fixture_id}';"
pnpm --filter syrabit-api exec wrangler d1 execute syrabit-db \
  --remote --env production --command "$cleanup_sql" >/dev/null 2>&1
rm -f "$metadata_file"
echo "Disposable staff portal fixture removed."