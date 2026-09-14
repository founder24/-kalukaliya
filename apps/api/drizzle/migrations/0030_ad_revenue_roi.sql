-- Provider-verified ad revenue and referral ROI controls.
-- These tables intentionally store aggregate network reports only, no user or
-- visitor identifiers are accepted as ad-provider evidence.

CREATE TABLE IF NOT EXISTS ad_network_inventory (
  network TEXT PRIMARY KEY,
  status TEXT NOT NULL CHECK (status IN ('enabled', 'disabled', 'unconfigured')),
  configured INTEGER NOT NULL DEFAULT 0 CHECK (configured IN (0, 1)),
  placements_json TEXT NOT NULL DEFAULT '[]',
  policy_notes TEXT NOT NULL DEFAULT '',
  updated_by TEXT,
  updated_at INTEGER NOT NULL DEFAULT (unixepoch())
);

INSERT OR IGNORE INTO ad_network_inventory
  (network, status, configured, placements_json, policy_notes)
VALUES
  ('adsense', 'enabled', 1, '["chat","library","browser","learn","pyq","chapter"]',
   'Only enabled production network, provider reporting is required for revenue evidence.'),
  ('adsterra', 'disabled', 0, '[]',
   'Disabled, contributes zero projected and reported revenue.'),
  ('adcash', 'unconfigured', 0, '[]',
   'Blueprint reference only, not enabled in production.'),
  ('adpushup', 'disabled', 0, '[]',
   'Disabled, contributes zero projected and reported revenue.'),
  ('propellerads', 'disabled', 0, '[]',
   'Disabled, contributes zero projected and reported revenue.');

CREATE TABLE IF NOT EXISTS ad_revenue_reports (
  id TEXT PRIMARY KEY,
  network TEXT NOT NULL REFERENCES ad_network_inventory(network),
  period_start INTEGER NOT NULL,
  period_end INTEGER NOT NULL,
  settlement_period TEXT NOT NULL,
  currency TEXT NOT NULL DEFAULT 'INR',
  gross_revenue_paise INTEGER NOT NULL CHECK (gross_revenue_paise >= 0),
  adjustments_paise INTEGER NOT NULL DEFAULT 0 CHECK (adjustments_paise >= 0),
  provider_fees_paise INTEGER NOT NULL DEFAULT 0 CHECK (provider_fees_paise >= 0),
  net_revenue_paise INTEGER NOT NULL,
  monetized_impressions INTEGER NOT NULL CHECK (monetized_impressions >= 0),
  finalized INTEGER NOT NULL CHECK (finalized IN (0, 1)),
  finalized_through_at INTEGER NOT NULL,
  fetched_at INTEGER NOT NULL,
  freshness_expires_at INTEGER NOT NULL,
  source_reference TEXT NOT NULL UNIQUE,
  evidence_hash TEXT NOT NULL,
  imported_by TEXT NOT NULL,
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  CHECK (period_end > period_start),
  CHECK (net_revenue_paise = gross_revenue_paise - adjustments_paise - provider_fees_paise)
);

CREATE INDEX IF NOT EXISTS ad_revenue_reports_period_idx
ON ad_revenue_reports (network, period_start, period_end, finalized, finalized_through_at);

CREATE TABLE IF NOT EXISTS referral_roi_controls (
  id TEXT PRIMARY KEY CHECK (id = 'singleton'),
  reserve_healthy INTEGER NOT NULL DEFAULT 0 CHECK (reserve_healthy IN (0, 1)),
  revenue_fresh INTEGER NOT NULL DEFAULT 0 CHECK (revenue_fresh IN (0, 1)),
  invalid_traffic_healthy INTEGER NOT NULL DEFAULT 0 CHECK (invalid_traffic_healthy IN (0, 1)),
  ad_account_healthy INTEGER NOT NULL DEFAULT 0 CHECK (ad_account_healthy IN (0, 1)),
  contribution_margin_healthy INTEGER NOT NULL DEFAULT 0 CHECK (contribution_margin_healthy IN (0, 1)),
  identity_resets_healthy INTEGER NOT NULL DEFAULT 0 CHECK (identity_resets_healthy IN (0, 1)),
  fraud_healthy INTEGER NOT NULL DEFAULT 0 CHECK (fraud_healthy IN (0, 1)),
  exposure_healthy INTEGER NOT NULL DEFAULT 0 CHECK (exposure_healthy IN (0, 1)),
  evidence_id TEXT NOT NULL,
  warnings_json TEXT NOT NULL DEFAULT '[]',
  updated_by TEXT NOT NULL,
  updated_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL
);

INSERT OR IGNORE INTO referral_roi_controls
  (id, evidence_id, updated_by, updated_at, expires_at)
VALUES ('singleton', 'missing', 'migration', 0, 0);

CREATE TABLE IF NOT EXISTS referral_weekly_roi_reports (
  id TEXT PRIMARY KEY,
  week_id TEXT NOT NULL UNIQUE REFERENCES referral_weeks(id),
  revenue_report_id TEXT REFERENCES ad_revenue_reports(id),
  referral_clicks INTEGER NOT NULL DEFAULT 0,
  unique_browser_identities INTEGER NOT NULL DEFAULT 0,
  authenticated_accounts INTEGER NOT NULL DEFAULT 0,
  mature_verified_visitors INTEGER NOT NULL DEFAULT 0,
  repeat_week_visitors INTEGER NOT NULL DEFAULT 0,
  payable_statements INTEGER NOT NULL DEFAULT 0,
  cash_paid_inr INTEGER NOT NULL DEFAULT 0,
  reserved_rewards_inr INTEGER NOT NULL DEFAULT 0,
  review_cost_inr INTEGER NOT NULL DEFAULT 0,
  fraud_cost_inr INTEGER NOT NULL DEFAULT 0,
  reversal_cost_inr INTEGER NOT NULL DEFAULT 0,
  support_cost_inr INTEGER NOT NULL DEFAULT 0,
  operating_cost_inr INTEGER NOT NULL DEFAULT 0,
  true_program_cost_inr INTEGER NOT NULL DEFAULT 0,
  actual_monetized_impressions INTEGER NOT NULL DEFAULT 0,
  finalized_net_ad_revenue_paise INTEGER,
  contribution_margin_paise INTEGER,
  payback_ratio_milli INTEGER,
  data_quality TEXT NOT NULL CHECK (data_quality IN ('healthy', 'warning', 'blocked')),
  pause_recommended INTEGER NOT NULL DEFAULT 1 CHECK (pause_recommended IN (0, 1)),
  warnings_json TEXT NOT NULL DEFAULT '[]',
  generated_by TEXT NOT NULL,
  generated_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS referral_weekly_roi_quality_idx
ON referral_weekly_roi_reports (data_quality, pause_recommended, generated_at);