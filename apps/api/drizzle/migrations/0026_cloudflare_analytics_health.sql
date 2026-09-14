-- Latest GitHub Cloudflare Analytics contract result handed off to the API
-- Worker. A singleton keeps the admin health surface small and queryable.
CREATE TABLE IF NOT EXISTS cloudflare_analytics_health (
  id TEXT PRIMARY KEY DEFAULT 'singleton',
  status TEXT NOT NULL,
  checked_at TEXT NOT NULL,
  error TEXT,
  remediation TEXT,
  needs_rotation INTEGER NOT NULL DEFAULT 0,
  hourly_buckets_returned INTEGER NOT NULL DEFAULT 0,
  hourly_bucket_count INTEGER,
  unique_visitors_supported INTEGER,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER DEFAULT (unixepoch())
);