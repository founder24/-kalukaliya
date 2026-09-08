#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB="$(mktemp)"
trap 'rm -f "$DB"' EXIT

sqlite3 "$DB" <<SQL
.bail on
.read ${ROOT}/apps/api/drizzle/migrations/0000_initial_schema.sql
.read ${ROOT}/apps/api/drizzle/migrations/0014_atomic_quota_and_refresh_claims.sql
.read ${ROOT}/apps/api/drizzle/migrations/0018_analytics_and_cron_operations.sql
.read ${ROOT}/apps/api/drizzle/migrations/0019_analytics_event_classification.sql
.read ${ROOT}/apps/api/drizzle/migrations/0020_release_staff_auth_leases.sql

INSERT INTO release_staff_auth_leases
  (fixture_id, telemetry_id, telemetry_route, expires_at)
VALUES
  ('release-staff-auth-expired', 'expired-telemetry-id', '/release-staff-auth/expired', unixepoch() - 1),
  ('release-staff-auth-active', 'active-telemetry-id', '/release-staff-auth/active', unixepoch() + 3600);

INSERT INTO users (id, email, role) VALUES
  ('release-staff-auth-expired', 'expired-release-staff-auth@example.invalid', 'admin'),
  ('release-staff-auth-active', 'active-release-staff-auth@example.invalid', 'admin');

INSERT INTO refresh_token_claims (jti, user_id, expires_at) VALUES
  ('expired-refresh', 'release-staff-auth-expired', unixepoch() + 86400),
  ('active-refresh', 'release-staff-auth-active', unixepoch() + 86400);

INSERT INTO content_audit_log (id, user_id, action, target_id) VALUES
  ('expired-audit-user', 'release-staff-auth-expired', 'fixture-test', 'unrelated-target'),
  ('expired-audit-target', 'unrelated-user', 'fixture-test', 'release-staff-auth-expired'),
  ('active-audit-user', 'release-staff-auth-active', 'fixture-test', 'unrelated-target'),
  ('active-audit-target', 'unrelated-user', 'fixture-test', 'release-staff-auth-active');

INSERT INTO analytics_events
  (id, event_name, event_subtype, classification, route_path)
VALUES
  ('expired-telemetry-id', 'fixture-test', 'fixture-test', 'essential_operational', '/unrelated'),
  ('expired-telemetry-route', 'fixture-test', 'fixture-test', 'essential_operational', '/release-staff-auth/expired'),
  ('active-telemetry-id', 'fixture-test', 'fixture-test', 'essential_operational', '/unrelated'),
  ('active-telemetry-route', 'fixture-test', 'fixture-test', 'essential_operational', '/release-staff-auth/active');

.read ${ROOT}/scripts/sql/reap-expired-release-staff-auth.sql
SQL

assert_count() {
  local expected="$1" query="$2" description="$3" actual
  actual="$(sqlite3 "$DB" "$query")"
  if [[ "$actual" != "$expected" ]]; then
    echo "${description}: expected ${expected}, got ${actual}" >&2
    exit 1
  fi
}

assert_count 0 "SELECT count(*) FROM release_staff_auth_leases WHERE fixture_id = 'release-staff-auth-expired';" \
  "expired fixture lease remained"
assert_count 0 "SELECT count(*) FROM users WHERE id = 'release-staff-auth-expired';" \
  "expired fixture user remained"
assert_count 0 "SELECT count(*) FROM refresh_token_claims WHERE user_id = 'release-staff-auth-expired';" \
  "expired fixture refresh claim remained"
assert_count 0 "SELECT count(*) FROM content_audit_log WHERE user_id = 'release-staff-auth-expired' OR target_id = 'release-staff-auth-expired';" \
  "expired fixture audit records remained"
assert_count 0 "SELECT count(*) FROM analytics_events WHERE id = 'expired-telemetry-id' OR route_path = '/release-staff-auth/expired';" \
  "expired fixture telemetry remained"

assert_count 1 "SELECT count(*) FROM release_staff_auth_leases WHERE fixture_id = 'release-staff-auth-active';" \
  "active fixture lease was removed"
assert_count 1 "SELECT count(*) FROM users WHERE id = 'release-staff-auth-active';" \
  "active fixture user was removed"
assert_count 1 "SELECT count(*) FROM refresh_token_claims WHERE user_id = 'release-staff-auth-active';" \
  "active fixture refresh claim was removed"
assert_count 2 "SELECT count(*) FROM content_audit_log WHERE user_id = 'release-staff-auth-active' OR target_id = 'release-staff-auth-active';" \
  "active fixture audit records were removed"
assert_count 2 "SELECT count(*) FROM analytics_events WHERE id = 'active-telemetry-id' OR route_path = '/release-staff-auth/active';" \
  "active fixture telemetry was removed"

echo "Release staff-auth reaper removed only the expired fixture graph."