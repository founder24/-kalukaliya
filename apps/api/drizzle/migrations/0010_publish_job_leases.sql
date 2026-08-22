ALTER TABLE publish_jobs ADD COLUMN lease_token TEXT;
ALTER TABLE publish_jobs ADD COLUMN lease_expires_at INTEGER;
CREATE INDEX IF NOT EXISTS publish_jobs_lease_idx ON publish_jobs(status, lease_expires_at);