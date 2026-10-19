ALTER TABLE chat_request_claims
  ADD COLUMN daily_identity_id TEXT;

UPDATE chat_request_claims
SET daily_identity_id = user_id
WHERE daily_quota_reserved = 1
  AND daily_identity_id IS NULL;

CREATE INDEX IF NOT EXISTS chat_request_claims_daily_identity_idx
  ON chat_request_claims (daily_identity_id, daily_period, daily_quota_reserved, status, expires_at);
