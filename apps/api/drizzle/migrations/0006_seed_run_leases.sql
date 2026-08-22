ALTER TABLE seed_runs ADD COLUMN lease_token TEXT;
ALTER TABLE seed_runs ADD COLUMN lease_expires_at INTEGER;
CREATE INDEX IF NOT EXISTS sr_lease_idx ON seed_runs(status, lease_expires_at);