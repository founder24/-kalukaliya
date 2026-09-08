-- Additive: NULL capabilities retains the historical full-staff policy.
ALTER TABLE users ADD COLUMN capabilities TEXT;

CREATE TABLE IF NOT EXISTS rag_reindex_jobs (
  id TEXT PRIMARY KEY,
  actor_id TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  requested_scopes TEXT NOT NULL DEFAULT '["notes"]',
  items TEXT NOT NULL DEFAULT '[]',
  error_log TEXT,
  lease_token TEXT,
  lease_expires_at INTEGER,
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
  completed_at INTEGER
);
CREATE INDEX IF NOT EXISTS rag_reindex_jobs_status_idx ON rag_reindex_jobs(status, updated_at);

CREATE TABLE IF NOT EXISTS destructive_preview_tokens (
  id TEXT PRIMARY KEY,
  actor_id TEXT NOT NULL,
  chapter_ids TEXT NOT NULL,
  impact_hash TEXT NOT NULL,
  expires_at INTEGER NOT NULL,
  consumed_at INTEGER,
  created_at INTEGER NOT NULL DEFAULT (unixepoch())
);