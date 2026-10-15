CREATE TABLE IF NOT EXISTS daily_chat_quota_usage (
  identity_id TEXT NOT NULL,
  is_anonymous INTEGER NOT NULL CHECK (is_anonymous IN (0, 1)),
  period TEXT NOT NULL,
  count INTEGER NOT NULL DEFAULT 0 CHECK (count >= 0),
  updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
  PRIMARY KEY (identity_id, is_anonymous, period)
);

CREATE INDEX IF NOT EXISTS daily_chat_quota_period_idx
  ON daily_chat_quota_usage (period, is_anonymous);

ALTER TABLE chat_request_claims
  ADD COLUMN daily_quota_reserved INTEGER NOT NULL DEFAULT 0;

ALTER TABLE chat_request_claims
  ADD COLUMN daily_period TEXT;

CREATE INDEX IF NOT EXISTS chat_request_claims_daily_idx
  ON chat_request_claims (user_id, is_anon, daily_period, daily_quota_reserved, status, expires_at);
