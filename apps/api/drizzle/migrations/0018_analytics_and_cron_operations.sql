-- Durable, privacy-bounded browser analytics and scheduled-operation history.
CREATE TABLE IF NOT EXISTS analytics_events (
  id TEXT PRIMARY KEY,
  event_name TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}',
  route_path TEXT,
  created_at INTEGER DEFAULT (unixepoch())
);
CREATE INDEX IF NOT EXISTS analytics_events_name_created_idx
  ON analytics_events(event_name, created_at);
CREATE INDEX IF NOT EXISTS analytics_events_route_created_idx
  ON analytics_events(route_path, created_at);

-- Cron executions are append-only records. The singleton state is updated at
-- the end of each invocation to retain the current failure/outage indication.
CREATE TABLE IF NOT EXISTS cron_runs (
  id TEXT PRIMARY KEY,
  cron_expression TEXT NOT NULL,
  scheduled_at INTEGER NOT NULL,
  started_at INTEGER NOT NULL,
  completed_at INTEGER,
  status TEXT NOT NULL,
  failure_count INTEGER NOT NULL DEFAULT 0,
  error_summary TEXT
);
CREATE INDEX IF NOT EXISTS cron_runs_expression_scheduled_idx
  ON cron_runs(cron_expression, scheduled_at);
CREATE INDEX IF NOT EXISTS cron_runs_status_started_idx
  ON cron_runs(status, started_at);

CREATE TABLE IF NOT EXISTS cron_alert_state (
  id TEXT PRIMARY KEY DEFAULT 'singleton',
  alert_active INTEGER NOT NULL DEFAULT 0,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  last_failure_at INTEGER,
  last_success_at INTEGER,
  last_alert_at INTEGER,
  alert_reason TEXT,
  updated_at INTEGER DEFAULT (unixepoch())
);