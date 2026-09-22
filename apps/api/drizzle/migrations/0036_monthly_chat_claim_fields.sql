ALTER TABLE chat_request_claims
  ADD COLUMN monthly_quota_reserved INTEGER NOT NULL DEFAULT 0;

-- monthly_period is created by 0024 for fresh databases and already exists in
-- production, so only add the fields and table introduced by this migration.
CREATE TABLE IF NOT EXISTS monthly_quota_usage (
  user_id TEXT NOT NULL REFERENCES users(id),
  period TEXT NOT NULL,
  count INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
  PRIMARY KEY (user_id, period)
);

CREATE INDEX IF NOT EXISTS monthly_quota_usage_updated_idx
  ON monthly_quota_usage (updated_at);