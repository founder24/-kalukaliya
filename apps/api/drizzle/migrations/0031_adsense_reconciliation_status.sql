-- The singleton status record keeps the latest automatic AdSense reconciliation
-- visible to staff without storing provider credentials or user-level identity.
CREATE TABLE IF NOT EXISTS adsense_reconciliation_status (
  id TEXT PRIMARY KEY CHECK (id = 'singleton'),
  status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'skipped', 'failed')),
  started_at INTEGER NOT NULL,
  completed_at INTEGER,
  weeks INTEGER NOT NULL DEFAULT 0,
  fetched INTEGER NOT NULL DEFAULT 0,
  imported INTEGER NOT NULL DEFAULT 0,
  idempotent INTEGER NOT NULL DEFAULT 0,
  calculated INTEGER NOT NULL DEFAULT 0,
  failures_json TEXT NOT NULL DEFAULT '[]'
);