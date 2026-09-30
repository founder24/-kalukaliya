CREATE UNIQUE INDEX IF NOT EXISTS monthly_quota_user_period_idx
  ON monthly_quota_usage (user_id, period);

CREATE INDEX IF NOT EXISTS chat_request_claims_monthly_idx
  ON chat_request_claims (user_id, monthly_period, monthly_quota_reserved, status, expires_at);

INSERT OR IGNORE INTO monthly_quota_usage (user_id, period, count, updated_at)
SELECT id, strftime('%Y-%m', 'now'), monthly_message_count, unixepoch()
FROM users
WHERE subscription_tier = 'free'
  AND COALESCE(monthly_message_count, 0) > 0
  AND last_reset_date >= unixepoch(date('now', 'start of month'));