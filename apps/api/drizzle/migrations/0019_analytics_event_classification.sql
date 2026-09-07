-- Keep the beacon route for compatibility while retaining the actual event
-- subtype and its explicit privacy classification for safe aggregation.
ALTER TABLE analytics_events ADD COLUMN event_subtype TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE analytics_events ADD COLUMN classification TEXT NOT NULL DEFAULT 'optional_analytics';
CREATE INDEX IF NOT EXISTS analytics_events_subtype_created_idx
  ON analytics_events(event_subtype, created_at);