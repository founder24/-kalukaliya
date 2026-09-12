-- Command-center aggregates filter by these timestamp/classification columns.
-- Keep the existing aggregate response shape while avoiding full-table scans
-- when the bounded dashboard cache is cold.
CREATE INDEX IF NOT EXISTS users_created_idx
  ON users(created_at);

CREATE INDEX IF NOT EXISTS chapters_created_idx
  ON chapters(created_at);

CREATE INDEX IF NOT EXISTS analytics_events_classification_created_idx
  ON analytics_events(classification, created_at);

CREATE INDEX IF NOT EXISTS analytics_events_created_idx
  ON analytics_events(created_at);

CREATE INDEX IF NOT EXISTS publish_jobs_created_idx
  ON publish_jobs(created_at);

CREATE INDEX IF NOT EXISTS content_audit_log_created_idx
  ON content_audit_log(created_at);
