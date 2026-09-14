CREATE TABLE IF NOT EXISTS referral_program_state (
  id TEXT PRIMARY KEY CHECK (id = 'singleton'),
  state TEXT NOT NULL DEFAULT 'paused' CHECK (state IN ('active', 'paused', 'closed')),
  policy_version TEXT NOT NULL,
  pause_effective_at INTEGER,
  resumed_at INTEGER,
  accrual_generation INTEGER NOT NULL DEFAULT 0 CHECK (accrual_generation >= 0),
  updated_by TEXT,
  updated_at INTEGER NOT NULL DEFAULT (unixepoch())
);

INSERT OR IGNORE INTO referral_program_state (id, state, policy_version)
VALUES ('singleton', 'paused', '2026-09-14');

CREATE TABLE IF NOT EXISTS referral_weeks (
  id TEXT PRIMARY KEY,
  week_key TEXT NOT NULL UNIQUE,
  starts_at INTEGER NOT NULL UNIQUE,
  ends_at INTEGER NOT NULL UNIQUE,
  state TEXT NOT NULL DEFAULT 'closed' CHECK (state IN ('open', 'paused', 'review', 'finalized', 'closed')),
  policy_version TEXT NOT NULL,
  opened_at INTEGER,
  pause_effective_at INTEGER,
  resumed_at INTEGER,
  accrual_generation INTEGER NOT NULL DEFAULT 0 CHECK (accrual_generation >= 0),
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
  CHECK (ends_at > starts_at)
);

CREATE INDEX IF NOT EXISTS referral_weeks_state_time_idx
ON referral_weeks (state, starts_at, ends_at);

CREATE TABLE IF NOT EXISTS referral_influencer_slots (
  slot_no INTEGER PRIMARY KEY CHECK (slot_no BETWEEN 1 AND 100),
  user_id TEXT UNIQUE REFERENCES users(id),
  referral_code TEXT UNIQUE,
  status TEXT NOT NULL DEFAULT 'available' CHECK (status IN ('available', 'active', 'inactive', 'suspended', 'removed')),
  tier TEXT NOT NULL DEFAULT 'basic' CHECK (tier IN ('basic', 'advanced')),
  identity_verified INTEGER NOT NULL DEFAULT 0 CHECK (identity_verified IN (0, 1)),
  kyc_verified INTEGER NOT NULL DEFAULT 0 CHECK (kyc_verified IN (0, 1)),
  academic_snapshot TEXT,
  eligibility_reviewed_at INTEGER,
  admitted_at INTEGER,
  inactive_at INTEGER,
  suspended_at INTEGER,
  removed_at INTEGER,
  advanced_effective_at INTEGER,
  policy_version TEXT NOT NULL DEFAULT '2026-09-14',
  updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
  CHECK (
    status IN ('available', 'removed')
    OR (
      user_id IS NOT NULL
      AND referral_code IS NOT NULL
      AND identity_verified = 1
      AND kyc_verified = 1
      AND academic_snapshot IS NOT NULL
    )
  )
);

WITH RECURSIVE referral_slot_numbers(slot_no) AS (
  SELECT 1
  UNION ALL
  SELECT slot_no + 1 FROM referral_slot_numbers WHERE slot_no < 100
)
INSERT OR IGNORE INTO referral_influencer_slots (slot_no)
SELECT slot_no FROM referral_slot_numbers;

CREATE INDEX IF NOT EXISTS referral_influencer_status_idx
ON referral_influencer_slots (status, slot_no);

