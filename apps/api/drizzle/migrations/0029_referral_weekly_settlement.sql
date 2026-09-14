CREATE TABLE IF NOT EXISTS referral_weekly_envelopes (
  week_id TEXT PRIMARY KEY REFERENCES referral_weeks(id),
  worst_case_exposure_inr INTEGER NOT NULL DEFAULT 37000 CHECK (worst_case_exposure_inr = 37000),
  funded_cap_inr INTEGER NOT NULL CHECK (funded_cap_inr BETWEEN 0 AND 37000),
  reserved_inr INTEGER NOT NULL DEFAULT 0 CHECK (reserved_inr >= 0),
  status TEXT NOT NULL DEFAULT 'paused' CHECK (status IN ('paused', 'funded', 'held', 'closed', 'failed')),
  provider_evidence_id TEXT NOT NULL,
  quality_evidence_id TEXT NOT NULL,
  gate_evidence_id TEXT NOT NULL,
  finalized_through_at INTEGER NOT NULL,
  quality_measured_at INTEGER NOT NULL,
  opened_at INTEGER NOT NULL,
  closed_at INTEGER,
  updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
  CHECK (reserved_inr <= funded_cap_inr)
);

CREATE TABLE IF NOT EXISTS referral_accrual_intervals (
  id TEXT PRIMARY KEY,
  week_id TEXT NOT NULL REFERENCES referral_weeks(id),
  generation INTEGER NOT NULL CHECK (generation >= 0),
  starts_at INTEGER NOT NULL,
  ends_at INTEGER,
  state TEXT NOT NULL CHECK (state IN ('open', 'paused', 'closed')),
  pause_reason TEXT,
  actor_id TEXT,
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (week_id, generation),
  CHECK (ends_at IS NULL OR ends_at >= starts_at)
);

CREATE INDEX IF NOT EXISTS referral_accrual_intervals_time_idx
ON referral_accrual_intervals (week_id, starts_at, ends_at);

CREATE TABLE IF NOT EXISTS referral_weekly_tier_snapshots (
  id TEXT PRIMARY KEY,
  week_id TEXT NOT NULL REFERENCES referral_weeks(id),
  influencer_slot INTEGER NOT NULL REFERENCES referral_influencer_slots(slot_no),
  user_id TEXT REFERENCES users(id),
  tier TEXT NOT NULL CHECK (tier IN ('basic', 'advanced')),
  qualifying_week INTEGER NOT NULL DEFAULT 0 CHECK (qualifying_week IN (0, 1)),
  effective_at INTEGER NOT NULL,
  policy_version TEXT NOT NULL,
  UNIQUE (week_id, influencer_slot)
);

CREATE INDEX IF NOT EXISTS referral_weekly_tier_snapshot_user_idx
ON referral_weekly_tier_snapshots (week_id, user_id);

CREATE TABLE IF NOT EXISTS referral_weekly_statements (
  id TEXT PRIMARY KEY,
  week_id TEXT NOT NULL REFERENCES referral_weeks(id),
  influencer_slot INTEGER NOT NULL REFERENCES referral_influencer_slots(slot_no),
  user_id TEXT NOT NULL REFERENCES users(id),
  tier TEXT NOT NULL CHECK (tier IN ('basic', 'advanced')),
  qualifying_week INTEGER NOT NULL DEFAULT 0 CHECK (qualifying_week IN (0, 1)),
  mature_verified_count INTEGER NOT NULL DEFAULT 0 CHECK (mature_verified_count >= 0),
  payable_claim_count INTEGER NOT NULL DEFAULT 0 CHECK (payable_claim_count >= 0),
  rate_inr INTEGER NOT NULL DEFAULT 1 CHECK (rate_inr = 1),
  gross_amount_inr INTEGER NOT NULL DEFAULT 0 CHECK (gross_amount_inr >= 0),
  funded_cap_inr INTEGER NOT NULL CHECK (funded_cap_inr >= 0),
  status TEXT NOT NULL DEFAULT 'held'
    CHECK (status IN ('calculated', 'held', 'approved', 'paid', 'failed', 'paused', 'reversed', 'clawed_back', 'corrected')),
  quality_hold_released_at INTEGER NOT NULL,
  accrual_generation INTEGER NOT NULL DEFAULT 0,
  evidence_snapshot TEXT NOT NULL DEFAULT '{}',
  approved_by TEXT REFERENCES users(id),
  approved_at INTEGER,
  paid_at INTEGER,
  failure_reason TEXT,
  correction_of TEXT REFERENCES referral_weekly_statements(id),
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (week_id, influencer_slot)
);

