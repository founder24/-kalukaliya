CREATE TABLE IF NOT EXISTS referral_applications (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL UNIQUE REFERENCES users(id),
  status TEXT NOT NULL DEFAULT 'incomplete' CHECK (status IN (
    'submitted', 'incomplete', 'under_review', 'waitlisted', 'approved',
    'activation_required', 'active', 'expired', 'suspended', 'rejected',
    'paused', 'closed'
  )),
  institution TEXT NOT NULL,
  class_name TEXT NOT NULL,
  stream_name TEXT NOT NULL,
  academic_snapshot TEXT NOT NULL,
  contact_snapshot TEXT NOT NULL,
  identity_status TEXT NOT NULL DEFAULT 'pending' CHECK (identity_status IN ('pending', 'verified', 'rejected')),
  kyc_status TEXT NOT NULL DEFAULT 'pending' CHECK (kyc_status IN ('not_requested', 'pending', 'verified', 'rejected')),
  age_eligible INTEGER NOT NULL CHECK (age_eligible IN (0, 1)),
  guardian_consent_required INTEGER NOT NULL DEFAULT 0 CHECK (guardian_consent_required IN (0, 1)),
  guardian_consent_confirmed INTEGER NOT NULL DEFAULT 0 CHECK (guardian_consent_confirmed IN (0, 1)),
  eligibility_acknowledged INTEGER NOT NULL CHECK (eligibility_acknowledged IN (0, 1)),
  conduct_acknowledged INTEGER NOT NULL CHECK (conduct_acknowledged IN (0, 1)),
  privacy_consent INTEGER NOT NULL CHECK (privacy_consent IN (0, 1)),
  terms_version TEXT NOT NULL,
  privacy_version TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  payload_fingerprint TEXT NOT NULL,
  waitlist_priority_at INTEGER,
  activation_deadline_at INTEGER,
  influencer_slot INTEGER REFERENCES referral_influencer_slots(slot_no),
  decision_reason TEXT,
  reviewed_by TEXT REFERENCES users(id),
  reviewed_at INTEGER,
  submitted_at INTEGER NOT NULL,
  activated_at INTEGER,
  suspended_at INTEGER,
  closed_at INTEGER,
  updated_at INTEGER NOT NULL DEFAULT (unixepoch())
);

CREATE UNIQUE INDEX IF NOT EXISTS referral_application_idempotency_idx
ON referral_applications (user_id, idempotency_key);

CREATE INDEX IF NOT EXISTS referral_application_review_queue_idx
ON referral_applications (status, submitted_at, id);

CREATE INDEX IF NOT EXISTS referral_application_waitlist_idx
ON referral_applications (status, waitlist_priority_at, submitted_at, id);

CREATE INDEX IF NOT EXISTS referral_application_activation_expiry_idx
ON referral_applications (status, activation_deadline_at);

CREATE TABLE IF NOT EXISTS referral_application_audits (
  id TEXT PRIMARY KEY,
  application_id TEXT NOT NULL REFERENCES referral_applications(id),
  user_id TEXT NOT NULL REFERENCES users(id),
  actor_id TEXT NOT NULL REFERENCES users(id),
  action TEXT NOT NULL CHECK (action IN (
    'submitted', 'resubmitted', 'under_review', 'waitlisted', 'approved',
    'activated', 'expired', 'suspended', 'rejected', 'closed', 'appealed',
    'waitlist_promoted'
  )),
  from_status TEXT,
  to_status TEXT NOT NULL,
  reason TEXT NOT NULL,
  policy_version TEXT NOT NULL,
  occurred_at INTEGER NOT NULL,
  metadata TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS referral_application_audit_app_idx
ON referral_application_audits (application_id, occurred_at);