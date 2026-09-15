-- Guarded, non-cash referral access rewards.
-- These ledgers are deliberately separate from referral settlement and from
-- the one-minute chat rate-limit buckets.

CREATE TABLE IF NOT EXISTS referral_reward_claims (
  id TEXT PRIMARY KEY,
  reward_type TEXT NOT NULL CHECK (reward_type IN ('ad_free', 'signup_bonus')),
  promoter_user_id TEXT REFERENCES users(id),
  promoter_slot INTEGER REFERENCES referral_influencer_slots(slot_no),
  week_id TEXT REFERENCES referral_weeks(id),
  referred_account_id TEXT REFERENCES users(id),
  period TEXT,
  status TEXT NOT NULL CHECK (status IN ('active', 'rejected', 'revoked', 'expired')),
  units INTEGER NOT NULL DEFAULT 0 CHECK (units >= 0),
  starts_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  policy_version TEXT NOT NULL,
  evidence_json TEXT NOT NULL DEFAULT '{}',
  reason TEXT,
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
  revoked_at INTEGER
);

CREATE UNIQUE INDEX IF NOT EXISTS referral_reward_signup_account_idx
  ON referral_reward_claims (referred_account_id)
  WHERE reward_type = 'signup_bonus' AND referred_account_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS referral_reward_adfree_week_idx
  ON referral_reward_claims (promoter_user_id, week_id)
  WHERE reward_type = 'ad_free' AND promoter_user_id IS NOT NULL AND week_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS referral_reward_claim_status_idx
  ON referral_reward_claims (status, reward_type, expires_at);

CREATE TABLE IF NOT EXISTS referral_access_entitlements (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  reward_claim_id TEXT NOT NULL UNIQUE REFERENCES referral_reward_claims(id),
  status TEXT NOT NULL CHECK (status IN ('active', 'revoked', 'expired')),
  starts_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  policy_version TEXT NOT NULL,
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
  revoked_at INTEGER
);

CREATE UNIQUE INDEX IF NOT EXISTS referral_access_active_user_idx
  ON referral_access_entitlements (user_id)
  WHERE status = 'active';

CREATE INDEX IF NOT EXISTS referral_access_expiry_idx
  ON referral_access_entitlements (status, expires_at);

CREATE TABLE IF NOT EXISTS referral_monthly_credit_grants (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  reward_claim_id TEXT NOT NULL UNIQUE REFERENCES referral_reward_claims(id),
  period TEXT NOT NULL,
  units INTEGER NOT NULL CHECK (units > 0),
  status TEXT NOT NULL CHECK (status IN ('active', 'revoked', 'expired')),
  policy_version TEXT NOT NULL,
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
  revoked_at INTEGER
);

CREATE INDEX IF NOT EXISTS referral_monthly_grants_user_period_idx
  ON referral_monthly_credit_grants (user_id, period, status);

CREATE TABLE IF NOT EXISTS referral_monthly_credit_usage (
  user_id TEXT NOT NULL REFERENCES users(id),
  period TEXT NOT NULL,
  base_limit INTEGER NOT NULL,
  base_reserved INTEGER NOT NULL DEFAULT 0,
  base_consumed INTEGER NOT NULL DEFAULT 0,
  bonus_granted INTEGER NOT NULL DEFAULT 0,
  bonus_reserved INTEGER NOT NULL DEFAULT 0,
  bonus_consumed INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
  PRIMARY KEY (user_id, period),
  CHECK (base_limit >= 0),
  CHECK (base_reserved >= 0 AND base_consumed >= 0),
  CHECK (bonus_granted >= 0 AND bonus_reserved >= 0 AND bonus_consumed >= 0)
);

CREATE INDEX IF NOT EXISTS referral_monthly_usage_period_idx
  ON referral_monthly_credit_usage (period, updated_at);

ALTER TABLE chat_request_claims ADD COLUMN monthly_period TEXT;
ALTER TABLE chat_request_claims ADD COLUMN monthly_base_reserved INTEGER NOT NULL DEFAULT 0;
ALTER TABLE chat_request_claims ADD COLUMN monthly_bonus_reserved INTEGER NOT NULL DEFAULT 0;