CREATE TABLE IF NOT EXISTS referral_admission_audits (
  id TEXT PRIMARY KEY,
  influencer_slot INTEGER NOT NULL,
  user_id TEXT NOT NULL,
  actor_id TEXT NOT NULL,
  reason TEXT NOT NULL CHECK (length(reason) BETWEEN 8 AND 1000),
  admitted_at INTEGER NOT NULL,
  policy_version TEXT NOT NULL,
  FOREIGN KEY (influencer_slot) REFERENCES referral_influencer_slots(slot_no),
  FOREIGN KEY (user_id) REFERENCES users(id),
  FOREIGN KEY (actor_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS referral_admission_audit_slot_idx
ON referral_admission_audits (influencer_slot, admitted_at);

CREATE TABLE IF NOT EXISTS referral_weekly_claims (
  id TEXT PRIMARY KEY,
  week_id TEXT NOT NULL REFERENCES referral_weeks(id),
  identity_hash TEXT NOT NULL,
  credited_influencer_slot INTEGER NOT NULL REFERENCES referral_influencer_slots(slot_no),
  account_id TEXT REFERENCES users(id),
  identity_confidence TEXT NOT NULL CHECK (identity_confidence IN ('low', 'browser', 'account')),
  state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'mature', 'rejected')),
  rejection_reason TEXT,
  first_seen_at INTEGER NOT NULL,
  last_seen_at INTEGER NOT NULL,
  matured_at INTEGER,
  maturity_token TEXT UNIQUE,
  quality_evidence_id TEXT UNIQUE,
  reversal_token TEXT UNIQUE,
  reconciliation_token TEXT UNIQUE,
  progress_counted INTEGER NOT NULL DEFAULT 0 CHECK (progress_counted IN (0, 1)),
  accrual_generation INTEGER NOT NULL DEFAULT 0 CHECK (accrual_generation >= 0),
  event_count INTEGER NOT NULL DEFAULT 1 CHECK (event_count BETWEEN 1 AND 100),
  quality_evidence TEXT NOT NULL DEFAULT '{}',
  policy_version TEXT NOT NULL,
  expires_at INTEGER NOT NULL,
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (week_id, identity_hash)
);

CREATE UNIQUE INDEX IF NOT EXISTS referral_weekly_account_claim_idx
ON referral_weekly_claims (week_id, account_id)
WHERE account_id IS NOT NULL AND state != 'rejected';

CREATE INDEX IF NOT EXISTS referral_weekly_claim_influencer_idx
ON referral_weekly_claims (week_id, credited_influencer_slot, state);

CREATE INDEX IF NOT EXISTS referral_weekly_claim_maturity_idx
ON referral_weekly_claims (state, identity_confidence, first_seen_at);

CREATE INDEX IF NOT EXISTS referral_weekly_claim_expiry_idx
ON referral_weekly_claims (expires_at);

