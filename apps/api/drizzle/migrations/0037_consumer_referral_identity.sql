ALTER TABLE referral_weekly_claims
  ADD COLUMN consumer_identity_key TEXT;

CREATE INDEX IF NOT EXISTS referral_weekly_consumer_identity_idx
  ON referral_weekly_claims (consumer_identity_key);