ALTER TABLE users ADD COLUMN referral_points INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN referral_visitors_verified INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN referral_upgrade_until INTEGER;
ALTER TABLE users ADD COLUMN referral_ads_free_until INTEGER;
ALTER TABLE users ADD COLUMN consumer_referral_code TEXT;

CREATE TABLE IF NOT EXISTS referral_points_ledger (
  id TEXT PRIMARY KEY,
  -- Keep the audit trail non-blocking for account deletion and legacy
  -- referral cleanup fixtures, user_id is an internal opaque identifier.
  user_id TEXT NOT NULL,
  source_type TEXT NOT NULL CHECK (
    source_type IN ('verified_visitor', 'upgrade_redemption', 'ads_free_redemption')
  ),
  source_key TEXT NOT NULL,
  points INTEGER NOT NULL,
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (user_id, source_key)
);

CREATE INDEX IF NOT EXISTS referral_points_ledger_user_idx
  ON referral_points_ledger (user_id, created_at);

CREATE UNIQUE INDEX IF NOT EXISTS referral_points_source_idx
  ON referral_points_ledger (source_key);