CREATE TABLE IF NOT EXISTS referral_claim_events (
  id TEXT PRIMARY KEY,
  claim_id TEXT REFERENCES referral_weekly_claims(id),
  week_id TEXT REFERENCES referral_weeks(id),
  influencer_slot INTEGER NOT NULL REFERENCES referral_influencer_slots(slot_no),
  event_key TEXT NOT NULL UNIQUE,
  event_type TEXT NOT NULL CHECK (event_type IN ('visit', 'repeat', 'reconcile', 'mature', 'reject', 'paused_visit')),
  identity_confidence TEXT NOT NULL CHECK (identity_confidence IN ('low', 'browser', 'account')),
  occurred_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  created_at INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS referral_claim_events_expiry_idx
ON referral_claim_events (expires_at);

CREATE INDEX IF NOT EXISTS referral_claim_events_week_slot_idx
ON referral_claim_events (week_id, influencer_slot, occurred_at);

CREATE TABLE IF NOT EXISTS referral_weekly_progress (
  id TEXT PRIMARY KEY,
  week_id TEXT NOT NULL REFERENCES referral_weeks(id),
  influencer_slot INTEGER NOT NULL REFERENCES referral_influencer_slots(slot_no),
  mature_verified_count INTEGER NOT NULL DEFAULT 0 CHECK (mature_verified_count >= 0),
  reward_eligible_count INTEGER NOT NULL DEFAULT 0 CHECK (reward_eligible_count BETWEEN 0 AND 1000),
  unique_browser_count INTEGER NOT NULL DEFAULT 0 CHECK (unique_browser_count >= 0),
  authenticated_account_count INTEGER NOT NULL DEFAULT 0 CHECK (authenticated_account_count >= 0),
  low_confidence_count INTEGER NOT NULL DEFAULT 0 CHECK (low_confidence_count >= 0),
  provisional_qualified_at INTEGER,
  policy_version TEXT NOT NULL,
  updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (week_id, influencer_slot)
);

CREATE INDEX IF NOT EXISTS referral_weekly_progress_qualification_idx
ON referral_weekly_progress (week_id, provisional_qualified_at);

CREATE TABLE IF NOT EXISTS referral_advanced_positions (
  position_no INTEGER PRIMARY KEY CHECK (position_no BETWEEN 1 AND 30),
  influencer_slot INTEGER UNIQUE REFERENCES referral_influencer_slots(slot_no),
  status TEXT NOT NULL DEFAULT 'available' CHECK (status IN ('available', 'provisional', 'approved', 'active', 'rejected', 'released')),
  qualified_week_id TEXT REFERENCES referral_weeks(id),
  qualified_at INTEGER,
  reviewed_by TEXT,
  reviewed_at INTEGER,
  review_reason TEXT,
  activates_at INTEGER,
  policy_version TEXT NOT NULL DEFAULT '2026-09-14',
  updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
  CHECK (
    status IN ('available', 'released')
    OR (influencer_slot IS NOT NULL AND qualified_week_id IS NOT NULL AND qualified_at IS NOT NULL)
  )
);

WITH RECURSIVE referral_advanced_numbers(position_no) AS (
  SELECT 1
  UNION ALL
  SELECT position_no + 1 FROM referral_advanced_numbers WHERE position_no < 30
)
INSERT OR IGNORE INTO referral_advanced_positions (position_no)
SELECT position_no FROM referral_advanced_numbers;

CREATE INDEX IF NOT EXISTS referral_advanced_status_idx
ON referral_advanced_positions (status, position_no);

CREATE TABLE IF NOT EXISTS referral_advanced_reviews (
  id TEXT PRIMARY KEY,
  position_no INTEGER NOT NULL REFERENCES referral_advanced_positions(position_no),
  influencer_slot INTEGER NOT NULL REFERENCES referral_influencer_slots(slot_no),
  qualified_week_id TEXT NOT NULL REFERENCES referral_weeks(id),
  qualified_at INTEGER NOT NULL,
  decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
  reviewer_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  reviewed_at INTEGER NOT NULL,
  activates_at INTEGER,
  policy_version TEXT NOT NULL,
  created_at INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS referral_advanced_reviews_influencer_idx
ON referral_advanced_reviews (influencer_slot, reviewed_at);

CREATE TABLE IF NOT EXISTS referral_program_transitions (
  id TEXT PRIMARY KEY,
  transition TEXT NOT NULL CHECK (transition IN ('pause', 'resume')),
  actor_id TEXT NOT NULL,
  reason TEXT,
  effective_at INTEGER NOT NULL,
  accrual_generation INTEGER NOT NULL CHECK (accrual_generation >= 0),
  gate_evidence TEXT,
  policy_version TEXT NOT NULL,
  created_at INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS referral_program_transitions_time_idx
ON referral_program_transitions (effective_at, transition);

CREATE UNIQUE INDEX IF NOT EXISTS referral_program_transition_once_idx
ON referral_program_transitions (transition, effective_at, accrual_generation);

CREATE TABLE IF NOT EXISTS referral_gate_evidence (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL CHECK (provider = 'adsense'),
  provider_evidence_id TEXT NOT NULL UNIQUE,
  finalized_through_at INTEGER NOT NULL,
  gross_reserve_inr INTEGER NOT NULL CHECK (gross_reserve_inr >= 0),
  outstanding_obligations_inr INTEGER NOT NULL CHECK (outstanding_obligations_inr >= 0),
  quality_evidence_id TEXT NOT NULL UNIQUE,
  quality_measured_at INTEGER NOT NULL,
  attribution_healthy INTEGER NOT NULL CHECK (attribution_healthy IN (0, 1)),
  deduplication_healthy INTEGER NOT NULL CHECK (deduplication_healthy IN (0, 1)),
  fraud_review_healthy INTEGER NOT NULL CHECK (fraud_review_healthy IN (0, 1)),
  settlement_healthy INTEGER NOT NULL CHECK (settlement_healthy IN (0, 1)),
  recorded_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,
  policy_version TEXT NOT NULL,
  CHECK (gross_reserve_inr >= outstanding_obligations_inr)
);

CREATE INDEX IF NOT EXISTS referral_gate_evidence_expiry_idx
ON referral_gate_evidence (expires_at);

CREATE TABLE IF NOT EXISTS referral_visit_rate_limits (
  bucket_key TEXT PRIMARY KEY,
  request_count INTEGER NOT NULL DEFAULT 1 CHECK (request_count BETWEEN 1 AND 30),
  expires_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE INDEX IF NOT EXISTS referral_visit_rate_limits_expiry_idx
ON referral_visit_rate_limits (expires_at);