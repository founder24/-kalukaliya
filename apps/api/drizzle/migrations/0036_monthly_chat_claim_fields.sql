ALTER TABLE chat_request_claims
  ADD COLUMN monthly_quota_reserved INTEGER NOT NULL DEFAULT 0;
ALTER TABLE chat_request_claims
  ADD COLUMN monthly_period TEXT;

CREATE TABLE IF NOT EXISTS monthly_quota_usage (
  user_id TEXT NOT NULL REFERENCES users(id),
  period TEXT NOT NULL,
  count INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
  PRIMARY KEY (user_id, period)
);

CREATE INDEX IF NOT EXISTS monthly_quota_usage_updated_idx
  ON monthly_quota_usage (updated_at);