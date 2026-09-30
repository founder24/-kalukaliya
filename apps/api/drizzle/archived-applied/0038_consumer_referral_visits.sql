CREATE UNIQUE INDEX IF NOT EXISTS users_consumer_referral_code_idx
  ON users (consumer_referral_code);

CREATE TABLE IF NOT EXISTS consumer_referral_visits (
  id TEXT PRIMARY KEY,
  owner_user_id TEXT NOT NULL,
  visitor_key TEXT NOT NULL,
  identity_confidence TEXT NOT NULL CHECK (identity_confidence IN ('browser', 'account')),
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  expires_at INTEGER
  ,
  UNIQUE (owner_user_id, visitor_key)
);

CREATE INDEX IF NOT EXISTS consumer_referral_visits_owner_idx
  ON consumer_referral_visits (owner_user_id, created_at);