CREATE INDEX IF NOT EXISTS referral_weekly_statement_user_idx
ON referral_weekly_statements (user_id, week_id);

CREATE TABLE IF NOT EXISTS referral_statement_claims (
  statement_id TEXT NOT NULL REFERENCES referral_weekly_statements(id),
  claim_id TEXT NOT NULL REFERENCES referral_weekly_claims(id),
  amount_inr INTEGER NOT NULL CHECK (amount_inr IN (0, 1)),
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  PRIMARY KEY (statement_id, claim_id),
  UNIQUE (claim_id)
);

CREATE TABLE IF NOT EXISTS referral_beneficiaries (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'verified', 'rejected', 'revoked')),
  details_snapshot TEXT NOT NULL,
  submitted_by TEXT NOT NULL REFERENCES users(id),
  verified_by TEXT REFERENCES users(id),
  verified_at INTEGER,
  decision_reason TEXT,
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  updated_at INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE UNIQUE INDEX IF NOT EXISTS referral_beneficiary_active_user_idx
ON referral_beneficiaries (user_id)
WHERE status IN ('pending', 'verified');

CREATE TABLE IF NOT EXISTS referral_payouts (
  id TEXT PRIMARY KEY,
  statement_id TEXT NOT NULL UNIQUE REFERENCES referral_weekly_statements(id),
  week_id TEXT NOT NULL REFERENCES referral_weeks(id),
  influencer_slot INTEGER NOT NULL REFERENCES referral_influencer_slots(slot_no),
  user_id TEXT NOT NULL REFERENCES users(id),
  beneficiary_id TEXT REFERENCES referral_beneficiaries(id),
  amount_inr INTEGER NOT NULL CHECK (amount_inr >= 0),
  status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'paid', 'failed', 'corrected', 'reversed', 'clawed_back')),
  idempotency_key TEXT NOT NULL UNIQUE,
  utr_reference TEXT,
  provider_reference TEXT,
  failure_reason TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  paid_at INTEGER,
  failed_at INTEGER,
  reversed_at INTEGER,
  created_by TEXT NOT NULL REFERENCES users(id),
  updated_at INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS referral_payout_user_idx
ON referral_payouts (user_id, week_id);

CREATE TABLE IF NOT EXISTS referral_payment_receipts (
  id TEXT PRIMARY KEY,
  payout_id TEXT NOT NULL REFERENCES referral_payouts(id),
  object_key TEXT NOT NULL UNIQUE,
  original_name TEXT NOT NULL,
  content_type TEXT NOT NULL,
  byte_size INTEGER NOT NULL CHECK (byte_size > 0 AND byte_size <= 5242880),
  sha256 TEXT NOT NULL UNIQUE,
  uploaded_by TEXT NOT NULL REFERENCES users(id),
  created_at INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE TABLE IF NOT EXISTS referral_settlement_audits (
  id TEXT PRIMARY KEY,
  week_id TEXT REFERENCES referral_weeks(id),
  statement_id TEXT REFERENCES referral_weekly_statements(id),
  payout_id TEXT REFERENCES referral_payouts(id),
  influencer_slot INTEGER,
  action TEXT NOT NULL,
  from_status TEXT,
  to_status TEXT,
  actor_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  metadata TEXT NOT NULL DEFAULT '{}',
  policy_version TEXT NOT NULL,
  evidence_snapshot TEXT NOT NULL DEFAULT '{}',
  occurred_at INTEGER NOT NULL,
  created_at INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS referral_settlement_audit_week_idx
ON referral_settlement_audits (week_id, occurred_at);