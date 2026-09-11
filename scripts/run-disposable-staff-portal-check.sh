#!/usr/bin/env bash
# Lease a disposable role=staff account, run the production portal browser
# lifecycle, and remove every fixture-owned record on success or failure.
set -euo pipefail
set +x

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${CF_ACCESS_CLIENT_ID:?CF_ACCESS_CLIENT_ID is required}"
: "${CF_ACCESS_CLIENT_SECRET:?CF_ACCESS_CLIENT_SECRET is required}"

fixture_suffix="$(openssl rand -hex 16)"
fixture_id="release-staff-auth-${fixture_suffix}"
fixture_email="${fixture_id}@example.invalid"
fixture_password="$(openssl rand -base64 36 | tr -d '\n')"
telemetry_id="${fixture_id}-portal"
telemetry_route="/release-staff-portal/${fixture_suffix}"
lease_expires_at="$(( $(date -u +%s) + 3600 ))"
fixture_metadata="${RUNNER_TEMP:-/tmp}/release-staff-portal-fixture.env"
fixture_hash="$(CUTOVER_STAFF_PASSWORD="$fixture_password" pnpm --filter syrabit-api exec node -e '
  const bcrypt = require("bcryptjs");
  bcrypt.hash(process.env.CUTOVER_STAFF_PASSWORD, 12).then(hash => process.stdout.write(hash));
')"

cleanup_fixture() {
  bash "$ROOT/scripts/cleanup-disposable-staff-portal.sh" "$fixture_metadata" || {
    echo "::error::Disposable staff portal fixture cleanup failed."
    return 1
  }
}

finish() {
  local status=$?
  trap - EXIT
  cleanup_fixture || status=1
  exit "$status"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

printf "fixture_id=%q\nfixture_email=%q\ntelemetry_id=%q\ntelemetry_route=%q\n" \
  "$fixture_id" "$fixture_email" "$telemetry_id" "$telemetry_route" >"$fixture_metadata"
chmod 600 "$fixture_metadata"

reaper_sql="$(cat "$ROOT/scripts/sql/reap-expired-release-staff-auth.sql")"
pnpm --filter syrabit-api exec wrangler d1 execute syrabit-db \
  --remote --env production --command "$reaper_sql" >/dev/null 2>&1 || {
  echo "::error::Expired disposable staff fixture cleanup failed."
  exit 1
}

create_sql="INSERT INTO release_staff_auth_leases
    (fixture_id, telemetry_id, telemetry_route, expires_at)
  VALUES
    ('${fixture_id}', '${telemetry_id}', '${telemetry_route}', ${lease_expires_at});
  INSERT INTO users
    (id, email, hashed_password, auth_provider, role, name, created_at, updated_at)
  VALUES
    ('${fixture_id}', '${fixture_email}', '${fixture_hash}', 'local', 'staff',
     'Release Staff Portal', unixepoch(), unixepoch());
  INSERT INTO analytics_events
    (id, event_name, event_subtype, classification, payload, route_path, created_at)
  VALUES
    ('${telemetry_id}', 'release_staff_portal', 'release_staff_portal',
     'essential_operational', '{}', '${telemetry_route}', unixepoch());"
pnpm --filter syrabit-api exec wrangler d1 execute syrabit-db \
  --remote --env production --command "$create_sql" >/dev/null

CUTOVER_STAFF_EMAIL="$fixture_email" \
CUTOVER_STAFF_PASSWORD="$fixture_password" \
PUBLIC_SITE_URL="${PUBLIC_SITE_URL:-https://syrabit.ai}" \
PUBLIC_EDGE_URL="${PUBLIC_EDGE_URL:-https://api.syrabit.ai}" \
node "$ROOT/scripts/verify-production-staff-portal.mjs"