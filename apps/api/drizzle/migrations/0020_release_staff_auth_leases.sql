-- Disposable release staff accounts can outlive a cancelled runner. Keep a
-- database-clock lease so a later release can safely reap only expired fixtures.
CREATE TABLE IF NOT EXISTS release_staff_auth_leases (
  fixture_id TEXT PRIMARY KEY,
  telemetry_id TEXT NOT NULL UNIQUE,
  telemetry_route TEXT NOT NULL UNIQUE,
  expires_at INTEGER NOT NULL,
  created_at INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS release_staff_auth_leases_expires_idx
  ON release_staff_auth_leases(expires_at);
