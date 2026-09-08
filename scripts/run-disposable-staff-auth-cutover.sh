#!/usr/bin/env bash
# Create a leased disposable admin, run the public sign-in lifecycle, and
# remove every fixture-owned record regardless of validation outcome.
set -euo pipefail
set +x

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${CF_ACCESS_CLIENT_ID:?CF_ACCESS_CLIENT_ID is required for release staff authentication}"
: "${CF_ACCESS_CLIENT_SECRET:?CF_ACCESS_CLIENT_SECRET is required for release staff authentication}"

fixture_suffix="$(openssl rand -hex 16)"
fixture_id="release-staff-auth-${fixture_suffix}"
fixture_email="${fixture_id}@example.invalid"
fixture_password="$(openssl rand -base64 36 | tr -d '\n')"
telemetry_id="${fixture_id}-telemetry"
telemetry_route="/release-staff-auth/${fixture_suffix}"
lease_expires_at="$(( $(date -u +%s) + 3600 ))"
fixture_hash="$(CUTOVER_STAFF_PASSWORD="$fixture_password" pnpm --filter syrabit-api exec node -e '
  const bcrypt = require("bcryptjs");
  bcrypt.hash(process.env.CUTOVER_STAFF_PASSWORD, 12).then(hash => process.stdout.write(hash));
')"

reap_expired_fixtures() {
  local reaper_sql
  reaper_sql="$(cat "$ROOT/scripts/sql/reap-expired-release-staff-auth.sql")"
  pnpm --filter syrabit-api exec wrangler d1 execute syrabit-db \
    --remote --env production --command "$reaper_sql" >/dev/null 2>&1 || {
    echo "::error::Expired disposable staff fixture cleanup failed."
    return 1
  }
  echo "Expired disposable staff fixtures and their marked records were reaped."
}

cleanup_fixture() {
  local cleanup_sql
  cleanup_sql="DELETE FROM refresh_token_claims WHERE user_id = '${fixture_id}';
    DELETE FROM content_audit_log WHERE user_id = '${fixture_id}' OR target_id = '${fixture_id}';
    DELETE FROM analytics_events WHERE id = '${telemetry_id}' OR route_path = '${telemetry_route}';
    DELETE FROM users WHERE id = '${fixture_id}' OR email = '${fixture_email}';
    DELETE FROM release_staff_auth_leases WHERE fixture_id = '${fixture_id}';"
  pnpm --filter syrabit-api exec wrangler d1 execute syrabit-db \
    --remote --env production --command "$cleanup_sql" >/dev/null 2>&1 || {
    echo "::error::Disposable staff fixture cleanup failed."
    return 1
  }
  echo "Disposable staff fixture and marked telemetry removed."
}

finish() {
  local status=$?
  trap - EXIT
  cleanup_fixture || status=1
  exit "$status"
}
trap finish EXIT

reap_expired_fixtures

create_sql="INSERT INTO release_staff_auth_leases
    (fixture_id, telemetry_id, telemetry_route, expires_at)
  VALUES
    ('${fixture_id}', '${telemetry_id}', '${telemetry_route}', ${lease_expires_at});
  INSERT INTO users
    (id, email, hashed_password, auth_provider, role, name, created_at, updated_at)
  VALUES
    ('${fixture_id}', '${fixture_email}', '${fixture_hash}', 'local', 'admin',
     'Release Staff Auth', unixepoch(), unixepoch());
  INSERT INTO analytics_events
    (id, event_name, event_subtype, classification, payload, route_path, created_at)
  VALUES
    ('${telemetry_id}', 'release_staff_auth', 'release_staff_auth',
     'essential_operational', '{}', '${telemetry_route}', unixepoch());"
pnpm --filter syrabit-api exec wrangler d1 execute syrabit-db \
  --remote --env production --command "$create_sql" >/dev/null

CUTOVER_STAFF_AUTH_ONLY=true \
CUTOVER_STAFF_EMAIL="$fixture_email" \
CUTOVER_STAFF_PASSWORD="$fixture_password" \
CUTOVER_STAFF_LEASE_EXPIRES_AT="$lease_expires_at" \
bash scripts/validate-cloudflare-api-cutover.sh